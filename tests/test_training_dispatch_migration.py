"""Forward-only additive marker: no invented historical no-dispatch evidence."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def _migration():
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/b0d3e4f5a6b7_training_dispatch_boundary.py'
    spec = importlib.util.spec_from_file_location('dispatch_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_additive_marker_preserves_historical_uncertainty_and_old_writes():
    migration = _migration()
    assert migration.down_revision == 'a9c1d2e3f4b5'
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE training_plan_generation_operation (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('INSERT INTO training_plan_generation_operation (id) VALUES (1)')
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            with pytest.raises(RuntimeError):
                migration.downgrade()
        connection.exec_driver_sql('INSERT INTO training_plan_generation_operation (id) VALUES (2)')
        assert connection.exec_driver_sql('SELECT provider_dispatch_possible FROM training_plan_generation_operation ORDER BY id').all() == [(None,), (None,)]


def test_marker_migration_refuses_incompatible_existing_column():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE training_plan_generation_operation (id INTEGER PRIMARY KEY, provider_dispatch_possible TEXT)')
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError):
                _migration().upgrade()
