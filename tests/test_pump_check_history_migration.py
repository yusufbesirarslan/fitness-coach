"""The additive index that lets history page without sorting the whole owner."""
import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models import PumpCheck


VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION = VERSIONS / "c1d2e3f4a5b6_add_pump_check_history_index.py"
INDEX = "ix_pump_check_user_captured"
TABLE = "pump_check"
PAGE_SQL = (
    "SELECT public_id, captured_at, body_region, analysis_status, analysis, id "
    "FROM pump_check WHERE user_id = 1 AND captured_at IS NOT NULL "
    "ORDER BY captured_at DESC, id DESC LIMIT 21"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "pump_check_history_index_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _legacy_engine(tmp_path, name="history-index.db"):
    engine = sa.create_engine(f"sqlite:///{tmp_path / name}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("""
                CREATE TABLE pump_check (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    public_id VARCHAR(24),
                    captured_at DATETIME,
                    body_region VARCHAR(20),
                    analysis_status VARCHAR(20),
                    analysis JSON,
                    created_at DATETIME
                )
            """))
            yield connection
    finally:
        engine.dispose()


def _run(connection, operation):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        operation()


def _indexes(connection):
    return {
        item["name"]: tuple(item["column_names"])
        for item in sa.inspect(connection).get_indexes(TABLE)
    }


# ---------------------------------------------------------------------------
# Model / migration parity
# ---------------------------------------------------------------------------

def test_the_model_declares_the_history_index():
    declared = {
        index.name: tuple(column.name for column in index.columns)
        for index in PumpCheck.__table__.indexes
    }

    assert declared[INDEX] == ("user_id", "captured_at", "id")


def test_the_repository_still_has_exactly_one_alembic_head():
    revisions, parents = {}, set()
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        # Both quote styles appear across this repository's history; matching
        # only one silently reports revisions as unparented heads.
        revision = re.search(
            r"""^revision\s*=\s*["']([^"']+)["']""", text, re.M)
        down = re.search(r"^down_revision\s*=\s*(.+)$", text, re.M)
        if not revision:
            continue
        revisions[revision.group(1)] = path.name
        if down:
            parents.update(
                re.findall(r"""["']([0-9a-zA-Z_]+)["']""", down.group(1)))

    assert [r for r in revisions if r not in parents] == ["c1d2e3f4a5b6"]


def test_the_migration_descends_from_the_merged_comparison_head():
    migration = _load_migration()

    assert migration.revision == "c1d2e3f4a5b6"
    assert migration.down_revision == "fa1b2c3d4e5f"


def test_the_migration_adds_nothing_but_the_index():
    """Additive only: no column, no table, no data rewrite."""
    source = MIGRATION.read_text(encoding="utf-8")

    for forbidden in (
            "add_column", "drop_column", "alter_column", "create_table",
            "drop_table", "execute(", "UPDATE ", "DELETE "):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# Upgrade / downgrade
# ---------------------------------------------------------------------------

def test_upgrade_creates_the_index_on_a_legacy_schema(tmp_path):
    with _legacy_engine(tmp_path) as connection:
        assert INDEX not in _indexes(connection)

        _run(connection, _load_migration().upgrade)

        assert _indexes(connection)[INDEX] == ("user_id", "captured_at", "id")


def test_upgrade_is_re_runnable_over_a_create_all_schema(app):
    """A fresh boot runs db.create_all() BEFORE Alembic replays the chain."""
    with app.app_context():
        with db.engine.begin() as connection:
            migration = _load_migration()
            _run(connection, migration.upgrade)
            _run(connection, migration.upgrade)

            assert _indexes(connection)[INDEX] == (
                "user_id", "captured_at", "id")


def test_downgrade_removes_the_index_and_keeps_the_rows(tmp_path):
    with _legacy_engine(tmp_path, "history-downgrade.db") as connection:
        migration = _load_migration()
        connection.execute(sa.text(
            "INSERT INTO pump_check (id, user_id) VALUES (1, 1)"))
        _run(connection, migration.upgrade)

        _run(connection, migration.downgrade)

        assert INDEX not in _indexes(connection)
        assert connection.execute(
            sa.text("SELECT count(*) FROM pump_check")).scalar() == 1
        assert "pump_check" in sa.inspect(connection).get_table_names()


def test_downgrade_is_re_runnable(tmp_path):
    with _legacy_engine(tmp_path, "history-downgrade-twice.db") as connection:
        migration = _load_migration()
        _run(connection, migration.upgrade)

        _run(connection, migration.downgrade)
        _run(connection, migration.downgrade)

        assert INDEX not in _indexes(connection)


# ---------------------------------------------------------------------------
# The reason the index exists
# ---------------------------------------------------------------------------

def test_the_history_page_no_longer_sorts_the_whole_owner(app):
    """Without this index the plan needs a temp b-tree for every page."""
    with app.app_context():
        if db.engine.dialect.name != "sqlite":
            pytest.skip("plan syntax is dialect specific")
        plan = " ".join(
            str(row[-1])
            for row in db.session.execute(sa.text("EXPLAIN QUERY PLAN " + PAGE_SQL))
        )

    assert INDEX in plan
    assert "TEMP B-TREE" not in plan.upper()


def test_the_pre_existing_feed_index_is_untouched():
    declared = {index.name for index in PumpCheck.__table__.indexes}

    assert "ix_pump_check_user_created" in declared
