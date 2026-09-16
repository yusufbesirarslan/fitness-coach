"""Native Cognito password recovery route and session lifecycle tests."""
import time
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import CognitoSession, User
from app.services import cognito_service, session_store
from app.services.cognito_service import CognitoServiceError


def _reset_context(client, username="alice", *, minutes_ago=0):
    with client.session_transaction() as state:
        state["password_reset_username"] = username
        state["password_reset_started_at"] = time.time() - (minutes_ago * 60)


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
        state["password_reset_started_at"] = time.time()
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


# --- Credential change must end MOBILE sessions too -------------------------
# Web sessions die with their CognitoSession row, so deleting the rows was
# enough for them. Mobile sessions are authorized by
# `mobile_auth.authenticate_access`, which validates the STORED provider access
# token OFFLINE — a reset that only touched web rows left a native session
# usable for the rest of its family's absolute lifetime.

def _mobile_now():
    """Anchor mobile-login clocks to the real wall clock.

    The opaque access credential is minted from this value but validated
    against datetime.utcnow() by the request path, so a hard-coded instant
    here would only hold for one MOBILE_AUTH_ACCESS_TTL_SECONDS window on
    one calendar day and red every other run.
    """
    return datetime.utcnow().replace(microsecond=0)


@pytest.fixture
def mobile_provider(monkeypatch):
    """Provider stubs for a real mobile login, plus a revoke_token spy."""
    import calendar
    from app.services import cognito_jwt

    revoked = []
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, password: {
        "tokens": {
            "access_token": "provider-access", "id_token": "provider-id",
            "refresh_token": "provider-refresh", "expires_in": 3600,
        },
        "claims": {"sub": "sub-alice"},
    })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {"sub": "sub-alice", "email": "alice@example.com",
                    "email_verified": True}
        return {"sub": "sub-alice",
                "exp": calendar.timegm(
                    (_mobile_now() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    monkeypatch.setattr(cognito_service, "revoke_token", revoked.append)
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", lambda *args: None)
    return revoked


def _mobile_login(username="alice"):
    from app.services import mobile_auth

    return mobile_auth.login(username, "Oldpass123", now=_mobile_now())


def test_reset_revokes_mobile_sessions_and_401s_the_live_credential(
        client, mobile_provider):
    from app.models import MobileAuthSession

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    issued = _mobile_login()
    headers = {"Authorization": f"Bearer {issued.access_credential}"}
    # Precondition: the mobile credential works while the password is unchanged.
    assert client.get("/api/v1/account/me", headers=headers).status_code == 200

    _reset_context(client)
    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 200

    rejected = client.get("/api/v1/account/me", headers=headers)
    assert rejected.status_code == 401
    assert rejected.get_json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.revoked_reason == "credential_change"
    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None


def test_reset_revokes_mobile_refresh_credential_so_no_new_access_is_minted(
        client, mobile_provider):
    issued = _mobile_login()
    user = User.query.filter_by(username="alice").one()
    assert user.cognito_sub == "sub-alice"

    _reset_context(client)
    assert client.post("/reset-password", json=_reset_payload()).status_code == 200

    replayed = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_credential": issued.refresh_credential})
    assert replayed.status_code == 401
    assert replayed.get_json()["error"]["code"] == "AUTH_REFRESH_FAILED"


def test_reset_revokes_stored_provider_refresh_tokens_for_both_surfaces(
        client, mobile_provider):
    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    _mobile_login()
    db.session.add(CognitoSession(
        session_id="web-one",
        user_id=user.id,
        cognito_username="alice",
        access_token=session_store.encrypt_token("web-access"),
        refresh_token=session_store.encrypt_token("web-refresh"),
        access_token_exp=datetime.utcnow() + timedelta(hours=1),
    ))
    db.session.commit()

    _reset_context(client)
    assert client.post("/reset-password", json=_reset_payload()).status_code == 200

    # ConfirmForgotPassword does not revoke refresh tokens and this route holds
    # no access token, so per-token RevokeToken is the only provider-side reach.
    assert sorted(mobile_provider) == ["provider-refresh", "web-refresh"]
    assert CognitoSession.query.filter_by(user_id=user.id).count() == 0


def test_reset_still_succeeds_when_provider_revocation_fails(
        client, mobile_provider, monkeypatch):
    from app.models import MobileAuthSession

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    _mobile_login()
    monkeypatch.setattr(
        cognito_service, "revoke_token",
        lambda _token: (_ for _ in ()).throw(
            CognitoServiceError("provider down", "InternalErrorException")))

    _reset_context(client)
    response = client.post("/reset-password", json=_reset_payload())

    # Local revocation is what ends the session; the provider call is advisory.
    assert response.status_code == 200
    assert MobileAuthSession.query.one().revoked_at is not None


def test_reset_reports_503_when_mobile_revocation_cannot_be_persisted(
        client, mobile_provider, monkeypatch):
    from app.models import MobileAuthSession
    from app.services import mobile_auth

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    _mobile_login()
    db.session.add(CognitoSession(
        session_id="web-one",
        user_id=user.id,
        cognito_username="alice",
        access_token=session_store.encrypt_token("web-access"),
        refresh_token=session_store.encrypt_token("web-refresh"),
        access_token_exp=datetime.utcnow() + timedelta(hours=1),
    ))
    db.session.commit()
    monkeypatch.setattr(
        mobile_auth, "revoke_all_for_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            mobile_auth.MobileAuthFailure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True, "storage_unavailable")))

    _reset_context(client)
    response = client.post("/reset-password", json=_reset_payload())

    # A 200 here would claim every session was closed when none were.
    assert response.status_code == 503
    assert response.get_json()["error"]
    assert MobileAuthSession.query.one().revoked_at is None
    assert CognitoSession.query.filter_by(user_id=user.id).count() == 1


def test_provider_refresh_tokens_are_scoped_and_skip_unreadable_ciphertext(app):
    alice = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    bob = User(username="bob", email="bob@example.com", cognito_sub="sub-bob")
    db.session.add_all([alice, bob])
    db.session.commit()
    rows = (
        (alice, "alice-one", session_store.encrypt_token("alice-refresh")),
        (alice, "alice-broken", "not-a-fernet-token"),
        (bob, "bob-one", session_store.encrypt_token("bob-refresh")),
    )
    for user, sid, ciphertext in rows:
        db.session.add(CognitoSession(
            session_id=sid,
            user_id=user.id,
            cognito_username=user.username,
            access_token="encrypted",
            refresh_token=ciphertext,
            access_token_exp=datetime.utcnow() + timedelta(hours=1),
        ))
    db.session.commit()

    assert session_store.provider_refresh_tokens_for_user(alice.id) == [
        "alice-refresh"]
    assert session_store.provider_refresh_tokens_for_user(bob.id) == ["bob-refresh"]


def test_provider_revocation_is_gated_and_capacity_refusal_is_not_fatal(
        client, mobile_provider, monkeypatch):
    """Every blocking Cognito round-trip added here takes the shared gate.

    One worker with 8 threads cannot afford an ungated sequence of provider
    calls; a capacity refusal must still leave the reset successful, because
    local revocation — not the provider call — is what ends the sessions.
    """
    from app.blueprints import auth as auth_bp
    from app.models import MobileAuthSession
    from app.services import ai_gate

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    _mobile_login()
    entered = []
    monkeypatch.setattr(
        auth_bp, "blocking_concurrency_slot",
        lambda *args, **kwargs: entered.append(True) or (_ for _ in ()).throw(
            ai_gate.BlockingConcurrencyLimit("exhausted")))

    _reset_context(client)
    response = client.post("/reset-password", json=_reset_payload())

    assert entered == [True]
    assert mobile_provider == []          # gate refused before any network call
    assert response.status_code == 200    # …and the reset still succeeded
    assert MobileAuthSession.query.one().revoked_at is not None


def test_provider_revocation_is_bounded_so_a_thread_cannot_park_for_minutes(
        client, mobile_provider, monkeypatch):
    from app.blueprints import auth as auth_bp
    from app.models import MobileAuthSession

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    _mobile_login()
    limit = auth_bp._PROVIDER_REVOKE_LIMIT
    for index in range(limit + 3):
        db.session.add(CognitoSession(
            session_id=f"web-{index}",
            user_id=user.id,
            cognito_username="alice",
            access_token=session_store.encrypt_token("web-access"),
            refresh_token=session_store.encrypt_token(f"web-refresh-{index}"),
            access_token_exp=datetime.utcnow() + timedelta(hours=1),
        ))
    db.session.commit()

    _reset_context(client)
    assert client.post("/reset-password", json=_reset_payload()).status_code == 200

    assert len(mobile_provider) == limit
    # Local revocation is NOT capped — it is the authoritative step.
    assert MobileAuthSession.query.one().revoked_at is not None
    assert CognitoSession.query.filter_by(user_id=user.id).count() == 0


def test_reset_route_publishes_the_credential_fence(client, mobile_provider):
    """The live route, not just the primitive, moves the fence.

    Without this the epoch could be bumped only by a test calling
    `revoke_all_for_user` directly, and the production credential-change path
    would leave every in-flight login unfenced.
    """
    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    assert user.credential_epoch == 0

    _reset_context(client)
    assert client.post("/reset-password", json=_reset_payload()).status_code == 200

    db.session.refresh(user)
    assert user.credential_epoch == 1


def test_reset_route_refuses_a_mobile_login_that_authenticated_before_it(
        client, mobile_provider, monkeypatch):
    """End to end: authenticate with the old password, reset, then try to land.

    The mobile login reaches Cognito over the network and writes its family row
    only after that call returns, so the whole reset request is driven from
    inside the provider stub - the exact interleaving the fence exists for, with
    no sleep and no thread. Nothing may survive it: not a family, not a
    credential, not a 200.
    """
    from app.models import MobileAuthSession, MobileAccessCredential
    from app.services import mobile_auth

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()

    real_authenticate = cognito_service.authenticate
    landed = {}

    def authenticate(username, password):
        tokens = real_authenticate(username, password)
        if "reset" not in landed:
            _reset_context(client)
            landed["reset"] = client.post("/reset-password", json=_reset_payload())
        return tokens

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        _mobile_login()

    assert landed["reset"].status_code == 200
    assert exc.value.status == 401
    assert exc.value.retryable is False
    assert exc.value.reason == "credential_changed_during_login"
    assert MobileAuthSession.query.count() == 0
    assert MobileAccessCredential.query.count() == 0


def test_reset_route_leaves_a_later_mobile_login_working(client, mobile_provider):
    """The fence closes the race, not the product."""
    from app.services import mobile_auth

    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()

    _reset_context(client)
    assert client.post("/reset-password", json=_reset_payload()).status_code == 200

    issued = mobile_auth.login(
        "alice", "Newpass123", now=_mobile_now() + timedelta(minutes=1))
    accepted = client.get(
        "/api/v1/account/me",
        headers={"Authorization": f"Bearer {issued.access_credential}"})
    assert accepted.status_code == 200
