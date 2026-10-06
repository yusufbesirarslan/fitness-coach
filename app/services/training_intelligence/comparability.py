"""Explicit comparability for TI-00 comparator v1.

Two observations are never compared merely because the exercise IDs match.
Every comparison is either ``paired`` or refused with one bounded TI-00 missing
code. Pure: catalog lookups are in-memory, nothing reads the database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.exercise_catalog import ExerciseResolutionError, resolve_exercise

from .models import (
    INSUFFICIENT_PAIRS,
    MIN_PERFORMANCE_PAIRS,
    MISSING_PRESCRIPTION,
    PLAN_CHANGED,
    STATE_INSUFFICIENT,
    STATE_NOT_COMPARABLE,
    UNKNOWN_LOAD_EQUIPMENT,
    UNSUPPORTED_MOVEMENTS,
    Pair,
    RecentPerformance,
    SessionExecution,
)

PAIRED = "paired"


@dataclass(frozen=True)
class Pairing:
    comparison: str              # PAIRED | STATE_INSUFFICIENT | STATE_NOT_COMPARABLE
    reason: Optional[str]        # TI-00 missing code when not paired
    previous: Optional[SessionExecution]
    pairs: tuple = ()            # Pair, ascending set index


def measurement_supported(exercise_id: str) -> bool:
    """Whether a logged kilogram value has a defined meaning for this exercise.

    Unknown, inactive or malformed catalog identities, cardio/mobility
    activities and bodyweight/assisted/band equipment have no comparable
    load convention. No synthetic identity is ever derived from a name.
    """
    try:
        definition = resolve_exercise(exercise_id=exercise_id)
    except ExerciseResolutionError:
        return False
    return (definition.movement not in UNSUPPORTED_MOVEMENTS
            and not definition.equipment & UNKNOWN_LOAD_EQUIPMENT)


def prescription_entry(session: SessionExecution, exercise_id: str) -> Optional[dict]:
    return session.prescription.get(exercise_id) if session.prescription else None


def context_reason(current: SessionExecution, previous: SessionExecution,
                   exercise_id: str) -> Optional[str]:
    """``None`` when the immutable start contexts are comparable.

    Same weekday slot, same non-null plan lineage and mutation version from the
    start snapshot, and an identical structured prescription entry. The user's
    current plan is never consulted.
    """
    entry = prescription_entry(previous, exercise_id)
    if entry is None:
        return MISSING_PRESCRIPTION
    if (current.plan_lineage is None or current.plan_version is None
            or (current.plan_lineage, current.plan_version)
            != (previous.plan_lineage, previous.plan_version)):
        return PLAN_CHANGED
    if current.weekday_slot is None or current.weekday_slot != previous.weekday_slot:
        return INSUFFICIENT_PAIRS
    if entry != prescription_entry(current, exercise_id):
        return PLAN_CHANGED
    return None


def eligible_sets(session: SessionExecution, exercise_id: str) -> dict:
    """Completed sets with observed reps AND load, below the prescribed count.

    Sets beyond the captured prescription are never compared: execution has no
    working/warm-up role, so extra sets are not labelled as working sets.
    """
    entry = prescription_entry(session, exercise_id)
    exercise = session.exercise(exercise_id)
    if entry is None or exercise is None:
        return {}
    return {item.index: item for item in exercise.completed
            if item.reps is not None and item.weight_kg is not None
            and item.index < entry["sets"]}


def pair_exercise(current: SessionExecution, recent: RecentPerformance,
                  exercise_id: str) -> Pairing:
    """Pair the current session with the immediately previous comparable one."""
    if not measurement_supported(exercise_id):
        return Pairing(STATE_INSUFFICIENT, INSUFFICIENT_PAIRS, None)
    if prescription_entry(current, exercise_id) is None:
        return Pairing(STATE_INSUFFICIENT, MISSING_PRESCRIPTION, None)
    previous = next((occurrence.session for occurrence in recent.occurrences
                     if context_reason(current, occurrence.session, exercise_id) is None), None)
    if previous is None:
        if not recent.occurrences:
            return Pairing(STATE_INSUFFICIENT, INSUFFICIENT_PAIRS, None)
        reason = context_reason(current, recent.occurrences[0].session, exercise_id)
        state = STATE_NOT_COMPARABLE if reason == PLAN_CHANGED else STATE_INSUFFICIENT
        return Pairing(state, reason, None)

    current_sets = eligible_sets(current, exercise_id)
    previous_sets = eligible_sets(previous, exercise_id)
    if min(len(current_sets), len(previous_sets)) < MIN_PERFORMANCE_PAIRS:
        return Pairing(STATE_INSUFFICIENT, INSUFFICIENT_PAIRS, previous)
    if set(current_sets) != set(previous_sets):
        return Pairing(STATE_NOT_COMPARABLE, INSUFFICIENT_PAIRS, previous)
    pairs = tuple(Pair(index, previous_sets[index], current_sets[index])
                  for index in sorted(current_sets))
    return Pairing(PAIRED, None, previous, pairs)
