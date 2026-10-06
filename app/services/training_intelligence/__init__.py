"""TI-03 deterministic training facts and diagnostics (read-only).

Layering (TI-00 §14): ``queries`` (the only impure module: two bounded,
owner-scoped reads) → ``facts`` (pure facts over canonical completed
execution) → ``comparability`` (explicit pairing or a bounded refusal) →
``diagnostics`` (fixed tokens per exercise) → ``policy`` (frozen primary
selection, at most one lever) → ``projection`` (the one public shape).

Authority boundaries, each pinned by an architecture test:

* evidence is the final acknowledged checkpoint of an owned COMPLETED
  ``WorkoutSession`` plus its anchor-verified TI-01A context and immutable
  start prescription -- never ACTIVE/ABANDONED drafts, the current plan,
  ``WorkoutLog`` markers or name-only legacy rows, Pump Check, notes or any
  provider output;
* nothing here writes: no add, flush, commit or plan/session mutation;
* the same stored data always yields the same payload.
"""
from __future__ import annotations

from app.models import WORKOUT_SESSION_COMPLETED

from . import queries
from .diagnostics import assess_exercise
from .facts import parse_session
from .metrics import record_insight_event
from .models import (
    KIND_INSUFFICIENT,
    MISSING_EXECUTION,
    NO_COMPLETED_SETS,
    SESSION_NOT_COMPLETED,
    STATE_AVAILABLE,
    STATE_INSUFFICIENT,
    Selection,
)
from .policy import select_primary
from .projection import ProjectionInvariantError, insight_payload

__all__ = ["build_training_insight", "record_insight_event", "ProjectionInvariantError"]

_EVENTS = {
    STATE_AVAILABLE: "insight_generated",
    STATE_INSUFFICIENT: "insight_insufficient",
}


def _insufficient(code: str) -> Selection:
    return Selection(STATE_INSUFFICIENT, KIND_INSUFFICIENT, None, (), (code,), None)


def _select(user_id: int, stored) -> Selection:
    if stored.status != WORKOUT_SESSION_COMPLETED:
        return _insufficient(SESSION_NOT_COMPLETED)
    current = parse_session(stored)
    if not current.observed:
        return _insufficient(MISSING_EXECUTION)
    exercises = [exercise for exercise in current.exercises if exercise.completed]
    if not exercises:
        return _insufficient(NO_COMPLETED_SETS)
    rows, truncated = queries.load_history(user_id, current.workout_date)
    history = tuple(parse_session(row) for row in rows if row.ref != current.ref)
    excluded = sum(1 for session in history if not session.observed)
    if excluded:
        record_insight_event("history_row_excluded")
    return select_primary(
        assess_exercise(current, history, exercise.exercise_id, position, truncated=truncated)
        for position, exercise in enumerate(exercises)
    )


def build_training_insight(user_id: int, session_ref) -> dict:
    """The owner's deterministic Training Insight for one session.

    Raises ``SessionNotFound`` for an absent or foreign reference. Any other
    exception is a read failure for the transport to report as retryable --
    never an empty or "insufficient history" answer.
    """
    stored = queries.load_target(user_id, session_ref)
    selection = _select(user_id, stored)
    payload = insight_payload(stored.ref, stored.checkpoint_revision, selection)
    record_insight_event(_EVENTS.get(selection.state, "insight_not_comparable"))
    return payload
