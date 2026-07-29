from datetime import datetime

from app.services import mobile_auth


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
