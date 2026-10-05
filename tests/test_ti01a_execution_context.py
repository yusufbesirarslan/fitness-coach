"""TI-01A frozen V2 contract and persisted cross-transport authority proofs."""
from copy import deepcopy
import json

import pytest

from app.extensions import db
from app.services.workout_session.checkpoint import MAX_REVISION, parse_checkpoint
from app.services.workout_session.context import parse_contract, parse_v2, project_context
from app.services.workout_session.errors import InvalidSessionRequest
from tests.test_sprint14_workout_execution_contract import (
    SQUAT, checkpoint_over_http, durable_state, row_for, save_workout_plan,
    sessions_on, snapshot, start_session_over_http, proof_accepted,
)
from tests.test_mobile_workout_sessions_api import as_mobile, completion_proof


def entry(index=0, rir='2', tempo=None, interval=None):
    return {'exercise_id': SQUAT, 'index': index, 'actual_rir': rir,
            'tempo_adherence': tempo, 'actual_rest': interval}


def body(entries=None, base=None):
    return {'checkpoint': base or snapshot(), 'execution_context': {
        'schema_version': 1, 'sets': [entry()] if entries is None else entries}}


@pytest.fixture
def p0(sessions_on):
    sessions_on.config['FITX_TRAINING_EXECUTION_CONTEXT_ENABLED'] = True
    yield sessions_on


def seed(client, user_id, revision=0):
    save_workout_plan(user_id)
    ref = start_session_over_http(client)
    if revision:
        row_for(user_id).checkpoint_revision = revision
        db.session.commit()
    return ref


def write(client, user, as_mobile, ref, revision, payload=None, key='v2-command-0001', version='2'):
    return client.put(f'/api/v1/training/workout-sessions/{ref}/checkpoint',
                      headers=as_mobile(user, **{'If-Match': str(revision),
                              'Idempotency-Key': key, 'AxisAI-Workout-Contract': version}),
                      json=body() if payload is None else payload)


def state(user_id):
    db.session.expire_all()
    row = row_for(user_id)
    return durable_state(row) + (row.execution_context_data, row.prescription_data,
                                row.last_activity_at, row.updated_at, row.version)


@pytest.mark.parametrize('raw,expected', [(None, 1), ('1', 1), ('2', 2)])
def test_version_explicit_or_legacy_default(raw, expected):
    assert parse_contract(raw) == expected


@pytest.mark.parametrize('raw', ['', ' 2', '2 ', '02', '3', 'v2', '2,1', 2, True])
def test_version_fails_closed(raw):
    with pytest.raises(InvalidSessionRequest):
        parse_contract(raw)


@pytest.mark.parametrize('rir', ['0', '1', '2', '3', '4_plus', None])
def test_rir_tokens(rir):
    parsed = parse_v2(body([entry(rir=rir)]), (SQUAT,))
    assert parsed.execution_context['sets'] == ([] if rir is None else [entry(rir=rir)])


@pytest.mark.parametrize('field,value', [
    ('actual_rir', 0), ('actual_rir', 4), ('actual_rir', 5), ('actual_rir', True),
    ('actual_rir', '4'), ('actual_rir', ''), ('actual_rir', []),
    ('tempo_adherence', '3-0-1'), ('tempo_adherence', 1), ('tempo_adherence', False),
    ('actual_rest', 0), ('actual_rest', {}), ('index', True), ('index', '0'),
    ('index', 20), ('exercise_id', 'ex_missing'), ('exercise_id', []),
])
def test_invalid_context_scalars(field, value):
    request = body()
    request['execution_context']['sets'][0][field] = value
    with pytest.raises(InvalidSessionRequest):
        parse_v2(request, (SQUAT,))


@pytest.mark.parametrize('mutation', ['extra_body', 'extra_context', 'extra_entry', 'missing_entry',
                                     'missing_context', 'null_context', 'schema_bool', 'schema_2',
                                     'duplicate', 'nonexistent', 'uncompleted', 'oversized'])
def test_closed_shape_and_membership(mutation):
    request = body()
    context = request['execution_context']
    if mutation == 'extra_body': request['metadata'] = {}
    if mutation == 'extra_context': context['metadata'] = {}
    if mutation == 'extra_entry': context['sets'][0]['note'] = 'x'
    if mutation == 'missing_entry': del context['sets'][0]['actual_rir']
    if mutation == 'missing_context': del request['execution_context']
    if mutation == 'null_context': request['execution_context'] = None
    if mutation == 'schema_bool': context['schema_version'] = True
    if mutation == 'schema_2': context['schema_version'] = 2
    if mutation == 'duplicate': context['sets'].append(entry())
    if mutation == 'nonexistent': context['sets'][0]['index'] = 1
    if mutation == 'uncompleted': request['checkpoint']['exercises'][0]['sets'][0]['completed'] = False
    if mutation == 'oversized': context['sets'] = [entry()] * 641
    with pytest.raises(InvalidSessionRequest):
        parse_v2(request, (SQUAT,))


def test_fingerprint_versions_context_and_null_normalization():
    base = snapshot()
    v1 = parse_checkpoint(base, (SQUAT,))
    empty = parse_v2(body([], base), (SQUAT,))
    null = parse_v2(body([entry(rir=None)], base), (SQUAT,))
    zero = parse_v2(body([entry(rir='0')], base), (SQUAT,))
    two = parse_v2(body(base=base), (SQUAT,))
    assert empty.fingerprint == null.fingerprint
    assert len({v1.fingerprint, empty.fingerprint, zero.fingerprint, two.fingerprint}) == 4


@pytest.mark.parametrize('bound', ['base', 'context', 'body'])
def test_independent_size_bounds(bound, monkeypatch):
    from app.services.workout_session import checkpoint, context
    target, name = {'base': (checkpoint, 'MAX_SNAPSHOT_BYTES'),
                    'context': (context, 'MAX_CONTEXT_BYTES'),
                    'body': (context, 'MAX_V2_BODY_BYTES')}[bound]
    monkeypatch.setattr(target, name, 1)
    with pytest.raises(InvalidSessionRequest):
        parse_v2(body(), (SQUAT,))


def test_cross_transport_preserve_invalidate_clear_and_replay(client, auth_user, p0, as_mobile):
    ref = seed(client, auth_user.id)
    base = snapshot(sets=2)
    assert checkpoint_over_http(client, ref, 0, base).status_code == 200
    interval = {'seconds': 83, 'method': 'completion_gap', 'quality': 'foreground_contiguous'}
    request = body([entry(), entry(1, interval=interval)], base)
    accepted = write(client, auth_user, as_mobile, ref, 1, request)
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()['execution_context'] == request['execution_context']
    row = row_for(auth_user.id)
    assert json.loads(row.execution_context_data)['bound_revision'] == row.checkpoint_revision == 2
    before = state(auth_user.id)
    replay = write(client, auth_user, as_mobile, ref, 1, request)
    assert replay.status_code == 200 and replay.headers['Idempotency-Replayed'] == 'true'
    assert state(auth_user.id) == before
    changed = deepcopy(request)
    changed['execution_context']['sets'][0]['actual_rir'] = '3'
    assert write(client, auth_user, as_mobile, ref, 2, changed).status_code == 409
    assert state(auth_user.id) == before
    base['elapsed_seconds'] += 1
    # Preservation must also work with P0 OFF.
    p0.config['FITX_TRAINING_EXECUTION_CONTEXT_ENABLED'] = False
    legacy = checkpoint_over_http(client, ref, 2, base, 'legacy-elapsed-01')
    assert legacy.status_code == 200
    assert 'execution_context' not in legacy.get_json()
    assert project_context(row_for(auth_user.id)) == request['execution_context']
    p0.config['FITX_TRAINING_EXECUTION_CONTEXT_ENABLED'] = True
    before = state(auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 2, changed, 'stale-command-01').status_code == 409
    assert state(auth_user.id) == before
    base['exercises'][0]['sets'][0]['reps'] += 1
    assert checkpoint_over_http(client, ref, 3, base, 'legacy-edit-0001').status_code == 200
    assert project_context(row_for(auth_user.id))['sets'] == [entry(1)]
    # Explicit null clears the remaining context; V1 cannot resurrect it.
    clear = body([entry(1, rir=None)], base)
    assert write(client, auth_user, as_mobile, ref, 4, clear, 'clear-command-01').status_code == 200
    base['elapsed_seconds'] += 1
    assert checkpoint_over_http(client, ref, 5, base, 'legacy-final-001').status_code == 200
    assert project_context(row_for(auth_user.id))['sets'] == []


@pytest.mark.parametrize('change', ['reps', 'weight_kg', 'reopen', 'remove_set', 'remove_exercise'])
def test_v1_material_changes_invalidate(client, auth_user, p0, as_mobile, change):
    ref = seed(client, auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 200
    base = snapshot()
    item = base['exercises'][0]['sets'][0]
    if change == 'reps': item['reps'] = 9
    if change == 'weight_kg': item['weight_kg'] = 61
    if change == 'reopen': item['completed'] = False
    if change == 'remove_set': base['exercises'][0]['sets'] = []
    if change == 'remove_exercise': base['exercises'] = []
    assert checkpoint_over_http(client, ref, 1, base, 'legacy-change-01').status_code == 200
    assert project_context(row_for(auth_user.id))['sets'] == []


@pytest.mark.parametrize('version,status', [(None, 200), ('1', 200), ('2', 200), ('3', 400), ('02', 400), ('', 400)])
def test_negotiated_read_shapes(client, auth_user, p0, as_mobile, version, status):
    headers = as_mobile(auth_user)
    if version is not None: headers['AxisAI-Workout-Contract'] = version
    response = client.get('/api/v1/training/workout-sessions/current', headers=headers)
    assert response.status_code == status
    assert 'AxisAI-Workout-Contract' in response.headers['Vary']
    assert response.headers['Cache-Control'] == ('private, no-store' if version == '2' else 'no-store')
    if status == 200:
        assert response.get_json() == ({'session': None, 'execution_context': {'schema_version': 1, 'sets': []},
                                        'prescription': None} if version == '2' else {'session': None})


def test_dark_v2_and_capability_never_write(client, auth_user, sessions_on, as_mobile):
    ref = seed(client, auth_user.id)
    before = state(auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 404
    assert state(auth_user.id) == before
    caps = client.get('/api/v1/training/workout-execution-capabilities', headers=as_mobile(auth_user))
    assert caps.get_json() == {'contract_version': 1, 'checkpoint_versions': [1],
                               'execution_context_enabled': False, 'insights_enabled': False}


@pytest.mark.parametrize('tempo', ['as_prescribed', 'faster', 'slower', 'lost_control'])
def test_tempo_requires_server_target(client, auth_user, p0, as_mobile, tempo):
    ref = seed(client, auth_user.id)
    before = state(auth_user.id)
    result = write(client, auth_user, as_mobile, ref, 0, body([entry(tempo=tempo)]))
    assert result.status_code == (200 if tempo == 'lost_control' else 400)
    if tempo != 'lost_control': assert state(auth_user.id) == before


@pytest.mark.parametrize('base', [MAX_REVISION - 1, MAX_REVISION])
def test_v2_saturation_atomicity(client, auth_user, p0, as_mobile, base):
    ref = seed(client, auth_user.id, base)
    before = state(auth_user.id)
    response = write(client, auth_user, as_mobile, ref, base)
    if base == MAX_REVISION:
        assert response.status_code == 409
        assert response.get_json()['error']['code'] == 'TRAINING_SESSION_REVISION_EXHAUSTED'
        assert state(auth_user.id) == before
    else:
        assert response.status_code == 200
        assert row_for(auth_user.id).checkpoint_revision == MAX_REVISION
        assert json.loads(row_for(auth_user.id).execution_context_data)['bound_revision'] == MAX_REVISION
        before = state(auth_user.id)
        assert write(client, auth_user, as_mobile, ref, base).headers['Idempotency-Replayed'] == 'true'
        assert state(auth_user.id) == before


def test_failed_cas_cannot_partially_persist_context(client, auth_user, p0, as_mobile, monkeypatch):
    from app.services.workout_session import execution
    ref = seed(client, auth_user.id)
    before = state(auth_user.id)
    # Simulate a writer losing after validation; the real race is in PG tests.
    monkeypatch.setattr(execution, 'advance_checkpoint', lambda *a, **kw: 0)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 409
    assert state(auth_user.id) == before


def test_binary_rollback_binding_fails_closed(client, auth_user, p0, as_mobile):
    ref = seed(client, auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 200
    row = row_for(auth_user.id)
    row.checkpoint_revision += 1  # pre-TI binary didn't stamp sidecar
    db.session.commit()
    assert project_context(row)['sets'] == []


def test_v2_context_terminalized_at_max(client, auth_user, p0, as_mobile, proof_accepted):
    ref = seed(client, auth_user.id, MAX_REVISION - 1)
    assert write(client, auth_user, as_mobile, ref, MAX_REVISION - 1).status_code == 200
    before_context = row_for(auth_user.id).execution_context_data
    complete = client.post('/workout/complete', json={'image': 'x', 'location_type': 'salon',
                          'session_id': ref, 'expected_checkpoint_revision': MAX_REVISION})
    assert complete.status_code == 200
    assert row_for(auth_user.id).execution_context_data == before_context
    assert project_context(row_for(auth_user.id))['sets'] == [entry()]
    assert write(client, auth_user, as_mobile, ref, MAX_REVISION).status_code == 409


@pytest.mark.parametrize('change', ['reopen', 'remove', 'weight'])
def test_predecessor_changes_clear_only_dependent_interval(client, auth_user, p0, as_mobile, change):
    ref = seed(client, auth_user.id)
    base = snapshot(sets=2)
    interval = {'seconds': 0, 'method': 'completion_gap', 'quality': 'foreground_contiguous'}
    assert write(client, auth_user, as_mobile, ref, 0, body([entry(1, interval=interval)], base)).status_code == 200
    if change == 'reopen': base['exercises'][0]['sets'][0]['completed'] = False
    if change == 'remove': base['exercises'][0]['sets'].pop(0)
    if change == 'weight': base['exercises'][0]['sets'][0]['weight_kg'] = 70
    assert checkpoint_over_http(client, ref, 1, base, 'preceding-edit-01').status_code == 200
    assert project_context(row_for(auth_user.id))['sets'] == [entry(1)]


def test_normalized_load_preserves_and_version_reuse_conflicts(client, auth_user, p0, as_mobile):
    ref = seed(client, auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 200
    before = state(auth_user.id)
    conflict = checkpoint_over_http(client, ref, 1, snapshot(), 'v2-command-0001')
    assert conflict.status_code == 409
    assert state(auth_user.id) == before
    base = snapshot()
    base['exercises'][0]['sets'][0]['weight_kg'] = 60.01
    assert checkpoint_over_http(client, ref, 1, base, 'normalized-load-01').status_code == 200
    assert project_context(row_for(auth_user.id))['sets'] == [entry()]


def test_v2_read_resume_abandon_projection(client, auth_user, p0, as_mobile):
    ref = seed(client, auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 200
    headers = as_mobile(auth_user, **{'AxisAI-Workout-Contract': '2'})
    for method, path in [('get', ref), ('get', 'current'), ('post', ref + '/resume'),
                         ('post', ref + '/abandon'), ('get', ref)]:
        response = getattr(client, method)('/api/v1/training/workout-sessions/' + path, headers=headers)
        assert response.status_code == 200, response.get_json()
        assert set(response.get_json()) == {'session', 'execution_context', 'prescription'}
        assert response.get_json()['execution_context']['sets'] == [entry()]


@pytest.mark.parametrize('native', [False, True])
def test_immutable_prescription_captured_at_shared_start(client, auth_user, p0, as_mobile, native):
    from app.services import mobile_training
    from app.timeutil import app_today
    plan = save_workout_plan(auth_user.id)
    from tests.test_mobile_workout_sessions_pg import _plan_document
    document = _plan_document(app_today().weekday())
    exercises = document['program'][app_today().weekday()]['egzersizler']
    for item in exercises:
        item.update(set=3, tekrar='8-10', dinlenme='90 sn')
        item['not'] = 'RPE 9; tempo 3-1-1; unstructured cue'
    plan.plan_data = json.dumps(document)
    db.session.commit()
    if native:
        reference = mobile_training.workout_ref(p0.config['SECRET_KEY'], auth_user.id,
                    plan.lineage_id, plan.mutation_version, app_today().weekday())
        response = client.post('/api/v1/training/workout-sessions',
                    headers=as_mobile(auth_user, **{'AxisAI-Workout-Contract': '2'}),
                    json={'workout_ref': reference})
        assert response.status_code == 201, response.get_json()
    else:
        start_session_over_http(client)
        response = client.get('/api/v1/training/workout-sessions/current',
                    headers=as_mobile(auth_user, **{'AxisAI-Workout-Contract': '2'}))
    frozen = response.get_json()['prescription']
    first = frozen['exercises'][0]
    assert first['target_reps'] == {'min': 8, 'max': 10}
    assert first['planned_rest_seconds'] == 90
    assert first['target_load_kg'] is first['target_rir'] is first['target_tempo'] is None
    assert 'not' not in first
    assert first['provenance']['target_reps'] == 'legacy_parsed'
    plan.plan_data = json.dumps({'program': []})
    db.session.commit()
    assert json.loads(row_for(auth_user.id).prescription_data) == frozen


@pytest.mark.parametrize('seconds', [-1, 3601, True, '83', None])
def test_logging_interval_invalid_seconds(seconds):
    interval = {'seconds': seconds, 'method': 'completion_gap', 'quality': 'foreground_contiguous'}
    with pytest.raises(InvalidSessionRequest):
        parse_v2(body([entry(1, interval=interval)], snapshot(sets=2)), (SQUAT,))


def test_v2_order_and_all_null_semantic_identity():
    base = snapshot(sets=2)
    ordered = body([entry(), entry(1)], base)
    reversed_body = body([entry(1), entry()], deepcopy(base))
    reversed_body['checkpoint']['exercises'][0]['sets'].reverse()
    assert parse_v2(ordered, (SQUAT,)).fingerprint == parse_v2(reversed_body, (SQUAT,)).fingerprint


def test_native_v2_completion_keeps_combined_state(client, auth_user, p0, as_mobile, completion_proof):
    import io
    ref = seed(client, auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 200
    before = row_for(auth_user.id).execution_context_data
    response = client.post(f'/api/v1/training/workout-sessions/{ref}/complete',
        headers=as_mobile(auth_user, **{'AxisAI-Workout-Contract': '2', 'If-Match': '1',
                                       'Idempotency-Key': 'native-complete-01'}),
        data={'image': (io.BytesIO(b'png'), 'proof.png'), 'location_type': 'salon'})
    assert response.status_code == 200, response.get_json()
    assert set(response.get_json()) == {'session', 'completion', 'execution_context', 'prescription'}
    assert response.get_json()['execution_context']['sets'] == [entry()]
    assert row_for(auth_user.id).execution_context_data == before


def test_v2_decoded_wire_size_bound_precedes_persistence(client, auth_user, p0, as_mobile):
    ref = seed(client, auth_user.id)
    before = state(auth_user.id)
    response = client.put(f'/api/v1/training/workout-sessions/{ref}/checkpoint',
        headers=as_mobile(auth_user, **{'AxisAI-Workout-Contract': '2', 'If-Match': '0',
                                       'Idempotency-Key': 'large-wire-0001'}),
        content_type='application/json', data=json.dumps(body()) + ' ' * 262144)
    assert response.status_code == 400
    assert response.get_json()['error']['code'] == 'TRAINING_SESSION_INVALID_REQUEST'
    assert state(auth_user.id) == before


def test_projection_failure_keeps_existing_retry_envelope(client, auth_user, p0, as_mobile, monkeypatch):
    from app.blueprints import mobile_workout_sessions as routes
    ref = seed(client, auth_user.id)
    def fail(row):
        raise RuntimeError('synthetic projection read failure')
    monkeypatch.setattr(routes, 'project_context', fail)
    response = write(client, auth_user, as_mobile, ref, 0)
    assert response.status_code == 503
    assert response.get_json()['error']['retryable'] is True
    assert response.headers['Session-Resolution'] == 'retry'
    assert row_for(auth_user.id).checkpoint_revision == 1
    # Lost acknowledgement retries the frozen command/key, never a second write.
    monkeypatch.setattr(routes, 'project_context', project_context)
    replay = write(client, auth_user, as_mobile, ref, 0)
    assert replay.status_code == 200 and replay.headers['Idempotency-Replayed'] == 'true'
    assert replay.get_json()['session']['revision'] == 1


def test_v1_selection_only_preserves_context_with_subset_checkpoint(client, auth_user, p0, as_mobile):
    ref = seed(client, auth_user.id)
    assert write(client, auth_user, as_mobile, ref, 0).status_code == 200
    # Selection indexes the whole workout, while the base snapshot may be a subset.
    selected = snapshot(index=1)
    assert checkpoint_over_http(client, ref, 1, selected, 'selection-only-01').status_code == 200
    assert project_context(row_for(auth_user.id))['sets'] == [entry()]
