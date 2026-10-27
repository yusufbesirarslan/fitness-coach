"""Additive dispatch-evidence upgrade is strict, reentrant and preserves rows."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


@pytest.fixture
def migration():
    path=Path(__file__).resolve().parents[1]/'migrations/versions/b5e7a9c1d3f0_allowance_dispatch_evidence.py'
    spec=importlib.util.spec_from_file_location('dispatch_migration',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_rows_preserved_and_repeat_upgrade_is_exact(migration, monkeypatch):
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE ai_usage_reservation (id INTEGER PRIMARY KEY, key_digest VARCHAR(64) NOT NULL)')
        connection.exec_driver_sql("INSERT INTO ai_usage_reservation VALUES (1, 'unchanged')")
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
        migration.upgrade();migration.upgrade()
        assert connection.exec_driver_sql('SELECT * FROM ai_usage_reservation').one()==(1,'unchanged',None,None,None,None)
        columns=sa.inspect(connection).get_columns('ai_usage_reservation')
        assert len(columns)==6
        migration.downgrade()
        assert len(sa.inspect(connection).get_columns('ai_usage_reservation'))==6
    engine.dispose()


@pytest.mark.parametrize('ddl',[
    'request_fingerprint VARCHAR(32)', 'request_fingerprint VARCHAR(64) NOT NULL',
    'physical_attempts TEXT', 'physical_attempt_limit INTEGER DEFAULT 1000000',
    'canonical_committed INTEGER',
])
def test_malformed_existing_evidence_fails_closed(migration, monkeypatch, ddl):
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE ai_usage_reservation (id INTEGER PRIMARY KEY, '+ddl+')')
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError,match='schema mismatch'):
            migration.upgrade()
    engine.dispose()


def aggregate_migration():
    path=Path(__file__).resolve().parents[1]/'migrations/versions/c6f8b0d2e4a1_aggregate_physical_budget.py'
    spec=importlib.util.spec_from_file_location('aggregate_migration',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_aggregate_upgrade_does_not_backfill_historical_authority(monkeypatch):
    migration = aggregate_migration()
    assert migration.down_revision == 'b5e7a9c1d3f0'
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE ai_usage_reservation (id INTEGER PRIMARY KEY, key_digest VARCHAR(64) NOT NULL)')
        connection.exec_driver_sql("INSERT INTO ai_usage_reservation VALUES (1, 'unchanged')")
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
        migration.upgrade();migration.upgrade();migration.downgrade()
        assert connection.exec_driver_sql('SELECT * FROM ai_usage_reservation').one()==(1,'unchanged',None,None,None,None,None)
        assert len(sa.inspect(connection).get_columns('ai_usage_reservation')) == 7
    engine.dispose()


@pytest.mark.parametrize('ddl', ['physical_budget VARCHAR(8)', 'physical_input_reserved INTEGER',
    'physical_output_reserved BIGINT NOT NULL', 'physical_cost_reserved BIGINT DEFAULT 0',
    'physical_images_reserved TEXT'])
def test_aggregate_schema_mismatch_is_not_waived(monkeypatch,ddl):
    migration = aggregate_migration()
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE ai_usage_reservation (id INTEGER PRIMARY KEY, '+ddl+')')
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError,match='schema mismatch'): migration.upgrade()
    engine.dispose()
