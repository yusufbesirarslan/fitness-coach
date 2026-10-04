"""The closed, request-scoped set of exercises a provider may choose from.

PR B (server-validated catalog identity). Exercise identity is server-owned:
the server computes this set ONCE per generation request from the accepted
``ExerciseContext``, the prompt shows it to the provider as opaque
``exercise_id`` values, and generation-time canonicalization
(``exercise_resolution.canonicalize_generated_exercises``) checks every
provider-chosen ID against the SAME object. The prompt is advisory; this set
is what the server enforces.

Built only from active, context-compatible catalog entries
(``exercise_catalog.compatible_exercises``). Carries no aliases and no
equipment/movement metadata — only what the provider needs to choose.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.exercise_catalog import (
    ExerciseContext,
    compatible_exercises,
    load_exercise_catalog,
)


@dataclass(frozen=True)
class ExerciseChoice:
    exercise_id: str
    canonical_name: str


@dataclass(frozen=True)
class CompatibleExerciseChoices:
    """One request's closed choice set, in deterministic prompt order."""

    context: ExerciseContext
    catalog_version: int
    choices: tuple[ExerciseChoice, ...]
    exercise_ids: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(
            self, "exercise_ids",
            frozenset(choice.exercise_id for choice in self.choices))

    def __contains__(self, exercise_id: object) -> bool:
        return isinstance(exercise_id, str) and exercise_id in self.exercise_ids


def compatible_exercise_choices(context: ExerciseContext) -> CompatibleExerciseChoices:
    """The closed choice set for ``context``, sorted by canonical name."""
    catalog = load_exercise_catalog()
    entries = sorted(compatible_exercises(context), key=lambda e: e.canonical_name)
    return CompatibleExerciseChoices(
        context=context,
        catalog_version=catalog.version,
        choices=tuple(
            ExerciseChoice(exercise_id=e.exercise_id, canonical_name=e.canonical_name)
            for e in entries
        ),
    )
