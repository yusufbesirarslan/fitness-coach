"""Deterministic impact decision for one typed plan-mutation request.

The caller gathers facts (including whether an ACTIVE workout session would
become stale). This module never inspects a session, a plan row, or a provider
payload. Unknown impact refuses; it never silently becomes APPLY_NOW.
"""
from dataclasses import dataclass

from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)


APPLY_NOW = "APPLY_NOW"
REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
REFUSE_UNSUPPORTED = "REFUSE_UNSUPPORTED"

REASON_REMOVE_EXERCISE = "REMOVE_EXERCISE"
REASON_MOVE_TRAINING_DAY = "MOVE_TRAINING_DAY"
REASON_ACTIVE_SESSION_IMPACT = "ACTIVE_SESSION_IMPACT"
REASON_UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
REASON_IMPACT_UNDETERMINABLE = "IMPACT_UNDETERMINABLE"

_APPLY_NOW_COMMANDS = (
    ReplaceExerciseCommand,
    AddExerciseCommand,
    UpdateExercisePrescriptionCommand,
)


class UndoLastChange:
    """Sentinel for the dedicated undo tool — not a PR1 command object."""


UNDO_LAST_CHANGE = UndoLastChange()


@dataclass(frozen=True)
class ImpactFacts:
    """Facts the caller already proved from canonical authorities.

    ``touches_active_session`` is True only when applying the command would
    change the exercise-name fingerprint (or schedule slot) of the user's
    current ACTIVE WorkoutSession. The fingerprint algorithm lives in
    ``workout_session``; this package only consumes the boolean.
    """
    touches_active_session: bool
    impact_undeterminable: bool = False


@dataclass(frozen=True)
class ImpactDecision:
    verdict: str
    reason_codes: tuple


def decide_plan_mutation_impact(command, facts: ImpactFacts) -> ImpactDecision:
    """Return the server-owned decision for ``command`` given ``facts``."""
    if not isinstance(facts, ImpactFacts):
        return ImpactDecision(REFUSE_UNSUPPORTED, (REASON_IMPACT_UNDETERMINABLE,))
    if facts.impact_undeterminable:
        return ImpactDecision(REFUSE_UNSUPPORTED, (REASON_IMPACT_UNDETERMINABLE,))

    reasons = []
    if isinstance(command, RemoveExerciseCommand):
        reasons.append(REASON_REMOVE_EXERCISE)
    elif isinstance(command, MoveTrainingDayCommand):
        reasons.append(REASON_MOVE_TRAINING_DAY)
    elif isinstance(command, _APPLY_NOW_COMMANDS) or command is UNDO_LAST_CHANGE:
        pass
    else:
        return ImpactDecision(REFUSE_UNSUPPORTED, (REASON_UNKNOWN_COMMAND,))

    if facts.touches_active_session:
        reasons.append(REASON_ACTIVE_SESSION_IMPACT)

    if reasons:
        return ImpactDecision(REQUIRE_CONFIRMATION, tuple(reasons))
    return ImpactDecision(APPLY_NOW, ())
