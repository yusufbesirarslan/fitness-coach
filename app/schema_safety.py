"""R6-01A: startup-mode contract, read-only schema readiness, migration lock bound.

Two concerns that used to be one:

* RELEASE-TIME MUTATION — Alembic upgrade, fresh-schema create_all/stamp,
  reference-data seeds, backfills (``app.db_init.prepare_release``).
* REQUEST-SERVING STARTUP — constructing the real application and proving it
  can serve against the schema that is already there.

``FITX_STARTUP_MODE`` selects how ``create_app()`` relates to the first one:

``self-migrating`` (default, also when unset/empty)
    Today's single-container boot, unchanged: the web process owns release
    preparation (``init_database``) and then runs read-only security readiness.
``read-only``
    Dual-revision candidate boot. NO schema mutation, NO seed/backfill, NO
    leaderboard rebuild. The process proves the database is exactly at the
    migration graph's head(s) without writing anything and fails closed
    otherwise; security readiness still runs.

``FITX_SKIP_DB_INIT=1`` keeps its historical meaning (worker, migration CLI,
tests, tooling: build the app object, touch nothing) and is reported as
``skip-db-init``. It cannot be combined with an explicit ``FITX_STARTUP_MODE``
— two switches describing the same boot are ambiguous, so the combination is
refused rather than resolved by precedence.
"""
import os
from enum import Enum

STARTUP_MODE_ENV = "FITX_STARTUP_MODE"
SKIP_DB_INIT_ENV = "FITX_SKIP_DB_INIT"


class StartupMode(str, Enum):
    SELF_MIGRATING = "self-migrating"
    READ_ONLY = "read-only"
    SKIP_DB_INIT = "skip-db-init"


# Values an operator may write. skip-db-init is NOT one of them: it is only ever
# derived from the legacy FITX_SKIP_DB_INIT=1 switch.
_SELECTABLE = {StartupMode.SELF_MIGRATING.value: StartupMode.SELF_MIGRATING,
               StartupMode.READ_ONLY.value: StartupMode.READ_ONLY}


class StartupModeError(RuntimeError):
    """The startup-mode configuration is invalid or ambiguous (fail closed)."""


def resolve_startup_mode(environ=None):
    """Return the single StartupMode this process boots in, or raise."""
    environ = os.environ if environ is None else environ
    raw = environ.get(STARTUP_MODE_ENV, "")
    skip = environ.get(SKIP_DB_INIT_ENV) == "1"
    if raw == "":
        return StartupMode.SKIP_DB_INIT if skip else StartupMode.SELF_MIGRATING
    mode = _SELECTABLE.get(raw)
    if mode is None:
        raise StartupModeError(
            f"{STARTUP_MODE_ENV}={raw!r} is not one of "
            f"{sorted(_SELECTABLE)}; refusing to guess a boot mode.")
    if skip:
        raise StartupModeError(
            f"{STARTUP_MODE_ENV}={raw} and {SKIP_DB_INIT_ENV}=1 are both set; "
            "they describe different boots. Set exactly one (per service, not "
            "in the shared .env).")
    return mode


# ── Read-only schema readiness ─────────────────────────────────────────────

class SchemaNotReady(RuntimeError):
    """The database is not provably at the code's migration head (fail closed)."""

    def __init__(self, reason, detail):
        super().__init__(f"schema not ready [{reason}]: {detail}")
        self.reason = reason
        self.detail = detail


def migrations_directory(app):
    """The same migrations directory app.db_init upgrades from."""
    return os.path.normpath(os.path.join(app.root_path, "..", "migrations"))


def expected_heads(app):
    """Head revision(s) of the code's migration graph, as Alembic computes them."""
    from alembic.script import ScriptDirectory
    directory = migrations_directory(app)
    if not os.path.isdir(directory):
        raise SchemaNotReady("migrations_unavailable", directory)
    try:
        heads = frozenset(ScriptDirectory(directory).get_heads())
    except Exception as exc:
        raise SchemaNotReady("migration_graph_invalid", repr(exc)) from exc
    if not heads:
        raise SchemaNotReady("migration_graph_invalid", "graph has no head")
    return heads


def _read_current_heads(engine):
    """Read alembic_version without the possibility of writing.

    On PostgreSQL the read runs inside ``SET TRANSACTION READ ONLY`` so even an
    unexpected write path in a library would be rejected by the server; the
    transaction is always rolled back.
    """
    import sqlalchemy as sa
    from alembic.runtime.migration import MigrationContext
    with engine.connect() as connection:
        try:
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            has_table = sa.inspect(connection).has_table("alembic_version")
            current = (frozenset(MigrationContext.configure(connection)
                                 .get_current_heads())
                       if has_table else None)
        finally:
            connection.rollback()
    return has_table, current


def verify_schema_ready(app):
    """Prove, read-only, that the database is exactly at the code's head(s).

    Uses Alembic's own graph (``ScriptDirectory.get_heads``) and its own reader
    (``MigrationContext.get_current_heads``) — no second interpretation of
    Alembic state. Every failure raises SchemaNotReady; nothing is upgraded,
    stamped or created here.
    """
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError
    from app.extensions import db

    heads = expected_heads(app)
    with app.app_context():
        try:
            has_table, current = _read_current_heads(db.engine)
        except SchemaNotReady:
            raise
        except Exception as exc:
            raise SchemaNotReady("unreadable", repr(exc)) from exc
    if not has_table:
        raise SchemaNotReady("version_table_missing",
                             "alembic_version does not exist")
    if not current:
        raise SchemaNotReady("version_table_empty",
                             "alembic_version has no revision")
    script = ScriptDirectory(migrations_directory(app))
    for revision in sorted(current):
        try:
            known = script.get_revision(revision)
        except CommandError:
            known = None
        if known is None:
            raise SchemaNotReady(
                "unknown_revision",
                f"database revision {revision} is not in this code's graph")
    if current != heads:
        raise SchemaNotReady(
            "not_at_head",
            f"database at {sorted(current)}, code requires {sorted(heads)}")
    return heads


# ── Bounded migration lock wait ────────────────────────────────────────────
# A DDL statement that cannot get its lock does not just wait itself: every
# later query on that table queues BEHIND it. While another revision is serving
# that is a full stall of the table, so the wait must be short and must end in
# failure (Alembic runs the whole upgrade in one PostgreSQL transaction, so a
# timeout rolls back everything). 5 s: below the 7–10 s web-recreate gap R6 is
# removing (a timed-out attempt is never worse than today's deploy), far above
# the lock hold time of a normal request transaction, and short enough that
# the deploy health gate sees a clean failure instead of a hang.
MIGRATION_LOCK_TIMEOUT_ENV = "FITX_MIGRATION_LOCK_TIMEOUT_MS"
DEFAULT_MIGRATION_LOCK_TIMEOUT_MS = 5000
MIN_MIGRATION_LOCK_TIMEOUT_MS = 100
MAX_MIGRATION_LOCK_TIMEOUT_MS = 60000


class MigrationSafetyError(RuntimeError):
    """Migration safety configuration is invalid (fail closed)."""


def migration_lock_timeout_ms(environ=None):
    environ = os.environ if environ is None else environ
    raw = environ.get(MIGRATION_LOCK_TIMEOUT_ENV, "").strip()
    if raw == "":
        return DEFAULT_MIGRATION_LOCK_TIMEOUT_MS
    try:
        value = int(raw)
    except ValueError:
        raise MigrationSafetyError(
            f"{MIGRATION_LOCK_TIMEOUT_ENV}={raw!r} is not an integer") from None
    if not MIN_MIGRATION_LOCK_TIMEOUT_MS <= value <= MAX_MIGRATION_LOCK_TIMEOUT_MS:
        # 0 would mean "wait forever" to PostgreSQL — never allowed.
        raise MigrationSafetyError(
            f"{MIGRATION_LOCK_TIMEOUT_ENV}={value} outside "
            f"[{MIN_MIGRATION_LOCK_TIMEOUT_MS}, {MAX_MIGRATION_LOCK_TIMEOUT_MS}]")
    return value


def migration_connectable(app_engine):
    """The engine Alembic migrations run on.

    PostgreSQL: a DEDICATED NullPool engine whose every connection starts with
    ``lock_timeout`` set as a libpq startup option. The setting therefore exists
    only on migration connections and dies with them — it can never be returned
    to the request pool. Other dialects (SQLite tests) keep the app engine,
    exactly as before (an in-memory SQLite URL names a different database per
    engine).
    """
    if app_engine.dialect.name != "postgresql":
        return app_engine, None
    import sqlalchemy as sa
    from sqlalchemy.pool import NullPool
    timeout_ms = migration_lock_timeout_ms()
    engine = sa.create_engine(
        app_engine.url, poolclass=NullPool,
        connect_args={"options": f"-c lock_timeout={timeout_ms}"})
    return engine, timeout_ms


def assert_lock_timeout(connection, timeout_ms):
    """Fail closed if the server did not apply the bound (e.g. a pooler that
    strips startup options). Leaves the connection outside a transaction so
    Alembic's begin_transaction() still owns and commits the migration."""
    effective = connection.exec_driver_sql(
        "SELECT setting FROM pg_settings WHERE name = 'lock_timeout'").scalar()
    connection.commit()
    if str(effective) != str(timeout_ms):
        raise MigrationSafetyError(
            f"migration connection lock_timeout is {effective!r}, "
            f"expected {timeout_ms}; refusing an unbounded migration")
