"""Four newly registered D1 purposes on disposable PG: grant, isolation, revoke.

Real F01/D1/cohort authorities; identity and SDK synthetic; no provider call is
made. This is source composition, not deployed or real-provider qualification.
"""
import os

import pytest

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("explicit disposable PostgreSQL required", allow_module_level=True)

from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.services import ai_consent, ai_launch, health_disclosure  # noqa: E402
# Same disposable-PG fixture and helper as the approved Training candidate.
from tests.test_f01_training_source_candidate_pg import candidate, call  # noqa: E402, F401

pytestmark = pytest.mark.pg_concurrency

FOUR = {
    "coach": "coach.conversation",
    "nutrition_plan": "nutrition.plan",
    "vision": "pump_check.analysis",
    "menu_extract": "qr_menu.analysis",
}


def _adult(client):
    declaration = call(client, 'post', '/api/v1/auth/eligibility', json={'age': 30, 'country': 'TR'})
    call(client, 'post', '/api/v1/auth/register', status=201, json={
        'username': 'sourcecandidate', 'email': 'source-candidate@example.invalid',
        'password': 'Synthetic123',
        'eligibility_token': declaration['eligibility']['registration_token']})
    call(client, 'post', '/api/v1/auth/verify', json={'username': 'sourcecandidate', 'code': '123456'})
    session = call(client, 'post', '/api/v1/auth/login', json={
        'username': 'sourcecandidate', 'password': 'Synthetic123'})
    headers = {'Authorization': 'Bearer ' + session['session']['access_credential']}
    status = call(client, 'get', '/api/v1/health-consent', headers=headers)
    call(client, 'post', '/api/v1/health-consent', headers=headers, json=dict(
        purpose=health_disclosure.PURPOSE, version=health_disclosure.VERSION, locale='en',
        text_digest=health_disclosure.DIGESTS['en'], action='affirmative_grant',
        expected_generation=status['generation'], request_id='four-health-grant'))
    return headers


def _act(client, headers, purpose, action, request_id):
    status = call(client, 'get', '/api/v1/ai-consent', headers=headers)
    disclosure = next(d for d in status['available_disclosures'] if d['purpose'] == purpose)
    call(client, 'post', '/api/v1/ai-consent', headers=headers, json=dict(
        action=action, purpose=purpose, version=disclosure['version'], locale='en',
        text_digest=disclosure['texts']['en'], contract_digest=disclosure['contract_digest'],
        expected_generation=status['generation'], request_id=request_id))


def _admits(app, owner, feature):
    with app.app_context():
        try:
            ai_launch.admit_endpoint(owner, feature)
            return True
        except ai_consent.ConsentDenied as error:
            return error.code


def test_each_new_purpose_is_independent_granted_and_revocable(candidate):  # noqa: F811
    app, client, sdk = candidate
    headers = _adult(client)
    with app.app_context():
        owner = db.session.query(User).one().id
    status = call(client, 'get', '/api/v1/ai-consent', headers=headers)
    assert {d['purpose'] for d in status['available_disclosures']} == {
        'training.plan', *FOUR.values()}

    for feature in FOUR:
        assert _admits(app, owner, feature) == 'AI_CONSENT_REQUIRED'

    for feature, purpose in FOUR.items():
        _act(client, headers, purpose, 'affirmative_grant', f'four-grant-{feature}')
        assert _admits(app, owner, feature) is True
        # A grant never opens a different purpose (no cross-purpose leakage).
        for other in FOUR:
            if other != feature:
                assert _admits(app, owner, other) == 'AI_CONSENT_REQUIRED'
        assert _admits(app, owner, 'training_plan') == 'AI_CONSENT_REQUIRED'
        _act(client, headers, purpose, 'revoke', f'four-revoke-{feature}')
        assert _admits(app, owner, feature) == 'AI_CONSENT_REQUIRED'

    # AI Memory stays unavailable whatever is granted; no provider was called.
    _act(client, headers, 'coach.conversation', 'affirmative_grant', 'four-coach-again')
    with app.app_context():
        assert ai_consent.memory_available(owner) is False
    assert sdk == []
