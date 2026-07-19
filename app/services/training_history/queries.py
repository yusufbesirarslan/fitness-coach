"""Explicit ORM data access for the training-history foundation.

The single place that reads ``WorkoutLog`` history for progression work. Impure
(needs an app/DB context); pure calculations live in ``analysis.py``. Windowing
goes through ``app.timeutil.utc_day_bounds`` because ``WorkoutLog.created_at`` is
naive UTC with no day-key column — Istanbul-day boundaries must be derived, never
compared raw.
"""
from datetime import date

from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.timeutil import app_date_of, utc_day_bounds

from .models import WorkoutEntry


def is_completion_marker(exercise_name: str) -> bool:
    """True when a row is the synthetic UI "session completed" marker (not a real
    exercise). The one canonical predicate — callers must not re-hardcode the
    constant."""
    return exercise_name == WORKOUT_COMPLETION_MARKER


def fetch_workout_entries(
    user_id: int,
    start_day: date,
    end_day: date,
    *,
    include_markers: bool = False,
) -> list[WorkoutEntry]:
    """Return normalized ``WorkoutEntry`` rows for ``user_id`` whose Istanbul day
    falls in the inclusive ``[start_day, end_day]`` range, oldest first.

    The window is ``[utc_day_bounds(start_day).start, utc_day_bounds(end_day).end)``
    so it matches how every other reader compares ``created_at``. Completion-marker
    rows are excluded unless ``include_markers=True`` (callers that need the raw
    "a session happened" count pass True and filter with ``entry.is_marker``).
    """
    start, _ = utc_day_bounds(start_day)
    _, end = utc_day_bounds(end_day)

    query = WorkoutLog.query.filter(
        WorkoutLog.user_id == user_id,
        WorkoutLog.created_at >= start,
        WorkoutLog.created_at < end,
    )
    if not include_markers:
        query = query.filter(WorkoutLog.exercise_name != WORKOUT_COMPLETION_MARKER)

    rows = query.order_by(WorkoutLog.created_at.asc()).all()
    return [
        WorkoutEntry(
            exercise_name=row.exercise_name,
            sets=row.sets,
            reps=row.reps,
            weight_kg=row.weight_kg,
            volume=row.volume,
            performed_on=app_date_of(row.created_at),
            created_at=row.created_at,
            is_marker=is_completion_marker(row.exercise_name),
        )
        for row in rows
    ]
