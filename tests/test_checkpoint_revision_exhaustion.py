"""V1 saturation: persisted state, both envelopes, replay and terminal commands."""
from datetime import datetime

import pytest

from app.extensions import db
from app.services.workout_session.checkpoint import MAX_REVISION, parse_checkpoint
from app.services.workout_session.queries import advance_checkpoint
from tests.test_sprint14_workout_execution_contract import (
    checkpoint_over_http, durable_state, proof_accepted, row_for,
    save_workout_plan, sessions_on, snapshot, start_session_over_http,
)
from tests.test_mobile_workout_sessions_api import as_mobile


def state(row):
    return durable_state(row) + (row.last_activity_at, row.updated_at, row.version)


def seed(client, user_id, revision):
    save_workout_plan(user_id)
    ref = start_session_over_http(client)
    row = row_for(user_id)
    row.checkpoint_revision = revision
    db.session.commit()
    return ref


@pytest.mark.parametrize('base', [0, MAX_REVISION - 1])
def test_boundary_and_normal_advancement(client, auth_user, sessions_on, base):
    ref = seed(client, auth_user.id, base)
    response = checkpoint_over_http(client, ref, base)
    assert response.status_code == 200
    assert response.get_json()['session']['checkpoint_revision'] == base + 1
    db.session.expire_all()
    assert row_for(auth_user.id).checkpoint_revision == base + 1


def test_authoritative_writer_refuses_saturation_without_mutation(
    client, auth_user, sessions_on,
):
    ref = seed(client, auth_user.id, MAX_REVISION - 1)
    assert checkpoint_over_http(client, ref, MAX_REVISION - 1).status_code == 200
    before = state(row_for(auth_user.id))
    parsed = parse_checkpoint(snapshot(reps=12), ('ex_barbell_back_squat',))
    assert advance_checkpoint(
        auth_user.id, ref, MAX_REVISION, parsed.to_json(), parsed.fingerprint,
        'direct-new-key', datetime.utcnow(),
    ) == 0
    db.session.expire_all()
    assert state(row_for(auth_user.id)) == before


@pytest.mark.parametrize('transport', ['browser', 'native'])
def test_saturation_replay_stale_and_exhaustion(
    client, auth_user, sessions_on, as_mobile, transport,
):
    ref = seed(client, auth_user.id, MAX_REVISION - 1)
    assert checkpoint_over_http(client, ref, MAX_REVISION - 1).status_code == 200
    before = state(row_for(auth_user.id))

    def command(base, key, body):
        if transport == 'browser':
            return checkpoint_over_http(client, ref, base, body, key)
        return client.put(
            f'/api/v1/training/workout-sessions/{ref}/checkpoint',
            headers=as_mobile(auth_user, **{
                'If-Match': str(base), 'Idempotency-Key': key,
            }), json={'checkpoint': body},
        )

    replay = command(MAX_REVISION, 'browser-key-000001', snapshot())
    assert replay.status_code == 200
    assert replay.headers['Idempotency-Replayed'] == 'true'
    session = replay.get_json()['session']
    assert session['checkpoint_revision' if transport == 'browser' else 'revision'] == MAX_REVISION
    stale = command(MAX_REVISION - 1, 'new-key-000001', snapshot(reps=10))
    assert stale.status_code == 409
    exhausted = command(MAX_REVISION, 'new-key-000001', snapshot(reps=10))
    assert exhausted.status_code == 409
    assert exhausted.headers['Session-Resolution'] == 'terminal'
    if transport == 'browser':
        assert stale.get_json()['code'] == 'revision_conflict'
        assert exhausted.get_json()['code'] == 'revision_exhausted'
    else:
        assert stale.get_json()['error']['code'] == 'TRAINING_SESSION_REVISION_CONFLICT'
        assert exhausted.get_json()['error']['code'] == 'TRAINING_SESSION_REVISION_EXHAUSTED'
        assert exhausted.get_json()['error']['retryable'] is False
    db.session.expire_all()
    assert state(row_for(auth_user.id)) == before


def test_max_checkpoint_remains_completable(
    client, auth_user, sessions_on, proof_accepted,
):
    ref = seed(client, auth_user.id, MAX_REVISION - 1)
    assert checkpoint_over_http(client, ref, MAX_REVISION - 1).status_code == 200
    response = client.post('/workout/complete', json={
        'image': 'x', 'location_type': 'salon', 'session_id': ref,
        'expected_checkpoint_revision': MAX_REVISION,
    })
    assert response.status_code == 200
    db.session.expire_all()
    row = row_for(auth_user.id)
    assert row.status == 'completed'
    assert row.checkpoint_revision == MAX_REVISION


def test_max_checkpoint_can_be_abandoned(client, auth_user, sessions_on):
    ref = seed(client, auth_user.id, MAX_REVISION)
    response = client.post(f'/workout/session/{ref}/abandon',
                           headers={'If-Match': str(MAX_REVISION)}, json={})
    assert response.status_code == 200
    db.session.expire_all()
    assert row_for(auth_user.id).status == 'abandoned'
    assert row_for(auth_user.id).checkpoint_revision == MAX_REVISION


@pytest.mark.parametrize("revision", [0, MAX_REVISION])
def test_revision_parsers_keep_the_existing_domain(revision):
    from app.services.workout_session.checkpoint import parse_revision, parse_revision_value
    from app.services.workout_session.errors import InvalidRevision

    assert parse_revision(str(revision)) == revision
    assert parse_revision(f'"{revision}"') == revision
    assert parse_revision_value(revision) == revision
    for invalid in (MAX_REVISION + 1, -1, True, str(revision)):
        with pytest.raises(InvalidRevision):
            parse_revision_value(invalid)
    for invalid in (str(MAX_REVISION + 1), "-1", "01", "+1", "1.0"):
        with pytest.raises(InvalidRevision):
            parse_revision(invalid)
