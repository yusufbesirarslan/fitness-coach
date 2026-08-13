import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.models import PumpCheck


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "e9f0a1b2c3d4_add_canonical_pump_check_fields.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("canonical_pump_check_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_table(connection):
    connection.execute(sa.text("""
        CREATE TABLE pump_check (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            image_key VARCHAR(300),
            location_type VARCHAR(50),
            description VARCHAR(200),
            valid BOOLEAN,
            fallback BOOLEAN,
            date_key VARCHAR(10),
            created_at DATETIME,
            CONSTRAINT uq_pump_check_day UNIQUE (user_id, date_key)
        )
    """))


def test_canonical_pump_check_model_maps_only_required_additive_fields():
    columns = PumpCheck.__table__.columns

    assert columns["captured_at"].nullable is True
    assert columns["body_region"].type.length == 20
    assert columns["analysis_status"].type.length == 20
    assert columns["analysis"].nullable is True
    assert columns["analysis_version"].type.length == 40
    assert columns["idempotency_key"].type.length == 64
    assert columns["idempotency_fingerprint"].type.length == 64
    assert {constraint.name for constraint in PumpCheck.__table__.constraints
            if isinstance(constraint, sa.UniqueConstraint)} == {
                "uq_pump_check_day",
                "uq_pump_check_user_idempotency",
            }


def test_additive_migration_preserves_legacy_row_without_fabricated_backfill(tmp_path):
    migration = _load_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pump-check.db'}")
    with engine.begin() as connection:
        _legacy_table(connection)
        connection.execute(sa.text("""
            INSERT INTO pump_check
                (id, user_id, location_type, description, valid, fallback, date_key, created_at)
            VALUES
                (7, 3, 'gym', 'legacy note', 1, 0, '2026-08-12', '2026-08-12 10:00:00')
        """))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        row = connection.execute(sa.text("""
            SELECT captured_at, body_region, analysis_status, analysis,
                   analysis_version, idempotency_key, idempotency_fingerprint
            FROM pump_check WHERE id = 7
        """)).mappings().one()
        assert dict(row) == {
            "captured_at": None,
            "body_region": None,
            "analysis_status": None,
            "analysis": None,
            "analysis_version": None,
            "idempotency_key": None,
            "idempotency_fingerprint": None,
        }
        uniques = {item["name"] for item in sa.inspect(connection).get_unique_constraints("pump_check")}
        assert uniques == {"uq_pump_check_day", "uq_pump_check_user_idempotency"}


def test_additive_migration_downgrade_removes_only_canonical_fields(tmp_path):
    migration = _load_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'pump-check-down.db'}")
    with engine.begin() as connection:
        _legacy_table(connection)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()

        columns = {item["name"] for item in sa.inspect(connection).get_columns("pump_check")}
        assert {"id", "user_id", "image_key", "location_type", "description",
                "valid", "fallback", "date_key", "created_at"} <= columns
        assert "analysis" not in columns
        assert {item["name"] for item in sa.inspect(connection).get_unique_constraints("pump_check")} == {
            "uq_pump_check_day"
        }
