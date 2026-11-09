"""Migration e1a5f0c3d2b4: expand-only, reentrant, refuses a populated downgrade.

Runs on SQLite always and also on PostgreSQL when PG_TEST_DATABASE_URL names a
disposable database (each run uses its own schema there).
"""
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

MIGRATION = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
             "e1a5f0c3d2b4_fatsecret_retention_phase1.py")


def _migration():
    spec = importlib.util.spec_from_file_location("retention_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


def _engines(tmp_path):
    yield "sqlite", sa.create_engine(f"sqlite:///{tmp_path / 'existing.db'}"), None
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if url.startswith(("postgresql://", "postgresql+psycopg2://")):
        schema = "ret_" + uuid.uuid4().hex[:10]
        admin = sa.create_engine(url)
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        engine = sa.create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            yield "postgresql", engine, schema
        finally:
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


EXISTING = (
    'CREATE TABLE "user" (id INTEGER PRIMARY KEY)',
    "CREATE TABLE meal_log (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
    "ogun VARCHAR(100) NOT NULL, yemekler TEXT NOT NULL, kalori FLOAT, tarih VARCHAR(10) NOT NULL, "
    "source VARCHAR(20))",
    "CREATE TABLE custom_meal_item (id INTEGER PRIMARY KEY, custom_meal_id INTEGER NOT NULL, "
    "food_name VARCHAR(200) NOT NULL, calories FLOAT NOT NULL)",
    'INSERT INTO "user" (id) VALUES (1)',
    "INSERT INTO meal_log (id, user_id, ogun, yemekler, kalori, tarih, source) "
    "VALUES (1, 1, 'Öğle', 'kept', 300, '2026-09-12', 'search')",
)


def test_upgrade_is_additive_reentrant_and_downgrade_refuses_populated(tmp_path):
    ran = []
    for dialect, engine, _schema in _engines(tmp_path):
        ran.append(dialect)
        with engine.begin() as connection:
            for statement in EXISTING:
                connection.execute(sa.text(statement))
            migration = _migration()
            _run(connection, migration.upgrade)
            _run(connection, migration.upgrade)          # reentrant: verified, not recreated

            inspector = sa.inspect(connection)
            columns = {c["name"]: c for c in inspector.get_columns("meal_log")}
            for name in ("provider_items", "provider_acquired_at", "provider_expired_at"):
                assert columns[name]["nullable"] is True
            items = {c["name"]: c for c in inspector.get_columns("custom_meal_item")}
            assert items["provider_acquired_at"]["nullable"] is True
            assert sorted(inspector.get_pk_constraint("meal_log_retained_total")[
                "constrained_columns"]) == ["tarih", "user_id"]
            # Existing rows untouched: no backfill, provenance stays NULL.
            row = connection.execute(sa.text(
                "SELECT yemekler, kalori, provider_acquired_at FROM meal_log WHERE id = 1")).one()
            assert tuple(row) == ("kept", 300, None)

            connection.execute(sa.text(
                "INSERT INTO meal_log_retained_total (user_id, tarih, energy_kcal, protein_g, "
                "carbohydrate_g, fat_g, item_count, updated_at) "
                "VALUES (1, '2026-09-12', 300, 0, 0, 0, 1, CURRENT_TIMESTAMP)"))
            with pytest.raises(RuntimeError, match="retained FatSecret totals exist"):
                _run(connection, migration.downgrade)
            connection.execute(sa.text("DELETE FROM meal_log_retained_total"))
            _run(connection, migration.downgrade)
            assert "meal_log_retained_total" not in sa.inspect(connection).get_table_names()
        engine.dispose()
    assert "sqlite" in ran


def test_create_all_first_schema_is_accepted_by_the_verifier(app):
    """A fresh boot creates the objects from models first; the migration accepts them."""
    from app.extensions import db
    with db.engine.begin() as connection:
        _run(connection, _migration().upgrade)


def test_a_not_null_provenance_column_fails_closed(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'bad.db'}")
    with engine.begin() as connection:
        for statement in EXISTING[:3]:
            connection.execute(sa.text(statement))
        connection.execute(sa.text(
            "ALTER TABLE meal_log ADD COLUMN provider_acquired_at DATETIME NOT NULL DEFAULT 0"))
        with pytest.raises(RuntimeError, match="must be nullable"):
            _run(connection, _migration().upgrade)
    engine.dispose()
