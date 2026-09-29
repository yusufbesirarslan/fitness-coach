"""Native password-recovery HTTP contract (LP-02).

    POST /api/v1/auth/password/forgot
    POST /api/v1/auth/password/reset

Covers the wire contract (status, exact bodies, ADR 0001 envelope), request
parsing, provider-failure mapping and sanitization, enumeration resistance,
the session boundary (no credential issued; existing sessions revoked exactly
as the web reset revokes them), pre-auth throttling, blocking-capacity
admission and the sensitive-logging boundary.

    python -m pytest tests/test_mobile_password_recovery_api.py -v
"""
import calendar
import json
import logging
import threading
from datetime import datetime, timedelta

import pytest

from app.blueprints import mobile_password_recovery
from app.extensions import db, limiter
from app.models import (
    CognitoSession, MobileAccessCredential, MobileAuthSession,
    MobileRefreshCredential, User,
)
from app.services import (
    account_recovery, ai_gate, cognito_jwt, cognito_service, email_service,
    mobile_auth, session_store,
)
from app.services.cognito_service import CognitoServiceError, _ERROR_MESSAGES


ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
PASSWORD = "Newpass123"
CODE = "482913"
# Headers that legitimately differ per request and carry no account state.
PER_REQUEST_HEADERS = {"X-Request-Id", "X-Request-ID", "Date",
                       "Content-Security-Policy"}


def _provider_error(code):
    return CognitoServiceError(
        _ERROR_MESSAGES.get(code, "İşlem başarısız. Lütfen tekrar dene."), code)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(mobile_password_recovery, "COGNITO_ENABLED", True)
    calls = {"forgot": [], "confirm": [], "revoke": []}
    failures = {}

    def forgot_password(username):
        calls["forgot"].append(username)
        if "forgot" in failures:
            raise failures["forgot"]

    def confirm_forgot_password(username, code, new_password):
        calls["confirm"].append(
            {"username": username, "code": code, "password": new_password})
        if "confirm" in failures:
            raise failures["confirm"]

    monkeypatch.setattr(cognito_service, "forgot_password", forgot_password)
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", confirm_forgot_password)
    monkeypatch.setattr(cognito_service, "revoke_token", calls["revoke"].append)
    return {"calls": calls, "failures": failures}


@pytest.fixture
def throttled(app):
    """Real limiting for THIS app instance, conditional deductions included.

    See tests/test_mobile_registration_api.py::throttled — `Limiter.init_app`
    no-ops while disabled, so a `deduct_when` limit never charges unless the
    limiter is re-bound to this app while enabled.
    """
    limiter.enabled = True
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)
    limiter.reset()
    yield
    limiter.enabled = False
    limiter.reset()


def _forgot(client, identifier="alice", **kwargs):
    return client.post("/api/v1/auth/password/forgot",
                       json={"identifier": identifier}, **kwargs)


def _reset(client, identifier="alice", code=CODE, new_password=PASSWORD,
           **kwargs):
    return client.post("/api/v1/auth/password/reset", json={
        "identifier": identifier, "code": code, "new_password": new_password},
        **kwargs)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


def _wire(response):
    """Everything an observer sees, minus per-request noise."""
    body = response.get_json()
    if "error" in body:
        body["error"]["request_id"] = "<request-id>"
    headers = tuple(sorted(
        (key, value) for key, value in response.headers.items()
        if key not in PER_REQUEST_HEADERS))
    return response.status_code, json.dumps(body, sort_keys=True), headers


def _decoded(response):
    """The body as a reader sees it. `get_data()` alone would miss non-ASCII
    provider sentences, which JSON escapes (`İ` → `\\u0130`)."""
    return json.dumps(response.get_json(), ensure_ascii=False)


def _assert_no_session_material(response):
    assert "Set-Cookie" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    text = response.get_data(as_text=True).lower()
    for forbidden in ("session", "credential", "token", "sub-", "cookie"):
        assert forbidden not in text


def _session_rows():
    return (MobileAuthSession.query.count(),
            MobileAccessCredential.query.count(),
            MobileRefreshCredential.query.count(),
            CognitoSession.query.count())


# ---------------------------------------------------------------------------
# Forgot — contract
# ---------------------------------------------------------------------------

def test_forgot_success_exact_contract(raw_client, provider, make_user):
    make_user("alice", email="alice@example.com")
    response = _forgot(raw_client, " Alice@Example.com ")
    assert response.status_code == 202
    assert response.get_json() == {"password_reset": {"status": "accepted"}}
    assert provider["calls"]["forgot"] == ["alice"]
    _assert_no_session_material(response)


def test_forgot_uses_the_same_normalization_as_web(raw_client, client,
                                                   provider, make_user):
    make_user("alice", email="alice@example.com")
    for identifier in ("Alice@Example.com", "Ghost@Example.com", " Bob "):
        _forgot(raw_client, identifier)
        client.post("/forgot-password", json={"identifier": identifier})
    native, web = provider["calls"]["forgot"][0::2], (
        provider["calls"]["forgot"][1::2])
    assert native == web == ["alice", "ghost@example.com", "Bob"]


@pytest.mark.parametrize("request_kwargs", [
    {"data": "not json", "content_type": "application/json"},
    {"data": "identifier=alice",
     "content_type": "application/x-www-form-urlencoded"},
    {"json": ["alice"]},
    {"json": "alice"},
    {"json": 42},
])
def test_forgot_malformed_body_is_invalid_request(raw_client, provider,
                                                  request_kwargs):
    response = raw_client.post("/api/v1/auth/password/forgot",
                               **request_kwargs)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert _error(response)["retryable"] is False
    assert provider["calls"]["forgot"] == []


@pytest.mark.parametrize("body", [
    {}, {"identifier": None}, {"identifier": 42}, {"identifier": True},
    {"identifier": ["alice"]}, {"identifier": {"u": "alice"}},
    {"identifier": ""}, {"identifier": "   "},
    {"identifier": "a" * 255}, {"username": "alice"},
])
def test_forgot_missing_wrong_type_or_oversized_identifier(
        raw_client, provider, body):
    response = raw_client.post("/api/v1/auth/password/forgot", json=body)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert provider["calls"]["forgot"] == []


def test_forgot_identifier_at_the_bound_is_accepted(raw_client, provider):
    identifier = "a" * 242 + "@example.com"
    assert len(identifier) == mobile_password_recovery.MAX_IDENTIFIER_CHARS
    assert _forgot(raw_client, identifier).status_code == 202


def test_forgot_is_unavailable_when_provider_not_configured(
        raw_client, provider, monkeypatch):
    monkeypatch.setattr(mobile_password_recovery, "COGNITO_ENABLED", False)
    response = _forgot(raw_client)
    assert response.status_code == 503
    assert _error(response)["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert provider["calls"]["forgot"] == []


def test_forgot_repeat_requests_are_deterministic(raw_client, provider):
    first, second = _forgot(raw_client), _forgot(raw_client)
    assert _wire(first) == _wire(second)
    assert provider["calls"]["forgot"] == ["alice", "alice"]


# ---------------------------------------------------------------------------
# Forgot — enumeration resistance
# ---------------------------------------------------------------------------

@pytest.fixture
def population(make_user):
    """Accounts in every state the answer must not reveal."""
    make_user("verified", email="verified@example.com")
    make_user("unverified", email="unverified@example.com")
    # Local row whose provider identity is gone (local/provider mismatch).
    make_user("orphan", email="orphan@example.com")


# identifier → the provider's answer for that account (PreventUserExistence
# is ENABLED, so an unknown account normally answers success).
FORGOT_STATES = {
    "verified": None,
    "verified@example.com": None,
    "unverified": "InvalidParameterException",
    "unverified@example.com": "NotAuthorizedException",
    "orphan@example.com": "UserNotFoundException",
    "nobody": None,
    "nobody@example.com": "UserNotFoundException",
    "undeliverable@example.com": "CodeDeliveryFailureException",
    "account-throttled@example.com": "LimitExceededException",
    "lambda-failure@example.com": "UserLambdaValidationException",
    "provider-internal@example.com": "InternalErrorException",
    "network@example.com": "",
}


def test_forgot_every_account_state_answers_identically(
        raw_client, provider, population, monkeypatch):
    def forgot_password(username):
        provider["calls"]["forgot"].append(username)
        for identifier, failure in FORGOT_STATES.items():
            if failure and username in (identifier, identifier.split("@")[0]):
                raise _provider_error(failure)

    monkeypatch.setattr(cognito_service, "forgot_password", forgot_password)
    answers = {identifier: _wire(_forgot(raw_client, identifier))
               for identifier in FORGOT_STATES}
    assert len(set(answers.values())) == 1, answers
    status, body, _ = next(iter(answers.values()))
    assert status == 202
    assert json.loads(body) == {"password_reset": {"status": "accepted"}}
    assert len(provider["calls"]["forgot"]) == len(FORGOT_STATES)


def test_forgot_known_and_unknown_take_the_same_provider_path(
        raw_client, provider, population):
    """Both reach the provider exactly once — no local short-circuit whose
    absence of a network round-trip would be a timing oracle."""
    _forgot(raw_client, "verified@example.com")
    _forgot(raw_client, "ghost@example.com")
    assert provider["calls"]["forgot"] == ["verified", "ghost@example.com"]


# ---------------------------------------------------------------------------
# Reset — contract
# ---------------------------------------------------------------------------

def test_reset_success_exact_contract(raw_client, provider, make_user):
    make_user("alice", email="alice@example.com")
    response = _reset(raw_client, identifier=" Alice@Example.com ",
                      code=f" {CODE} ")
    assert response.status_code == 200
    assert response.get_json() == {"password_reset": {"status": "completed"}}
    assert provider["calls"]["confirm"] == [
        {"username": "alice", "code": CODE, "password": PASSWORD}]
    _assert_no_session_material(response)


@pytest.mark.parametrize("request_kwargs", [
    {"data": "not json", "content_type": "application/json"},
    {"json": ["alice", CODE, PASSWORD]},
    {"json": None},
])
def test_reset_malformed_body_is_invalid_request(raw_client, provider,
                                                 request_kwargs):
    response = raw_client.post("/api/v1/auth/password/reset",
                               **request_kwargs)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert provider["calls"]["confirm"] == []


@pytest.mark.parametrize("overrides", [
    {"identifier": None}, {"identifier": 7}, {"identifier": False},
    {"identifier": ""}, {"identifier": "a" * 255},
    {"code": None}, {"code": 482913}, {"code": True}, {"code": ""},
    {"code": "   "}, {"code": "1" * 65},
    {"new_password": None}, {"new_password": 12345678},
    {"new_password": ["Newpass123"]}, {"new_password": ""},
])
def test_reset_missing_wrong_type_or_oversized_fields(raw_client, provider,
                                                      overrides):
    body = {"identifier": "alice", "code": CODE, "new_password": PASSWORD}
    body.update(overrides)
    response = raw_client.post("/api/v1/auth/password/reset", json=body)
    assert response.status_code == 400
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"
    assert provider["calls"]["confirm"] == []


@pytest.mark.parametrize("missing", ["identifier", "code", "new_password"])
def test_reset_missing_keys(raw_client, provider, missing):
    body = {"identifier": "alice", "code": CODE, "new_password": PASSWORD}
    del body[missing]
    response = raw_client.post("/api/v1/auth/password/reset", json=body)
    assert _error(response)["code"] == "AUTH_INVALID_REQUEST"


@pytest.mark.parametrize("password", [
    "short1", "x" * 129 + "1", "12345678", "abcdefgh",
])
def test_reset_weak_password_is_the_canonical_policy_code(
        raw_client, provider, password):
    response = _reset(raw_client, new_password=password)
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "AUTH_PASSWORD_POLICY"
    assert error["retryable"] is False
    assert provider["calls"]["confirm"] == []


def test_password_policy_parity_with_native_registration(
        raw_client, provider, monkeypatch):
    """One policy: every password native registration refuses for policy,
    native reset refuses with the same code — and the reverse."""
    from app.blueprints import mobile_registration

    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    monkeypatch.setattr(cognito_service, "sign_up",
                        lambda **kwargs: "sub-x")
    for index, password in enumerate(
            ("short1", "12345678", "abcdefgh", "x" * 129 + "1",
             "Goodpass1", "Another9x")):
        register = raw_client.post("/api/v1/auth/register", json={
            "username": f"policy{index}", "email": f"p{index}@example.com",
            "password": password})
        reset = _reset(raw_client, new_password=password)
        refused = {r.status_code == 400 and _error(r)["code"]
                   == "AUTH_PASSWORD_POLICY" for r in (register, reset)}
        assert len(refused) == 1, password


@pytest.mark.parametrize(("provider_code", "status", "code", "retryable"), [
    ("CodeMismatchException", 400, "AUTH_VERIFICATION_CODE_INVALID", False),
    ("ExpiredCodeException", 400, "AUTH_VERIFICATION_CODE_INVALID", False),
    ("UserNotFoundException", 400, "AUTH_VERIFICATION_CODE_INVALID", False),
    ("NotAuthorizedException", 400, "AUTH_VERIFICATION_CODE_INVALID", False),
    ("UserNotConfirmedException", 400, "AUTH_VERIFICATION_CODE_INVALID",
     False),
    ("InvalidParameterException", 400, "AUTH_VERIFICATION_CODE_INVALID",
     False),
    ("InvalidPasswordException", 400, "AUTH_PASSWORD_POLICY", False),
    ("PasswordHistoryPolicyViolationException", 400, "AUTH_PASSWORD_POLICY",
     False),
    ("TooManyFailedAttemptsException", 429, "AUTH_RATE_LIMITED", True),
    ("LimitExceededException", 429, "AUTH_RATE_LIMITED", True),
    ("TooManyRequestsException", 429, "AUTH_RATE_LIMITED", True),
    ("InternalErrorException", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
    ("UnexpectedLambdaException", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
    ("", 503, "AUTH_TEMPORARILY_UNAVAILABLE", True),
])
def test_reset_provider_failures_are_typed_and_sanitized(
        raw_client, provider, provider_code, status, code, retryable):
    provider["failures"]["confirm"] = _provider_error(provider_code)
    response = _reset(raw_client, identifier="alice@example.com")
    assert response.status_code == status
    error = _error(response)
    assert (error["code"], error["retryable"]) == (code, retryable)
    text = _decoded(response)
    leaked = [provider_code or None, "Exception", "Traceback", "Cognito",
              _ERROR_MESSAGES.get(provider_code), "İşlem başarısız",
              "alice", "sub-"]
    for fragment in filter(None, leaked):
        assert fragment not in text, fragment
    _assert_no_session_material(response)


def test_forgot_provider_failures_never_leak_either(raw_client, provider):
    for provider_code in list(_ERROR_MESSAGES) + [
            "CodeDeliveryFailureException", ""]:
        provider["failures"]["forgot"] = _provider_error(provider_code)
        response = _forgot(raw_client, f"{provider_code or 'x'}@example.com")
        text = _decoded(response)
        assert response.status_code == 202
        assert "Exception" not in text and "İşlem" not in text


def test_reset_unexpected_service_exception_is_normalized(
        raw_client, provider, monkeypatch):
    monkeypatch.setattr(
        account_recovery, "reset_password",
        lambda *a: (_ for _ in ()).throw(RuntimeError("secret internals")))
    response = _reset(raw_client)
    assert response.status_code == 503
    assert _error(response)["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert "secret internals" not in _decoded(response)


def test_reset_is_unavailable_when_provider_not_configured(
        raw_client, provider, monkeypatch):
    monkeypatch.setattr(mobile_password_recovery, "COGNITO_ENABLED", False)
    response = _reset(raw_client)
    assert response.status_code == 503
    assert provider["calls"]["confirm"] == []


def test_reset_wrong_expired_and_unknown_account_are_indistinguishable(
        raw_client, provider, make_user):
    make_user("alice", email="alice@example.com")
    answers = []
    for identifier, failure in (
            ("alice", "CodeMismatchException"),
            ("alice", "ExpiredCodeException"),
            ("nobody", "CodeMismatchException"),
            ("nobody@example.com", "UserNotFoundException"),
            ("alice", "UserNotConfirmedException"),
            ("alice", "NotAuthorizedException")):
        provider["failures"]["confirm"] = _provider_error(failure)
        answers.append(_wire(_reset(raw_client, identifier=identifier)))
    assert len(set(answers)) == 1


def test_reset_repeat_after_success_is_the_provider_answer(
        raw_client, provider, make_user):
    make_user("alice", email="alice@example.com")
    assert _reset(raw_client).status_code == 200
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    again = _reset(raw_client)
    assert again.status_code == 400
    assert _error(again)["code"] == "AUTH_VERIFICATION_CODE_INVALID"
    assert User.query.filter_by(username="alice").one().credential_epoch == 1


def test_reset_fails_closed_when_distributed_throttle_is_unavailable(
        raw_client, provider, monkeypatch):
    monkeypatch.setattr(mobile_password_recovery, "login_throttle_available",
                        lambda: False)
    response = _reset(raw_client)
    assert response.status_code == 503
    assert _error(response)["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert provider["calls"]["confirm"] == []


# ---------------------------------------------------------------------------
# Session boundary and revocation authority
# ---------------------------------------------------------------------------

def _mobile_now():
    return datetime.utcnow().replace(microsecond=0)


@pytest.fixture
def mobile_login(monkeypatch):
    """A real native login for 'alice' against stubbed provider primitives."""
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: {
        "tokens": {"access_token": "provider-access", "id_token": "provider-id",
                   "refresh_token": "provider-refresh", "expires_in": 3600},
        "claims": {"sub": "sub-alice"},
    })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {"sub": "sub-alice", "email": "alice@example.com",
                    "email_verified": True}
        return {"sub": "sub-alice", "exp": calendar.timegm(
            (_mobile_now() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    return lambda: mobile_auth.login("alice", "Oldpass123", now=_mobile_now())


def test_forgot_and_reset_never_issue_a_session(
        raw_client, provider, make_user, monkeypatch):
    make_user("alice", email="alice@example.com")

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery must never sign anyone in")

    monkeypatch.setattr(cognito_service, "authenticate", forbidden)
    monkeypatch.setattr(mobile_auth, "login", forbidden)
    for response in (_forgot(raw_client), _reset(raw_client)):
        assert response.status_code in (200, 202)
        _assert_no_session_material(response)
    assert _session_rows() == (0, 0, 0, 0)
    me = raw_client.get("/api/v1/account/me")
    assert me.status_code == 401
    assert _error(me)["code"] == "AUTH_SESSION_EXPIRED"


def test_native_reset_revokes_live_sessions_like_the_web_reset(
        raw_client, provider, make_user, mobile_login):
    user = make_user("alice", email="alice@example.com")
    issued = mobile_login()
    db.session.add(CognitoSession(
        session_id="web-one", user_id=user.id, cognito_username="alice",
        access_token=session_store.encrypt_token("web-access"),
        refresh_token=session_store.encrypt_token("web-refresh"),
        access_token_exp=datetime.utcnow() + timedelta(hours=1)))
    db.session.commit()
    bearer = {"Authorization": f"Bearer {issued.access_credential}"}
    assert raw_client.get("/api/v1/account/me", headers=bearer).status_code == 200

    assert _reset(raw_client).status_code == 200

    assert raw_client.get("/api/v1/account/me", headers=bearer).status_code == 401
    refreshed = raw_client.post("/api/v1/auth/refresh", json={
        "refresh_credential": issued.refresh_credential})
    assert _error(refreshed)["code"] == "AUTH_REFRESH_FAILED"
    family = MobileAuthSession.query.one()
    assert family.revoked_reason == "credential_change"
    assert CognitoSession.query.count() == 0
    db.session.refresh(user)
    assert user.credential_epoch == 1
    assert sorted(provider["calls"]["revoke"]) == [
        "provider-refresh", "web-refresh"]


def test_native_reset_leaves_the_normal_login_working(
        raw_client, provider, make_user, mobile_login):
    make_user("alice", email="alice@example.com")
    assert _reset(raw_client).status_code == 200
    issued = mobile_login()
    assert raw_client.get("/api/v1/account/me", headers={
        "Authorization": f"Bearer {issued.access_credential}"}).status_code == 200


def test_native_reset_revocation_failure_is_never_success(
        raw_client, provider, make_user, monkeypatch):
    make_user("alice", email="alice@example.com")
    monkeypatch.setattr(
        mobile_auth, "revoke_all_for_user",
        lambda *a, **k: (_ for _ in ()).throw(mobile_auth.MobileAuthFailure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True, "storage_unavailable")))
    response = _reset(raw_client)
    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    # Replaying the spent code cannot help; the client restarts recovery.
    assert error["retryable"] is False


def test_native_reset_sends_the_same_password_changed_notice_as_web(
        raw_client, provider, make_user, monkeypatch):
    make_user("alice", email="alice@example.com")
    sent = []
    monkeypatch.setattr(email_service, "send_html_email",
                        lambda to, subject, html, **kw: sent.append(to))
    assert _reset(raw_client).status_code == 200
    assert sent == ["alice@example.com"]


def test_preauth_routes_ignore_a_presented_bearer(raw_client, provider,
                                                  monkeypatch, throttled):
    """No credential lookup runs for a bearer on a pre-auth route. Recorded,
    not raised: the limiter hook swallows lookup exceptions, so a raising stub
    could never fail this test."""
    looked_up = []
    monkeypatch.setattr(mobile_auth, "authenticate_access", looked_up.append)
    headers = {"Authorization": "Bearer " + "A" * 43}
    assert _forgot(raw_client, headers=headers).status_code == 202
    assert _reset(raw_client, headers=headers).status_code == 200
    assert looked_up == []


# ---------------------------------------------------------------------------
# Pre-auth rate limiting
# ---------------------------------------------------------------------------

def _assert_rate_limited(response):
    assert response.status_code == 429
    error = _error(response)
    assert error["code"] == "AUTH_RATE_LIMITED"
    assert error["retryable"] is True
    assert int(response.headers["Retry-After"]) > 0


def test_forgot_per_ip_budget(raw_client, provider, throttled):
    ip = {"REMOTE_ADDR": "198.51.100.10"}
    for i in range(5):
        assert _forgot(raw_client, f"user{i}",
                       environ_base=ip).status_code == 202
    _assert_rate_limited(_forgot(raw_client, "user9", environ_base=ip))
    assert _forgot(raw_client, "user9", environ_base={
        "REMOTE_ADDR": "198.51.100.11"}).status_code == 202


def test_forgot_per_identifier_budget_across_ips(raw_client, provider,
                                                 throttled):
    for i in range(3):
        assert _forgot(raw_client, "victim@example.com", environ_base={
            "REMOTE_ADDR": f"192.0.2.{i + 1}"}).status_code == 202
    _assert_rate_limited(_forgot(raw_client, " VICTIM@example.com ",
                                 environ_base={"REMOTE_ADDR": "192.0.2.99"}))
    assert provider["calls"]["forgot"].count("victim@example.com") == 3
    # The per-identifier answer is the same whether or not an account exists:
    # the budget is keyed on what was submitted, never on a lookup.
    assert _forgot(raw_client, "other@example.com", environ_base={
        "REMOTE_ADDR": "192.0.2.100"}).status_code == 202


def test_forgot_identifier_budget_is_identical_for_known_and_unknown(
        raw_client, provider, make_user, throttled):
    make_user("alice", email="alice@example.com")
    sequences = []
    for identifier, net in (("alice@example.com", 10), ("ghost@example.com", 20)):
        sequences.append([_wire(_forgot(raw_client, identifier, environ_base={
            "REMOTE_ADDR": f"203.0.113.{net + i}"}))[0] for i in range(5)])
    assert sequences[0] == sequences[1] == [202, 202, 202, 429, 429]


def test_reset_per_ip_budget(raw_client, provider, throttled):
    ip = {"REMOTE_ADDR": "203.0.113.50"}
    for i in range(10):
        assert _reset(raw_client, identifier=f"user{i}",
                      environ_base=ip).status_code == 200
    _assert_rate_limited(_reset(raw_client, identifier="user99",
                                environ_base=ip))


def test_reset_code_guessing_is_bounded_per_identifier_across_ips(
        raw_client, provider, throttled):
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    for i in range(5):
        response = _reset(raw_client, code=f"{i:06d}", environ_base={
            "REMOTE_ADDR": f"198.18.0.{i + 1}"})
        assert response.status_code == 400
    _assert_rate_limited(_reset(raw_client, code="999999", environ_base={
        "REMOTE_ADDR": "198.18.0.200"}))
    assert len(provider["calls"]["confirm"]) == 5
    # A different account is not collateral damage.
    assert _reset(raw_client, identifier="otheruser", environ_base={
        "REMOTE_ADDR": "198.18.0.201"}).status_code == 400


def test_reset_policy_errors_and_successes_do_not_consume_the_code_budget(
        raw_client, provider, throttled):
    for i in range(6):
        assert _reset(raw_client, new_password="weak", environ_base={
            "REMOTE_ADDR": f"198.18.1.{i + 1}"}).status_code == 400
    for i in range(6):
        assert _reset(raw_client, environ_base={
            "REMOTE_ADDR": f"198.18.2.{i + 1}"}).status_code == 200


# ---------------------------------------------------------------------------
# Blocking concurrency capacity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [_forgot, _reset])
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
    assert provider["calls"]["forgot"] == provider["calls"]["confirm"] == []


@pytest.mark.parametrize(("call", "attribute", "failure"), [
    (_forgot, "forgot_password", None),
    (_forgot, "forgot_password", "UserNotFoundException"),
    (_reset, "confirm_forgot_password", None),
    (_reset, "confirm_forgot_password", "CodeMismatchException"),
])
def test_provider_call_holds_exactly_one_slot_and_releases_it(
        raw_client, provider, monkeypatch, call, attribute, failure):
    semaphore = threading.BoundedSemaphore(2)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    observed = []
    original = getattr(cognito_service, attribute)

    def observing(*args):
        observed.append(semaphore._value)
        return original(*args)

    monkeypatch.setattr(cognito_service, attribute, observing)
    if failure:
        provider["failures"][
            "forgot" if attribute == "forgot_password" else "confirm"] = (
                _provider_error(failure))
    call(raw_client)
    assert observed == [1]
    assert semaphore._value == 2


# ---------------------------------------------------------------------------
# Sensitive logging
# ---------------------------------------------------------------------------

def test_no_password_code_or_identifier_reaches_the_logs(
        raw_client, provider, make_user, caplog):
    caplog.set_level(logging.DEBUG)
    make_user("alice", email="alice@example.com")
    secret_password = "Zq7SecretPassw0rd"
    secret_code = "731905"
    private_identifier = "private.person@example.com"
    _forgot(raw_client, private_identifier)
    provider["failures"]["forgot"] = _provider_error("UserNotFoundException")
    _forgot(raw_client, private_identifier)
    _reset(raw_client, identifier=private_identifier, code=secret_code,
           new_password=secret_password)
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    _reset(raw_client, identifier=private_identifier, code=secret_code,
           new_password=secret_password)
    _reset(raw_client, identifier=private_identifier, code=secret_code,
           new_password="weak")
    text = caplog.text
    assert secret_password not in text
    assert secret_code not in text
    assert private_identifier not in text
    assert "account_recovery event=request outcome=requested" in text
    assert "account_recovery event=request outcome=not_eligible" in text
    assert "account_recovery event=reset outcome=code_invalid" in text
