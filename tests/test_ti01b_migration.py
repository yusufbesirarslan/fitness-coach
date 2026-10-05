"""Migration-only schema creation and deliberate downgrade loss."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_note_migration_round_trip(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/e2f3a4b5c6d7_persistent_exercise_notes.py'
    spec = importlib.util.spec_from_file_location('ti01b_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'notes.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
        conn.execute(sa.text('INSERT INTO user (id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            inspector = sa.inspect(conn)
            assert {c['name'] for c in inspector.get_columns('exercise_note')} == {
                'id', 'user_id', 'exercise_id', 'text', 'revision', 'created_at', 'updated_at'}
            assert inspector.get_unique_constraints('exercise_note') == [{
                'name': 'uq_exercise_note_owner_exercise', 'column_names': ['user_id', 'exercise_id']}]
            fk = inspector.get_foreign_keys('exercise_note')[0]
            assert fk['referred_table'] == 'user' and fk['options']['ondelete'] == 'CASCADE'
            assert len(inspector.get_check_constraints('exercise_note')) == 2
            conn.execute(sa.text("INSERT INTO exercise_note VALUES (1, 1, 'ex_barbell_back_squat', 'Seat 4', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
            migration.upgrade()
            assert conn.execute(sa.text('SELECT revision FROM exercise_note')).scalar_one() == 1
            migration.downgrade()
            assert not sa.inspect(conn).has_table('exercise_note')
            assert conn.execute(sa.text('SELECT id FROM user')).scalar_one() == 1
    engine.dispose()


def test_existing_table_without_unique_authority_fails_closed(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/e2f3a4b5c6d7_persistent_exercise_notes.py'
    spec = importlib.util.spec_from_file_location('ti01b_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    from app.models import ExerciseNote
    metadata = sa.MetaData()
    sa.Table('user', metadata, sa.Column('id', sa.Integer, primary_key=True))
    table = ExerciseNote.__table__.to_metadata(metadata)
    unique = next(c for c in table.constraints if isinstance(c, sa.UniqueConstraint))
    table.constraints.remove(unique)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'invalid-notes.db'}")
    with engine.begin() as conn:
        metadata.create_all(conn)
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match='uniqueness missing'):
                migration.upgrade()
    engine.dispose()
