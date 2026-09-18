"""F5: web logout is GlobalSignOut and must end AxisAI-local sessions too.

    python -m pytest tests/test_web_logout_global_revocation.py -v
"""
import calendar
from datetime import datetime, timedelta

import pytest

from app.blueprints import auth as auth_bp
from app.extensions import db
from app.models import CognitoSession, MobileAuthSession, User
from app.services import (
    cognito_jwt, cognito_service, mobile_auth, session_store,
)
from app.services.cognito_service import CognitoServiceError


LOGOUT_HEADERS = {"Referer": "http://localhost/"}


@pytest.fixture
def cognito_env(monkeypatch):
    """Web + mobile share one provider stub with matching verified subs."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    sign_out_calls = []

    def fake_authenticate(username, password):
        return {
            "tokens": {
                "access_token": f"acc-{username}",
                "id_token": f"id-{username}",
                "refresh_token": f"ref-{username}",
                "expires_in": 3600,
            },
            "claims": {"sub": f"sub-{username}"},
        }

    def fake_validate(token, use, **kw):
        name = token
        for prefix in ("id-", "acc-", "ref-"):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return {
            "sub": f"sub-{name}",
            "email": f"{name}@example.com",
            "email_verified": True,
            "exp": calendar.timegm(
                (datetime.utcnow() + timedelta(hours=1)).timetuple()),
        }

    monkeypatch.setattr(cognito_service, "authenticate", fake_authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", fake_validate)
    monkeypatch.setattr(
        cognito_service, "global_sign_out",
        lambda tok: sign_out_calls.append(tok))
    monkeypatch.setattr(cognito_service, "revoke_token", lambda token: None)
    return sign_out_calls


def _web_login(client, username, password="x"):
    response = client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response


def _mobile_login(username):
    return mobile_auth.login(username, "x", now=datetime.utcnow().replace(microsecond=0))


def _bearer(issued):
    return {"Authorization": f"Bearer {issued.access_credential}"}


def _logout(client, extra_headers=None):
    headers = dict(LOGOUT_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return client.get("/logout", headers=headers)


def test_web_logout_revokes_the_same_user_mobile_session(
        client, make_user, cognito_env):
    """Baseline F5: a live mobile family cannot authenticate after web logout."""
    make_user("alice")
    _web_login(client, "alice")
    issued = _mobile_login("alice")
    assert client.get("/api/v1/account/me", headers=_bearer(issued)).status_code == 200
    assert MobileAuthSession.query.filter_by(
        revoked_at=None).count() == 1

    response = _logout(client)

    assert response.status_code in (301, 302)
    assert "/login" in response.headers["Location"]
    assert client.get("/supplements").status_code == 302
    assert CognitoSession.query.count() == 0
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.revoked_reason == mobile_auth.GLOBAL_LOGOUT_REASON
    me = client.get("/api/v1/account/me", headers=_bearer(issued))
    assert me.status_code == 401
    assert me.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_web_logout_revokes_every_preexisting_mobile_family_for_that_user(
        client, make_user, cognito_env):
    make_user("alice")
    _web_login(client, "alice")
    first = _mobile_login("alice")
    second = _mobile_login("alice")
    assert first.access_credential != second.access_credential

    assert _logout(client).status_code in (301, 302)

    families = MobileAuthSession.query.all()
    assert len(families) == 2
    assert all(family.revoked_at is not None for family in families)
    assert all(
        family.revoked_reason == mobile_auth.GLOBAL_LOGOUT_REASON
        for family in families)
    assert client.get(
        "/api/v1/account/me", headers=_bearer(first)).status_code == 401
    assert client.get(
        "/api/v1/account/me", headers=_bearer(second)).status_code == 401
    refresh = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_credential": first.refresh_credential})
    assert refresh.status_code == 401
    assert refresh.json["error"]["code"] == "AUTH_REFRESH_FAILED"


def _plant_web_session(user, sid):
    db.session.add(CognitoSession(
        session_id=sid,
        user_id=user.id,
        cognito_username=user.username,
        access_token=session_store.encrypt_token(f"acc-{user.username}"),
        refresh_token=session_store.encrypt_token(f"ref-{user.username}"),
        access_token_exp=datetime.utcnow() + timedelta(hours=1),
    ))
    db.session.commit()


def test_web_logout_does_not_revoke_another_users_sessions(
        client, make_user, cognito_env):
    alice = make_user("alice")
    bob = make_user("bob")
    _web_login(client, "alice")
    alice_mobile = _mobile_login("alice")
    bob_mobile = _mobile_login("bob")
    _plant_web_session(bob, "bob-web")

    assert _logout(client).status_code in (301, 302)

    assert CognitoSession.query.filter_by(user_id=alice.id).count() == 0
    assert CognitoSession.query.filter_by(user_id=bob.id).count() == 1
    assert MobileAuthSession.query.filter_by(
        user_id=alice.id).one().revoked_at is not None
    assert MobileAuthSession.query.filter_by(
        user_id=bob.id).one().revoked_at is None
    assert client.get("/supplements").status_code == 302
    assert client.get(
        "/api/v1/account/me", headers=_bearer(alice_mobile)).status_code == 401
    assert client.get(
        "/api/v1/account/me", headers=_bearer(bob_mobile)).status_code == 200


def test_web_logout_ignores_client_supplied_user_identity(
        client, make_user, cognito_env):
    make_user("alice")
    bob = make_user("bob")
    _web_login(client, "alice")
    bob_mobile = _mobile_login("bob")
    _plant_web_session(bob, "bob-web")

    response = client.get(
        f"/logout?user_id={bob.id}",
        headers={**LOGOUT_HEADERS, "X-User-Id": str(bob.id)},
        json={"user_id": bob.id, "username": "bob"})
    assert response.status_code in (301, 302)

    assert CognitoSession.query.filter_by(user_id=bob.id).count() == 1
    assert MobileAuthSession.query.filter_by(
        user_id=bob.id).one().revoked_at is None
    assert client.get(
        "/api/v1/account/me", headers=_bearer(bob_mobile)).status_code == 200
    assert CognitoSession.query.filter_by(user_id=bob.id).one().session_id == (
        "bob-web")


def test_web_logout_revokes_other_browser_cognito_sessions(
        app, make_user, cognito_env):
    make_user("alice")
    c1, c2 = app.test_client(), app.test_client()
    _web_login(c1, "alice")
    _web_login(c2, "alice")
    assert CognitoSession.query.count() == 2

    assert _logout(c1).status_code in (301, 302)

    assert CognitoSession.query.count() == 0
    assert c2.get("/supplements").status_code == 302


def test_global_sign_out_failure_still_revokes_local_sessions(
        client, make_user, cognito_env, monkeypatch, caplog):
    make_user("alice")
    _web_login(client, "alice")
    issued = _mobile_login("alice")
    monkeypatch.setattr(
        cognito_service, "global_sign_out",
        lambda tok: (_ for _ in ()).throw(
            CognitoServiceError("provider down", "ServiceUnavailableException")))

    response = _logout(client)

    assert response.status_code in (301, 302)
    assert client.get("/supplements").status_code == 302
    assert CognitoSession.query.count() == 0
    assert MobileAuthSession.query.one().revoked_at is not None
    assert client.get(
        "/api/v1/account/me", headers=_bearer(issued)).status_code == 401
    assert "GlobalSignOut failed after local revoke" in caplog.text
    assert issued.access_credential not in caplog.text
    assert issued.refresh_credential not in caplog.text
    assert "acc-alice" not in caplog.text
    assert "ref-alice" not in caplog.text


def test_local_mobile_revoke_failure_does_not_claim_logout_succeeded(
        client, make_user, cognito_env, monkeypatch):
    make_user("alice")
    _web_login(client, "alice")
    issued = _mobile_login("alice")
    monkeypatch.setattr(
        mobile_auth, "revoke_all_for_global_logout",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            mobile_auth.MobileAuthFailure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "storage_unavailable")))

    response = _logout(client)

    assert response.status_code == 503
    assert client.get("/supplements").status_code == 200
    assert CognitoSession.query.count() == 1
    assert MobileAuthSession.query.one().revoked_at is None
    assert client.get(
        "/api/v1/account/me", headers=_bearer(issued)).status_code == 200


def test_web_session_delete_failure_does_not_redirect_as_success(
        client, make_user, cognito_env, monkeypatch):
    make_user("alice")
    _web_login(client, "alice")
    issued = _mobile_login("alice")
    monkeypatch.setattr(
        session_store, "delete_for_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("database unavailable")))

    response = _logout(client)

    assert response.status_code == 503
    assert response.status_code not in (301, 302)
    # Mobile sweep committed before the web-row delete; the browser cookie
    # is still present because the route did not claim completion.
    assert client.get("/supplements").status_code == 200
    assert CognitoSession.query.count() == 1
    assert MobileAuthSession.query.one().revoked_at is not None
    assert client.get(
        "/api/v1/account/me", headers=_bearer(issued)).status_code == 401


def test_repeated_web_logout_is_safe(client, make_user, cognito_env):
    make_user("alice")
    _web_login(client, "alice")
    issued = _mobile_login("alice")

    first = _logout(client)
    second = _logout(client)

    assert first.status_code in (301, 302)
    assert second.status_code in (301, 302)
    assert "/login" in second.headers["Location"]
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.revoked_reason == mobile_auth.GLOBAL_LOGOUT_REASON
    assert client.get(
        "/api/v1/account/me", headers=_bearer(issued)).status_code == 401


def test_new_web_and_mobile_login_after_logout_succeed(
        client, make_user, cognito_env):
    user = make_user("alice")
    _web_login(client, "alice")
    old = _mobile_login("alice")
    assert _logout(client).status_code in (301, 302)
    assert user.credential_epoch == 0

    _web_login(client, "alice")
    assert client.get("/supplements").status_code == 200
    fresh = _mobile_login("alice")
    assert client.get(
        "/api/v1/account/me", headers=_bearer(fresh)).status_code == 200
    assert client.get(
        "/api/v1/account/me", headers=_bearer(old)).status_code == 401
    db.session.refresh(user)
    assert user.credential_epoch == 0


def test_web_logout_still_calls_global_sign_out_after_local_revoke(
        client, make_user, cognito_env, monkeypatch):
    make_user("alice")
    _web_login(client, "alice")
    _mobile_login("alice")
    observed = {}

    def sign_out(token):
        observed["token"] = token
        observed["mobile_revoked"] = MobileAuthSession.query.one().revoked_at
        observed["web_rows"] = CognitoSession.query.count()

    monkeypatch.setattr(cognito_service, "global_sign_out", sign_out)
    assert _logout(client).status_code in (301, 302)
    assert observed["token"] == "acc-alice"
    assert observed["mobile_revoked"] is not None
    assert observed["web_rows"] == 0


def test_csrf_rejected_logout_does_not_revoke_sessions(
        client, make_user, cognito_env):
    make_user("alice")
    _web_login(client, "alice")
    issued = _mobile_login("alice")

    response = client.get("/logout", headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403
    assert client.get("/supplements").status_code == 200
    assert CognitoSession.query.count() == 1
    assert MobileAuthSession.query.one().revoked_at is None
    assert client.get(
        "/api/v1/account/me", headers=_bearer(issued)).status_code == 200


def test_mobile_logout_remains_device_scoped(client, make_user, cognito_env):
    make_user("alice")
    first = _mobile_login("alice")
    second = _mobile_login("alice")

    response = client.post(
        "/api/v1/auth/logout",
        headers=_bearer(first),
        json={"refresh_credential": first.refresh_credential})

    assert response.status_code == 204
    families = MobileAuthSession.query.order_by(MobileAuthSession.id).all()
    revoked = [family for family in families if family.revoked_at is not None]
    live = [family for family in families if family.revoked_at is None]
    assert len(revoked) == 1
    assert len(live) == 1
    assert revoked[0].revoked_reason == "logout"
    assert client.get(
        "/api/v1/account/me", headers=_bearer(first)).status_code == 401
    assert client.get(
        "/api/v1/account/me", headers=_bearer(second)).status_code == 200


def test_web_logout_does_not_touch_workout_session_rows(
        client, make_user, cognito_env):
    from app.models import UserSession

    user = make_user("alice")
    db.session.add(UserSession(user_id=user.id, name="plan"))
    db.session.commit()
    _web_login(client, "alice")
    _mobile_login("alice")

    assert _logout(client).status_code in (301, 302)

    assert UserSession.query.filter_by(user_id=user.id).count() == 1
    assert User.query.filter_by(id=user.id).one().username == "alice"
