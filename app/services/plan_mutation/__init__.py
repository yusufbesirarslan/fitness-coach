"""Canonical training-plan mutation boundary — Adaptive Coaching Sprint 1.

The one server-authoritative place a user's persisted training plan may be
changed in a *targeted* way. Everything a future consumer needs is here; nothing
below this package is part of the contract.

    consumer (AI / API / web)
            ↓
    plan mutation service      ← this package
            ↓
    canonical training domain / persistence

The arrow never runs the other way: this package does not import the AI Coach,
any provider SDK, prompt construction, a blueprint, or UI state, and
tests/test_plan_mutation_architecture.py enforces that.

The journal stores internal evidence. Consumers receive only the typed
mutation result.

Sprint 1 PR2 adds three things to the contract: every call carries a
``MutationContext`` (durable operation key + actor + optional bounded reason),
every result carries a monotonic ``plan_version`` and an opaque mutation
identity, and ``undo_last_change`` reverses the newest reversible change by
restoring exact stored bytes.
"""
from .commands import (
    PLAN_MUTATION_COMMANDS,
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)
from .context import (
    ACTOR_AI_COACH,
    ACTOR_SYSTEM,
    ACTOR_USER,
    MAX_REASON_CHARS,
    PLAN_MUTATION_ACTORS,
    MutationContext,
)
from .errors import (
    ActiveWorkoutConflict,
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseAlreadyPresent,
    ExerciseNotFound,
    IdempotencyConflict,
    InvalidMutation,
    InvalidPrescription,
    PlanMutationError,
    PlanNotFound,
    PlanNotMutable,
    PlanStateConflict,
    UndoConflict,
    UndoUnavailable,
)
from .journal import OUTCOME_APPLIED, OUTCOME_NO_OP
from .service import PlanEditRevision, PlanMutationResult, apply_plan_mutation, undo_last_change

__all__ = [
    "ACTOR_AI_COACH",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "MAX_REASON_CHARS",
    "OUTCOME_APPLIED",
    "OUTCOME_NO_OP",
    "PLAN_MUTATION_ACTORS",
    "PLAN_MUTATION_COMMANDS",
    "ActiveWorkoutConflict",
    "AddExerciseCommand",
    "AmbiguousExerciseTarget",
    "DayNotFound",
    "ExerciseAlreadyPresent",
    "ExerciseNotFound",
    "IdempotencyConflict",
    "InvalidMutation",
    "InvalidPrescription",
    "MoveTrainingDayCommand",
    "MutationContext",
    "PlanMutationError",
    "PlanEditRevision",
    "PlanMutationResult",
    "PlanNotFound",
    "PlanNotMutable",
    "PlanStateConflict",
    "RemoveExerciseCommand",
    "ReplaceExerciseCommand",
    "UndoConflict",
    "UndoUnavailable",
    "UpdateExercisePrescriptionCommand",
    "apply_plan_mutation",
    "undo_last_change",
]
