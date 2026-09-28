"""Native registration / verification HTTP contract (LP-01).

    POST /api/v1/auth/register
    POST /api/v1/auth/verify
    POST /api/v1/auth/verify/resend

Covers the wire contract (status, exact bodies, ADR 0001 envelope), request
parsing, provider-failure mapping, enumeration resistance, the session
boundary (no credential before or after verification), pre-auth throttling,
blocking-capacity admission and the sensitive-logging boundary.

    python -m pytest tests/test_mobile_registration_api.py -v
"""
import json
import logging
import threading

import pytest

from app.blueprints import mobile_registration
from app.extensions import db, limiter
from app.models import (
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential, User,
)
from app.services import ai_gate, cognito_service, mobile_auth
from app.services.cognito_service import CognitoServiceError, _ERROR_MESSAGES


ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
PASSWORD = "Sifre123"
CODE = "482913"


def _provider_error(code):
    return CognitoServiceError(
        _ERROR_MESSAGES.get(code, "İşlem başarısız. Lütfen tekrar dene."), code)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    calls = {"sign_up": [], "confirm": [], "resend": []}
    failures = {}

    def sign_up(username, password, email, name):
        calls["sign_up"].append(
            {"username": username, "email": email, "name": name})
        if "sign_up" in failures:
            raise failures["sign_up"]
        return f"sub-{username}"

    def confirm_sign_up(username, code):
        calls["confirm"].append({"username": username, "code": code})
        if "confirm" in failures:
            raise failures["confirm"]

    def resend_code(username):
        calls["resend"].append({"username": username})
        if "resend" in failures:
            raise failures["resend"]

    monkeypatch.setattr(cognito_service, "sign_up", sign_up)
    monkeypatch.setattr(cognito_service, "confirm_sign_up", confirm_sign_up)
    monkeypatch.setattr(cognito_service, "resend_code", resend_code)
    return {"calls": calls, "failures": failures}


@pytest.fixture
def throttled(app):
    """Real limiting for THIS app instance, conditional deductions included.

    `Limiter.init_app` returns early while the limiter is disabled, and the
    shared `app` fixture disables it after the first app of the session — so
    every later test app lacks the limiter's before/after-request hooks. The
    decorator-level limits still run (they live in the view wrapper), but a
    `deduct_when` limit is only charged from the after-request hook and would
    silently never trip. Re-binding while enabled attaches the hooks to this
    app, so the per-username verification budget is exercised for real.
    """
    limiter.enabled = True
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)
    limiter.reset()
    yield
    limiter.enabled = False
    limiter.reset()


def _register(client, **overrides):
    body = {"username": "nativeuser", "email": "nativeuser@example.com",
            "password": PASSWORD}
    body.update(overrides)
    return client.post("/api/v1/auth/register", json=body)


def _verify(client, username="nativeuser", code=CODE, **kwargs):
    return client.post("/api/v1/auth/verify",
                       json={"username": username, "code": code}, **kwargs)


def _resend(client, username="nativeuser", **kwargs):
    return client.post("/api/v1/auth/verify/resend",
                       json={"username": username}, **kwargs)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


def _comparable(response):
    """Status + body with the per-request id masked, for oracle comparisons."""
    body = response.get_json()
    if "error" in body:
        body["error"]["request_id"] = "<request-id>"
    return response.status_code, json.dumps(body, sort_keys=True)


def _assert_no_session_material(response):
    assert "Set-Cookie" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    text = response.get_data(as_text=True)
    for forbidden in ("session", "credential", "token", "sub-"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# Register — contract
# ---------------------------------------------------------------------------

def test_register_success_exact_contract(raw_client, provider):
    response = _register(raw_client)
    assert response.status_code == 201
    assert response.get_json() == {"registration": {
        "status": "verification_required", "username": "nativeuser"}}
    assert response.content_type == "application/json"
    _assert_no_session_material(response)
    assert provider["calls"]["sign_up"] == [{
        "username": "nativeuser", "email": "nativeuser@example.com",
        "name": "nativeuser"}]
    user = User.query.filter_by(username="nativeuser").one()
    assert user.cognito_sub == "sub-nativeuser"
    assert user.password_hash is None
    assert user.language == "tr"


def test_register_uses_the_same_normalization_as_web(raw_client, provider):
    response = _register(raw_client, email="  Native.User@EXAMPLE.com ",
                         language="en")
    assert response.status_code == 201
    user = User.query.filter_by(username="nativeuser").one()
    assert user.email == "native.user@example.com"
    assert user.language == "en"
    assert provider["calls"]["sign_up"][0]["email"] == "native.user@example.com"


def test_register_ignores_browser_only_referral_inputs(raw_client, provider):
    raw_client.set_cookie("fitx_ref", "COOKIECODE")
    response = _register(raw_client, ref="BODYCODE")
    assert response.status_code == 201
    assert "Set-Cookie" not in response.headers
    user = User.query.filter_by(username="nativeuser").one()
    assert not (user.user_metadata or {}).get("pending_referral_code")


@pytest.mark.parametrize("request_kwargs", [
    {"data": "not json", "content_type": "application/json"},
    {"data": "username=x", "content_type": "application/x-www-form-urlencoded"},
    {"json": ["nativeuser"]},
    {"json": "nativeuser"},
])
def test_register_malformed_body_is_invalid_request(
        raw_client, provider, request_kwargs):
    response = raw_client.post("/api/v1/auth/register", **request_kwargs)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert provider["calls"]["sign_up"] == []


@pytest.mark.parametrize("overrides", [
    {"username": None}, {"email": None}, {"password": None},
    {"username": 42}, {"email": ["a@b.co"]}, {"password": {"p": 1}},
    {"password": True}, {"username": ""}, {"email": "   "}, {"password": ""},
    {"language": "xx"}, {"language": 1},
])
def test_register_invalid_field_types_or_missing_values(
        raw_client, provider, overrides):
    response = _register(raw_client, **overrides)
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "AUTH_INVALID_REQUEST"
    assert error["retryable"] is False
    assert provider["calls"]["sign_up"] == []


def test_register_missing_keys_is_invalid_request(raw_client, provider):
    response = raw_client.post("/api/v1/auth/register",
                               json={"username": "nativeuser"})
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"


@pytest.mark.parametrize(("overrides", "code"), [
    ({"password": "kisa1"}, "AUTH_PASSWORD_POLICY"),
    ({"password": "12345678"}, "AUTH_PASSWORD_POLICY"),
    ({"password": "a1" * 65}, "AUTH_PASSWORD_POLICY"),
    ({"username": "ab"}, "AUTH_USERNAME_INVALID"),
    ({"username": "bad name"}, "AUTH_USERNAME_INVALID"),
    ({"email": "not-an-email"}, "AUTH_EMAIL_INVALID"),
])
def test_register_validation_codes(raw_client, provider, overrides, code):
    response = _register(raw_client, **overrides)
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == code
    assert error["retryable"] is False
    assert provider["calls"]["sign_up"] == []


@pytest.mark.parametrize(("provider_code", "status", "code", "retryable"), [
    ("UsernameExistsException", 409, "AUTH_IDENTITY_UNAVAILABLE", False),
    ("AliasExistsException", 409, "AUTH_IDENTITY_UNAVAILABLE", False),
    ("InvalidPasswordException", 400, "AUTH_PASSWORD_POLICY", False),
    ("InvalidParameterException", 400, "AUTH_INVALID_REQUEST", False),
    ("LimitExceededException", 429, "AUTH_RATE_LIMITED", True),
    ("TooManyRequestsException", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
    ("InternalErrorException", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
    ("CodeDeliveryFailureException", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
    ("", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
])
def test_register_provider_failures_are_typed_and_sanitized(
        raw_client, provider, provider_code, status, code, retryable):
    provider["failures"]["sign_up"] = _provider_error(provider_code)
    response = _register(raw_client)
    assert response.status_code == status
    error = _error(response)
    assert error["code"] == code
    assert error["retryable"] is retryable
    text = response.get_data(as_text=True)
    assert provider_code not in text if provider_code else True
    for sentence in set(_ERROR_MESSAGES.values()):
        assert sentence not in text
    assert User.query.filter_by(username="nativeuser").first() is None


def test_register_local_commit_race_is_identity_unavailable(
        raw_client, provider, monkeypatch):
    def racing(username, password, email, name):
        db.session.add(User(username="racer", email=email, password_hash="x"))
        db.session.commit()
        return f"sub-{username}"

    monkeypatch.setattr(cognito_service, "sign_up", racing)
    response = _register(raw_client)
    assert response.status_code == 409
    assert _error(response)["code"] == "AUTH_IDENTITY_UNAVAILABLE"


def test_register_unexpected_provider_exception_is_normalized(
        raw_client, provider, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("raw internal detail 10.0.0.1")

    monkeypatch.setattr(cognito_service, "sign_up", explode)
    response = _register(raw_client)
    assert response.status_code == 503
    assert _error(response)["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert "raw internal detail" not in response.get_data(as_text=True)


def test_register_is_unavailable_when_provider_not_configured(
        raw_client, provider, monkeypatch):
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", False)
    response = _register(raw_client)
    assert response.status_code == 503
    assert _error(response)["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert provider["calls"]["sign_up"] == []


def test_register_repeat_submission_is_deterministic_and_non_corrupting(
        raw_client, provider):
    first = _register(raw_client)
    second = _register(raw_client)
    assert first.status_code == 201
    assert second.status_code == 409
    assert _error(second)["code"] == "AUTH_IDENTITY_UNAVAILABLE"
    assert User.query.filter_by(username="nativeuser").count() == 1
    assert len(provider["calls"]["sign_up"]) == 1


# ---------------------------------------------------------------------------
# Verify — contract
# ---------------------------------------------------------------------------

def test_verify_success_exact_contract(raw_client, provider):
    response = _verify(raw_client, username=" nativeuser ", code=" 482913 ")
    assert response.status_code == 200
    assert response.get_json() == {"verification": {"status": "verified"}}
    _assert_no_session_material(response)
    assert provider["calls"]["confirm"] == [
        {"username": "nativeuser", "code": CODE}]


@pytest.mark.parametrize("body", [
    {}, {"username": "nativeuser"}, {"code": CODE},
    {"username": None, "code": CODE}, {"username": 7, "code": CODE},
    {"username": "nativeuser", "code": 482913},
    {"username": "nativeuser", "code": ["482913"]},
    {"username": "  ", "code": CODE}, {"username": "nativeuser", "code": " "},
    {"username": "u" * 129, "code": CODE},
    {"username": "nativeuser", "code": "1" * 65},
])
def test_verify_malformed_or_missing_fields(raw_client, provider, body):
    response = raw_client.post("/api/v1/auth/verify", json=body)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert provider["calls"]["confirm"] == []


def test_verify_non_json_is_invalid_request(raw_client, provider):
    response = raw_client.post("/api/v1/auth/verify", data="x",
                               content_type="text/plain")
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"


@pytest.mark.parametrize(("provider_code", "status", "code"), [
    ("CodeMismatchException", 400, "AUTH_VERIFICATION_CODE_INVALID"),
    ("ExpiredCodeException", 400, "AUTH_VERIFICATION_CODE_INVALID"),
    ("UserNotFoundException", 400, "AUTH_VERIFICATION_CODE_INVALID"),
    ("NotAuthorizedException", 400, "AUTH_VERIFICATION_CODE_INVALID"),
    ("InvalidParameterException", 400, "AUTH_INVALID_REQUEST"),
    ("LimitExceededException", 429, "AUTH_RATE_LIMITED"),
    ("TooManyFailedAttemptsException", 429, "AUTH_RATE_LIMITED"),
    ("TooManyRequestsException", 503, "AUTH_TEMPORARILY_UNAVAILABLE"),
    ("InternalErrorException", 503, "AUTH_TEMPORARILY_UNAVAILABLE"),
    ("AliasExistsException", 503, "AUTH_TEMPORARILY_UNAVAILABLE"),
    ("", 503, "AUTH_TEMPORARILY_UNAVAILABLE"),
])
def test_verify_provider_failures_are_typed_and_sanitized(
        raw_client, provider, provider_code, status, code):
    provider["failures"]["confirm"] = _provider_error(provider_code)
    response = _verify(raw_client)
    assert response.status_code == status
    assert _error(response)["code"] == code
    text = response.get_data(as_text=True)
    for sentence in set(_ERROR_MESSAGES.values()):
        assert sentence not in text


def test_verify_consumes_a_web_registered_pending_referral(
        raw_client, provider, make_user):
    """One authority: a referral recorded by the web registration is honoured
    whichever transport performs the verification."""
    from app.services.referral import REFERRAL_REWARD_XP, ensure_referral_code

    referrer = make_user("nativeref", rank_points=0)
    ensure_referral_code(referrer)
    invited = User(username="nativeuser", email="nativeuser@example.com",
                   cognito_sub="sub-nativeuser",
                   user_metadata={"pending_referral_code":
                                  referrer.referral_code})
    ensure_referral_code(invited)
    db.session.add(invited)
    db.session.commit()

    response = _verify(raw_client)
    assert response.status_code == 200
    assert "referred" not in response.get_json()["verification"]
    db.session.expire_all()
    invited = User.query.filter_by(username="nativeuser").one()
    assert invited.rank_points == REFERRAL_REWARD_XP
    assert "pending_referral_code" not in (invited.user_metadata or {})


def test_verify_sends_the_same_welcome_email_as_web(
        raw_client, provider, monkeypatch):
    from app.services import email_service

    sent = []
    monkeypatch.setattr(email_service, "send_html_email",
                        lambda to, subject, html, **kw: sent.append(to))
    assert _register(raw_client).status_code == 201
    assert _verify(raw_client).status_code == 200
    assert sent == ["nativeuser@example.com"]


# ---------------------------------------------------------------------------
# Resend — contract
# ---------------------------------------------------------------------------

def test_resend_success_exact_contract(raw_client, provider):
    response = _resend(raw_client, username=" nativeuser ")
    assert response.status_code == 202
    assert response.get_json() == {"verification_resend": {"status": "accepted"}}
    _assert_no_session_material(response)
    assert provider["calls"]["resend"] == [{"username": "nativeuser"}]


@pytest.mark.parametrize("body", [
    {}, {"username": None}, {"username": 3}, {"username": ""},
    {"username": "   "}, {"username": "u" * 129},
])
def test_resend_malformed_or_missing_username(raw_client, provider, body):
    response = raw_client.post("/api/v1/auth/verify/resend", json=body)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert provider["calls"]["resend"] == []


@pytest.mark.parametrize("provider_code", [
    "TooManyRequestsException", "InternalErrorException",
    "CodeDeliveryFailureException", ""])
def test_resend_genuine_unavailability_is_retryable_503(
        raw_client, provider, provider_code):
    provider["failures"]["resend"] = _provider_error(provider_code)
    response = _resend(raw_client)
    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert error["retryable"] is True


def test_resend_repeat_requests_are_deterministic(raw_client, provider):
    answers = {_comparable(_resend(raw_client)) for _ in range(3)}
    assert len(answers) == 1


# ---------------------------------------------------------------------------
# Enumeration resistance
# ---------------------------------------------------------------------------

def test_register_taken_username_and_taken_email_are_indistinguishable(
        raw_client, provider, make_user, monkeypatch):
    make_user("existing", email="existing@example.com")
    by_username = _register(raw_client, username="existing")
    by_email = _register(raw_client, email="EXISTING@example.com")
    # A local collision is answered before any provider work: no Cognito
    # account (and so no orphan) is created for an identity already taken.
    assert provider["calls"]["sign_up"] == []

    provider["failures"]["sign_up"] = _provider_error("UsernameExistsException")
    by_provider = _register(raw_client, username="orphanuser",
                            email="orphan@example.com")
    del provider["failures"]["sign_up"]

    def racing(username, password, email, name):
        db.session.add(User(username="racer", email=email, password_hash="x"))
        db.session.commit()
        return f"sub-{username}"

    monkeypatch.setattr(cognito_service, "sign_up", racing)
    by_race = _register(raw_client, username="raceduser",
                        email="raced@example.com")

    answers = {_comparable(r) for r in (by_username, by_email, by_provider,
                                        by_race)}
    assert len(answers) == 1
    status, body = answers.pop()
    assert status == 409
    assert json.loads(body)["error"]["code"] == "AUTH_IDENTITY_UNAVAILABLE"


def test_verify_wrong_expired_unknown_and_confirmed_are_indistinguishable(
        raw_client, provider):
    answers = set()
    for provider_code in ("CodeMismatchException", "ExpiredCodeException",
                          "UserNotFoundException", "NotAuthorizedException"):
        provider["failures"]["confirm"] = _provider_error(provider_code)
        answers.add(_comparable(_verify(raw_client, username="someone")))
    assert len(answers) == 1
    assert answers.pop()[0] == 400


def test_resend_pending_unknown_confirmed_and_account_throttled_are_identical(
        raw_client, provider):
    answers = {_comparable(_resend(raw_client, username="pending"))}
    for provider_code in ("UserNotFoundException", "InvalidParameterException",
                          "NotAuthorizedException", "LimitExceededException"):
        provider["failures"]["resend"] = _provider_error(provider_code)
        answers.add(_comparable(_resend(raw_client, username="someone")))
    assert answers == {(202, json.dumps(
        {"verification_resend": {"status": "accepted"}}, sort_keys=True))}


# ---------------------------------------------------------------------------
# Session boundary
# ---------------------------------------------------------------------------

def _session_rows():
    return (MobileAuthSession.query.count(),
            MobileAccessCredential.query.count(),
            MobileRefreshCredential.query.count())


def test_register_and_verify_never_issue_a_session(
        raw_client, provider, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no provider sign-in may run during LP-01 flows")

    monkeypatch.setattr(cognito_service, "authenticate", forbidden)
    monkeypatch.setattr(mobile_auth, "login", forbidden)

    for response in (_register(raw_client), _resend(raw_client),
                     _verify(raw_client)):
        assert response.status_code in (200, 201, 202)
        _assert_no_session_material(response)
    assert _session_rows() == (0, 0, 0)

    # Verified is not signed in: the protected boundary still refuses.
    me = raw_client.get("/api/v1/account/me")
    assert me.status_code == 401
    assert _error(me)["code"] == "AUTH_SESSION_EXPIRED"


def test_verified_account_signs_in_only_through_the_existing_login(
        raw_client, provider, monkeypatch):
    calls = []

    def login(username, password):
        calls.append(username)
        raise mobile_auth.MobileAuthFailure(
            "AUTH_INVALID_CREDENTIALS", 401, False, "invalid_credentials")

    monkeypatch.setattr(mobile_auth, "login", login)
    _register(raw_client)
    _verify(raw_client)
    assert calls == []
    raw_client.post("/api/v1/auth/login",
                    json={"username": "nativeuser", "password": PASSWORD})
    assert calls == ["nativeuser"]


def test_preauth_routes_ignore_a_presented_bearer(raw_client, provider,
                                                  monkeypatch, throttled):
    """A bearer header on a pre-auth route is not resolved into a principal
    (the default limiter stays IP-keyed and no credential lookup runs)."""
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: (_ for _ in ()).throw(AssertionError("must not resolve")))
    headers = {"Authorization": "Bearer " + "A" * 43}
    assert _register(raw_client, headers=headers).status_code == 201
    assert _verify(raw_client, headers=headers).status_code == 200
    assert _resend(raw_client, headers=headers).status_code == 202


# ---------------------------------------------------------------------------
# Pre-auth rate limiting
# ---------------------------------------------------------------------------

def _assert_rate_limited(response):
    assert response.status_code == 429
    error = _error(response)
    assert error["code"] == "AUTH_RATE_LIMITED"
    assert error["retryable"] is True
    assert int(response.headers["Retry-After"]) > 0


def test_register_per_ip_budget(raw_client, provider, throttled):
    ip = {"REMOTE_ADDR": "198.51.100.10"}
    for i in range(5):
        response = raw_client.post(
            "/api/v1/auth/register", json={"username": f"x{i}"},
            environ_base=ip)
        assert response.status_code == 400
    _assert_rate_limited(raw_client.post(
        "/api/v1/auth/register", json={"username": "x"}, environ_base=ip))
    other = raw_client.post("/api/v1/auth/register", json={"username": "x"},
                            environ_base={"REMOTE_ADDR": "198.51.100.11"})
    assert other.status_code == 400


def test_verify_brute_force_is_bounded_per_username_across_ips(
        raw_client, provider, throttled):
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    for i in range(10):
        response = _verify(raw_client, code=f"{i:06d}", environ_base={
            "REMOTE_ADDR": f"203.0.113.{i + 1}"})
        assert response.status_code == 400
    _assert_rate_limited(_verify(raw_client, code="999999", environ_base={
        "REMOTE_ADDR": "203.0.113.200"}))
    assert len(provider["calls"]["confirm"]) == 10
    # A different account is not collateral damage.
    assert _verify(raw_client, username="otheruser", environ_base={
        "REMOTE_ADDR": "203.0.113.201"}).status_code == 400


def test_verify_successes_do_not_consume_the_username_budget(
        raw_client, provider, throttled):
    for i in range(12):
        assert _verify(raw_client, environ_base={
            "REMOTE_ADDR": f"203.0.113.{i + 1}"}).status_code == 200


def test_verify_per_ip_budget(raw_client, provider, throttled):
    ip = {"REMOTE_ADDR": "203.0.113.50"}
    for i in range(10):
        assert _verify(raw_client, username=f"user{i}",
                       environ_base=ip).status_code == 200
    _assert_rate_limited(_verify(raw_client, username="user99",
                                 environ_base=ip))


def test_resend_per_ip_and_per_username_budgets(raw_client, provider,
                                                throttled):
    ip = {"REMOTE_ADDR": "192.0.2.10"}
    for i in range(3):
        assert _resend(raw_client, username=f"user{i}",
                       environ_base=ip).status_code == 202
    _assert_rate_limited(_resend(raw_client, username="user9", environ_base=ip))

    for i in range(3):
        assert _resend(raw_client, username="victim", environ_base={
            "REMOTE_ADDR": f"192.0.2.{100 + i}"}).status_code == 202
    _assert_rate_limited(_resend(raw_client, username="VICTIM", environ_base={
        "REMOTE_ADDR": "192.0.2.150"}))
    assert [c["username"] for c in provider["calls"]["resend"]].count(
        "victim") == 3


def test_verify_fails_closed_when_distributed_throttle_is_unavailable(
        raw_client, provider, monkeypatch):
    monkeypatch.setattr(mobile_registration, "login_throttle_available",
                        lambda: False)
    response = _verify(raw_client)
    assert response.status_code == 503
    assert _error(response)["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert provider["calls"]["confirm"] == []


# ---------------------------------------------------------------------------
# Blocking concurrency capacity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [_register, _verify, _resend])
def test_saturated_capacity_rejects_without_a_provider_call(
        raw_client, provider, monkeypatch, call):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    assert semaphore.acquire(blocking=False)
    try:
        response = call(raw_client)
    finally:
        semaphore.release()
    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert error["retryable"] is True
    assert response.headers["Retry-After"] == "15"
    assert provider["calls"] == {"sign_up": [], "confirm": [], "resend": []}
    assert User.query.count() == 0


@pytest.mark.parametrize(("call", "operation", "failure"), [
    (_register, "sign_up", None),
    (_register, "sign_up", "InternalErrorException"),
    (_verify, "confirm", None),
    (_verify, "confirm", "CodeMismatchException"),
    (_resend, "resend", None),
    (_resend, "resend", "UserNotFoundException"),
])
def test_provider_call_holds_exactly_one_slot_and_releases_it(
        raw_client, provider, monkeypatch, call, operation, failure):
    semaphore = threading.BoundedSemaphore(2)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    observed = []
    original = {"sign_up": cognito_service.sign_up,
                "confirm": cognito_service.confirm_sign_up,
                "resend": cognito_service.resend_code}[operation]

    def observing(**kwargs):
        observed.append(semaphore._value)
        return original(**kwargs)

    attribute = {"sign_up": "sign_up", "confirm": "confirm_sign_up",
                 "resend": "resend_code"}[operation]
    monkeypatch.setattr(cognito_service, attribute, observing)
    if failure:
        provider["failures"][operation] = _provider_error(failure)
    call(raw_client)
    assert observed == [1]
    assert semaphore._value == 2


# ---------------------------------------------------------------------------
# Sensitive logging
# ---------------------------------------------------------------------------

def test_no_password_code_or_email_reaches_the_logs(
        raw_client, provider, caplog):
    caplog.set_level(logging.DEBUG)
    secret_password = "Zq7SecretPassw0rd"
    secret_code = "731905"
    email = "private.person@example.com"
    _register(raw_client, password=secret_password, email=email)
    _verify(raw_client, code=secret_code)
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    _verify(raw_client, code=secret_code)
    _resend(raw_client)
    text = caplog.text
    assert secret_password not in text
    assert secret_code not in text
    assert email not in text
    assert "account_registration event=register outcome=pending_verification" in text
    assert "account_registration event=confirm outcome=code_invalid" in text
