"""Age declaration != health/AI consent; exercise real authorities and sinks."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from flask import request
from itsdangerous import TimestampSigner

from app.extensions import db
from app.models import User, UserSession
from app.services import account_eligibility as policy, account_registration
from eligibility_support import born, declaration, legacy_minor_record


@pytest.fixture(autouse=True)
def enable(app):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True


def ticket(age=30, country="GB"):
    return policy.preview(declaration(age, country))["eligibility"]["registration_token"]


def attach_record(user, age, country="GB"):
    """An adult declaration through the screen; under 18 only as the legacy
    signed record a pre-FDO-AGE18 account could hold (never issued now)."""
    if age < 18:
        legacy_minor_record(user, age, country)
    else:
        policy.attach(user, policy.registration_record(ticket(age, country)))
    db.session.commit()
    return user


def record_user(make_user, age=17, country="GB"):
    return attach_record(make_user(f"age{age}{country}"), age, country)


@pytest.mark.parametrize("country", sorted(policy.COUNTRIES))
@pytest.mark.parametrize("age,status", [
    (5, "ineligible"), (12, "ineligible"), (13, "ineligible"), (14, "ineligible"),
    (15, "ineligible"), (17, "ineligible"), (18, "screen_passed"),
    (30, "screen_passed"), (120, "screen_passed")])
def test_adult_only_in_every_supported_market(age, status, country):
    result = policy.preview(declaration(age, country))["eligibility"]
    assert result["status"] == status
    assert result["reason"] == (None if status == "screen_passed" else "UNDER_MINIMUM_AGE")
    assert bool(result["registration_token"]) == (status == "screen_passed")
    assert result["capabilities"] == {"social": False,
        "body_photos": status == "screen_passed",
        "automated_weight_targets": status == "screen_passed"}


def test_unsupported_market_and_v1_minor_pins_are_closed():
    result = policy.preview(declaration(30, "DE"))["eligibility"]
    assert (result["status"], result["reason"]) == ("ineligible", "MARKET_NOT_SUPPORTED")
    assert result["registration_token"] is None
    # FDO-AGE18: the only under-18 admission channels stay closed in source.
    assert policy.APPROVED_TR_MINOR_NOTICE_VERSION is None
    assert policy.APPROVED_TR_MINOR_TRAINING_AI_VERSION is None
    assert policy.ADULT_AGE == 18


@pytest.mark.parametrize("today,birth_date,passes", [
    ("2026-11-14", "2008-11-14", True),    # 18th birthday today
    ("2026-11-14", "2008-11-15", False),   # 18th birthday tomorrow
    ("2026-11-14", "2008-11-13", True),
    ("2026-11-14", "2008-12-31", False),   # right birth year, not yet 18
    ("2026-01-01", "2008-01-01", True), ("2026-12-31", "2009-01-01", False),
    ("2026-02-28", "2008-02-29", False),   # leap-day birthday: 1 March
    ("2026-03-01", "2008-02-29", True),
    ("2028-02-29", "2010-02-28", True), ("2028-02-29", "2010-03-01", False),
])
def test_exact_eighteenth_birthday_boundary(today, birth_date, passes):
    from datetime import datetime
    from app.timeutil import APP_TZ, audit_clock
    with audit_clock(datetime.fromisoformat(today + "T12:00:00").replace(tzinfo=APP_TZ)):
        result = policy.preview({"birth_date": birth_date, "country": "TR"})["eligibility"]
    assert (result["status"] == "screen_passed") is passes
    assert bool(result["registration_token"]) is passes


def test_the_day_is_the_istanbul_calendar_day_not_utc():
    from datetime import datetime, timezone
    from app.timeutil import audit_clock
    # 21:30 UTC on the 13th is already the 14th in Istanbul: the birthday.
    with audit_clock(datetime(2026, 11, 13, 21, 30, tzinfo=timezone.utc)):
        assert policy.evaluate({"birth_date": "2008-11-14", "country": "TR"})[
            "status"] == "screen_passed"
    with audit_clock(datetime(2026, 11, 13, 20, 30, tzinfo=timezone.utc)):
        assert policy.evaluate({"birth_date": "2008-11-14", "country": "TR"})[
            "status"] == "ineligible"


@pytest.mark.parametrize("data", [None, {}, [], "2000-01-01",
    # The retired integer-age contract, in every shape an old client can send.
    {"age": 18, "country": "GB"}, {"age": 30, "country": "TR"},
    {"age": 30, "birth_date": "1990-01-01", "country": "TR"},
    {"birth_date": "1990-01-01", "country": "TR", "approved": True},
    {"birth_date": "1990-01-01"}, {"birth_date": "1990-01-01", "country": []},
    {"birth_date": "1990-01-01", "country": "TUR"},
    {"birth_date": 19900101, "country": "TR"}, {"birth_date": None, "country": "TR"},
    {"birth_date": "1990-1-1", "country": "TR"}, {"birth_date": "01-01-1990", "country": "TR"},
    {"birth_date": "1990-01-01T00:00:00", "country": "TR"},
    {"birth_date": " 1990-01-01", "country": "TR"},
    {"birth_date": "1990-02-30", "country": "TR"}, {"birth_date": "1990-13-01", "country": "TR"},
    {"birth_date": "0000-01-01", "country": "TR"}, {"birth_date": "1800-01-01", "country": "TR"},
    {"birth_date": "9999-01-01", "country": "TR"}])
def test_bad_types_and_client_authority_are_not_declarations(data):
    with pytest.raises(policy.EligibilityDenied) as caught:
        policy.preview(data)
    assert caught.value.code == "AUTH_ELIGIBILITY_INVALID_REQUEST"
    assert caught.value.status == 400


@pytest.mark.parametrize("days", [0, 1, 400])
def test_today_or_a_future_date_is_not_a_date_of_birth(days):
    with pytest.raises(policy.EligibilityDenied) as caught:
        policy.preview({"birth_date": born(0, days=days), "country": "TR"})
    assert caught.value.code == "AUTH_ELIGIBILITY_INVALID_REQUEST"


@pytest.mark.parametrize("route", ["/api/v1/auth/eligibility", "/eligibility"])
def test_old_integer_age_clients_get_no_ticket_on_either_transport(app, route):
    client = app.test_client()
    response = client.post(route, json={"age": 30, "country": "TR"})
    assert response.status_code == 400
    assert "registration_token" not in response.get_data(as_text=True)
    response = client.post(route, json=declaration(30))
    assert response.status_code == 200
    assert response.json["eligibility"]["registration_token"]


@pytest.mark.parametrize("age,country", [(13, "US"), (17, "GB"), (14, "CA"),
                                         (15, "AU"), (17, "TR"), (12, "TR")])
def test_a_ticket_issued_under_the_old_rule_no_longer_registers(app, monkeypatch, age, country):
    """A still-live ticket minted before this rule (old slot, old cohort) is
    re-screened: it creates no provider account and no local row."""
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: pytest.fail("provider called"))
    stale = policy._signer().dumps({"version": policy.VERSION, "age": age, "country": country})
    with pytest.raises(policy.EligibilityDenied):
        policy.registration_record(stale)
    with pytest.raises(account_registration.RegistrationFailure) as caught:
        account_registration.register_account("staleuser", "fixture@example.invalid", "Sifre123",
                                             eligibility_token=stale)
    assert caught.value.outcome == account_registration.Outcome.ELIGIBILITY_REQUIRED
    assert User.query.count() == 0


def test_date_of_birth_is_not_carried_into_the_ticket_or_the_account(app, monkeypatch):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: "sub-dob")
    birth_date = born(30, days=-200)
    token = policy.preview({"birth_date": birth_date, "country": "TR"})[
        "eligibility"]["registration_token"]
    assert policy._signer().loads(token) == {
        "version": policy.VERSION, "age": 30, "country": "TR"}
    account_registration.register_account("dobuser", "fixture@example.invalid", "Sifre123",
                                         eligibility_token=token)
    user = User.query.one()
    envelope = policy._signer(account=True).loads(user.user_metadata[policy.METADATA_KEY])
    assert set(envelope["record"]) == {"version", "age_band", "country", "source", "checked_at"}
    assert user.age == 30
    stored = repr({c.name: getattr(user, c.name) for c in User.__table__.columns}) + repr(envelope)
    assert birth_date not in stored and birth_date.replace("-", "") not in stored


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("country", ["GB", "US", "TR"])
def test_legacy_signed_minor_record_stays_closed_whatever_the_flag_says(
        app, make_user, flag, country):
    from datetime import datetime, timezone
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = flag
    user = make_user("legacyminor")
    legacy_minor_record(user, 17, country)
    db.session.commit()
    now = datetime.now(timezone.utc)
    assert policy._record(user) is None
    assert policy.projection(user)["status"] == "review_required"
    assert not any(policy.projection(user)["capabilities"].values())
    assert policy.minority_signal(user) is True
    assert policy.d1_grant_cohort(user, now) is None
    assert policy.purchase_eligible(user, now) is False
    for feature in [None, "training_plan", "coach"]:
        assert policy.ai_service_eligible(user, feature, now) is False
    for capability in ["health_data", "general_profile", "automated_weight_targets",
                       "body_photos", "social"]:
        with pytest.raises(policy.EligibilityDenied):
            policy.require_capability(user, capability)
    # Growing the editable profile age is not a declaration either.
    user.age = 30
    assert policy._record(user) is None
    with pytest.raises(policy.EligibilityDenied):
        policy.require_capability(user, "health_data")


def test_adult_profile_age_under_eighteen_is_refused_not_stored(app, make_user):
    from app.services import account_profile
    user = record_user(make_user, 30)
    def profile(age):
        return account_profile.OnboardingProfile(
            weight=80, height=180, age=age, gender="male", goal="build_muscle",
            fitness_level="beginner", current_activity="sedentary")
    with pytest.raises(account_profile.ProfileRejected) as caught:
        account_profile.complete_onboarding(user, profile(17))
    assert (caught.value.field, caught.value.reason) == ("age", "range")
    assert user.age == 30 and UserSession.query.count() == 0
    assert policy.projection(user)["age_band"] == "18_plus"


@pytest.mark.parametrize("value", [None, "", "bogus", "a" * 1025])
def test_no_contact_provider_or_local_row_without_valid_ticket(app, monkeypatch, value):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: pytest.fail("provider called"))
    with pytest.raises(account_registration.RegistrationFailure) as caught:
        account_registration.register_account("ticketuser", "fixture@example.invalid", "Sifre123",
                                             eligibility_token=value)
    assert caught.value.outcome == account_registration.Outcome.ELIGIBILITY_REQUIRED
    assert User.query.count() == 0


def test_expired_and_other_purpose_tickets_do_not_register(app, monkeypatch):
    original = TimestampSigner.get_timestamp
    now = original(TimestampSigner("test"))
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda _: now - 601)
    expired = ticket()
    monkeypatch.setattr(TimestampSigner, "get_timestamp", original)
    for value in [expired, policy._signer(account=True).dumps(
            {"version": policy.VERSION, "age": 18, "country": "GB"})]:
        with pytest.raises(policy.EligibilityDenied):
            policy.registration_record(value)


def test_registration_persists_bound_cohort_not_a_consent(app, monkeypatch):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: "sub-declared")
    account_registration.register_account("declared", "fixture@example.invalid", "Sifre123",
                                         eligibility_token=ticket())
    user = User.query.one()
    assert user.age == 30 and user.profile_complete is False
    assert policy.projection(user) == {"status": "screen_passed", "age_band": "18_plus",
        "country": "GB", "source": "self_declared", "capabilities": {
            "body_photos": True, "automated_weight_targets": True, "social": False}}
    from app.models import AIConsentState
    assert AIConsentState.query.count() == 0
    assert UserSession.query.count() == 0


def test_profile_age_and_copied_record_cannot_promote_minority(app, make_user):
    teen = record_user(make_user)
    teen.age = 30
    assert policy.projection(teen)["capabilities"]["body_photos"] is False
    other = make_user("different")
    other.user_metadata = dict(teen.user_metadata)
    assert policy.projection(other)["status"] == "review_required"
    with pytest.raises(policy.EligibilityDenied):
        policy.review_existing(teen, ticket(30))
    assert policy.projection(teen)["status"] == "review_required"
    assert policy.minority_signal(teen) is True


def test_missing_corrupt_and_legacy_records_never_default_adult(app, make_user):
    user = make_user("legacy")
    user.age = 30
    assert not any(policy.projection(user)["capabilities"].values())
    user.user_metadata = {policy.METADATA_KEY: "corrupt"}
    assert not any(policy.projection(user)["capabilities"].values())
    with pytest.raises(policy.EligibilityDenied):
        policy.review_existing(user, ticket(30))
    user.user_metadata = None
    user.age = 17
    with pytest.raises(policy.EligibilityDenied):
        policy.review_existing(user, ticket(30))


def test_atomic_legacy_review_is_idempotent_and_keeps_metadata(app, make_user):
    user = make_user("review")
    user.user_metadata = {"existing": "fixture"}
    first = policy.review_existing(user, ticket(18))
    assert first["age_band"] == "18_plus"
    assert user.user_metadata["existing"] == "fixture"
    assert policy.review_existing(user, ticket(18)) == first
    with pytest.raises(policy.EligibilityDenied):
        policy.review_existing(user, ticket(18, "US"))


def test_business_authorities_refuse_before_weight_math_or_mutation(app, make_user, monkeypatch):
    from app.services import account_profile
    from app.services.weekly_checkin import service
    user = record_user(make_user)
    monkeypatch.setattr(account_profile, "calculate_bmr", lambda *_: pytest.fail("adult math"))
    # PD1: the check-in re-derives through account_profile.derive_targets, so
    # the one patch above covers both authorities.
    assert not hasattr(service, "calculate_bmr")
    with pytest.raises(policy.EligibilityDenied):
        account_profile.complete_onboarding(user, object())
    with pytest.raises(policy.EligibilityDenied):
        service.apply_body_weight(user, 40, None)
    assert user.weight is None and UserSession.query.count() == 0


def test_mode_off_is_not_an_eligibility_approval(app):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = False
    with pytest.raises(policy.EligibilityDenied) as caught:
        policy.preview(declaration(18, "GB"))
    assert caught.value.status == 503


@pytest.fixture
def bearer(app, make_user, monkeypatch):
    from app.services import mobile_auth
    user = record_user(make_user)
    def authenticate(_):
        return SimpleNamespace(user=db.session.get(User, user.id), family=None, claims={})
    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)
    return app.test_client(), {"Authorization": "Bearer fixture-access"}, user


def test_preauth_contract_no_credentials_or_storage(app):
    client = app.test_client()
    for age in [12, 13, 17, 18]:
        response = client.post("/api/v1/auth/eligibility", json=declaration(age, "GB"))
        assert response.status_code == 200
        assert (response.json["eligibility"]["status"] == "screen_passed") is (age >= 18)
        assert response.headers["Cache-Control"] == "no-store"
        assert not response.headers.getlist("Set-Cookie")
    assert User.query.count() == 0


@pytest.mark.parametrize("path", ["/api/v1/account/eligibility", "/account/eligibility"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_existing_account_contract_authenticates_before_body_or_review(
        app, monkeypatch, path, method):
    monkeypatch.setattr(policy, "review_existing", lambda *_: pytest.fail("review before auth"))
    monkeypatch.setattr(app.request_class, "get_json",
                        lambda *_a, **_k: pytest.fail("body before auth"))
    response = app.test_client().open(path, method=method,
        data=b"untrusted", content_type="application/json")
    assert response.status_code == (401 if path.startswith("/api/") else 302)
    if path.startswith("/api/"):
        assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_native_existing_account_review_uses_authenticated_owner(app, bearer, make_user):
    client, headers, user = bearer
    other = make_user("untouched_owner")
    response = client.get("/api/v1/account/eligibility", headers=headers)
    assert response.status_code == 200
    assert response.json == {"eligibility": policy.projection(user)}
    assert response.headers["Cache-Control"] == "no-store"
    # A held (legacy) minor record is never replaced by an adult declaration.
    response = client.post("/api/v1/account/eligibility", headers=headers,
                           json={"eligibility_token": ticket(30)})
    assert response.status_code == 409
    assert response.json["error"]["code"] == "ELIGIBILITY_REVIEW_REQUIRED"
    assert policy.projection(user)["status"] == "review_required"
    assert policy.minority_signal(user) is True
    assert policy.projection(other)["status"] == "review_required"


def test_native_registration_and_web_cannot_bypass_canonical_ticket(app, monkeypatch):
    from app.blueprints import auth, mobile_registration
    from app.services import cognito_service
    monkeypatch.setattr(auth, "COGNITO_ENABLED", True)
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: pytest.fail("provider"))
    client = app.test_client()
    data = {"username": "fixture", "email": "fixture@example.invalid", "password": "Sifre123"}
    for route in ["/api/v1/auth/register", "/register"]:
        response = client.post(route, json=data)
        assert response.status_code == 422
    assert User.query.count() == 0


@pytest.mark.parametrize("path", ["/api/v1/pump-checks", "/api/v1/pump-check-comparisons",
    "/api/v1/account/profile", "/api/v1/progress/check-ins"])
def test_native_guard_precedes_body_parsing_and_sinks(app, bearer, monkeypatch, path):
    client, headers, _ = bearer
    # The exact existing paths are asserted separately below; no fake route
    # or client-supplied owner/capability is involved.
    original = app.request_class.get_json
    def no_parse(self, *args, **kwargs):
        if self.path == path:
            pytest.fail("protected body parsed")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(app.request_class, "get_json", no_parse)
    method = "PUT" if path.endswith("profile") else "POST"
    response = client.open(path, method=method, data=b"private fixture", headers=headers,
                           content_type="multipart/form-data; boundary=fixture")
    assert response.status_code == 403, (path, response.status_code)
    assert response.json["error"]["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"


def test_workout_transport_guard_does_not_consume_multipart(app, bearer):
    _, _, user = bearer
    class Unreadable(BytesIO):
        def read(self, *_): raise AssertionError("multipart consumed")
        def readinto(self, *_): raise AssertionError("multipart consumed")
    with app.test_request_context("/", method="POST", content_type="multipart/form-data",
                                  environ_overrides={"wsgi.input": Unreadable(b"fixture"),
                                                     "CONTENT_LENGTH": "7"}):
        request.url_rule = SimpleNamespace(endpoint="mobile_api.complete_workout_session")
        with pytest.raises(policy.EligibilityDenied):
            policy.enforce_request(user)
    with app.test_request_context("/", method="POST", json={}):
        request.url_rule = SimpleNamespace(endpoint="mobile_api.complete_workout_session")
        policy.enforce_request(user)  # Existing photo-free command remains possible.


def test_web_social_is_closed_for_adult_senders_too(app, client, auth_user):
    policy.attach(auth_user, policy.registration_record(ticket(30)))
    db.session.commit()
    # Launch V1: the social boundary (app/services/launch_social.py) answers
    # before the eligibility check, so the refusal is its 404, not a 403.
    for route in ["/friends", "/friends/search", "/feed/data", "/chat/fixture/messages"]:
        response = client.get(route)
        assert response.status_code == 404


def test_web_registration_contact_controls_stay_in_inert_template(app, client):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(client.get("/register").data, "html.parser")
    assert soup.select_one("#eligibility-birth-date") and soup.select_one("#eligibility-country")
    assert not soup.select_one("#eligibility-age")
    # A template's contents are its own fragment, not document descendants
    # (soupsieve >= 4.0 follows the HTML spec), so select from the template.
    template = soup.select_one("template#eligible-contact-template")
    assert template and template.select_one("#email")
    assert not soup.select_one("#eligible-contact-stage input")


@pytest.mark.parametrize("path", ["/api/v1/pump-checks",
    "/api/v1/pump-checks/fixture", "/api/v1/pump-check-comparisons/fixture"])
def test_teen_body_analysis_reads_are_not_a_creation_guard_bypass(app, bearer, path):
    client, headers, _ = bearer
    response = client.get(path, headers=headers)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"


def test_legacy_physique_projection_is_unavailable_to_unknown_cohort(app, client, auth_user):
    response = client.get("/api/progress/physique")
    assert response.status_code == 403
    assert response.json["code"] == "ELIGIBILITY_FEATURE_UNAVAILABLE"


@pytest.mark.parametrize("age", [17, 30])
def test_legacy_coach_adult_math_keeps_age_guard_after_ai_activation(
        app, make_user, age):
    user = record_user(make_user, age)
    with app.test_request_context("/chat", method="POST", json={}):
        request.url_rule = SimpleNamespace(endpoint="coach.chat")
        if age < 18:
            with pytest.raises(policy.EligibilityDenied):
                policy.enforce_request(user)
        else:
            policy.enforce_request(user)  # Not an AI grant; D1 still applies.


@pytest.mark.parametrize("age", [None, 17, 30])
def test_restricted_and_signed_cohorts_keep_real_account_erasure(
        app, make_user, monkeypatch, age):
    from account_deletion_support import install_fakes, issue_bearer
    cognito, _ = install_fakes(monkeypatch)
    user = make_user("eligibility_erasure")
    if age is not None:
        attach_record(user, age)
    user_id = user.id
    headers = issue_bearer(cognito, user)
    response = app.test_client().delete("/api/v1/account", headers=headers)
    assert response.status_code == 204
    assert db.session.get(User, user_id) is None
    assert user.cognito_sub in cognito.deleted_subs
