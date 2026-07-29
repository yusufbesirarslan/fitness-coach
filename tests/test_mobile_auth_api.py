import calendar
from datetime import datetime, timedelta

import pytest
from types import SimpleNamespace

from app.services import cognito_jwt, cognito_service, mobile_auth
from app.models import MobileAuthSession
from app.extensions import db, limiter
from app.blueprints import mobile_api


@pytest.fixture
def mobile_user(app, make_user):
    return make_user("mobile-service", cognito_sub="sub-mobile")


@pytest.fixture
def provider(monkeypatch):
    access_exp = datetime(2026, 7, 29, 11)
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, password: {
        "tokens": {
            "access_token": "provider-access", "id_token": "provider-id",
            "refresh_token": "provider-refresh", "expires_in": 3600,
        }, "claims": {"sub": "sub-mobile"},
    })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {"sub": "sub-mobile", "email": "mobile@example.com",
                    "email_verified": True}
        return {"sub": "sub-mobile",
                "exp": calendar.timegm(access_exp.timetuple())}

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    return access_exp


def test_mobile_error_envelope_contains_only_approved_fields(raw_client):
    response = raw_client.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 400
    assert set(response.json) == {"error"}
    assert set(response.json["error"]) == {
        "code", "message", "retryable", "request_id"}


def test_cookie_auth_cannot_authenticate_mobile_me(client, make_user):
    user = make_user("cookie-only")
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user.id)
        browser_session["_fresh"] = True
    response = client.get("/api/v1/account/me")
    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_mobile_post_is_csrf_exempt_but_web_post_is_not(raw_client):
    assert raw_client.post("/api/v1/auth/login", json={}).status_code != 403
    assert raw_client.post("/login", json={}).status_code == 403


def test_login_success_has_exact_opaque_session_contract(raw_client, monkeypatch):
    issued = mobile_auth.IssuedSession(
        "access", "refresh",
        datetime(2026, 7, 29, 10, 15), datetime(2026, 8, 5, 10, 0))
    monkeypatch.setattr(mobile_auth, "login", lambda username, password: issued)
    response = raw_client.post("/api/v1/auth/login", json={
        "username": "alice", "password": "correct"})
    assert response.status_code == 200
    assert set(response.json) == {"session"}
    assert set(response.json["session"]) == {
        "type", "token_type", "access_credential", "refresh_credential",
        "access_expires_at", "refresh_expires_at"}
    assert "Set-Cookie" not in response.headers


def test_mobile_login_fails_closed_when_distributed_throttle_is_unavailable(
        raw_client, monkeypatch):
    monkeypatch.setattr(mobile_api, "login_throttle_available", lambda: False)
    monkeypatch.setattr(
        mobile_auth, "login",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not call provider")))
    response = raw_client.post("/api/v1/auth/login", json={
        "username": "alice", "password": "correct"})
    assert response.status_code == 503
    assert response.json["error"]["code"] == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True


def test_mobile_refresh_limit_uses_mobile_error_envelope(raw_client, app):
    app.config["MOBILE_AUTH_REFRESH_RATELIMIT"] = "1 per minute"
    limiter.reset()
    limiter.enabled = True
    try:
        request = {"refresh_credential": "not-a-valid-credential"}
        first = raw_client.post(
            "/api/v1/auth/refresh", json=request,
            environ_base={"REMOTE_ADDR": "192.0.2.88"})
        second = raw_client.post(
            "/api/v1/auth/refresh", json=request,
            environ_base={"REMOTE_ADDR": "192.0.2.88"})
        assert first.status_code != 429
        assert second.status_code == 429
        assert second.json["error"]["code"] == "AUTH_RATE_LIMITED"
        assert set(second.json["error"]) == {
            "code", "message", "retryable", "request_id"}
    finally:
        limiter.enabled = False
        limiter.reset()


def test_strict_bearer_parser_rejects_cookie_and_malformed_headers(
        raw_client, monkeypatch):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda value: (_ for _ in ()).throw(AssertionError("must not authenticate")))
    for value in (None, "", "Basic abc", "Bearer", "Bearer one two"):
        headers = {} if value is None else {"Authorization": value}
        response = raw_client.get("/api/v1/account/me", headers=headers)
        assert response.status_code == 401
        assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_account_projection_is_exact_and_has_no_cors_headers(
        raw_client, make_user, monkeypatch):
    user = make_user("mobile-projection")
    user.full_name = "Mobile User"
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda value: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": "safe-sub"}))
    response = raw_client.get(
        "/api/v1/account/me", headers={"Authorization": "Bearer opaque"})
    assert response.status_code == 200
    assert set(response.json["user"]) == {
        "username", "display_name", "profile_complete", "preferred_language",
        "goal", "goal_type"}
    assert "Access-Control-Allow-Origin" not in response.headers


def test_logout_commits_local_revoke_before_provider_call(
        raw_client, mobile_user, provider, monkeypatch):
    issued = mobile_auth.login("mobile-service", "correct", now=datetime(2026, 7, 29, 10))
    family_id = MobileAuthSession.query.one().id
    observed = {}

    def revoke(token):
        family = db.session.get(MobileAuthSession, family_id)
        observed["revoked_at"] = family.revoked_at
        observed["ciphertext"] = family.cognito_refresh_token

    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "revoke_token", revoke)
    response = raw_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {issued.access_credential}"})
    assert response.status_code == 204
    assert response.data == b""
    assert observed["revoked_at"] is not None
    assert observed["ciphertext"] is None


def test_remote_revoke_failure_still_returns_empty_204(
        raw_client, mobile_user, provider, monkeypatch, caplog):
    issued = mobile_auth.login("mobile-service", "correct", now=datetime(2026, 7, 29, 10))
    from app.services import cognito_service
    monkeypatch.setattr(
        cognito_service, "revoke_token",
        lambda token: (_ for _ in ()).throw(RuntimeError("raw-provider-error")))
    response = raw_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {issued.access_credential}"})
    assert response.status_code == 204
    assert response.data == b""
    assert "raw-provider-error" not in caplog.text
    assert issued.access_credential not in caplog.text
    assert issued.refresh_credential not in caplog.text
