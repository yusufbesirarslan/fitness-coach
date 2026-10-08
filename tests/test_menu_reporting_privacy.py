"""LP15-GR1: complete envelopes from the production SDK configuration, offline."""
import base64
import json
import logging
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID

import pytest
import sentry_sdk
from flask import Flask, has_request_context, request
from sentry_sdk.transport import Transport

from app.observability import assign_request_id, init_sentry

SENTINELS = {name: f'GR1_PRIVATE_{name}_ş&/+' for name in (
    'url_path', 'url_query', 'userinfo', 'qr', 'html', 'body', 'title', 'dish',
    'category', 'macro', 'token', 'token_payload', 'analysis_id', 'candidate_id',
    'idempotency', 'fingerprint', 'user_id', 'email', 'sub', 'authorization',
    'cookie', 'provider', 'prompt', 'exception', 'breadcrumb', 'local', 'span')}
SENTINELS['idempotency'] = 'GR1_PRIVATE_idempotency_0001'
URL = 'https://public.example/' + quote(SENTINELS['url_path']) + '?q=' + quote(SENTINELS['url_query'])


@pytest.fixture(autouse=True)
def hermetic_reporting_env(monkeypatch):
    monkeypatch.setenv('SENTRY_DSN', '')
    monkeypatch.setenv('SENTRY_ENVIRONMENT', 'privacy-test')
    monkeypatch.setenv('SENTRY_RELEASE', 'privacy-test')
    monkeypatch.setattr('app.observability.uuid.uuid4', lambda: UUID('b' * 32))


@pytest.fixture
def reporting(app, monkeypatch):
    envelopes = []

    class LocalTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope.serialize())

    original_init = sentry_sdk.init
    previous = sentry_sdk.get_client()

    def offline_init(**options):
        options['transport'] = LocalTransport
        return original_init(**options)

    monkeypatch.setattr(sentry_sdk, 'init', offline_init)
    monkeypatch.setenv('SENTRY_DSN', 'https://synthetic@example.invalid/1')
    monkeypatch.setenv('SENTRY_TRACES_SAMPLE_RATE', '1')
    with sentry_sdk.isolation_scope() as scope:
        scope.clear()
        init_sentry(app)
        client = sentry_sdk.get_client()
        assert {'flask', 'logging', 'threading'} <= set(client.integrations)
        assert client.integrations['logging']._handler.level == logging.ERROR
        assert client.integrations['logging']._breadcrumb_handler.level == logging.INFO
        yield envelopes, client
        client.flush()
        client.close()
        sentry_sdk.get_global_scope().set_client(previous)


def contaminate():
    """Simulate upstream enrichers and breadcrumbs accumulated before a request."""
    sentry_sdk.get_isolation_scope().add_attachment(
        bytes=json.dumps(SENTINELS).encode(), filename=SENTINELS['body'])
    sentry_sdk.set_user({key: SENTINELS[key] for key in ('user_id', 'email', 'sub')})
    sentry_sdk.set_context('provider', dict(SENTINELS))
    for key, value in SENTINELS.items():
        sentry_sdk.set_extra(key, value)
        sentry_sdk.set_tag(key, value)
    sentry_sdk.add_breadcrumb(message=SENTINELS['breadcrumb'], data=dict(SENTINELS))

    def upstream(event, hint):
        # Integration/plugin payloads must also fail closed, even if SDK body
        # capture is disabled. Seed ALL structures before the real before_send.
        event.setdefault('request', {}).update(
            data=request.get_json(silent=True) if has_request_context() else dict(SENTINELS),
            headers={'Authorization': SENTINELS['authorization'], 'Cookie': SENTINELS['cookie']},
            query_string=SENTINELS['url_query'])
        event.setdefault('breadcrumbs', {}).setdefault('values', []).append(
            {'message': SENTINELS['breadcrumb']})
        event['spans'] = [{'description': SENTINELS['span']}]
        event['transaction_info'] = {'source': SENTINELS['span']}
        for value in event.get('exception', {}).get('values', []):
            value.setdefault('stacktrace', {}).setdefault('frames', []).append(
                {'function': 'synthetic', 'vars': dict(SENTINELS)})
        return event

    sentry_sdk.get_isolation_scope().add_event_processor(upstream)


def assert_clean(envelopes, client, *, event_name=None, forbidden=()):
    client.flush()
    assert envelopes, 'No outgoing reporting envelope: privacy check is vacuous'
    serialized = b'\n'.join(envelopes).decode()
    for value in (*SENTINELS.values(), *forbidden):
        for representation in (value, json.dumps(value)[1:-1], quote(value), quote(value, safe=''),
                               base64.b64encode(value.encode()).decode(),
                               base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')):
            assert representation not in serialized
    events = []
    for raw in envelopes:
        lines = raw.splitlines()
        for index in range(1, len(lines), 2):
            header = json.loads(lines[index])
            assert header['type'] != 'transaction'
            if header['type'] == 'event':
                events.append(json.loads(lines[index + 1]))
    assert events
    event = events[-1]
    assert event['tags']['event'] == (event_name or 'menu_failure')
    assert event['tags']['error_type'] == 'RuntimeError'
    assert len(event['tags']['request_id']) == 16
    assert set(event['request']) == {'url', 'method'}
    assert event['request']['method'] == 'POST'
    assert 'user' not in event and 'breadcrumbs' not in event and 'extra' not in event
    for value in event.get('exception', {}).get('values', []):
        assert set(value) == {'type'}
    return event


@pytest.mark.parametrize('phase', ['fetch', 'parser', 'provider'])
def test_analyze_failure_envelope(app, raw_client, make_user, monkeypatch, reporting, phase):
    from app.services import menu_analysis, mobile_auth
    owner = make_user('report-owner', email=SENTINELS['email'], cognito_sub=SENTINELS['sub'])
    monkeypatch.setattr(mobile_auth, 'authenticate_access', lambda raw:
        mobile_auth.MobilePrincipal(owner, SimpleNamespace(id=1), {'sub': owner.cognito_sub}))
    scan = dict(body_text=SENTINELS['body'], title=SENTINELS['title'],
                headings=[SENTINELS['category']], raw_text=SENTINELS['html'])

    def boom(*args, **kwargs):
        menu_body = dict(SENTINELS)  # actual sensitive stack locals
        raise RuntimeError(SENTINELS['exception'] + SENTINELS['provider'] + str(menu_body))

    monkeypatch.setattr(menu_analysis, 'acquire_menu', boom if phase == 'fetch' else lambda *a, **kw: scan)
    monkeypatch.setattr(menu_analysis, 'analysis_request_from_scan', lambda scan: {'raw_text': scan['body_text']})
    monkeypatch.setattr(menu_analysis, 'analyze_menu_text', boom)
    contaminate()
    raw_client.set_cookie('private', SENTINELS['cookie'])
    response = raw_client.post('/api/v1/nutrition/menu/analyze?q=' + quote(SENTINELS['url_query']), json={'url': URL},
                               headers={'Authorization': 'Bearer ' + SENTINELS['authorization']})
    assert response.status_code == 503
    event = assert_clean(*reporting, event_name='acquisition_failed' if phase == 'fetch' else 'analysis_failed')
    assert event['tags']['request_id'] == response.json['error']['request_id']


def test_confirm_failure_envelope(app, raw_client, make_user, monkeypatch, reporting):
    from app.services import mobile_auth, mobile_log_food, mobile_menu
    owner = make_user('confirm-owner', id=9731562, email=SENTINELS['email'], cognito_sub=SENTINELS['sub'])
    monkeypatch.setattr(mobile_auth, 'authenticate_access', lambda raw:
        mobile_auth.MobilePrincipal(owner, SimpleNamespace(id=1), {'sub': owner.cognito_sub}))
    token = mobile_menu.issue_item_proof(
        app.config['SECRET_KEY'], owner.id, analysis_id=SENTINELS['analysis_id'],
        candidate_id=SENTINELS['candidate_id'], name=SENTINELS['dish'],
        portion={'basis': 'serving', 'quantity': 1, 'stated_grams': None},
        nutrition={'energy_kcal': 98761, 'protein_g': 43219,
                   'carbohydrate_g': 38765, 'fat_g': 26543}, source='llm', confidence=0.5)

    def boom(*args):
        token_payload = dict(SENTINELS)
        raise RuntimeError(SENTINELS['exception'] + str(token_payload))

    monkeypatch.setattr(mobile_log_food, 'log_food', boom)
    contaminate()
    raw_client.set_cookie('private', SENTINELS['cookie'])
    response = raw_client.post('/api/v1/nutrition/menu/log?q=' + quote(SENTINELS['url_query']), json={
        'confirmation_token': token, 'quantity': 1, 'slot': 'ogle', 'confirmed': True},
        headers={'Authorization': 'Bearer ' + SENTINELS['authorization'],
                 'Idempotency-Key': SENTINELS['idempotency']})
    assert response.status_code == 503
    assert_clean(*reporting, event_name='menu_log_failed',
                 forbidden=(token, str(owner.id), '98761', '43219', '38765', '26543'))


def test_unhandled_menu_exception_locals(app, reporting):
    # Genuine Flask unhandled exception, outside the adapter's handled catch.
    target = Flask('unhandled-menu')
    target.before_request(assign_request_id)

    @target.post('/api/v1/nutrition/menu/analyze')
    def unhandled():
        menu_body = dict(SENTINELS)
        logging.getLogger('app.services.menu_analysis').warning(SENTINELS['breadcrumb'])
        raise RuntimeError(SENTINELS['exception'] + str(menu_body))

    contaminate()
    client = target.test_client()
    client.set_cookie('private', SENTINELS['cookie'])
    response = client.post('/api/v1/nutrition/menu/analyze', json={'url': URL},
        headers={'Authorization': SENTINELS['authorization'], 'Cookie': SENTINELS['cookie']})
    assert response.status_code == 500
    assert_clean(*reporting)
    assert reporting[1].options['include_local_variables'] is False
    assert reporting[1].options['max_request_body_size'] == 'never'
    assert reporting[1].options['enable_logs'] is False


def test_note_privacy_real_envelope(app, reporting):
    contaminate()
    with app.test_request_context('/api/v1/training/exercises/opaque/note', method='PUT',
                                  json={'text': SENTINELS['body']}):
        assign_request_id()
        sentry_sdk.capture_exception(RuntimeError(SENTINELS['exception']))
        logging.getLogger('app').error(SENTINELS['breadcrumb'])
    reporting[1].flush()
    assert reporting[0] == []


def test_worker_error_and_breadcrumb_without_request(app, reporting):
    from concurrent.futures import ThreadPoolExecutor
    contaminate()

    def worker():
        with app.app_context():
            try:
                menu_body = dict(SENTINELS)
                raise RuntimeError(SENTINELS['exception'] + str(menu_body))
            except RuntimeError:
                logger = logging.getLogger('app.services.ai')
                logger.warning(SENTINELS['breadcrumb'])
                logger.error('provider failed', exc_info=True)
            # A capture_exception without log_record or Flask request: classify
            # it by the real shared menu/provider frame in its traceback.
            from app.services import ai

            class BrokenMenuInput:
                def __iter__(self):
                    raise RuntimeError(SENTINELS['prompt'])

            try:
                ai._claude_chat(BrokenMenuInput(), feature='menu_extract')
            except RuntimeError as error:
                sentry_sdk.capture_exception(error)

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(worker).result()
    reporting[1].flush()
    assert reporting[0]
    serialized = b'\n'.join(reporting[0]).decode()
    assert 'RuntimeError' in serialized
    assert 'menu_failure' in serialized
    for value in SENTINELS.values():
        assert value not in serialized and json.dumps(value)[1:-1] not in serialized


@pytest.mark.parametrize('operation', ['http.client', 'gen_ai.chat'])
def test_menu_transaction_and_span_metadata_dropped(app, reporting, operation):
    contaminate()
    with app.test_request_context('/api/v1/nutrition/menu/analyze', method='POST', json={'url': URL}):
        assign_request_id()
        with sentry_sdk.start_transaction(name='/api/v1/nutrition/menu/analyze') as transaction:
            transaction.set_data('private', dict(SENTINELS))
            with sentry_sdk.start_span(op=operation, name=URL) as span:
                span.set_data('private', dict(SENTINELS))
    reporting[1].flush()
    assert reporting[0] == []


@pytest.mark.parametrize('provider', ['openai', 'bedrock', 'ocr'])
def test_provider_and_ocr_source_logs_type_only(app, monkeypatch, caplog, provider):
    import httpx
    import openai
    import anthropic
    from app.services import ai, ai_provider_call, menu_ocr
    error_class = anthropic.APIError if provider == 'bedrock' else openai.APIError
    error = error_class(SENTINELS['provider'], request=httpx.Request('POST', URL), body=dict(SENTINELS))

    def rejected(*args, **kwargs):
        raise error

    monkeypatch.setattr(ai_provider_call, 'admit', rejected)
    if provider == 'bedrock':
        monkeypatch.setattr(ai, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(create=None)))
    with app.app_context(), caplog.at_level(logging.WARNING):
        if provider == 'ocr':
            assert menu_ocr._extract_text_from_image(b'synthetic-image') == ''
        else:
            call = ai._claude_chat if provider == 'bedrock' else ai._openai_chat
            with pytest.raises(RuntimeError):
                call([{'role': 'user', 'content': SENTINELS['prompt']}], feature='menu_extract')
    assert 'APIError' in caplog.text
    for value in SENTINELS.values():
        assert value not in caplog.text
