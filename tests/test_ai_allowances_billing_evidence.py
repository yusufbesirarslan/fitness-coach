"""MON-02-BE-03B-01B ledger extension, qualified on its own.

``reserve_within`` (same authority as ``reserve``, caller-owned transaction) and
tri-state billing evidence on ``finalize``. BE-03A callers are unchanged.
"""
from datetime import datetime, timedelta, timezone

import pytest

from ai_allowance_support import enable_ledger, fixture_policy
from app.extensions import db
from app.models import AiAllowanceBucket, AiUsageReservation
from app.services import ai_allowances as A

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def owner(make_user):
    return make_user("evidence-owner").id


def _bucket(user_id, feature="training_generation"):
    db.session.expire_all()
    return AiAllowanceBucket.query.filter_by(user_id=user_id, feature=feature).one_or_none()


def _row(reference):
    db.session.expire_all()
    return AiUsageReservation.query.filter_by(public_id=reference).one()


def test_reserve_within_does_not_commit_and_rolls_back_to_nothing(app, owner):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0001", now=NOW)
    assert hold.may_execute and hold.state == "RESERVED"
    db.session.rollback()
    assert AiUsageReservation.query.count() == 0
    # No hold survives. (SQLite commits the lazy grant's released SAVEPOINT;
    # the PostgreSQL module proves the grant rolls back too.)
    assert {(b.used, b.reserved) for b in AiAllowanceBucket.query} <= {(0, 0)}


def test_reserve_within_holds_with_unknown_evidence_and_never_replays(app, owner):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0002", now=NOW)
    db.session.commit()
    row = _row(hold.reference)
    assert (row.state, row.billing_evidence, row.provider_billed) == ("RESERVED", "UNKNOWN", None)
    assert (_bucket(owner).reserved, _bucket(owner).used) == (1, 0)
    with pytest.raises(A.AllowanceError) as error:
        A.reserve_within(owner, "training_generation", "within-key-0002", now=NOW)
    assert error.value.code == "AI_ALLOWANCE_IDEMPOTENCY_CONFLICT"
    db.session.rollback()
    assert AiUsageReservation.query.count() == 1


def test_reserve_within_uses_the_same_authority_as_reserve(app, owner):
    enable_ledger(app)  # Starter grants one training_generation unit
    A.reserve_within(owner, "training_generation", "within-key-0003", now=NOW)
    db.session.commit()
    with pytest.raises(A.AllowanceError) as error:
        A.reserve_within(owner, "training_generation", "within-key-0004", now=NOW)
    assert error.value.code == "AI_ALLOWANCE_EXHAUSTED"
    db.session.rollback()
    app.config["AI_ALLOWANCE_LEDGER_ENABLED"] = False
    with pytest.raises(A.AllowanceError) as error:
        A.reserve_within(owner, "training_generation", "within-key-0005", now=NOW)
    assert error.value.code == "AI_ALLOWANCE_DISABLED"

def test_reserve_within_without_a_plan_scope_is_not_in_plan(app, make_user):
    enable_ledger(app, policy=fixture_policy(starter=False, premium=True))
    fresh = make_user("evidence-no-scope").id
    with pytest.raises(A.AllowanceError) as error:
        A.reserve_within(fresh, "training_generation", "within-key-0006", now=NOW)
    assert error.value.code == "AI_FEATURE_NOT_IN_PLAN"


def test_legacy_reserve_rows_keep_the_boolean_and_no_evidence(app, owner):
    enable_ledger(app)
    hold = A.reserve(owner, "coach", "legacy-key-0001", now=NOW)
    row = _row(hold.reference)
    assert (row.provider_billed, row.billing_evidence) == (False, None)
    A.finalize(owner, hold.reference, "provider_error", provider_billed=True, now=NOW)
    row = _row(hold.reference)
    assert (row.state, row.provider_billed, row.billing_evidence) == ("RELEASED", True, None)


def test_unknown_success_consumes_once_and_stores_null_billing(app, owner):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0010", now=NOW)
    db.session.commit()
    first = A.finalize(owner, hold.reference, "success", billing_evidence="UNKNOWN", now=NOW)
    again = A.finalize(owner, hold.reference, "success", billing_evidence="UNKNOWN", now=NOW)
    assert (first.state, first.consumed, first.replayed) == ("COMMITTED", True, False)
    assert (again.state, again.replayed) == ("COMMITTED", True)
    row = _row(hold.reference)
    assert (row.provider_billed, row.billing_evidence) == (None, "UNKNOWN")
    assert (_bucket(owner).used, _bucket(owner).reserved) == (1, 0)


@pytest.mark.parametrize("cap,state,used,billed_releases", [
    (2, "RELEASED", 0, 1),   # bounded subsidy: within the founder-set cap
    (0, "COMMITTED", 1, 0),  # cap 0: every UNKNOWN failure consumes
])
def test_unknown_failure_uses_the_billed_release_cap(app, owner, cap, state, used, billed_releases):
    enable_ledger(app, policy=fixture_policy(max_billed=cap))
    hold = A.reserve_within(owner, "training_generation", "within-key-0020", now=NOW)
    db.session.commit()
    settled = A.finalize(owner, hold.reference, "invalid_output", billing_evidence="UNKNOWN", now=NOW)
    assert settled.state == state
    bucket = _bucket(owner)
    assert (bucket.used, bucket.reserved, bucket.billed_releases) == (used, 0, billed_releases)
    row = _row(hold.reference)
    assert (row.outcome, row.provider_billed, row.billing_evidence) == ("invalid_output", None, "UNKNOWN")


def test_a_bound_hold_refuses_a_boolean_that_would_overwrite_unknown(app, owner):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0030", now=NOW)
    db.session.commit()
    for kwargs in ({}, {"provider_billed": False}, {"provider_billed": True}):
        with pytest.raises(A.AllowanceError) as error:
            A.finalize(owner, hold.reference, "provider_error", now=NOW, **kwargs)
        assert error.value.code == "AI_ALLOWANCE_INVALID_REQUEST"
    row = _row(hold.reference)
    assert (row.state, row.provider_billed, row.billing_evidence) == ("RESERVED", None, "UNKNOWN")


@pytest.mark.parametrize("kwargs", [
    {"billing_evidence": "PROBABLY"},
    {"billing_evidence": "UNKNOWN", "provider_billed": True},
    {"billing_evidence": "CONFIRMED_BILLED", "provider_billed": True},
])
def test_malformed_evidence_is_refused_before_any_write(app, owner, kwargs):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0040", now=NOW)
    db.session.commit()
    with pytest.raises(A.AllowanceError) as error:
        A.finalize(owner, hold.reference, "provider_error", now=NOW, **kwargs)
    assert error.value.code == "AI_ALLOWANCE_INVALID_REQUEST"
    assert _row(hold.reference).state == "RESERVED"


@pytest.mark.parametrize("evidence", ["UNKNOWN", "CONFIRMED_BILLED"])
def test_never_billed_outcome_cannot_carry_possible_billing(app, owner, evidence):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0050", now=NOW)
    db.session.commit()
    with pytest.raises(A.AllowanceError) as error:
        A.finalize(owner, hold.reference, "refused_before_provider",
                   billing_evidence=evidence, now=NOW)
    assert error.value.code == "AI_ALLOWANCE_INVALID_REQUEST"


def test_expired_unknown_hold_settles_once_and_keeps_its_evidence(app, owner):
    enable_ledger(app)
    hold = A.reserve_within(owner, "training_generation", "within-key-0060", now=NOW)
    db.session.commit()
    late = NOW + A.RESERVATION_TTL + timedelta(seconds=1)
    settled = A.finalize(owner, hold.reference, "success", billing_evidence="UNKNOWN", now=late)
    assert (settled.state, settled.consumed, settled.replayed) == ("EXPIRED", True, True)
    row = _row(hold.reference)
    assert (row.state, row.provider_billed, row.billing_evidence) == ("EXPIRED", None, "UNKNOWN")
    assert (_bucket(owner).used, _bucket(owner).reserved) == (1, 0)


def test_confirmed_evidence_maps_to_the_existing_boolean_rules(app, owner):
    enable_ledger(app, policy=fixture_policy(max_billed=0))
    hold = A.reserve(owner, "coach", "confirmed-key-0001", now=NOW)
    settled = A.finalize(owner, hold.reference, "provider_error",
                         billing_evidence="CONFIRMED_NOT_BILLED", now=NOW)
    row = _row(hold.reference)
    assert settled.state == "RELEASED"
    assert (row.provider_billed, row.billing_evidence) == (False, "CONFIRMED_NOT_BILLED")
    hold = A.reserve(owner, "coach", "confirmed-key-0002", now=NOW)
    settled = A.finalize(owner, hold.reference, "provider_error",
                         billing_evidence="CONFIRMED_BILLED", now=NOW)
    assert settled.state == "COMMITTED"  # cap 0
    assert _row(hold.reference).provider_billed is True
