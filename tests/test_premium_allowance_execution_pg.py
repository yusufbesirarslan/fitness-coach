"""Disposable PostgreSQL financial/consent races. No real provider or policy."""
import os
import threading
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext

import pytest
import sqlalchemy as sa
from flask import Flask

from app.extensions import db
from app.models import User, AiUsageReservation, AiAllowanceBucket, AppleSubscription
from app.services import ai, ai_consent as C, subscriptions
from app.services.ai_allowances import service as L, execution as E
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
from ai_allowance_support import premium_row
from test_ai_allowances_pg import race, wait_until_blocked

pytestmark = pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('requires explicitly disposable PostgreSQL', allow_module_level=True)


@pytest.fixture
def isolated(monkeypatch):
    url = os.environ['PG_TEST_DATABASE_URL']
    assert url.startswith(('postgresql://', 'postgresql+psycopg2://'))
    app = Flask('premium-allowance-isolated')
    app.config.update(TESTING=True, SECRET_KEY='synthetic-only', SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False, AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT='Sandbox',
        ACCOUNT_ELIGIBILITY_ENABLED=False, APPLE_SANDBOX_FALLBACK_ENABLED=False,
        AI_ALLOWANCE_POLICY=AllowancePolicy('synthetic-pg',
            premium=ScopePolicy('WEEKLY', {f: 2 for f in SUPPORTED_FEATURES}, 1)))
    db.init_app(app)
    monkeypatch.setattr(subscriptions, 'AppleAdapter', lambda _: SimpleNamespace(environment='Sandbox', environments=('Sandbox',)))
    # Explicit synthetic legal test fixture only; production registry untouched.
    bindings = {f: ('synthetic.'+f, ('synthetic',)) for f in set(E.CONSENT_FEATURE.values())}
    monkeypatch.setattr(C, 'BINDINGS', bindings)
    policies = {f'synthetic-{f}': C.Disclosure(f'synthetic-{f}', purpose, categories,
        (ai.heavy_primary_route(), ('openai', ai.OPENAI_MODEL)), ()) for f,(purpose,categories) in bindings.items()}
    from consent_projection_support import publish_synthetic_texts
    monkeypatch.setattr(C, 'POLICIES', publish_synthetic_texts(monkeypatch, policies))
    with app.app_context():
        db.drop_all(); db.create_all()
        db.session.add_all([User(username='premium-a',email='a@example.invalid'), User(username='premium-b',email='b@example.invalid')])
        db.session.commit()
        premium_row(1)
        premium_row(2, original_id='901')
        for feature in bindings:
            state = C.status(1)
            policy = C.POLICIES[f'synthetic-{feature}']
            C.mutate(1, dict(action='affirmative_grant',purpose=policy.purpose,version=policy.version,
                locale='en',text_digest=dict(policy.texts)['en'],contract_digest=C.contract_digest(policy),
                expected_generation=state['generation'],request_id='grant-'+feature))
        db.session.remove()
    yield app
    with app.app_context():
        db.session.remove(); db.drop_all(); db.engine.dispose()


def reserve(owner, family, key):
    hold = L.reserve_dispatch_within(owner, family, key, 'a'*64, E.ATTEMPT_LIMITS[family])
    db.session.commit()
    return hold


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_same_key_has_one_execution_authority(isolated, family):
    results = race(isolated, reserve, [(1,family,'same-logical-action')]*8)
    assert sum(isinstance(r,L.Reservation) for r in results) == 1
    assert results.count('AI_ALLOWANCE_IN_PROGRESS') == 7
    with isolated.app_context():
        assert AiUsageReservation.query.count() == 1
        b=AiAllowanceBucket.query.one()
        assert (b.used,b.reserved,b.granted)==(0,1,2)


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_distinct_keys_cannot_overdraw_premium(isolated, family):
    results=race(isolated,reserve,[(1,family,f'distinct-key-{i}') for i in range(8)])
    assert sum(isinstance(r,L.Reservation) for r in results)==2
    assert results.count('AI_ALLOWANCE_EXHAUSTED')==6
    with isolated.app_context():
        b=AiAllowanceBucket.query.one()
        assert (b.used,b.reserved)==(0,2)


def test_dispatch_evidence_rolls_back_with_callers_lease(isolated):
    with isolated.app_context():
        owner=db.session.get(User,1)
        owner.language='en'
        hold=L.reserve_dispatch_within(1,'pump_check','rollback-lease','a'*64,4)
        db.session.flush()
        db.session.rollback()
        assert AiUsageReservation.query.count()==AiAllowanceBucket.query.count()==0
        assert db.session.get(User,1).language != 'en'
        with pytest.raises(L.AllowanceError): L.record_dispatch_attempt(1,hold.reference)


@pytest.mark.parametrize('native', [False, True])
def test_reconciliation_winning_owner_lock_prevents_stale_premium(isolated, native):
    started = threading.Event()
    results = []
    def contender():
        with isolated.app_context():
            # Deliberately retain an ACTIVE ORM snapshot before the writer wins.
            assert AppleSubscription.query.filter_by(user_id=1).one().status == 1
            started.set()
            try:
                if native:
                    L.reserve_within(1, 'training_generation', 'native-expiry-race', strict_entitlement=True)
                else:
                    L.reserve_dispatch_within(1, 'coach', 'generic-expiry-race', 'a'*64, 30)
                db.session.commit()
                results.append('unexpected_dispatch')
            except L.AllowanceError as error:
                results.append(error.code)
                db.session.rollback()
            finally:
                db.session.remove()
    with isolated.app_context():
        engine = db.engine
        db.session.execute(sa.select(User).where(User.id == 1).with_for_update()).scalar_one()
        thread = threading.Thread(target=contender)
        thread.start()
        try:
            assert started.wait(5)
            assert wait_until_blocked(engine), 'reservation did not serialize with entitlement writer'
            row = AppleSubscription.query.filter_by(user_id=1).one()
            row.status = 2
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.session.commit()
        finally:
            db.session.rollback()
            thread.join(10)
        assert not thread.is_alive()
        assert results == ['AI_FEATURE_NOT_IN_PLAN']
        assert AiAllowanceBucket.query.count() == AiUsageReservation.query.count() == 0


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_finalize_and_physical_attempt_races_are_atomic(isolated, family):
    with isolated.app_context():
        hold=reserve(1,family,'finalization-race')
        L.record_dispatch_attempt(1,hold.reference)
    results=race(isolated,L.finalize_dispatch,[(1,hold.reference)]*8)
    assert sum(isinstance(r,L.Settlement) and not r.replayed for r in results)==1
    with isolated.app_context():
        row=AiUsageReservation.query.one()
        assert (row.state,row.physical_attempts,row.billing_evidence)==('RELEASED',1,'UNKNOWN')
        with pytest.raises(L.AllowanceError): L.record_dispatch_attempt(1,hold.reference)
        assert AiAllowanceBucket.query.one().billed_releases==1


def test_d1_attempt_owns_user_before_settlement_seals(isolated):
    with isolated.app_context():
        action=E.prepare(1,'nutrition_generation',{},key='d1-physical-race')
        action.start()
        engine=db.engine
    attempted,release=threading.Event(),threading.Event()
    results=[]
    def provider():
        with isolated.app_context(),E.scope(action):
            with C.dispatch(1,'nutrition_plan','openai',{'model':ai.OPENAI_MODEL},action.authority.generation):
                E.before_attempt(1,'nutrition_plan')
                attempted.set()
                assert release.wait(10)
    def settle():
        with isolated.app_context():
            results.append(L.finalize_dispatch(1,action.reference))
    p=threading.Thread(target=provider)
    s=threading.Thread(target=settle)
    try:
        p.start(); assert attempted.wait(5)
        s.start(); assert wait_until_blocked(engine)
    finally:
        release.set();p.join(15);s.join(15)
    assert len(results)==1
    with isolated.app_context():
        row=AiUsageReservation.query.one()
        assert row.physical_attempts==1 and row.billing_evidence=='UNKNOWN'
        assert row.provider_billed is None


@pytest.mark.parametrize('after_attempt',[False,True])
def test_revoke_regrant_cannot_authorize_old_key(isolated, after_attempt):
    with isolated.app_context():
        action=E.prepare(1,'nutrition_generation',{},key='original-consent-key')
        action.start()
        if after_attempt:
            with E.scope(action), C.dispatch(1,'nutrition_plan','openai',{'model':ai.OPENAI_MODEL},action.authority.generation):
                E.before_attempt(1,'nutrition_plan')
        p=C.POLICIES['synthetic-nutrition_plan']
        def mutate(kind,key):
            C.mutate(1,dict(action=kind,purpose=p.purpose,version=p.version,locale='en',
                text_digest=dict(p.texts)['en'],contract_digest=C.contract_digest(p),
                expected_generation=C.status(1)['generation'],request_id=key))
        mutate('revoke','revoke-original')
        with E.scope(action),pytest.raises(C.ConsentDenied):
            C.authorize_operation(action.authority)
        mutate('affirmative_grant','regrant-original')
        with E.scope(action),pytest.raises(C.ConsentDenied):
            with C.dispatch(1,'nutrition_plan','openai',{'model':ai.OPENAI_MODEL},action.authority.generation):
                pytest.fail('old authority reached SDK')
        action.finish()
        with pytest.raises(E.ExecutionRefused):
            E.prepare(1,'nutrition_generation',{},key='original-consent-key').start()
        row=AiUsageReservation.query.one()
        assert row.physical_attempts==int(after_attempt)
        assert row.billing_evidence==('UNKNOWN' if after_attempt else 'CONFIRMED_NOT_BILLED')


def test_expiry_before_authorization_cannot_mint_premium(isolated):
    with isolated.app_context():
        # Same user-row serialization used by subscription writers.
        db.session.execute(sa.select(User).where(User.id==1).with_for_update()).scalar_one()
        row=AppleSubscription.query.filter_by(user_id=1).one()
        row.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.session.commit()
        with pytest.raises(L.AllowanceError) as e:
            reserve(1,'coach','expired-subscription')
        db.session.rollback()
        assert e.value.code=='AI_FEATURE_NOT_IN_PLAN'
        assert AiAllowanceBucket.query.count()==0


@pytest.fixture
def pump_provider(monkeypatch):
    from app.services.mobile_pump_checks import service
    calls = []
    monkeypatch.setattr(service.s3_helper, 'is_enabled', lambda: True)
    monkeypatch.setattr(service.s3_helper, 'upload_image',
        lambda *a, user_id=None, **kw: f'pump-checks/{user_id}/synthetic-private.jpg')
    def analyze(*args, **kwargs):
        action = E.current_action()
        assert action is not None
        E.check_provider(action.owner, 'vision')
        # The real D1 dispatch lock, no SDK or image processor.
        with C.dispatch(action.owner, 'vision', 'openai', {'model':ai.OPENAI_MODEL}, action.authority.generation):
            E.before_attempt(action.owner, 'vision')
            calls.append(action.reference)
        return {'summary':'Synthetic result', 'observations':[], 'strengths':[],
            'focus_areas':[], 'limitations':['Synthetic'],
            'next_check_guidance':'Synthetic', 'quality':'limited'}
    monkeypatch.setattr(service, 'analyze_image', analyze)
    return calls


def pump_command():
    from app.services.mobile_pump_checks.service import CreateCommand
    return CreateCommand(image_bytes=b'synthetic-image',media_type='image/jpeg',
        body_region='upper_body',environment='gym',description='Synthetic',
        captured_at=datetime(2026,10,1))


def run_pump(key):
    from app.services.mobile_pump_checks.service import create_or_replay
    row, created = create_or_replay(1,key,pump_command())
    return row.analysis_status


def test_real_pump_lease_and_hold_commit_once_under_same_key(isolated, pump_provider):
    results = race(isolated,run_pump,[('actual-pump-same-key',)]*6)
    assert all(r in ('completed','analyzing') for r in results), results
    assert len(pump_provider) == 1
    with isolated.app_context():
        row = AiUsageReservation.query.one()
        assert (row.state,row.physical_attempts,row.canonical_committed)==('COMMITTED',1,True)
        assert run_pump('actual-pump-same-key') == 'completed'
        assert AiUsageReservation.query.count() == 1
        assert AiAllowanceBucket.query.one().used == 1


def test_real_pump_different_keys_share_finite_premium_bucket(isolated, pump_provider):
    results = race(isolated,run_pump,[(f'actual-pump-distinct-{n}',) for n in range(3)])
    assert results.count('completed') == 2, results
    assert results.count('AI_ALLOWANCE_EXHAUSTED') == 1, results
    assert len(pump_provider) == 2
    with isolated.app_context():
        bucket=AiAllowanceBucket.query.one()
        assert (bucket.used,bucket.reserved)==(2,0)
        assert AiUsageReservation.query.count()==2
