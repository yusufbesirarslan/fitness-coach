"""LP14 PR-D: closed, transactional, Bearer-owned language setting."""
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import User
from app.services import mobile_auth
from test_default_rate_limit_identity import tight_default_limiter  # noqa: F401
from test_mobile_account_profile_api import BODY as PROFILE_BODY

PATH = "/api/v1/account/language"
ME = "/api/v1/account/me"


@pytest.fixture
def principal(monkeypatch):
    current = {}

    def authenticate(raw):
        user = current["user"]
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def select(user):
        current["user"] = user
        return {"Authorization": "Bearer opaque-language-access"}
    return select


def _error(response, status, code, retryable=False):
    assert response.status_code == status
    assert set(response.json) == {"error"}
    error = response.json["error"]
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert (error["code"], error["retryable"]) == (code, retryable)
    assert error["request_id"]
    assert response.headers["Cache-Control"] == "no-store"
    return error


def _columns(user):
    return {column.name: getattr(user, column.name)
            for column in User.__table__.columns}


@pytest.mark.parametrize(("initial", "requested"), [
    ("tr", "en"), ("en", "tr"), ("en", "en"),
])
def test_success_is_committed_idempotent_and_matches_me(
        raw_client, make_user, principal, initial, requested):
    user = make_user("language-owner", language=initial, profile_complete=True,
                     full_name="Owner", weight=80, goal="kilo verme")
    user_id = user.id
    before = _columns(user)
    headers = principal(user)
    response = raw_client.put(PATH, json={"language": requested}, headers=headers)
    # Same HTTP 200 and projection as the existing account profile PUT.
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert response.json["user"]["preferred_language"] == requested
    db.session.expire_all()
    assert _columns(db.session.get(User, user_id)) == {
        **before, "language": requested}
    assert raw_client.get(ME, headers=headers).json == response.json
    repeated = raw_client.put(PATH, json={"language": requested}, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json == response.json


def test_bearer_is_mandatory_even_with_browser_cookie(
        raw_client, client, make_user):
    user = make_user("cookie-language", language="tr")
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    for test_client in (raw_client, client):
        response = test_client.put(PATH, json={"language": "en"})
        _error(response, 401, "AUTH_SESSION_EXPIRED")
    db.session.refresh(user)
    assert user.language == "tr"


@pytest.mark.parametrize("body", [
    {}, {"other": "en"}, {"language": None}, {"language": ""},
    {"language": "EN"}, {"language": "english"}, {"language": "de"},
    {"language": " en"}, {"language": "en "}, {"language": False},
    {"language": 1}, {"language": []}, {"language": {}},
    {"language": "en", "foo": "bar"},
    {"language": "en", "user_id": 999},
    {"language": "en", "email": "other@example.com"},
    [], "en", 7,
])
def test_invalid_body_is_closed_and_never_mutates(
        raw_client, make_user, principal, body):
    user = make_user("invalid-language", language="tr")
    before = _columns(user)
    response = raw_client.put(PATH, json=body, headers=principal(user))
    _error(response, 400, "LANGUAGE_INVALID_REQUEST")
    db.session.refresh(user)
    assert _columns(user) == before


@pytest.mark.parametrize("body", [None, "{", "null", "not json"])
def test_missing_or_malformed_json_is_typed(
        raw_client, make_user, principal, body):
    user = make_user("malformed-language")
    response = raw_client.put(PATH, data=body, headers=principal(user),
                              content_type="application/json")
    _error(response, 400, "LANGUAGE_INVALID_REQUEST")
    db.session.refresh(user)
    assert user.language == "tr"


def test_owner_isolation_and_query_identifiers_cannot_select_owner(
        raw_client, make_user, principal):
    alice = make_user("language-alice", language="tr")
    bob = make_user("language-bob", language="tr")
    before_bob = _columns(bob)
    response = raw_client.put(
        PATH + f"?user_id={bob.id}&email={bob.email}",
        json={"language": "en"}, headers=principal(alice))
    assert response.status_code == 200
    db.session.refresh(alice)
    db.session.refresh(bob)
    assert alice.language == "en"
    assert _columns(bob) == before_bob


def test_profile_still_rejects_language(raw_client, make_user, principal):
    user = make_user("language-profile")
    response = raw_client.put(
        "/api/v1/account/profile", json={**PROFILE_BODY, "language": "en"},
        headers=principal(user))
    _error(response, 400, "PROFILE_INVALID_REQUEST")
    db.session.refresh(user)
    assert user.language == "tr"
    assert user.profile_complete is False


@pytest.mark.parametrize("failure_stage", ["flush", "commit"])
def test_persistence_failure_rolls_back_without_leaking(
        raw_client, make_user, principal, caplog, failure_stage):
    user = make_user("language-failure", language="tr")
    before = _columns(user)

    def refuse(*args):
        raise RuntimeError("injected secret-token database-detail")

    event_name = "before_flush" if failure_stage == "flush" else "before_commit"
    event.listen(db.session, event_name, refuse)
    try:
        with caplog.at_level(logging.ERROR):
            response = raw_client.put(
                PATH, json={"language": "en"}, headers=principal(user))
    finally:
        event.remove(db.session, event_name, refuse)
    _error(response, 503, "LANGUAGE_TEMPORARILY_UNAVAILABLE", True)
    # Rollback expires the in-memory mutation too, before any fresh read.
    assert user.language == "tr"
    db.session.refresh(user)
    assert _columns(user) == before
    text = response.get_data(as_text=True) + caplog.text
    for forbidden in ("injected", "secret-token", "database-detail",
                      "opaque-language-access", "language-failure"):
        assert forbidden not in text
    assert raw_client.get(ME, headers=principal(user)).json["user"][
        "preferred_language"] == "tr"
    assert raw_client.put(PATH, json={"language": "en"},
                          headers=principal(user)).status_code == 200


def test_projection_failure_never_returns_false_success(
        raw_client, make_user, principal, monkeypatch):
    from app.blueprints import mobile_account_language
    user = make_user("language-projection")

    def refuse(user):
        raise RuntimeError("private projection detail")

    monkeypatch.setattr(mobile_account_language, "account_projection", refuse)
    response = raw_client.put(PATH, json={"language": "en"},
                              headers=principal(user))
    _error(response, 503, "LANGUAGE_TEMPORARILY_UNAVAILABLE", True)
    # The write committed; a retry is safe, and GET reflects persisted truth.
    assert raw_client.get(ME, headers=principal(user)).json["user"][
        "preferred_language"] == "en"


def test_inherits_default_verified_owner_limit(
        raw_client, make_user, principal, tight_default_limiter):
    alice = make_user("limit-language-alice")
    bob = make_user("limit-language-bob")
    headers = principal(alice)
    for ip in ("203.0.113.1", "203.0.113.2"):
        assert raw_client.put(PATH, json={"language": "en"}, headers=headers,
                              environ_base={"REMOTE_ADDR": ip}).status_code == 200
    limited = raw_client.put(PATH, json={"language": "tr"}, headers=headers)
    _error(limited, 429, "AUTH_RATE_LIMITED", True)
    assert int(limited.headers["Retry-After"]) > 0
    db.session.refresh(alice)
    assert alice.language == "en"
    assert raw_client.put(PATH, json={"language": "en"},
                          headers=principal(bob)).status_code == 200


def test_route_is_on_canonical_blueprint_and_marked_bearer_required(app):
    rule = next(rule for rule in app.url_map.iter_rules() if rule.rule == PATH)
    assert rule.endpoint == "mobile_api.put_account_language"
    assert rule.methods - {"OPTIONS"} == {"PUT"}
    assert app.view_functions[rule.endpoint]._require_mobile_auth is True


def test_query_budget_has_one_language_update_and_bounded_projection(
        raw_client, make_user, principal):
    user = make_user("query-language", language="tr")
    headers = principal(user)
    # Authentication is stubbed; count only the mutation and its projection.
    db.session.refresh(user)
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", observe)
    try:
        response = raw_client.put(PATH, json={"language": "en"}, headers=headers)
    finally:
        event.remove(db.engine, "before_cursor_execute", observe)
    assert response.status_code == 200
    assert len(statements) == 3  # UPDATE, post-commit User reload, UserSession read
    [update] = [sql for sql in statements if sql.lstrip().upper().startswith("UPDATE")]
    assert 'SET language=? WHERE' in update
