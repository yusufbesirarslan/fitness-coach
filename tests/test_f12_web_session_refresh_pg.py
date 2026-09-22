"""F12: disposable-PostgreSQL concurrency coverage for legacy web session refresh.

`session_store.get_valid_access_token` renews the provider access token in three
phases: a short read that ends in a detached snapshot, the Cognito call with no
database transaction open, then a short `SELECT ... FOR UPDATE` that
re-validates the row before writing or deleting anything. SQLite has no row
locks, so every contention claim here needs real PostgreSQL.

Threads are coordinated with events and barriers only; the one wait loop polls
`pg_stat_activity` for a lock waiter, which is a state, not a duration.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \
        python -m pytest tests/test_f12_web_session_refresh_pg.py -v
"""

import os
import threading
import time
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from flask import Flask

from app.blueprints import auth as auth_bp
from app.extensions import db
from app.models import CognitoSession, User
from app.services import ai_gate, cognito_service, session_store
from app.services.cognito_service import CognitoServiceError


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


WAIT = 10


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    app = Flask("f12-web-session-refresh-pg")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-f12-web-refresh",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    monkeypatch.setattr(session_store, "_fernet", None)
    monkeypatch.setattr(ai_gate, "_ai_slots", threading.BoundedSemaphore(2))
    with app.app_context():
        db.drop_all()
        db.create_all()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _expired_session(app):
    """One user with one web session whose access token needs renewal."""
    with app.app_context():
        user = User(username="f12-web", email="f12-web@example.invalid",
                    cognito_sub="sub-f12-web")
        db.session.add(user)
        db.session.commit()
        sid = session_store.create(user, {
            "access_token": "acc-old", "refresh_token": "ref-old",
            "expires_in": 3600,
        }, "f12-web")
        row = session_store.get(sid)
        row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        return user.id, sid, row.id


def _row(app, sid):
    """Committed row state, read in a fresh context (None = row absent)."""
    with app.app_context():
        row = session_store.get(sid)
        state = None if row is None else {
            "access": session_store.decrypt_token(row.access_token),
            "refresh": session_store.decrypt_token(row.refresh_token),
            "refresh_ciphertext": row.refresh_token,
            "access_token_exp": row.access_token_exp,
        }
        db.session.remove()
        return state


class Provider:
    """Scripted `cognito_service.refresh_tokens` with per-contender gates.

    Each call records what the caller could see at the moment of provider I/O:
    whether its session had a transaction open and how many pooled
    connections were checked out process-wide.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.observed = {}
        self.entered = {}
        self.release = {}
        self.outcome = {}
        self.local = threading.local()

    def contender(self, name, outcome):
        self.entered[name] = threading.Event()
        self.release[name] = threading.Event()
        self.outcome[name] = outcome

    def __call__(self, refresh_token, username):
        name = self.local.name
        with self.lock:
            self.calls.append((name, refresh_token))
        self.observed[name] = {
            "in_transaction": db.session().in_transaction(),
            "checked_out": db.engine.pool.checkedout(),
        }
        self.entered[name].set()
        assert self.release[name].wait(WAIT), f"{name} never released"
        outcome = self.outcome[name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _start(app, provider, name, uid, sid, results):
    def run():
        provider.local.name = name
        with app.app_context():
            try:
                results[name] = (
                    "ok", session_store.get_valid_access_token(sid, uid))
            except Exception as exc:  # recorded, then asserted on
                results[name] = ("raise", type(exc).__name__, str(exc))
            finally:
                db.session.remove()

    thread = threading.Thread(target=run, name=f"f12-{name}")
    thread.start()
    return thread


def _join(*threads):
    for thread in threads:
        thread.join(WAIT)
        assert not thread.is_alive(), f"{thread.name} did not finish"


def _tokens(access, refresh="ref-old"):
    return {"access_token": access, "id_token": "", "refresh_token": refresh,
            "expires_in": 3600}


def test_two_simultaneous_refreshes_converge_on_one_committed_winner(
        pg_app, monkeypatch):
    uid, sid, _ = _expired_session(pg_app)
    provider = Provider()
    provider.contender("A", _tokens("acc-A"))
    provider.contender("B", _tokens("acc-B"))
    monkeypatch.setattr(cognito_service, "refresh_tokens", provider)
    results = {}

    a = _start(pg_app, provider, "A", uid, sid, results)
    b = _start(pg_app, provider, "B", uid, sid, results)
    # Both contenders snapshotted the expired row and are at the provider.
    assert provider.entered["A"].wait(WAIT)
    assert provider.entered["B"].wait(WAIT)
    provider.release["A"].set()
    _join(a)
    after_winner = _row(pg_app, sid)
    provider.release["B"].set()
    _join(b)

    final = _row(pg_app, sid)
    # Duplicate provider calls are possible and are recorded, not hidden.
    assert sorted(name for name, _ in provider.calls) == ["A", "B"]
    assert results == {"A": ("ok", "acc-A"), "B": ("ok", "acc-A")}
    assert after_winner["access"] == "acc-A"
    # No stale overwrite: the loser left the winner's row byte-identical.
    assert final == after_winner
    assert final["refresh"] == "ref-old"
    assert final["access_token_exp"] > datetime.utcnow()
    for name in ("A", "B"):
        assert provider.observed[name]["in_transaction"] is False


def test_global_logout_during_provider_io_wins_permanently(
        pg_app, monkeypatch):
    uid, sid, _ = _expired_session(pg_app)
    provider = Provider()
    provider.contender("A", _tokens("acc-A"))
    monkeypatch.setattr(cognito_service, "refresh_tokens", provider)
    results = {}

    a = _start(pg_app, provider, "A", uid, sid, results)
    assert provider.entered["A"].wait(WAIT)
    with pg_app.app_context():
        _, web_deleted = auth_bp._revoke_local_sessions_for_global_logout(uid)
        db.session.remove()
    assert web_deleted == 1
    provider.release["A"].set()
    _join(a)

    assert results["A"] == ("raise", "SessionInvalid", "no_session")
    assert _row(pg_app, sid) is None
    with pg_app.app_context():
        assert CognitoSession.query.filter_by(user_id=uid).count() == 0
        # The stale request cannot authenticate afterwards either.
        with pytest.raises(session_store.SessionInvalid):
            session_store.get_valid_access_token(sid, uid)
        db.session.remove()


def test_password_reset_revocation_during_provider_io_wins_permanently(
        pg_app, monkeypatch):
    uid, sid, _ = _expired_session(pg_app)
    provider = Provider()
    provider.contender("A", _tokens("acc-A"))
    monkeypatch.setattr(cognito_service, "refresh_tokens", provider)
    revoked = []
    monkeypatch.setattr(cognito_service, "revoke_token", revoked.append)
    results = {}

    a = _start(pg_app, provider, "A", uid, sid, results)
    assert provider.entered["A"].wait(WAIT)
    with pg_app.app_context():
        auth_bp._revoke_all_sessions_after_credential_change(uid)
        db.session.remove()
    provider.release["A"].set()
    _join(a)

    assert results["A"] == ("raise", "SessionInvalid", "no_session")
    assert _row(pg_app, sid) is None
    assert revoked == ["ref-old"]
    with pg_app.app_context():
        assert CognitoSession.query.filter_by(user_id=uid).count() == 0
        assert db.session.get(User, uid).credential_epoch == 1
        with pytest.raises(session_store.SessionInvalid):
            session_store.get_valid_access_token(sid, uid)
        db.session.remove()


def test_stale_definitive_rejection_never_deletes_a_committed_winner(
        pg_app, monkeypatch):
    uid, sid, _ = _expired_session(pg_app)
    provider = Provider()
    provider.contender(
        "stale", CognitoServiceError("rejected", "NotAuthorizedException"))
    provider.contender("winner", _tokens("acc-winner"))
    monkeypatch.setattr(cognito_service, "refresh_tokens", provider)
    results = {}

    stale = _start(pg_app, provider, "stale", uid, sid, results)
    assert provider.entered["stale"].wait(WAIT)
    winner = _start(pg_app, provider, "winner", uid, sid, results)
    assert provider.entered["winner"].wait(WAIT)
    provider.release["winner"].set()
    _join(winner)
    committed = _row(pg_app, sid)
    provider.release["stale"].set()
    _join(stale)

    assert results == {
        "winner": ("ok", "acc-winner"), "stale": ("ok", "acc-winner")}
    assert committed["access"] == "acc-winner"
    assert _row(pg_app, sid) == committed


def test_provider_io_holds_no_transaction_connection_or_row_lock(
        pg_app, monkeypatch):
    uid, sid, row_id = _expired_session(pg_app)
    provider = Provider()
    provider.contender("A", _tokens("acc-A"))
    monkeypatch.setattr(cognito_service, "refresh_tokens", provider)
    results = {}

    a = _start(pg_app, provider, "A", uid, sid, results)
    assert provider.entered["A"].wait(WAIT)
    try:
        assert provider.observed["A"] == {
            "in_transaction": False, "checked_out": 0}
        with pg_app.app_context():
            with db.engine.connect() as conn:
                with conn.begin():
                    # NOWAIT fails at once if anyone holds the row lock.
                    locked = conn.execute(sa.text(
                        "SELECT id FROM cognito_session WHERE id = :id "
                        "FOR UPDATE NOWAIT"), {"id": row_id}).scalar()
                    assert locked == row_id
            # The real revocation path commits while the provider is blocked.
            assert session_store.delete_for_user(uid) == 1
            db.session.remove()
    finally:
        provider.release["A"].set()
        _join(a)
    assert results["A"] == ("raise", "SessionInvalid", "no_session")
    assert _row(pg_app, sid) is None


def _lock_waiters(conn):
    return conn.execute(sa.text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock' "
        "AND state = 'active'")).scalar()


def test_phase_three_serializes_contenders_that_reconcile_together(
        pg_app, monkeypatch):
    """Both contenders leave the provider while a third party holds the row.

    They queue on the row lock; whichever runs first commits, and the second
    must see that commit under its own lock and return it — every caller ends
    with the token that is actually stored.
    """
    uid, sid, row_id = _expired_session(pg_app)
    provider = Provider()
    provider.contender("A", _tokens("acc-A"))
    provider.contender("B", _tokens("acc-B"))
    monkeypatch.setattr(cognito_service, "refresh_tokens", provider)
    results = {}

    with pg_app.app_context():
        holder = db.engine.connect()
        observer = db.engine.connect()
    try:
        a = _start(pg_app, provider, "A", uid, sid, results)
        b = _start(pg_app, provider, "B", uid, sid, results)
        assert provider.entered["A"].wait(WAIT)
        assert provider.entered["B"].wait(WAIT)
        hold = holder.begin()
        holder.execute(sa.text(
            "SELECT id FROM cognito_session WHERE id = :id FOR UPDATE"),
            {"id": row_id})
        provider.release["A"].set()
        provider.release["B"].set()
        deadline = time.monotonic() + WAIT
        while _lock_waiters(observer) < 2:
            assert time.monotonic() < deadline, "contenders never queued"
            observer.rollback()
        hold.rollback()
        _join(a, b)
    finally:
        holder.close()
        observer.close()

    final = _row(pg_app, sid)
    assert final["access"] in {"acc-A", "acc-B"}
    assert results == {
        "A": ("ok", final["access"]), "B": ("ok", final["access"])}
