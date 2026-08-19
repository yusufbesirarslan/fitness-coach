"""Canonical Progress History read model (Progress Redesign PR5).

The single server-owned authority for "how has my Progress state evolved?".
It is a **reconstructed** read model: each row is the current canonical
Progress algorithms applied to historical facts as-of that check-in's Istanbul
day. It is not a persisted snapshot of what AxisAI displayed at the time.

Layering (mirrors ``progress_summary`` / ``progress_physique``):

- ``models``  — frozen value objects + the bounded state constants.
- ``queries`` — owner-scoped bounded qualifying-check-in reads.
- ``payload`` — the JSON projection of the contract.
- ``__init__`` — public API + the ``build_progress_history`` orchestrator.

There is no analysis engine here. Trajectory, performance and consistency are
the Progress Summary mappings of ``training_progression(..., end_day=D)``.
Body facts come from the anchored qualifying WeeklyCheckIn and the previous
one. ``build_progress_summary`` is not called: its body/profile read is
current-state and is not historically safe.

Dependency direction is one-way and must stay that way::

    progress_history.js → GET /api/progress/history → progress_history
                               ↙                    ↘
                    WeeklyCheckIn            training_progression
                                                    ↓
                                           Progress Summary mappings

Nothing under ``training_*`` or ``progress_summary`` imports this package.
See ``docs/PROGRESS_HISTORY.md``.
"""
from app.services.progress_summary import (
    CONSISTENCY_STATES,
    SUMMARY_WEEKS,
    BodyFacts,
    UnknownProgressionSignal,
    build_window,
    summarize_body,
    summarize_consistency,
    summarize_performance,
    trajectory_for_signal,
)
from app.services.training_progression import build_progression_report
from app.timeutil import app_date_of

from .models import (
    CONTRACT_VERSION,
    HISTORY_LIMIT,
    STATE_AVAILABLE,
    STATE_EMPTY,
    STATES,
    HistoryEntry,
    ProgressHistory,
)
from .payload import progress_history_payload
from .queries import _positive, fetch_qualifying_checkins

__all__ = [
    "CONTRACT_VERSION",
    "HISTORY_LIMIT",
    "STATE_AVAILABLE",
    "STATE_EMPTY",
    "STATES",
    "HistoryEntry",
    "ProgressHistory",
    "UnknownProgressionSignal",
    "build_progress_history",
    "progress_history_payload",
    "fetch_qualifying_checkins",
]


def _historical_body(row, previous):
    """Anchored check-in weight + delta vs the previous qualifying check-in.

    Reuses ``summarize_body`` for the two-point subtraction/rounding. Target
    and current profile are deliberately omitted: they are not historical
    facts of this check-in.
    """
    current = _positive(row.weight)
    previous_weight = _positive(previous.weight) if previous is not None else None
    weights = tuple(w for w in (current, previous_weight) if w is not None)
    summary = summarize_body(BodyFacts(
        current_weight_kg=current,
        recent_qualifying_weights=weights,
    ))
    return current, summary.weight_delta_kg


def _reconstruct_entry(user_id, row, previous) -> HistoryEntry:
    """One history row: historical training as-of D + historical check-in body."""
    analysis_day = app_date_of(row.created_at)
    report = build_progression_report(
        user_id, weeks=SUMMARY_WEEKS, end_day=analysis_day)

    trajectory = trajectory_for_signal(report.next_signal)
    performance = summarize_performance(report)
    consistency = summarize_consistency(report)
    if consistency.state not in CONSISTENCY_STATES:
        raise UnknownProgressionSignal("unmapped training consistency state")

    weight_kg, weight_delta_kg = _historical_body(row, previous)
    return HistoryEntry(
        checked_in_at=row.created_at,
        analysis_day=analysis_day,
        window=build_window(analysis_day, SUMMARY_WEEKS),
        trajectory=trajectory,
        performance=performance,
        consistency=consistency,
        weight_kg=weight_kg,
        weight_delta_kg=weight_delta_kg,
    )


def build_progress_history(user_id: int) -> ProgressHistory:
    """The canonical Progress History for ``user_id``.

    Read-only and deterministic: for the same persisted qualifying check-ins
    and the same historical training facts, the result is equal. The visible
    window is fixed at ``HISTORY_LIMIT``. One extra qualifying row is fetched
    so the oldest visible row can compute its delta and so ``has_more`` does
    not need ``COUNT(*)``.

    Raises ``UnknownProgressionSignal`` if a historical training report yields
    a signal this layer has no mapping for — fail closed rather than invent a
    reconstructed state.
    """
    rows = fetch_qualifying_checkins(user_id, HISTORY_LIMIT + 1)
    if not rows:
        return ProgressHistory(state=STATE_EMPTY, entries=(), has_more=False)

    has_more = len(rows) > HISTORY_LIMIT
    visible = rows[:HISTORY_LIMIT]
    entries = tuple(
        _reconstruct_entry(
            user_id,
            row,
            rows[index + 1] if index + 1 < len(rows) else None,
        )
        for index, row in enumerate(visible)
    )
    return ProgressHistory(
        state=STATE_AVAILABLE,
        entries=entries,
        has_more=has_more,
    )
