"""Pure row classification, fact extraction, state derivation and invariants.

The anchor day is an argument; this module never reads a clock, the database or
Flask. Checkpoint validity is decided ONLY by the canonical write-path
validator, ``parse_stored_exercises(load_snapshot(...))`` -- there is no second
interpretation of a stored checkpoint, and a checkpoint it refuses is
unavailable as a whole (no entry is ever salvaged from it).

Exception discipline (TD-01 C-1): exactly two handlers exist. ``InvalidSessionRequest``
from canonical re-validation becomes ``checkpoint_invalid``; ``ValueError`` from
``date.fromisoformat`` becomes ``session_invalid``. Every other exception
propagates unchanged to the caller (the invariant_failed class).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from app.services.workout_session.checkpoint import load_snapshot, parse_stored_exercises
from app.services.workout_session.errors import InvalidSessionRequest
from app.timeutil import app_date_of

from .models import (
    EXCLUDED_CROSS_DATE,
    EXCLUDED_MISSING_COMPLETED_AT,
    HISTORY_DAYS,
    MAX_OCCURRENCES,
    REASON_CHECKPOINT_INVALID,
    REASON_CHECKPOINT_MISSING,
    REASON_SESSION_INVALID,
    ROW_ELIGIBLE,
    ROW_EXCLUDED,
    ROW_UNAVAILABLE,
    STATE_AVAILABLE,
    STATE_AVAILABLE_WITH_GAPS,
    STATE_NO_HISTORY,
    STATE_UNDETERMINED,
    STATES,
    UNAVAILABLE_REASONS,
    Coverage,
    ExerciseHistory,
    Occurrence,
    PerformedSet,
    ProjectionInvariantError,
    RowClassification,
    UnavailableSession,
)

_MAX_REPS = 1_000
_MAX_WEIGHT_KG = 1_000.0
_MAX_SET_INDEX = 19


def window(anchor: date) -> tuple:
    """``(start, end)`` of the semantic history window, inclusive."""
    return anchor - timedelta(days=HISTORY_DAYS), anchor


def _parse_workout_date(raw) -> Optional[date]:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _revalidate(checkpoint_data) -> Optional[list]:
    """Canonical exercises, or ``None`` when the canonical validator refuses
    the checkpoint. A valid checkpoint with no exercises is ``[]``."""
    try:
        return parse_stored_exercises(load_snapshot(checkpoint_data))
    except InvalidSessionRequest:
        return None


def classify(row) -> RowClassification:
    """Classify one candidate row; the first matching rule wins (TD-01 T3)."""
    day = _parse_workout_date(row.workout_date)
    if day is None:
        return RowClassification(ROW_UNAVAILABLE, REASON_SESSION_INVALID, None)
    if row.completed_at is None:
        return RowClassification(ROW_EXCLUDED, EXCLUDED_MISSING_COMPLETED_AT, day)
    if app_date_of(row.completed_at) != day:
        return RowClassification(ROW_EXCLUDED, EXCLUDED_CROSS_DATE, day)
    if row.checkpoint_data is None and row.checkpoint_revision == 0:
        return RowClassification(ROW_UNAVAILABLE, REASON_CHECKPOINT_MISSING, day)
    exercises = _revalidate(row.checkpoint_data)
    if exercises is None:
        return RowClassification(ROW_UNAVAILABLE, REASON_CHECKPOINT_INVALID, day)
    return RowClassification(ROW_ELIGIBLE, None, day, tuple(exercises))


def performed_sets(exercises, exercise_id: str) -> tuple:
    """The ``completed is True`` sets of ``exercise_id``, ascending index.

    Values are copied exactly as the canonical parser returned them: null stays
    null and zero stays zero. Nothing else about the exercise is read.
    """
    for entry in exercises:
        if entry["exercise_id"] == exercise_id:
            return tuple(PerformedSet(index=item["index"], reps=item["reps"],
                                      weight_kg=item["weight_kg"])
                         for item in entry["sets"] if item["completed"] is True)
    return ()


def derive_state(occurrence_count: int, coverage: Coverage) -> str:
    if occurrence_count:
        return STATE_AVAILABLE if coverage.complete else STATE_AVAILABLE_WITH_GAPS
    return STATE_NO_HISTORY if coverage.complete else STATE_UNDETERMINED


def select_history(exercise_id: str, anchor: date, rows, scan_limit_reached: bool) -> ExerciseHistory:
    """Assemble the projection from candidate rows in SQL order.

    Every scanned row is classified, including rows after the last returned
    occurrence, so a gap anywhere in the scanned window degrades the state.
    """
    occurrences = []
    unavailable = []
    cross_date = missing_completed_at = 0
    occurrence_limit_reached = False
    for row in rows:
        verdict = classify(row)
        if verdict.row_class == ROW_UNAVAILABLE:
            unavailable.append(UnavailableSession(
                workout_date=verdict.workout_date, completed_at=row.completed_at,
                reason=verdict.reason))
        elif verdict.row_class == ROW_EXCLUDED:
            if verdict.reason == EXCLUDED_CROSS_DATE:
                cross_date += 1
            else:
                missing_completed_at += 1
        else:
            sets = performed_sets(verdict.exercises, exercise_id)
            if not sets:
                continue
            if len(occurrences) < MAX_OCCURRENCES:
                occurrences.append(Occurrence(
                    session_ref=row.public_id, checkpoint_revision=row.checkpoint_revision,
                    workout_date=verdict.workout_date, completed_at=row.completed_at,
                    sets=sets))
            else:
                occurrence_limit_reached = True
    coverage = Coverage(
        scanned_sessions=len(rows), excluded_cross_date=cross_date,
        excluded_missing_completed_at=missing_completed_at, unavailable=tuple(unavailable),
        scan_limit_reached=scan_limit_reached, occurrence_limit_reached=occurrence_limit_reached)
    start, end = window(anchor)
    return ExerciseHistory(
        exercise_id=exercise_id, window_start=start, window_end=end,
        state=derive_state(len(occurrences), coverage),
        occurrences=tuple(occurrences), coverage=coverage)


# ── Internal invariant check (TD-01 §Internal Projection) ────────────────────
def _require(condition, message: str) -> None:
    if not condition:
        raise ProjectionInvariantError(message)


def _check_set(item) -> None:
    _require(isinstance(item, PerformedSet), "set is not a PerformedSet")
    _require(type(item.index) is int and 0 <= item.index <= _MAX_SET_INDEX,
             "set index out of range")
    _require(item.reps is None or (type(item.reps) is int and 0 <= item.reps <= _MAX_REPS),
             "reps out of range")
    _require(item.weight_kg is None or (
        type(item.weight_kg) is float and math.isfinite(item.weight_kg)
        and 0.0 <= item.weight_kg <= _MAX_WEIGHT_KG), "weight_kg out of range")


def _check_occurrence(occurrence) -> None:
    _require(isinstance(occurrence, Occurrence), "occurrence is not an Occurrence")
    _require(isinstance(occurrence.session_ref, str) and occurrence.session_ref,
             "occurrence has no session reference")
    _require(occurrence.completed_at is not None and occurrence.workout_date is not None,
             "occurrence is undated")
    _require(isinstance(occurrence.sets, tuple) and len(occurrence.sets) >= 1,
             "occurrence has no performed set")
    for item in occurrence.sets:
        _check_set(item)
    indices = [item.index for item in occurrence.sets]
    _require(all(a < b for a, b in zip(indices, indices[1:])),
             "sets are not strictly ascending by index")


def _check_marker(marker) -> None:
    _require(isinstance(marker, UnavailableSession), "marker is not an UnavailableSession")
    _require(marker.reason in UNAVAILABLE_REASONS, "unknown unavailable reason")
    if marker.reason == REASON_SESSION_INVALID:
        _require(marker.workout_date is None, "session_invalid marker carries a date")
    else:
        _require(marker.workout_date is not None, "dated marker has no workout_date")
        _require(marker.completed_at is not None, "dated marker has no completed_at")


def check_invariants(history: ExerciseHistory) -> None:
    """Raise ``ProjectionInvariantError`` unless ``history`` satisfies TD-01."""
    coverage = history.coverage
    _require(history.state in STATES, "unknown state")
    _require(len(history.occurrences) <= MAX_OCCURRENCES, "too many occurrences")
    for marker in coverage.unavailable:
        _check_marker(marker)
    _require(history.state == derive_state(len(history.occurrences), coverage),
             "state does not match occurrences and coverage")
    if history.state == STATE_NO_HISTORY:
        _require(not coverage.unavailable and not coverage.scan_limit_reached,
                 "no_history with incomplete coverage")
    for occurrence in history.occurrences:
        _check_occurrence(occurrence)
    keys = [(item.completed_at, item.session_ref) for item in history.occurrences]
    _require(all(a > b for a, b in zip(keys, keys[1:])),
             "occurrences are not strictly newest first")
