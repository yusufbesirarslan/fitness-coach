"""LP-11 anti-resurrection: a deleted identity never gets a local account back.

    DELETE /api/v1/account  ->  tombstone + purge in ONE transaction
    web POST /login, mobile login  ->  refuse a tombstoned subject

The race (docs/MOBILE_ACCOUNT_DELETION.md §6): a login authenticates at the
provider, THEN the account is deleted (204), THEN the login resumes and
resolves its local user. Every race here is reproduced deterministically by
running the real DELETE from inside the provider call of a real login — the
exact point after "the provider accepted the password" and before "the login
looks for its local row". Only Cognito and S3 are faked
(tests/account_deletion_support.py). The PostgreSQL interleavings the
serialization argument depends on live in tests/test_account_deletion_pg.py.

    python -m pytest tests/test_account_deletion_resurrection.py -v
"""
import hashlib
import logging

import pytest

from account_deletion_support import (
    install_fakes, issue_bearer, session_rows, user_exists,
)
from app.blueprints import auth as auth_bp
from app.extensions import db
from app.models import (
    CognitoSession, DeletedIdentityTombstone, MobileAuthSession, User,
)
from app.services import (
    account_deletion, cognito_service, deleted_identity, mobile_auth,
)


PATH = "/api/v1/account"
PASSWORD = "Sifre123"
TRANSPORTS = ("mobile", "web")


@pytest.fixture
def pair(app, make_user, monkeypatch):
    """Two accounts, each with a live native session; web login enabled."""
    cognito, s3 = install_fakes(monkeypatch)
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    alice = make_user("alice_lp11")
    bob = make_user("bob_lp11")

    class Pair:
        pass
    p = Pair()
    p.cognito, p.s3 = cognito, s3
    p.a_id, p.b_id = alice.id, bob.id
    p.a_sub, p.b_sub = alice.cognito_sub, bob.cognito_sub
    p.a_bearer = issue_bearer(cognito, alice)
    p.b_bearer = issue_bearer(cognito, bob)
    return p


def _sign_in(transport, client, identifier):
    """Run one real login. Returns ("issued", None) or ("refused", reason)."""
    if transport == "mobile":
        try:
            mobile_auth.login(identifier, PASSWORD)
        except mobile_auth.MobileAuthFailure as failure:
            assert failure.status == 401
            assert failure.code == "AUTH_INVALID_CREDENTIALS"
            return "refused", failure.reason
        return "issued", None
    response = client.post(
        "/login", json={"username": identifier, "password": PASSWORD})
    if response.status_code == 200:
        with client.session_transaction() as session:
            assert session.get("cognito_sid")
        return "issued", None
    assert response.status_code == 401
    with client.session_transaction() as session:
        assert "cognito_sid" not in session and "_user_id" not in session
    return "refused", None


def _delete_during_provider_call(monkeypatch, raw_client, bearer):
    """The next login's provider call succeeds, then the account is deleted
    before the login gets back to the database."""
    real_authenticate = cognito_service.authenticate
    seen = {}

    def authenticate(username, password):
        result = real_authenticate(username, password)
        seen["delete"] = raw_client.delete(PATH, headers=bearer).status_code
        return result
    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    return seen


def _no_local_trace(sub, *names):
    """No user row, and no native or web session, for `sub`."""
    assert User.query.filter_by(cognito_sub=sub).count() == 0
    for name in names:
        assert User.query.filter(
            db.func.lower(User.username) == name.lower()).count() == 0
    assert MobileAuthSession.query.filter_by(cognito_sub=sub).count() == 0
    live_users = {user_id for (user_id,) in db.session.query(User.id)}
    assert CognitoSession.query.filter(
        CognitoSession.user_id.notin_(live_users)).count() == 0


def _tombstones():
    return DeletedIdentityTombstone.query.all()


def _b_still_works(p, raw_client):
    assert user_exists(p.b_id)
    assert raw_client.get(
        "/api/v1/account/me", headers=p.b_bearer).status_code == 200
    assert session_rows(p.b_id)["families"] >= 1


# -- 1/2/7: the in-flight login race, both transports --------------------------
@pytest.mark.parametrize("transport", TRANSPORTS)
def test_login_in_flight_across_deletion_cannot_recreate_account_or_session(
        pair, client, raw_client, monkeypatch, transport):
    p = pair
    seen = _delete_during_provider_call(monkeypatch, raw_client, p.a_bearer)

    outcome, reason = _sign_in(transport, client, "alice_lp11")

    assert seen["delete"] == 204           # the deletion really succeeded ...
    assert outcome == "refused"            # ... and the login did not undo it
    if transport == "mobile":
        assert reason == "identity_deleted"
    assert not user_exists(p.a_id)
    _no_local_trace(p.a_sub, "alice_lp11")
    assert [t.fingerprint for t in _tombstones()] == [
        deleted_identity.fingerprint(p.a_sub)]
    _b_still_works(p, raw_client)


# -- 3: legacy row whose local username/e-mail differ from the provider's -----
@pytest.mark.parametrize("transport", TRANSPORTS)
def test_renamed_legacy_account_is_blocked_by_identity_not_by_strings(
        app, make_user, client, raw_client, monkeypatch, transport):
    """The local row was renamed before usernames became immutable: nothing
    the user types names it, so the credential fence reads no row at all.
    Only the verified subject connects the login to the deleted account."""
    cognito, _s3 = install_fakes(monkeypatch)
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    legacy = make_user("legacy_local", email="legacy.local@example.com",
                       cognito_sub="sub-alice_cloud")
    cognito.live.add("sub-alice_cloud")
    issued = mobile_auth.login("alice_cloud", PASSWORD)     # provider username
    bearer = {"Authorization": f"Bearer {issued.access_credential}"}
    assert mobile_auth._credential_fence("alice_cloud") == {}
    legacy_id = legacy.id

    seen = _delete_during_provider_call(monkeypatch, raw_client, bearer)
    outcome, _reason = _sign_in(transport, client, "alice_cloud")

    assert seen["delete"] == 204
    assert outcome == "refused"
    assert not user_exists(legacy_id)
    _no_local_trace("sub-alice_cloud", "alice_cloud", "legacy_local")
    assert User.query.filter_by(email="alice_cloud@example.com").count() == 0


# -- 4: a NEW provider identity for the same person is not blocked -------------
@pytest.mark.parametrize("transport", TRANSPORTS)
def test_new_provider_identity_with_same_username_and_email_is_not_blocked(
        pair, client, raw_client, transport):
    p = pair
    assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204

    # The same human signs up again: same username, same e-mail, NEW subject.
    p.cognito.rebind("alice_lp11", "sub2-alice_lp11")
    outcome, _reason = _sign_in(transport, client, "alice_lp11")

    assert outcome == "issued"
    fresh = User.query.filter_by(cognito_sub="sub2-alice_lp11").one()
    assert fresh.username == "alice_lp11"
    assert fresh.email == "alice_lp11@example.com"
    assert fresh.id != p.a_id
    # The old identity's tombstone is still there and still only the old one.
    assert [t.fingerprint for t in _tombstones()] == [
        deleted_identity.fingerprint(p.a_sub)]
    assert (deleted_identity.fingerprint("sub2-alice_lp11")
            != deleted_identity.fingerprint(p.a_sub))


# -- 5: the tombstone and the purge commit together or not at all -------------
def test_tombstone_failure_is_incomplete_and_leaves_account_intact(
        pair, raw_client, monkeypatch):
    p = pair
    real_record = deleted_identity.record
    failing = [True]

    def record(sub):
        if failing[0]:
            raise RuntimeError("tombstone store down")
        return real_record(sub)
    monkeypatch.setattr(deleted_identity, "record", record)

    response = raw_client.delete(PATH, headers=p.a_bearer)

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    assert p.a_sub not in p.cognito.live       # the identity step did run
    assert user_exists(p.a_id)                 # no state claims completion
    assert session_rows(p.a_id)["families"] >= 1
    assert _tombstones() == []

    # Retry: the provider says the identity is already gone — that is NOT a
    # licence to skip the tombstone.
    failing[0] = False
    assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204
    assert p.cognito.deleted_subs == [p.a_sub, p.a_sub]
    assert not user_exists(p.a_id)
    assert [t.fingerprint for t in _tombstones()] == [
        deleted_identity.fingerprint(p.a_sub)]


def test_purge_failure_after_tombstone_rolls_the_tombstone_back(
        pair, raw_client, monkeypatch):
    import app.cli as cli
    p = pair
    real_purge = cli._purge_user

    def purge_then_fail(user):
        real_purge(user)
        raise RuntimeError("commit would have failed")
    monkeypatch.setattr(cli, "_purge_user", purge_then_fail)

    response = raw_client.delete(PATH, headers=p.a_bearer)

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ACCOUNT_DELETION_INCOMPLETE"
    assert user_exists(p.a_id)
    assert _tombstones() == []

    monkeypatch.setattr(cli, "_purge_user", real_purge)
    assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204
    assert not user_exists(p.a_id)
    assert len(_tombstones()) == 1


def test_every_successful_deletion_leaves_its_tombstone(pair, raw_client):
    p = pair
    assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204
    assert not user_exists(p.a_id)
    assert [t.fingerprint for t in _tombstones()] == [
        deleted_identity.fingerprint(p.a_sub)]


def test_row_already_purged_elsewhere_still_gets_its_tombstone(
        pair, monkeypatch):
    """The owner row vanished between preflight and the purge (another purge
    won). The 204 this returns must still leave the subject tombstoned."""
    from app.cli import _purge_user
    p = pair
    user = db.session.get(User, p.a_id)
    family = MobileAuthSession.query.filter_by(user_id=p.a_id).first()
    claims = {"sub": p.a_sub}
    real_delete_identity = account_deletion._delete_identity

    def delete_identity_then_lose_the_row(provider_access):
        real_delete_identity(provider_access)
        _purge_user(db.session.get(User, p.a_id))
        db.session.commit()
    monkeypatch.setattr(
        account_deletion, "_delete_identity", delete_identity_then_lose_the_row)

    account_deletion.delete_account(user, family, claims)

    assert not user_exists(p.a_id)
    assert [t.fingerprint for t in _tombstones()] == [
        deleted_identity.fingerprint(p.a_sub)]


# -- 6: A's tombstone never touches B ------------------------------------------
@pytest.mark.parametrize("transport", TRANSPORTS)
def test_a_tombstone_never_blocks_or_alters_b(
        pair, client, raw_client, transport):
    p = pair
    assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204

    outcome, _reason = _sign_in(transport, client, "bob_lp11")

    assert outcome == "issued"
    assert User.query.filter_by(cognito_sub=p.b_sub).one().id == p.b_id
    bob = db.session.get(User, p.b_id)
    assert (bob.username, bob.email) == ("bob_lp11", "bob_lp11@example.com")
    _b_still_works(p, raw_client)
    assert len(_tombstones()) == 1


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_a_tombstone_never_blocks_a_different_identity_being_created(
        pair, client, raw_client, transport):
    """The guard only runs when a login writes a subject onto a row — so the
    A/B case that reaches it is B's FIRST login (a registration orphan: live
    at the provider, no local row yet) after A was deleted."""
    p = pair
    assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204
    p.cognito.live.add("sub-carol_lp11")

    outcome, _reason = _sign_in(transport, client, "carol_lp11")

    assert outcome == "issued"
    carol = User.query.filter_by(cognito_sub="sub-carol_lp11").one()
    assert carol.username == "carol_lp11"
    assert len(_tombstones()) == 1


# -- 8: the tombstone holds no raw identifier -----------------------------------
def test_tombstone_row_holds_no_raw_identifier(pair, raw_client, caplog):
    p = pair
    with caplog.at_level(logging.DEBUG):
        assert raw_client.delete(PATH, headers=p.a_bearer).status_code == 204

    columns = {c.name for c in DeletedIdentityTombstone.__table__.columns}
    assert columns == {"fingerprint", "deleted_at"}
    (row,) = _tombstones()
    stored = f"{row.fingerprint} {row.deleted_at}"
    # No subject, username or e-mail in any form; the column set above
    # already rules out a user id (there is no integer column at all).
    for raw in (p.a_sub, p.a_sub[4:], "alice_lp11", "alice_lp11@example.com"):
        assert raw not in stored and raw.upper() not in stored.upper()
    # Keyed, not a bare hash anyone could recompute from a known subject.
    assert row.fingerprint != hashlib.sha256(p.a_sub.encode()).hexdigest()
    assert len(row.fingerprint) == 64
    int(row.fingerprint, 16)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert row.fingerprint not in logged and p.a_sub not in logged


def test_fingerprint_depends_on_the_server_key(app):
    first = deleted_identity.fingerprint("sub-x")
    app.config["SECRET_KEY"] = "a-different-server-key"
    assert deleted_identity.fingerprint("sub-x") != first
