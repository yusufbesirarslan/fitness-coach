"""Real PG forced orders. Events place windows; pg_blocking_pids proves waits.

All timeouts are test failure bounds, never accepted correctness outcomes.
Uses a dedicated disposable database, separate sessions for every contender.
"""
import importlib.util
import os
import threading
import time
from pathlib import Path
import pytest
import sqlalchemy as sa
from flask import Flask
from app.extensions import db
from app.models import (User, TrainingPlan, WorkoutSession, PumpCheck,
                        TrainingPlanReplacementReceipt as Receipt,
                        TrainingPlanConfirmationProposal as CoachProposal,
                        PlanMutationRecord)
from app.services.training_plan_replacement import service as s
from app.services import plan_replacement as pr
from app.services.workout_session import service as ws
from app.services.workout_session import WorkoutNotStartable, NativeWorkoutIdentity
from app.services.workout_completion import service as wc
from app.services.workout_completion import CompleteWorkoutCommand
from app.services.plan_mutation import service as pm
from app.services.plan_mutation.commands import UpdateExercisePrescriptionCommand
from app.services.plan_mutation.context import MutationContext
from app.services.plan_mutation.fingerprint import snapshot_fingerprint
from test_lp18_b0 import DAY, plan_text, coach_proposal

pytestmark=pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('requires disposable PostgreSQL', allow_module_level=True)


@pytest.fixture
def pg():
    url=os.environ.get('PG_TEST_DATABASE_URL','')
    assert url.startswith('postgresql'), 'required PG database missing'
    app=Flask('lp18-b0-pg')
    app.config.update(TESTING=True, SECRET_KEY='local-test', SQLALCHEMY_DATABASE_URI=url,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False, AI_PLAN_QUOTA_ENABLED=False,
                      AI_COACH_PLAN_MUTATION_TOOLS_ENABLED=True)
    db.init_app(app)
    with app.app_context():
        db.drop_all();db.create_all()
        user=User(username='lp18-pg',email='lp18-pg@example.invalid',cognito_sub='lp18-pg')
        db.session.add(user);db.session.flush();uid=user.id
        plan=TrainingPlan(user_id=uid,plan_data=plan_text('Old'),score=8)
        db.session.add(plan);db.session.commit()
        binding=s.BaseBinding(plan.lineage_id,0,snapshot_fingerprint(plan.plan_data))
        token=s.stage_proposal(uid,binding,plan_data=plan_text('New'),score=9)
    yield app, uid, token, binding
    with app.app_context():
        db.session.remove();db.drop_all();db.engine.dispose()


def confirm(uid, token): return lambda:s.confirm_replacement(uid,token,'confirm-key-1')
def start(uid): return lambda:ws.start_session(uid,today=DAY)
def complete(uid): return lambda:wc.complete_workout(CompleteWorkoutCommand(user_id=uid,today=DAY))


def force(pg, monkeypatch, module, hook, first_call, second_call, *, blocks=True,
          after=False):
    app,uid,token,binding=pg
    ready=threading.Event();release=threading.Event();done=threading.Event()
    result={};pids={};real=getattr(module,hook)
    def paused(*args,**kw):
        if threading.current_thread().name!='lp18-first':return real(*args,**kw)
        value=real(*args,**kw) if after else None
        ready.set();assert release.wait(15), 'first contender stranded'
        return value if after else real(*args,**kw)
    monkeypatch.setattr(module,hook,paused)
    def worker(name,call):
        with app.app_context():
            try:
                db.session.execute(sa.text("SET statement_timeout = '12s'"))
                db.session.execute(sa.text("SET lock_timeout = '10s'"))
                pids[name]=db.session.execute(sa.text('SELECT pg_backend_pid()')).scalar_one()
                result[name]=call()
            except (s.ReplacementRefusal, pm.PlanStateConflict, pm.PlanNotFound, WorkoutNotStartable) as e:
                result[name]=e
            except Exception as e:
                result[name]=('unexpected',type(e).__name__)
            finally:
                if name=='second':done.set()
                db.session.remove()
    first=threading.Thread(target=worker,args=('first',first_call),name='lp18-first')
    second=threading.Thread(target=worker,args=('second',second_call),name='lp18-second')
    first.start()
    assert ready.wait(10), result
    second.start()
    try:
        if blocks:
            blocked=False; deadline=time.monotonic()+8
            with app.app_context(), db.engine.connect() as observer:
                while time.monotonic()<deadline and not done.is_set():
                    if 'second' in pids:
                        blockers=observer.execute(sa.text('SELECT pg_blocking_pids(:pid)'),{'pid':pids['second']}).scalar_one()
                        if blockers:
                            assert pids['first'] in blockers
                            blocked=True;break
                    done.wait(.01)
            assert blocked, ('missing actual PG wait', result)
        else:
            assert done.wait(8), ('completion should proceed while transition held',result)
            assert not isinstance(result['second'],tuple),result
    finally:
        release.set();first.join(15);second.join(15)
    assert not first.is_alive() and not second.is_alive(),result
    assert all(not isinstance(v,tuple) for v in result.values()),result
    return result


def test_a_start_wins_active_refuses(pg,monkeypatch):
    _,uid,token,binding=pg
    out=force(pg,monkeypatch,ws,'compute_plan_snapshot',start(uid),confirm(uid,token))
    assert out['first'].outcome is ws.SessionOutcome.CREATED
    assert isinstance(out['second'],s.ReplacementRefusal) and out['second'].reason=='ACTIVE_REFUSED'
    with pg[0].app_context():
        assert TrainingPlan.query.one().lineage_id==binding.lineage_id
        assert WorkoutSession.query.one().status=='active'
        assert Receipt.query.one().status=='ACTIVE_REFUSED'


def test_b_replacement_wins_start_snapshots_new(pg,monkeypatch):
    _,uid,token,binding=pg
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),start(uid))
    assert out['first'].status=='APPLIED' and out['second'].outcome is ws.SessionOutcome.CREATED
    with pg[0].app_context():
        p=TrainingPlan.query.one();session=WorkoutSession.query.one()
        assert p.lineage_id==out['first'].lineage_id and p.lineage_id!=binding.lineage_id
        assert session.planned_training_plan_id==p.id
        assert session.plan_fingerprint==ws.compute_plan_snapshot(uid,DAY).plan_fingerprint


def test_c_completion_holds_day_start_waits_then_refuses(pg,monkeypatch):
    _,uid,_,_=pg
    out=force(pg,monkeypatch,wc,'award_xp',complete(uid),start(uid))
    assert out['first'].created and out['second'].outcome is ws.SessionOutcome.INVALID_TRANSITION
    with pg[0].app_context():
        assert WorkoutSession.query.count()==0 and PumpCheck.query.count()==1


def test_d_start_transition_does_not_block_completion_xp(pg,monkeypatch):
    _,uid,_,_=pg
    out=force(pg,monkeypatch,ws,'compute_plan_snapshot',start(uid),complete(uid),blocks=False)
    assert out['second'].created and out['first'].outcome is ws.SessionOutcome.INVALID_TRANSITION


def test_e_completion_holds_xp_user_start_pending_no_cycle(pg,monkeypatch):
    _,uid,_,_=pg
    out=force(pg,monkeypatch,wc,'award_xp',complete(uid),start(uid),after=True)
    assert out['first'].created and out['second'].outcome is ws.SessionOutcome.INVALID_TRANSITION
    with pg[0].app_context():assert User.query.one().rank_points>0


def test_linked_completion_session_day_user_is_compatible(pg,monkeypatch):
    _,uid,token,_=pg
    with pg[0].app_context():
        ws.start_session(uid,today=DAY);sid=WorkoutSession.query.one().id
    call=lambda:wc.complete_workout(CompleteWorkoutCommand(user_id=uid,today=DAY,session_id=sid))
    out=force(pg,monkeypatch,wc,'award_xp',call,confirm(uid,token),blocks=False)
    # Conservative MVCC ACTIVE refusal while terminalization is uncommitted.
    assert out['first'].created and out['second'].reason=='ACTIVE_REFUSED'
    with pg[0].app_context():assert WorkoutSession.query.one().status=='completed'


def mutation(uid):
    return lambda:pm.apply_plan_mutation(uid,UpdateExercisePrescriptionCommand('Pazartesi','Old',sets=4),MutationContext('mutate-key-1'))


def test_f_direct_mutation_wins_replacement_stale(pg,monkeypatch):
    _,uid,token,_=pg
    out=force(pg,monkeypatch,pm,'apply_command',mutation(uid),confirm(uid,token))
    assert out['first'].changed and out['second'].reason=='STALE'
    with pg[0].app_context():
        assert TrainingPlan.query.one().mutation_version==1
        assert PlanMutationRecord.query.count()==1


def test_f_replacement_wins_old_selected_mutation_cannot_overwrite(pg,monkeypatch):
    _,uid,token,_=pg
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),mutation(uid))
    assert out['first'].status=='APPLIED' and isinstance(out['second'],pm.PlanNotFound)
    with pg[0].app_context():
        assert TrainingPlan.query.one().plan_data==plan_text('New') and PlanMutationRecord.query.count()==0


def coach_confirm(uid,public_id):
    from flask import current_app
    from app.services import coach_plan_tools
    from app.observability import assign_request_id
    def confirm():
        with current_app.test_request_context('/ask', method='POST'):
            assign_request_id()
            coach_plan_tools.begin_turn('evet')
            return coach_plan_tools.execute_plan_tool(uid, coach_plan_tools.CONFIRM_TOOL, {})
    return confirm


def test_coach_confirm_first_mutation_is_not_lost(pg,monkeypatch):
    _,uid,token,_=pg
    with pg[0].app_context():public=coach_proposal(uid).public_id
    out=force(pg,monkeypatch,pm,'apply_command',coach_confirm(uid,public),confirm(uid,token))
    assert out['first']['status']=='applied' and out['second'].reason in {'STALE','COACH_PENDING_REFUSED'}
    with pg[0].app_context():
        assert TrainingPlan.query.one().mutation_version==1
        assert PlanMutationRecord.query.count()==1
        assert CoachProposal.query.one().status=='applied'


def test_replacement_coach_boundary_first_pending_refuses(pg,monkeypatch):
    _,uid,token,_=pg
    with pg[0].app_context():public=coach_proposal(uid).public_id
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),coach_confirm(uid,public))
    assert out['first'].reason=='COACH_PENDING_REFUSED' and out['second']['status']=='applied'
    with pg[0].app_context():
        assert TrainingPlan.query.one().mutation_version==1
        assert CoachProposal.query.one().status=='applied'


def test_new_coach_proposal_old_snapshot_after_replacement_is_stale(pg,monkeypatch):
    _,uid,token,binding=pg
    with pg[0].app_context():
        candidate=plan_text('Old')+' '
        token=s.stage_proposal(uid,binding,plan_data=candidate,score=9)
    from app.services.plan_confirmation import service as cp
    from app.services.coach_plan_tools.executor import _apply_confirmed
    # Capture old server preview before replacement; insert pending after its
    # final pending-row scan. FK KEY SHARE must not deadlock behind owner NKU.
    with pg[0].app_context():
        row=coach_proposal(uid); fields={c.name:getattr(row,c.name) for c in CoachProposal.__table__.columns if c.name not in {'id','public_id','created_at','resolved_at'}}
        CoachProposal.query.delete();db.session.commit()
    def create():
        row=CoachProposal(**fields);db.session.add(row);db.session.commit();return row.public_id
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),create,blocks=False)
    assert out['first'].status=='APPLIED'
    with pg[0].app_context():
        public=out['second'];row=cp.lock_proposal(uid,public)
        with pytest.raises(pm.PlanStateConflict):_apply_confirmed(uid,row)
        assert TrainingPlan.query.one().plan_data==candidate and PlanMutationRecord.query.count()==0
        result=coach_confirm(uid,public)()
        from app.services.coach_plan_tools import results
        assert result['error']==results.ERROR_PENDING_CONFIRMATION_STALE
        assert CoachProposal.query.one().status=='stale'


def test_g_first_plan_operation_user_plan_order_has_no_cycle(pg,monkeypatch):
    _,uid,token,_=pg
    from app.services.mobile_training_generation import store, ExistingPlanRefused
    with pg[0].app_context():
        operation=store.claim(uid,'generate-key-1','fingerprint')
        store.stage_candidate(operation.id,plan_text('First'),8);oid=operation.id
    def first_plan():
        try:return store.commit_plan(oid,uid)
        except ExistingPlanRefused:return 'existing-refused'
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),first_plan)
    assert out['first'].status=='APPLIED' and out['second']=='existing-refused'
    with pg[0].app_context():assert TrainingPlan.query.count()==1


def test_g_first_plan_owner_wins_replacement_waits(pg,monkeypatch):
    _,uid,token,_=pg
    from app.services.mobile_training_generation import store, ExistingPlanRefused
    with pg[0].app_context():
        operation=store.claim(uid,'generate-key-1','fingerprint')
        store.stage_candidate(operation.id,plan_text('First'),8);oid=operation.id
    def first_plan():
        try:return store.commit_plan(oid,uid)
        except ExistingPlanRefused:return 'existing-refused'
    out=force(pg,monkeypatch,store,'get_active_plan',first_plan,confirm(uid,token))
    assert out['first']=='existing-refused' and out['second'].status=='APPLIED'


def test_same_key_confirm_race_replays_once(pg,monkeypatch):
    _,uid,token,_=pg
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),confirm(uid,token))
    assert out['first'].lineage_id==out['second'].lineage_id and out['second'].replayed
    with pg[0].app_context():assert Receipt.query.count()==1 and TrainingPlan.query.count()==1


def test_pg_migration_upgrade_rerun_and_metadata_shape(pg):
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('lp18_pg_migration',root/'migrations/versions/e3f4a5b6c7d8_replacement_authority.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    with pg[0].app_context():
        db.session.remove()
        with db.engine.begin() as conn:
            # Existing metadata boot shape also must be recognized, with no
            # destructive overwrite or empty-ledger backfill.
            conn.execute(sa.text('DROP TABLE training_plan_replacement_receipt'))
            conn.execute(sa.text('DROP TABLE training_plan_replacement_proposal'))
            with Operations.context(MigrationContext.configure(conn)):m.upgrade();m.upgrade()
            inspector=sa.inspect(conn)
            assert len(inspector.get_columns(Receipt.__tablename__))==10


def test_replacement_wins_native_pre_resolved_identity_refuses(pg,monkeypatch):
    _,uid,token,binding=pg
    identity=NativeWorkoutIdentity('old-server-resolved-ref',binding.lineage_id,0)
    out=force(pg,monkeypatch,s,'_guard',confirm(uid,token),
              lambda:ws.start_session(uid,today=DAY,native=identity))
    assert out['first'].status=='APPLIED' and isinstance(out['second'],WorkoutNotStartable)
    with pg[0].app_context():assert WorkoutSession.query.count()==0


@pytest.mark.parametrize('native_first', [True, False])
def test_absent_base_first_plan_commit_never_enters_a_cycle(pg,monkeypatch,native_first):
    _,uid,token,_=pg
    from app.services.mobile_training_generation import store
    with pg[0].app_context():
        TrainingPlan.query.filter_by(user_id=uid).delete();db.session.commit()
        operation=store.claim(uid,'generate-key-1','fingerprint')
        store.stage_candidate(operation.id,plan_text('First'),8);oid=operation.id
    create=lambda:store.commit_plan(oid,uid).lineage_id
    if native_first:
        out=force(pg,monkeypatch,store,'get_active_plan',create,confirm(uid,token))
        assert isinstance(out['first'],str) and out['second'].reason=='STALE'
    else:
        out=force(pg,monkeypatch,pr,'get_active_plan',confirm(uid,token),create)
        assert out['first'].reason=='STALE' and isinstance(out['second'],str)
    with pg[0].app_context():
        assert TrainingPlan.query.one().plan_data==plan_text('First')
        assert Receipt.query.one().status=='STALE'


def test_transition_namespace_disjoint_from_all_single_bigint_locks(pg,monkeypatch):
    _,uid,_,_=pg
    from app.services import plan_session_transition as transition
    # Deliberately collide numeric bits. PostgreSQL's distinct advisory key
    # spaces (one bigint versus two ints) must still permit concurrent holders.
    key=(transition.TRANSITION_LOCK_NAMESPACE << 32) | uid
    def single_bigint():
        db.session.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})
        db.session.commit();return 'single-space-acquired'
    out=force(pg,monkeypatch,ws,'lock_plan_session_transition',start(uid),
              single_bigint,after=True,blocks=False)
    assert out['second']=='single-space-acquired' and out['first'].outcome is ws.SessionOutcome.CREATED


@pytest.mark.parametrize('start_first', [False, True])
def test_completion_quest_and_challenge_xp_paths_do_not_cycle(pg,monkeypatch,start_first):
    _,uid,_,_=pg
    from app.models import Challenge, DailyQuest, UserChallengeProgress, UserQuestProgress
    with pg[0].app_context():
        for metric in ('pump_check_created','workout_logged','xp_earned'):
            db.session.add(Challenge(code='lp18-'+metric, title='qualification', metric=metric,
                                     target_value=1, xp_reward=5, challenge_type='global'))
        db.session.add(DailyQuest(title='qualification', quest_type='workout_logged', points_reward=7))
        db.session.commit()
    if start_first:
        out=force(pg,monkeypatch,ws,'compute_plan_snapshot',start(uid),complete(uid),blocks=False)
        assert out['second'].created and out['first'].outcome is ws.SessionOutcome.INVALID_TRANSITION
    else:
        out=force(pg,monkeypatch,wc,'award_xp',complete(uid),start(uid),after=True)
        assert out['first'].created and out['second'].outcome is ws.SessionOutcome.INVALID_TRANSITION
    with pg[0].app_context():
        assert UserChallengeProgress.query.filter(UserChallengeProgress.completed_at.isnot(None)).count()==3
        assert UserQuestProgress.query.count()==1
        assert User.query.one().rank_points==57
        assert PumpCheck.query.count()==1 and WorkoutSession.query.count()==0
