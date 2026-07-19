"""Pure, deterministic calculations over training-history value objects.

No DB, no Flask, no clock — every function is a total function of its inputs, so
it unit-tests without fixtures (mirrors ``training_generation/scoring_engine.py``
and ``tests/test_calculations.py``). All windowing/marker semantics are decided by
the caller; these helpers only do arithmetic.
"""
from datetime import date, timedelta
from collections.abc import Iterable

from .models import WeeklyVolume, WorkoutEntry

WEEK_DAYS = 7
# Volume must move more than ±5% week-over-week to count as a trend, not noise.
_TREND_BAND = 0.05


def total_volume(entries: Iterable[WorkoutEntry]) -> float:
    """Sum of ``volume`` across real exercise entries (markers excluded)."""
    return float(sum(e.volume for e in entries if not e.is_marker))


def total_sets(entries: Iterable[WorkoutEntry]) -> int:
    """Sum of ``sets`` across real exercise entries (markers excluded)."""
    return int(sum(e.sets for e in entries if not e.is_marker))


def session_days(entries: Iterable[WorkoutEntry]) -> set[date]:
    """Distinct Istanbul days on which the user trained (marker or real)."""
    return {e.performed_on for e in entries}


def count_sessions(entries: Iterable[WorkoutEntry]) -> int:
    """Number of distinct trained days — the canonical "sessions" definition."""
    return len(session_days(entries))


def weekly_windows(end_day: date, weeks: int) -> list[date]:
    """Week-start dates for the last ``weeks`` 7-day windows, oldest first.

    The newest window starts on ``end_day``; each earlier window starts 7 days
    before the next (matching the 21/14/7/0 offsets used elsewhere).
    """
    if weeks <= 0:
        return []
    return [end_day - timedelta(days=WEEK_DAYS * (weeks - 1 - i)) for i in range(weeks)]


def bucket_by_week(
    entries: Iterable[WorkoutEntry], end_day: date, weeks: int
) -> list[WeeklyVolume]:
    """Aggregate ``entries`` into ``weeks`` consecutive 7-day windows, oldest first.

    Each window covers ``[week_start, week_start + 6 days]`` inclusive. Windows do
    not overlap, so every entry lands in at most one bucket; entries outside all
    windows are ignored. ``session_count`` counts distinct trained days (marker or
    real); the other totals count real exercise entries only.
    """
    entries = list(entries)
    buckets: list[WeeklyVolume] = []
    for week_start in weekly_windows(end_day, weeks):
        week_end = week_start + timedelta(days=WEEK_DAYS - 1)
        in_week = [e for e in entries if week_start <= e.performed_on <= week_end]
        buckets.append(
            WeeklyVolume(
                week_start=week_start,
                session_count=count_sessions(in_week),
                entry_count=sum(1 for e in in_week if not e.is_marker),
                total_sets=total_sets(in_week),
                total_volume=total_volume(in_week),
            )
        )
    return buckets


def volume_trend(weekly: list[WeeklyVolume]) -> str:
    """Direction of training volume across the windows: "up" | "flat" | "down".

    Compares the earliest to the latest window that has any real volume. Fewer
    than two such windows → "flat". A change within ±5% is treated as flat.
    """
    active = [w for w in weekly if w.total_volume > 0]
    if len(active) < 2:
        return "flat"
    first, last = active[0].total_volume, active[-1].total_volume
    if last > first * (1 + _TREND_BAND):
        return "up"
    if last < first * (1 - _TREND_BAND):
        return "down"
    return "flat"


def estimated_1rm(weight_kg: float, reps: int) -> float:
    """Epley one-rep-max estimate: ``w * (1 + reps / 30)``, rounded to 2 dp.

    A minimal foundation building block for future intensity-trend work — not
    progressive-overload logic. Non-positive load/reps → 0.0; a single rep is
    already the 1RM.
    """
    if weight_kg <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return round(float(weight_kg), 2)
    return round(weight_kg * (1 + reps / 30), 2)
