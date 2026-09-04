"""Server-owned confirmation resolution for one Coach turn.

Pending workout/meal logs live on ``PendingAction``. Pending training-plan
mutations live on ``TrainingPlanConfirmationProposal``. Both are consumed here
from the raw user turn *before* the provider loop, using the same structural
CONFIRM/CANCEL/NONE parser as PR4. The model does not choose whether a pending
action executes.
"""
import json
import re
import unicodedata

from flask import g

from app.i18n import t
from app.models import PendingAction
from app.observability import assign_request_id
from app.services import coach_plan_tools, plan_confirmation
from app.services.coach_plan_policy import CANCEL, CONFIRM, NONE
from app.services.coach_plan_tools import results
from app.services.coach_plan_tools import clarifications as clar_mod
from app.services.coach_plan_tools.grounding import (
    PROPOSED_REPS,
    PROPOSED_SETS,
    continuation_matches_record,
    current_user_message,
    followup_add_arguments,
    followup_mutation,
    invalid_candidate_result,
    is_continuation_attempt,
)
from app.services.coach_plan_tools.weekdays import (
    localize_weekday,
    localize_weekday_text,
)


_LOG_STAGE_TOOLS = frozenset({
    "fetch_nutrition_and_stage_log",
    "stage_workout_log",
})

# Pre-tool assistant prose is held until these tools finish. Meal staging is
# intentionally omitted so the nutrition path keeps streaming model copy.
_HELD_STREAM_TOOLS = frozenset(coach_plan_tools.PLAN_WRITE_TOOL_NAMES) | frozenset({
    "stage_workout_log",
    "confirm_and_commit_workout_log",
    "cancel_pending_log",
})

_PLAN_CHANGE_PATTERNS = (
    r"\b(?:add|addition|change|exercise|plan|remove|replace|workout)\b",
    r"\b(?:antrenman|degisik\w*|egzersiz|ekle\w*|cikar\w*|yerine)\b",
    r"\bprogram\w*\b",
)
_CONFIRMATION_REQUEST_PATTERNS = (
    r"\bconfirm\b",
    r"\bonay\w*\b",
    r"\b(?:would you like|do you want)(?: me)? to\b",
    r"\b(?:can|may|shall|should) (?:i|we)\b",
    r"\b(?:reply|say) yes\b",
    r"\bister misin\b",
    r"\b(?:ekle|cikar|degistir)\w* mi\b",
)


def resolve_pending_turn(user_id, language="tr"):
    """Consume a pending confirmation from this turn's intent, or ask.

    Returns a user-facing reply when the provider loop must not run, else
    ``None``. Fail closed: mixed pending kinds never execute on a bare yes.
    """
    try:
        _ensure_request_id()
    except RuntimeError:
        return None
    intent = coach_plan_tools.current_confirmation_intent()
    plan_pending = _plan_pending(user_id)
    log_pending = active_log_pending(user_id)
    n = int(plan_pending is not None) + int(log_pending is not None)
    if n == 0:
        return _complete_grounded_followup(user_id, language)
    if n > 1:
        if intent == CANCEL:
            return _cancel_all(user_id, language)
        return t("coach.confirm.ambiguous", locale=language)
    if intent == NONE:
        return t("coach.confirm.clarify", locale=language)
    if intent == CONFIRM:
        if plan_pending is not None:
            return _confirm_plan(user_id, language)
        return _confirm_log(user_id, log_pending, language)
    if intent == CANCEL:
        if plan_pending is not None:
            return _cancel_plan(user_id, language)
        return _cancel_log(user_id, language)
    return None


def reply_after_tools(user_id, language="tr", tool_results=None):
    """Server-owned user copy once this turn's tools have settled.

    Pending proposals stay future-tense. APPLY_NOW / replayed mutations use
    past-tense success copy so the model cannot ask for confirmation after
    persistence already succeeded. Grounded clarifications (missing
    prescription, unknown exercise, ambiguous workout) are also owned here
    so the model cannot persist a guess after asking.
    """
    pending = canonical_pending_prompt(user_id, language)
    if pending:
        return pending
    applied = []
    clarifications = []
    for payload in tool_results or ():
        if not isinstance(payload, dict):
            continue
        if payload.get("status") in (
                results.STATUS_APPLIED, results.STATUS_REPLAYED):
            applied.append(payload)
        elif payload.get("status") == results.STATUS_NEEDS_INPUT:
            clarifications.append(payload)
    if applied:
        return _format_plan_applied(applied[-1], language)
    if clarifications:
        return _format_plan_clarification(clarifications[-1], language)
    return None


def grounded_provider_reply(user_id, language, text):
    """Suppress model-authored plan confirmation with no durable proposal.

    This is a narrow output invariant, not an intent classifier: it does not
    infer or execute a mutation. It only refuses to expose confirmation-request
    copy when the server cannot read the state that such copy claims exists.
    """
    reply = text or ""
    if not _asks_for_plan_confirmation(reply):
        return reply
    if _plan_pending(user_id) is not None:
        return reply
    return t("coach.confirm.no_plan_proposal", locale=language)


def holds_stream_preamble(tool_names):
    """True when pre-tool prose must not reach the client yet."""
    return any(name in _HELD_STREAM_TOOLS for name in tool_names or ())


def should_block_plan_mutation(user_id):
    """Bare confirm/cancel with nothing pending must not start a new mutation."""
    intent = coach_plan_tools.current_confirmation_intent()
    if intent not in (CONFIRM, CANCEL):
        return False
    return _plan_pending(user_id) is None


def blocked_plan_mutation_payload():
    return results.error_result(results.ERROR_NO_PENDING_CONFIRMATION)


def canonical_pending_prompt(user_id, language="tr"):
    """User-facing propose copy for a pending created this turn.

    Future tense until persistence succeeds. Meal staging keeps model prose
    (nutrition path is otherwise unchanged).
    """
    if coach_plan_tools.proposal_created_this_turn():
        pending = _plan_pending(user_id)
        if pending is not None:
            return _format_plan_proposal(pending, language)
    try:
        staged_ids = getattr(g, "_coach_staged_ids", None) or set()
    except RuntimeError:
        return None
    if not staged_ids:
        return None
    try:
        pending = (
            PendingAction.query
            .filter(PendingAction.id.in_(list(staged_ids)))
            .order_by(PendingAction.created_at.desc(), PendingAction.id.desc())
            .first()
        )
    except Exception:
        return None
    if pending is None or pending.action_type != "log_workout":
        return None
    return _format_workout_proposal(pending.payload or {}, language)


def active_log_pending(user_id):
    """The user's latest staged log that this turn did not itself create."""
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        return None
    try:
        pending = (
            PendingAction.query
            .filter_by(user_id=user_id)
            .order_by(PendingAction.created_at.desc(), PendingAction.id.desc())
            .first()
        )
    except Exception:
        return None
    if pending is None:
        return None
    from app.services import ai_coach
    if ai_coach._staged_this_turn(pending.id):
        return None
    return pending


def should_refuse_new_staging(user_id):
    """Whether a new meal/workout stage would restart or mix confirmation."""
    intent = coach_plan_tools.current_confirmation_intent()
    if intent in (CONFIRM, CANCEL):
        return True
    if _plan_pending(user_id) is not None:
        return True
    if (coach_plan_tools.plan_changed_this_turn()
            or coach_plan_tools.proposal_created_this_turn()):
        return True
    return False


def staging_refusal_payload():
    return {
        "status": "error",
        "message": (
            "Bekleyen bir onay var veya bu tur onay/iptal. Yeni kayıt stage "
            "etme; ilgili confirm/cancel yolunu kullan."
        ),
    }


def filter_coach_tool_defs(defs, user_id=None):
    """Hide log staging when a confirmation is in flight or being answered.

    Confirm/cancel log tools stay published so a bypassed interceptor can still
    dispatch them. Staging is what restarts the confirmation loop.
    """
    intent = coach_plan_tools.current_confirmation_intent()
    plan_pending = _plan_pending(user_id) if user_id else None
    log_pending = active_log_pending(user_id) if user_id else None
    hide_stage = (
        intent in (CONFIRM, CANCEL)
        or plan_pending is not None
        or log_pending is not None
    )
    if not hide_stage:
        return list(defs)
    return [tool for tool in defs if tool.get("name") not in _LOG_STAGE_TOOLS]


def _ensure_request_id():
    try:
        if not getattr(g, "request_id", None):
            assign_request_id()
    except RuntimeError:
        pass


def _asks_for_plan_confirmation(text):
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold().replace("ı", "i")
    return (
        any(re.search(pattern, normalized)
            for pattern in _PLAN_CHANGE_PATTERNS)
        and any(re.search(pattern, normalized)
                for pattern in _CONFIRMATION_REQUEST_PATTERNS)
    )


def _plan_pending(user_id):
    try:
        return plan_confirmation.get_pending(user_id)
    except Exception:
        return None


def _confirm_plan(user_id, language):
    result = coach_plan_tools.execute_plan_tool(
        user_id, coach_plan_tools.CONFIRM_TOOL, {})
    status = result.get("status")
    if status in (results.STATUS_APPLIED, results.STATUS_REPLAYED,
                  results.STATUS_NO_OP):
        return _format_plan_applied(result, language)
    return t("coach.confirm.plan_failed", locale=language)


def _cancel_plan(user_id, language):
    coach_plan_tools.execute_plan_tool(
        user_id, coach_plan_tools.CANCEL_PENDING_TOOL, {})
    return t("coach.confirm.cancelled", locale=language)


def _confirm_log(user_id, pending, language):
    from app.services import ai_coach
    action_type = pending.action_type
    payload = dict(pending.payload or {})
    if action_type == "log_workout":
        raw = ai_coach._tool_confirm_and_commit_workout_log(user_id)
        copy_key = "coach.confirm.workout_logged"
    else:
        raw = ai_coach._tool_confirm_and_commit_meal_log(user_id)
        copy_key = "coach.confirm.meal_logged"
    try:
        result = json.loads(raw)
    except (TypeError, ValueError):
        result = {}
    if result.get("status") != "committed":
        return t("coach.confirm.clarify", locale=language)
    logged = result.get("logged") or payload
    if action_type == "log_workout":
        extra = {
            "exercise": logged.get("exercise_name") or "",
            "sets": logged.get("sets") or "",
            "reps": logged.get("reps") or "",
        }
    else:
        extra = {"food": logged.get("food_name") or ""}
    return t(copy_key, locale=language, **extra)


def _cancel_log(user_id, language):
    from app.services import ai_coach
    ai_coach._tool_cancel_pending_log(user_id)
    return t("coach.confirm.cancelled", locale=language)


def _cancel_all(user_id, language):
    from app.services import ai_coach
    if _plan_pending(user_id) is not None:
        coach_plan_tools.execute_plan_tool(
            user_id, coach_plan_tools.CANCEL_PENDING_TOOL, {})
    if active_log_pending(user_id) is not None:
        ai_coach._tool_cancel_pending_log(user_id)
    return t("coach.confirm.cancelled", locale=language)


def _format_workout_proposal(payload, language):
    return t(
        "coach.confirm.propose_workout",
        locale=language,
        exercise=payload.get("exercise_name") or "",
        sets=payload.get("sets") or "",
        reps=payload.get("reps") or "",
    )


def _day_for_copy(value, language):
    return localize_weekday_text(
        localize_weekday(value or "", language), language)


def _format_plan_proposal(pending, language):
    payload = pending.command_payload or {}
    kind = pending.command_type
    day = _day_for_copy(payload.get("day") or "", language)
    if kind == "add_exercise":
        return t(
            "coach.confirm.propose_plan_add",
            locale=language,
            exercise=payload.get("exercise") or "",
            day=day,
            sets=payload.get("sets") or "",
            reps=payload.get("reps") or "",
        )
    if kind == "remove_exercise":
        return t(
            "coach.confirm.propose_plan_remove",
            locale=language,
            exercise=payload.get("exercise") or "",
            day=day,
        )
    if kind == "replace_exercise":
        return t(
            "coach.confirm.propose_plan_replace",
            locale=language,
            exercise=payload.get("exercise") or "",
            replacement=payload.get("replacement") or "",
            day=day,
        )
    return t("coach.confirm.propose_plan", locale=language)


def _format_plan_applied(result, language):
    change = result.get("change") or {}
    operation = result.get("operation") or ""
    day = _day_for_copy(change.get("day") or "", language)
    if operation == "add_exercise":
        return t(
            "coach.confirm.plan_add",
            locale=language,
            exercise=change.get("exercise") or "",
            day=day,
        )
    if operation == "remove_exercise":
        return t(
            "coach.confirm.plan_remove",
            locale=language,
            exercise=change.get("exercise") or "",
            day=day,
        )
    if operation == "replace_exercise":
        return t(
            "coach.confirm.plan_replace",
            locale=language,
            exercise=change.get("exercise") or "",
            replacement=change.get("replacement") or "",
            day=day,
        )
    return t("coach.confirm.plan_generic", locale=language)


def _complete_grounded_followup(user_id, language):
    """Apply a prescription the user just supplied or accepted.

    Completes a server-owned clarification record from the previous turn.
    Assistant chat text is not authority. Confirmation proposals are
    handled above via TrainingPlanConfirmationProposal. Shared-store
    failure fails closed: no mutation, truthful retry copy.
    """
    try:
        invalid = invalid_candidate_result(user_id)
        if invalid:
            return _format_plan_clarification(invalid, language)
        mutation = followup_mutation(user_id)
        if not mutation:
            arguments = followup_add_arguments(user_id)
            if not arguments:
                return None
            tool, arguments = "add_training_plan_exercise", arguments
        else:
            tool, arguments = mutation
        taken = clar_mod.consume(user_id)
        if taken is None:
            return None
        if not continuation_matches_record(taken, tool, arguments):
            # The arguments were planned from one read of a shared store and
            # the record actually taken is a different request. Consume-once
            # has already retired both; executing would run a mutation this
            # continuation never established.
            return None
        result = coach_plan_tools.execute_plan_tool(user_id, tool, arguments)
    except clar_mod.ClarificationAuthorityUnavailable:
        if is_continuation_attempt(current_user_message()):
            return t("coach.plan.clarification_unavailable", locale=language)
        return None
    if result.get("status") in (results.STATUS_APPLIED, results.STATUS_REPLAYED):
        return _format_plan_applied(result, language)
    if result.get("status") == results.STATUS_NEEDS_INPUT:
        return _format_plan_clarification(result, language)
    return None


def _format_plan_clarification(payload, language):
    reason = payload.get("reason") or ""
    change = payload.get("change") or {}
    day = _day_for_copy(change.get("day") or "", language)
    exercise = change.get("exercise") or ""
    detail = localize_weekday_text(payload.get("detail") or "", language)
    if reason == "missing_prescription":
        label = detail if detail and detail != reason else day
        label = localize_weekday_text(label, language)
        return t(
            "coach.plan.ask_sets_reps",
            locale=language,
            label=label,
            day=day,
            exercise=exercise,
            sets=PROPOSED_SETS,
            reps=PROPOSED_REPS,
        )
    if reason == "missing_reps":
        return t(
            "coach.plan.ask_reps",
            locale=language,
            exercise=exercise,
            sets=change.get("sets") or "",
        )
    if reason == "missing_sets":
        return t(
            "coach.plan.ask_sets",
            locale=language,
            exercise=exercise,
            reps=change.get("reps") or "",
        )
    if reason == "exercise_unknown":
        return t(
            "coach.plan.exercise_unknown",
            locale=language,
            exercise=exercise or detail,
        )
    if reason == "exercise_suggest":
        return t(
            "coach.plan.exercise_suggest",
            locale=language,
            exercise=exercise,
            suggestion=detail,
        )
    if reason == "ambiguous_workout":
        candidates = [
            _day_for_copy(item.strip(), language)
            for item in str(detail).split(",") if item.strip()
        ]
        return t(
            "coach.plan.workout_ambiguous",
            locale=language,
            candidates=", ".join(candidates),
        )
    if reason == "workout_not_found":
        return t("coach.plan.workout_unknown", locale=language)
    return t("coach.plan.workout_unknown", locale=language)
