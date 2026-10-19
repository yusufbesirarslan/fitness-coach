"""No network: exercise the real operational boundary and real D1 denials."""
from types import SimpleNamespace

import pytest

from app import extensions
from app.services import ai_consent, ai_provider_call, ai_spend_guard, bedrock_health


@pytest.fixture
def provider(monkeypatch):
    sent = []
    class Stream:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get_final_message(self):
            return SimpleNamespace(usage=None)
    def stream(**kwargs):
        sent.append(kwargs)
        return Stream()
    monkeypatch.setattr(extensions, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    monkeypatch.setattr(bedrock_health, 'BEDROCK_ENABLED', True)
    monkeypatch.setattr(ai_consent, 'POLICIES', {})
    bedrock_health.reset_cache()
    yield sent
    bedrock_health.reset_cache()


def test_probe_reaches_provider_without_policy_or_user_consent(provider):
    with ai_spend_guard.subject_scope(123):
        assert bedrock_health.probe() == {'configured': True, 'reachable': True}
    assert provider == [dict(model=bedrock_health.BEDROCK_MODEL, max_tokens=1,
                             messages=[{'role': 'user', 'content': 'ping'}], timeout=5.0)]
    assert ai_consent.POLICIES == {}


@pytest.mark.parametrize('feature', ['health_probe', 'training_plan', 'nutrition_plan'])
@pytest.mark.parametrize('provider_name', ['bedrock', 'bedrock-stream', 'openai'])
def test_generic_admission_still_denies_user_calls(provider, feature, provider_name):
    with pytest.raises(ai_consent.ConsentDenied):
        with ai_provider_call.admit(feature=feature, provider=provider_name,
                                    payload=dict(model='test', max_tokens=1,
                                                 messages=[{'role': 'user', 'content': 'private'}]),
                                    gate=False, charge=False) as call:
            call.create(lambda **kwargs: pytest.fail('user data escaped'))
    assert provider == []


@pytest.mark.parametrize('name,status,category', [('PermissionDeniedError',403,'access_denied'),
    ('APITimeoutError',None,'timeout'), ('ServiceUnavailableError',503,'provider_error')])
def test_real_provider_failures_remain_unhealthy(monkeypatch, provider, name, status, category):
    error = type(name, (Exception,), {'status_code': status})
    def fail(**kwargs):
        raise error('never expose raw provider text')
    monkeypatch.setattr(extensions, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(stream=fail)))
    assert bedrock_health.probe() == {'configured': True, 'reachable': False, 'failure': category}


def test_disabled_does_not_call_provider(monkeypatch, provider):
    monkeypatch.setattr(bedrock_health, 'BEDROCK_ENABLED', False)
    assert bedrock_health.probe() == {'configured': False, 'reachable': False}
    assert provider == []


def test_operational_authority_accepts_no_caller_inputs(provider):
    with pytest.raises(TypeError):
        ai_provider_call.probe_bedrock_readiness(payload={'messages': ['private']})
    with pytest.raises(TypeError):
        ai_provider_call.probe_bedrock_readiness('private')
    assert provider == []


def test_health_feature_is_not_added_to_user_consent_authority():
    assert 'health_probe' not in ai_consent.BINDINGS


def test_probe_emits_no_subject_even_inside_user_scope(monkeypatch, provider):
    from app.services import ai_usage
    events = []
    monkeypatch.setattr(ai_usage, 'emit', lambda **kwargs: events.append(kwargs))
    with ai_spend_guard.subject_scope(123):
        assert bedrock_health._probe_once() == (True, None)
    assert len(events) == 1
    assert events[0]['subject_id'] is None
    assert events[0]['attempt'] == 1
    assert ai_spend_guard._local_counts == {}


def test_final_stream_failure_cannot_report_healthy(monkeypatch, provider):
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get_final_message(self):
            raise type('PermissionDeniedError', (Exception,), {})()
    monkeypatch.setattr(extensions, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream())))
    assert bedrock_health.probe() == {'configured': True, 'reachable': False, 'failure': 'access_denied'}
