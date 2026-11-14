"""Shared D1 grant-cohort scenarios.

F-QR-W1 (teen QR grant, 2026-11-13) and the screened-grant closure
(2026-11-14): a NEW affirmative D1 grant needs an affirmative screened cohort
from the canonical subject-bound signed record, whatever
ACCOUNT_ELIGIBILITY_ENABLED says. Run twice: on the real PostgreSQL authority
(test_ai_consent_cohort_grant_pg) and on a SQLite stand-in for
``ai_consent._transaction`` (test_ai_consent_cohort_grant). Every disclosure is
the REAL launch source (POLICIES + LAUNCH_VERSIONS + PUBLISHED_TEXTS). The TR
minor notice pin ships None, so a qualified teen exists only where a scenario
opens it (``open_minor_notice``); nothing else opens a pin.
"""
from datetime import datetime, timedelta, timezone

import pytest
from flask import current_app
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
DECLARED = datetime(2026, 11, 1, tzinfo=timezone.utc).isoformat()


def record_for(band, country="TR", **changes):
    record = {"version": eligibility.VERSION, "age_band": band, "country": country,
              "source": "self_declared", "checked_at": DECLARED}
    if band == "13_17" and country == "TR":
        record["minor_notice"] = eligibility.TR_MINOR_NOTICE_VERSION
    record.update(changes)
    return {key: value for key, value in record.items() if value is not None}


def signed_envelope(subject, record):
    return eligibility._signer(account=True).dumps({"subject": subject, "record": record})


def signed_record(user, band, country="TR", **changes):
    """The canonical subject-bound cohort record, as registration attaches it."""
    metadata = dict(user.user_metadata or {})
    metadata[eligibility.METADATA_KEY] = signed_envelope(
        user.cognito_sub, record_for(band, country, **changes))
    user.user_metadata = metadata


def add_user(name, *, age=None, band=None, raw=None, **record_changes):
    user = User(username=name, email=f"{name}@example.invalid", cognito_sub=f"sub-{name}", age=age)
    db.session.add(user)
    db.session.flush()
    if band:
        signed_record(user, band, **record_changes)
    if raw is not None:
        user.user_metadata = {eligibility.METADATA_KEY: raw}
    db.session.commit()
    return user.id


def open_minor_notice(monkeypatch):
    """The source pin that makes a TR 13-17 record canonical (ships None)."""
    monkeypatch.setattr(eligibility, "APPROVED_TR_MINOR_NOTICE_VERSION",
                        eligibility.TR_MINOR_NOTICE_VERSION)


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


def refused_everything(owner, tag):
    for purpose in ALL_PURPOSES:
        for locale in ("tr", "en"):
            refused(owner, command(purpose, durable(owner)[0], f"{tag}-{purpose}-{locale}"[:80],
                                   locale=locale))


def granted(owner, purpose, key):
    result = consent.mutate(owner, command(purpose, durable(owner)[0], key))
    assert result["scopes"][purpose]["state"] == "granted"
    return result


def legacy_grant(monkeypatch, owner, purpose, key):
    """Record a grant through the pre-fix write path (as window 1 did)."""
    with monkeypatch.context() as patch:
        patch.setattr(consent, "_require_grant_cohort", lambda *a, **k: None)
        return consent.mutate(owner, command(purpose, durable(owner)[0], key))


def eligibility_flag(value):
    current_app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = value


# ── scenarios ───────────────────────────────────────────────────────────────

def adult_allowed_every_purpose():
    for flag in (False, True):
        eligibility_flag(flag)
        adult = add_user(f"cohort-adult-{flag}", age=30, band="18_plus")
        for purpose in ALL_PURPOSES:
            granted(adult, purpose, f"adult-{purpose}")
        assert durable(adult)[0] == len(ALL_PURPOSES)
        assert durable(adult)[2] == len(ALL_PURPOSES)


def screened_adult_without_profile_age_allowed():
    # A reviewed legacy adult whose profile age is unset is not contradicted.
    adult = add_user("cohort-adult-no-age", band="18_plus")
    granted(adult, "coach.conversation", "adult-no-age-coach-0001")


def unscreened_refused_regardless_of_flag():
    for flag in (False, True):
        eligibility_flag(flag)
        # Legacy account: no record, no profile age.
        refused_everything(add_user(f"cohort-unscreened-{flag}"), f"unscreened-{flag}")
        # A profile age is never affirmative authority, adult or not.
        refused_everything(add_user(f"cohort-profile-adult-{flag}", age=30), f"p-adult-{flag}")
        refused_everything(add_user(f"cohort-profile-minor-{flag}", age=16), f"p-minor-{flag}")


def refusal_consumes_no_request_id():
    owner = add_user("cohort-later-screened", age=30)
    refused(owner, command(TRAINING, 0, "later-screened-0001"))
    signed_record(db.session.get(User, owner), "18_plus")
    db.session.commit()
    db.session.remove()
    # The refused id, generation 0, is still unused.
    granted(owner, TRAINING, "later-screened-0001")
    assert durable(owner)[0] == 1 and durable(owner)[2] == 1


def malformed_records_refused():
    other = signed_envelope("sub-someone-else", record_for("18_plus"))
    good = signed_envelope("sub-cohort-tampered", record_for("18_plus"))
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    cases = {
        "tampered": dict(raw=good[:-2] + ("AA" if not good.endswith("AA") else "BB")),
        "forged": dict(raw="forged.value"),
        "not-a-string": dict(raw={"subject": "x", "record": record_for("18_plus")}),
        "oversized": dict(raw="x" * 4096),
        "foreign-subject": dict(raw=other),
        "wrong-version": dict(band="18_plus", version="age-screen-v0"),
        "wrong-source": dict(band="18_plus", source="operator"),
        "extra-key": dict(band="18_plus", status="screen_passed"),
        "bad-band": dict(band="18_plus", age_band="21_plus"),
        "naive-date": dict(band="18_plus", checked_at="2026-11-01T00:00:00"),
        "future-date": dict(band="18_plus", checked_at=future),
        "unsupported-market": dict(band="18_plus", country="US"),
        "unknown-market": dict(band="18_plus", country="DE"),
    }
    for name, setup in cases.items():
        owner = add_user(f"cohort-{name}", age=30, **setup)
        refused_everything(owner, name)
        assert durable(owner) == (0, {}, 0)


def conflicting_profile_ages_refused(monkeypatch):
    open_minor_notice(monkeypatch)
    for name, age, band in (("adult-record-minor-age", 16, "18_plus"),
                            ("adult-record-child-age", 12, "18_plus"),
                            ("teen-record-adult-age", 20, "13_17"),
                            ("teen-record-child-age", 12, "13_17"),
                            ("teen-record-no-age", None, "13_17")):
        refused_everything(add_user(f"cohort-{name}", age=age, band=band), name)


def teen_training_allowed(monkeypatch):
    open_minor_notice(monkeypatch)
    teen = add_user("cohort-teen-training", age=15, band="13_17")
    granted(teen, TRAINING, "teen-training-0001")
    assert durable(teen)[0] == 1 and durable(teen)[2] == 1
    # And withdrawal of it.
    consent.mutate(teen, command(TRAINING, 1, "teen-training-0002", action="revoke"))
    assert durable(teen)[1][TRAINING]["state"] == "revoked"


def teen_forbidden_each_purpose(monkeypatch):
    open_minor_notice(monkeypatch)
    teen = add_user("cohort-teen-forbidden", age=15, band="13_17")
    for purpose in FORBIDDEN_FOR_MINOR:
        for locale in ("tr", "en"):
            refused(teen, command(purpose, 0, f"teen-{purpose}-{locale}"[:80], locale=locale))
    assert durable(teen) == (0, {}, 0)
    # A refused request id is not consumed: the same id may later carry Training.
    granted(teen, TRAINING, "teen-qr_menu.analysis-tr")


def unpinned_teen_record_refused():
    # Shipping state: the minor notice pin is closed, so a signed 13-17 record
    # is not canonical. Fail closed, Training included.
    teen = add_user("cohort-teen-unpinned", age=15, band="13_17")
    refused_everything(teen, "unpinned")


def teen_record_without_notice_refused(monkeypatch):
    open_minor_notice(monkeypatch)
    for name, changes in (("no-notice", dict(minor_notice=None)),
                          ("wrong-notice", dict(minor_notice="tr-minor-notice-v0"))):
        refused_everything(add_user(f"cohort-teen-{name}", age=15, band="13_17", **changes), name)


def legacy_grant_inspect_withdraw_and_no_regrant(monkeypatch, *, band, age, tag):
    owner = add_user(f"cohort-legacy-{tag}", age=age, band=band)
    legacy_grant(monkeypatch, owner, "qr_menu.analysis", f"legacy-{tag}-grant-0001")
    generation, grants, events = durable(owner)
    assert (generation, events) == (1, 1)
    # Inspection: status still reports the historical grant, unrewritten.
    status = consent.status(owner)
    assert status["scopes"]["qr_menu.analysis"]["state"] == "granted"
    # Denied repeat grant while the legacy grant stands: record byte-identical.
    refused(owner, command("qr_menu.analysis", 1, f"legacy-{tag}-regrant-0001"))
    assert durable(owner)[1] == grants
    # Exact replay of the historical request id still answers (no new event).
    replay = consent.mutate(owner, command("qr_menu.analysis", 0, f"legacy-{tag}-grant-0001"))
    assert replay["generation"] == 1 and durable(owner)[2] == 1
    # Withdrawal works, with no cohort check.
    after = consent.mutate(owner, command("qr_menu.analysis", 1, f"legacy-{tag}-revoke-0001",
                                          action="revoke"))
    assert after["generation"] == 2
    generation, grants, events = durable(owner)
    assert (generation, events) == (2, 2)
    assert grants["qr_menu.analysis"]["state"] == "revoked"
    # Replay of the withdrawal is idempotent.
    again = consent.mutate(owner, command("qr_menu.analysis", 1, f"legacy-{tag}-revoke-0001",
                                          action="revoke"))
    assert again["generation"] == 2 and durable(owner)[2] == 2
    # Denied repeat grant after withdrawal: no event, no generation change.
    refused(owner, command("qr_menu.analysis", 2, f"legacy-{tag}-regrant-0002"))
    # Lineage kept: both historical events remain, in order.
    rows = db.session.execute(select(AIConsentEvent.action, AIConsentEvent.generation)
                              .where(AIConsentEvent.owner_id == owner)
                              .order_by(AIConsentEvent.generation)).all()
    db.session.remove()
    assert [tuple(r) for r in rows] == [("affirmative_grant", 1), ("revoke", 2)]


def legacy_grants(monkeypatch):
    # Unscreened legacy adult, and a teen whose record is not canonical.
    legacy_grant_inspect_withdraw_and_no_regrant(monkeypatch, band=None, age=None, tag="adult")
    legacy_grant_inspect_withdraw_and_no_regrant(monkeypatch, band="13_17", age=15, tag="teen")


def screened_adult_replay_after_record_loss():
    # Replay answers from the event table, not the cohort: it never resurrects
    # or re-authorizes, so a record lost after the grant leaves replay intact.
    adult = add_user("cohort-adult-lost-record", age=30, band="18_plus")
    granted(adult, TRAINING, "lost-record-grant-0001")
    db.session.get(User, adult).user_metadata = {}
    db.session.commit()
    db.session.remove()
    replay = consent.mutate(adult, command(TRAINING, 0, "lost-record-grant-0001"))
    assert replay["generation"] == 1 and durable(adult)[2] == 1
    consent.mutate(adult, command(TRAINING, 1, "lost-record-revoke-0001", action="revoke"))
    refused(adult, command(TRAINING, 2, "lost-record-regrant-0001"))


def pins_do_not_widen_minor_grants(monkeypatch):
    from app.services import ai_launch
    open_minor_notice(monkeypatch)
    monkeypatch.setattr(eligibility, "APPROVED_TR_MINOR_TRAINING_AI_VERSION",
                        eligibility.TR_MINOR_TRAINING_AI_VERSION)
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", eligibility.VERSION)
    eligibility_flag(True)
    teen = add_user("cohort-teen-pinned", age=15, band="13_17")
    granted(teen, TRAINING, "pinned-training-0001")
    for purpose in FORBIDDEN_FOR_MINOR:
        refused(teen, command(purpose, 1, f"pinned-{purpose}"[:80]))
