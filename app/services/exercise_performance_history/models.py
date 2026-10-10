"""TD-01 bounds, closed vocabularies and frozen value objects.

Every bound and token of the per-exercise historical performance projection is
defined here and nowhere else. The values equal TI-00 §10 but are deliberately
NOT imported from ``training_intelligence``: a change to the TI ruleset must not
silently move this projection's window (TD-01 §Bounds).

Nothing here touches the database, Flask, the clock or a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

# ── Bounds (TD-01 §Bounds) ──────────────────────────────────────────────────
HISTORY_DAYS = 56        # product window: anchor - 56 .. anchor (57 Istanbul day keys)
MAX_SCAN_ROWS = 64       # technical candidate-scan safety bound (SQL LIMIT)
MAX_OCCURRENCES = 8      # returned occurrences, newest first
# Sets per occurrence are bounded by the canonical checkpoint parser
# (MAX_SETS_PER_EXERCISE = 20); TD-01 adds no second limit.

# ── Closed vocabularies ─────────────────────────────────────────────────────
STATE_AVAILABLE = "available"
STATE_AVAILABLE_WITH_GAPS = "available_with_gaps"
STATE_NO_HISTORY = "no_history"
STATE_UNDETERMINED = "undetermined"
STATES = (STATE_AVAILABLE, STATE_AVAILABLE_WITH_GAPS, STATE_NO_HISTORY, STATE_UNDETERMINED)

# Row classes (TD-01 §Row classification).
ROW_ELIGIBLE = "eligible"
ROW_EXCLUDED = "excluded"
ROW_UNAVAILABLE = "unavailable"

# Unavailable reasons: each one degrades window coverage.
REASON_CHECKPOINT_MISSING = "checkpoint_missing"   # SQL NULL at revision 0 only
REASON_CHECKPOINT_INVALID = "checkpoint_invalid"   # canonical re-validation refused it
REASON_SESSION_INVALID = "session_invalid"         # stored workout_date unreadable
UNAVAILABLE_REASONS = (REASON_CHECKPOINT_MISSING, REASON_CHECKPOINT_INVALID,
                       REASON_SESSION_INVALID)

# Date exclusions: deterministic non-facts, never coverage gaps (T4).
EXCLUDED_MISSING_COMPLETED_AT = "missing_completed_at"
EXCLUDED_CROSS_DATE = "cross_date"
EXCLUSION_REASONS = (EXCLUDED_MISSING_COMPLETED_AT, EXCLUDED_CROSS_DATE)


class ProjectionInvariantError(RuntimeError):
    """The assembled history violates a TD-01 invariant (the invariant_failed
    class). No ``ExerciseHistory`` is ever returned alongside it."""


# ── Source row (the five selected columns, unchanged) ───────────────────────
@dataclass(frozen=True)
class CandidateRow:
    public_id: str
    workout_date: object                 # stored value, interpreted by selection
    completed_at: Optional[datetime]     # naive UTC as written
    checkpoint_revision: int
    checkpoint_data: Optional[str]


@dataclass(frozen=True)
class RowClassification:
    row_class: str                       # ROW_ELIGIBLE | ROW_EXCLUDED | ROW_UNAVAILABLE
    reason: Optional[str]                # exclusion or unavailable reason; None if eligible
    workout_date: Optional[date]         # None only for session_invalid
    exercises: tuple = ()                # canonical parsed exercises (eligible only)


# ── Internal projection (TD-01 §Internal Projection) ────────────────────────
@dataclass(frozen=True)
class PerformedSet:
    """One canonical performed-set fact; ``None`` means unknown, never zero."""
    index: int
    reps: Optional[int]
    weight_kg: Optional[float]


@dataclass(frozen=True)
class Occurrence:
    """One exercise inside one completed session (identity = B5)."""
    session_ref: str
    checkpoint_revision: int
    workout_date: date
    completed_at: datetime
    sets: tuple                          # PerformedSet, ascending index, >= 1


@dataclass(frozen=True)
class UnavailableSession:
    workout_date: Optional[date]         # None ONLY for session_invalid (C-3)
    completed_at: Optional[datetime]
    reason: str                          # one of UNAVAILABLE_REASONS


@dataclass(frozen=True)
class Coverage:
    scanned_sessions: int
    excluded_cross_date: int
    excluded_missing_completed_at: int
    unavailable: tuple                   # UnavailableSession, SQL order
    scan_limit_reached: bool
    occurrence_limit_reached: bool

    @property
    def complete(self) -> bool:
        """The whole scanned window was readable and the scan was not cut."""
        return not self.unavailable and not self.scan_limit_reached


@dataclass(frozen=True)
class ExerciseHistory:
    exercise_id: str
    window_start: date
    window_end: date
    state: str                           # one of STATES
    occurrences: tuple                   # Occurrence, newest first, <= MAX_OCCURRENCES
    coverage: Coverage
