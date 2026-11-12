"""TR 13-17 onboarding: account registration only, every restricted lane closed.

The minor's own notice acknowledgment is Terms/notice evidence, never parental
consent, age assurance, health/AI permission or a capability promotion.
"""
import pytest

from app.extensions import db
from app.models import User
from app.services import account_eligibility as policy, account_registration, ai_launch
from app.services.ai_consent import ConsentDenied

NOTICE = policy.TR_MINOR_NOTICE_VERSION
WIRE_CAPS = {"body_photos": False, "automated_weight_targets": False, "social": False}
RESTRICTED = ("automated_weight_targets", "body_photos", "social")
TEEN_ALLOWED = ("health_data", "general_profile")


@pytest.fixture(autouse=True)
def enable(app):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", NOTICE)


def ticket(age, country="TR"):
    return policy.preview({"age": age, "country": country})["eligibility"]["registration_token"]


def teen_user(make_user, age=15, name="trteen"):
    user = make_user(name)
    policy.attach(user, policy.registration_record(ticket(age), NOTICE))
    db.session.commit()
    return user


def test_source_pin_is_closed_by_default():
    assert policy.APPROVED_TR_MINOR_NOTICE_VERSION is None
    assert policy.tr_minor_open() is False


@pytest.mark.parametrize("age", [13, 15, 17])
def test_default_keeps_tr_minors_in_qualification_required(age):
    result = policy.preview({"age": age, "country": "TR"})["eligibility"]
    assert (result["status"], result["reason"]) == (
        "qualification_required", "MARKET_QUALIFICATION_REQUIRED")
    assert result["registration_token"] is None and result["capabilities"] == WIRE_CAPS


@pytest.mark.parametrize("age,country,status,band", [
    (12, "TR", "ineligible", None), (13, "TR", "screen_passed", "13_17"),
    (17, "TR", "screen_passed", "13_17"), (18, "TR", "screen_passed", "18_plus"),
    (13, "CA", "qualification_required", "13_17"), (14, "AU", "qualification_required", "13_17"),
])
def test_pin_opens_only_tr_13_17_registration_with_no_capability(pinned, age, country, status, band):
    result = policy.evaluate({"age": age, "country": country})
    assert (result["status"], result["age_band"]) == (status, band)
    if band == "13_17":
        assert result["capabilities"] == WIRE_CAPS


@pytest.mark.parametrize("notice", [None, "", "tr-minor-notice-v0", True, ["tr-minor-notice-v1"]])
def test_tr_minor_registration_requires_exact_acknowledged_notice(app, monkeypatch, pinned, notice):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: pytest.fail("provider called"))
    with pytest.raises(account_registration.RegistrationFailure) as caught:
        account_registration.register_account(
            "trteen", "fixture@example.invalid", "Sifre123",
            eligibility_token=ticket(16), minor_notice=notice)
    assert caught.value.outcome == account_registration.Outcome.ELIGIBILITY_REQUIRED
    assert User.query.count() == 0


@pytest.mark.parametrize("age,country", [(18, "TR"), (16, "GB"), (30, "US")])
def test_notice_is_refused_outside_the_tr_minor_cohort(pinned, age, country):
    with pytest.raises(policy.EligibilityDenied):
        policy.registration_record(ticket(age, country), NOTICE)
    assert policy.registration_record(ticket(age, country))["age_band"]


def test_registered_tr_minor_gets_only_teen_lanes(app, monkeypatch, pinned):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: "sub-trteen")
    account_registration.register_account(
        "trteen", "fixture@example.invalid", "Sifre123",
        eligibility_token=ticket(14), minor_notice=NOTICE)
    user = User.query.one()
    assert user.age == 14 and user.profile_complete is False
    # Wire projection key set is unchanged (shipped mobile parser contract).
    assert policy.projection(user) == {"status": "screen_passed", "age_band": "13_17",
        "country": "TR", "source": "self_declared", "capabilities": WIRE_CAPS}
    for capability in TEEN_ALLOWED:
        policy.require_capability(user, capability)
    for capability in RESTRICTED:
        with pytest.raises(policy.EligibilityDenied) as caught:
            policy.require_capability(user, capability)
        assert caught.value.code == "ELIGIBILITY_FEATURE_UNAVAILABLE"
    from datetime import datetime, timezone
    assert policy.purchase_eligible(user, datetime.now(timezone.utc)) is False
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", policy.VERSION)
    with pytest.raises(ConsentDenied):
        ai_launch.require_cohort(user)


def test_adult_tr_keeps_health_data_separate_from_teen_path(app, make_user, pinned):
    adult = make_user("tradult")
    policy.attach(adult, policy.registration_record(ticket(30)))
    db.session.commit()
    for capability in ("health_data", "automated_weight_targets", "body_photos"):
        policy.require_capability(adult, capability)
    with pytest.raises(policy.EligibilityDenied):
        policy.require_capability(adult, "social")


def test_removing_the_pin_returns_tr_minor_to_review_required(app, make_user, monkeypatch, pinned):
    user = teen_user(make_user)
    assert policy.projection(user)["status"] == "screen_passed"
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", None)
    assert policy.projection(user)["status"] == "review_required"


def test_signed_records_cannot_drop_or_borrow_the_notice(app, make_user, pinned):
    teen = teen_user(make_user)
    envelope = policy._signer(account=True).loads(teen.user_metadata[policy.METADATA_KEY])
    stripped = dict(envelope["record"]); stripped.pop("minor_notice")
    borrowed = dict(envelope["record"], country="GB")
    for record in (stripped, borrowed, dict(envelope["record"], minor_notice="other")):
        teen.user_metadata = {policy.METADATA_KEY: policy._signer(account=True).dumps(
            {"subject": teen.cognito_sub, "record": record})}
        assert policy.projection(teen)["status"] == "review_required"


def test_existing_account_review_cannot_attach_a_tr_minor_without_notice(app, make_user, pinned):
    user = make_user("legacyteen")
    with pytest.raises(policy.EligibilityDenied):
        policy.review_existing(user, ticket(16))
    db.session.refresh(user)
    assert policy.METADATA_KEY not in (user.user_metadata or {})


def test_native_register_carries_the_notice_version(app, monkeypatch, pinned):
    from app.blueprints import mobile_registration
    from app.services import cognito_service
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: "sub-native-teen")
    client = app.test_client()
    body = {"username": "nativeteen", "email": "fixture@example.invalid",
            "password": "Sifre123", "eligibility_token": ticket(13)}
    assert client.post("/api/v1/auth/register", json=body).status_code == 422
    assert User.query.count() == 0
    body["minor_notice_version"] = NOTICE
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    assert policy.projection(User.query.one())["age_band"] == "13_17"


def test_health_consent_grant_needs_the_live_pin_for_tr_minor(app, make_user, monkeypatch, pinned):
    from flask import g
    from app.blueprints import health_consent
    user = teen_user(make_user)
    called = []
    monkeypatch.setattr(health_consent.consent, "mutate", lambda *a: called.append(a))
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", None)
    with app.test_request_context(json={"action": "affirmative_grant"}, method="POST"):
        g.mobile_user = user
        with pytest.raises(policy.EligibilityDenied):
            health_consent._respond(user)
    assert called == []
