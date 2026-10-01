"""LP-11 native account deletion — contract, isolation, lifecycle, failures.

    DELETE /api/v1/account

Real opaque mobile credentials, real database, real owner-checked S3 helpers
and the canonical `_purge_user`; only Cognito and the S3 client are faked
(tests/account_deletion_support.py). Every A/B test seeds BOTH accounts with a
row in (almost) every user-owned domain plus media and asserts on the database
and the fake object store directly — never on "a function was called".

    python -m pytest tests/test_mobile_account_deletion_api.py -v
"""
import json
import logging

import pytest
from sqlalchemy import event

from account_deletion_support import (
    census, install_fakes, issue_bearer, rows_exist, rows_gone, seed_account,
    seed_cross_links, session_rows, user_exists,
)
from app.blueprints import mobile_account_deletion
from app.extensions import db
from app.models import (
    FeedItem, Friendship, Message, Notification, PumpCheck, PumpCheckComment,
    PumpCheckLike, User,
)
from app.services import account_deletion, cognito_service, mobile_auth


PATH = "/api/v1/account"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}


@pytest.fixture
def world(app, make_user, monkeypatch):
    """Two fully populated accounts, each with a live mobile session."""
    cognito, s3 = install_fakes(monkeypatch)
    alice = make_user("alice_lp11")
    bob = make_user("bob_lp11")
    a_own, a_keys = seed_account(alice, s3, "A")
    b_own, b_keys = seed_account(bob, s3, "B")
    cross_repost_id = seed_cross_links(alice, bob)
    a_bearer = issue_bearer(cognito, alice)
    b_bearer = issue_bearer(cognito, bob)

    class World:
        pass
    w = World()
    w.cognito, w.s3 = cognito, s3
    w.a, w.b = alice, bob
    w.a_id, w.b_id = alice.id, bob.id
    w.a_own, w.b_own, w.a_keys, w.b_keys = a_own, b_own, a_keys, b_keys
    w.a_bearer, w.b_bearer = a_bearer, b_bearer
    w.cross_repost_id = cross_repost_id
    w.a_pump_ids = [p.id for p in PumpCheck.query.filter_by(user_id=alice.id)]
    return w


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    return body["error"]


def _me(raw_client, bearer):
    return raw_client.get("/api/v1/account/me", headers=bearer)


def _assert_a_intact(w):
    assert user_exists(w.a_id)
    assert rows_exist(w.a_own) == []
    assert session_rows(w.a_id)["families"] >= 1


def _assert_b_untouched(w, raw_client):
    """B's account, sessions, own rows and media survive A's deletion."""
    assert user_exists(w.b_id)
    assert rows_exist(w.b_own) == []
    assert set(w.b_keys.values()) <= w.s3.objects
    assert not set(w.b_keys.values()) & set(w.s3.deleted)
    assert f"sub-{w.b.username}" in w.cognito.live
    assert f"sub-{w.b.username}" not in w.cognito.deleted_subs
    assert _me(raw_client, w.b_bearer).status_code == 200
    bob = db.session.get(User, w.b_id)
    assert bob.full_name == "Private Name B"
    assert bob.profile_picture_key == w.b_keys["avatar"]


# -- Full lifecycle + A/B isolation (spec §18/§19) -----------------------------
def test_populated_account_deletion_purges_a_and_leaves_b(world, raw_client):
    w = world
    b_census_before = census(w.b_id)

    response = raw_client.delete(PATH, headers=w.a_bearer)

    assert response.status_code == 204
    assert response.get_data() == b""
    assert response.headers["Cache-Control"] == "no-store"

    # Identity: exactly A's Cognito principal, exactly once.
    assert w.cognito.delete_calls == [f"access|sub-{w.a.username}"]
    assert f"sub-{w.a.username}" not in w.cognito.live

    # Local: no row anywhere names A through any user foreign key.
    assert not user_exists(w.a_id)
    assert {k: v for k, v in census(w.a_id).items() if v} == {}
    assert rows_gone(w.a_own) == []
    assert session_rows(w.a_id) == {
        "families": 0, "access": 0, "refresh": 0, "web": 0}
    # Rows owned only THROUGH A's rows (FK-less or via a child) are gone too.
    assert PumpCheckLike.query.filter(
        PumpCheckLike.pump_check_id.in_(w.a_pump_ids)).count() == 0
    assert PumpCheckComment.query.filter(
        PumpCheckComment.pump_check_id.in_(w.a_pump_ids)).count() == 0
    assert db.session.get(FeedItem, w.cross_repost_id) is None
    assert Friendship.query.count() == 0
    assert Message.query.count() == 0
    assert Notification.query.filter_by(actor_id=w.a_id).count() == 0
    # The one ANONYMIZE edge: B's referral link to A is detached, B survives.
    assert db.session.get(User, w.b_id).referred_by_id is None

    # Storage: exactly A's objects, none of B's.
    assert sorted(w.s3.deleted) == sorted(w.a_keys.values())
    assert not set(w.a_keys.values()) & w.s3.objects

    # B: only the edges that touched A changed.
    b_census_after = census(w.b_id)
    changed = {k for k in b_census_before
               if b_census_before[k] != b_census_after[k]}
    assert changed == {
        ("Friendship", "receiver_id"), ("Message", "receiver_id"),
        ("PumpCheckLike", "user_id"), ("PumpCheckComment", "user_id"),
        ("FeedItem", "user_id"), ("Notification", "user_id"),
    }
    _assert_b_untouched(w, raw_client)


def test_deleted_session_is_dead_and_identity_cannot_resurrect(
        world, raw_client):
    w = world
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204

    me = _me(raw_client, w.a_bearer)
    assert me.status_code == 401
    assert _error(me)["code"] == "AUTH_SESSION_EXPIRED"
    again = raw_client.delete(PATH, headers=w.a_bearer)
    assert again.status_code == 401

    # A sign-in with the deleted identity cannot recreate the local account.
    with pytest.raises(mobile_auth.MobileAuthFailure) as failure:
        mobile_auth.login(w.a.username, "Sifre123")
    assert failure.value.status == 401
    assert User.query.filter_by(cognito_sub=f"sub-{w.a.username}").count() == 0
    assert User.query.filter_by(username=w.a.username).count() == 0
    _assert_b_untouched(w, raw_client)


def test_reverse_direction_b_deletion_leaves_a(world, raw_client):
    w = world
    assert raw_client.delete(PATH, headers=w.b_bearer).status_code == 204
    assert w.cognito.delete_calls == [f"access|sub-{w.b.username}"]
    assert not user_exists(w.b_id)
    assert {k: v for k, v in census(w.b_id).items() if v} == {}
    assert user_exists(w.a_id)
    assert rows_exist(w.a_own) == []
    assert set(w.a_keys.values()) <= w.s3.objects
    assert sorted(w.s3.deleted) == sorted(w.b_keys.values())
    assert _me(raw_client, w.a_bearer).status_code == 200


# -- Ownership comes only from the Bearer principal (spec §4/§13/§24) ----------
@pytest.mark.parametrize("kwargs", [
    {"query_string": {"user_id": "B"}},
    {"query_string": {"sub": "sub-bob_lp11"}},
    {"json": {"user_id": "B"}},
    {"json": {"cognito_sub": "sub-bob_lp11", "keys": ["avatars/2/x.jpg"]}},
    {"data": "user_id=2", "content_type": "application/x-www-form-urlencoded"},
])
def test_request_carrying_any_authority_is_refused(world, raw_client, kwargs):
    w = world
    for field in ("query_string", "json"):
        if field in kwargs and "user_id" in kwargs[field]:
            kwargs[field]["user_id"] = w.b_id
    response = raw_client.delete(PATH, headers=w.a_bearer, **kwargs)

    assert response.status_code == 400
    assert _error(response)["code"] == "ACCOUNT_DELETION_INVALID_REQUEST"
    assert response.headers["Cache-Control"] == "no-store"
    assert w.cognito.delete_calls == []
    assert w.s3.deleted == []
    _assert_a_intact(w)
    _assert_b_untouched(w, raw_client)


def test_headers_cannot_redirect_deletion(world, raw_client):
    w = world
    headers = dict(w.a_bearer)
    headers.update({"X-User-Id": str(w.b_id), "X-Cognito-Sub": "sub-bob_lp11"})
    assert raw_client.delete(PATH, headers=headers).status_code == 204
    assert w.cognito.delete_calls == [f"access|sub-{w.a.username}"]
    assert not user_exists(w.a_id)
    _assert_b_untouched(w, raw_client)


@pytest.mark.parametrize("headers", [
    None,
    {"Authorization": "Bearer not-a-real-credential"},
    {"Authorization": "Basic abc"},
])
def test_unauthenticated_request_is_rejected(world, raw_client, headers):
    w = world
    response = raw_client.delete(PATH, headers=headers)
    assert response.status_code == 401
    assert _error(response)["code"].startswith("AUTH_")
    assert response.headers["Cache-Control"] == "no-store"
    assert w.cognito.delete_calls == []
    _assert_a_intact(w)
    _assert_b_untouched(w, raw_client)


def test_browser_session_cannot_reach_mobile_deletion(
        world, client, login):
    w = world
    assert login(w.a.username).status_code in (200, 302)
    assert client.get("/api/wearables/status").status_code == 200
    response = client.delete(PATH)
    assert response.status_code == 401
    assert w.cognito.delete_calls == []
    assert user_exists(w.a_id)


@pytest.mark.parametrize("method", ["get", "post", "put", "patch"])
def test_unsupported_methods_are_not_deletion(world, raw_client, method):
    w = world
    response = getattr(raw_client, method)(PATH, headers=w.a_bearer)
    # 405, or 403 where the app-wide CSRF gate answers a write that no
    # mobile route matched first (pre-existing, not changed here).
    assert response.status_code in (403, 405)
    assert w.cognito.delete_calls == []
    _assert_a_intact(w)


@pytest.mark.parametrize("path", [
    "/api/v1/account/delete", "/api/v1/accounts", "/api/v1/account/2",
    "/api/v1/account/me/delete", "/api/v1/users/2",
])
def test_sibling_paths_are_unavailable(world, raw_client, path):
    w = world
    response = raw_client.delete(path, headers=w.a_bearer)
    # 403 where the app-wide CSRF gate answers an unmatched write first.
    assert response.status_code in (403, 404, 405)
    assert w.cognito.delete_calls == []
    _assert_a_intact(w)


def test_route_is_behind_mobile_auth_and_on_the_mobile_blueprint(app):
    view = app.view_functions["mobile_api.delete_account"]
    assert getattr(view, "_require_mobile_auth", False) is True
    rules = [rule for rule in app.url_map.iter_rules()
             if rule.rule == PATH]
    assert [(r.endpoint, sorted(r.methods - {"HEAD", "OPTIONS"}))
            for r in rules] == [("mobile_api.delete_account", ["DELETE"])]
    assert mobile_account_deletion.delete_account is not None


# -- Failure injection (spec §8/§20) -------------------------------------------
@pytest.mark.parametrize("code", [
    "InternalErrorException", "TooManyRequestsException", "",
    "ForbiddenException", "PasswordResetRequiredException",
])
def test_cognito_failure_deletes_nothing(world, raw_client, code):
    w = world
    w.cognito.delete_failure = cognito_service.CognitoServiceError(
        "Sunucu hatası provider-secret", code)
    census_a = census(w.a_id)

    response = raw_client.delete(PATH, headers=w.a_bearer)

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "ACCOUNT_DELETION_UNAVAILABLE"
    assert error["retryable"] is True
    assert response.headers["Cache-Control"] == "no-store"
    assert census(w.a_id) == census_a
    assert w.s3.deleted == []
    assert f"sub-{w.a.username}" in w.cognito.live
    assert _me(raw_client, w.a_bearer).status_code == 200
    _assert_b_untouched(w, raw_client)


def test_provider_refusing_the_session_asks_for_sign_in(world, raw_client):
    w = world
    w.cognito.delete_failure = cognito_service.CognitoServiceError(
        "x", "NotAuthorizedException")
    census_a = census(w.a_id)
    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert census(w.a_id) == census_a
    assert w.s3.deleted == []
    _assert_b_untouched(w, raw_client)


def test_capacity_exhaustion_is_retryable_and_deletes_nothing(
        world, raw_client, monkeypatch):
    from app.services.ai_gate import BlockingConcurrencyLimit
    w = world

    class Full:
        def __enter__(self):
            raise BlockingConcurrencyLimit("full")

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(
        account_deletion, "blocking_concurrency_slot", lambda: Full())
    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 503
    assert _error(response)["code"] == "ACCOUNT_DELETION_UNAVAILABLE"
    assert response.headers["Retry-After"] == "15"
    assert w.cognito.delete_calls == []
    _assert_a_intact(w)


def test_owned_media_without_object_store_stops_before_identity(
        world, raw_client, monkeypatch):
    import s3_helper
    w = world
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", "")
    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 503
    assert _error(response)["code"] == "ACCOUNT_DELETION_UNAVAILABLE"
    assert w.cognito.delete_calls == []
    _assert_a_intact(w)


def test_local_purge_failure_is_incomplete_then_recovers_on_retry(
        world, raw_client, monkeypatch, caplog):
    import app.cli as cli
    w = world
    real_purge = cli._purge_user

    def broken(user):
        raise RuntimeError("SELECT secret FROM user_table")
    monkeypatch.setattr(cli, "_purge_user", broken)
    census_a = census(w.a_id)

    with caplog.at_level(logging.INFO):
        response = raw_client.delete(PATH, headers=w.a_bearer)

    assert response.status_code == 503
    error = _error(response)
    assert error["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    assert error["retryable"] is True
    assert "secret" not in response.get_data(as_text=True)
    # Identity is gone, local account + its sessions are intact (one txn).
    assert f"sub-{w.a.username}" not in w.cognito.live
    assert census(w.a_id) == census_a
    assert session_rows(w.a_id)["families"] >= 1
    assert _me(raw_client, w.a_bearer).status_code == 200
    # Operator-visible, PII-free: stage + type + internal id only.
    incomplete = [r.getMessage() for r in caplog.records
                  if "event=incomplete" in r.getMessage()]
    assert incomplete and "stage=local_purge" in incomplete[0]
    assert "error_type=RuntimeError" in incomplete[0]
    assert f"user_id={w.a_id}" in incomplete[0]
    _assert_b_untouched(w, raw_client)

    # Retry with the SAME session: provider says absent, lifecycle resumes.
    monkeypatch.setattr(cli, "_purge_user", real_purge)
    retry = raw_client.delete(PATH, headers=w.a_bearer)
    assert retry.status_code == 204
    assert w.cognito.delete_calls == [f"access|sub-{w.a.username}"] * 2
    assert not user_exists(w.a_id)
    assert {k: v for k, v in census(w.a_id).items() if v} == {}
    assert not set(w.a_keys.values()) & w.s3.objects
    _assert_b_untouched(w, raw_client)


def test_one_object_release_failure_stops_before_local_purge(
        world, raw_client):
    w = world
    w.s3.failing.add(w.a_keys["meal"])
    census_a = census(w.a_id)

    response = raw_client.delete(PATH, headers=w.a_bearer)

    assert response.status_code == 503
    assert _error(response)["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    body = response.get_data(as_text=True)
    assert w.a_keys["meal"] not in body and "provider-text" not in body
    # The rows that name the unreleased object still exist: nothing stranded.
    assert census(w.a_id) == census_a
    assert w.a_keys["meal"] in w.s3.objects
    _assert_b_untouched(w, raw_client)

    w.s3.failing.clear()
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    assert not set(w.a_keys.values()) & w.s3.objects
    assert not user_exists(w.a_id)
    _assert_b_untouched(w, raw_client)


def test_session_removal_failure_is_not_success_and_is_atomic(
        world, raw_client):
    """Sessions die in the purge transaction; if that fails, nothing dies."""
    w = world
    census_a = census(w.a_id)
    engine = db.engine

    def refuse_session_delete(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith(
                "DELETE FROM MOBILE_AUTH_SESSION"):
            raise RuntimeError("session store down")
    event.listen(engine, "before_cursor_execute", refuse_session_delete)
    try:
        response = raw_client.delete(PATH, headers=w.a_bearer)
    finally:
        event.remove(engine, "before_cursor_execute", refuse_session_delete)

    assert response.status_code == 503
    assert _error(response)["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    assert census(w.a_id) == census_a
    assert session_rows(w.a_id)["families"] >= 1
    _assert_b_untouched(w, raw_client)


def test_already_missing_object_is_released_not_retargeted(world, raw_client):
    w = world
    w.s3.missing_error.add(w.a_keys["pump"])
    w.s3.objects.discard(w.a_keys["pump"])
    assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204
    assert not user_exists(w.a_id)
    _assert_b_untouched(w, raw_client)


def test_foreign_or_unmanaged_references_are_never_deleted(
        world, raw_client, caplog):
    """A row of A naming B's object (or a URL) is not A's object."""
    w = world
    alice = db.session.get(User, w.a_id)
    alice.profile_picture_key = w.b_keys["avatar"]
    PumpCheck.query.filter_by(user_id=w.a_id).first().image_key = (
        "https://evil.example/avatars/1/x.jpg")
    db.session.commit()

    with caplog.at_level(logging.WARNING):
        assert raw_client.delete(PATH, headers=w.a_bearer).status_code == 204

    assert w.b_keys["avatar"] in w.s3.objects
    assert w.b_keys["avatar"] not in w.s3.deleted
    assert all(key.split("/")[1] == str(w.a_id) for key in w.s3.deleted)
    assert any("event=unmanaged_references_skipped count=2" in r.getMessage()
               for r in caplog.records)
    _assert_b_untouched(w, raw_client)


def test_duplicate_lifecycle_invocation_converges(world, raw_client):
    """A second run for an already-deleted account is a safe no-op."""
    w = world
    from types import SimpleNamespace
    from app.models import MobileAuthSession
    alice = db.session.get(User, w.a_id)
    family = MobileAuthSession.query.filter_by(user_id=w.a_id).first()
    claims = {"sub": alice.cognito_sub}
    # What a re-invocation knows: the principal's VALUES, not live rows.
    snapshot = (
        SimpleNamespace(id=alice.id, cognito_sub=alice.cognito_sub),
        SimpleNamespace(user_id=family.user_id,
                        cognito_sub=family.cognito_sub,
                        cognito_access_token=family.cognito_access_token),
        claims)

    account_deletion.delete_account(alice, family, claims)
    assert not user_exists(w.a_id)
    deleted_once = list(w.s3.deleted)

    account_deletion.delete_account(*snapshot)
    # The purged ORM objects themselves are a dead session, not a fault.
    with pytest.raises(account_deletion.ProviderSessionRejected):
        account_deletion.delete_account(alice, family, claims)
    assert w.cognito.delete_calls == [f"access|sub-{w.a.username}"] * 2
    assert sorted(w.s3.deleted) == sorted(deleted_once)
    assert {k: v for k, v in census(w.a_id).items() if v} == {}
    _assert_b_untouched(w, raw_client)


def test_incoherent_principal_is_refused_before_any_side_effect(world):
    w = world
    from app.models import MobileAuthSession
    alice = db.session.get(User, w.a_id)
    b_family = MobileAuthSession.query.filter_by(user_id=w.b_id).first()
    a_family = MobileAuthSession.query.filter_by(user_id=w.a_id).first()
    for family, claims in (
            (b_family, {"sub": alice.cognito_sub}),       # B's session
            (a_family, {"sub": f"sub-{w.b.username}"}),   # B's claims
            (None, {"sub": alice.cognito_sub})):
        with pytest.raises(account_deletion.ProviderSessionRejected):
            account_deletion.delete_account(alice, family, claims)
    assert w.cognito.delete_calls == []
    assert user_exists(w.a_id) and user_exists(w.b_id)


# -- Privacy (spec §16) ---------------------------------------------------------
def test_logs_and_bodies_carry_no_private_material(
        world, raw_client, caplog):
    w = world
    with caplog.at_level(logging.DEBUG):
        response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.status_code == 204
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (*w.a_keys.values(), "access|", "refresh|",
                   w.a.email, "Private Name A", "private food A",
                   w.a_bearer["Authorization"].split()[1]):
        assert secret not in text
    assert "account_deletion event=completed" in text


def test_error_bodies_are_json_and_leak_nothing(world, raw_client):
    w = world
    w.cognito.delete_failure = cognito_service.CognitoServiceError(
        "UserPool eu-central-1_X provider-secret", "InternalErrorException")
    response = raw_client.delete(PATH, headers=w.a_bearer)
    assert response.is_json
    raw = json.dumps(response.get_json())
    for secret in ("provider-secret", "eu-central-1", w.a.username,
                   f"sub-{w.a.username}", "Exception"):
        assert secret not in raw
    error = _error(response)
    assert error["message"] == "Account deletion is temporarily unavailable."
    assert str(w.a_id) not in error["message"] + error["code"]


def test_deletion_succeeds_with_the_real_limiter_hooks_armed(
        world, raw_client, app):
    """Production keys limits on `g.mobile_user.id` and its after-request hook
    runs once the user row is gone; that must not turn a 204 into a failure."""
    from app.extensions import limiter
    limiter.enabled = True
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)
    limiter.reset()
    try:
        response = raw_client.delete(PATH, headers=world.a_bearer)
    finally:
        limiter.enabled = False
        limiter.reset()
    assert response.status_code == 204
    assert not user_exists(world.a_id)


def test_leaderboard_entry_is_dropped(world, raw_client, monkeypatch):
    from app.services import gamification

    class FakeRedis:
        def __init__(self):
            self.sets = {gamification.LB_ALLTIME_KEY: {"1", "2"},
                         gamification.LB_WEEKLY_KEY: {"1", "2"}}

        def zrem(self, key, member):
            self.sets[key].discard(member)
    fake = FakeRedis()
    for key in fake.sets:
        fake.sets[key] = {str(world.a_id), str(world.b_id)}
    monkeypatch.setattr(gamification, "redis_client", fake)
    assert raw_client.delete(PATH, headers=world.a_bearer).status_code == 204
    for members in fake.sets.values():
        assert members == {str(world.b_id)}
