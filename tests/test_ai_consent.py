"""Offline default-deny/HTTP/architecture qualification. No provider requests."""
import ast
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_mobile_coach_api import bearer  # real opaque credential; synthetic Cognito
from app.services import ai_consent as consent, ai_provider_call as calls


def test_release_has_no_inferred_or_configured_disclosures():
    assert dict(consent.POLICIES) == {}
    assert set(consent.BINDINGS) == {'training_plan', 'nutrition_plan'}


@pytest.mark.parametrize('record', [None, True, {}, {'version': []},
    {'version': 'obsolete', 'state': 'granted'}, {'state': 'revoked'},
    {'version': 'test', 'granted_at': 'malformed'}])
def test_malformed_and_legacy_records_deny(record):
    assert not consent._record_valid('training.plan', record)


@pytest.mark.parametrize('feature', ['coach', 'summary', 'nutrition', 'menu_extract',
                                    'menu_ocr', 'vision', 'health_probe', 'other', 'unknown'])
def test_unqualified_physical_paths_deny_without_sdk(feature):
    sdk = Mock()
    with pytest.raises(consent.ConsentDenied):
        with calls.admit(feature=feature, provider='openai', payload={}) as call:
            call.create(sdk)
    sdk.assert_not_called()


def test_sqlite_never_claims_distributed_authority(app, make_user):
    owner = make_user('sqlite_consent')
    with pytest.raises(consent.ConsentDenied) as error:
        consent.status(owner.id)
    assert error.value.status == 503


@pytest.mark.parametrize('data', [None, {}, {'owner_id': 1}, {'ai_consent': True}])
def test_client_booleans_cannot_create_consent(data):
    with pytest.raises(consent.ConsentDenied) as error:
        consent.mutate(1, data)
    assert error.value.status == 400


def test_web_owner_comes_from_authenticated_session(client, auth_user, monkeypatch):
    seen = []
    monkeypatch.setattr(consent, 'status', lambda owner: seen.append(owner) or {'generation': 0})
    response = client.get('/api/ai-consent?owner_id=987')
    assert response.status_code == 200
    assert seen == [auth_user.id]
    assert response.headers['Cache-Control'] == 'no-store'
    response = client.post('/api/ai-consent', json={'owner_id': 987, 'ai_consent': True})
    assert response.status_code == 400


def test_web_csrf_required(raw_client, auth_user):
    assert raw_client.post('/api/ai-consent', json={}).status_code == 403


def test_mobile_consent_requires_bearer(client):
    assert client.get('/api/v1/ai-consent').status_code == 401


def test_closed_ai_route_has_typed_terminal_error_and_keeps_history(client, auth_user, monkeypatch):
    response = client.post('/ask', json={'question': 'SYNTHETIC', 'ai_consent': True})
    assert response.status_code == 403
    assert response.json['error']['code'] == 'AI_CONSENT_DISCLOSURE_UNAVAILABLE'
    assert response.json['error']['retryable'] is False
    assert set(response.json['error']) == {'code', 'message', 'retryable', 'request_id'}
    assert client.get('/coach/history').status_code == 200


def test_memory_default_off_and_old_workers_cannot_summarize(app, make_user, monkeypatch):
    from app.services import memory_manager, ai, ai_coach
    from app.services.ai_pipeline import _memory_stage
    from app.extensions import db
    from app.models import CoachConversation
    sdk = Mock(side_effect=AssertionError('No provider calls'))
    monkeypatch.setattr(ai, '_openai_chat', sdk)
    user = make_user('memory_disabled')
    conversation, context, deferred = _memory_stage(user.id)
    conversation.summary = 'HISTORICAL_RECORD_PRESERVED'
    db.session.commit()
    assert context == [] and deferred is None
    assert memory_manager.maybe_summarize(conversation) is False
    assert db.session.get(CoachConversation, conversation.id).summary == 'HISTORICAL_RECORD_PRESERVED'
    assert 'AI_MEMORY_CONSENT_REQUIRED' in ai_coach._tool_manage_user_memory(user.id, 'update', 'preferences', 'synthetic')
    sdk.assert_not_called()


def test_only_guard_owns_sdk_submission():
    # Reuse the pre-existing inventory architecture test as well; this test
    # prevents future creation of alternate direct SDK clients/call sites.
    root = Path(__file__).parents[1] / 'app'
    physical = []
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                expr = ast.unparse(node.func)
                if expr.endswith(('messages.create', 'completions.create', 'messages.stream', 'responses.create', 'invoke_model', 'converse')):
                    physical.append(str(path.relative_to(root)))
    assert physical == []  # SDK bound methods are passed to Admission, never invoked elsewhere


def test_mobile_owner_comes_from_bearer_not_cookie(client, make_user, bearer, monkeypatch):
    a, b = make_user('consent_mobile_a'), make_user('consent_mobile_b')
    headers = bearer(b)
    with client.session_transaction() as session:
        session['_user_id'] = str(a.id)
        session['cognito_sid'] = 'synthetic-cookie-identity'
    captured = []
    monkeypatch.setattr(consent, 'status', lambda owner: captured.append(owner) or {'generation': 0})
    response = client.get('/api/v1/ai-consent?owner_id=' + str(a.id), headers=headers)
    assert response.status_code == 200
    assert captured == [b.id]
    assert response.headers['Cache-Control'] == 'no-store'


def test_native_denial_preserves_existing_error_envelope(client, make_user, bearer):
    user = make_user('consent_native_denied')
    response = client.post('/api/v1/coach/messages', headers=bearer(user),
                           json={'message': 'SYNTHETIC', 'ai_consent': True})
    assert response.status_code == 403
    assert set(response.json['error']) == {'code', 'message', 'retryable', 'request_id'}
    assert response.json['error']['code'] == 'AI_CONSENT_DISCLOSURE_UNAVAILABLE'
    assert response.json['error']['retryable'] is False
