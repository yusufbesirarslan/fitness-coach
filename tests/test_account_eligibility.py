"""Age declaration != health/AI consent; exercise real authorities and sinks."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from flask import request
from itsdangerous import TimestampSigner

from app.extensions import db
from app.models import User, UserSession
from app.services import account_eligibility as policy, account_registration


@pytest.fixture(autouse=True)
def enable(app):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True


def ticket(age=17, country="GB"):
    return policy.preview({"age": age, "country": country})["eligibility"]["registration_token"]


def record_user(make_user, age=17, country="GB"):
    user = make_user(f"age{age}{country}")
    policy.attach(user, policy.registration_record(ticket(age, country)))
    db.session.commit()
    return user


@pytest.mark.parametrize("age,country,status", [
    (12, "US", "ineligible"), (13, "US", "screen_passed"),
    (17, "US", "screen_passed"), (18, "US", "screen_passed"),
    (13, "GB", "screen_passed"), (17, "GB", "screen_passed"),
    (13, "CA", "qualification_required"), (14, "CA", "screen_passed"),
    (13, "AU", "qualification_required"), (14, "AU", "qualification_required"),
    (15, "AU", "screen_passed"), (13, "TR", "qualification_required"),
    (17, "TR", "qualification_required"), (18, "TR", "screen_passed"),
    (18, "DE", "ineligible"), (150, "GB", "screen_passed"),
])
def test_five_markets_and_real_pending_cohorts(age, country, status):
    result = policy.preview({"age": age, "country": country})["eligibility"]
    assert result["status"] == status
    assert bool(result["registration_token"]) == (status == "screen_passed")
    assert result["capabilities"]["social"] is False
    assert result["capabilities"]["body_photos"] == (status == "screen_passed" and age >= 18)
    assert result["capabilities"]["automated_weight_targets"] == (status == "screen_passed" and age >= 18)


@pytest.mark.parametrize("data", [None, {}, [], {"age": True, "country": "GB"},
    {"age": "18", "country": "GB"}, {"age": 18.0, "country": "GB"},
    {"age": 0, "country": "GB"}, {"age": 18, "country": []},
    {"age": 18, "country": "GB", "approved": True}])
def test_bad_types_and_client_authority_are_not_declarations(data):
    with pytest.raises(policy.EligibilityDenied) as caught:
        policy.preview(data)
    assert caught.value.code == "AUTH_ELIGIBILITY_INVALID_REQUEST"


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
    for value in [expired, policy._signer(account=True).dumps({"age": 18, "country": "GB"})]:
        with pytest.raises(policy.EligibilityDenied):
            policy.registration_record(value)


def test_registration_persists_bound_cohort_not_a_consent(app, monkeypatch):
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "sign_up", lambda **_: "sub-declared")
    account_registration.register_account("declared", "fixture@example.invalid", "Sifre123",
                                         eligibility_token=ticket())
    user = User.query.one()
    assert user.age == 17 and user.profile_complete is False
    assert policy.projection(user) == {"status": "screen_passed", "age_band": "13_17",
        "country": "GB", "source": "self_declared", "capabilities": {
            "body_photos": False, "automated_weight_targets": False, "social": False}}
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
    assert policy.projection(teen)["age_band"] == "13_17"


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
    monkeypatch.setattr(service, "calculate_bmr", lambda *_: pytest.fail("adult math"))
    with pytest.raises(policy.EligibilityDenied):
        account_profile.complete_onboarding(user, object())
    with pytest.raises(policy.EligibilityDenied):
        service.apply_body_weight(user, 40, None)
    assert user.weight is None and UserSession.query.count() == 0


def test_mode_off_is_not_an_eligibility_approval(app):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = False
    with pytest.raises(policy.EligibilityDenied) as caught:
        policy.preview({"age": 18, "country": "GB"})
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
        response = client.post("/api/v1/auth/eligibility", json={"age": age, "country": "GB"})
        assert response.status_code == 200
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
    response = client.post("/api/v1/account/eligibility", headers=headers,
                           json={"eligibility_token": ticket()})
    assert response.status_code == 200
    assert response.json == {"eligibility": policy.projection(user)}
    response = client.post("/api/v1/account/eligibility", headers=headers,
                           json={"eligibility_token": ticket(30)})
    assert response.status_code == 409
    assert response.json["error"]["code"] == "ELIGIBILITY_REVIEW_REQUIRED"
    assert policy.projection(user)["age_band"] == "13_17"
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
    for route in ["/friends", "/friends/search", "/feed/data", "/chat/fixture/messages"]:
        response = client.get(route)
        assert response.status_code == 403


def test_web_registration_contact_controls_stay_in_inert_template(app, client):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(client.get("/register").data, "html.parser")
    assert soup.select_one("#eligibility-age") and soup.select_one("#eligibility-country")
    assert soup.select_one("template#eligible-contact-template #email")
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
        policy.attach(user, policy.registration_record(ticket(age)))
        db.session.commit()
    user_id = user.id
    headers = issue_bearer(cognito, user)
    response = app.test_client().delete("/api/v1/account", headers=headers)
    assert response.status_code == 204
    assert db.session.get(User, user_id) is None
    assert user.cognito_sub in cognito.deleted_subs
