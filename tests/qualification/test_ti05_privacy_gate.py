"""Known release gate, separate from passing TI-03 projection privacy tests."""
import pytest
from tests.test_ti03_api import (
    owner, flags, as_mobile, completion_proof, _plan, _workout,
    _CompletionClock, THURSDAY_ONE,
)


@pytest.mark.xfail(strict=True, reason='TI-06 required: integrated session/completion logs contain user_id')
def test_integrated_execution_completion_logs_have_no_user_identity(
        client, app, owner, flags, as_mobile, completion_proof, monkeypatch, caplog):
    from app.services.workout_completion import service
    monkeypatch.setattr(service, 'datetime', _CompletionClock)
    _plan(owner.id)
    caplog.clear()
    _workout(client, app, owner, as_mobile, THURSDAY_ONE, [8, 8, 8], 'ti05-privacy-key')
    messages = [record.getMessage() for record in caplog.records
                if '[WORKOUT_SESSION]' in record.getMessage()
                or '[WORKOUT_COMPLETION]' in record.getMessage()]
    assert messages, 'The integrated observability gate must observe real events.'
    assert all('user_id=' not in message for message in messages)
