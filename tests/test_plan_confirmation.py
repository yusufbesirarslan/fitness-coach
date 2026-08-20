"""Durable pending plan-confirmation state (PR4 T3).

Proposal creation must not touch TrainingPlan, mutation_version, or the
journal. Isolation is structural: User B cannot load User A's pending row.
"""
import json

import pytest

from app.extensions import db
from app.models import (
    PlanMutationRecord,
    TrainingPlan,
    TrainingPlanConfirmationProposal,
)
from app.services import plan_confirmation
from app.services.plan_mutation.fingerprint import snapshot_fingerprint
from tests.test_coach_plan_tool_characterization import (  # noqa: F401
    _program, open_transaction, planned_user,
)


def _fields(user):
    plan = TrainingPlan.query.filter_by(user_id=user.id).one()
    return dict(
        lineage_id=plan.lineage_id,
        mutation_version=plan.mutation_version,
        snapshot_fingerprint=snapshot_fingerprint(plan.plan_data),
        command_type="remove_exercise",
        command_payload={"day": "Pazartesi", "exercise": "Bench Press"},
        command_fingerprint="a" * 64,
        reason_codes=["REMOVE_EXERCISE"],
        summary="Pazartesi gününden Bench Press çıkarılacak.",
    )


def _create(user, **overrides):
    fields = _fields(user)
    fields.update(overrides)
    return plan_confirmation.create_or_reuse_pending(user.id, **fields)


def test_creating_a_proposal_does_not_mutate_the_plan(
        app, planned_user):
    plan = TrainingPlan.query.filter_by(user_id=planned_user.id).one()
    before = (plan.plan_data, plan.mutation_version, plan.lineage_id)

    row, reused = _create(planned_user)
    residual = open_transaction()

    assert not reused
    assert not residual
    db.session.expire_all()
    plan = TrainingPlan.query.filter_by(user_id=planned_user.id).one()
    assert (plan.plan_data, plan.mutation_version, plan.lineage_id) == before
    assert PlanMutationRecord.query.filter_by(user_id=planned_user.id).count() == 0
    assert row.status == plan_confirmation.STATUS_PENDING


def test_duplicate_create_reuses_the_same_pending_row(app, planned_user):
    first, _ = _create(planned_user)
    second, reused = _create(planned_user)
    assert reused
    assert second.public_id == first.public_id
    assert TrainingPlanConfirmationProposal.query.filter_by(
        user_id=planned_user.id).count() == 1


def test_a_different_pending_command_does_not_stack(app, planned_user):
    _create(planned_user)
    with pytest.raises(plan_confirmation.PendingConfirmationConflict):
        _create(planned_user, command_fingerprint="b" * 64,
                command_payload={"day": "Pazartesi", "exercise": "Squat"})
    assert TrainingPlanConfirmationProposal.query.filter_by(
        user_id=planned_user.id,
        status=plan_confirmation.STATUS_PENDING).count() == 1


def test_user_b_cannot_see_or_cancel_user_a_pending(
        app, planned_user, make_user):
    other = make_user("other")
    db.session.add(TrainingPlan(
        user_id=other.id, score=8,
        plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()
    created, _ = _create(planned_user)

    assert plan_confirmation.get_pending(other.id) is None
    assert plan_confirmation.cancel_pending(other.id) is None
    assert plan_confirmation.lock_proposal(other.id, created.public_id) is None
    assert plan_confirmation.get_pending(planned_user.id).public_id == \
        created.public_id


def test_lock_proposal_is_owner_scoped_and_re_reads(app, planned_user):
    created, _ = _create(planned_user)
    locked = plan_confirmation.lock_proposal(planned_user.id, created.public_id)
    assert locked is not None
    assert locked.public_id == created.public_id
    assert locked.user_id == planned_user.id
    db.session.rollback()


def test_cancel_is_idempotent_and_does_not_touch_the_plan(
        app, planned_user):
    plan = TrainingPlan.query.filter_by(user_id=planned_user.id).one()
    before = (plan.plan_data, plan.mutation_version)
    _create(planned_user)

    first = plan_confirmation.cancel_pending(planned_user.id)
    second = plan_confirmation.cancel_pending(planned_user.id)

    assert first.status == plan_confirmation.STATUS_CANCELLED
    assert second is None
    db.session.expire_all()
    plan = TrainingPlan.query.filter_by(user_id=planned_user.id).one()
    assert (plan.plan_data, plan.mutation_version) == before
    assert PlanMutationRecord.query.filter_by(user_id=planned_user.id).count() == 0
    assert plan_confirmation.get_pending(planned_user.id) is None


def test_mark_applied_is_conditional_on_pending(app, planned_user):
    row, _ = _create(planned_user)
    applied = plan_confirmation.mark_applied(planned_user.id, row.public_id)
    assert applied.status == plan_confirmation.STATUS_APPLIED
    again = plan_confirmation.mark_applied(planned_user.id, row.public_id)
    assert again.status == plan_confirmation.STATUS_APPLIED
    assert plan_confirmation.get_pending(planned_user.id) is None


def test_mark_stale_makes_the_proposal_non_executable(app, planned_user):
    row, _ = _create(planned_user)
    plan_confirmation.mark_stale(planned_user.id, row.public_id)
    assert plan_confirmation.get_pending(planned_user.id) is None
    db.session.expire_all()
    stored = TrainingPlanConfirmationProposal.query.filter_by(
        public_id=row.public_id).one()
    assert stored.status == plan_confirmation.STATUS_STALE
