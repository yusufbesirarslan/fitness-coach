"""Adaptive Training Engine — progression-analysis layer (Sprint 6 PR2).

Turns the canonical training-history foundation (Sprint 6 PR1) into deterministic
progression signals AxisAI can consume for adaptive coaching: volume/strength
trends, plateau detection, deload detection, load consistency, and a single
normalized "next signal" for the coach. Pure interpretation on top of the
foundation — it never re-derives windowing or marker exclusion, it composes
``training_history``.

Layering (mirrors the foundation):
- ``models``   — frozen value objects (``WeeklyStrength``, ``ProgressionReport``).
- ``analysis`` — pure deterministic signal functions (fixture-free).
- ``__init__`` — public API + ``build_progression_report`` orchestrator (the one
  impure boundary, only because it reads history through the foundation).

See ``docs/TRAINING_PROGRESSION.md``. This layer adds no schema, no route, and no
coach-prompt change — later PRs wire it into runtime.
"""
from datetime import date

from app.services.training_history import (
    bucket_by_week,
    fetch_workout_entries,
    volume_trend,
    weekly_windows,
)
from app.timeutil import app_today

from .analysis import (
    assess_consistency,
    derive_next_signal,
    detect_deload_due,
    detect_plateau,
    is_progressing,
    series_trend,
    weekly_best_estimated_1rm,
)
from .models import ProgressionReport, WeeklyStrength

__all__ = [
    "WeeklyStrength",
    "ProgressionReport",
    "series_trend",
    "weekly_best_estimated_1rm",
    "is_progressing",
    "detect_plateau",
    "detect_deload_due",
    "assess_consistency",
    "derive_next_signal",
    "build_progression_report",
]


def build_progression_report(
    user_id: int, weeks: int = 4, *, end_day: date | None = None
) -> ProgressionReport:
    """Deterministic progression signals for ``user_id``'s last ``weeks`` weeks.

    Reads history once through the foundation (Istanbul-day windows, marker-aware),
    then derives every signal purely. ``end_day`` defaults to today (Istanbul); pass
    an explicit day for hermetic tests. Empty history (or ``weeks <= 0``) yields a
    fully neutral report with ``has_data=False``.
    """
    if weeks <= 0:
        return ProgressionReport(weeks=0)

    end_day = end_day or app_today()
    starts = weekly_windows(end_day, weeks)
    entries = fetch_workout_entries(user_id, starts[0], end_day, include_markers=True)

    weekly_volume = bucket_by_week(entries, end_day, weeks)
    weekly_strength = weekly_best_estimated_1rm(entries, end_day, weeks)

    vtrend = volume_trend(weekly_volume)
    strend = series_trend(s.best_estimated_1rm for s in weekly_strength)
    progressing = is_progressing(vtrend, strend)
    plateau = detect_plateau(weekly_volume, weekly_strength)
    deload = detect_deload_due(weekly_volume, weekly_strength)
    consistency = assess_consistency(weekly_volume)
    has_data = bool(entries)
    next_signal = derive_next_signal(
        has_data=has_data,
        load_consistency=consistency,
        deload_due=deload,
        is_plateau=plateau,
        is_progressing=progressing,
    )

    return ProgressionReport(
        weeks=weeks,
        has_data=has_data,
        volume_trend=vtrend,
        strength_trend=strend,
        is_progressing=progressing,
        is_plateau=plateau,
        deload_due=deload,
        load_consistency=consistency,
        next_signal=next_signal,
        weekly_volume=weekly_volume,
        weekly_strength=weekly_strength,
    )
