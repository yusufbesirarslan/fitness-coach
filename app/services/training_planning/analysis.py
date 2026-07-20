"""Pure, deterministic plan-decision rules (Sprint 6 PR3).

No DB, no Flask, no clock — every function is a total function of its inputs, so it
unit-tests without fixtures (mirrors ``training_progression/analysis.py``).

One precedence, not two: ``ProgressionReport.next_signal`` is already the single
canonical winner of the progression layer's documented precedence
(``insufficient_data`` → ``build_consistency`` → ``deload`` → ``plateau`` →
``progressing`` → ``keep_pushing``). This layer maps that signal 1:1 to a plan and
NEVER re-derives decisions from the raw booleans — a second precedence ladder would
be a second source of truth that could drift. Safety invariants therefore come for
free from upstream: ``next_signal == "progressing"`` implies consistent training
with no deload/plateau pending, so "never recommend an increase to an inconsistent
user" holds by construction (and is pinned by tests).

Every threshold below is explicit and documented in ``docs/TRAINING_PLANNING.md``.
"""
from app.services.training_progression import ProgressionReport

from .models import AdaptivePlan

# Conservative weekly volume increase when overload-ready — deliberately well under
# the common <=10% progressive-overload guideline.
VOLUME_INCREASE_STEP = 0.05
# Deload week trains at ~60% of recent weekly volume (the conservative middle of
# the standard 50-60% deload band).
DELOAD_VOLUME_CUT = 0.40

# next_signal → week_focus, 1:1. Unknown/future signal strings fall back to the
# fully neutral focus rather than guessing.
_FOCUS_BY_SIGNAL = {
    "insufficient_data": "insufficient_data",
    "build_consistency": "build_consistency",
    "deload": "deload",
    "plateau": "maintenance",
    "progressing": "overload",
    "keep_pushing": "steady",
}

# week_focus → primary machine-readable reason code.
_PRIMARY_REASON = {
    "insufficient_data": "insufficient_history",
    "build_consistency": "inconsistent_training",
    "deload": "deload_due",
    "maintenance": "plateau_detected",
    "overload": "progressing",
    "steady": "steady_state",
}


def derive_week_focus(next_signal: str) -> str:
    """The week's focus for the coach: what kind of week should come next.

    Judgment calls (documented, deliberately conservative):
    - ``plateau`` → ``"maintenance"`` (hold volume AND intensity), not an intensity
      push: a plateau that did not trigger deload means short history or a recent
      rest week, and without fatigue/recovery data we cannot tell "stalled because
      under-recovered" from "stalled because under-stimulated" — pushing the
      fatigued case is the harmful branch, so recommend a consolidation week.
    - ``keep_pushing`` → ``"steady"`` even when volume is trending down:
      recommending an increase "back to baseline" on ambiguous signals is
      speculative; reason codes carry the down-trend nuance instead.
    """
    return _FOCUS_BY_SIGNAL.get(next_signal, "insufficient_data")


def derive_volume_action(week_focus: str) -> str:
    """Weekly volume adjustment: only overload increases, only deload decreases."""
    if week_focus == "overload":
        return "increase"
    if week_focus == "deload":
        return "decrease"
    return "hold"


def derive_intensity_action(week_focus: str) -> str:
    """Weekly intensity adjustment: only overload progresses, only deload deloads."""
    if week_focus == "overload":
        return "progress"
    if week_focus == "deload":
        return "deload"
    return "hold"


def volume_delta_for(volume_action: str) -> float:
    """Signed volume-change fraction for an action; ``0.0`` for ``"hold"``.

    Intensity magnitude is deliberately NOT modelled: a single global intensity
    percentage is meaningless without per-exercise data (rep ranges and plate
    increments live in the generation layer's domain). Volume is the one knob the
    history actually measures, so it is the one magnitude we quantify.
    """
    if volume_action == "increase":
        return VOLUME_INCREASE_STEP
    if volume_action == "decrease":
        return -DELOAD_VOLUME_CUT
    return 0.0


def derive_reason_codes(report: ProgressionReport) -> tuple[str, ...]:
    """Ordered, deterministic machine codes explaining the recommendation.

    Always starts with the focus's primary code, then appends the trend nuances in
    a fixed order (``volume_trend_down`` before ``strength_trend_down``) whenever
    the underlying report shows them — uniformly for every focus, so consumers can
    rely on position 0 being the primary cause.
    """
    focus = derive_week_focus(report.next_signal)
    codes = [_PRIMARY_REASON.get(focus, "insufficient_history")]
    if report.volume_trend == "down":
        codes.append("volume_trend_down")
    if report.strength_trend == "down":
        codes.append("strength_trend_down")
    return tuple(codes)


def derive_adaptive_plan(report: ProgressionReport) -> AdaptivePlan:
    """Compose the canonical weekly plan recommendation from a progression report.

    Pure and total: same report in, same plan out. Echoes ``weeks``/``has_data``
    and embeds the report itself so downstream consumers (future AI coaching / UI)
    get the trends and weekly series without a second history read.
    """
    focus = derive_week_focus(report.next_signal)
    volume_action = derive_volume_action(focus)
    return AdaptivePlan(
        weeks=report.weeks,
        has_data=report.has_data,
        week_focus=focus,
        volume_action=volume_action,
        intensity_action=derive_intensity_action(focus),
        volume_delta_pct=volume_delta_for(volume_action),
        overload_ready=focus == "overload",
        maintenance_recommended=focus == "maintenance",
        reason_codes=derive_reason_codes(report),
        progression=report,
    )
