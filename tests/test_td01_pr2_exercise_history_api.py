"""TD-01 PR2 deterministic wire, owner, error, gate and read-only acceptance."""
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.feature_flags import FLAGS_BY_KEY, FeatureFlagConfigurationError, resolve_rollout_flags
from app.models import User
from app.services import exercise_performance_history as authority
from app.services.exercise_catalog import CatalogConfigurationError, ExerciseIdentityInvalid, ExerciseUnknown
from app.services.exercise_performance_history import selection
from app.services.exercise_performance_history.models import (
    Coverage, ExerciseHistory, Occurrence, PerformedSet, ProjectionInvariantError, UnavailableSession,
)
from app.services import mobile_exercise_history as wire
from app.blueprints import mobile_exercise_history as route
from app.timeutil import audit_clock
from tests.exercise_history_support import (
    ANCHOR, ANCHOR_NOW, SQUAT, BENCH, candidate, day, persist, straight, snapshot, utc_noon,
)
from test_lp16b_native_checkin_api import real_bearer  # noqa: F401
from test_default_rate_limit_identity import tight_default_limiter  # noqa: F401

FLAG = 'FITX_EXERCISE_PERFORMANCE_HISTORY_ENABLED'
CAPABILITY = '/api/v1/training/exercise-history-capabilities'
PATH = f'/api/v1/training/exercises/{SQUAT}/performance-history'
FIXTURES = Path(__file__).parent / 'fixtures/td01_pr2'


def get_history(client, headers=None, path=PATH, **kwargs):
    with audit_clock(ANCHOR_NOW):
        return client.get(path, headers=headers, **kwargs)


@pytest.fixture
def ready(app):
    app.config.update({FLAG: True, 'FITX_WORKOUT_SESSIONS_ENABLED': True})
    return app


@pytest.fixture
def owner(make_user):
    return make_user('td01-pr2-owner')


def _projection(rows=()):
    return selection.select_history(SQUAT, ANCHOR, tuple(rows), len(rows) == 64)


def _error(response, status, code):
    assert response.status_code == status
    body = response.get_json()
    assert set(body) == {'error'}
    assert set(body['error']) == {'code', 'message', 'retryable', 'request_id'}
    assert body['error']['code'] == code
    assert body['error']['request_id']
    assert body['error']['retryable'] is (status == 503)
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Session-Resolution' not in response.headers
    return body


@pytest.mark.parametrize('state,rows', [
    ('no_history', ()),
    ('available', (candidate('recorded', day(-1), [(SQUAT, [(0, True, 8, 60.0), (1, False, 9, 70.0), (2, True, None, 0.0)])]),)),
    ('undetermined', (candidate('missing', day(-2), revision=0, checkpoint_data=None),)),
    ('available_with_gaps', (candidate('recorded', day(-1)), candidate('missing', day(-2), revision=0, checkpoint_data=None))),
])
def test_closed_golden_four_states(ready, client, owner, real_bearer, state, rows):
    for row in rows:
        persist(owner.id, row)
    response = get_history(client, real_bearer(owner))
    assert response.status_code == 200
    assert response.get_json() == json.loads((FIXTURES / f'{state}.json').read_text())
    assert len(response.data) <= wire.MAX_PAYLOAD_BYTES
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'ETag' not in response.headers
    assert 'Session-Resolution' not in response.headers


@pytest.mark.parametrize('checkpoint,revision,reason', [
    (None, 0, 'checkpoint_missing'), (None, 1, 'checkpoint_invalid'),
    ('', 0, 'checkpoint_invalid'), (' ', 3, 'checkpoint_invalid'),
    ('{x', 3, 'checkpoint_invalid'), ('[]', 3, 'checkpoint_invalid'),
    (json.dumps(snapshot([(SQUAT, [(0, True, 8, 60), (0, True, 8, 60)])])), 3, 'checkpoint_invalid'),
    (json.dumps(snapshot([(SQUAT, straight(60, 8)), (BENCH, [(0, 'true', 8, 60)])])), 3, 'checkpoint_invalid'),
])
def test_whole_checkpoint_refusal(ready, client, owner, real_bearer, checkpoint, revision, reason):
    persist(owner.id, candidate('corrupt', day(-1), checkpoint_data=checkpoint, revision=revision))
    body = get_history(client, real_bearer(owner)).get_json()['exercise_performance_history']
    assert body['state'] == 'undetermined'
    assert body['occurrences'] == [] and body['latest_valid_occurrence_ref'] is None
    assert [m['reason'] for m in body['coverage']['unavailable_sessions']] == [reason]


@pytest.mark.parametrize('corrupt_offset', [-1, -12])
def test_gaps_before_or_after_eighth_are_preserved(ready, client, owner, real_bearer, corrupt_offset):
    for i in range(2, 12):
        persist(owner.id, candidate(f'valid-{i}', day(-i)))
    persist(owner.id, candidate('corrupt', day(corrupt_offset), checkpoint_data='bad'))
    body = get_history(client, real_bearer(owner)).get_json()['exercise_performance_history']
    assert body['state'] == 'available_with_gaps'
    assert len(body['occurrences']) == 8
    assert body['coverage']['occurrence_limit_reached'] is True
    assert len(body['coverage']['unavailable_sessions']) == 1
    assert body['latest_valid_occurrence_ref']['session_ref'] == 'valid-2'


@pytest.mark.parametrize('count', [64, 65])
def test_candidate_bound_never_claims_absence(ready, client, owner, real_bearer, count):
    for i in range(count):
        persist(owner.id, candidate(f'row-{i}', day(-1), [(BENCH, straight(40, 8))], second=i % 60))
    body = get_history(client, real_bearer(owner)).get_json()['exercise_performance_history']
    assert body['state'] == 'undetermined'
    assert body['coverage']['scanned_sessions'] == 64
    assert body['coverage']['scan_limit_reached'] is True


def test_dates_lifecycle_identity_and_null_zero(ready, client, owner, real_bearer):
    for ref, offset in [('outside', -57), ('edge', -56), ('today', 0), ('future', 1)]:
        persist(owner.id, candidate(ref, day(offset), [(SQUAT, [(2, True, 0, None), (5, True, None, 0.0)])]))
    persist(owner.id, candidate('no-time', day(-1), completed_at=None))
    persist(owner.id, candidate('cross-date', day(-2), completed_at=utc_noon(day(-1))))
    for status in ('active', 'abandoned'):
        persist(owner.id, candidate(status, day(-1)), status=status)
    body = get_history(client, real_bearer(owner)).get_json()['exercise_performance_history']
    assert body['state'] == 'available'
    assert [o['session_ref'] for o in body['occurrences']] == ['today', 'edge']
    assert body['occurrences'][0]['sets'] == [{'index': 2, 'reps': 0, 'weight_kg': None}, {'index': 5, 'reps': None, 'weight_kg': 0.0}]
    assert body['coverage']['excluded_cross_date'] == body['coverage']['excluded_missing_completed_at'] == 1
    assert body['window'] == {'start_date': day(-56).isoformat(), 'end_date': ANCHOR.isoformat(), 'timezone': 'Europe/Istanbul'}


def test_real_credentials_owner_isolation_and_ignored_inputs(ready, client, owner, make_user, real_bearer):
    stranger = make_user('td01-pr2-stranger')
    a, b = real_bearer(owner), real_bearer(stranger)
    persist(owner.id, candidate('only-owner', day(-1)))
    baseline = get_history(client, a).data
    persist(stranger.id, candidate('foreign-corrupt', day(-1), checkpoint_data='bad'))
    assert get_history(client, a).data == baseline
    spoof = dict(a, **{'user_id': str(stranger.id), 'AxisAI-Workout-Contract': '99'})
    assert get_history(client, spoof, path=PATH+f'?user_id={stranger.id}&limit=999&date=1900-01-01&cursor=x', json={'user_id': stranger.id}).data == baseline
    other = get_history(client, b).get_json()['exercise_performance_history']
    assert other['state'] == 'undetermined' and not other['occurrences']
    assert 'only-owner' not in json.dumps(other)


@pytest.mark.parametrize('history_on,sessions_on', [(False, False), (False, True), (True, False)])
@pytest.mark.parametrize('path', [PATH, CAPABILITY])
def test_off_precedes_view_auth_and_read(app, client, monkeypatch, history_on, sessions_on, path):
    app.config.update({FLAG: history_on, 'FITX_WORKOUT_SESSIONS_ENABLED': sessions_on})
    monkeypatch.setattr(route, 'build_exercise_history', lambda *a: pytest.fail('disabled read'))
    for headers in (None, {'Authorization': 'Basic bad'}, {'Authorization': 'Bearer invalid'}):
        assert get_history(client, headers, path=path).status_code == 404


def test_flag_lifecycle_and_auth_registration(monkeypatch):
    flag = FLAGS_BY_KEY[FLAG]
    assert flag.default is False and flag.lifecycle == 'shipped_dark'
    assert flag.depends_on == ('FITX_WORKOUT_SESSIONS_ENABLED', 'MOBILE_AUTH_ENABLED')
    assert resolve_rollout_flags({})[FLAG] is False
    assert resolve_rollout_flags({FLAG: '1'})[FLAG] is True
    for value in ('true', 'yes', '1 ', 'on'):
        with pytest.raises(FeatureFlagConfigurationError):
            resolve_rollout_flags({FLAG: value})
    from app import create_app
    monkeypatch.setenv(FLAG, '1')
    assert create_app().config[FLAG] is True
    monkeypatch.setenv('MOBILE_AUTH_ENABLED', '0')
    app = create_app()
    assert not any(r.rule.startswith('/api/v1') for r in app.url_map.iter_rules())
    assert app.test_client().get(PATH).status_code == 404


@pytest.mark.parametrize('headers', [None, {'Authorization': 'Bearer invalid'}, {'Authorization': 'Basic x'}])
@pytest.mark.parametrize('path', [PATH, CAPABILITY])
def test_ready_requires_bearer(ready, client, headers, path):
    assert get_history(client, headers, path=path).status_code == 401


def test_capability_no_history_sql_and_old_contract_unchanged(ready, client, owner, real_bearer, monkeypatch):
    headers = real_bearer(owner)
    monkeypatch.setattr(route, 'build_exercise_history', lambda *a: pytest.fail('capability must not read history'))
    response = get_history(client, headers, path=CAPABILITY)
    assert response.get_json() == {'contract_version': 1, 'exercise_performance_history_enabled': True, 'history_contract_version': 1}
    old = get_history(client, headers, path='/api/v1/training/workout-execution-capabilities').get_json()
    assert set(old) == {'contract_version', 'checkpoint_versions', 'execution_context_enabled', 'insights_enabled'}
    ready.config[FLAG] = False
    assert get_history(client, headers, path=CAPABILITY).status_code == get_history(client, headers).status_code == 404
    ready.config[FLAG] = True
    assert get_history(client, headers, path=CAPABILITY).status_code == 200


@pytest.mark.parametrize('identity,status,code', [
    ('malformed', 400, 'TRAINING_HISTORY_INVALID_REQUEST'),
    ('ex_' + 'a' * 65, 400, 'TRAINING_HISTORY_INVALID_REQUEST'),
    ('ex_unassigned', 404, 'TRAINING_HISTORY_EXERCISE_NOT_FOUND'),
])
def test_identity_errors_before_history_select(ready, client, owner, real_bearer, monkeypatch, identity, status, code):
    monkeypatch.setattr(authority.queries, 'load_candidates', lambda *a: pytest.fail('invalid identity read'))
    _error(get_history(client, real_bearer(owner), path=f'/api/v1/training/exercises/{identity}/performance-history'), status, code)


def test_retired_identity_remains_readable(ready, client, owner, real_bearer, monkeypatch):
    from app.services import exercise_catalog
    catalog = exercise_catalog.load_exercise_catalog()
    retired = replace(catalog.by_id[SQUAT], active=False)
    monkeypatch.setattr(exercise_catalog, 'load_exercise_catalog', lambda: replace(catalog, by_id={**catalog.by_id, SQUAT: retired}))
    persist(owner.id, candidate('retired', day(-1)))
    assert get_history(client, real_bearer(owner)).get_json()['exercise_performance_history']['state'] == 'available'


@pytest.mark.parametrize('error,category', [
    (OperationalError('private sql', {}, Exception('secret')), 'read_failed'),
    (CatalogConfigurationError('secret'), 'catalog_unavailable'),
    (ProjectionInvariantError('secret'), 'invariant_failed'),
    (RecursionError('secret'), 'invariant_failed'),
    (RuntimeError('secret'), 'invariant_failed'),
])
def test_failure_refuses_without_facts_or_sensitive_logs(ready, client, owner, real_bearer, monkeypatch, caplog, error, category):
    headers = real_bearer(owner)
    def fail(*args):
        raise error
    monkeypatch.setattr(route, 'build_exercise_history', fail)
    caplog.set_level(logging.ERROR)
    response = get_history(client, headers)
    _error(response, 503, 'TRAINING_HISTORY_TEMPORARILY_UNAVAILABLE')
    assert response.headers['Retry-After'] == '15'
    assert f'event={category}' in caplog.text
    assert 'secret' not in caplog.text + response.get_data(as_text=True)
    assert headers['Authorization'] not in caplog.text


def test_pathological_json_and_telemetry_failure(ready, client, owner, real_bearer, monkeypatch):
    persist(owner.id, candidate('deep', day(-1), checkpoint_data='[' * 2000 + '0' + ']' * 2000))
    headers = real_bearer(owner)
    monkeypatch.setattr(route.runtime_metrics, 'increment', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('metrics')))
    _error(get_history(client, headers), 503, 'TRAINING_HISTORY_TEMPORARILY_UNAVAILABLE')


def assert_read_only_request(client, headers):
    """Auth reads may occur; history is exactly one five-column owner SELECT."""
    statements, commits, flushes = [], [], []
    def sql(*args):
        statements.append(args[2])
    def commit(*args):
        commits.append(True)
    def flush(*args):
        flushes.append(True)
    event.listen(db.engine, 'before_cursor_execute', sql)
    event.listen(db.session(), 'after_commit', commit)
    event.listen(db.session(), 'before_flush', flush)
    try:
        response = get_history(client, headers)
        assert response.status_code == 200
        assert not commits and not flushes
        assert not db.session.new and not db.session.dirty and not db.session.deleted
        assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
        history_sql = [s for s in statements if 'FROM workout_session' in s]
        assert len(history_sql) == 1
        select = history_sql[0]
        assert 'LIMIT' in select and 'workout_session.user_id =' in select
        assert 'workout_log' not in select and 'JOIN' not in select.upper()
        assert 'prescription_data' not in select and 'execution_context_data' not in select
        return response, statements
    finally:
        event.remove(db.engine, 'before_cursor_execute', sql)
        event.remove(db.session(), 'after_commit', commit)
        event.remove(db.session(), 'before_flush', flush)


def test_read_only_transport(ready, client, owner, real_bearer):
    persist(owner.id, candidate('recorded', day(-1)))
    assert_read_only_request(client, real_bearer(owner))


@pytest.mark.parametrize('path', [PATH, CAPABILITY])
def test_history_reads_never_run_shared_maintenance(ready, client, owner, real_bearer, monkeypatch, path):
    from app import hooks
    monkeypatch.setattr(hooks, '_rollover_throttle_passed', lambda *a: pytest.fail('rollover throttle reached'))
    monkeypatch.setattr(hooks, '_purge_throttle_passed', lambda *a: pytest.fail('purge throttle reached'))
    assert get_history(client, real_bearer(owner), path=path).status_code == 200
    with ready.test_request_context('/api/v1/training/workout-execution-capabilities'):
        assert hooks.request_runs_maintenance() is True
    # Non-vacuity: removing the exact exemption admits maintenance.
    monkeypatch.setattr(hooks, '_MAINTENANCE_EXEMPT_ENDPOINTS', frozenset({'health'}))
    with ready.test_request_context(path):
        assert hooks.request_runs_maintenance() is True


def maximal_history(occurrences=8):
    records = tuple(Occurrence('r' * 62 + f'{i:02}', 999999999, day(-i), utc_noon(day(-i)),
                              tuple(PerformedSet(j, 1000, 1000.0) for j in range(20)))
                    for i in range(occurrences))
    markers = tuple(UnavailableSession(day(-(i % 56)), utc_noon(day(-(i % 56))), 'checkpoint_invalid')
                    for i in range(64 - occurrences))
    return ExerciseHistory(SQUAT, day(-56), ANCHOR, 'available_with_gaps' if records else 'undetermined',
                           records, Coverage(64, 0, 0, markers, True, False))


@pytest.mark.parametrize('occurrences', [0, 8])
def test_maximal_legitimate_payload_retains_every_fact_and_marker(occurrences):
    encoded = wire.history_json(maximal_history(occurrences))
    assert len(encoded) < 64 * 1024
    body = json.loads(encoded)['exercise_performance_history']
    assert len(body['coverage']['unavailable_sessions']) == 64 - occurrences
    assert sum(len(o['sets']) for o in body['occurrences']) == occurrences * 20


@pytest.mark.parametrize('change', [
    {'session_ref': 'x' * 65}, {'session_ref': ''}, {'session_ref': 42},
    {'checkpoint_revision': True}, {'checkpoint_revision': -1}, {'checkpoint_revision': 1000000000},
    {'workout_date': datetime(2026, 10, 1)}, {'completed_at': datetime(2026, 10, 1, tzinfo=timezone.utc)},
    {'sets': (PerformedSet(0, True, 60),)}, {'sets': (PerformedSet(0, 8, float('nan')),)},
])
def test_invalid_wire_occurrence_refuses(change):
    history = _projection([candidate('a', day(-1))])
    history = replace(history, occurrences=(replace(history.occurrences[0], **change),))
    with pytest.raises(ProjectionInvariantError):
        wire.history_json(history)


def test_wire_byte_backstop_refuses_not_truncates(ready, client, owner, real_bearer, monkeypatch):
    persist(owner.id, candidate('a', day(-1)))
    monkeypatch.setattr(wire, 'MAX_PAYLOAD_BYTES', 100)
    _error(get_history(client, real_bearer(owner)), 503, 'TRAINING_HISTORY_TEMPORARILY_UNAVAILABLE')


@pytest.mark.parametrize('change', [
    {'exercise_id': 'ex_' + 'a' * 65}, {'state': 'unknown'}, {'window_end': day(1)},
    {'coverage': Coverage(True, 0, 0, (), False, False)},
    {'coverage': Coverage(65, 0, 0, (), True, False)},
    {'coverage': Coverage(1, 0, 0, (), True, False)},
    {'coverage': Coverage(1, 0, 0, (), False, True)},
])
def test_wire_invalid_counts_state_window_and_identity_refuse(change):
    with pytest.raises(ProjectionInvariantError):
        wire.history_json(replace(_projection(), **change))


def test_session_invalid_marker_nulls_and_closed_reason():
    coverage = Coverage(1, 0, 0, (UnavailableSession(None, None, 'session_invalid'),), False, False)
    history = replace(_projection(), state='undetermined', coverage=coverage)
    body = json.loads(wire.history_json(history))['exercise_performance_history']
    assert body['coverage']['unavailable_sessions'] == [
        {'workout_date': None, 'completed_at': None, 'reason': 'session_invalid'}]
    for reason in ('unknown', 'checkpoint_invalid', 'checkpoint_missing'):
        with pytest.raises(ProjectionInvariantError):
            wire.history_json(replace(history, coverage=replace(coverage,
                unavailable=(UnavailableSession(None, None, reason),))))


def test_default_throttle_still_applies(ready, client, owner, real_bearer, tight_default_limiter):
    headers = real_bearer(owner)
    assert get_history(client, headers).status_code == 200
    assert get_history(client, headers).status_code == 200
    assert get_history(client, headers).status_code == 429
