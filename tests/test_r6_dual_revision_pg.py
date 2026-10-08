"""R6-01A on real PostgreSQL: startup coexistence and bounded migration locks.

SQLite cannot show any of this: transactional DDL, lock queues, lock_timeout,
read-only transactions, or two application engines sharing one server.

These tests own the disposable database's ``public`` schema: they reset it
before and after, because the Alembic chain (and alembic_version) is the very
thing under test. Never point PG_TEST_DATABASE_URL at a database you care about.

What this does NOT prove: blue/green availability. It proves only that a
read-only candidate can boot next to a serving revision without mutating the
shared schema, reference data or canonical leaderboard state.
"""
import os
import re
import threading
import time
from datetime import datetime

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )

_WRITE_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|COMMENT|GRANT)\b",
    re.IGNORECASE)


def _url():
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    return url


def _reset_public_schema(url):
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _heads():
    from alembic.script import ScriptDirectory
    script = ScriptDirectory(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "migrations"))
    (head,) = script.get_heads()
    return head, script.get_revision(head).down_revision


def _versions(url):
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(sa.text(
                "SELECT version_num FROM alembic_version ORDER BY 1")).scalars().all()
    finally:
        engine.dispose()


def _fingerprint(url):
    """Schema + reference data + version, as the serving revision sees it."""
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            columns = connection.execute(sa.text(
                "SELECT table_name, column_name, data_type, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' ORDER BY 1, 2")).all()
            indexes = connection.execute(sa.text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' ORDER BY 1")).all()
            constraints = connection.execute(sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE connamespace = 'public'::regnamespace ORDER BY 1")).all()
            quests = connection.execute(sa.text(
                "SELECT id, quest_type, points_reward FROM daily_quest ORDER BY id")).all()
            challenges = connection.execute(sa.text(
                "SELECT id, code FROM challenge ORDER BY id")).all()
            referral = connection.execute(sa.text(
                'SELECT id, referral_code FROM "user" ORDER BY id')).all()
            versions = connection.execute(sa.text(
                "SELECT version_num FROM alembic_version ORDER BY 1")).scalars().all()
        return (columns, indexes, constraints, quests, challenges, referral,
                versions)
    finally:
        engine.dispose()


class SharedRedis:
    """The one Redis both revisions share — every command is recorded."""

    def __init__(self):
        self.zsets = {}
        self.commands = []

    def snapshot(self):
        return {k: dict(v) for k, v in self.zsets.items()}

    def delete(self, *keys):
        self.commands.append(("delete", keys))
        for key in keys:
            self.zsets.pop(key, None)

    def zadd(self, key, mapping):
        self.commands.append(("zadd", key))
        self.zsets.setdefault(key, {}).update(
            {str(m): float(s) for m, s in mapping.items()})

    def zscore(self, key, member):
        return self.zsets.get(key, {}).get(str(member))

    def pipeline(self):
        shared = self

        class _Pipe:
            def zadd(self, key, mapping):
                shared.zadd(key, mapping)

            def execute(self):
                shared.commands.append(("exec", None))

        return _Pipe()

    def __getattr__(self, name):
        def _command(*args, **kwargs):
            self.commands.append((name, args))
            return None
        return _command


@pytest.fixture
def pg_url(monkeypatch):
    url = _url()
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()
    _reset_public_schema(url)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    monkeypatch.delenv("FITX_DB_UPGRADE_FAIL_OPEN", raising=False)
    monkeypatch.delenv("FITX_DB_AUTO_UPGRADE", raising=False)
    # Request-time maintenance is a separate runtime contract (see module doc).
    from app import hooks
    monkeypatch.setattr(hooks, "_last_rollover_check", [datetime.utcnow()])
    monkeypatch.setattr(hooks, "_last_purge_check", [datetime.utcnow()])
    try:
        yield url
    finally:
        _reset_public_schema(url)


def _dispose(flask_app):
    from app.extensions import db
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


def _boot_active(monkeypatch):
    """The CURRENT production boot (self-migrating) on a fresh database."""
    from app import create_app
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    active = create_app()
    active.config.update(TESTING=True)
    return active


def _boot_tool(monkeypatch):
    from app import create_app
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    return create_app()


# ── TEST 1 (PG) + TEST 10: old + candidate coexistence ─────────────────────

def test_read_only_candidate_coexists_with_the_serving_revision(pg_url, monkeypatch):
    from app.config import LB_ALLTIME_KEY, LB_WEEKLY_KEY
    from app.extensions import db
    from app.models import DailyQuest, User
    from app.services import gamification

    redis = SharedRedis()
    monkeypatch.setattr(gamification, "redis_client", redis)
    head, _ = _heads()

    # Serving revision: today's boot on a fresh database — create_all, stamp,
    # real Alembic upgrade on PostgreSQL (bounded lock), seeds, rebuild.
    active = _boot_active(monkeypatch)
    assert _versions(pg_url) == [head]
    with active.app_context():
        assert DailyQuest.query.count() >= 3
        for name in ("r6a-one", "r6a-two"):
            db.session.add(User(username=name, email=f"{name}@example.invalid",
                                cognito_sub=f"sub-{name}"))
        db.session.commit()
        ids = [u.id for u in User.query.order_by(User.id)]
        gamification.award_xp(ids[0], 70)
        db.session.commit()
    assert redis.zscore(LB_ALLTIME_KEY, ids[0]) is not None

    before = _fingerprint(pg_url)
    leaderboard = redis.snapshot()
    commands_before = len(redis.commands)

    statements = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    # Candidate: read-only boot against the SAME database and Redis while the
    # active app is still constructed and serving.
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    sa.event.listen(sa.engine.Engine, "before_cursor_execute", record)
    try:
        from app import create_app
        candidate = create_app()
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", record)
    try:
        assert [s for s in statements if _WRITE_SQL.match(s)] == []
        assert "SET TRANSACTION READ ONLY" in statements
        assert _fingerprint(pg_url) == before
        assert redis.commands[commands_before:] == []
        assert redis.snapshot() == leaderboard

        # Candidate reaches local readiness and reads shared state.
        response = candidate.test_client().get("/health")
        assert response.status_code == 200 and response.get_json()["db"] == "ok"
        with candidate.app_context():
            assert User.query.count() == 2

        # The serving revision keeps reading and writing throughout.
        assert active.test_client().get("/health").status_code == 200
        with active.app_context():
            gamification.award_xp(ids[1], 40)
            db.session.commit()
            assert db.session.get(User, ids[1]).rank_points >= 40
        assert redis.zscore(LB_ALLTIME_KEY, ids[1]) is not None
        assert redis.zscore(LB_WEEKLY_KEY, ids[1]) is not None
        assert not any(cmd[0] == "delete" for cmd in redis.commands[commands_before:])
        assert _versions(pg_url) == [head]
    finally:
        _dispose(candidate)
        _dispose(active)


# ── R6-01A readiness: candidate probes write nothing shared ───────────────

class ThrottlingRedis(SharedRedis):
    """SharedRedis plus real ``SET NX EX``: an empty throttle key is takeable,
    so any hook that reaches it on a probe WOULD run maintenance."""

    def __init__(self):
        super().__init__()
        self.strings = {}

    def set(self, key, value, nx=False, ex=None):
        self.commands.append(("set", key))
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True


_REDIS_READS = frozenset({"ping", "exists", "get", "ttl", "zscore", "mget"})


def _maintenance_state(url):
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            weekly = connection.execute(sa.text(
                "SELECT * FROM weekly_reset_log ORDER BY 1")).all()
            sessions = connection.execute(sa.text(
                "SELECT count(*) FROM cognito_session")).scalar()
            xp = connection.execute(sa.text(
                'SELECT id, rank_points, weekly_xp FROM "user" ORDER BY id')).all()
        return weekly, sessions, xp
    finally:
        engine.dispose()


def test_read_only_candidate_readiness_probes_mutate_nothing(pg_url, monkeypatch):
    from app import hooks, jobs
    from app.extensions import db
    from app.jobs.tasks import run_daily_maintenance
    from app.models import User
    from app.services import gamification

    redis = ThrottlingRedis()
    monkeypatch.setattr(gamification, "redis_client", redis)
    monkeypatch.setattr("app.extensions.redis_client", redis)
    dispatched = []
    monkeypatch.setattr(jobs, "dispatch_background",
                        lambda func, *a, **k: dispatched.append(func))

    active = _boot_active(monkeypatch)
    with active.app_context():
        db.session.add(User(username="r6a-probe", email="r6a-probe@example.invalid",
                            cognito_sub="sub-r6a-probe"))
        db.session.commit()
        gamification.award_xp(User.query.one().id, 30)
        db.session.commit()

    # Arm maintenance completely: no throttle held in Redis or in process,
    # and no rollover recorded for this week — a hook that runs now writes.
    monkeypatch.setattr(gamification, "_last_rollover_check", [None])
    monkeypatch.setattr(hooks, "_last_rollover_check", [None])
    monkeypatch.setattr(hooks, "_last_purge_check", [None])
    engine = sa.create_engine(pg_url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM weekly_reset_log"))
    finally:
        engine.dispose()

    before = _fingerprint(pg_url)
    state = _maintenance_state(pg_url)
    leaderboard = redis.snapshot()
    commands_before = len(redis.commands)
    statements = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    sa.event.listen(sa.engine.Engine, "before_cursor_execute", record)
    try:
        from app import create_app
        candidate = create_app()
        candidate.config.update(TESTING=True)
        client = candidate.test_client()
        shallow = client.get("/health")
        deep = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", record)
    try:
        assert shallow.status_code == 200 and shallow.get_json()["db"] == "ok"
        body = deep.get_json()
        assert deep.status_code == 200, body
        assert body["revision"] and body["redis"] == "ok" and body["login"] == "ok"

        assert [s for s in statements if _WRITE_SQL.match(s)] == []
        assert _fingerprint(pg_url) == before
        assert _maintenance_state(pg_url) == state
        probe_commands = redis.commands[commands_before:]
        assert [c for c in probe_commands if c[0] not in _REDIS_READS] == []
        assert "fitx:rollover_check" not in redis.strings
        assert "fitx:session_purge" not in redis.strings
        assert redis.snapshot() == leaderboard
        assert dispatched == []

        # Control (non-vacuity) + contract preserved: the SAME armed state on
        # an ordinary request does run the existing maintenance mechanism.
        client.get("/login")
        assert "fitx:rollover_check" in redis.strings
        assert "fitx:session_purge" in redis.strings
        assert dispatched == [run_daily_maintenance]
        assert len(_maintenance_state(pg_url)[0]) == 1  # this week's rollover
    finally:
        _dispose(candidate)
        _dispose(active)


# ── TEST 3 / 5 (PG): behind or broken → fail closed, no mutation ───────────

@pytest.mark.parametrize("case", ["behind", "unknown", "missing"])
def test_candidate_fails_closed_on_postgres_without_mutating(pg_url, monkeypatch, case):
    import flask_migrate

    from app.extensions import db
    from app.schema_safety import SchemaNotReady

    head, parent = _heads()
    tool = _boot_tool(monkeypatch)
    with tool.app_context():
        flask_migrate.upgrade()
        statement = {
            "behind": "UPDATE alembic_version SET version_num = :parent",
            "unknown": "UPDATE alembic_version SET version_num = 'ffffffffffff'",
            "missing": "DROP TABLE alembic_version",
        }[case]
        db.session.execute(sa.text(statement), {"parent": parent})
        db.session.commit()
    _dispose(tool)
    before = _fingerprint(pg_url) if case != "missing" else None

    def _no_upgrade(*_a, **_k):
        raise AssertionError("read-only candidate invoked an upgrade")

    monkeypatch.setattr(flask_migrate, "upgrade", _no_upgrade)
    monkeypatch.setattr(flask_migrate, "stamp", _no_upgrade)
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    from app import create_app
    with pytest.raises(SchemaNotReady) as raised:
        create_app()
    assert raised.value.reason == {
        "behind": "not_at_head", "unknown": "unknown_revision",
        "missing": "version_table_missing"}[case]
    if before is not None:
        assert _fingerprint(pg_url) == before
    else:
        engine = sa.create_engine(pg_url)
        try:
            assert not sa.inspect(engine).has_table("alembic_version")
        finally:
            engine.dispose()


# ── TEST 6: migration lock wait is bounded on the real migration path ──────

def _hold_user_row_exclusive(url):
    """A serving-revision write transaction left open on "user" (ROW EXCLUSIVE).

    Creating a table with a FOREIGN KEY to "user" needs SHARE ROW EXCLUSIVE on
    it, which conflicts — exactly the DDL the head migration runs.
    """
    engine = sa.create_engine(url)
    connection = engine.connect()
    connection.execute(sa.text('UPDATE "user" SET id = id WHERE false'))
    pid = connection.execute(sa.text("SELECT pg_backend_pid()")).scalar()
    return engine, connection, pid


def _run_with_deadline(fn, deadline):
    outcome = {}

    def _target():
        started = time.monotonic()
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome["error"] = exc
        outcome["elapsed"] = time.monotonic() - started

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(deadline)
    return thread, outcome


def test_blocked_migration_aborts_at_the_lock_timeout(pg_url, monkeypatch):
    import flask_migrate

    from app.extensions import db

    head, parent = _heads()
    tool = _boot_tool(monkeypatch)
    with tool.app_context():
        flask_migrate.upgrade()
        flask_migrate.downgrade(revision=parent)
    _dispose(tool)
    assert _versions(pg_url) == [parent]

    monkeypatch.setenv("FITX_MIGRATION_LOCK_TIMEOUT_MS", "700")
    blocker_engine, blocker, blocker_pid = _hold_user_row_exclusive(pg_url)
    try:
        # The real current production path: a self-migrating boot that finds
        # a pending migration while another session holds a conflicting lock.
        from app import create_app
        monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
        thread, outcome = _run_with_deadline(create_app, deadline=60)
        if thread.is_alive():
            blocker.rollback()
            thread.join(30)
            pytest.fail("migration waited on the lock with no bound")
        error = outcome.get("error")
        assert isinstance(error, sa.exc.OperationalError), outcome
        assert getattr(error.orig, "pgcode", None) == "55P03"  # lock_not_available
        assert 0.6 <= outcome["elapsed"] < 15

        # Fail closed and atomic: nothing of the blocked migration landed.
        assert _versions(pg_url) == [parent]
        engine = sa.create_engine(pg_url)
        try:
            assert not sa.inspect(engine).has_table("exercise_note")
            with engine.connect() as connection:
                held = connection.execute(sa.text(
                    "SELECT count(*) FROM pg_locks WHERE pid = :pid AND granted"),
                    {"pid": blocker_pid}).scalar()
            assert held > 0  # the serving transaction was never disturbed
        finally:
            engine.dispose()
    finally:
        blocker.rollback()
        blocker.close()
        blocker_engine.dispose()

    # With the lock released the same release is retryable and completes, and
    # the request pool never inherited the migration session's lock_timeout.
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    tool = _boot_tool(monkeypatch)
    try:
        with tool.app_context():
            flask_migrate.upgrade()
            assert db.session.execute(sa.text("SHOW lock_timeout")).scalar() == "0"
            for _ in range(3):  # several pooled connections, all default
                with db.engine.connect() as connection:
                    assert connection.execute(sa.text(
                        "SHOW lock_timeout")).scalar() == "0"
    finally:
        _dispose(tool)
    assert _versions(pg_url) == [head]


def test_migration_connection_carries_the_bound_and_app_pool_does_not(
        pg_url, monkeypatch):
    from app.extensions import db
    from app.schema_safety import (
        DEFAULT_MIGRATION_LOCK_TIMEOUT_MS, assert_lock_timeout,
        migration_connectable)

    monkeypatch.delenv("FITX_MIGRATION_LOCK_TIMEOUT_MS", raising=False)
    tool = _boot_tool(monkeypatch)
    try:
        with tool.app_context():
            engine, timeout = migration_connectable(db.engine)
            try:
                assert timeout == DEFAULT_MIGRATION_LOCK_TIMEOUT_MS
                with engine.connect() as connection:
                    assert_lock_timeout(connection, timeout)
                    assert not connection.in_transaction()
                    assert connection.execute(sa.text(
                        "SHOW lock_timeout")).scalar() == "5s"
            finally:
                engine.dispose()
            assert db.session.execute(sa.text("SHOW lock_timeout")).scalar() == "0"
    finally:
        _dispose(tool)


def test_control_the_held_lock_really_blocks_unbounded_ddl(pg_url, monkeypatch):
    """Without a lock bound the same DDL simply waits (here cut by a
    statement_timeout so the control itself terminates)."""
    import flask_migrate
    head, parent = _heads()
    tool = _boot_tool(monkeypatch)
    with tool.app_context():
        flask_migrate.upgrade()
        flask_migrate.downgrade(revision=parent)
    _dispose(tool)
    blocker_engine, blocker, _ = _hold_user_row_exclusive(pg_url)
    engine = sa.create_engine(pg_url)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SET statement_timeout = 1500"))
            with pytest.raises(sa.exc.OperationalError) as raised:
                connection.execute(sa.text(
                    'CREATE TABLE r6a_probe (user_id INTEGER REFERENCES "user"(id))'))
            assert raised.value.orig.pgcode == "57014"  # query_canceled, not lock
            connection.rollback()
    finally:
        engine.dispose()
        blocker.rollback()
        blocker.close()
        blocker_engine.dispose()


# ── R6-01B: a read-only candidate serves the authenticated mobile API ──────

def test_read_only_candidate_serves_the_authenticated_mobile_api(pg_url, monkeypatch):
    """The slot primitive exists to host the native mobile API. A candidate
    booted exactly as a web slot boots (FITX_STARTUP_MODE=read-only) must serve
    a real Bearer-authenticated /api/v1 read on the shared schema, report its
    exact revision on deep health, and leave the schema untouched. Only Cognito
    is faked; the opaque mobile credential is minted and resolved for real."""
    import calendar
    from datetime import timedelta
    import app.config as app_config
    from app.extensions import db
    from app.models import User
    from app.services import cognito_jwt, cognito_service, gamification, mobile_auth

    candidate_revision = "c" * 40
    monkeypatch.setattr(gamification, "redis_client", SharedRedis())

    def authenticate(username, password):
        sub = f"sub-{username}"
        return {"tokens": {"access_token": f"access|{sub}", "id_token": f"id|{sub}",
                           "refresh_token": f"refresh|{sub}", "expires_in": 3600},
                "claims": {"sub": sub}}

    def validate(token, expected_use, leeway_seconds=0):
        sub = token.split("|", 1)[1]
        if expected_use == "id":
            return {"sub": sub, "email": f"{sub}@example.com", "email_verified": True}
        return {"sub": sub, "exp": calendar.timegm(
            (datetime.utcnow() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    # Production-shaped provider-token encryption (no TESTING shortcut).
    from cryptography.fernet import Fernet
    from app.services import session_store
    monkeypatch.setattr(session_store, "COGNITO_TOKEN_ENC_KEY",
                        Fernet.generate_key().decode())
    monkeypatch.setattr(session_store, "_fernet", None)

    active = _boot_active(monkeypatch)
    with active.app_context():
        db.session.add(User(username="r6b-mobile", email="r6b-mobile@example.com",
                            cognito_sub="sub-r6b-mobile"))
        db.session.commit()
    schema_before = _fingerprint(pg_url)

    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    monkeypatch.setattr(app_config, "load_build_revision", lambda: candidate_revision)
    from app import create_app
    candidate = create_app()
    try:
        assert candidate.config["MOBILE_AUTH_ENABLED"] is True
        assert "mobile_api" in candidate.blueprints
        client = candidate.test_client()

        deep = client.get("/health?deep=1")
        assert deep.status_code == 200
        assert deep.get_json()["revision"] == candidate_revision

        assert client.get("/api/v1/account/me").status_code == 401
        bogus = client.get("/api/v1/account/me",
                           headers={"Authorization": "Bearer not-a-credential"})
        assert bogus.status_code == 401
        assert bogus.get_json()["error"]["code"] == "AUTH_SESSION_EXPIRED"

        with candidate.app_context():
            issued = mobile_auth.login("r6b-mobile", "Sifre123")
            user_id = User.query.filter_by(username="r6b-mobile").one().id
        me = client.get("/api/v1/account/me", headers={
            "Authorization": f"Bearer {issued.access_credential}"})
        assert me.status_code == 200, me.get_data(as_text=True)
        assert me.headers["Cache-Control"].startswith("no-store")
        assert me.get_json()["user"]["username"] == "r6b-mobile"
        assert user_id is not None

        schema_after = _fingerprint(pg_url)
        assert schema_after[:3] == schema_before[:3]       # columns/indexes/constraints
        assert schema_after[-1] == schema_before[-1]       # alembic head unchanged
    finally:
        _dispose(candidate)
        _dispose(active)
