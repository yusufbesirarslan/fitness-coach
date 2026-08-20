"""PR4 confirmation scenarios: impact, durable proposal, structural gate."""
import json

from app.extensions import db
from app.models import (
    PlanMutationRecord,
    TrainingPlan,
    TrainingPlanConfirmationProposal,
    WorkoutLog,
    WorkoutSession,
)
from app.observability import assign_request_id
from app.services import ai_coach, coach_plan_tools, plan_confirmation
from app.services.coach_plan_tools import identity, results
from app.services.plan_mutation import (
    ACTOR_AI_COACH,
    MutationContext,
    RemoveExerciseCommand,
    apply_plan_mutation,
)
from app.services.today_facts import get_active_plan
from app.services.workout_session.models import compute_fingerprint
from app.timeutil import day_key
from tests.test_coach_plan_tool_characterization import (  # noqa: F401
    _program, planned_user,
)
from tests.test_coach_plan_tools import (  # noqa: F401
    call, day_exercises, journal, names, plan_version, tools_on, turn,
)

REMOVE = "remove_training_plan_exercise"
MOVE = "move_training_plan_day"
REPLACE = "replace_training_plan_exercise"
PRESCRIBE = "update_training_plan_prescription"
CONFIRM = coach_plan_tools.CONFIRM_TOOL
CANCEL = coach_plan_tools.CANCEL_PENDING_TOOL


def _new_turn(app, message=""):
    assign_request_id()
    ai_coach._begin_coach_turn(message)


def test_replace_without_session_impact_still_applies(
        app, planned_user, tools_on, turn):
    result = call(planned_user.id, REPLACE,
                  {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Machine Press"})
    assert result["status"] == results.STATUS_APPLIED
    assert names(planned_user.id)[0] == "Machine Press"
    assert plan_version(planned_user.id) == 1
    assert len(journal(planned_user.id)) == 1


def test_remove_requires_confirmation_and_does_not_mutate(
        app, planned_user, tools_on, turn):
    before = names(planned_user.id)
    result = call(planned_user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Bench Press"})

    assert result["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert result["operation"] == "remove_exercise"
    assert "proposal" not in json.dumps(result).lower()
    assert names(planned_user.id) == before
    assert plan_version(planned_user.id) == 0
    assert journal(planned_user.id) == []
    pending = plan_confirmation.get_pending(planned_user.id)
    assert pending is not None
    assert pending.public_id not in json.dumps(result)


def test_move_day_requires_confirmation(app, planned_user, tools_on, turn):
    before = day_exercises(planned_user.id, "Cuma")
    result = call(planned_user.id, MOVE,
                  {"day": "Cuma", "target_day": "Cumartesi"})
    assert result["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert day_exercises(planned_user.id, "Cuma") == before
    assert plan_version(planned_user.id) == 0
    assert journal(planned_user.id) == []


def test_replace_on_an_active_session_day_requires_confirmation(
        app, planned_user, tools_on, turn):
    plan = get_active_plan(planned_user.id)
    db.session.add(WorkoutSession(
        user_id=planned_user.id, status="active", workout_date=day_key(),
        weekday_slot="Pazartesi", source="scheduled",
        planned_training_plan_id=plan.id,
        plan_fingerprint=compute_fingerprint(["Bench Press", "Shoulder Press"])))
    db.session.commit()
    before = names(planned_user.id)
    session_before = WorkoutSession.query.filter_by(
        user_id=planned_user.id).one()
    fingerprint = session_before.plan_fingerprint
    version = session_before.version

    result = call(planned_user.id, REPLACE,
                  {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Machine Press"})

    assert result["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert "ACTIVE_SESSION_IMPACT" in result["reason_codes"]
    assert names(planned_user.id) == before
    db.session.expire_all()
    session = WorkoutSession.query.filter_by(user_id=planned_user.id).one()
    assert session.plan_fingerprint == fingerprint
    assert session.status == "active"
    assert session.version == version


def test_model_cannot_self_confirm(app, planned_user, tools_on, turn):
    call(planned_user.id, REMOVE,
         {"day": "Pazartesi", "exercise": "Bench Press"})
    result = call(planned_user.id, CONFIRM)

    assert result["error"] == results.ERROR_CONFIRMATION_NOT_AUTHORIZED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert journal(planned_user.id) == []
    assert plan_confirmation.get_pending(planned_user.id) is not None


def test_same_turn_cannot_confirm_a_proposal_just_created(
        app, planned_user, tools_on):
    """A confirm-form user turn is not license to stage AND apply.

    Publication at turn start has no pending, so the six command tools are
    advertised. After the server stages a proposal, OpenAI would otherwise
    re-publish confirm on the same 'evet' and the executor would apply —
    the user never saw the durable proposal on a later turn.
    """
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        staged = call(planned_user.id, REMOVE,
                      {"day": "Pazartesi", "exercise": "Bench Press"})
        published = [t["name"] for t in
                     coach_plan_tools.published_plan_tool_defs(planned_user.id)]
        result = call(planned_user.id, CONFIRM)

    assert staged["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert CONFIRM not in published
    assert result["error"] == results.ERROR_CONFIRMATION_NOT_AUTHORIZED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert journal(planned_user.id) == []
    assert plan_confirmation.get_pending(planned_user.id) is not None


def test_real_confirmation_applies_the_stored_mutation(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        result = call(planned_user.id, CONFIRM)

    assert result["status"] == results.STATUS_APPLIED
    assert names(planned_user.id) == ["Shoulder Press"]
    assert plan_version(planned_user.id) == 1
    assert len(journal(planned_user.id)) == 1
    assert plan_confirmation.get_pending(planned_user.id) is None


def test_cancellation_never_changes_the_plan(app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "iptal")
        result = call(planned_user.id, CANCEL)
        again = call(planned_user.id, CANCEL)

    assert result["status"] == results.STATUS_CANCELLED
    assert again["status"] == results.STATUS_CANCELLED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert plan_version(planned_user.id) == 0
    assert journal(planned_user.id) == []
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        denied = call(planned_user.id, CONFIRM)
    assert denied["error"] == results.ERROR_NO_PENDING_CONFIRMATION


def test_stale_plan_invalidates_the_proposal(app, planned_user, tools_on):
    from app.services.plan_mutation import ReplaceExerciseCommand

    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    apply_plan_mutation(
        planned_user.id,
        ReplaceExerciseCommand(
            day="Pazartesi", exercise="Shoulder Press",
            replacement="Face Pull"),
        MutationContext(idempotency_key="user-stale-0001", actor=ACTOR_AI_COACH))
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        result = call(planned_user.id, CONFIRM)

    assert result["error"] == results.ERROR_PENDING_CONFIRMATION_STALE
    assert "Bench Press" in names(planned_user.id)
    assert "Face Pull" in names(planned_user.id)
    assert plan_confirmation.get_pending(planned_user.id) is None


def test_lineage_replacement_fails_closed(app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    old = TrainingPlan.query.filter_by(user_id=planned_user.id).one()
    db.session.delete(old)
    db.session.add(TrainingPlan(
        user_id=planned_user.id, score=8,
        plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        result = call(planned_user.id, CONFIRM)
    assert result["error"] == results.ERROR_PENDING_CONFIRMATION_STALE
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]


def test_duplicate_proposal_converges(app, planned_user, tools_on, turn):
    first = call(planned_user.id, REMOVE,
                 {"day": "Pazartesi", "exercise": "Bench Press"})
    second = call(planned_user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Bench Press"})
    assert first["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert second["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert TrainingPlanConfirmationProposal.query.filter_by(
        user_id=planned_user.id,
        status=plan_confirmation.STATUS_PENDING).count() == 1


def test_duplicate_confirmation_converges(app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        first = call(planned_user.id, CONFIRM)
        second = call(planned_user.id, CONFIRM)
    assert first["status"] == results.STATUS_APPLIED
    assert second["error"] == results.ERROR_NO_PENDING_CONFIRMATION
    assert names(planned_user.id).count("Bench Press") == 0
    assert len(journal(planned_user.id)) == 1


def test_crash_window_replays_instead_of_mutating_twice(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
        pending = plan_confirmation.get_pending(planned_user.id)
        key = identity.confirmation_operation_key(pending.public_id)
        apply_plan_mutation(
            planned_user.id,
            RemoveExerciseCommand(day="Pazartesi", exercise="Bench Press"),
            MutationContext(idempotency_key=key, actor=ACTOR_AI_COACH))
        assert plan_confirmation.get_pending(planned_user.id) is not None
        assert plan_version(planned_user.id) == 1
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        result = call(planned_user.id, CONFIRM)
    assert result["status"] == results.STATUS_REPLAYED
    assert len(journal(planned_user.id)) == 1
    assert plan_confirmation.get_pending(planned_user.id) is None


def test_cancel_after_crash_window_does_not_claim_plan_unchanged(
        app, planned_user, tools_on):
    """Mutation committed, proposal still PENDING: cancel must not lie."""
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
        pending = plan_confirmation.get_pending(planned_user.id)
        key = identity.confirmation_operation_key(pending.public_id)
        apply_plan_mutation(
            planned_user.id,
            RemoveExerciseCommand(day="Pazartesi", exercise="Bench Press"),
            MutationContext(idempotency_key=key, actor=ACTOR_AI_COACH))
        assert plan_confirmation.get_pending(planned_user.id) is not None
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "iptal")
        result = call(planned_user.id, CANCEL)

    assert result["status"] == results.STATUS_REPLAYED
    assert "aynı kaldı" not in result.get("summary", "").lower()
    assert names(planned_user.id).count("Bench Press") == 0
    assert len(journal(planned_user.id)) == 1
    assert plan_confirmation.get_pending(planned_user.id) is None


def test_cancel_does_not_rebind_applied_proposal_after_lineage_replacement(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        applied = call(planned_user.id, CONFIRM)
    assert applied["status"] == results.STATUS_APPLIED

    old_plan = TrainingPlan.query.filter_by(user_id=planned_user.id).one()
    db.session.delete(old_plan)
    db.session.add(TrainingPlan(
        user_id=planned_user.id, score=8,
        plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()

    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "iptal")
        cancelled = call(planned_user.id, CANCEL)

    assert cancelled["status"] == results.STATUS_CANCELLED
    assert cancelled["operation"] == "cancel_pending"
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert plan_version(planned_user.id) == 0
    assert len(journal(planned_user.id)) == 1
    proposal = TrainingPlanConfirmationProposal.query.filter_by(
        user_id=planned_user.id).one()
    assert proposal.status == plan_confirmation.STATUS_APPLIED


def test_cancel_cannot_execute_applied_proposal_without_journal(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    proposal = plan_confirmation.get_pending(planned_user.id)
    proposal.status = plan_confirmation.STATUS_APPLIED
    db.session.commit()

    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "iptal")
        cancelled = call(planned_user.id, CANCEL)

    assert cancelled["status"] == results.STATUS_CANCELLED
    assert cancelled["operation"] == "cancel_pending"
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert plan_version(planned_user.id) == 0
    assert journal(planned_user.id) == []


def test_flag_off_creates_no_proposal(app, planned_user, turn):
    result = call(planned_user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Bench Press"})
    assert result["error"] == results.ERROR_CAPABILITY_DISABLED
    assert plan_confirmation.get_pending(planned_user.id) is None
    with app.test_request_context("/ask"):
        names_published = [t["function"]["name"]
                           for t in ai_coach._openai_tools_for_call(
                               planned_user.id)]
    assert CONFIRM not in names_published
    assert CANCEL not in names_published


def test_pending_blocks_other_plan_writes(app, planned_user, tools_on, turn):
    call(planned_user.id, REMOVE,
         {"day": "Pazartesi", "exercise": "Bench Press"})
    blocked = call(planned_user.id, REPLACE,
                   {"day": "Pazartesi", "exercise": "Shoulder Press",
                    "replacement": "Face Pull"})
    assert blocked["error"] == results.ERROR_PENDING_CONFIRMATION_EXISTS
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]


def test_mixed_confirmation_text_does_not_authorize(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "yes but make it 8 reps")
        result = call(planned_user.id, CONFIRM)
        published = [t["name"] for t in
                     coach_plan_tools.published_plan_tool_defs(planned_user.id)]
    assert result["error"] == results.ERROR_CONFIRMATION_NOT_AUTHORIZED
    assert published == []
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]


def test_negation_does_not_authorize_confirm(app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "don't do it")
        result = call(planned_user.id, CONFIRM)
    assert result["error"] == results.ERROR_CONFIRMATION_NOT_AUTHORIZED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]


def test_workout_log_is_untouched(app, planned_user, tools_on):
    log = WorkoutLog(user_id=planned_user.id, exercise_name="Bench Press",
                     sets=3, reps=10, weight_kg=80, volume=2400)
    db.session.add(log)
    db.session.commit()
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        call(planned_user.id, CONFIRM)
    db.session.expire_all()
    stored = WorkoutLog.query.filter_by(user_id=planned_user.id).one()
    assert (stored.exercise_name, stored.sets, stored.volume) == (
        "Bench Press", 3, 2400)


def test_pending_fallback_wording_is_an_error_fallback_and_honest(app):
    from app.services.response_formatter import is_coach_error_fallback

    for language in ("tr", "en"):
        text = ai_coach._COACH_FALLBACKS[language][
            "tool_plan_confirmation_pending"]
        assert is_coach_error_fallback(text) is True
        assert "kaydedildi" not in text.lower()
        assert "was saved" not in text.lower()


def test_user_b_cannot_confirm_user_a(app, planned_user, make_user, tools_on):
    other = make_user("intruder")
    db.session.add(TrainingPlan(
        user_id=other.id, score=8,
        plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "evet")
        result = call(other.id, CONFIRM)
    assert result["error"] == results.ERROR_NO_PENDING_CONFIRMATION
    assert plan_confirmation.get_pending(planned_user.id) is not None
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
