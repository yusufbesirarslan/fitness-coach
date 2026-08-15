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

The boundary still has **no** runtime surface — no route, no AI tool, no feature
flag, and Sprint 1 PR2 deliberately adds none. The durable history it now keeps
is internal evidence; there is no history endpoint, no mobile serialization and
no AI-facing serialization of the journal. What later work owns is listed in
docs/ADAPTIVE_COACHING.md; the short version is that an AI may *request* a plan
change, but never owns plan persistence.

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
    AmbiguousExerciseTarget,
    DayNotFound,
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
from .service import PlanMutationResult, apply_plan_mutation, undo_last_change

__all__ = [
    "ACTOR_AI_COACH",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "MAX_REASON_CHARS",
    "OUTCOME_APPLIED",
    "OUTCOME_NO_OP",
    "PLAN_MUTATION_ACTORS",
    "PLAN_MUTATION_COMMANDS",
    "AddExerciseCommand",
    "AmbiguousExerciseTarget",
    "DayNotFound",
    "ExerciseNotFound",
    "IdempotencyConflict",
    "InvalidMutation",
    "InvalidPrescription",
    "MoveTrainingDayCommand",
    "MutationContext",
    "PlanMutationError",
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
