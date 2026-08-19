"""Frozen value objects + bounded vocabulary for Progress History.

This layer owns no Progress algorithm. Trajectory, performance and consistency
are the Progress Summary mappings of a historical training report. Body facts
are the anchored qualifying WeeklyCheckIn and the previous one.

A history row is a reconstructed read of canonical facts through the
check-in's Istanbul calendar day. V1 is day-granular, not timestamp-granular.
It is not a persisted snapshot of what AxisAI displayed then.
"""
from dataclasses import dataclass
from datetime import date, datetime

from app.services.progress_summary import (
    ConsistencySummary,
    PerformanceSummary,
    ProgressWindow,
    Trajectory,
)

# Visible entry bound. Server-owned, not a query parameter.
HISTORY_LIMIT = 12

STATE_EMPTY = "empty"
STATE_AVAILABLE = "available"
STATES = (STATE_EMPTY, STATE_AVAILABLE)

# Bumped only on a breaking change to the wire contract.
CONTRACT_VERSION = 1


@dataclass(frozen=True)
class HistoryEntry:
    """One reconstructed Progress state, anchored by a qualifying check-in."""
    checked_in_at: datetime
    analysis_day: date
    window: ProgressWindow
    trajectory: Trajectory
    performance: PerformanceSummary
    consistency: ConsistencySummary
    weight_kg: float | None = None
    weight_delta_kg: float | None = None


@dataclass(frozen=True)
class ProgressHistory:
    """The canonical Progress History read model for one user."""
    state: str
    entries: tuple[HistoryEntry, ...] = ()
    has_more: bool = False
