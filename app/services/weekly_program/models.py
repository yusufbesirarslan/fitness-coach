"""Frozen value object for the weekly-program consumer layer (Sprint 6 PR5).

A plain data holder (no logic, no DB, no Flask) describing one week of training
guidance in program terms. Style mirrors
``app/services/training_planning/models.py``.

Deliberately flat: it does NOT embed the ``AdaptivePlan`` it was built from. A
self-contained object is what lets future UI/runtime consumers depend on this layer
alone — they never need a ``training_planning`` import of their own, so the planner
keeps exactly the two approved outside owners
(``tests/test_dependency_boundaries.py``).
"""
from dataclasses import dataclass
from datetime import date

# Capabilities a weekly program would normally carry that ``AdaptivePlan`` does not
# model. Named explicitly so consumers can render an "unsupported" state instead of
# receiving a silently invented number — PR5 is forbidden from filling these in with
# a heuristic of its own (see docs/WEEKLY_PROGRAM.md, "Unsupported capabilities").
UNSUPPORTED_CAPABILITIES = (
    "session_frequency",
    "intensity_magnitude",
    "exercise_selection",
)


@dataclass(frozen=True)
class WeeklyProgramRecommendation:
    """Deterministic weekly-program recommendation translated from an ``AdaptivePlan``.

    Three kinds of field, and the distinction is the whole point of this layer:

    - *echoed* — copied verbatim from ``AdaptivePlan``. Every planning decision lives
      here; this layer never recomputes one.
    - *observed* — read straight out of the plan's embedded progression series
      (``baseline_week_start`` / ``baseline_weekly_volume``). A measurement, not a
      recommendation.
    - *derived* — ``target_weekly_volume``, the single arithmetic step this layer
      performs: the plan's own ``volume_delta_pct`` applied to the observed baseline.

    Both volume fields are ``None`` together whenever the window holds no positive
    volume — never ``0.0``, which would read as "train nothing this week" rather than
    "not enough data to say".

    ``explanation_keys`` are locale-neutral message keys for coach/UI copy (the copy
    itself is a later PR's concern); ``reason_codes`` are the plan's own ordered codes,
    unchanged.
    """
    weeks: int
    has_data: bool = False
    week_focus: str = "insufficient_data"
    volume_action: str = "hold"
    intensity_action: str = "hold"
    volume_delta_pct: float = 0.0
    overload_ready: bool = False
    maintenance_recommended: bool = False
    baseline_week_start: date | None = None
    baseline_weekly_volume: float | None = None
    target_weekly_volume: float | None = None
    reason_codes: tuple[str, ...] = ("insufficient_history",)
    explanation_keys: tuple[str, ...] = (
        "weekly_program.focus.insufficient_data",
        "weekly_program.reason.insufficient_history",
    )
    unsupported: tuple[str, ...] = UNSUPPORTED_CAPABILITIES
