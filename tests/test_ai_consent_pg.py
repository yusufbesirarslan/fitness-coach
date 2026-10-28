"""Real PostgreSQL fencing with synthetic owners and final mocked SDK capture."""
import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import update, select

from app import config as app_config
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
            (('bedrock', app_config.BEDROCK_MODEL), ('openai', ai.OPENAI_MODEL)), (('en', 'a' * 64),))
    from consent_projection_support import publish_synthetic_texts
    policies = publish_synthetic_texts(monkeypatch, policies)
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
                locale='en', text_digest=dict(consent.POLICIES['synthetic-' + feature].texts)['en'], contract_digest=consent.contract_digest(consent.POLICIES['synthetic-' + feature]),
                expected_generation=generation, request_id=key)


def invoke(owner=1, feature='training_plan', recipient='openai', sdk=None):
    payload = dict(model=ai.OPENAI_MODEL if recipient == 'openai' else app_config.BEDROCK_MODEL,
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
        payload = dict(model=ai.OPENAI_MODEL if recipient == 'openai' else app_config.BEDROCK_MODEL,
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
                        (('bedrock', app_config.BEDROCK_MODEL),), old.texts)})
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
                model=app_config.BEDROCK_MODEL, messages=[{'role':'user','content':'synthetic'}], max_tokens=100), gate=False, charge=False) as call:
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
    assert migration.down_revision == 'b0d3e4f5a6b7'
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
            old.categories, (('bedrock', app_config.BEDROCK_MODEL),), old.texts)})
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


def test_editing_same_version_contract_never_expands_existing_grant(pg_app, monkeypatch):
    captured = []
    with pg_app.app_context():
        version = 'synthetic-training_plan'
        old = consent.POLICIES[version]
        restricted = consent.Disclosure(old.version, old.purpose, old.categories,
            (('bedrock', app_config.BEDROCK_MODEL),), old.texts)
        monkeypatch.setattr(consent, 'POLICIES', {version: restricted})
        stale_action = command()
        consent.mutate(1, stale_action)
        monkeypatch.setattr(consent, 'POLICIES', {version: old})
        with pytest.raises(consent.ConsentDenied):
            invoke(recipient='openai', sdk=lambda **kw: captured.append(kw))
        stale_action.update(expected_generation=1, request_id='stale-accept-0002')
        with pytest.raises(consent.ConsentDenied):
            consent.mutate(1, stale_action)
        assert consent.status(1)['scopes']['training.plan']['state'] == 'unavailable'
        assert captured == []


@pytest.fixture
def chain_app():
    """Own a throwaway database: never stamp models as migration evidence."""
    from uuid import uuid4
    from flask import Flask
    from flask_migrate import Migrate
    import sqlalchemy as sa
    url = sa.engine.make_url(os.environ['PG_TEST_DATABASE_URL'])
    assert url.get_backend_name() == 'postgresql'
    name = 'd1_chain_' + uuid4().hex
    admin = sa.create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        assert conn.dialect.server_version_info[:1] == (16,)
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    app = Flask('d1-chain-synthetic')
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=url.set(database=name),
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    Migrate(app, db)
    try:
        with app.app_context():
            try:
                yield app
            finally:
                db.session.remove()
                db.engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def _chain_upgrade(revision='head'):
    from pathlib import Path
    from flask_migrate import upgrade
    upgrade(directory=str(Path(__file__).parents[1] / 'migrations'), revision=revision)


def _assert_chain_head_and_parity():
    from pathlib import Path
    from alembic.script import ScriptDirectory
    from alembic.migration import MigrationContext
    from alembic.autogenerate import compare_metadata
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / 'migrations'))
    assert scripts.get_heads() == ['c6f8b0d2e4a1']
    assert len(list(scripts.walk_revisions())) == 60
    assert scripts.get_revision('a4d6f8b0c2e1').down_revision == 'd7e3a9c1f5b8'
    assert scripts.get_revision('d7e3a9c1f5b8').down_revision == 'f9c6d0a1b2e3'
    assert scripts.get_revision('f9c6d0a1b2e3').down_revision == 'f8b2d4e6a0c3'
    assert scripts.get_revision('f8b2d4e6a0c3').down_revision == 'e7a1c3d5f9b2'
    assert scripts.get_revision('e7a1c3d5f9b2').down_revision == 'b1e4f5a6c7d8'
    assert scripts.get_revision('b1e4f5a6c7d8').down_revision == 'd1c0a15e0001'
    assert scripts.get_revision('d1c0a15e0001').down_revision == 'b0d3e4f5a6b7'
    assert scripts.get_revision('b0d3e4f5a6b7').down_revision == 'a9c1d2e3f4b5'
    assert scripts.get_revision('a9c1d2e3f4b5').down_revision == 'c2b9d0e1f2a3'
    assert scripts.get_revision('c2b9d0e1f2a3').down_revision == 'b1a8c9d0e1f2'
    with db.engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={'compare_type': True})
        assert context.get_current_heads() == ('c6f8b0d2e4a1',)
        assert compare_metadata(context, db.metadata) == []
        checks = conn.execute(sa.text(
            "SELECT conname, convalidated FROM pg_constraint WHERE conrelid IN "
            "('ai_consent_state'::regclass, 'ai_consent_event'::regclass) AND contype='c'"
        )).all()
        assert dict(checks) == {'ck_ai_consent_generation': True,
                               'ck_ai_consent_event_generation': True,
                               'ck_ai_consent_action': True}


def test_fresh_empty_postgres_complete_chain_has_no_implicit_grants(chain_app):
    import sqlalchemy as sa
    with db.engine.connect() as conn:
        assert sa.inspect(conn).get_table_names() == []
    _chain_upgrade()
    _assert_chain_head_and_parity()
    with db.engine.connect() as conn:
        for table in ('user', 'apple_account_association', 'apple_subscription',
                      'ai_starter_grant', 'ai_allowance_bucket', 'ai_usage_reservation',
                      'ai_consent_state', 'ai_consent_event'):
            assert conn.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar_one() == 0
    _chain_upgrade()  # Alembic head reentry
    _assert_chain_head_and_parity()


def test_existing_merged_main_upgrade_preserves_all_rows_and_schema(chain_app):
    import sqlalchemy as sa
    from datetime import datetime, timezone, timedelta
    from ai_allowance_support import premium_row
    from app.models import AppleAccountAssociation, AiStarterGrant, AiAllowanceBucket, TrainingPlan
    _chain_upgrade('b0d3e4f5a6b7')
    # Seed the historical schema through reflected tables, not future ORM columns.
    operation_table = sa.Table('training_plan_generation_operation', sa.MetaData(), autoload_with=db.engine)
    reservation_table = sa.Table('ai_usage_reservation', sa.MetaData(), autoload_with=db.engine)
    now = datetime.now(timezone.utc)
    db.session.add_all([User(id=1, username='chain-a', email='chain-a@example.invalid'),
                        User(id=2, username='chain-b', email='chain-b@example.invalid')])
    db.session.commit()
    for uid in (1, 2):
        premium_row(uid, original_id=str(900 + uid))
        db.session.add_all([
            AppleAccountAssociation(user_id=uid, token=f'00000000-0000-0000-0000-{uid:012d}'),
            AiStarterGrant(user_id=uid, policy_version='synthetic-v1', granted_at=now),
            AiAllowanceBucket(id=uid, user_id=uid, scope='STARTER', feature='coach',
                period_key='NEVER', policy_version='synthetic-v1', granted=10, used=2,
                reserved=1, billed_releases=0, created_at=now, updated_at=now),
            TrainingPlan(user_id=uid, plan_data='{"synthetic": true}', score=7),
        ])
    db.session.commit()
    for uid in (1, 2):
        db.session.execute(operation_table.insert().values(user_id=uid, idempotency_key='synthetic-chain',
            request_fingerprint='a'*64, status='IN_PROGRESS', attempt_count=1, quota_reserved=False,
            created_at=now, updated_at=now, provider_dispatch_possible=None if uid == 1 else True))
        db.session.execute(reservation_table.insert().values(public_id=str(uid)*32, user_id=uid, bucket_id=uid,
            feature='coach', key_digest=str(uid)*64, state='RESERVED', provider_billed=False,
            created_at=now, expires_at=now + timedelta(minutes=15)))
    db.session.commit()
    db.session.remove()

    def snapshot(conn, names):
        inspector = sa.inspect(conn)
        return {name: (sorted(conn.exec_driver_sql(
                    f'SELECT row_to_json(t)::text FROM "{name}" t').scalars()),
                inspector.get_columns(name), inspector.get_foreign_keys(name),
                inspector.get_unique_constraints(name), inspector.get_check_constraints(name),
                inspector.get_indexes(name), inspector.get_pk_constraint(name)) for name in names}

    with db.engine.connect() as conn:
        names = set(sa.inspect(conn).get_table_names()) - {'alembic_version'}
        assert not names & {'ai_consent_state', 'ai_consent_event'}
        before = snapshot(conn, names)
    # Keep the original D1-only preservation assertion byte-for-byte in scope.
    _chain_upgrade('d1c0a15e0001')
    with db.engine.connect() as conn:
        # SQLAlchemy type objects compare by identity; canonical repr preserves
        # their actual reflected type/length while comparing every old object.
        assert repr(snapshot(conn, names)) == repr(before)
        assert set(sa.inspect(conn).get_table_names()) == names | {
            'alembic_version', 'ai_consent_state', 'ai_consent_event'}
        assert conn.exec_driver_sql('SELECT count(*) FROM ai_consent_state').scalar_one() == 0
        assert conn.exec_driver_sql('SELECT count(*) FROM ai_consent_event').scalar_one() == 0
    # The create_all-first contract also permits exact migration reentry.
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from pathlib import Path
    migration = ScriptDirectory(str(Path(__file__).parents[1] / 'migrations')).get_revision('d1c0a15e0001').module
    with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
        assert repr(snapshot(conn, names)) == repr(before)
        conn.execute(sa.insert(AIConsentState), dict(owner_id=1, generation=1, grants={}, changed_at=now))
        conn.execute(sa.insert(AIConsentEvent), dict(owner_id=1, generation=1, request_id='synthetic',
            request_digest='a'*64, purpose='synthetic', version='synthetic', action='revoke',
            locale='en', text_digest='a'*64, contract_digest='b'*64, occurred_at=now))
        with pytest.raises(RuntimeError, match='refuses to erase'):
            migration.downgrade()
        # Foreign keys are enforced; account deletion cascades just its evidence.
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            conn.execute(sa.insert(AIConsentState), dict(owner_id=999, generation=1, grants={}, changed_at=now))
        conn.execute(sa.delete(User).where(User.id == 1))
        assert conn.exec_driver_sql('SELECT count(*) FROM ai_consent_state').scalar_one() == 0
        assert conn.exec_driver_sql('SELECT count(*) FROM ai_consent_event').scalar_one() == 0
        assert conn.exec_driver_sql('SELECT user_id FROM apple_subscription').scalar_one() == 2
        assert conn.exec_driver_sql('SELECT user_id FROM ai_usage_reservation').scalar_one() == 2

    # Then qualify the additive allowance successor and full current-model parity.
    _chain_upgrade()
    _assert_chain_head_and_parity()


@pytest.mark.parametrize('table,constraint,predicate', [
    ('ai_consent_state', 'ck_ai_consent_generation', 'generation > 0'),
    ('ai_consent_event', 'ck_ai_consent_event_generation', 'generation > 0'),
    ('ai_consent_event', 'ck_ai_consent_action', "action IN ('affirmative_grant', 'revoke')"),
])
@pytest.mark.parametrize('damage', ['name_only', 'not_valid'])
def test_all_d1_checks_reject_unsafe_substitutes(pg_app, table, constraint, predicate, damage):
    from pathlib import Path
    from alembic.script import ScriptDirectory
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = ScriptDirectory(str(Path(__file__).parents[1] / 'migrations')).get_revision('d1c0a15e0001').module
    with pg_app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql(f'ALTER TABLE {table} DROP CONSTRAINT {constraint}')
        check = '1=1' if damage == 'name_only' else predicate
        suffix = '' if damage == 'name_only' else ' NOT VALID'
        conn.exec_driver_sql(f'ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ({check}){suffix}')
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            migration.upgrade()


@pytest.mark.parametrize('recipient', ['openai', 'bedrock'])
def test_paid_entitlement_and_reserved_allowance_cannot_authorize_sdk(pg_app, monkeypatch, recipient):
    from ai_allowance_support import (enable_subscriptions, enable_ledger, fixture_policy, premium_row)
    from app.services import ai_allowances as ledger, subscriptions
    from dataclasses import replace
    with pg_app.app_context():
        enable_subscriptions(pg_app, monkeypatch)
        policy = replace(fixture_policy(starter=False, premium=True),
                         premium=ledger.ScopePolicy('WEEKLY', {'training_generation': 1}, 2))
        enable_ledger(pg_app, policy)
        premium_row(1)
        assert subscriptions.entitlement(1)['effective_premium'] is True
        reservation = ledger.reserve(1, 'training_generation', 'synthetic-integration-key')
        assert reservation.may_execute is True
        captured = []
        for owner in (1, 2):
            with pytest.raises(consent.ConsentDenied):
                invoke(owner=owner, recipient=recipient, sdk=lambda **kw: captured.append(kw))
        assert captured == []
        assert db.session.query(AIConsentState).count() == db.session.query(AIConsentEvent).count() == 0
        assert consent.memory_available(1) is False


@pytest.mark.parametrize('feature', ['coach', 'training_plan', 'nutrition_plan', 'vision', 'menu_extract'])
def test_phase2_purposes_are_independent_and_original_generation_is_fenced(pg_app, feature):
    with pg_app.app_context(), ai_spend_guard.subject_scope(1):
        consent.mutate(1, command(feature))
        authority = consent.begin_operation(1, feature, 'openai', ai.OPENAI_MODEL)
        consent.authorize_operation(authority)
        captured = []
        with consent.operation_scope(authority):
            invoke(feature=feature, sdk=lambda **payload: captured.append(payload))
        assert len(captured) == 1
        for other in consent.BINDINGS:
            if other == feature:
                continue
            with pytest.raises(consent.ConsentDenied) as denied:
                consent.begin_operation(1, other, 'openai', ai.OPENAI_MODEL)
            assert denied.value.code == 'AI_CONSENT_REQUIRED'
        with ai_spend_guard.subject_scope(2):
            with pytest.raises(consent.ConsentDenied):
                consent.authorize_operation(authority)
        consent.mutate(1, command(feature, generation=1, action='revoke', key='revoke-phase2'))
        with pytest.raises(consent.ConsentDenied) as stale:
            consent.authorize_operation(authority)
        assert stale.value.code == 'AI_CONSENT_CHANGED'
        consent.mutate(1, command(feature, generation=2, key='regrant-phase2'))
        with pytest.raises(consent.ConsentDenied):
            consent.authorize_operation(authority)
