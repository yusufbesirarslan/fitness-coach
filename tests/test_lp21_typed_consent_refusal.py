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


@pytest.mark.parametrize('enforced,attempted', [(False, False), (True, False), (True, True)])
def test_real_first_plan_refusal_terminates_refunds_and_replays(
        app, mobile_user, monkeypatch, synthetic_ai_layer, enforced, attempted):
    from ai_allowance_support import enable_ledger
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    from app.extensions import db
    from app.models import (TrainingPlanGenerationOperation, TrainingPlan,
                            AiUsageReservation, AiAllowanceBucket)
    enable_ledger(app)
    app.config['AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED'] = enforced
    app.config['AI_PLAN_QUOTA_ENABLED'] = True
    error = deny('AI_CONSENT_CHANGED')
    def refuse(*args, **kwargs):
        if attempted:
            from app.services import ai_provider_call
            ai_provider_call._before_physical_attempt()
        raise error
    generate = Mock(side_effect=refuse)
    monkeypatch.setattr(service, 'generate_training_plan_candidate', generate)
    request = parse_native_request(CANONICAL)
    uid = mobile_user.id
    with pytest.raises(ai_consent.ConsentDenied) as caught:
        service.generate_and_persist(mobile_user, request, 'consent-first-key',
                                     chat_fn=Mock(), provider_guard=nullcontext)
    assert caught.value is error
    row = TrainingPlanGenerationOperation.query.one()
    assert row.status == 'FAILED' and row.error_code == error.code
    assert row.error_http_status == 403 and row.quota_reserved is False
    assert row.provider_attempt_possible is (attempted if enforced else None)
    db.session.refresh(mobile_user)
    assert ((mobile_user.user_metadata or {}).get('ai_plan_quota') or {}).get('training', 0) == 0
    assert TrainingPlan.query.count() == 0
    if enforced:
        hold = AiUsageReservation.query.one()
        assert hold.state == ('COMMITTED' if attempted else 'RELEASED')
        assert hold.billing_evidence == ('UNKNOWN' if attempted else 'CONFIRMED_NOT_BILLED')
        bucket = AiAllowanceBucket.query.filter_by(user_id=uid, feature='training_generation').one()
        assert bucket.used == int(attempted) and bucket.reserved == 0
    else:
        assert AiUsageReservation.query.count() == 0
    with pytest.raises(StoredGenerationFailure) as replay:
        service.generate_and_persist(mobile_user, request, 'consent-first-key',
                                     chat_fn=Mock(), provider_guard=nullcontext)
    assert replay.value.public_code == error.code and replay.value.http_status == 403
    assert generate.call_count == 1
    assert TrainingPlanGenerationOperation.query.filter_by(user_id=uid).count() == 1
