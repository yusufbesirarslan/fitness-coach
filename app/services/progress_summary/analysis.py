"""Pure, deterministic Progress-summary interpretation.

No DB, no Flask, no clock — every function is a total function of its inputs, so
it unit-tests without fixtures (mirrors ``training_progression/analysis.py``).

The whole file is *mapping*, not measurement. Volume trends, plateau, deload and
consistency thresholds all belong to ``training_progression``; nothing here
recomputes one of them. What this layer adds is the product decision of how the
canonical training signal reads as a trajectory, and how the user's body facts
read as an availability state.
"""
from datetime import date

from app.services.training_history import weekly_windows
from app.services.training_progression import ProgressionReport
from app.timeutil import APP_TZ

from .models import (
    BODY_AVAILABLE,
    BODY_INSUFFICIENT_DATA,
    BODY_PARTIAL,
    PERFORMANCE_BUILDING_BASELINE,
    PERFORMANCE_BUILDING_CONSISTENCY,
    PERFORMANCE_DELOAD,
    PERFORMANCE_PLATEAU,
    PERFORMANCE_PROGRESSING,
    PERFORMANCE_STEADY,
    TRAJECTORY_BUILDING_BASELINE,
    TRAJECTORY_NEEDS_ATTENTION,
    TRAJECTORY_ON_TRACK,
    BodyFacts,
    BodySummary,
    ConsistencySummary,
    PerformanceSummary,
    ProgressWindow,
    Trajectory,
    UnknownProgressionSignal,
)

# ── The V1 trajectory contract ─────────────────────────────────────────────
# Training-led on purpose. training_progression is the only domain in the
# product with a validated, deterministic, documented longitudinal authority;
# body, nutrition and recovery have none, so weighting them into a composite
# score would be inventing product semantics rather than reporting them.
#
# One entry per canonical ``next_signal`` value. There is no fallback branch:
# a value missing from this table raises (see ``UnknownProgressionSignal``).
TRAJECTORY_BY_SIGNAL = {
    "insufficient_data": TRAJECTORY_BUILDING_BASELINE,
    "progressing": TRAJECTORY_ON_TRACK,
    "keep_pushing": TRAJECTORY_ON_TRACK,
    "build_consistency": TRAJECTORY_NEEDS_ATTENTION,
    "plateau": TRAJECTORY_NEEDS_ATTENTION,
    "deload": TRAJECTORY_NEEDS_ATTENTION,
}

# Presentation projection of the same signal — one step finer than trajectory so
# the PERFORMANCE card can say *which* attention is due. This renames for the
# UI; it does not reinterpret. ``keep_pushing`` becomes ``steady`` because the
# canonical meaning is "holding, nothing to escalate", which reads as an
# instruction rather than a state under its coaching-oriented name.
PERFORMANCE_BY_SIGNAL = {
    "insufficient_data": PERFORMANCE_BUILDING_BASELINE,
    "progressing": PERFORMANCE_PROGRESSING,
    "keep_pushing": PERFORMANCE_STEADY,
    "build_consistency": PERFORMANCE_BUILDING_CONSISTENCY,
    "plateau": PERFORMANCE_PLATEAU,
    "deload": PERFORMANCE_DELOAD,
}

# Rounding applies to *derived* kilograms only. Stored weights are echoed exactly
# as persisted (``_parse_weight`` accepts any finite 20..500 float, so rounding
# them here would quietly publish a different number than the one the user typed);
# a subtraction of two floats, on the other hand, produces noise like
# ``-0.5999999999999943``. One decimal place is what /api/progress/insights
# already applies to this same two-point subtraction, so the two surfaces cannot
# disagree about the same delta.
_DERIVED_WEIGHT_DP = 1


def trajectory_for_signal(next_signal: str) -> Trajectory:
    """Canonical ``next_signal`` → the one V1 trajectory state.

    Raises ``UnknownProgressionSignal`` for anything not in the table: an
    unrecognised value means the training contract moved under us, and guessing
    a trajectory from it would publish a fabricated assessment.
    """
    try:
        state = TRAJECTORY_BY_SIGNAL[next_signal]
    except (KeyError, TypeError):
        raise UnknownProgressionSignal(
            "unmapped training progression signal") from None
    return Trajectory(state=state, reason=next_signal)


def performance_state_for_signal(next_signal: str) -> str:
    """Canonical ``next_signal`` → PERFORMANCE presentation state (fails closed)."""
    try:
        return PERFORMANCE_BY_SIGNAL[next_signal]
    except (KeyError, TypeError):
        raise UnknownProgressionSignal(
            "unmapped training progression signal") from None


def build_window(end_day: date, weeks: int) -> ProgressWindow:
    """The inclusive Istanbul-day window ``weeks`` trailing windows cover.

    ``start`` comes from the training-history foundation's own ``weekly_windows``
    — the same call ``build_progression_report`` makes — so the window reported
    to the client is by construction the window the signals were computed over,
    not a parallel piece of date arithmetic that could drift from it.
    """
    starts = weekly_windows(end_day, weeks)
    return ProgressWindow(
        weeks=weeks,
        start=starts[0] if starts else end_day,
        end=end_day,
        timezone=APP_TZ.key,
    )


def summarize_performance(report: ProgressionReport) -> PerformanceSummary:
    """Bounded PERFORMANCE projection of the canonical progression report."""
    return PerformanceSummary(
        state=performance_state_for_signal(report.next_signal),
        volume_trend=report.volume_trend,
        strength_trend=report.strength_trend,
        next_signal=report.next_signal,
    )


def summarize_consistency(report: ProgressionReport) -> ConsistencySummary:
    """Canonical training consistency + the counts that make it explainable.

    ``active_weeks`` and ``sessions`` are read straight off the report's own
    ``weekly_volume`` objects. ``session_count`` is the foundation's count of
    distinct trained days in a window, and the windows are contiguous and
    non-overlapping, so summing them cannot double-count a day — no second
    definition of "a session" is introduced here.
    """
    weekly = report.weekly_volume
    return ConsistencySummary(
        state=report.load_consistency,
        active_weeks=sum(1 for w in weekly if w.session_count > 0),
        analyzed_weeks=len(weekly),
        sessions=sum(w.session_count for w in weekly),
    )


def summarize_body(facts: BodyFacts) -> BodySummary:
    """Body facts → the truthful availability state. No judgement, no threshold.

    Weight is noisy and the repository owns no validated body-trend authority, so
    this reports what is known and stops. In particular a delta needs **two**
    qualifying observations — one observation is not a change, and treating the
    missing one as zero would render "no data" as "no movement".
    """
    current = facts.current_weight_kg
    target = facts.target_weight_kg

    delta = None
    weights = facts.recent_qualifying_weights
    if len(weights) >= 2:  # newest-first: [latest, previous]
        delta = round(weights[0] - weights[1], _DERIVED_WEIGHT_DP)

    if current is None:
        status = BODY_INSUFFICIENT_DATA
    elif delta is None:
        status = BODY_PARTIAL
    else:
        status = BODY_AVAILABLE

    distance = None
    if current is not None and target is not None:
        distance = round(abs(current - target), _DERIVED_WEIGHT_DP)

    return BodySummary(
        status=status,
        current_weight_kg=current,
        weight_delta_kg=delta,
        target_weight_kg=target,
        distance_to_target_kg=distance,
    )
