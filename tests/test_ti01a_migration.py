"""Additive TI-01A migration preserves existing execution and is reversible."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_existing_rows_upgrade_and_disposable_downgrade(tmp_path):
    path = Path('migrations/versions/e1f2a3b4c5d6_execution_context_v2.py')
    spec = importlib.util.spec_from_file_location('ti01a_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE workout_session (id INTEGER PRIMARY KEY, checkpoint_data TEXT)'))
            connection.execute(sa.text("INSERT INTO workout_session VALUES (1, 'unchanged')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                columns = {c['name']: c for c in sa.inspect(connection).get_columns('workout_session')}
                for name in ('execution_context_data', 'prescription_data'):
                    assert columns[name]['nullable']
                assert connection.execute(sa.text('SELECT checkpoint_data, execution_context_data, prescription_data FROM workout_session')).one() == ('unchanged', None, None)
                migration.downgrade()
            assert {c['name'] for c in sa.inspect(connection).get_columns('workout_session')} == {'id', 'checkpoint_data'}
            assert connection.execute(sa.text('SELECT checkpoint_data FROM workout_session')).scalar_one() == 'unchanged'
    finally:
        engine.dispose()
