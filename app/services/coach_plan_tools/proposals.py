"""Encode a typed command for durable proposal storage, and reconstruct it.

The stored payload is the bounded field set of the typed command — never a
raw prompt, never a provider body. Reconstruction names every constructor
keyword; it does not splat model JSON into a command.
"""
from dataclasses import dataclass

from app.services.coach_plan_policy import UNDO_LAST_CHANGE
from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)
from app.services.plan_mutation.document import apply_command, parse_plan_document
from app.services.plan_mutation.fingerprint import (
    UNDO_COMMAND_TYPE,
    command_type,
    semantic_fingerprint,
    snapshot_fingerprint,
    undo_fingerprint,
)
from app.services.today_facts import get_active_plan


def encode_command(command):
    """Return ``(command_type, payload, fingerprint)`` for durable storage."""
    if command is UNDO_LAST_CHANGE:
        return UNDO_COMMAND_TYPE, {}, undo_fingerprint()
    kind = command_type(command)
    if isinstance(command, ReplaceExerciseCommand):
        payload = {
            "day": command.day,
            "exercise": command.exercise,
            "replacement": command.replacement,
        }
        if command.sets is not None:
            payload["sets"] = command.sets
        if command.reps is not None:
            payload["reps"] = command.reps
        return kind, payload, semantic_fingerprint(command)
    if isinstance(command, AddExerciseCommand):
        return kind, {
            "day": command.day,
            "exercise": command.exercise,
            "sets": command.sets,
            "reps": command.reps,
        }, semantic_fingerprint(command)
    if isinstance(command, RemoveExerciseCommand):
        return kind, {
            "day": command.day, "exercise": command.exercise,
        }, semantic_fingerprint(command)
    if isinstance(command, UpdateExercisePrescriptionCommand):
        payload = {"day": command.day, "exercise": command.exercise}
        if command.sets is not None:
            payload["sets"] = command.sets
        if command.reps is not None:
            payload["reps"] = command.reps
        return kind, payload, semantic_fingerprint(command)
    if isinstance(command, MoveTrainingDayCommand):
        return kind, {
            "day": command.day, "target_day": command.target_day,
        }, semantic_fingerprint(command)
    raise TypeError("unsupported mutation command")


def decode_command(command_type_name, payload):
    """Rebuild the typed command from a server-stored payload."""
    payload = payload or {}
    if command_type_name == UNDO_COMMAND_TYPE:
        return UNDO_LAST_CHANGE
    if command_type_name == "replace_exercise":
        return ReplaceExerciseCommand(
            day=payload["day"],
            exercise=payload["exercise"],
            replacement=payload["replacement"],
            sets=payload.get("sets"),
            reps=payload.get("reps"),
        )
    if command_type_name == "add_exercise":
        return AddExerciseCommand(
            day=payload["day"],
            exercise=payload["exercise"],
            sets=payload["sets"],
            reps=payload["reps"],
        )
    if command_type_name == "remove_exercise":
        return RemoveExerciseCommand(
            day=payload["day"], exercise=payload["exercise"])
    if command_type_name == "update_exercise_prescription":
        return UpdateExercisePrescriptionCommand(
            day=payload["day"],
            exercise=payload["exercise"],
            sets=payload.get("sets"),
            reps=payload.get("reps"),
        )
    if command_type_name == "move_training_day":
        return MoveTrainingDayCommand(
            day=payload["day"], target_day=payload["target_day"])
    raise TypeError("unsupported stored command type")


def preview_command(user_id, command):
    """Validate ``command`` against the current plan without persisting.

    Returns ``(plan_row, document, changed)``. Domain errors propagate.
    """
    plan = get_active_plan(user_id)
    if plan is None:
        from app.services.plan_mutation.errors import PlanNotFound
        raise PlanNotFound("no active training plan")
    if not plan.lineage_id:
        from app.services.plan_mutation.errors import PlanStateConflict
        raise PlanStateConflict("this plan has no history identity")
    document, _program = parse_plan_document(plan.plan_data)
    _new_document, changed = apply_command(document, command)
    return plan, document, changed


@dataclass(frozen=True)
class PlanBinding:
    lineage_id: str
    mutation_version: int
    snapshot_fingerprint: str


def plan_binding(plan):
    return PlanBinding(
        lineage_id=plan.lineage_id,
        mutation_version=plan.mutation_version,
        snapshot_fingerprint=snapshot_fingerprint(plan.plan_data),
    )


def binding_matches(proposal, plan):
    return (
        plan is not None
        and plan.lineage_id == proposal.plan_lineage_id
        and plan.mutation_version == proposal.base_mutation_version
        and snapshot_fingerprint(plan.plan_data)
        == proposal.base_snapshot_fingerprint
    )


def _program_of(document):
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        return document.get("program")
    return None


def session_impact_facts(user_id, command):
    """Whether applying ``command`` would change an ACTIVE session's fingerprint.

    Reads canonical session + fingerprint authorities. Never writes session
    state. An evaluation failure is ``impact_undeterminable``, never a guess.
    """
    from app.services.coach_plan_policy import ImpactFacts, UNDO_LAST_CHANGE
    from app.services.workout_session.queries import (
        fingerprint_for_program,
        get_active_session,
    )

    try:
        session = get_active_session(user_id)
        if session is None:
            return ImpactFacts(touches_active_session=False)
        slot = session.weekday_slot
        plan = get_active_plan(user_id)
        if plan is None:
            return ImpactFacts(touches_active_session=False)
        document, program = parse_plan_document(plan.plan_data)
        before = fingerprint_for_program(program, slot)
        if command is UNDO_LAST_CHANGE:
            from app.services.plan_mutation.journal import latest_reversible
            record = latest_reversible(user_id, plan.lineage_id)
            if record is None:
                return ImpactFacts(touches_active_session=False)
            _before_doc, before_program = parse_plan_document(
                record.before_snapshot)
            after = fingerprint_for_program(before_program, slot)
        else:
            new_document, _changed = apply_command(document, command)
            after = fingerprint_for_program(_program_of(new_document), slot)
        return ImpactFacts(touches_active_session=before != after)
    except Exception:
        return ImpactFacts(
            touches_active_session=False, impact_undeterminable=True)
