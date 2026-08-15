"""The typed mutation contract (brief §9).

Frozen value objects, one per supported operation. This *is* the contract: the
service accepts nothing else. There is deliberately no generic "patch this
field", no dict-of-changes, and no JSON Patch — an arbitrary mutation surface is
exactly what a future AI consumer must not be handed (brief §8).

Every command names its target explicitly. No command carries a ``user_id``:
ownership comes from the authenticated server context and is never accepted from
a caller or a model (brief §11).
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReplaceExerciseCommand:
    """Replace one named exercise in one named day with one replacement.

    ``sets``/``reps`` are optional overrides. When omitted the replacement
    *inherits the replaced slot's prescription* — including fields this PR does
    not model — because swapping an exercise for an equivalent one should not
    silently reset its programming. The slot's position is preserved.
    """
    day: str
    exercise: str
    replacement: str
    sets: Optional[int] = None
    reps: Optional[str] = None


@dataclass(frozen=True)
class AddExerciseCommand:
    """Append one exercise to one named day with an explicit prescription.

    ``sets`` and ``reps`` are both required. The canonical defaults that exist in
    the repository (``set``→1, ``tekrar``→"8-12") belong to the *generator*
    filling gaps in LLM output; reusing them here would let a caller add an
    exercise without saying how it should be trained, and the invented numbers
    would be indistinguishable from prescribed ones (brief §9B).
    """
    day: str
    exercise: str
    sets: Optional[int] = None
    reps: Optional[str] = None


@dataclass(frozen=True)
class RemoveExerciseCommand:
    """Remove exactly one identified exercise from exactly one named day."""
    day: str
    exercise: str


@dataclass(frozen=True)
class UpdateExercisePrescriptionCommand:
    """Update the safe prescription fields of one exercise in one day.

    Scoped to ``sets`` and ``reps``: those are the only prescription fields with
    canonical persistence *and* canonical validation bounds today. ``dinlenme``
    (rest) and ``not`` are persisted but only length-bounded, and RIR/RPE do not
    exist in the plan shape at all — so neither is in PR1's contract (brief §9D).

    At least one field must be supplied; a command that changes nothing is
    refused rather than treated as a successful no-op, because an empty command
    signals a caller bug, not a satisfied desired state.
    """
    day: str
    exercise: str
    sets: Optional[int] = None
    reps: Optional[str] = None


@dataclass(frozen=True)
class MoveTrainingDayCommand:
    """Move a planned day's content to another weekday slot.

    Implemented because discovery confirmed both preconditions the brief sets for
    it (§9E): day identity is stable (``gun`` is a canonical Turkish weekday,
    validated unique across exactly seven days), and the plan schedule is fully
    separate from completed history (``WorkoutLog`` snapshots its own columns and
    derives nothing from ``plan_data``).

    The two slots exchange their *content*; each entry keeps its own ``gun``
    label, so the weekday calendar is never renamed or reordered and a completed
    workout's timestamps are untouched.
    """
    day: str
    target_day: str


#: Every command type the boundary accepts. Anything else is an invalid
#: mutation — checked before a single row is read.
PLAN_MUTATION_COMMANDS = (
    ReplaceExerciseCommand,
    AddExerciseCommand,
    RemoveExerciseCommand,
    UpdateExercisePrescriptionCommand,
    MoveTrainingDayCommand,
)
