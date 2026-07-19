"""Frozen value objects for the training-history foundation.

Plain data holders (no logic, no DB, no Flask) so they can cross the pure/impure
boundary freely — the query layer builds them, the analysis layer computes over
them. Style mirrors ``app/services/training_generation/models.py``.
"""
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class WorkoutEntry:
    """A single normalized ``WorkoutLog`` row.

    ``performed_on`` is the Istanbul calendar day of ``created_at`` (naive-UTC),
    resolved via ``app.timeutil.app_date_of`` — never compare ``created_at``
    directly. ``is_marker`` flags the synthetic ``WORKOUT_COMPLETION_MARKER``
    row (a "session happened" signal, ``volume=0``), which real volume/exercise
    reporting excludes.
    """
    exercise_name: str
    sets: int
    reps: int
    weight_kg: float
    volume: float
    performed_on: date
    created_at: datetime
    is_marker: bool = False


@dataclass(frozen=True)
class WeeklyVolume:
    """Aggregates for one 7-day window (``week_start`` .. ``week_start`` + 6 days).

    ``session_count`` counts distinct trained days (marker or real). The remaining
    totals count only real exercise entries (markers excluded).
    """
    week_start: date
    session_count: int = 0
    entry_count: int = 0
    total_sets: int = 0
    total_volume: float = 0.0


@dataclass(frozen=True)
class TrainingHistorySummary:
    """Deterministic roll-up of a user's recent training history.

    The canonical foundation output future PRs (progression analysis, plateau /
    deload detection, recovery scoring, adaptive program updates) build on.
    """
    weeks: int
    weekly: list[WeeklyVolume] = field(default_factory=list)
    total_sessions: int = 0
    total_volume: float = 0.0
    volume_trend: str = "flat"  # "up" | "flat" | "down"
    has_data: bool = False
