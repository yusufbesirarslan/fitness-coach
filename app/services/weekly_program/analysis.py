"""Pure, deterministic translation of an ``AdaptivePlan`` into program terms (PR5).

No DB, no Flask, no clock — every function is a total function of its inputs, so the
whole consumer unit-tests without fixtures (mirrors
``training_planning/analysis.py``).

**This module decides nothing.** ``AdaptivePlan`` is the sole planning authority: it
already answers whether the user is progressing, plateauing, deload-due,
overload-ready, or should hold — and this layer copies those answers verbatim. There
is no threshold, no precedence ladder, and no decision read out of the report's raw
booleans anywhere below; adding one would make PR5 a second planning engine.

The one computation here is arithmetic, not judgment: the plan states volume change as
a signed fraction, and consumers need an absolute number to show. So we take the most
recent *observed* positive weekly volume out of the plan's own embedded progression
series and apply the plan's own ``volume_delta_pct`` to it. No history is read (the
plan carries the series), and no fallback volume is invented when there is none.
"""
from app.services.training_planning import AdaptivePlan

from .models import UNSUPPORTED_CAPABILITIES, WeeklyProgramRecommendation

# Namespace for the locale-neutral message keys. Consumers map these to copy; this
# layer never emits user-facing text.
EXPLANATION_KEY_PREFIX = "weekly_program"
# Weekly volume is reported to 2 decimals, matching the rest of the stack
# (training_history.estimated_1rm, GET /api/progress/workout). It keeps binary float
# noise — 1000 * (1 + 0.05) == 1050.0000000000001 — out of a displayed number.
VOLUME_PRECISION = 2


def select_volume_baseline(plan: AdaptivePlan):
    """Most recent window with positive volume, or ``None``.

    Scans the plan's embedded ``progression.weekly_volume`` newest-first and returns
    the first ``WeeklyVolume`` that actually holds load. Zero-volume windows are
    skipped rather than treated as a baseline of ``0.0``: a rest week (or a
    marker-only "I trained" week) is missing data for this purpose, not a measurement
    of zero — anchoring a target to it would scale every recommendation to nothing.

    Purely observational: the window is read out of the plan, never re-queried from
    ``WorkoutLog``.
    """
    for week in reversed(plan.progression.weekly_volume):
        if week.total_volume > 0:
            return week
    return None


def target_volume_for(
    baseline_volume: float | None, volume_delta_pct: float
) -> float | None:
    """Apply the plan's signed volume delta to an observed baseline.

    ``baseline * (1 + volume_delta_pct)``, rounded to ``VOLUME_PRECISION``. A missing
    baseline propagates as ``None`` — with nothing measured there is nothing to scale,
    and substituting a number would be a guess.
    """
    if baseline_volume is None:
        return None
    return round(baseline_volume * (1 + volume_delta_pct), VOLUME_PRECISION)


def derive_explanation_keys(plan: AdaptivePlan) -> tuple[str, ...]:
    """Ordered, locale-neutral message keys for coach/UI explanation.

    Mechanical namespacing of the canonical vocabulary that already exists — the
    plan's ``week_focus`` first, then its ``reason_codes`` in their given order. No
    second taxonomy is introduced: every key here is an existing code with a prefix,
    so position 0 remains the primary cause exactly as ``reason_codes`` guarantees.
    """
    keys = [f"{EXPLANATION_KEY_PREFIX}.focus.{plan.week_focus}"]
    keys.extend(
        f"{EXPLANATION_KEY_PREFIX}.reason.{code}" for code in plan.reason_codes
    )
    return tuple(keys)


def derive_weekly_program(plan: AdaptivePlan) -> WeeklyProgramRecommendation:
    """Compose the weekly-program recommendation from a plan. Pure and total.

    Every decision field is a verbatim echo — same value, same vocabulary, no
    reinterpretation — so a consumer reading this object and a consumer reading
    ``AdaptivePlan`` can never disagree.
    """
    baseline = select_volume_baseline(plan)
    baseline_volume = None if baseline is None else baseline.total_volume
    return WeeklyProgramRecommendation(
        weeks=plan.weeks,
        has_data=plan.has_data,
        week_focus=plan.week_focus,
        volume_action=plan.volume_action,
        intensity_action=plan.intensity_action,
        volume_delta_pct=plan.volume_delta_pct,
        overload_ready=plan.overload_ready,
        maintenance_recommended=plan.maintenance_recommended,
        baseline_week_start=None if baseline is None else baseline.week_start,
        baseline_weekly_volume=baseline_volume,
        target_weekly_volume=target_volume_for(
            baseline_volume, plan.volume_delta_pct
        ),
        reason_codes=plan.reason_codes,
        explanation_keys=derive_explanation_keys(plan),
        unsupported=UNSUPPORTED_CAPABILITIES,
    )
