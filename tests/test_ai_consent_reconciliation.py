"""Real empty-registry HTTP boundaries; no synthetic authority fixture.

The initial completion in replay tests uses a scoped fake proof to establish
historical state. Replay itself restores the real gate and must do no AI work.
"""
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import TrainingPlan
from test_mobile_workout_sessions_api import (
    sessions_enabled, owner, as_mobile, plan, workout_ref, completion_proof,
    _start, _complete,
)


def test_web_fresh_completion_denies_before_image_or_provider(client, auth_user, monkeypatch):
    from app.blueprints import training
    db.session.add(TrainingPlan(user_id=auth_user.id, plan_data='{"v": 1}', score=7))
    db.session.commit()
    image, provider = Mock(), Mock()
    monkeypatch.setattr(training, 'validate_pump_check_image', image)
    monkeypatch.setattr(training, 'validate_pump_check', provider)
    response = client.post('/workout/complete', json={'image': 'SYNTHETIC'})
    assert response.status_code == 403
    assert response.json['error']['code'] == 'AI_CONSENT_DISCLOSURE_UNAVAILABLE'
    image.assert_not_called()
    provider.assert_not_called()


def test_native_fresh_completion_denies_before_image_or_provider(
        client, owner, as_mobile, workout_ref, monkeypatch):
    from app.blueprints import mobile_workout_sessions as routes
    headers = as_mobile(owner)
    started = _start(client, headers, workout_ref)
    reference = started.json['session']['session_ref']
    image, provider = Mock(), Mock()
    monkeypatch.setattr(routes, 'validate_uploaded_pump_check_image', image)
    monkeypatch.setattr(routes, 'validate_pump_check', provider)
    response = _complete(client, headers, reference, 0)
    assert response.status_code == 403
    assert response.json['error']['code'] == 'AI_CONSENT_DISCLOSURE_UNAVAILABLE'
    image.assert_not_called()
    provider.assert_not_called()


def test_native_completion_replay_keeps_real_gate_and_skips_proof(
        client, owner, as_mobile, workout_ref, completion_proof, monkeypatch):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json['session']['session_ref']
    with monkeypatch.context() as setup:
        setup.setattr('app.blueprints.ai_consent.require_legacy_ai', lambda: None)
        assert _complete(client, headers, reference, 0).status_code == 200
    calls = dict(completion_proof)
    response = _complete(client, headers, reference, 0)
    assert response.status_code == 200
    assert response.json['completion']['outcome'] == 'already_completed'
    assert completion_proof == calls


def test_manual_meal_and_first_party_reads_work_with_empty_registry(client, auth_user, monkeypatch):
    from app.blueprints.nutrition import meallog
    from app.services import ai_consent
    provider = Mock(side_effect=AssertionError('manual tracking must not invoke AI'))
    monkeypatch.setattr(meallog, '_openai_chat', provider)
    assert dict(ai_consent.POLICIES) == {}
    response = client.post('/meal-log', json={
        'ogun': 'Akşam', 'yemekler': 'SYNTHETIC',
        'override_macros': {'kalori': 495, 'protein': 62, 'karb': 0, 'yag': 10.5},
    })
    assert response.status_code == 200
    assert response.json['nutrients']['kalori'] == 495
    assert client.get('/meal-log/today').status_code == 200
    assert client.get('/coach/history').status_code == 200
    provider.assert_not_called()
