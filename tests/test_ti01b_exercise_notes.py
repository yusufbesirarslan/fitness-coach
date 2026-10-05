"""Frozen TI-00 §9 contract, ownership and independent authority proofs."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import ExerciseNote, TrainingPlan, WorkoutSession
from app.services import exercise_notes as notes
from app.services import exercise_catalog
from app.timeutil import audit_clock
from test_mobile_workout_sessions_api import (
    owner, stranger, as_mobile, plan, workout_ref, completion_proof,
    _start, _checkpoint, _snapshot, _abandon, _complete, FIXED_NOW, EXERCISE_A,
)

PATH = f"/api/v1/training/exercises/{EXERCISE_A}/note"


@pytest.fixture(autouse=True)
def enabled(app):
    app.config['FITX_TRAINING_EXECUTION_CONTEXT_ENABLED'] = True
    app.config['FITX_WORKOUT_SESSIONS_ENABLED'] = True


def put(client, headers, text, revision=0):
    return client.put(PATH, headers={**headers, 'If-Match': str(revision)}, json={'text': text})


def test_crud_retry_clear_and_timestamps(client, owner, as_mobile):
    headers = as_mobile(owner)
    assert client.get(PATH, headers=headers).json == {'note': {
        'exercise_id': EXERCISE_A, 'text': None, 'revision': 0, 'updated_at': None}}
    with audit_clock(FIXED_NOW):
        first = put(client, headers, '  Seat 4\r\nNeutral handles  ')
    assert first.status_code == 200
    assert first.json['note'] == {'exercise_id': EXERCISE_A,
        'text': 'Seat 4\nNeutral handles', 'revision': 1, 'updated_at': '2026-07-23T12:00:00Z'}
    created = ExerciseNote.query.one().created_at
    for revision in (0, 1):
        assert put(client, headers, 'Seat 4\nNeutral handles', revision).json == first.json
    with audit_clock(FIXED_NOW + timedelta(minutes=1)):
        second = put(client, headers, 'Bench pin 3', 1)
    assert second.json['note']['revision'] == 2
    assert ExerciseNote.query.one().created_at == created
    assert ExerciseNote.query.one().updated_at > created
    assert client.get(PATH, headers=headers).json == second.json
    conflict = put(client, headers, 'Different', 0)
    assert conflict.status_code == 409
    assert conflict.headers['Session-Resolution'] == 'reread'
    clear = put(client, headers, None, 2)
    assert clear.json['note']['text'] is None
    assert clear.json['note']['revision'] == 3
    assert ExerciseNote.query.count() == 1
    assert client.get(PATH, headers=headers).json == clear.json
    assert put(client, headers, None, 2).json == clear.json
    assert put(client, headers, 'ABA', 1).status_code == 409
    assert client.delete(PATH, headers=headers).status_code == 405


@pytest.mark.parametrize('body', [None, [], {}, {'note': 'x'}, {'text': 1}, {'text': True},
    {'text': []}, {'text': {}}, {'text': 'x', 'user_id': 1}, {'text': 'x' * 501},
    {'text': '😀' * 501}, {'text': '\ud800'}, {'text': 'x\x00'}, {'text': '\x1b'},
    {'text': 'x\ry'}, {'text': '\x7f'}, {'text': ' ' * 501}])
def test_strict_validation(client, owner, as_mobile, body):
    response = client.put(PATH, headers={**as_mobile(owner), 'If-Match': '0'}, json=body)
    assert response.status_code == 400
    assert response.json['error']['code'] == 'TRAINING_NOTE_INVALID'
    assert ExerciseNote.query.count() == 0


@pytest.mark.parametrize('text', [None, '', ' \n\t ', '😀' * 500, '<script>alert(1)</script>', 'a\tb\nc'])
def test_valid_text_and_clear(client, owner, as_mobile, text):
    response = put(client, as_mobile(owner), text)
    assert response.status_code == 200
    assert response.json['note']['text'] == (text.strip() or None if text else None)
    assert response.json['note']['revision'] == 1


@pytest.mark.parametrize('revision', [None, '', '-1', '1000000000', 'W/"1"', '１', '1.0', 'true'])
def test_revision_validation(client, owner, as_mobile, revision):
    headers = as_mobile(owner)
    if revision is not None:
        headers['If-Match'] = revision
    assert client.put(PATH, headers=headers, json={'text': 'Seat 4'}).status_code == 400
    assert ExerciseNote.query.count() == 0


def test_revision_exhaustion_and_retry(owner):
    notes.set_exercise_note(owner.id, EXERCISE_A, {'text': 'Seat 4'}, '0')
    row = ExerciseNote.query.one()
    row.revision = notes.MAX_REVISION
    db.session.commit()
    assert notes.set_exercise_note(owner.id, EXERCISE_A, {'text': 'Seat 4'}, '999999998')['note']['revision'] == notes.MAX_REVISION
    with pytest.raises(notes.NoteExhausted):
        notes.set_exercise_note(owner.id, EXERCISE_A, {'text': None}, '999999999')
    assert ExerciseNote.query.one().text == 'Seat 4'


def test_cross_user_read_update_clear_are_isolated(client, owner, stranger, as_mobile):
    a = as_mobile(owner)
    original = put(client, a, 'Owner A private note').json
    b = as_mobile(stranger)
    assert client.get(PATH, headers=b).json['note']['text'] is None
    assert put(client, b, 'Owner B', 1).status_code == 409
    assert put(client, b, None, 1).status_code == 409
    assert put(client, b, 'Owner B').status_code == 200
    assert put(client, b, None, 1).status_code == 200
    a = as_mobile(owner)
    assert client.get(PATH, headers=a).json == original
    assert ExerciseNote.query.count() == 2


@pytest.mark.parametrize('identity', ['Bench Press', 'ex_nonexistent', 'generated name', '123'])
def test_no_shadow_identity(client, owner, as_mobile, identity):
    response = client.put(f'/api/v1/training/exercises/{identity}/note',
        headers={**as_mobile(owner), 'If-Match': '0'}, json={'text': 'Seat 4'})
    assert response.status_code == 400
    assert ExerciseNote.query.count() == 0


def test_retired_exercise_fails_closed_but_retains_note(owner, monkeypatch):
    notes.set_exercise_note(owner.id, EXERCISE_A, {'text': 'Seat 4'}, '0')
    catalog = exercise_catalog.load_exercise_catalog()
    retired = replace(catalog.by_id[EXERCISE_A], active=False)
    changed = replace(catalog, by_id={**catalog.by_id, EXERCISE_A: retired})
    monkeypatch.setattr(exercise_catalog, 'load_exercise_catalog', lambda: changed)
    with pytest.raises(notes.NoteError):
        notes.get_exercise_note(owner.id, EXERCISE_A)
    with pytest.raises(notes.NoteError):
        notes.set_exercise_note(owner.id, EXERCISE_A, {'text': 'Changed'}, '1')
    assert ExerciseNote.query.one().text == 'Seat 4'


def test_unique_pair_and_independent_pairs(owner, stranger):
    # Exercise the DB constraint directly, independent of safe-upsert SQL.
    db.session.add_all([
        ExerciseNote(user_id=owner.id, exercise_id=EXERCISE_A, text='A', revision=1),
        ExerciseNote(user_id=stranger.id, exercise_id=EXERCISE_A, text='B', revision=1),
        ExerciseNote(user_id=owner.id, exercise_id='ex_barbell_deadlift', text='C', revision=1),
    ])
    db.session.commit()
    row = ExerciseNote.query.filter_by(user_id=owner.id, exercise_id=EXERCISE_A).one()
    db.session.add(ExerciseNote(user_id=owner.id, exercise_id=EXERCISE_A, text='duplicate',
        revision=1, created_at=row.created_at, updated_at=row.updated_at))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert ExerciseNote.query.count() == 3


def test_account_purge_removes_notes(owner, stranger):
    from app.cli import _purge_user
    from app.models import User
    notes.set_exercise_note(owner.id, EXERCISE_A, {'text': 'A'}, '0')
    notes.set_exercise_note(stranger.id, EXERCISE_A, {'text': 'B'}, '0')
    _purge_user(db.session.get(User, owner.id))
    db.session.commit()
    assert ExerciseNote.query.filter_by(user_id=owner.id).count() == 0
    assert ExerciseNote.query.filter_by(user_id=stranger.id).one().text == 'B'


@pytest.mark.parametrize('terminal', ['complete', 'abandon'])
@pytest.mark.parametrize('contract', ['1', '2'])
def test_workout_and_note_authorities_are_independent(client, owner, as_mobile,
        workout_ref, completion_proof, terminal, contract):
    headers = as_mobile(owner)
    ref = _start(client, headers, workout_ref).json['session']['session_ref']
    session = WorkoutSession.query.one()
    original_plan = TrainingPlan.query.one().plan_data
    for text, revision in [('Seat 4', 0), ('Neutral handles', 1), (None, 2)]:
        before = (session.checkpoint_revision, session.checkpoint_data,
                  session.checkpoint_fingerprint)
        assert put(client, headers, text, revision).status_code == 200
        session = WorkoutSession.query.populate_existing().one()
        assert (session.checkpoint_revision, session.checkpoint_data,
                session.checkpoint_fingerprint) == before
        assert TrainingPlan.query.one().plan_data == original_plan
    saved = put(client, headers, 'Persistent', 3).json
    if contract == '1':
        response = _checkpoint(client, headers, ref, 0, 'ti01b-checkpoint-01')
    else:
        response = client.put(f'/api/v1/training/workout-sessions/{ref}/checkpoint',
            headers={**headers, 'If-Match': '0', 'Idempotency-Key': 'ti01b-checkpoint-01',
                     'AxisAI-Workout-Contract': '2'},
            json={'checkpoint': _snapshot(), 'execution_context': {
                'schema_version': 1, 'sets': [{'exercise_id': EXERCISE_A, 'index': 0,
                    'actual_rir': '2', 'tempo_adherence': None, 'actual_rest': None}]}})
    assert response.status_code == 200
    assert client.get(PATH, headers=headers).json == saved
    session = WorkoutSession.query.populate_existing().one()
    before = (session.checkpoint_revision, session.checkpoint_data,
              session.checkpoint_fingerprint, session.execution_context_data,
              session.prescription_data)
    assert put(client, headers, 'Still persistent', 4).status_code == 200
    session = WorkoutSession.query.populate_existing().one()
    assert (session.checkpoint_revision, session.checkpoint_data,
            session.checkpoint_fingerprint, session.execution_context_data,
            session.prescription_data) == before
    saved = client.get(PATH, headers=headers).json
    if terminal == 'complete':
        assert _complete(client, headers, ref, 1).status_code == 200
    else:
        assert _abandon(client, headers, ref, reason='user_cancelled').status_code == 200
        assert _start(client, headers, workout_ref).status_code == 201
    assert client.get(PATH, headers=headers).json == saved
    # Removal/replacement of plan rows cannot own note lifetime.
    TrainingPlan.query.delete()
    db.session.commit()
    assert client.get(PATH, headers=headers).json == saved


def test_auth_and_dark_readiness(client, owner, as_mobile, app):
    assert client.get(PATH).status_code == 401
    headers = as_mobile(owner)
    app.config['FITX_TRAINING_EXECUTION_CONTEXT_ENABLED'] = False
    assert client.get(PATH, headers=headers).status_code == 404
    assert put(client, headers, 'Seat 4').status_code == 404
    app.config['FITX_TRAINING_EXECUTION_CONTEXT_ENABLED'] = True
    app.config['FITX_WORKOUT_SESSIONS_ENABLED'] = False
    assert client.get(PATH, headers=headers).status_code == 404


def test_private_response_and_no_prose_in_logs(client, owner, as_mobile, caplog):
    response = put(client, as_mobile(owner), 'secret-note-marker')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert set(response.json['note']) == {'exercise_id', 'text', 'revision', 'updated_at'}
    assert 'secret-note-marker' not in caplog.text


def test_missing_schema_is_dark(client, owner, as_mobile):
    headers = as_mobile(owner)
    ExerciseNote.__table__.drop(db.engine)
    response = client.get(PATH, headers=headers)
    assert response.status_code == 404
    assert response.json['error']['code'] == 'TRAINING_SESSION_NOT_FOUND'


def test_account_fk_cascades_without_purge(owner):
    import sqlalchemy as sa
    from app.models import User
    notes.set_exercise_note(owner.id, EXERCISE_A, {'text': 'Seat 4'}, '0')
    db.session.execute(sa.delete(User).where(User.id == owner.id))
    db.session.commit()
    assert ExerciseNote.query.count() == 0


def test_sentry_excludes_note_events_locals_bodies_and_breadcrumbs(app):
    from app.observability import _note_safe_event, _note_safe_breadcrumb
    event = {'request': {'url': 'https://axisai.example' + PATH, 'data': {'text': 'secret'}},
             'exception': {'values': [{'stacktrace': {'frames': [{'vars': {'text': 'secret'}}]}}]}}
    assert _note_safe_event(event, {}) is None
    with app.test_request_context(PATH, method='PUT', json={'text': 'secret'}):
        assert _note_safe_event({'extra': {'text': 'secret'}}, {}) is None
        assert _note_safe_breadcrumb({'message': 'secret'}, {}) is None
    other = {'request': {'url': 'https://axisai.example/health'}}
    assert _note_safe_event(other, {}) is other
    crumb = {'message': 'normal'}
    assert _note_safe_breadcrumb(crumb, {}) is crumb
