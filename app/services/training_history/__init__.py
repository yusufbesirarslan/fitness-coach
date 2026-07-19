"""Adaptive Training Engine — training-history foundation (Sprint 6 PR1).

The single, deterministic, ORM-based source of truth for reading a user's workout
history and computing progression baselines. Future PRs (progression analysis,
plateau / deload detection, recovery scoring, adaptive program updates) build on
this instead of re-deriving windows and marker exclusion inline.

Layering:
- ``models``   — frozen value objects (no logic).
- ``queries``  — explicit ``WorkoutLog`` reads (needs app context).
- ``analysis`` — pure deterministic calculations (fixture-free).

Reuses ``WORKOUT_COMPLETION_MARKER`` and ``app.timeutil`` — see
``docs/TRAINING_HISTORY.md``.
"""
from datetime import date

from app.timeutil import app_today

from .analysis import (
    bucket_by_week,
    count_sessions,
    estimated_1rm,
    session_days,
    total_sets,
    total_volume,
    volume_trend,
    weekly_windows,
)
from .models import TrainingHistorySummary, WeeklyVolume, WorkoutEntry
from .queries import fetch_workout_entries, is_completion_marker

__all__ = [
    "WorkoutEntry",
    "WeeklyVolume",
    "TrainingHistorySummary",
    "fetch_workout_entries",
    "is_completion_marker",
    "total_volume",
    "total_sets",
    "session_days",
    "count_sessions",
    "weekly_windows",
    "bucket_by_week",
    "volume_trend",
    "estimated_1rm",
    "build_training_history_summary",
]


def build_training_history_summary(
    user_id: int, weeks: int = 4, *, end_day: date | None = None
) -> TrainingHistorySummary:
    """Deterministic roll-up of ``user_id``'s last ``weeks`` weeks of training.

    ``end_day`` defaults to today (Istanbul); pass an explicit day for hermetic
    tests. Empty history yields ``has_data=False`` with zeroed totals.
    """
    if weeks <= 0:
        return TrainingHistorySummary(weeks=0)

    end_day = end_day or app_today()
    starts = weekly_windows(end_day, weeks)
    entries = fetch_workout_entries(
        user_id, starts[0], end_day, include_markers=True
    )
    weekly = bucket_by_week(entries, end_day, weeks)
    return TrainingHistorySummary(
        weeks=weeks,
        weekly=weekly,
        total_sessions=sum(w.session_count for w in weekly),
        total_volume=float(sum(w.total_volume for w in weekly)),
        volume_trend=volume_trend(weekly),
        has_data=bool(entries),
    )
