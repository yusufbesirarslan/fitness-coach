"""Real multi-connection PostgreSQL note CAS and database constraints."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import ExerciseNote, User
from app.services import exercise_notes as notes

pytestmark = pytest.mark.pg_concurrency
EXERCISE = 'ex_barbell_back_squat'


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get('PG_TEST_DATABASE_URL')
    if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1' or not url:
        pytest.skip('requires opt-in isolated PostgreSQL test database')
    monkeypatch.setenv('DATABASE_URL', url)
    from app import create_app
    app = create_app()
    with app.app_context():
        assert db.engine.dialect.name == 'postgresql'
        db.drop_all()
        db.create_all()
        db.session.add(User(username='note-pg', email='note-pg@example.com', cognito_sub='note-pg-sub'))
        db.session.commit()
        owner = User.query.one().id
    yield app, owner
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def race(app, owner, commands):
    barrier = threading.Barrier(len(commands))
    def write(command):
        with app.app_context():
            # Different connection per writer; synchronize after connection acquisition.
            db.session.execute(sa.text('SELECT 1'))
            barrier.wait(timeout=10)
            try:
                return notes.set_exercise_note(owner, EXERCISE,
                    {'text': command[0]}, str(command[1]))
            except notes.NoteConflict:
                return 'conflict'
            finally:
                db.session.remove()
    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        return list(pool.map(write, commands))


@pytest.mark.parametrize('same', [False, True])
def test_concurrent_first_create_one_row(pg_app, same):
    app, owner = pg_app
    results = race(app, owner, [('Seat 4', 0), ('Seat 4' if same else 'Bench pin 3', 0)])
    assert sum(r == 'conflict' for r in results) == (0 if same else 1)
    with app.app_context():
        row = ExerciseNote.query.one()
        assert row.user_id == owner and row.exercise_id == EXERCISE
        assert row.text in {'Seat 4', 'Bench pin 3'}
        assert row.revision == 1


@pytest.mark.parametrize('texts', [('Update A', 'Update B'), ('Update', None)])
def test_concurrent_update_and_clear(pg_app, texts):
    app, owner = pg_app
    with app.app_context():
        notes.set_exercise_note(owner, EXERCISE, {'text': 'Initial'}, '0')
    results = race(app, owner, [(text, 1) for text in texts])
    assert sum(r == 'conflict' for r in results) == 1
    winner = next(r['note'] for r in results if isinstance(r, dict))
    with app.app_context():
        row = ExerciseNote.query.one()
        assert row.revision == 2
        assert row.text == winner['text']
        # Neither clear nor competing update can restore the pre-clear revision.
        with pytest.raises(notes.NoteConflict):
            notes.set_exercise_note(owner, EXERCISE, {'text': 'ABA'}, '1')


def test_database_cascade_fk_unique_and_bounds(pg_app):
    app, owner = pg_app
    with app.app_context():
        notes.set_exercise_note(owner, EXERCISE, {'text': 'Seat 4'}, '0')
        row = ExerciseNote.query.one()
        values = dict(user_id=owner, exercise_id=EXERCISE, text='Duplicate', revision=1,
                      created_at=row.created_at, updated_at=row.updated_at)
        with pytest.raises(sa.exc.IntegrityError):
            with db.session.begin_nested():
                db.session.execute(sa.insert(ExerciseNote).values(**values))
        for changes in ({'user_id': owner + 1000}, {'text': 'x' * 501}, {'revision': -1}, {'revision': 1000000000}):
            with pytest.raises(sa.exc.DBAPIError):
                with db.session.begin_nested():
                    db.session.execute(sa.insert(ExerciseNote).values(
                        **{**values, 'exercise_id': 'ex_barbell_deadlift', **changes}))
        db.session.execute(sa.delete(User).where(User.id == owner))
        db.session.commit()
        assert ExerciseNote.query.count() == 0


def test_deletion_races_first_create_without_orphans(pg_app):
    app, owner = pg_app
    barrier = threading.Barrier(2)
    def create():
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                notes.set_exercise_note(owner, EXERCISE, {'text': 'Seat 4'}, '0')
            except notes.NoteUnavailable:
                pass  # Parent deletion won; FK refusal is translated safely.
            finally:
                db.session.remove()
    def delete():
        with app.app_context():
            barrier.wait(timeout=10)
            db.session.execute(sa.delete(User).where(User.id == owner))
            db.session.commit()
            db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create), pool.submit(delete)]
        for future in futures:
            future.result(timeout=20)
    with app.app_context():
        assert User.query.count() == 0
        assert ExerciseNote.query.count() == 0
