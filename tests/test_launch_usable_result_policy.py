"""Founder policy, unknown-outcome reconciliation and retained spend evidence."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import AiAllowanceBucket, AiUsageReservation
from app.services.ai_allowances import service as ledger, launch_policy
from app.services.ai_allowances.policy import SUPPORTED_FEATURES, starter_eligible, period_key, resets_at
from ai_allowance_support import enable_ledger, enable_subscriptions

NOW = datetime(2026, 10, 31, 20, 59, tzinfo=timezone.utc)


@pytest.fixture
def usable(app, make_user, monkeypatch):
    # Isolate financial settlement from the separate signed service gate.
    policy = replace(launch_policy.LAUNCH_V1_POLICY, version="synthetic-usable-result")
    enable_ledger(app, policy=policy)
    enable_subscriptions(app, monkeypatch)
    return make_user("usable-result-owner").id


def reserve(owner, key="usable-result-key", feature="coach"):
    hold = ledger.reserve_dispatch_within(owner, feature, key, "a" * 64, 8, now=NOW)
    db.session.commit()
    return hold


def row(ref):
    db.session.expire_all()
    return AiUsageReservation.query.filter_by(public_id=ref).one()


def counts(owner, feature="coach"):
    db.session.expire_all()
    bucket = AiAllowanceBucket.query.filter_by(user_id=owner, feature=feature).one()
    return bucket.used, bucket.reserved


def test_exact_approved_policy_and_istanbul_boundary(approved_launch_policy):
    policy = launch_policy.installed_policy()
    assert list(policy.starter.allowances.values()) == [10, 1, 1, 1, 1]
    assert list(policy.premium.allowances.values()) == [60, 2, 2, 16, 2]
    assert policy.starter.period == "NEVER" and policy.premium.period == "MONTHLY"
    assert policy.refund_unusable_results is True
    assert starter_eligible(policy, datetime(1960, 1, 1))
    assert not starter_eligible(policy, None)
    boundary = datetime(2026, 10, 31, 21, tzinfo=timezone.utc)
    assert period_key("MONTHLY", NOW) == "2026-10"
    assert resets_at("MONTHLY", NOW) == boundary
    assert period_key("MONTHLY", boundary) == "2026-11"
    assert resets_at("NEVER", NOW) is None


@pytest.mark.parametrize("feature", SUPPORTED_FEATURES)
@pytest.mark.parametrize("billing", ["UNKNOWN", "CONFIRMED_BILLED"])
def test_known_unusable_result_refunds_regardless_of_billing_once(usable, feature, billing):
    hold = reserve(usable, feature=feature)
    # A physical debit survives customer settlement, including UNKNOWN costs.
    record = row(hold.reference)
    record.physical_attempts = 2
    record.physical_input_reserved = 400
    db.session.commit()
    result = ledger.finalize(usable, hold.reference, "invalid_output", billing_evidence=billing,
                             result_known=True, now=NOW)
    assert result.state == "RELEASED" and not result.consumed
    assert counts(usable, feature) == (0, 0)
    assert (row(hold.reference).physical_attempts, row(hold.reference).physical_input_reserved) == (2, 400)
    assert row(hold.reference).billing_evidence == billing
    assert ledger.finalize(usable, hold.reference, "invalid_output", billing_evidence=billing,
                           result_known=True, now=NOW).replayed
    with pytest.raises(ledger.AllowanceError):
        reserve(usable, feature=feature)
    db.session.rollback()
    assert counts(usable, feature) == (0, 0)


def test_unknown_outcome_requires_reconciliation_and_cannot_reexecute(usable):
    hold = reserve(usable)
    result = ledger.finalize(usable, hold.reference, "provider_timeout", billing_evidence="UNKNOWN", now=NOW)
    assert result.state == "RESERVED"
    assert counts(usable) == (0, 1)
    with pytest.raises(ledger.AllowanceError):
        reserve(usable)
    db.session.rollback()
    later = NOW + timedelta(hours=1)
    assert ledger.expire_abandoned(now=later) == 1
    assert counts(usable) == (1, 0)
    assert ledger.finalize(usable, hold.reference, "provider_timeout", billing_evidence="UNKNOWN",
                           now=later).state == "EXPIRED"
    # Internal caller has now reconciled the original durable domain outcome.
    result = ledger.finalize(usable, hold.reference, "provider_error", billing_evidence="UNKNOWN",
                             result_known=True, now=later)
    assert result.state == "RELEASED" and counts(usable) == (0, 0)
    assert ledger.finalize(usable, hold.reference, "provider_error", billing_evidence="UNKNOWN",
                           result_known=True, now=later).replayed
    assert counts(usable) == (0, 0)


def test_canonical_success_wins_over_lost_response_and_expiry(usable):
    hold = reserve(usable)
    row(hold.reference).canonical_committed = True
    db.session.commit()
    later = NOW + timedelta(hours=1)
    ledger.expire_abandoned(now=later)
    result = ledger.finalize(usable, hold.reference, "provider_error", billing_evidence="UNKNOWN",
                             result_known=True, now=later)
    assert result.state == "COMMITTED" and result.outcome == "success"
    assert counts(usable) == (1, 0)


def test_deterministic_coach_confirmation_has_no_additional_ai_charge(usable):
    hold = reserve(usable)
    record = row(hold.reference)
    record.canonical_committed = True
    db.session.commit()
    # finalize_dispatch seals zero attempts under the original owner lock.
    result = ledger.finalize_dispatch(usable, hold.reference, successful=True, coach_first_party=True)
    assert result.state == "RELEASED" and counts(usable) == (0, 0)
    assert row(hold.reference).physical_attempts == 0


def test_other_canonical_coach_success_still_consumes_without_sdk_attempts(usable):
    hold = reserve(usable)
    row(hold.reference).canonical_committed = True
    db.session.commit()
    result = ledger.finalize_dispatch(usable, hold.reference, successful=False, coach_first_party=False)
    assert (result.state, result.outcome) == ("COMMITTED", "success")
    assert counts(usable) == (1, 0)


def test_bound_reconciliation_requires_explicit_billing_and_original_owner(usable, make_user):
    hold = reserve(usable)
    other = make_user("other-usable-owner").id
    for owner, kwargs in [(usable, {}), (other, {"billing_evidence": "UNKNOWN"})]:
        with pytest.raises(ledger.AllowanceError):
            ledger.finalize(owner, hold.reference, "provider_error", result_known=True, now=NOW, **kwargs)
    assert counts(usable) == (0, 1)


@pytest.mark.parametrize("evidence", ["CONFIRMED_BILLED", "CONFIRMED_NOT_BILLED"])
def test_reconciliation_never_downgrades_confirmed_financial_evidence(usable, evidence):
    hold = reserve(usable)
    ledger.finalize(usable, hold.reference, "provider_timeout", billing_evidence=evidence, now=NOW)
    if evidence == "CONFIRMED_NOT_BILLED":
        assert row(hold.reference).state == "RELEASED"
        return
    assert row(hold.reference).state == "RESERVED"
    with pytest.raises(ledger.AllowanceError):
        ledger.finalize(usable, hold.reference, "provider_error", billing_evidence="CONFIRMED_NOT_BILLED",
                        result_known=True, now=NOW)
    result = ledger.finalize(usable, hold.reference, "provider_error", billing_evidence="UNKNOWN",
                             result_known=True, now=NOW)
    assert result.state == "RELEASED"
    assert row(hold.reference).billing_evidence == "CONFIRMED_BILLED"
    assert row(hold.reference).provider_billed is True
