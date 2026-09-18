"""F6: the default limiter is keyed by verified app identity, else client IP."""
import calendar
from datetime import datetime, timedelta

import pytest
from flask import g, has_request_context, request
from flask_limiter.util import get_remote_address
from flask_login import login_user

from app.config import DEFAULT_RATELIMIT
from app.extensions import default_limiter_key, limiter
from app.services import cognito_jwt, cognito_service, mobile_auth


SAME_IP = "203.0.113.10"
OTHER_IP = "198.51.100.20"
FAKE_JWT = "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiI5OTkiLCJ1c2VyX2lkIjoiMSJ9."


def _enable_limiter_on(app):
    """Re-bind hooks if a previous test left the singleton disabled.

    Flask-Limiter.init_app returns before registering before_request when
    ``limiter.enabled`` is False. The shared ``app`` fixture disables the
    limiter after the first create_app, so later apps would otherwise have
    no default-limit hook.
    """
    limiter.enabled = True
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)


@pytest.fixture
def tight_default_limiter(app):
    """Isolate default quota for NAT/hop proofs without changing production 600/hour."""
    _enable_limiter_on(app)
    limiter.reset()
    groups = limiter.limit_manager._default_limits
    original_providers = [group.limit_provider for group in groups]
    original_keys = [group.key_function for group in groups]
    original_key_func = limiter._key_func
    for group in groups:
        group.limit_provider = "2 per hour"
        group.key_function = default_limiter_key
    try:
        yield
    finally:
        limiter._key_func = original_key_func
        for group, provider, key_fn in zip(groups, original_providers, original_keys):
            group.limit_provider = provider
            group.key_function = key_fn
        limiter.enabled = False
        limiter.reset()


@pytest.fixture
def enabled_limiter(app):
    # Route-specific @limiter.limit wrappers do not need the default
    # before_request hook; only flip the singleton on.
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


def _env(ip):
    return {"REMOTE_ADDR": ip}


def _sign_in_web(client, username, ip):
    return client.post(
        "/login", json={"username": username, "password": "Sifre123"},
        environ_base=_env(ip))


def _install_web_login(monkeypatch):
    from app.blueprints import auth as auth_bp

    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)

    def authenticate(username, _password):
        return {
            "tokens": {
                "access_token": f"acc-{username}",
                "id_token": f"id-{username}",
                "refresh_token": f"ref-{username}",
                "expires_in": 3600,
            },
            "claims": {"sub": f"sub-{username}"},
        }

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(
        cognito_jwt,
        "validate_token",
        lambda token, use, **kw: {
            "sub": f"sub-{token.removeprefix('id-').removeprefix('acc-')}"
        },
    )


def _install_mobile_provider(monkeypatch):
    def authenticate(username, password):
        return {
            "tokens": {
                "access_token": f"provider-access-{username}",
                "id_token": f"provider-id-{username}",
                "refresh_token": f"provider-refresh-{username}",
                "expires_in": 3600,
            },
            "claims": {"sub": f"sub-{username}"},
        }

    def validate(token, expected_use, leeway_seconds=0):
        if token.startswith("provider-access-"):
            username = token.removeprefix("provider-access-")
        elif token.startswith("provider-id-"):
            username = token.removeprefix("provider-id-")
        else:
            username = token
        if expected_use == "id":
            return {
                "sub": f"sub-{username}",
                "email": f"{username}@example.com",
                "email_verified": True,
            }
        return {
            "sub": f"sub-{username}",
            "exp": calendar.timegm(
                (datetime.utcnow() + timedelta(hours=1)).timetuple()),
        }

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)


def _probe_keys():
    seen = []
    original = default_limiter_key

    def wrapped():
        key = original()
        seen.append({
            "key": key,
            "path": request.path,
            "blueprint": request.blueprint,
            "authenticated": (
                has_request_context()
                and getattr(getattr(g, "_login_user", None), "is_authenticated", False)
            ),
            "mobile_user_id": getattr(getattr(g, "mobile_user", None), "id", None),
        })
        return key

    _set_default_key(wrapped)
    return seen


def _set_default_key(func):
    limiter._key_func = func
    for group in limiter.limit_manager._default_limits:
        group.key_function = func


def _health(client, ip):
    return client.get("/health", environ_base=_env(ip))


def _mobile_me(client, credential, ip):
    return client.get(
        "/api/v1/account/me",
        headers={"Authorization": f"Bearer {credential}"},
        environ_base=_env(ip),
    )


# ---------------------------------------------------------------------------
# Key-function contract
# ---------------------------------------------------------------------------

def test_production_default_quota_is_unchanged():
    assert DEFAULT_RATELIMIT == "600 per hour"
    registered = [str(limit.limit) for limit in limiter.limit_manager.default_limits]
    assert registered == ["600 per 1 hour"]


def test_browser_authenticated_key_is_canonical_user_id(app, make_user):
    user = make_user("alice")
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        login_user(user)
        assert default_limiter_key() == f"user:{user.id}"


def test_same_browser_user_different_ips_share_user_key(app, make_user):
    user = make_user("alice")
    keys = []
    for ip in (SAME_IP, OTHER_IP):
        with app.test_request_context("/__limiter_key", environ_base=_env(ip)):
            login_user(user)
            keys.append(default_limiter_key())
    assert keys[0] == keys[1] == f"user:{user.id}"


def test_different_browser_users_same_ip_do_not_share_key(app, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        login_user(alice)
        alice_key = default_limiter_key()
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        login_user(bob)
        bob_key = default_limiter_key()
    assert alice_key == f"user:{alice.id}"
    assert bob_key == f"user:{bob.id}"
    assert alice_key != bob_key


def test_anonymous_same_ip_shares_ip_key(app):
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        first = default_limiter_key()
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        second = default_limiter_key()
    assert first == second == f"ip:{SAME_IP}"


def test_anonymous_different_ips_do_not_share_key(app):
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        first = default_limiter_key()
    with app.test_request_context("/__limiter_key", environ_base=_env(OTHER_IP)):
        second = default_limiter_key()
    assert first == f"ip:{SAME_IP}"
    assert second == f"ip:{OTHER_IP}"


def test_mobile_bound_principal_uses_user_key_not_ip(app, make_user):
    user = make_user("mobile-alice")
    with app.test_request_context(
            "/api/v1/account/me", environ_base=_env(SAME_IP)):
        assert request.blueprint == "mobile_api"
        g.mobile_user = user
        assert default_limiter_key() == f"user:{user.id}"


def test_mobile_ignores_flask_login_cookie_without_bearer_principal(app, make_user):
    user = make_user("cookie-alice")
    with app.test_request_context(
            "/api/v1/account/me", environ_base=_env(SAME_IP)):
        login_user(user)
        assert default_limiter_key() == f"ip:{SAME_IP}"


def test_unverified_bearer_and_client_ids_cannot_mint_user_key(app):
    with app.test_request_context(
            "/__limiter_key",
            query_string={"user_id": "1", "sub": "attacker"},
            headers={
                "Authorization": FAKE_JWT,
                "X-User-Id": "1",
                "X-Forwarded-For": "1.2.3.4",
            },
            environ_base=_env(SAME_IP),
            json={"user_id": 1, "username": "alice"},
    ):
        assert default_limiter_key() == f"ip:{SAME_IP}"


def test_ip_only_key_would_collapse_nat_users(app, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        login_user(alice)
        ip_key_alice = f"ip:{get_remote_address()}"
        user_key_alice = default_limiter_key()
    with app.test_request_context("/__limiter_key", environ_base=_env(SAME_IP)):
        login_user(bob)
        ip_key_bob = f"ip:{get_remote_address()}"
        user_key_bob = default_limiter_key()
    assert ip_key_alice == ip_key_bob
    assert user_key_alice != user_key_bob


def test_user_plus_ip_key_would_allow_ip_hopping(app, make_user):
    user = make_user("alice")
    mixed = []
    canonical = []
    for ip in (SAME_IP, OTHER_IP):
        with app.test_request_context("/__limiter_key", environ_base=_env(ip)):
            login_user(user)
            canonical.append(default_limiter_key())
            mixed.append(f"user:{user.id}:ip:{get_remote_address()}")
    assert canonical[0] == canonical[1]
    assert mixed[0] != mixed[1]


# ---------------------------------------------------------------------------
# Request lifecycle: identity is present when the default key runs
# ---------------------------------------------------------------------------

def test_browser_identity_is_available_when_default_limiter_evaluates(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_web_login(monkeypatch)
    user = make_user("alice")
    client = app.test_client()
    assert _sign_in_web(client, "alice", SAME_IP).status_code == 200
    seen = _probe_keys()
    resp = _health(client, SAME_IP)
    assert resp.status_code == 200
    health_keys = [row for row in seen if row["path"] == "/health"]
    assert health_keys
    assert health_keys[0]["key"] == f"user:{user.id}"
    assert health_keys[0]["authenticated"] is True


def test_mobile_identity_is_available_when_default_limiter_evaluates(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_mobile_provider(monkeypatch)
    user = make_user("mobile-alice", cognito_sub="sub-mobile-alice")
    issued = mobile_auth.login("mobile-alice", "correct")
    seen = _probe_keys()
    resp = _mobile_me(app.test_client(), issued.access_credential, SAME_IP)
    assert resp.status_code == 200
    me_keys = [row for row in seen if row["path"] == "/api/v1/account/me"]
    assert me_keys
    assert me_keys[0]["key"] == f"user:{user.id}"
    assert me_keys[0]["mobile_user_id"] == user.id
    assert me_keys[0]["blueprint"] == "mobile_api"


def test_mobile_authenticate_access_runs_once_per_request(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_mobile_provider(monkeypatch)
    make_user("mobile-alice", cognito_sub="sub-mobile-alice")
    issued = mobile_auth.login("mobile-alice", "correct")
    calls = {"n": 0}
    real = mobile_auth.authenticate_access

    def counted(raw_access, now=None):
        calls["n"] += 1
        return real(raw_access, now=now)

    monkeypatch.setattr(mobile_auth, "authenticate_access", counted)
    resp = _mobile_me(app.test_client(), issued.access_credential, SAME_IP)
    assert resp.status_code == 200
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Shared-NAT and IP-hopping proofs against the default limiter
# ---------------------------------------------------------------------------

def test_two_authenticated_users_same_ip_have_independent_default_buckets(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_web_login(monkeypatch)
    make_user("alice")
    make_user("bob")
    alice = app.test_client()
    bob = app.test_client()
    assert _sign_in_web(alice, "alice", SAME_IP).status_code == 200
    assert _sign_in_web(bob, "bob", SAME_IP).status_code == 200
    assert [_health(alice, SAME_IP).status_code for _ in range(2)] == [200, 200]
    assert _health(alice, SAME_IP).status_code == 429
    assert _health(bob, SAME_IP).status_code == 200


def test_ip_keyed_default_would_make_nat_neighbors_share_a_bucket(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_web_login(monkeypatch)
    make_user("alice")
    make_user("bob")
    alice = app.test_client()
    bob = app.test_client()
    assert _sign_in_web(alice, "alice", SAME_IP).status_code == 200
    assert _sign_in_web(bob, "bob", SAME_IP).status_code == 200
    limiter.reset()
    _set_default_key(lambda: f"ip:{get_remote_address()}")
    assert [_health(alice, SAME_IP).status_code for _ in range(2)] == [200, 200]
    assert _health(alice, SAME_IP).status_code == 429
    assert _health(bob, SAME_IP).status_code == 429


def test_same_authenticated_user_cannot_reset_quota_by_changing_ip(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_web_login(monkeypatch)
    make_user("alice")
    at_x = app.test_client()
    at_y = app.test_client()
    assert _sign_in_web(at_x, "alice", SAME_IP).status_code == 200
    assert [_health(at_x, SAME_IP).status_code for _ in range(2)] == [200, 200]
    assert _health(at_x, SAME_IP).status_code == 429
    assert _sign_in_web(at_y, "alice", OTHER_IP).status_code == 200
    assert _health(at_y, OTHER_IP).status_code == 429


def test_user_plus_ip_key_would_reset_quota_on_ip_hop(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_web_login(monkeypatch)
    user = make_user("alice")
    at_x = app.test_client()
    at_y = app.test_client()
    assert _sign_in_web(at_x, "alice", SAME_IP).status_code == 200
    limiter.reset()
    _set_default_key(lambda: f"user:{user.id}:ip:{get_remote_address()}")
    assert [_health(at_x, SAME_IP).status_code for _ in range(2)] == [200, 200]
    assert _health(at_x, SAME_IP).status_code == 429
    assert _sign_in_web(at_y, "alice", OTHER_IP).status_code == 200
    assert _health(at_y, OTHER_IP).status_code == 200


def test_anonymous_same_ip_shares_default_bucket(
        app, tight_default_limiter):
    client = app.test_client()
    assert [_health(client, SAME_IP).status_code for _ in range(2)] == [200, 200]
    assert _health(client, SAME_IP).status_code == 429


def test_anonymous_different_ips_have_separate_default_buckets(
        app, tight_default_limiter):
    client = app.test_client()
    assert [_health(client, SAME_IP).status_code for _ in range(2)] == [200, 200]
    assert _health(client, SAME_IP).status_code == 429
    assert _health(client, OTHER_IP).status_code == 200


def test_two_mobile_users_same_ip_have_independent_default_buckets(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_mobile_provider(monkeypatch)
    make_user("mobile-alice", cognito_sub="sub-mobile-alice")
    make_user("mobile-bob", cognito_sub="sub-mobile-bob")
    alice = mobile_auth.login("mobile-alice", "correct")
    bob = mobile_auth.login("mobile-bob", "correct")
    client = app.test_client()
    assert [_mobile_me(client, alice.access_credential, SAME_IP).status_code
            for _ in range(2)] == [200, 200]
    assert _mobile_me(client, alice.access_credential, SAME_IP).status_code == 429
    assert _mobile_me(client, bob.access_credential, SAME_IP).status_code == 200


def test_same_mobile_user_cannot_reset_quota_by_changing_ip(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_mobile_provider(monkeypatch)
    make_user("mobile-alice", cognito_sub="sub-mobile-alice")
    issued = mobile_auth.login("mobile-alice", "correct")
    client = app.test_client()
    assert [_mobile_me(client, issued.access_credential, SAME_IP).status_code
            for _ in range(2)] == [200, 200]
    assert _mobile_me(client, issued.access_credential, SAME_IP).status_code == 429
    assert _mobile_me(client, issued.access_credential, OTHER_IP).status_code == 429


# ---------------------------------------------------------------------------
# Invalid / unverified credentials stay on the anonymous policy
# ---------------------------------------------------------------------------

def test_invalid_mobile_bearer_uses_ip_key_not_a_user_key(
        app, monkeypatch, tight_default_limiter):
    seen = _probe_keys()
    resp = _mobile_me(app.test_client(), "not-a-valid-credential", SAME_IP)
    assert resp.status_code == 401
    me_keys = [row for row in seen if row["path"] == "/api/v1/account/me"]
    assert me_keys
    assert me_keys[0]["key"] == f"ip:{SAME_IP}"
    assert me_keys[0]["mobile_user_id"] is None
    assert not me_keys[0]["key"].startswith("user:")


def test_unverified_jwt_claim_does_not_create_a_user_bucket(
        app, monkeypatch, tight_default_limiter):
    seen = _probe_keys()
    resp = app.test_client().get(
        "/health",
        headers={"Authorization": FAKE_JWT},
        environ_base=_env(SAME_IP),
    )
    assert resp.status_code == 200
    health_keys = [row for row in seen if row["path"] == "/health"]
    assert health_keys[0]["key"] == f"ip:{SAME_IP}"


# ---------------------------------------------------------------------------
# Pre-auth surfaces stay IP-keyed
# ---------------------------------------------------------------------------

def test_login_and_reset_limits_pin_client_ip(app):
    decorated = limiter.limit_manager._decorated_limits
    ip_pinned = {
        "app.blueprints.auth.forgot_password.forgot_password",
        "app.blueprints.auth.reset_password.reset_password",
        "app.blueprints.auth.register.register",
        "app.blueprints.auth.login.login",
        "app.blueprints.auth.verify_confirm.verify_confirm",
        "app.blueprints.auth.verify_resend.verify_resend",
        "app.blueprints.mobile_api.login.login",
    }
    for name in ip_pinned:
        funcs = {group.key_function for group in decorated[name]}
        assert get_remote_address in funcs, name


def test_forgot_password_stays_ip_keyed_even_when_a_session_exists(
        app, make_user, monkeypatch, enabled_limiter):
    _install_web_login(monkeypatch)
    monkeypatch.setattr(cognito_service, "forgot_password", lambda identifier: None)
    make_user("alice")
    make_user("bob")
    alice = app.test_client()
    bob = app.test_client()
    assert _sign_in_web(alice, "alice", SAME_IP).status_code == 200
    assert _sign_in_web(bob, "bob", SAME_IP).status_code == 200
    payload = {"identifier": "anyone"}
    for _ in range(5):
        assert alice.post(
            "/forgot-password", json=payload, environ_base=_env(SAME_IP)
        ).status_code == 200
    blocked = alice.post(
        "/forgot-password", json=payload, environ_base=_env(SAME_IP))
    assert blocked.status_code == 429
    neighbor = bob.post(
        "/forgot-password", json=payload, environ_base=_env(SAME_IP))
    assert neighbor.status_code == 429
    other_net = app.test_client().post(
        "/forgot-password", json=payload, environ_base=_env(OTHER_IP))
    assert other_net.status_code == 200


def test_login_stays_ip_keyed_for_unauthenticated_abuse(
        app, monkeypatch, enabled_limiter):
    _install_web_login(monkeypatch)
    client = app.test_client()
    payload = {"username": "missing", "password": "wrong"}
    codes = [
        client.post("/login", json=payload, environ_base=_env(SAME_IP)).status_code
        for _ in range(11)
    ]
    assert 429 in codes
    assert client.post(
        "/login", json=payload, environ_base=_env(OTHER_IP)
    ).status_code != 429


def test_explicit_user_keyed_write_limit_is_unchanged(
        client, auth_user, enabled_limiter):
    path = "/water"
    payload = {"count": "invalid"}
    assert client.post(path, json=payload).status_code != 429
    assert client.post(path, json=payload).status_code != 429
    assert client.post(path, json=payload).status_code == 429


# ---------------------------------------------------------------------------
# 429 contract
# ---------------------------------------------------------------------------

def test_default_429_contract_is_unchanged_json_error(
        app, make_user, monkeypatch, tight_default_limiter):
    _install_web_login(monkeypatch)
    make_user("alice")
    client = app.test_client()
    assert _sign_in_web(client, "alice", SAME_IP).status_code == 200
    _health(client, SAME_IP)
    _health(client, SAME_IP)
    blocked = _health(client, SAME_IP)
    assert blocked.status_code == 429
    body = blocked.get_json()
    assert set(body) == {"error"}
    assert isinstance(body["error"], str)
    assert body["error"]
