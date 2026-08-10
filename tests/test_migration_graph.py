import importlib.util

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
import ast
from pathlib import Path

from app.models import MealLog


def _literal_assignment(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    return None


def test_alembic_migrations_have_single_head():
    revisions = {}
    down_revisions = set()
    versions_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"

    for path in versions_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _literal_assignment(tree, "revision")
        down_revision = _literal_assignment(tree, "down_revision")
        assert revision, f"{path.name} is missing revision"
        revisions[revision] = path.name
        if isinstance(down_revision, (tuple, list)):
            down_revisions.update(item for item in down_revision if item)
        elif down_revision:
            down_revisions.add(down_revision)

    heads = sorted(set(revisions) - down_revisions)

    # Sprint 7 PR3 adds the workout_session table as the single new head off
    # bb88cc99dd00 (the PR2 base). One head only — no branching.
    assert heads == ["d8e9f0a1b2c3"]


def test_meal_idempotency_fingerprint_migration_round_trips_only_nullable_column(
        tmp_path):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "d8e9f0a1b2c3_add_meal_log_idempotency_fingerprint.py"
    )
    spec = importlib.util.spec_from_file_location(
        "meal_idempotency_fingerprint_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fingerprint.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE meal_log (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                idempotency_key VARCHAR(64),
                CONSTRAINT uq_meal_log_user_idempotency
                    UNIQUE (user_id, idempotency_key)
            )
        """))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        columns = {c["name"]: c for c in sa.inspect(connection).get_columns("meal_log")}
        assert columns["idempotency_fingerprint"]["nullable"] is True
        assert columns["idempotency_fingerprint"]["type"].length == 64
        assert {
            tuple(constraint["column_names"])
            for constraint in sa.inspect(connection).get_unique_constraints("meal_log")
        } == {("user_id", "idempotency_key")}
        assert sa.inspect(connection).get_indexes("meal_log") == []

        with Operations.context(context):
            migration.downgrade()
        assert "idempotency_fingerprint" not in {
            c["name"] for c in sa.inspect(connection).get_columns("meal_log")
        }

        with Operations.context(context):
            migration.upgrade()
        assert "idempotency_fingerprint" in {
            c["name"] for c in sa.inspect(connection).get_columns("meal_log")
        }


def test_meal_log_maps_nullable_idempotency_fingerprint_without_new_constraint():
    column = MealLog.__table__.columns["idempotency_fingerprint"]

    assert column.nullable is True
    assert column.type.length == 64
    assert {constraint.name for constraint in MealLog.__table__.constraints
            if isinstance(constraint, sa.UniqueConstraint)} == {
                "uq_meal_log_user_idempotency"
            }


def test_fingerprint_migration_accepts_fresh_model_created_schema(tmp_path):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "d8e9f0a1b2c3_add_meal_log_idempotency_fingerprint.py"
    )
    spec = importlib.util.spec_from_file_location(
        "fresh_schema_fingerprint_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh-schema.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE meal_log (
                id INTEGER PRIMARY KEY,
                idempotency_fingerprint VARCHAR(64)
            )
        """))
        context = MigrationContext.configure(connection)

        with Operations.context(context):
            migration.upgrade()

        columns = [c["name"] for c in sa.inspect(connection).get_columns("meal_log")]
        assert columns.count("idempotency_fingerprint") == 1


def test_activity_trigger_revision_is_postgresql_guarded():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "bb22cc33dd44_activity_calorie_trigger.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert source.count('op.get_bind().dialect.name != "postgresql"') == 2
    assert "CREATE OR REPLACE FUNCTION calc_activity_calories" in source
    assert "CREATE TRIGGER trg_calc_activity" in source
    assert "DROP TRIGGER IF EXISTS trg_calc_activity ON daily_activity" in source
    assert "DROP FUNCTION IF EXISTS calc_activity_calories()" in source


def test_meal_idempotency_migration_upgrades_deployed_pre_column_schema(tmp_path):
    """The additive migration must work when a deployed ledger lacks the column."""
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "bb88cc99dd00_add_meal_log_idempotency.py"
    )
    spec = importlib.util.spec_from_file_location("meal_idempotency_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pre_column.db'}")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE meal_log (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                ogun VARCHAR(100) NOT NULL,
                yemekler TEXT NOT NULL
            )
        """))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        inspector = sa.inspect(connection)
        assert "idempotency_key" in {c["name"] for c in inspector.get_columns("meal_log")}
        assert any(
            constraint["name"] == "uq_meal_log_user_idempotency"
            for constraint in inspector.get_unique_constraints("meal_log")
        )
