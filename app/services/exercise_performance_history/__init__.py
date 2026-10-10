"""TD-01 PR1: canonical per-exercise historical performance projection.

One deterministic, owner-scoped, bounded, read-only projection of ADR 0002 D1a
facts for one catalog exercise: ``completed: true`` set entries in the final
checkpoint of an owned COMPLETED ``WorkoutSession``. No other source is read,
merged or used as a fallback.

Layering: ``queries`` (one SELECT) -> pure ``selection`` (classification, fact
extraction, state, invariants) over ``models`` (bounds, tokens, value objects).

PR1 has ZERO production callers (TD-01 T11, guard H-A8): no route, flag, client,
Coach or Today consumer imports this package. A later, separately approved
slice must change that guard explicitly.

Failure classes are distinct exception types and are never converted into a
state: ``SQLAlchemyError`` (read_failed), ``ProjectionInvariantError`` or any
other unexpected exception (invariant_failed), ``CatalogConfigurationError``
(catalog_unavailable), ``ExerciseIdentityInvalid`` / ``ExerciseUnknown``
(invalid input, raised before any history read).
"""
from __future__ import annotations

from app.services.exercise_catalog import resolve_historical_exercise
from app.timeutil import app_today

from . import queries, selection
from .models import (
    STATES,
    Coverage,
    ExerciseHistory,
    Occurrence,
    PerformedSet,
    ProjectionInvariantError,
    UnavailableSession,
)

__all__ = [
    "STATES",
    "Coverage",
    "ExerciseHistory",
    "Occurrence",
    "PerformedSet",
    "ProjectionInvariantError",
    "UnavailableSession",
    "build_exercise_history",
]


def build_exercise_history(user_id: int, exercise_id: str) -> ExerciseHistory:
    """The owner's canonical performed-set history for one exercise.

    ``user_id`` must come from the caller's authenticated principal; it is the
    only owner input. The exercise identity is validated before any history
    read, and the anchor day is computed exactly once.
    """
    resolve_historical_exercise(exercise_id)
    anchor = app_today()
    rows, scan_limit_reached = queries.load_candidates(user_id, anchor)
    history = selection.select_history(exercise_id, anchor, rows, scan_limit_reached)
    selection.check_invariants(history)
    return history
