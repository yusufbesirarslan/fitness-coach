"""Frozen value objects + enums for the canonical workout-state contract.

Pure data holders (no DB, no Flask, no logic) so the resolver stays unit-testable
without an app context and the query layer can build inputs freely. Style mirrors
``app/services/training_planning/models.py``.

The dimensions model *distinct* concepts deliberately — schedule, execution,
plan-relationship, action and the single dominant state are never collapsed into
one overloaded boolean (see docs/WORKOUT_STATE.md).
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Additive-only contract version. Consumers may branch on it; PR1 == 1.
CONTRACT_VERSION = 1

# ── Normalized schedule kind (query layer owns the TR vocabulary; the resolver
#    stays vocabulary-free) ─────────────────────────────────────────────────
KIND_WORKOUT = "workout"   # plan 'tip' is antrenman/kardiyo
KIND_REST = "rest"         # plan 'tip' is dinlenme

# ── Dimension A: schedule state ──────────────────────────────────────────────
SCHEDULE_SCHEDULED = "scheduled"
SCHEDULE_REST_DAY = "rest_day"
SCHEDULE_NO_PLAN = "no_plan"
SCHEDULE_UNAVAILABLE = "schedule_unavailable"

# ── Dimension B: execution state (today only) ────────────────────────────────
EXEC_NONE = "no_execution"
EXEC_IN_PROGRESS = "in_progress"
EXEC_COMPLETED = "completed"

# ── Dimension C: plan ↔ performance relationship ─────────────────────────────
REL_MATCHES_SCHEDULED = "matches_scheduled"
REL_UNSCHEDULED = "unscheduled"
REL_UNRELATED_DATE = "unrelated_date"
REL_INDETERMINATE = "indeterminate"

# ── Dimension D: current action eligibility ──────────────────────────────────
# `complete` is intentionally NOT a distinct value: the client "complete" is a
# mutation (POST /workout/complete) reachable from an in-progress session, and no
# started-session is persisted server-side to gate a separate complete action.
# Adding it would be an unused state (docs/WORKOUT_STATE.md §Action).
ACTION_START = "start"
ACTION_RESUME = "resume"
ACTION_NONE = "none"
ACTION_BLOCKED = "blocked"

# ── Dimension E: dominant consumer state ─────────────────────────────────────
PRIMARY_REST_DAY = "rest_day"
PRIMARY_SCHEDULED_NOT_STARTED = "scheduled_not_started"
PRIMARY_IN_PROGRESS = "in_progress"
PRIMARY_COMPLETED = "completed"
PRIMARY_UNSCHEDULED_IN_PROGRESS = "unscheduled_in_progress"
PRIMARY_UNSCHEDULED_COMPLETED = "unscheduled_completed"
PRIMARY_NO_PLAN = "no_plan"
PRIMARY_NEEDS_ATTENTION = "needs_attention"

# ── Anomaly categories (safe operational labels — never PII/health data) ──────
ANOMALY_SCHEDULE_UNPARSEABLE = "schedule_unparseable"
ANOMALY_COMPLETION_MARKER_MISMATCH = "completion_marker_mismatch"
ANOMALY_RESOLUTION_ERROR = "resolution_error"


@dataclass(frozen=True)
class WorkoutStateInputs:
    """The trusted, already-loaded facts the pure resolver classifies over.

    Built by the query layer (impure); consumed by ``resolver.resolve`` (pure).
    Making this boundary explicit lets the whole state matrix be unit-tested
    without a DB — the resolver never touches the ORM or Flask.

    ``today_schedule_kind`` is ``KIND_WORKOUT`` / ``KIND_REST`` / ``None`` (today's
    weekday absent from the plan, or an unrecognized ``tip``). ``completed_today``
    is the canonical completion proof (today's PumpCheck); the marker flag only
    corroborates it. ``real_entry_count_today`` counts non-marker WorkoutLog rows
    — execution evidence, never proof of completion.
    """
    today: date
    has_plan: bool
    schedule_valid: bool                      # plan_data parsed to a 7-day program
    today_schedule_kind: Optional[str]        # KIND_WORKOUT | KIND_REST | None
    completed_today: bool                     # today's PumpCheck exists (canonical)
    has_completion_marker_today: bool         # corroborating WorkoutLog marker today
    real_entry_count_today: int               # non-marker WorkoutLog rows today
    stale_previous_workout: bool              # prior-day real rows w/ no completion


@dataclass(frozen=True)
class WorkoutStateSnapshot:
    """Deterministic, serializable snapshot of the user's current workout state.

    The single canonical answer API/client consumers read instead of recombining
    raw flags. ``primary_state`` is the one dominant field; the individual
    dimensions and diagnostics are exposed for consumers that need them.
    """
    today: date
    schedule_state: str
    execution_state: str
    plan_relationship: str
    action: str
    primary_state: str
    completed_today: bool
    is_rest_day: bool
    stale_previous_workout: bool
    anomaly: Optional[str] = None
    contract_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict:
        """Stable snake_case projection for JSON responses (additive-only)."""
        return {
            "contract_version": self.contract_version,
            "today": self.today.isoformat(),
            "schedule_state": self.schedule_state,
            "execution_state": self.execution_state,
            "plan_relationship": self.plan_relationship,
            "action": self.action,
            "primary_state": self.primary_state,
            "completed_today": self.completed_today,
            "is_rest_day": self.is_rest_day,
            "stale_previous_workout": self.stale_previous_workout,
            "anomaly": self.anomaly,
        }
