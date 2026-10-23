"""Final integrated release gate over all canonical workout log families."""
import logging

from app.services.workout_completion import service
from app.services.workout_state import queries
from app.timeutil import audit_clock
from tests.test_ti03_api import (
    flags, as_mobile, completion_proof, _plan, _workout,
    _CompletionClock, THURSDAY_ONE,
)
from tests.test_ti06_workout_log_privacy import SESSION_LINE, COMPLETION_LINE
from tests.test_ti07_workout_state_log_privacy import STATE_LINE

OWNER_ID = 1987654399
FAMILIES = ('[WORKOUT_SESSION]', '[WORKOUT_COMPLETION]', '[WORKOUT_STATE]')


class SyntheticOwnerReadError(RuntimeError):
    pass


def test_integrated_execution_completion_logs_have_no_user_identity(
        client, app, make_user, flags, as_mobile, completion_proof, monkeypatch, caplog):
    owner = make_user('ti05b_distinct_owner', id=OWNER_ID,
                      email='ti05b_distinct_owner@example.invalid',
                      cognito_sub='ti05b-distinct-cognito-subject-qualification')
    identities = {str(owner.id), owner.username, owner.email, owner.cognito_sub}
    assert len(identities) == 4 and all(identities)
    monkeypatch.setattr(service, 'datetime', _CompletionClock)
    _plan(owner.id)
    caplog.set_level(logging.INFO)
    caplog.clear()
    ref = _workout(client, app, owner, as_mobile, THURSDAY_ONE,
                   [8, 8, 8], 'ti05b-privacy-key')

    def fail_owner_read(*args, **kwargs):
        raise SyntheticOwnerReadError('owner read failed: ' + ' '.join(sorted(identities)))

    # Real state read and exception-detail boundary, in the lifecycle capture.
    with monkeypatch.context() as patch, audit_clock(THURSDAY_ONE):
        patch.setattr(queries, 'fetch_workout_entries', fail_owner_read)
        response = client.get('/api/v1/today', headers=as_mobile(owner))
    assert response.status_code == 503
    records = [record for record in caplog.records
               if any(family in record.getMessage() for family in FAMILIES)]
    assert {record.getMessage().split(' ', 1)[0] for record in records} == set(FAMILIES)
    shapes = dict(zip(FAMILIES, (SESSION_LINE, COMPLETION_LINE, STATE_LINE)))
    for record in records:
        message = record.getMessage()
        # Concrete values in text, arguments, and all record attributes,
        # including extra/exception metadata. Exact shape forbids substitutes.
        for captured in (message, repr(record.args), repr(vars(record))):
            for identity in identities | {ref}:
                assert identity not in captured, (identity, captured)
        assert shapes[message.split(' ', 1)[0]].fullmatch(message), message
    state_records = [r for r in records if r.getMessage().startswith('[WORKOUT_STATE]')]
    assert state_records
    assert {STATE_LINE.fullmatch(r.getMessage())['detail'] for r in state_records} == {
        'SyntheticOwnerReadError'}
    assert all(' rid=-' not in r.getMessage() for r in records)
