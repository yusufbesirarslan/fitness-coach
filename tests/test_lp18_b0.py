"""Receipt authority, exact stale binding, atomicity and browser regression."""
from datetime import date, datetime, timedelta
import json
import pytest
from sqlalchemy import text
from app.extensions import db
from app.models import (User, TrainingPlan, WorkoutSession,
                        TrainingPlanReplacementProposal as Proposal,
                        TrainingPlanReplacementReceipt as Receipt)
from app.services.training_plan_replacement import service as s
from app.services.plan_mutation.fingerprint import snapshot_fingerprint
from app.services.workout_session import start_session

DAY = date(2026, 10, 5)

def plan_text(marker):
    from app.services.training_generation.response_validator import WEEKDAYS
    return json.dumps({'program': [
        {'gun': day, 'tip': 'antrenman' if n == 0 else 'dinlenme',
         'egzersizler': [{'isim': marker, 'set': 3, 'tekrar': '8-12'}] if n == 0 else []}
        for n, day in enumerate(WEEKDAYS)]})


@pytest.fixture
def foundation(app):
    with app.app_context():
        owner = User(username='lp18', email='lp18@example.invalid', cognito_sub='lp18')
        db.session.add(owner); db.session.flush()
        plan = TrainingPlan(user_id=owner.id, plan_data=plan_text('Old'), score=8)
        db.session.add(plan); db.session.commit()
        uid = owner.id
        binding = s.BaseBinding(plan.lineage_id, plan.mutation_version, snapshot_fingerprint(plan.plan_data))
        token = s.stage_proposal(uid, binding, plan_data=plan_text('New'), score=9)
        yield uid, token, binding


def current(uid):
    return TrainingPlan.query.filter_by(user_id=uid).populate_existing().one()


def refusal(uid, token, status, key='confirm-key-1'):
    with pytest.raises(s.ReplacementRefusal) as e:
        s.confirm_replacement(uid, token, key)
    assert e.value.reason == status
    assert Receipt.query.filter_by(user_id=uid).count() == 1
    assert current(uid).plan_data == plan_text('Old')
    return e.value.result


def test_success_and_replay_survive_restart_plan_and_proposal_deletion(foundation):
    uid, token, binding = foundation
    result = s.confirm_replacement(uid, token, 'confirm-key-1')
    assert result.status == 'APPLIED' and result.mutation_version == 0
    assert result.lineage_id != binding.lineage_id
    from app.services.plan_replacement import replace_training_plan, PlanExpectation
    later = replace_training_plan(uid, PlanExpectation(False, result.lineage_id, 0),
                                  plan_data=plan_text('Later'), score=8)
    Proposal.query.filter_by(user_id=uid).delete(); db.session.commit(); db.session.remove()
    replay = s.confirm_replacement(uid, token, 'confirm-key-1')
    assert replay.replayed and replay.lineage_id == result.lineage_id
    assert replay.mutation_version == 0 and replay.receipt_id == result.receipt_id
    assert current(uid).lineage_id == later.lineage_id
    assert Receipt.query.count() == 1


def test_different_intent_same_key_conflicts(foundation):
    uid, token, binding = foundation
    s.confirm_replacement(uid, token, 'confirm-key-1')
    with pytest.raises(s.ConfirmationConflict):
        s.confirm_replacement(uid, 'different-proposal', 'confirm-key-1')
    assert Receipt.query.count() == 1 and current(uid).plan_data == plan_text('New')


def test_fingerprint_binds_every_frozen_dimension(foundation):
    uid, token, _ = foundation
    p = Proposal.query.filter_by(public_id=token).one()
    original = s.intent_fingerprint(p)
    for attr, value in [('public_id', 'other'), ('base_lineage_id', 'other'),
                        ('base_mutation_version', 9), ('base_snapshot_digest', 'f'*64),
                        ('candidate_fingerprint', 'f'*64)]:
        old = getattr(p, attr); setattr(p, attr, value)
        assert s.intent_fingerprint(p) != original
        setattr(p, attr, old)
    assert s.candidate_fingerprint('bytes', 8) != s.candidate_fingerprint('bytes ', 8)
    assert s.candidate_fingerprint('bytes', 8) != s.candidate_fingerprint('bytes', 9)
    db.session.rollback()


@pytest.mark.parametrize('dimension', ['lineage_id', 'mutation_version', 'plan_data'])
def test_each_stale_dimension_refuses(foundation, dimension):
    uid, token, _ = foundation
    p = current(uid)
    setattr(p, dimension, {'lineage_id': 'another-lineage', 'mutation_version': 1,
                           'plan_data': plan_text('Old')+' '}[dimension])
    db.session.commit()
    before = (p.lineage_id, p.mutation_version, p.plan_data)
    with pytest.raises(s.ReplacementRefusal) as e: s.confirm_replacement(uid, token, 'confirm-key-1')
    assert e.value.reason == 'STALE'
    p = current(uid)
    assert (p.lineage_id, p.mutation_version, p.plan_data) == before
    assert Receipt.query.one().status == 'STALE'


def test_any_active_even_previous_day_refuses_and_replays(foundation):
    uid, token, _ = foundation
    start_session(uid, today=DAY-timedelta(days=3))
    first = refusal(uid, token, 'ACTIVE_REFUSED')
    session = WorkoutSession.query.one(); checkpoint = session.checkpoint_data
    session.status = 'abandoned'; db.session.commit()
    with pytest.raises(s.ReplacementRefusal) as e: s.confirm_replacement(uid, token, 'confirm-key-1')
    assert e.value.result.replayed and e.value.result.receipt_id == first.receipt_id
    assert session.checkpoint_data == checkpoint
    assert s.confirm_replacement(uid, token, 'confirm-key-2').status == 'APPLIED'


def coach_proposal(uid):
    from app.services.plan_confirmation.service import create_or_reuse_pending
    from app.services.plan_mutation.commands import UpdateExercisePrescriptionCommand
    from app.services.coach_plan_tools.proposals import encode_command
    from app.services.plan_mutation.fingerprint import semantic_fingerprint, command_type
    p = current(uid); command = UpdateExercisePrescriptionCommand('Pazartesi', 'Old', sets=4)
    return create_or_reuse_pending(uid, lineage_id=p.lineage_id, mutation_version=p.mutation_version,
        snapshot_fingerprint=snapshot_fingerprint(p.plan_data), command_type=command_type(command),
        command_payload=encode_command(command)[1], command_fingerprint=semantic_fingerprint(command),
        reason_codes=[], summary='review')[0]


def test_pending_coach_refuses_without_resolving(foundation):
    uid, token, _ = foundation
    row = coach_proposal(uid)
    refusal(uid, token, 'COACH_PENDING_REFUSED')
    assert row.status == 'pending' and row.resolved_at is None


def test_expiry_and_consumption_closed_states(foundation):
    uid, token, _ = foundation
    result = s.confirm_replacement(uid, token, 'confirm-key-1')
    with pytest.raises(s.ReplacementRefusal) as e: s.confirm_replacement(uid, token, 'confirm-key-2')
    assert e.value.reason == 'CONSUMED'
    assert current(uid).lineage_id == result.lineage_id


def test_expired_proposal_refuses(foundation):
    uid, token, _ = foundation
    db.session.execute(text("UPDATE training_plan_replacement_proposal SET created_at = :created, expires_at = :expires"),
                       {'created': datetime.utcnow()-timedelta(hours=2), 'expires': datetime.utcnow()-timedelta(hours=1)})
    db.session.commit(); db.session.remove()
    refusal(uid, token, 'EXPIRED')


def test_receipt_failure_rolls_back_replacement(foundation, monkeypatch):
    uid, token, binding = foundation
    real_flush = db.session.flush
    def fail_receipt(*args, **kw):
        if any(isinstance(row, Receipt) for row in db.session.new):
            raise RuntimeError('injected receipt failure')
        return real_flush(*args, **kw)
    monkeypatch.setattr(db.session, 'flush', fail_receipt)
    with pytest.raises(RuntimeError): s.confirm_replacement(uid, token, 'confirm-key-1')
    assert current(uid).lineage_id == binding.lineage_id
    assert current(uid).plan_data == plan_text('Old')
    assert Receipt.query.count() == 0
    monkeypatch.setattr(db.session, 'flush', real_flush)
    assert s.confirm_replacement(uid, token, 'confirm-key-1').status == 'APPLIED'


def test_browser_still_replaces_with_active(foundation):
    uid, token, binding = foundation
    start_session(uid, today=DAY)
    from app.services.plan_replacement import replace_training_plan, PlanExpectation
    result = replace_training_plan(uid, PlanExpectation(False, binding.lineage_id, 0),
                                  plan_data=plan_text('Browser'), score=8)
    assert result.lineage_id != binding.lineage_id
    assert WorkoutSession.query.one().status == 'active'
    assert Receipt.query.count() == 0


def test_start_snapshot_is_after_transition_and_refreshes_identity_map(foundation, monkeypatch):
    uid, token, _ = foundation
    from app.services.workout_session import service as ws
    events = []
    real_lock = ws.lock_plan_session_transition; real_snapshot = ws.compute_plan_snapshot
    def lock(owner): events.append('lock'); return real_lock(owner)
    def snapshot(owner, day):
        assert events == ['lock']; events.append('snapshot'); return real_snapshot(owner, day)
    monkeypatch.setattr(ws, 'lock_plan_session_transition', lock)
    monkeypatch.setattr(ws, 'compute_plan_snapshot', snapshot)
    current(uid)  # old instance in map
    applied = s.confirm_replacement(uid, token, 'confirm-key-1')
    start_session(uid, today=DAY)
    row = WorkoutSession.query.one()
    assert row.planned_training_plan_id == current(uid).id
    assert current(uid).lineage_id == applied.lineage_id


def test_stale_native_start_identity_refuses(foundation):
    uid, token, binding = foundation
    from app.services.workout_session import NativeWorkoutIdentity, WorkoutNotStartable
    s.confirm_replacement(uid, token, 'confirm-key-1')
    with pytest.raises(WorkoutNotStartable):
        start_session(uid, today=DAY, native=NativeWorkoutIdentity('old-ref', binding.lineage_id, 0))
    assert WorkoutSession.query.count() == 0


def test_wrong_owner_cannot_read_proposal_or_receipt(foundation):
    uid, token, _ = foundation
    other = User(username='other', email='other@example.invalid', cognito_sub='other')
    db.session.add(other); db.session.commit(); oid=other.id
    with pytest.raises(s.ProposalUnavailable): s.confirm_replacement(oid, token, 'confirm-key-1')
    s.confirm_replacement(uid, token, 'confirm-key-1')
    with pytest.raises(s.ProposalUnavailable): s.confirm_replacement(oid, token, 'confirm-key-1')
    assert Receipt.query.count() == 1


def test_missing_base_fails_closed(foundation):
    uid, token, _ = foundation
    TrainingPlan.query.filter_by(user_id=uid).delete(); db.session.commit()
    with pytest.raises(s.ReplacementRefusal) as e: s.confirm_replacement(uid, token, 'confirm-key-1')
    assert e.value.reason == 'STALE' and TrainingPlan.query.count() == 0


def test_replay_checks_frozen_intent_integrity(foundation):
    uid, token, _ = foundation
    s.confirm_replacement(uid, token, 'confirm-key-1')
    db.session.execute(text("UPDATE training_plan_replacement_proposal SET candidate_fingerprint = :digest WHERE public_id = :token"), {'digest': 'f'*64, 'token': token})
    db.session.commit(); db.session.remove()
    with pytest.raises(s.ConfirmationConflict):
        s.confirm_replacement(uid, token, 'confirm-key-1')
    assert Receipt.query.count() == 1


@pytest.mark.parametrize('kind', ['proposal', 'receipt'])
def test_authority_is_immutable(foundation, kind):
    uid, token, _ = foundation
    if kind == 'receipt':
        s.confirm_replacement(uid, token, 'confirm-key-1')
        row = Receipt.query.one(); row.intent_fingerprint = 'f'*64
    else:
        row = Proposal.query.one(); row.candidate_plan_data = plan_text('Changed')
    with pytest.raises(ValueError, match='immutable'): db.session.commit()
    db.session.rollback()


def test_lost_response_after_real_commit_replays_original(foundation, monkeypatch):
    uid, token, _ = foundation
    real_commit = db.session.commit
    def lose_response():
        real_commit()
        raise RuntimeError('simulated response loss')
    monkeypatch.setattr(db.session, 'commit', lose_response)
    with pytest.raises(RuntimeError):s.confirm_replacement(uid, token, 'confirm-key-1')
    monkeypatch.setattr(db.session, 'commit', real_commit)
    original = Receipt.query.one().result_lineage_id
    db.session.remove()
    replay = s.confirm_replacement(uid, token, 'confirm-key-1')
    assert replay.replayed and replay.lineage_id == original
    assert Receipt.query.count() == 1 and TrainingPlan.query.count() == 1
