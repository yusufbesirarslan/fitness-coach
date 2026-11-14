"""Shared D1 grant-cohort scenarios (teen QR finding F-QR-W1, 2026-11-13).

Run twice: on the real PostgreSQL authority (test_ai_consent_cohort_grant_pg)
and on a SQLite stand-in for ``ai_consent._transaction``
(test_ai_consent_cohort_grant). Every disclosure is the REAL launch source
(POLICIES + LAUNCH_VERSIONS + PUBLISHED_TEXTS); nothing here opens a pin.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import AIConsentEvent, AIConsentState, User
from app.services import account_eligibility as eligibility, ai_consent as consent
from app.services.ai_consent_disclosures import LAUNCH_VERSIONS

REFUSAL = "AI_CONSENT_PURPOSE_UNAVAILABLE_FOR_ACCOUNT"
TRAINING = "training.plan"
FORBIDDEN_FOR_MINOR = ("coach.conversation", "nutrition.plan",
                       "pump_check.analysis", "qr_menu.analysis")
ALL_PURPOSES = (TRAINING,) + FORBIDDEN_FOR_MINOR


def signed_record(user, band):
    """The canonical subject-bound cohort record, as registration attaches it."""
    record = {"version": eligibility.VERSION, "age_band": band, "country": "TR",
              "source": "self_declared",
              "checked_at": datetime(2026, 11, 1, tzinfo=timezone.utc).isoformat()}
    if band == "13_17":
        record["minor_notice"] = eligibility.TR_MINOR_NOTICE_VERSION
    metadata = dict(user.user_metadata or {})
    metadata[eligibility.METADATA_KEY] = eligibility._signer(account=True).dumps(
        {"subject": user.cognito_sub, "record": record})
    user.user_metadata = metadata


def add_user(name, *, age=None, band=None):
    user = User(username=name, email=f"{name}@example.invalid", cognito_sub=f"sub-{name}", age=age)
    db.session.add(user)
    db.session.flush()
    if band:
        signed_record(user, band)
    db.session.commit()
    return user.id


def command(purpose, generation, key, action="affirmative_grant", locale="tr"):
    policy = consent.POLICIES[LAUNCH_VERSIONS[purpose]]
    return dict(action=action, purpose=purpose, version=policy.version, locale=locale,
                text_digest=dict(policy.texts)[locale],
                contract_digest=consent.contract_digest(policy),
                expected_generation=generation, request_id=key)


def durable(owner):
    """Everything a refused grant must leave untouched."""
    db.session.remove()
    state = db.session.get(AIConsentState, owner)
    events = db.session.execute(select(func.count()).select_from(AIConsentEvent)
                                .where(AIConsentEvent.owner_id == owner)).scalar_one()
    db.session.remove()
    return (state.generation if state else 0,
            dict(state.grants) if state else {}, events)


def refused(owner, data):
    before = durable(owner)
    with pytest.raises(consent.ConsentDenied) as caught:
        consent.mutate(owner, data)
    assert (caught.value.code, caught.value.status) == (REFUSAL, 403)
    assert durable(owner) == before
    return caught.value


def legacy_grant(monkeypatch, owner, purpose, key):
    """Record a grant through the pre-fix write path (as window 1 did)."""
    with monkeypatch.context() as patch:
        patch.setattr(consent, "_require_grant_cohort", lambda *a, **k: None)
        return consent.mutate(owner, command(purpose, durable(owner)[0], key))


# ── scenarios ───────────────────────────────────────────────────────────────

def adult_allowed_every_purpose():
    adult = add_user("cohort-adult", age=30, band="18_plus")
    for generation, purpose in enumerate(ALL_PURPOSES):
        result = consent.mutate(adult, command(purpose, generation, f"adult-{purpose}"))
        assert result["scopes"][purpose]["state"] == "granted"
    assert durable(adult)[0] == len(ALL_PURPOSES)
    assert durable(adult)[2] == len(ALL_PURPOSES)


def unscreened_adult_unchanged():
    # No signed record and no profile age (legacy account): no minority signal.
    legacy = add_user("cohort-unscreened")
    result = consent.mutate(legacy, command("qr_menu.analysis", 0, "unscreened-qr-0001"))
    assert result["scopes"]["qr_menu.analysis"]["state"] == "granted"


def teen_training_allowed():
    teen = add_user("cohort-teen-training", age=15, band="13_17")
    result = consent.mutate(teen, command(TRAINING, 0, "teen-training-0001"))
    assert result["scopes"][TRAINING]["state"] == "granted"
    assert durable(teen)[0] == 1 and durable(teen)[2] == 1
    # And withdrawal of it.
    consent.mutate(teen, command(TRAINING, 1, "teen-training-0002", action="revoke"))
    assert durable(teen)[1][TRAINING]["state"] == "revoked"


def teen_forbidden_each_purpose():
    teen = add_user("cohort-teen-forbidden", age=15, band="13_17")
    for purpose in FORBIDDEN_FOR_MINOR:
        for locale in ("tr", "en"):
            refused(teen, command(purpose, 0, f"teen-{purpose}-{locale}"[:80], locale=locale))
    assert durable(teen) == (0, {}, 0)
    # A refused request id is not consumed: the same id may later carry Training.
    result = consent.mutate(teen, command(TRAINING, 0, "teen-qr_menu.analysis-tr"))
    assert result["scopes"][TRAINING]["state"] == "granted"


def profile_age_minor_forbidden():
    # A profile age under 18 restricts even without a signed record.
    minor = add_user("cohort-profile-minor", age=16)
    refused(minor, command("qr_menu.analysis", 0, "profile-minor-qr-0001"))
    consent.mutate(minor, command(TRAINING, 0, "profile-minor-training-0001"))
    # A signed adult record cannot erase a contradicting profile minority.
    contradicted = add_user("cohort-contradicted", age=16, band="18_plus")
    refused(contradicted, command("coach.conversation", 0, "contradicted-coach-0001"))


def legacy_grant_inspect_withdraw_and_no_regrant(monkeypatch):
    teen = add_user("cohort-teen-legacy", age=15, band="13_17")
    legacy_grant(monkeypatch, teen, "qr_menu.analysis", "legacy-qr-grant-0001")
    generation, grants, events = durable(teen)
    assert (generation, events) == (1, 1)
    # Inspection: status still reports the historical grant, unrewritten.
    status = consent.status(teen)
    assert status["scopes"]["qr_menu.analysis"]["state"] == "granted"
    # Denied repeat grant while the legacy grant stands: record byte-identical.
    refused(teen, command("qr_menu.analysis", 1, "legacy-qr-regrant-0001"))
    assert durable(teen)[1] == grants
    # Exact replay of the historical request id still answers (no new event).
    replay = consent.mutate(teen, command("qr_menu.analysis", 0, "legacy-qr-grant-0001"))
    assert replay["generation"] == 1 and durable(teen)[2] == 1
    # Withdrawal works.
    after = consent.mutate(teen, command("qr_menu.analysis", 1, "legacy-qr-revoke-0001",
                                         action="revoke"))
    assert after["generation"] == 2
    generation, grants, events = durable(teen)
    assert (generation, events) == (2, 2)
    assert grants["qr_menu.analysis"]["state"] == "revoked"
    # Denied repeat grant after withdrawal: no event, no generation change.
    refused(teen, command("qr_menu.analysis", 2, "legacy-qr-regrant-0002"))
    # Lineage kept: both historical events remain, in order.
    rows = db.session.execute(select(AIConsentEvent.action, AIConsentEvent.generation)
                              .where(AIConsentEvent.owner_id == teen)
                              .order_by(AIConsentEvent.generation)).all()
    db.session.remove()
    assert [tuple(r) for r in rows] == [("affirmative_grant", 1), ("revoke", 2)]


def pins_do_not_widen_minor_grants(monkeypatch):
    from app.services import ai_launch
    monkeypatch.setattr(eligibility, "APPROVED_TR_MINOR_NOTICE_VERSION",
                        eligibility.TR_MINOR_NOTICE_VERSION)
    monkeypatch.setattr(eligibility, "APPROVED_TR_MINOR_TRAINING_AI_VERSION",
                        eligibility.TR_MINOR_TRAINING_AI_VERSION)
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", eligibility.VERSION)
    teen = add_user("cohort-teen-pinned", age=15, band="13_17")
    consent.mutate(teen, command(TRAINING, 0, "pinned-training-0001"))
    for purpose in FORBIDDEN_FOR_MINOR:
        refused(teen, command(purpose, 1, f"pinned-{purpose}"[:80]))
