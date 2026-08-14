"""Canonical training-plan mutation boundary — Adaptive Coaching Sprint 1 PR1.

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

PR1 has **no** runtime surface — no route, no AI tool, no feature flag. It is a
domain foundation that later work exposes. What that later work owns is listed in
docs/ADAPTIVE_COACHING.md; the short version is that an AI may *request* a plan
change, but never owns plan persistence.
"""
from .commands import (
    PLAN_MUTATION_COMMANDS,
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)
from .errors import (
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseNotFound,
    InvalidMutation,
    InvalidPrescription,
    PlanMutationError,
    PlanNotFound,
    PlanNotMutable,
)
from .service import PlanMutationResult, apply_plan_mutation

__all__ = [
    "PLAN_MUTATION_COMMANDS",
    "AddExerciseCommand",
    "AmbiguousExerciseTarget",
    "DayNotFound",
    "ExerciseNotFound",
    "InvalidMutation",
    "InvalidPrescription",
    "MoveTrainingDayCommand",
    "PlanMutationError",
    "PlanMutationResult",
    "PlanNotFound",
    "PlanNotMutable",
    "RemoveExerciseCommand",
    "ReplaceExerciseCommand",
    "UpdateExercisePrescriptionCommand",
    "apply_plan_mutation",
]
