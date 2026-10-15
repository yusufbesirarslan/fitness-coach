"""Real PostgreSQL fencing with synthetic owners and final mocked SDK capture."""
import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy import update, select

from app.services import ai_consent as consent, ai_provider_call as calls, ai, ai_spend_guard
from app.extensions import db
from app.models import User, AIConsentState, AIConsentEvent

pytestmark = pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('requires explicitly disposable PostgreSQL', allow_module_level=True)


@pytest.fixture
def pg_app(monkeypatch):
    from flask import Flask
    url = os.environ.get('PG_TEST_DATABASE_URL', '')
    if not url.startswith(('postgresql://', 'postgresql+psycopg2://')):
        pytest.fail('explicit disposable PostgreSQL URL required')
    app = Flask('d1-synthetic')
    app.config.update(TESTING=True, SECRET_KEY='d1-synthetic-key', SQLALCHEMY_DATABASE_URI=url,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    policies = {}
    for feature, (purpose, categories) in consent.BINDINGS.items():
        version = 'synthetic-' + feature
        policies[version] = consent.Disclosure(version, purpose, categories,
            (('bedrock', ai.BEDROCK_MODEL), ('openai', ai.OPENAI_MODEL)), (('en', 'a' * 64),))
    monkeypatch.setattr(consent, 'POLICIES', policies)
    monkeypatch.setattr(calls, 'model_concurrency_slot', lambda *a, **k: nullcontext())
    monkeypatch.setattr(ai_spend_guard, 'charge', lambda *a, **k: None)
    monkeypatch.setattr(calls.time, 'sleep', lambda _: None)
    with app.app_context():
        db.create_all()
        db.session.add_all([User(username='synthetic-a', email='a@example.invalid'),
                            User(username='synthetic-b', email='b@example.invalid')])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def command(feature='training_plan', generation=0, action='affirmative_grant', key='grant-0001'):
    return dict(action=action, purpose=consent.BINDINGS[feature][0], version='synthetic-' + feature,
                locale='en', text_digest='a'*64, expected_generation=generation, request_id=key)


def invoke(owner=1, feature='training_plan', recipient='openai', sdk=None):
    payload = dict(model=ai.OPENAI_MODEL if recipient == 'openai' else ai.BEDROCK_MODEL,
                   messages=[{'role': 'user', 'content': 'SYNTHETIC_OWN_FITNESS'}], max_tokens=100)
    with ai_spend_guard.subject_scope(owner):
        with calls.admit(feature=feature, provider=recipient, payload=payload, gate=False, charge=False) as call:
            return call.create(sdk or (lambda **kw: SimpleNamespace(usage=None)))


@pytest.mark.parametrize('recipient', ['openai', 'bedrock'])
def test_default_deny_at_final_sdk_for_both_recipients(pg_app, recipient):
    captured = []
    with pg_app.app_context():
        with pytest.raises(consent.ConsentDenied):
            invoke(recipient=recipient, sdk=lambda **kw: captured.append(kw))
    assert captured == []


@pytest.mark.parametrize('recipient', ['openai', 'bedrock'])
def test_frozen_final_sdk_capture_positive_and_revoked_negative(pg_app, recipient):
    captured = []
    with pg_app.app_context():
        consent.mutate(1, command())
        payload = dict(model=ai.OPENAI_MODEL if recipient == 'openai' else ai.BEDROCK_MODEL,
                       messages=[{'role': 'user', 'content': 'OWN_SYNTHETIC'}], max_tokens=100)
        with ai_spend_guard.subject_scope(1):
            with calls.admit(feature='training_plan', provider=recipient, payload=payload, gate=False, charge=False) as call:
                payload['messages'][0]['content'] = 'THIRD_PARTY_SOCIAL_CANARY'
                call.create(lambda **kw: captured.append(copy.deepcopy(kw)))
        assert captured[0]['messages'][0]['content'] == 'OWN_SYNTHETIC'
        assert captured[0]['timeout'] == 30.0
        consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))
        with pytest.raises(consent.ConsentDenied):
            invoke(recipient=recipient, sdk=lambda **kw: captured.append(kw))
        assert len(captured) == 1


def test_actual_nutrition_builder_to_openai_sdk_excludes_social_profile(pg_app, monkeypatch):
    from app.services.nutrition_plan_generation import generate_plan_options
    captured = []
    def sdk(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{"planlar": []}'))], usage=None)
    monkeypatch.setattr(ai, 'openai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=sdk))))
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        user = db.session.get(User, 1)
        user.full_name = 'EXCLUDED_OWNER_IDENTITY'
        user.user_metadata = {'friend': 'THIRD_PARTY_SOCIAL_CANARY'}
        db.session.commit()
        consent.mutate(1, command('nutrition_plan'))
        assert generate_plan_options(ai._openai_chat, 'en', 2100, 'fitness', ['eggs'], ['rice'], ['olive oil'], []) == []
        frozen = repr(captured)
        assert 'THIRD_PARTY_SOCIAL_CANARY' not in frozen and 'EXCLUDED_OWNER_IDENTITY' not in frozen
        assert '2100' in frozen
        assert len(captured) == 1


@pytest.mark.parametrize('feature', ['nutrition_plan', 'coach', 'summary', 'vision', 'nutrition', 'menu_ocr'])
def test_training_grant_cannot_authorize_other_purposes(pg_app, feature):
    captured = []
    with pg_app.app_context():
        consent.mutate(1, command())
        with pytest.raises(consent.ConsentDenied):
            invoke(feature=feature, sdk=lambda **kw: captured.append(kw))
        assert captured == []


def test_recipient_transition_and_unknown_model_denied(pg_app, monkeypatch):
    captured = []
    with pg_app.app_context():
        version = 'synthetic-training_plan'
        old = consent.POLICIES[version]
        monkeypatch.setattr(consent, 'POLICIES', {version: consent.Disclosure(old.version, old.purpose, old.categories,
                        (('bedrock', ai.BEDROCK_MODEL),), old.texts)})
        consent.mutate(1, command())
        invoke(recipient='bedrock', sdk=lambda **kw: captured.append('bedrock'))
        with pytest.raises(consent.ConsentDenied):
            invoke(recipient='openai', sdk=lambda **kw: captured.append('openai'))
        assert captured == ['bedrock']
        with ai_spend_guard.subject_scope(1), pytest.raises(consent.ConsentDenied):
            with calls.admit(feature='training_plan', provider='bedrock',
                payload=dict(model='unknown-profile', messages=[{'role':'user','content':'synthetic'}], max_tokens=100), gate=False, charge=False) as call:
                call.create(lambda **kw: captured.append('unknown'))
        assert captured == ['bedrock']


def test_cross_account_and_client_owner_spoofing(pg_app):
    with pg_app.app_context():
        consent.mutate(1, command())
        with pytest.raises(consent.ConsentDenied):
            invoke(owner=2)
        assert consent.status(2)['generation'] == 0
        with pytest.raises(consent.ConsentDenied):
            consent.mutate(2, dict(command(), owner_id=1))
        with ai_spend_guard.subject_scope(2), pytest.raises(consent.ConsentDenied):
            with consent.dispatch(1, 'training_plan', 'openai', {'model': ai.OPENAI_MODEL}, 1):
                pytest.fail('mismatched authenticated owner')


@pytest.mark.parametrize('replacement', [None, [], {}, {'training.plan': {'version': []}},
    {'training.plan': {'version': 'obsolete', 'state': 'granted'}}])
def test_corrupt_durable_authority_denies_sdk(pg_app, replacement):
    captured = []
    with pg_app.app_context():
        consent.mutate(1, command())
        with db.engine.begin() as conn:
            conn.execute(update(AIConsentState).where(AIConsentState.owner_id == 1).values(grants=replacement))
        with pytest.raises(consent.ConsentDenied):
            invoke(sdk=lambda **kw: captured.append(kw))
        assert captured == []


def test_idempotent_grant_and_stale_replay_cannot_undo_revoke(pg_app):
    with pg_app.app_context():
        assert consent.mutate(1, command())['generation'] == 1
        assert consent.mutate(1, command())['generation'] == 1
        assert consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))['generation'] == 2
        assert consent.mutate(1, command())['generation'] == 2
        with pytest.raises(consent.ConsentDenied):
            consent.mutate(1, command(key='new-grant-0002'))
        with pytest.raises(consent.ConsentDenied):
            invoke()
        assert db.session.query(AIConsentEvent).count() == 2


def test_retry_revalidates_after_committed_revoke(pg_app):
    class APIConnectionError(Exception):
        pass
    captured = []
    def sdk(**kwargs):
        captured.append(kwargs)
        raise APIConnectionError()
    def backoff(_):
        consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))
    with pg_app.app_context():
        consent.mutate(1, command())
        from unittest.mock import patch
        with patch.object(calls.time, 'sleep', backoff), pytest.raises(consent.ConsentDenied):
            invoke(sdk=sdk)
        assert len(captured) == 1


def test_admission_waiting_for_capacity_does_not_survive_revoke_regrant(pg_app):
    captured = []
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        consent.mutate(1, command())
        with calls.admit(feature='training_plan', provider='openai', payload=dict(
            model=ai.OPENAI_MODEL, messages=[{'role':'user','content':'synthetic'}], max_tokens=100), gate=False, charge=False) as call:
            consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))
            consent.mutate(1, command(generation=2, key='regrant-0001'))
            with pytest.raises(consent.ConsentDenied):
                call.create(lambda **kw: captured.append(kw))
        assert captured == []


def test_concurrent_dispatch_orders_revoke_acknowledgement(pg_app):
    entered, release, withdrawing, acknowledged = [threading.Event() for _ in range(4)]
    captured = []
    with pg_app.app_context():
        consent.mutate(1, command())
    def sdk(**kwargs):
        captured.append('submitted')
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(usage=None)
    def dispatch():
        with pg_app.app_context():
            invoke(sdk=sdk)
    def withdraw():
        with pg_app.app_context():
            withdrawing.set()
            consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))
            acknowledged.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempt = pool.submit(dispatch)
        assert entered.wait(5)
        withdrawal = pool.submit(withdraw)
        assert withdrawing.wait(5)
        assert not acknowledged.wait(.15)
        release.set()
        attempt.result(timeout=5)
        withdrawal.result(timeout=5)
    assert acknowledged.is_set()
    with pg_app.app_context(), pytest.raises(consent.ConsentDenied):
        invoke(sdk=lambda **kw: captured.append('forbidden'))
    assert captured == ['submitted']


def test_concurrent_mutations_have_one_winner(pg_app):
    barrier = threading.Barrier(2)
    def grant(index):
        with pg_app.app_context():
            barrier.wait(5)
            try:
                return consent.mutate(1, command(key=f'grant-{index:04}'))['generation']
            except consent.ConsentDenied as error:
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(grant, [1, 2]))
    assert sorted(map(str, outcomes)) == ['1', 'AI_CONSENT_CONFLICT']


def test_deleted_account_cannot_dispatch_or_inherit_consent(pg_app):
    with pg_app.app_context():
        consent.mutate(1, command())
        db.session.delete(db.session.get(User, 1))
        db.session.commit()
        assert db.session.query(AIConsentState).count() == 0
        assert db.session.query(AIConsentEvent).count() == 0
        with pytest.raises(consent.ConsentDenied):
            invoke(owner=1)


def test_streaming_remains_disabled_with_grant(pg_app):
    captured = []
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        consent.mutate(1, command())
        with pytest.raises(consent.ConsentDenied):
            with calls.admit(feature='training_plan', provider='bedrock', payload=dict(
                model=ai.BEDROCK_MODEL, messages=[{'role':'user','content':'synthetic'}], max_tokens=100), gate=False, charge=False) as call:
                with call.stream(lambda **kw: captured.append(kw)):
                    pytest.fail('stream must be closed')
        assert captured == []


def test_withdrawal_lock_timeout_is_unavailable_never_false_ack(pg_app):
    entered, release = threading.Event(), threading.Event()
    with pg_app.app_context():
        consent.mutate(1, command())
    def sdk(**kwargs):
        entered.set()
        assert release.wait(6)
        return SimpleNamespace(usage=None)
    def dispatch():
        with pg_app.app_context():
            invoke(sdk=sdk)
    with ThreadPoolExecutor(max_workers=1) as pool:
        attempt = pool.submit(dispatch)
        assert entered.wait(5)
        with pg_app.app_context(), pytest.raises(consent.ConsentDenied) as error:
            consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))
        assert error.value.status == 503
        release.set()
        attempt.result(timeout=5)
    with pg_app.app_context():
        assert consent.status(1)['generation'] == 1
        assert consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))['generation'] == 2


def test_exact_migration_empty_upgrade_and_safe_downgrade(pg_app):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text, inspect
    source = Path(__file__).parents[1] / 'migrations/versions/d1c0a15e0001_ai_consent_authority.py'
    spec = importlib.util.spec_from_file_location('d1_migration', source)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == 'b1a8c9d0e1f2'
    with pg_app.app_context(), db.engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA d1_migration_test'))
        conn.execute(text('SET LOCAL search_path TO d1_migration_test'))
        conn.execute(text('CREATE TABLE "user" (id integer PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert set(inspect(conn).get_table_names(schema='d1_migration_test')) == {'user', 'ai_consent_state', 'ai_consent_event'}
            assert conn.execute(text('SELECT count(*) FROM ai_consent_state')).scalar_one() == 0
            conn.execute(text('INSERT INTO "user" VALUES (1)'))
            conn.execute(text("INSERT INTO ai_consent_state VALUES (1, 1, '{}', CURRENT_TIMESTAMP)"))
            with pytest.raises(RuntimeError, match='refuses to erase'):
                migration.downgrade()
            conn.execute(text('DELETE FROM ai_consent_state'))
            migration.downgrade()
        conn.execute(text('SET LOCAL search_path TO public'))
        conn.execute(text('DROP SCHEMA d1_migration_test CASCADE'))


def test_actual_bedrock_helper_captures_final_sdk_arguments(pg_app, monkeypatch):
    captured = []
    def sdk(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return SimpleNamespace(stop_reason='end_turn', usage=None,
                               content=[SimpleNamespace(type='text', text='SYNTHETIC_RESULT')])
    monkeypatch.setattr(ai, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(create=sdk)))
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        consent.mutate(1, command())
        assert ai._claude_chat([{'role':'user','content':'OWN_SYNTHETIC_TRAINING'}],
            max_tokens=100, feature='training_plan') == 'SYNTHETIC_RESULT'
        assert captured[0]['messages'] == [{'role':'user','content':'OWN_SYNTHETIC_TRAINING'}]
        consent.mutate(1, command(generation=1, action='revoke', key='revoke-0001'))
        with pytest.raises(consent.ConsentDenied):
            ai._claude_chat([{'role':'user','content':'NOT_SENT'}], max_tokens=100, feature='training_plan')
        assert len(captured) == 1


def test_actual_heavy_router_fallback_does_not_inherit_recipient_permission(pg_app, monkeypatch):
    captured = []
    def bedrock(**kwargs):
        captured.append(('bedrock', copy.deepcopy(kwargs)))
        raise RuntimeError('synthetic provider failure')
    def openai(**kwargs):
        captured.append(('openai', kwargs))
        pytest.fail('unlisted fallback recipient was dispatched')
    monkeypatch.setattr(ai, 'BEDROCK_ENABLED', True)
    monkeypatch.setattr(ai, 'bedrock_client', SimpleNamespace(messages=SimpleNamespace(create=bedrock)))
    monkeypatch.setattr(ai, 'openai_client', SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=openai))))
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        version = 'synthetic-training_plan'
        old = consent.POLICIES[version]
        monkeypatch.setattr(consent, 'POLICIES', {version: consent.Disclosure(old.version, old.purpose,
            old.categories, (('bedrock', ai.BEDROCK_MODEL),), old.texts)})
        consent.mutate(1, command())
        with pytest.raises(consent.ConsentDenied):
            ai._heavy_chat([{'role':'user','content':'SYNTHETIC_TRAINING'}], max_tokens=100, feature='training_plan')
        assert [provider for provider, _ in captured] == ['bedrock']


def test_migration_accepts_create_all_boot_but_rejects_partial_schema(pg_app):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    source = Path(__file__).parents[1] / 'migrations/versions/d1c0a15e0001_ai_consent_authority.py'
    spec = importlib.util.spec_from_file_location('d1_create_all_migration', source)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with pg_app.app_context(), db.engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()  # all current models already created by fixture
            assert conn.execute(text('SELECT count(*) FROM ai_consent_state')).scalar_one() == 0
            conn.execute(text('ALTER TABLE ai_consent_event DROP CONSTRAINT ck_ai_consent_action'))
            with pytest.raises(RuntimeError, match='incompatible checks'):
                migration.upgrade()
            conn.execute(text("ALTER TABLE ai_consent_event ADD CONSTRAINT ck_ai_consent_action CHECK (action IN ('affirmative_grant', 'revoke')) NOT VALID"))
            with pytest.raises(RuntimeError, match='not validated'):
                migration.upgrade()
