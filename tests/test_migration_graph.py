import importlib.util

import pytest
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

    # Adaptive Coaching S1 PR4 adds the confirmation-proposal table as the sole
    # new head, chained off Sprint 10 PR4A's c1d2e3f4a5b6. One head only.
    assert heads == ["c2d3e4f5a6b7"]


def test_pr4_canonical_exercise_authority_adds_no_migration():
    """The exercise catalog is bundled data, not a table.

    Sprint 11 PR4 makes a server-owned catalog the single authority on exercise
    identity and stores nothing new: no exercise table, no ``exercise_id``
    column on ``WorkoutLog``, no backfill of historical rows. Identity lives in
    the reviewed, version-controlled JSON asset and inside the plan document
    that already existed. That is what makes the whole PR deployable with an
    unchanged Alembic graph — and it is a claim worth failing on rather than
    a sentence in a document, because "we added one small table" is exactly
    how a no-migration PR stops being one.
    """
    versions_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    revision_files = sorted(path.name for path in versions_dir.glob("*.py"))

    assert len(revision_files) == 36
    assert not any("exercise" in name for name in revision_files)

    catalog_path = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "training_assets" / "exercises.json"
    )
    assert catalog_path.is_file()

    from app.models import WorkoutLog

    assert "exercise_id" not in WorkoutLog.__table__.columns


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


def _plan_history_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "b3c4d5e6f7a8_add_plan_mutation_history.py"
    )
    spec = importlib.util.spec_from_file_location(
        "plan_mutation_history_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _deployed_plan_schema(connection):
    connection.execute(sa.text(
        "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80))"))
    connection.execute(sa.text("""
        CREATE TABLE training_plan (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            plan_data TEXT NOT NULL,
            score FLOAT,
            created_at DATETIME
        )
    """))
    connection.execute(sa.text("INSERT INTO user (id, username) VALUES (1, 'a')"))
    connection.execute(sa.text("INSERT INTO user (id, username) VALUES (2, 'b')"))
    for plan_id, user_id in ((1, 1), (2, 2), (3, 2)):
        connection.execute(
            sa.text("INSERT INTO training_plan (id, user_id, plan_data) "
                    "VALUES (:i, :u, '{\"program\": []}')"),
            {"i": plan_id, "u": user_id},
        )


def test_plan_history_migration_round_trips_and_backfills_distinct_lineages(tmp_path):
    """upgrade → downgrade → upgrade against a deployed pre-column schema.

    The backfill must give every existing plan its OWN lineage. One shared
    constant would be far easier to write and would make two users' plans look
    like a single lineage, which is precisely how an undo could reach across
    them.
    """
    migration = _plan_history_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'plan-history.db'}")

    with engine.begin() as connection:
        _deployed_plan_schema(connection)
        context = MigrationContext.configure(connection)

        with Operations.context(context):
            migration.upgrade()

        columns = {c["name"]: c for c in sa.inspect(connection).get_columns("training_plan")}
        assert columns["lineage_id"]["type"].length == 64
        assert columns["lineage_id"]["nullable"] is False
        assert columns["mutation_version"]["nullable"] is False
        plan_indexes = {ix["name"]: ix for ix in sa.inspect(connection).get_indexes(
            "training_plan")}
        assert plan_indexes["uq_training_plan_lineage_id"]["unique"]

        rows = connection.execute(sa.text(
            "SELECT lineage_id, mutation_version FROM training_plan")).fetchall()
        lineages = [row[0] for row in rows]
        assert len(set(lineages)) == 3
        assert all(lineage and len(lineage) >= 32 for lineage in lineages)
        # Existing plans are "version 0, never mutated" — not invented history.
        assert {row[1] for row in rows} == {0}
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM plan_mutation_record")).scalar() == 0

        journal = {c["name"] for c in sa.inspect(connection).get_columns(
            "plan_mutation_record")}
        assert {"public_id", "plan_lineage_id", "before_snapshot", "after_snapshot",
                "before_fingerprint", "after_fingerprint", "idempotency_key",
                "reverts_mutation_id", "outcome"} <= journal
        uniques = {
            tuple(uc["column_names"])
            for uc in sa.inspect(connection).get_unique_constraints(
                "plan_mutation_record")
        }
        assert ("user_id", "idempotency_key") in uniques
        assert ("reverts_mutation_id",) in uniques

        with Operations.context(context):
            migration.downgrade()
        remaining = {c["name"] for c in sa.inspect(connection).get_columns("training_plan")}
        assert "lineage_id" not in remaining and "mutation_version" not in remaining
        assert not sa.inspect(connection).has_table("plan_mutation_record")

        with Operations.context(context):
            migration.upgrade()
        assert sa.inspect(connection).has_table("plan_mutation_record")
        assert connection.execute(sa.text(
            "SELECT COUNT(DISTINCT lineage_id) FROM training_plan")).scalar() == 3


def test_plan_history_migration_accepts_a_create_all_built_schema(tmp_path):
    """The boot path runs ``db.create_all()`` first, so this revision routinely
    meets objects that already exist. It must verify rather than blow up — and
    must not blanket-skip, which would hide a half-built journal."""
    from app.models import PlanMutationRecord, TrainingPlan

    migration = _plan_history_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'plan-created.db'}")

    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80))"))
        TrainingPlan.__table__.create(connection)
        PlanMutationRecord.__table__.create(connection)
        context = MigrationContext.configure(connection)

        with Operations.context(context):
            migration.upgrade()

        names = [c["name"] for c in sa.inspect(connection).get_columns("training_plan")]
        assert names.count("lineage_id") == 1
        assert names.count("mutation_version") == 1


def test_plan_history_migration_refuses_an_incompatible_journal(tmp_path):
    """Fail closed: a table that shares the name but not the shape must not be
    reported as a successful upgrade."""
    migration = _plan_history_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'plan-broken.db'}")

    with engine.begin() as connection:
        _deployed_plan_schema(connection)
        connection.execute(sa.text(
            "CREATE TABLE plan_mutation_record (id INTEGER PRIMARY KEY)"))
        context = MigrationContext.configure(connection)

        with pytest.raises(RuntimeError):
            with Operations.context(context):
                migration.upgrade()


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
