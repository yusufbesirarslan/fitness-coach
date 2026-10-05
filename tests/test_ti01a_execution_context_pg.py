"""Real PostgreSQL combined-state races with distinct connections and barriers."""
import json
import threading

import pytest

from app.services.workout_session.checkpoint import MAX_REVISION
from app.services.workout_session.context import parse_v2, project_context
from tests.test_mobile_workout_sessions_pg import (
    _ENABLED, _PG_URL, _SKIP_REASON, _make_pg_app, _race, _require_pg, _snapshot, _teardown,
)

pytestmark = [pytest.mark.pg_concurrency,
              pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)]


@pytest.mark.parametrize('versions', [(1, 2), (2, 2)])
@pytest.mark.parametrize('base', [0, MAX_REVISION - 1])
def test_combined_checkpoint_race(versions, base, monkeypatch):
    _require_pg()
    from app.extensions import db
    from app.models import WorkoutSession
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import execution

    app, user_id, reference = _make_pg_app()
    secret = app.config['SECRET_KEY']
    with app.app_context():
        ref = sessions.start(user_id, secret, reference).payload['session']['session_ref']
        row = WorkoutSession.query.filter_by(user_id=user_id).one()
        row.checkpoint_revision = base
        db.session.commit()
    barrier = threading.Barrier(2)
    original = execution.advance_checkpoint

    def advance(*args, **kwargs):
        barrier.wait(timeout=20)
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, 'advance_checkpoint', advance)
    snapshots, contexts, parsed = {}, {}, {}
    for name, version, elapsed, rir in [('browser', versions[0], 60, '0'), ('native', versions[1], 900, '3')]:
        snapshots[name] = _snapshot(elapsed)
        contexts[name] = {'schema_version': 1, 'sets': [{
            'exercise_id': 'ex_barbell_back_squat', 'index': 0,
            'actual_rir': rir, 'tempo_adherence': None, 'actual_rest': None}]}
        parsed[name] = (parse_v2({'checkpoint': snapshots[name], 'execution_context': contexts[name]},
                                ('ex_barbell_back_squat',)) if version == 2 else
                        sessions.parse_checkpoint(snapshots[name], ('ex_barbell_back_squat',)))

    def writer(name):
        def work():
            if name == 'native':
                return sessions.checkpoint(user_id, secret, ref, 'race-key-' + name, base,
                                           lambda allowed: parsed[name]).payload['session']['revision']
            return execution.record_checkpoint(user_id, ref, 'race-key-' + name, base,
                                               execution.planned_exercise_identities,
                                               lambda allowed: parsed[name]).view.checkpoint_revision
        return work

    try:
        results = _race(app, user_id, {name: writer(name) for name in parsed})
        assert sorted(kind for kind, _ in results.values()) == ['ok', 'raise']
        assert next(value for kind, value in results.values() if kind == 'raise') == 'RevisionConflict'
        winner = next(name for name, (kind, _) in results.items() if kind == 'ok')
        with app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert row.checkpoint_revision == base + 1
            assert json.loads(row.checkpoint_data) == snapshots[winner]
            assert row.checkpoint_idempotency_key == 'race-key-' + winner
            assert row.checkpoint_fingerprint == parsed[winner].fingerprint
            assert json.loads(row.execution_context_data)['bound_revision'] == base + 1
            assert project_context(row) == (contexts[winner] if parsed[winner].execution_context is not None
                                            else {'schema_version': 1, 'sets': []})
    finally:
        _teardown(app)
