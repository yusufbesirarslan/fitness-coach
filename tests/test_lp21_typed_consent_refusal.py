"""Refusals keep their D1 identity before mutations and after preflight races.

No policy is published and no provider is invoked. Mock ALLOW in race cases
represents only the early snapshot; the injected final refusal must survive.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import ai_consent
from app.services.training_generation.service import _CompletionBudget
from app.services.training_plan_replacement import generation as replacement
from app.services.nutrition_native import plan as nutrition
from app.services.mobile_training_generation.contract import parse_native_request
from test_mobile_training_generation_api import CANONICAL, mobile_user, as_mobile


def deny(code='AI_CONSENT_REQUIRED'):
    return ai_consent.ConsentDenied(code)


@pytest.mark.parametrize('code', ['AI_CONSENT_REQUIRED', 'AI_CONSENT_CHANGED',
                                 'AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE'])
def test_training_completion_preserves_typed_refusal(code):
    error = deny(code)
    chat = Mock(side_effect=error)
    budget = _CompletionBudget(chat)
    with pytest.raises(ai_consent.ConsentDenied) as caught:
        budget.complete(prompt='synthetic', system_prompt='', max_tokens=10, temperature=0)
    assert caught.value is error
    assert chat.call_count == 1
    assert budget.calls == []


def test_replacement_preflight_denies_before_claim_or_provider(monkeypatch):
    claim = Mock(side_effect=AssertionError('claim would retire/quota'))
    provider = Mock(side_effect=AssertionError('provider reached'))
    error = deny()
    preflight = Mock(side_effect=error)
    monkeypatch.setattr(replacement.ai_consent, 'preflight', preflight)
    monkeypatch.setattr(replacement.ai, 'heavy_primary_route', lambda: ('bedrock', 'synthetic'))
    monkeypatch.setattr(replacement, 'claim', claim)
    with pytest.raises(ai_consent.ConsentDenied) as caught:
        replacement.generate_proposal(SimpleNamespace(id=7), parse_native_request(CANONICAL),
                                      'synthetic-key', chat_fn=provider, provider_guard=nullcontext)
    assert caught.value is error
    preflight.assert_called_once_with(7, 'training_plan', 'bedrock', 'synthetic')
    claim.assert_not_called()
    provider.assert_not_called()


def test_replacement_final_refusal_keeps_typed_failure(app, monkeypatch):
    # Isolate the post-claim refusal boundary from the frozen-input decoder.
    import json
    monkeypatch.setattr(replacement.ai_consent, 'preflight', lambda *args: None)
    monkeypatch.setattr(replacement, 'claim', lambda *args: (17, 'lease', json.dumps({
        'language': 'en', 'features': {'performance_history': {}}})))
    monkeypatch.setattr(replacement, 'PerformanceHistory', lambda **kwargs: object())
    monkeypatch.setattr(replacement, 'UserTrainingFeatures', lambda **kwargs: object())
    error = deny('AI_CONSENT_CHANGED')
    monkeypatch.setattr(replacement, 'generate_training_plan_candidate', Mock(side_effect=error))
    fail = Mock()
    monkeypatch.setattr(replacement, 'fail', fail)
    with pytest.raises(ai_consent.ConsentDenied) as caught:
        replacement.generate_proposal(SimpleNamespace(id=7), parse_native_request(CANONICAL),
                                      'synthetic-key', chat_fn=Mock(), provider_guard=nullcontext)
    assert caught.value is error
    fail.assert_called_once_with(17, 'lease', error.code, 403)


@pytest.mark.parametrize('quota_enabled', [False, True])
def test_nutrition_preflight_denies_before_quota_or_provider(app, monkeypatch, quota_enabled):
    monkeypatch.setattr(nutrition, '_generation_target', lambda uid: (2000, 'general'))
    monkeypatch.setattr(nutrition.ai_consent, 'preflight', Mock(side_effect=deny()))
    reserve = Mock(side_effect=AssertionError('quota reached'))
    chat = Mock(side_effect=AssertionError('provider reached'))
    monkeypatch.setattr(nutrition, 'reserve_ai_quota', reserve)
    with pytest.raises(ai_consent.ConsentDenied):
        nutrition.generate_proposals(SimpleNamespace(id=7), 'synthetic', {}, chat, quota_enabled)
    reserve.assert_not_called()
    chat.assert_not_called()


@pytest.mark.parametrize('quota_enabled', [False, True])
def test_nutrition_final_refusal_preserved_and_quota_refunded(app, monkeypatch, quota_enabled):
    monkeypatch.setattr(nutrition, '_generation_target', lambda uid: (2000, 'general'))
    monkeypatch.setattr(nutrition.ai_consent, 'preflight', lambda *args: None)
    reserve, refund = Mock(return_value=True), Mock()
    monkeypatch.setattr(nutrition, 'reserve_ai_quota', reserve)
    monkeypatch.setattr(nutrition, 'refund_ai_quota', refund)
    error = deny('AI_CONSENT_CHANGED')
    monkeypatch.setattr(nutrition.generation, 'generate_plan_options', Mock(side_effect=error))
    user = SimpleNamespace(id=7, language='en')
    request = dict(proteins=[], carbs=[], fats=[], custom_foods=[])
    with pytest.raises(ai_consent.ConsentDenied) as caught:
        nutrition.generate_proposals(user, 'synthetic', request, Mock(), quota_enabled)
    assert caught.value is error
    assert reserve.call_count == refund.call_count == int(quota_enabled)


@pytest.mark.parametrize('path', ['/api/v1/training/plans',
                                 '/api/v1/training/plans/replacement-proposals',
                                 '/api/v1/nutrition/plan/generate'])
def test_transport_preserves_refusal_after_endpoint_gate(app, client, mobile_user, as_mobile, monkeypatch, path):
    from app.blueprints import ai_consent as routes
    from app.blueprints import mobile_training
    monkeypatch.setattr(routes, '_CLOSED_ENDPOINTS', frozenset())
    error = deny('AI_CONSENT_CHANGED')
    monkeypatch.setattr(mobile_training, 'generate_and_persist', Mock(side_effect=error))
    monkeypatch.setattr(replacement, 'generate_proposal', Mock(side_effect=error))
    monkeypatch.setattr(nutrition, 'generate_proposals', Mock(side_effect=error))
    body = (dict(proteins=['Tavuk Göğsü'], carbs=['Pirinç'], fats=['Zeytinyağı'])
            if 'nutrition' in path else CANONICAL)
    response = client.post(path, headers=as_mobile(mobile_user, 'synthetic-key'), json=body)
    assert response.status_code == 403, response.get_json()
    assert response.get_json()['error']['code'] == error.code
    assert response.get_json()['error']['retryable'] is False
