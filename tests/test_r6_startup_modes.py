"""R6-01A: dual-revision startup contract (hermetic, file-backed SQLite).

The candidate boot (FITX_STARTUP_MODE=read-only) is exercised through the REAL
create_app(): nothing in the startup path is replaced wholesale. The spies below
only turn every shared-state mutation into a hard failure, and a cursor-level
listener records every SQL statement the candidate sends, so "mutation-free"
is proven at the database boundary, not inferred from a skipped function.

PostgreSQL-only semantics (read-only transactions, lock_timeout, coexistence of
two apps on one database) live in tests/test_r6_dual_revision_pg.py.
"""
import os
import re

import pytest
import sqlalchemy as sa

import app as app_package
from app import schema_safety
from app.config import LB_ALLTIME_KEY, LB_WEEKLY_KEY
from app.schema_safety import (
    SchemaNotReady, StartupMode, StartupModeError, resolve_startup_mode,
)

_WRITE_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE)\b",
    re.IGNORECASE)


# ── helpers ────────────────────────────────────────────────────────────────

def _head_and_parent():
    from alembic.script import ScriptDirectory
    script = ScriptDirectory(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "migrations"))
    (head,) = script.get_heads()
    parent = script.get_revision(head).down_revision
    return head, parent


def _build_schema(monkeypatch, url, versions):
    """Model schema + an alembic_version table holding ``versions`` (or none)."""
    from app.extensions import db
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    tool = app_package.create_app()
    with tool.app_context():
        db.create_all()
        if versions is not None:
            db.session.execute(sa.text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL "
                "PRIMARY KEY)"))
            for version in versions:
                db.session.execute(sa.text(
                    "INSERT INTO alembic_version VALUES (:v)"), {"v": version})
        db.session.commit()
        db.session.remove()
        db.engine.dispose()


def _schema_fingerprint(path):
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            tables = connection.execute(sa.text(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            )).all()
            versions = (connection.execute(sa.text(
                "SELECT version_num FROM alembic_version ORDER BY 1")).scalars().all()
                if any(row[1] == "alembic_version" for row in tables) else None)
            quests = connection.execute(sa.text(
                "SELECT count(*) FROM daily_quest")).scalar()
            challenges = connection.execute(sa.text(
                "SELECT count(*) FROM challenge")).scalar()
        return tables, versions, quests, challenges
    finally:
        engine.dispose()


class _Recorder:
    """Every statement sent by any engine while installed."""

    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(statement)

    def writes(self):
        return [s for s in self.statements if _WRITE_SQL.match(s)]


@pytest.fixture
def sql_recorder():
    recorder = _Recorder()
    sa.event.listen(sa.engine.Engine, "before_cursor_execute", recorder)
    try:
        yield recorder
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", recorder)


class _LeaderboardRedis:
    """Strict stand-in for the shared Redis: records every command."""

    def __init__(self):
        self.zsets = {
            LB_ALLTIME_KEY: {"7": 4200000.0, "9": 100003.0},
            LB_WEEKLY_KEY: {"7": 900001.0},
        }
        self.commands = []

    def snapshot(self):
        return {key: dict(members) for key, members in self.zsets.items()}

    def delete(self, *keys):
        self.commands.append(("delete", keys))
        for key in keys:
            self.zsets.pop(key, None)

    def zadd(self, key, mapping):
        self.commands.append(("zadd", key))
        self.zsets.setdefault(key, {}).update(
            {str(m): float(s) for m, s in mapping.items()})

    def pipeline(self):
        recorder = self

        class _Pipe:
            def zadd(self, key, mapping):
                recorder.zadd(key, mapping)

            def execute(self):
                recorder.commands.append(("exec", None))

        return _Pipe()

    def __getattr__(self, name):  # any other command is recorded, never silent
        def _command(*args, **kwargs):
            self.commands.append((name, args))
            return None
        return _command


def _forbid_startup_mutations(monkeypatch):
    """Every shared-state mutation reachable from startup raises."""
    import flask_migrate

    from app import db_init
    from app.extensions import db
    from app.services import challenges, gamification, referral

    def _forbidden(name):
        def _raise(*_a, **_k):
            raise AssertionError(f"read-only startup called {name}")
        return _raise

    monkeypatch.setattr(flask_migrate, "upgrade", _forbidden("upgrade"))
    monkeypatch.setattr(flask_migrate, "stamp", _forbidden("stamp"))
    monkeypatch.setattr(db, "create_all", _forbidden("create_all"))
    monkeypatch.setattr(challenges, "seed_challenges",
                        _forbidden("seed_challenges"))
    monkeypatch.setattr(referral, "backfill_referral_codes",
                        _forbidden("backfill_referral_codes"))
    monkeypatch.setattr(gamification, "lb_rebuild", _forbidden("lb_rebuild"))
    monkeypatch.setattr(db_init, "lb_rebuild", _forbidden("lb_rebuild"))
    monkeypatch.setattr(db_init, "prepare_release", _forbidden("prepare_release"))


def _candidate_env(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")


def _dispose(flask_app):
    from app.extensions import db
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


# ── mode resolution ────────────────────────────────────────────────────────

@pytest.mark.parametrize("environ, expected", [
    ({}, StartupMode.SELF_MIGRATING),
    ({"FITX_STARTUP_MODE": ""}, StartupMode.SELF_MIGRATING),
    ({"FITX_STARTUP_MODE": "self-migrating"}, StartupMode.SELF_MIGRATING),
    ({"FITX_STARTUP_MODE": "read-only"}, StartupMode.READ_ONLY),
    ({"FITX_SKIP_DB_INIT": "1"}, StartupMode.SKIP_DB_INIT),
    ({"FITX_SKIP_DB_INIT": "0"}, StartupMode.SELF_MIGRATING),
])
def test_startup_mode_resolution(environ, expected):
    assert resolve_startup_mode(environ) is expected


@pytest.mark.parametrize("environ", [
    {"FITX_STARTUP_MODE": "readonly"},
    {"FITX_STARTUP_MODE": "READ-ONLY"},
    {"FITX_STARTUP_MODE": "skip-db-init"},   # derived only, never selectable
    {"FITX_STARTUP_MODE": "candidate"},
    {"FITX_STARTUP_MODE": "read-only", "FITX_SKIP_DB_INIT": "1"},
    {"FITX_STARTUP_MODE": "self-migrating", "FITX_SKIP_DB_INIT": "1"},
])
def test_invalid_or_ambiguous_startup_mode_fails_closed(environ):
    with pytest.raises(StartupModeError):
        resolve_startup_mode(environ)


def test_create_app_refuses_an_invalid_mode_before_any_work(monkeypatch):
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "candidate")
    monkeypatch.setattr(app_package, "Flask", None)  # nothing may be built
    with pytest.raises(StartupModeError):
        app_package.create_app()


# ── TEST 1: default (self-migrating) boot keeps today's sequence ───────────

def test_default_boot_still_runs_release_preparation_then_rebuild(monkeypatch):
    calls = []
    from app import db_init
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    monkeypatch.setattr(db_init, "prepare_release",
                        lambda app: calls.append("prepare_release"))
    monkeypatch.setattr(db_init, "lb_rebuild", lambda: calls.append("lb_rebuild"))
    monkeypatch.setattr(app_package, "verify_schema_ready",
                        lambda app: calls.append("verify"))
    from app.services import mobile_auth
    monkeypatch.setattr(mobile_auth, "validate_derivation_key_readiness",
                        lambda: calls.append("mobile_readiness"))
    flask_app = app_package.create_app()
    _dispose(flask_app)
    assert calls == ["prepare_release", "lb_rebuild", "mobile_readiness"]


def test_skip_db_init_boot_does_no_db_work(monkeypatch):
    calls = []
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    monkeypatch.setattr(app_package, "init_database",
                        lambda app: calls.append("init"))
    monkeypatch.setattr(app_package, "verify_schema_ready",
                        lambda app: calls.append("verify"))
    from app.services import mobile_auth
    monkeypatch.setattr(mobile_auth, "validate_derivation_key_readiness",
                        lambda: calls.append("mobile_readiness"))
    _dispose(app_package.create_app())
    assert calls == []


# ── TEST 2 + 4 + 9: candidate boot is mutation-free and leaves Redis alone ─

def test_read_only_candidate_boots_without_any_shared_mutation(
        monkeypatch, tmp_path, sql_recorder):
    from app.services import gamification
    head, _ = _head_and_parent()
    path = tmp_path / "ready.db"
    url = f"sqlite:///{path}"
    _build_schema(monkeypatch, url, [head])
    before = _schema_fingerprint(path)

    redis = _LeaderboardRedis()
    monkeypatch.setattr(gamification, "redis_client", redis)
    active_leaderboard = redis.snapshot()
    _forbid_startup_mutations(monkeypatch)
    _candidate_env(monkeypatch, url)

    sql_recorder.statements.clear()
    candidate = app_package.create_app()
    try:
        # The startup boundary: everything create_app() sent to the database.
        boot_statements = list(sql_recorder.statements)
        assert [s for s in boot_statements if _WRITE_SQL.match(s)] == []
        assert any("alembic_version" in s for s in boot_statements)
        assert _schema_fingerprint(path) == before
        assert redis.commands == []
        assert redis.snapshot() == active_leaderboard

        # Readiness of the real app. Request-time maintenance hooks (weekly
        # rollover, session purge) are a separate, fleet-throttled RUNTIME
        # contract shared with the active revision; they are held off here so
        # this probe proves only that the candidate serves.
        from datetime import datetime

        from app import hooks
        monkeypatch.setattr(hooks, "_last_rollover_check", [datetime.utcnow()])
        monkeypatch.setattr(hooks, "_last_purge_check", [datetime.utcnow()])
        response = candidate.test_client().get("/health")
        assert response.status_code == 200
        assert response.get_json()["db"] == "ok"
    finally:
        _dispose(candidate)

    assert sql_recorder.writes() == []
    assert _schema_fingerprint(path) == before
    assert redis.commands == []
    assert redis.snapshot() == active_leaderboard


def test_control_self_migrating_boot_does_rebuild_the_canonical_keys(
        monkeypatch, tmp_path):
    """Proves the leaderboard assertion above can fail: the default boot's
    lb_rebuild deletes and repopulates the very keys the candidate preserves."""
    import flask_migrate

    from app.services import gamification
    head, _ = _head_and_parent()
    url = f"sqlite:///{tmp_path / 'control.db'}"
    _build_schema(monkeypatch, url, [head])
    redis = _LeaderboardRedis()
    monkeypatch.setattr(gamification, "redis_client", redis)
    monkeypatch.setattr(flask_migrate, "upgrade", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    _dispose(app_package.create_app())
    assert ("delete", (LB_ALLTIME_KEY, LB_WEEKLY_KEY)) in redis.commands
    assert redis.snapshot() != _LeaderboardRedis().snapshot()


# ── TEST 3 + 5: anything but "exactly at head" fails closed, read-only ─────

@pytest.mark.parametrize("case", [
    "behind", "missing_table", "empty_table", "unknown_revision", "divergent",
])
def test_candidate_fails_closed_unless_schema_is_exactly_at_head(
        monkeypatch, tmp_path, sql_recorder, case):
    head, parent = _head_and_parent()
    versions = {
        "behind": [parent],
        "missing_table": None,
        "empty_table": [],
        "unknown_revision": ["ffffffffffff"],
        "divergent": [head, parent],
    }[case]
    expected_reason = {
        "behind": "not_at_head",
        "missing_table": "version_table_missing",
        "empty_table": "version_table_empty",
        "unknown_revision": "unknown_revision",
        "divergent": "not_at_head",
    }[case]
    path = tmp_path / f"{case}.db"
    url = f"sqlite:///{path}"
    _build_schema(monkeypatch, url, versions)
    before = _schema_fingerprint(path)
    _forbid_startup_mutations(monkeypatch)
    _candidate_env(monkeypatch, url)

    sql_recorder.statements.clear()
    with pytest.raises(SchemaNotReady) as raised:
        app_package.create_app()

    assert raised.value.reason == expected_reason
    assert sql_recorder.writes() == []
    assert _schema_fingerprint(path) == before


def test_candidate_fails_closed_when_the_database_is_unreadable(
        monkeypatch, tmp_path):
    head, _ = _head_and_parent()
    url = f"sqlite:///{tmp_path / 'unreadable.db'}"
    _build_schema(monkeypatch, url, [head])
    _forbid_startup_mutations(monkeypatch)
    _candidate_env(monkeypatch, url)

    def _broken(*_a, **_k):
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("db down"))

    monkeypatch.setattr(schema_safety, "_read_current_heads", _broken)
    with pytest.raises(SchemaNotReady) as raised:
        app_package.create_app()
    assert raised.value.reason == "unreadable"


def test_candidate_fails_closed_without_a_migration_graph(monkeypatch, tmp_path):
    monkeypatch.setattr(schema_safety, "migrations_directory",
                        lambda app: str(tmp_path / "absent"))
    with pytest.raises(SchemaNotReady) as raised:
        schema_safety.expected_heads(object())
    assert raised.value.reason == "migrations_unavailable"


def test_expected_heads_is_alembics_own_head_set():
    from alembic.script import ScriptDirectory
    flask_app = type("A", (), {"root_path": os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")})()
    directory = schema_safety.migrations_directory(flask_app)
    assert schema_safety.expected_heads(flask_app) == frozenset(
        ScriptDirectory(directory).get_heads())


# ── TEST 12: security readiness is NOT skipped by a mutation-free boot ─────

def test_candidate_runs_mobile_derivation_readiness_and_fails_closed(
        monkeypatch, tmp_path):
    from app.services import mobile_auth, mobile_credentials
    head, _ = _head_and_parent()
    url = f"sqlite:///{tmp_path / 'security.db'}"
    _build_schema(monkeypatch, url, [head])
    _forbid_startup_mutations(monkeypatch)
    _candidate_env(monkeypatch, url)
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "1")
    calls = []

    def _missing_key():
        calls.append("readiness")
        raise mobile_credentials.CredentialConfigurationError(
            "missing retained mobile derivation key version: v0")

    monkeypatch.setattr(mobile_auth, "validate_derivation_key_readiness",
                        _missing_key)
    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        app_package.create_app()
    assert calls == ["readiness"]


def test_candidate_still_rejects_invalid_security_configuration(
        monkeypatch, tmp_path):
    head, _ = _head_and_parent()
    url = f"sqlite:///{tmp_path / 'config.db'}"
    _build_schema(monkeypatch, url, [head])
    _forbid_startup_mutations(monkeypatch)
    _candidate_env(monkeypatch, url)
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "1")
    monkeypatch.setenv("MOBILE_AUTH_DERIVATION_KEYRING", "not-json")
    with pytest.raises(Exception):
        app_package.create_app()


def test_candidate_schema_check_precedes_security_readiness(monkeypatch, tmp_path):
    """A behind schema stops the boot before anything else touches the DB."""
    from app.services import mobile_auth
    _, parent = _head_and_parent()
    url = f"sqlite:///{tmp_path / 'order.db'}"
    _build_schema(monkeypatch, url, [parent])
    _forbid_startup_mutations(monkeypatch)
    _candidate_env(monkeypatch, url)
    monkeypatch.setattr(mobile_auth, "validate_derivation_key_readiness",
                        lambda: pytest.fail("readiness ran on a behind schema"))
    with pytest.raises(SchemaNotReady):
        app_package.create_app()


# ── TEST 11: the worker never owns migrations or release preparation ───────

def _forbid_any_db_startup(monkeypatch):
    monkeypatch.setattr(app_package, "init_database",
                        lambda app: pytest.fail("worker ran init_database"))
    monkeypatch.setattr(app_package, "verify_schema_ready",
                        lambda app: pytest.fail("worker ran schema readiness"))
    from app import db_init
    from app.services import gamification
    monkeypatch.setattr(db_init, "lb_rebuild",
                        lambda: pytest.fail("worker rebuilt the leaderboard"))
    monkeypatch.setattr(gamification, "lb_rebuild",
                        lambda: pytest.fail("worker rebuilt the leaderboard"))


def test_worker_entrypoint_boots_in_skip_mode(monkeypatch):
    import worker
    from app import jobs
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    monkeypatch.setattr(jobs, "_rq", lambda: None)
    with pytest.raises(SystemExit):
        worker.main()
    assert resolve_startup_mode() is StartupMode.SKIP_DB_INIT


def test_worker_job_app_does_no_startup_db_work(monkeypatch):
    from app.jobs import tasks
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    monkeypatch.setattr(tasks, "_worker_app", None)
    _forbid_any_db_startup(monkeypatch)
    try:
        assert tasks._in_app_context(lambda: "ran") == "ran"
        assert os.environ["FITX_SKIP_DB_INIT"] == "1"
    finally:
        if tasks._worker_app is not None:
            _dispose(tasks._worker_app)
        monkeypatch.setattr(tasks, "_worker_app", None)


def test_worker_cannot_be_turned_into_a_migration_owner(monkeypatch):
    """A serving mode leaking into the worker's environment is refused, never
    obeyed: the worker crashes visibly instead of migrating."""
    from app.jobs import tasks
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "self-migrating")
    monkeypatch.setattr(tasks, "_worker_app", None)
    _forbid_any_db_startup(monkeypatch)
    with pytest.raises(StartupModeError):
        tasks._in_app_context(lambda: "ran")
    assert tasks._worker_app is None


# ── release-prepare: the single future migration authority ─────────────────

def test_release_prepare_requires_a_tooling_process(monkeypatch):
    from click.testing import CliRunner

    from app.cli import release_prepare_cmd
    calls = []
    from app import db_init
    monkeypatch.setattr(db_init, "prepare_release",
                        lambda app: calls.append("prepare"))
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    import click
    result = CliRunner().invoke(click.command()(release_prepare_cmd))
    assert result.exit_code != 0
    assert calls == []


def test_release_prepare_mutates_once_then_proves_head_without_rebuild(
        monkeypatch, app):
    from app import db_init
    from app.services import gamification
    calls = []
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    monkeypatch.delenv("FITX_STARTUP_MODE", raising=False)
    monkeypatch.setattr(db_init, "prepare_release",
                        lambda a: calls.append("prepare"))
    monkeypatch.setattr(schema_safety, "verify_schema_ready",
                        lambda a: calls.append("verify") or frozenset({"h"}))
    monkeypatch.setattr(gamification, "lb_rebuild",
                        lambda: pytest.fail("release-prepare rebuilt the leaderboard"))
    monkeypatch.setattr(db_init, "lb_rebuild",
                        lambda: pytest.fail("release-prepare rebuilt the leaderboard"))
    result = app.test_cli_runner().invoke(args=["release-prepare"])
    assert result.exit_code == 0, result.output
    assert calls == ["prepare", "verify"]
    assert "schema at head h" in result.output


def test_prepare_release_contains_no_leaderboard_rebuild():
    import inspect

    from app import db_init
    assert "lb_rebuild" not in inspect.getsource(db_init.prepare_release)
    assert "lb_rebuild" in inspect.getsource(db_init.init_database)


# ── migration lock-timeout configuration (PG behaviour: *_pg.py) ───────────

@pytest.mark.parametrize("raw, expected", [
    (None, 5000), ("", 5000), ("100", 100), ("60000", 60000), (" 2500 ", 2500),
])
def test_migration_lock_timeout_parsing(raw, expected):
    environ = {} if raw is None else {"FITX_MIGRATION_LOCK_TIMEOUT_MS": raw}
    assert schema_safety.migration_lock_timeout_ms(environ) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "99", "60001", "5s", "inf", "1e3"])
def test_migration_lock_timeout_rejects_unbounded_or_invalid(raw):
    with pytest.raises(schema_safety.MigrationSafetyError):
        schema_safety.migration_lock_timeout_ms(
            {"FITX_MIGRATION_LOCK_TIMEOUT_MS": raw})


def test_non_postgres_migrations_keep_the_application_engine():
    engine = sa.create_engine("sqlite://")
    try:
        assert schema_safety.migration_connectable(engine) == (engine, None)
    finally:
        engine.dispose()


def test_postgres_migrations_get_a_dedicated_bounded_engine(monkeypatch):
    from sqlalchemy.pool import NullPool
    monkeypatch.setenv("FITX_MIGRATION_LOCK_TIMEOUT_MS", "1234")
    app_engine = sa.create_engine("postgresql://u:p@127.0.0.1:1/none")
    captured = {}

    def _capture(url, **kwargs):
        captured.update(url=url, **kwargs)
        return "dedicated-engine"

    monkeypatch.setattr(sa, "create_engine", _capture)
    try:
        engine, timeout = schema_safety.migration_connectable(app_engine)
    finally:
        app_engine.dispose()
    assert (engine, timeout) == ("dedicated-engine", 1234)
    assert captured["url"] is app_engine.url
    assert captured["poolclass"] is NullPool
    assert captured["connect_args"] == {"options": "-c lock_timeout=1234"}
