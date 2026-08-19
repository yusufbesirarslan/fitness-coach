"""Canonical Progress summary read model (Progress Redesign PR2).

The single server-owned authority for the three questions the Progress page asks:
*how am I doing*, *what changed*, and *is there enough evidence to say*. It is a
**read model** — nothing is persisted, cached or scored; it composes authorities
that already exist and publishes one bounded, deterministic contract.

Layering (mirrors ``training_progression``):
- ``models``   — frozen value objects + the bounded state constants.
- ``analysis`` — pure signal→state mapping and body interpretation (fixture-free).
- ``queries``  — narrow, user-scoped body/profile reads.
- ``payload``  — the JSON projection of the contract.
- ``__init__`` — public API + the ``build_progress_summary`` orchestrator.

Dependency direction is one-way and must stay that way::

    progress.js → GET /api/progress/summary → progress_summary
                                                   ↓
                                          training_progression
                                                   ↓
                                            training_history

Nothing under ``training_*`` imports this package; nothing here re-derives a
training signal. See ``docs/PROGRESS_SUMMARY.md``.
"""
from datetime import date

from app.services.training_progression import build_progression_report
from app.timeutil import app_today

from .analysis import (
    PERFORMANCE_BY_SIGNAL,
    TRAJECTORY_BY_SIGNAL,
    build_window,
    performance_state_for_signal,
    summarize_body,
    summarize_consistency,
    summarize_performance,
    trajectory_for_signal,
)
from .models import (
    BODY_AVAILABLE,
    BODY_INSUFFICIENT_DATA,
    BODY_PARTIAL,
    BODY_STATUSES,
    CONSISTENCY_CONSISTENT,
    CONSISTENCY_INCONSISTENT,
    CONSISTENCY_INSUFFICIENT_DATA,
    CONSISTENCY_STATES,
    CONTRACT_VERSION,
    PERFORMANCE_BUILDING_BASELINE,
    PERFORMANCE_BUILDING_CONSISTENCY,
    PERFORMANCE_DELOAD,
    PERFORMANCE_PLATEAU,
    PERFORMANCE_PROGRESSING,
    PERFORMANCE_STATES,
    PERFORMANCE_STEADY,
    SUMMARY_WEEKS,
    TRAJECTORY_BUILDING_BASELINE,
    TRAJECTORY_NEEDS_ATTENTION,
    TRAJECTORY_ON_TRACK,
    TRAJECTORY_STATES,
    BodyFacts,
    BodySummary,
    ConsistencySummary,
    PerformanceSummary,
    ProgressSummary,
    ProgressWindow,
    Trajectory,
    UnknownProgressionSignal,
)
from .payload import progress_summary_payload
from .queries import fetch_body_facts

__all__ = [
    "BodyFacts",
    "BodySummary",
    "ConsistencySummary",
    "PerformanceSummary",
    "ProgressSummary",
    "ProgressWindow",
    "Trajectory",
    "UnknownProgressionSignal",
    "CONTRACT_VERSION",
    "SUMMARY_WEEKS",
    "TRAJECTORY_STATES",
    "TRAJECTORY_BUILDING_BASELINE",
    "TRAJECTORY_ON_TRACK",
    "TRAJECTORY_NEEDS_ATTENTION",
    "PERFORMANCE_STATES",
    "PERFORMANCE_BUILDING_BASELINE",
    "PERFORMANCE_PROGRESSING",
    "PERFORMANCE_STEADY",
    "PERFORMANCE_BUILDING_CONSISTENCY",
    "PERFORMANCE_PLATEAU",
    "PERFORMANCE_DELOAD",
    "BODY_STATUSES",
    "BODY_AVAILABLE",
    "BODY_PARTIAL",
    "BODY_INSUFFICIENT_DATA",
    "CONSISTENCY_STATES",
    "CONSISTENCY_CONSISTENT",
    "CONSISTENCY_INCONSISTENT",
    "CONSISTENCY_INSUFFICIENT_DATA",
    "TRAJECTORY_BY_SIGNAL",
    "PERFORMANCE_BY_SIGNAL",
    "trajectory_for_signal",
    "performance_state_for_signal",
    "build_window",
    "summarize_body",
    "summarize_consistency",
    "summarize_performance",
    "fetch_body_facts",
    "progress_summary_payload",
    "build_progress_summary",
]


def build_progress_summary(
    user_id: int, *, end_day: date | None = None
) -> ProgressSummary:
    """The canonical Progress summary for ``user_id`` over the fixed 4-week window.

    Read-only and deterministic: for the same persisted data and the same
    ``end_day`` the result is equal. ``end_day`` defaults to the application's
    single day authority (``app.timeutil.app_today``, fixed Europe/Istanbul);
    tests pass an explicit day to stay hermetic. It is a *service* argument, not
    a request parameter — the route pins it (see ``docs/PROGRESS_SUMMARY.md``).

    The window is fixed at ``SUMMARY_WEEKS`` for the same reason: a trajectory the
    caller can re-window until it turns green is not a trajectory.

    Trajectory is training-led in V1. ``training_progression`` already resolves
    every overlap between its own booleans into one ``next_signal`` with a
    documented precedence, so this layer consumes that resolution and maps it —
    building a second precedence chain out of ``is_plateau``/``deload_due``/… would
    put two disagreeing authorities in the product. Body is summarized truthfully
    beside it and deliberately cannot flip the state: weight is noisy and no
    validated longitudinal body authority exists to judge it against.

    Raises ``UnknownProgressionSignal`` if the training contract yields a signal
    this layer has no mapping for — fail closed rather than invent a state.
    """
    # Resolve "today" exactly once and pass it down. Letting the progression layer
    # default it separately would leave two clock reads in one summary, and a
    # request that straddles Istanbul midnight would report a window the signals
    # were not computed over.
    end_day = end_day or app_today()

    report = build_progression_report(user_id, weeks=SUMMARY_WEEKS, end_day=end_day)

    return ProgressSummary(
        window=build_window(end_day, SUMMARY_WEEKS),
        trajectory=trajectory_for_signal(report.next_signal),
        body=summarize_body(fetch_body_facts(user_id)),
        performance=summarize_performance(report),
        consistency=summarize_consistency(report),
    )
