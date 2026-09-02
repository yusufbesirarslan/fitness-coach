import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.models import TrainingPlanGenerationOperation


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations" / "versions"
    / "d3e4f5a6b7c8_add_training_plan_generation_operations.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("generation_operation_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _assert_shape(connection):
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns(
        "training_plan_generation_operation")}
    assert columns == {column.name for column in TrainingPlanGenerationOperation.__table__.columns}
    uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("training_plan_generation_operation")
    }
    assert ("user_id", "idempotency_key") in uniques
    indexes = {item["name"]: item for item in inspector.get_indexes(
        "training_plan_generation_operation")}
    assert indexes["ix_training_plan_generation_owner_status"]["column_names"] == [
        "user_id", "status"]
    assert indexes["uq_training_plan_generation_active_owner"]["unique"] == 1


def test_migration_revision_and_empty_schema_round_trip(tmp_path):
    migration = _migration()
    assert migration.revision == "d3e4f5a6b7c8"
    assert migration.down_revision == "c2d3e4f5a6b7"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE training_plan (id INTEGER PRIMARY KEY)"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        _assert_shape(connection)

        with Operations.context(context):
            migration.downgrade()
        inspector = sa.inspect(connection)
        assert not inspector.has_table("training_plan_generation_operation")
        assert inspector.has_table("training_plan")


def test_migration_accepts_create_all_first_schema(tmp_path):
    migration = _migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'create-all.db'}")

    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        TrainingPlanGenerationOperation.__table__.create(connection)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        _assert_shape(connection)
