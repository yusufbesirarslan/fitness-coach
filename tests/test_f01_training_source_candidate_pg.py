"""Local source composition on disposable PG; identity/SDK synthetic only.

No production or staging requests. F01/D1/cohort/ledger/publication authorities
are real; Redis is emulated. This is not deployed end-to-end qualification.
"""
import calendar
from datetime import datetime, timedelta
import json
import os
from types import SimpleNamespace

import fakeredis
import pytest

from app.extensions import db
from app.models import (User, TrainingPlan, TrainingPlanGenerationOperation,
                        AiUsageReservation, AIConsentEvent)
from app.services import (ai_consent, ai_consent_disclosures, health_disclosure,
                          ai_launch)
from tests.test_mobile_training_generation_api import CANONICAL, _provider_document

pytestmark = pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('explicit disposable PostgreSQL required', allow_module_level=True)


@pytest.fixture
def candidate(monkeypatch):
    from app import create_app, config, extensions
    from app.blueprints import mobile_registration
    from app.services import ai, ai_spend_guard, cognito_jwt, cognito_service
    from app.services.ai_allowances import launch_policy
    url = os.environ.get('PG_TEST_DATABASE_URL', '')
    assert url.startswith(('postgresql://', 'postgresql+psycopg2://'))
    monkeypatch.setenv('DATABASE_URL', url)
    # Restore the actual installed source policy isolated by legacy layer tests.
    monkeypatch.setattr(launch_policy, 'APPROVED_POLICY', launch_policy.LAUNCH_V1_POLICY)
    monkeypatch.setattr(config, 'AI_MODEL_ROUTING', 'claude55')
    monkeypatch.setattr(config, 'BEDROCK_ENABLED', True)
    monkeypatch.setattr(config, 'BEDROCK_SONNET55_MODEL', 'eu.anthropic.claude-sonnet-5-5')
    monkeypatch.setattr(config, 'BEDROCK_HAIKU55_MODEL', 'eu.anthropic.claude-haiku-5-5')
    monkeypatch.setattr(extensions, 'redis_client', fakeredis.FakeRedis())
    monkeypatch.setattr(ai_spend_guard, 'ENABLED', True)
    monkeypatch.setattr(ai, 'BEDROCK_ENABLED', True)
    calls = []

    def sdk(**kwargs):
        calls.append(kwargs['model'])
        return SimpleNamespace(stop_reason='end_turn', usage=None, content=[
            SimpleNamespace(type='text', text=json.dumps(_provider_document(
                'ex_barbell_back_squat'), ensure_ascii=False))])

    monkeypatch.setattr(ai, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(create=sdk)))
    monkeypatch.setattr(mobile_registration, 'COGNITO_ENABLED', True)
    monkeypatch.setattr(cognito_service, 'sign_up', lambda *a, **k: 'synthetic-founder-sub')
    monkeypatch.setattr(cognito_service, 'confirm_sign_up', lambda *a, **k: None)
    monkeypatch.setattr(cognito_service, 'authenticate', lambda *a, **k: {
        'tokens': {'access_token': 'synthetic-access', 'id_token': 'synthetic-id',
                   'refresh_token': 'synthetic-refresh', 'expires_in': 3600},
        'claims': {'sub': 'synthetic-founder-sub'}})
    expiry = calendar.timegm((datetime.utcnow() + timedelta(hours=1)).timetuple())
    monkeypatch.setattr(cognito_jwt, 'validate_token', lambda *a, **k: {
        'sub': 'synthetic-founder-sub', 'exp': expiry,
        'email': 'source-candidate@example.invalid', 'email_verified': True})
    app = create_app()
    app.config.update(TESTING=True, ACCOUNT_ELIGIBILITY_ENABLED=True,
        AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True, APPLE_SUBSCRIPTIONS_ENABLED=False,
        AI_ALLOWANCE_POLICY=launch_policy.LAUNCH_V1_POLICY)
    with app.app_context():
        db.create_all()
    try:
        yield app, app.test_client(), calls
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def call(client, method, path, status=200, **kwargs):
    response = getattr(client, method)(path, **kwargs)
    try:
        data = response.get_json()
        assert response.status_code == status, data
        return data
    finally:
        response.close()  # Release the real response-lifetime health scope.


@pytest.mark.parametrize('locale', ['en', 'tr'])
def test_exact_source_fresh_registration_profile_training_and_stop(candidate, locale, monkeypatch):
    app, client, sdk = candidate
    assert ai_launch.APPROVED_SERVICE_COHORT_VERSION == 'age-screen-v1'
    assert health_disclosure.APPROVED_VERSION == 'health-fitness-v2-review'
    assert set(ai_consent.POLICIES) == {'d1-closure01-training-candidate'}
    declaration = call(client, 'post', '/api/v1/auth/eligibility', json={'age': 30, 'country': 'TR'})
    call(client, 'post', '/api/v1/auth/register', status=201, json={
        'username': 'sourcecandidate', 'email': 'source-candidate@example.invalid',
        'password': 'Synthetic123',
        'eligibility_token': declaration['eligibility']['registration_token']})
    call(client, 'post', '/api/v1/auth/verify', json={'username': 'sourcecandidate', 'code': '123456'})
    session = call(client, 'post', '/api/v1/auth/login', json={
        'username': 'sourcecandidate', 'password': 'Synthetic123'})
    headers = {'Authorization': 'Bearer ' + session['session']['access_credential']}
    call(client, 'get', '/api/v1/account/profile', status=403, headers=headers)
    status = call(client, 'get', '/api/v1/health-consent', headers=headers)
    health = dict(purpose=health_disclosure.PURPOSE, version=health_disclosure.VERSION,
        locale=locale, text_digest=health_disclosure.DIGESTS[locale],
        action='affirmative_grant', expected_generation=status['generation'],
        request_id='source-health-grant')
    call(client, 'post', '/api/v1/health-consent', status=403, headers=headers,
         json={**health, 'text_digest': '0' * 64, 'request_id': 'source-invalid-health'})
    call(client, 'post', '/api/v1/health-consent', headers=headers, json=health)
    call(client, 'put', '/api/v1/account/profile', headers=headers, json={
        'weight_kg': 80, 'height_cm': 180, 'age': 30, 'gender': 'male',
        'goal': 'build_muscle', 'fitness_level': 'intermediate', 'activity_level': 'active'})
    blocked = call(client, 'post', '/api/v1/training/plans', status=403,
                   headers={**headers, 'Idempotency-Key': 'source-before-grant'}, json=CANONICAL)
    assert blocked['error']['code'] == 'AI_CONSENT_REQUIRED'
    assert sdk == []
    status = call(client, 'get', '/api/v1/ai-consent', headers=headers)
    disclosure, = status['available_disclosures']
    assert disclosure['purpose'] == 'training.plan'
    assert disclosure['localized_texts'][locale] == ai_consent_disclosures.PUBLISHED_TEXTS[(disclosure['version'], locale)]
    grant = dict(action='affirmative_grant', purpose='training.plan',
        version=disclosure['version'], locale=locale,
        text_digest=disclosure['texts'][locale], contract_digest=disclosure['contract_digest'],
        expected_generation=status['generation'], request_id='source-training-grant')
    call(client, 'post', '/api/v1/ai-consent', headers=headers, json=grant)
    # Keep final authorities real: change one runtime/publication input at a
    # time and prove no SDK call or canonical result under the valid user grant.
    for fault in ('publication', 'redis', 'cohort', 'recipient', 'ledger',
                  'training_enforcement', 'premium_enforcement'):
        with monkeypatch.context() as control:
            if fault == 'publication':
                control.setattr(ai_consent_disclosures, 'PUBLISHED_TEXTS', {})
            elif fault == 'redis':
                from app import extensions
                control.setattr(extensions, 'redis_client', None)
            elif fault == 'cohort':
                control.setattr(ai_launch, 'APPROVED_SERVICE_COHORT_VERSION', None)
            elif fault == 'recipient':
                from app import config
                control.setattr(config, 'BEDROCK_SONNET55_MODEL', 'global.anthropic.claude-sonnet-5-5')
            else:
                key = {
                    'ledger': 'AI_ALLOWANCE_LEDGER_ENABLED',
                    'training_enforcement': 'AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED',
                    'premium_enforcement': 'AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED',
                }[fault]
                control.setitem(app.config, key, False)
            expected_status = 503 if fault in ('ledger', 'training_enforcement') else 403
            call(client, 'post', '/api/v1/training/plans', status=expected_status,
                 headers={**headers, 'Idempotency-Key': 'source-fault-' + fault}, json=CANONICAL)
            assert sdk == []
            with app.app_context():
                assert TrainingPlan.query.count() == 0
                assert AiUsageReservation.query.count() == 0
    created = call(client, 'post', '/api/v1/training/plans', status=201,
                   headers={**headers, 'Idempotency-Key': 'source-first-plan'}, json=CANONICAL)
    assert created['plan'] is not None
    current = call(client, 'get', '/api/v1/training/plans/current', headers=headers)
    assert current['plan'] == created['plan']
    call(client, 'post', '/api/v1/training/plans', status=201,
         headers={**headers, 'Idempotency-Key': 'source-first-plan'}, json=CANONICAL)
    assert sdk == ['eu.anthropic.claude-sonnet-5-5']
    with app.app_context():
        assert TrainingPlan.query.count() == 1
        assert TrainingPlanGenerationOperation.query.one().status == 'SUCCEEDED'
        assert AiUsageReservation.query.one().state == 'COMMITTED'
        assert AIConsentEvent.query.count() == 1
    stopped = {**grant, 'action': 'revoke', 'expected_generation': 1,
               'request_id': 'source-training-revoke'}
    call(client, 'post', '/api/v1/ai-consent', headers=headers, json=stopped)
    call(client, 'post', '/api/v1/training/plans', status=403,
         headers={**headers, 'Idempotency-Key': 'source-after-revoke'}, json=CANONICAL)
    assert sdk == ['eu.anthropic.claude-sonnet-5-5']
    call(client, 'post', '/api/v1/health-consent', headers=headers, json={
        **health, 'action': 'withdraw', 'expected_generation': 1,
        'request_id': 'source-health-withdraw'})
    call(client, 'get', '/api/v1/account/profile', status=403, headers=headers)
    with app.app_context():
        assert TrainingPlan.query.count() == 0
        assert db.session.query(User).one().profile_complete is False
