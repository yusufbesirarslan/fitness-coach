"""Real PostgreSQL event-placed generation races; provider holds no DB locks."""
import os
import threading
from datetime import datetime, timedelta
import json
import pytest
import sqlalchemy as sa
from flask import Flask
from app.extensions import db
from app.models import User, UserSession, TrainingPlan, TrainingPlanReplacementGenerationOperation as Operation, TrainingPlanReplacementProposal as Proposal
from app.services.training_plan_replacement import generation as gen, service as confirm
from app.services.mobile_training_generation.contract import parse_native_request
from app.services.mobile_training_generation.errors import GenerationInProgress, IdempotencyConflict
from test_mobile_training_generation_api import CANONICAL, _document
from test_lp18_b1 import run, expire

pytestmark=pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST')!='1':pytest.skip('requires disposable PostgreSQL',allow_module_level=True)


@pytest.fixture
def pg():
    url=os.environ['PG_TEST_DATABASE_URL'];assert url.startswith('postgresql')
    app=Flask('lp18-b1-pg');app.config.update(TESTING=True,SQLALCHEMY_DATABASE_URI=url,SQLALCHEMY_TRACK_MODIFICATIONS=False,AI_PLAN_QUOTA_ENABLED=True)
    db.init_app(app)
    with app.app_context():
        db.drop_all();db.create_all()
        user=User(username='replacement-pg',email='replacement-pg@example.invalid',cognito_sub='replacement-pg',profile_complete=True)
        db.session.add(user);db.session.flush();uid=user.id
        db.session.add(UserSession(user_id=uid,goal='fit',fitness_level='beginner',current_activity='active',tdee=2400))
        db.session.add(TrainingPlan(user_id=uid,plan_data=json.dumps(_document()),score=8));db.session.commit()
    yield app,uid
    with app.app_context():db.session.remove();db.drop_all();db.engine.dispose()


def worker(app,uid,call,output,name):
    with app.app_context():
        try:
            db.session.execute(sa.text("SET statement_timeout='12s'"));db.session.commit()
            output[name]=call(db.session.get(User,uid))
        except Exception as e:output[name]=e
        finally:db.session.remove()


@pytest.mark.parametrize('different',[False,True])
def test_duplicate_inflight_intent_and_quota_once(pg,monkeypatch,different):
    app,uid=pg;ready=threading.Event();release=threading.Event();result={};calls=[]
    def provider(*args,**kw):
        assert not db.session().in_transaction()
        calls.append(1);ready.set();assert release.wait(10)
        from types import SimpleNamespace
        return SimpleNamespace(document=_document(),overall_score=8)
    monkeypatch.setattr(gen,'generate_training_plan_candidate',provider)
    first=threading.Thread(target=worker,args=(app,uid,run,result,'first'));first.start();assert ready.wait(10)
    second=threading.Thread(target=worker,args=(app,uid,lambda u:run(u,body={**CANONICAL,'sure':60} if different else CANONICAL),result,'second'));second.start();second.join(8)
    try:
        assert not second.is_alive()
        assert isinstance(result['second'],IdempotencyConflict if different else GenerationInProgress)
        with app.app_context():
            assert Operation.query.count()==1 and db.session.get(User,uid).user_metadata['ai_plan_quota']['training']==1
            assert Proposal.query.count()==0
    finally:release.set();first.join(10)
    assert not first.is_alive() and not isinstance(result['first'],Exception) and calls==[1]
    with app.app_context():
        # Lost response / process restart after atomic commit.
        replay=run(db.session.get(User,uid))
        assert replay.replayed and replay.proposal_token==result['first'].proposal_token and calls==[1]


def test_missing_row_claim_race_serializes_without_double_reservation(pg,monkeypatch):
    app,uid=pg;barrier=threading.Barrier(2);out={}
    def claim(user):
        barrier.wait(8)
        return gen.claim(user,parse_native_request(CANONICAL),'generation-key-1')
    threads=[threading.Thread(target=worker,args=(app,uid,claim,out,str(i))) for i in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(12)
    assert all(not t.is_alive() for t in threads)
    assert sum(isinstance(v,tuple) for v in out.values())==1
    assert sum(isinstance(v,GenerationInProgress) for v in out.values())==1
    with app.app_context():
        assert Operation.query.count()==1 and db.session.get(User,uid).user_metadata['ai_plan_quota']['training']==1


def test_lease_takeover_late_provider_cannot_overwrite(pg,monkeypatch):
    app,uid=pg;ready=threading.Event();release=threading.Event();out={};calls=[]
    def provider(*args,**kw):
        assert not db.session().in_transaction();calls.append(threading.current_thread().name)
        if threading.current_thread().name=='old-provider':ready.set();assert release.wait(10)
        from types import SimpleNamespace
        document=_document();document['program'][0]['egzersizler'][0]['not']=threading.current_thread().name
        return SimpleNamespace(document=document,overall_score=8)
    monkeypatch.setattr(gen,'generate_training_plan_candidate',provider)
    first=threading.Thread(target=worker,args=(app,uid,run,out,'old'),name='old-provider');first.start();assert ready.wait(10)
    with app.app_context():expire(Operation.query.one())
    new=threading.Thread(target=worker,args=(app,uid,run,out,'new'),name='new-provider');new.start();new.join(10)
    try:assert not new.is_alive() and not isinstance(out['new'],Exception)
    finally:release.set();first.join(10)
    assert isinstance(out['old'],GenerationInProgress) and len(calls)==2
    with app.app_context():
        assert Proposal.query.count()==1 and Operation.query.one().attempt_count==2
        assert Operation.query.one().proposal_public_id==out['new'].proposal_token
        assert 'new-provider' in Proposal.query.one().candidate_plan_data
        assert db.session.get(User,uid).user_metadata['ai_plan_quota']['training']==1


def test_cross_owner_keys_are_isolated(pg):
    app,uid=pg
    with app.app_context():
        owner=db.session.get(User,uid);first=gen.claim(owner,parse_native_request(CANONICAL),'generation-key-1')
        other=User(username='second-pg',email='second-pg@example.invalid',cognito_sub='second-pg',profile_complete=True)
        db.session.add(other);db.session.flush()
        db.session.add(UserSession(user_id=other.id,goal='fit',fitness_level='beginner',current_activity='active',tdee=2400))
        db.session.add(TrainingPlan(user_id=other.id,plan_data=json.dumps(_document()),score=8));db.session.commit()
        second=gen.claim(other,parse_native_request(CANONICAL),'generation-key-1')
        assert first[0]!=second[0] and Operation.query.count()==2
        result=gen.publish(*first[:2],_document(),8)
        with pytest.raises(confirm.ProposalUnavailable):confirm.confirm_replacement(other.id,result.proposal_token,'confirm-key-1')


@pytest.mark.parametrize('fault',['proposal','operation','commit'])
def test_pg_proposal_finalization_rollback(pg,monkeypatch,fault):
    from sqlalchemy import event
    app,uid=pg
    with app.app_context():
        admitted=gen.claim(db.session.get(User,uid),parse_native_request(CANONICAL),'generation-key-1')
        def injected(*args):raise RuntimeError('persistence failure')
        target=Proposal if fault=='proposal' else Operation if fault=='operation' else db.session()
        hook='before_insert' if fault=='proposal' else 'before_update' if fault=='operation' else 'before_commit'
        event.listen(target,hook,injected)
        try:
            with pytest.raises(RuntimeError):gen.publish(*admitted[:2],_document(),8)
        finally:event.remove(target,hook,injected)
        assert Proposal.query.count()==0 and Operation.query.one().status=='IN_PROGRESS'
        result=gen.publish(*admitted[:2],_document(),8)
        assert Proposal.query.count()==1
        applied=confirm.confirm_replacement(uid,result.proposal_token,'confirm-key-1')
        replay=confirm.confirm_replacement(uid,result.proposal_token,'confirm-key-1')
        assert applied.receipt_id==replay.receipt_id and replay.replayed


def test_generation_migration_upgrade_rerun_and_model_parity(pg):
    from test_lp18_b1_migration import migration
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    app,uid=pg;m=migration()
    with app.app_context():
        db.session.remove()
        with db.engine.begin() as conn:
            # Model boot schema must also be admitted by migration verifier.
            with Operations.context(MigrationContext.configure(conn)):m.upgrade()
            conn.execute(sa.text('DROP TABLE training_plan_replacement_generation_operation'))
            with Operations.context(MigrationContext.configure(conn)):m.upgrade();m.upgrade()
            inspector=sa.inspect(conn)
            assert {c['name'] for c in inspector.get_columns(m.TABLE)}==set(Operation.__table__.columns.keys())


def test_pg_damaged_same_name_check_refused(pg):
    from test_lp18_b1_migration import migration
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    app,uid=pg;m=migration()
    with app.app_context(),db.engine.begin() as conn:
        conn.execute(sa.text('ALTER TABLE training_plan_replacement_generation_operation DROP CONSTRAINT ck_replacement_generation_attempts'))
        conn.execute(sa.text('ALTER TABLE training_plan_replacement_generation_operation ADD CONSTRAINT ck_replacement_generation_attempts CHECK (attempt_count >= 1 AND attempt_count <= 20)'))
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError,match='checks'):m.upgrade()
