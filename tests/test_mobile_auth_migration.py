import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models import (
    MobileAccessCredential,
    MobileAuthSession,
    MobileRefreshCredential,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / 'migrations'
    / 'versions'
    / 'c7d8e9f0a1b2_add_mobile_auth_sessions.py'
)
TABLES = {
    'mobile_auth_session',
    'mobile_access_credential',
    'mobile_refresh_credential',
}


def _load_migration():
    spec = importlib.util.spec_from_file_location('mobile_auth_migration', MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, operation):
    with engine.begin() as connection:
        connection.execute(sa.text(
            'CREATE TABLE IF NOT EXISTS user (id INTEGER PRIMARY KEY)'))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            operation()


def _model_columns(model):
    return {column.name for column in model.__table__.columns}


def test_migration_creates_model_equivalent_tables_on_fresh_database(tmp_path):
    migration = _load_migration()
    database_path = tmp_path / 'mobile-fresh.db'
    engine = sa.create_engine(f'sqlite:///{database_path}')

    _run(engine, migration.upgrade)

    inspector = sa.inspect(engine)
    assert TABLES <= set(inspector.get_table_names())
    assert {column['name'] for column in inspector.get_columns(
        'mobile_auth_session')} == _model_columns(MobileAuthSession)
    assert {column['name'] for column in inspector.get_columns(
        'mobile_access_credential')} == _model_columns(MobileAccessCredential)
    assert {column['name'] for column in inspector.get_columns(
        'mobile_refresh_credential')} == _model_columns(MobileRefreshCredential)
    engine.dispose()


def test_migration_is_idempotent_over_create_all_schema(app):
    migration = _load_migration()

    _run(db.engine, migration.upgrade)
    _run(db.engine, migration.upgrade)

    assert TABLES <= set(sa.inspect(db.engine).get_table_names())


def test_migration_fails_closed_on_incompatible_existing_table(tmp_path):
    migration = _load_migration()
    database_path = tmp_path / 'mobile-bad.db'
    engine = sa.create_engine(f'sqlite:///{database_path}')
    with engine.begin() as connection:
        connection.execute(sa.text(
            'CREATE TABLE mobile_auth_session (id INTEGER PRIMARY KEY)'))

    with pytest.raises(RuntimeError, match='incompatible'):
        _run(engine, migration.upgrade)
    engine.dispose()


def test_migration_fails_closed_on_wrong_named_index_shape(tmp_path):
    migration = _load_migration()
    database_path = tmp_path / 'mobile-wrong-index.db'
    engine = sa.create_engine(f'sqlite:///{database_path}')
    _run(engine, migration.upgrade)
    with engine.begin() as connection:
        connection.execute(sa.text(
            'DROP INDEX uq_mobile_access_credential_hash'))
        connection.execute(sa.text(
            'CREATE INDEX uq_mobile_access_credential_hash '
            'ON mobile_access_credential (generation)'))

    with pytest.raises(RuntimeError, match='wrong columns or uniqueness'):
        _run(engine, migration.upgrade)
    engine.dispose()


def test_migration_downgrade_removes_only_additive_mobile_tables(tmp_path):
    migration = _load_migration()
    database_path = tmp_path / 'mobile-down.db'
    engine = sa.create_engine(f'sqlite:///{database_path}')
    _run(engine, migration.upgrade)

    _run(engine, migration.downgrade)

    inspector = sa.inspect(engine)
    assert not (TABLES & set(inspector.get_table_names()))
    assert inspector.has_table('user')
    engine.dispose()
