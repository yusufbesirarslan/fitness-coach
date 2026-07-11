"""Native Cognito password recovery route and session lifecycle tests."""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import CognitoSession, User
from app.services import cognito_service, session_store
from app.services.cognito_service import CognitoServiceError


def _reset_context(client, username="alice", *, minutes_ago=0):
    with client.session_transaction() as state:
        state["password_reset_username"] = username
        state["password_reset_started_at"] = (
            datetime.utcnow() - timedelta(minutes=minutes_ago)
        ).timestamp()


def _reset_payload(**overrides):
    payload = {
        "code": "123456",
        "password": "Newpass123",
        "confirm_password": "Newpass123",
    }
    payload.update(overrides)
    return payload


def test_forgot_known_and_unknown_are_indistinguishable(client, monkeypatch):
    known = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(known)
    db.session.commit()
    calls = []
    monkeypatch.setattr(cognito_service, "forgot_password", calls.append)

    known_response = client.post(
        "/forgot-password", json={"identifier": " Alice@Example.com "})
    with client.session_transaction() as state:
        state.clear()
    unknown_response = client.post(
        "/forgot-password", json={"identifier": "missing@example.com"})

    assert known_response.status_code == unknown_response.status_code == 200
    assert known_response.get_json()["message"] == unknown_response.get_json()["message"]
    assert calls == ["alice", "missing@example.com"]


def test_forgot_provider_error_still_returns_generic_success(client, monkeypatch):
    def fail(_identifier):
        raise CognitoServiceError("Kullanıcı bulunamadı", "UserNotFoundException")

    monkeypatch.setattr(cognito_service, "forgot_password", fail)
    response = client.post("/forgot-password", json={"identifier": "nobody"})
    assert response.status_code == 200
    assert response.get_json()["next"] == "/reset-password"


def test_forgot_requires_identifier(client):
    response = client.post("/forgot-password", json={"identifier": "  "})
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_reset_success_is_single_use_and_invalidates_all_sessions(client, monkeypatch):
    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    for suffix in ("one", "two"):
        db.session.add(CognitoSession(
            session_id=suffix,
            user_id=user.id,
            cognito_username="alice",
            access_token="encrypted",
            refresh_token="encrypted",
            access_token_exp=datetime.utcnow() + timedelta(hours=1),
        ))
    db.session.commit()
    with client.session_transaction() as state:
        state["password_reset_username"] = "alice"
        state["password_reset_started_at"] = datetime.utcnow().timestamp()
        state["_user_id"] = str(user.id)
        state["cognito_sid"] = "one"

    calls = []
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", lambda *args: calls.append(args))
    response = client.post("/reset-password", json=_reset_payload())

    assert response.status_code == 200
    assert calls == [("alice", "123456", "Newpass123")]
    assert CognitoSession.query.filter_by(user_id=user.id).count() == 0
    with client.session_transaction() as state:
        assert "password_reset_username" not in state
        assert "password_reset_started_at" not in state
        assert "_user_id" not in state
        assert "cognito_sid" not in state
    assert client.post("/reset-password", json=_reset_payload()).status_code == 409


def test_reset_rejects_expired_local_context(client):
    _reset_context(client, minutes_ago=16)
    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 409
    with client.session_transaction() as state:
        assert "password_reset_username" not in state
        assert "password_reset_started_at" not in state


@pytest.mark.parametrize("payload", [
    {"code": "", "password": "Newpass123", "confirm_password": "Newpass123"},
    {"code": "123456", "password": "", "confirm_password": ""},
])
def test_reset_requires_all_fields(client, payload):
    _reset_context(client)
    response = client.post("/reset-password", json=payload)
    assert response.status_code == 400


def test_reset_rejects_password_mismatch(client):
    _reset_context(client)
    response = client.post(
        "/reset-password", json=_reset_payload(confirm_password="Different123"))
    assert response.status_code == 400


def test_reset_rejects_weak_password(client):
    _reset_context(client)
    response = client.post(
        "/reset-password", json=_reset_payload(password="weak", confirm_password="weak"))
    assert response.status_code == 400


@pytest.mark.parametrize(("code", "status"), [
    ("CodeMismatchException", 400),
    ("ExpiredCodeException", 400),
    ("LimitExceededException", 429),
    ("TooManyRequestsException", 429),
    ("UserNotFoundException", 400),
    ("NotAuthorizedException", 400),
])
def test_reset_maps_provider_errors(client, monkeypatch, code, status):
    _reset_context(client)

    def fail(*_args):
        raise CognitoServiceError("provider detail", code)

    monkeypatch.setattr(cognito_service, "confirm_forgot_password", fail)
    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == status
    assert response.get_json()["error"] != "provider detail"


def test_delete_for_user_removes_only_target_sessions(app):
    alice = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    bob = User(username="bob", email="bob@example.com", cognito_sub="sub-bob")
    db.session.add_all([alice, bob])
    db.session.commit()
    for user, sid in ((alice, "alice-one"), (alice, "alice-two"), (bob, "bob-one")):
        db.session.add(CognitoSession(
            session_id=sid,
            user_id=user.id,
            cognito_username=user.username,
            access_token="encrypted",
            refresh_token="encrypted",
            access_token_exp=datetime.utcnow() + timedelta(hours=1),
        ))
    db.session.commit()

    assert session_store.delete_for_user(alice.id) == 2
    assert CognitoSession.query.filter_by(user_id=alice.id).count() == 0
    assert CognitoSession.query.filter_by(user_id=bob.id).count() == 1
