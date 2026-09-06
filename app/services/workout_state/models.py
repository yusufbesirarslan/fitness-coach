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
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.workout_session.models import SessionView

# Additive-only contract version. Consumers may branch on it; PR1 == 1.
# When the persisted workout-session lifecycle is enabled (Sprint 7 PR3,
# ``FITX_WORKOUT_SESSIONS_ENABLED``) the resolver emits ``CONTRACT_VERSION_SESSIONS``
# with the additive session-aware fields below. With the flag OFF the snapshot is
# byte-identical to the PR1 ``CONTRACT_VERSION`` contract (no session keys, no new
# enum values) — see docs/WORKOUT_STATE.md "Sprint 7 PR3".
CONTRACT_VERSION = 1
CONTRACT_VERSION_SESSIONS = 2

# ── Normalized schedule kind (query layer owns the TR vocabulary; the resolver
#    stays vocabulary-free) ─────────────────────────────────────────────────
KIND_WORKOUT = "workout"   # plan 'tip' is antrenman/kardiyo
KIND_REST = "rest"         # plan 'tip' is dinlenme

# ── Dimension A: schedule state ──────────────────────────────────────────────
SCHEDULE_SCHEDULED = "scheduled"
SCHEDULE_REST_DAY = "rest_day"
SCHEDULE_NO_PLAN = "no_plan"
SCHEDULE_UNAVAILABLE = "schedule_unavailable"

# ── Session-aware additions (Sprint 7 PR3; emitted ONLY at CONTRACT_VERSION_SESSIONS)
# ``in_progress`` and ``resume`` are producible ONLY from a persisted ELIGIBLE
# ACTIVE WorkoutSession — never from evidence-only WorkoutLog rows (which stay
# ``execution_recorded`` / ``none``). This is the whole point of PR3: a resumable
# session is a server-owned fact, not an inference from execution evidence.
EXEC_IN_PROGRESS = "in_progress"        # an eligible ACTIVE session exists
ACTION_RESUME = "resume"                # resume that eligible ACTIVE session
PRIMARY_IN_PROGRESS = "in_progress"     # dominant state: a resumable session

# Derived session lifecycle state for the v2 contract (read-only classification of
# the persisted session, never a mutation): no session · resumable · blocked/stale
# · completed · abandoned · execution-evidence-without-session · inconsistent.
SESSION_STATE_NONE = "none"
SESSION_STATE_ACTIVE_RESUMABLE = "active_resumable"
SESSION_STATE_ACTIVE_BLOCKED = "active_blocked"
SESSION_STATE_COMPLETED = "completed"
SESSION_STATE_ABANDONED = "abandoned"
SESSION_STATE_EXECUTION_WITHOUT_SESSION = "execution_without_session"
SESSION_STATE_INCONSISTENT = "inconsistent"

# Persisted WorkoutSession status values, mirrored BY VALUE from
# app.models.WORKOUT_SESSION_* so this pure layer imports no ORM (a drift guard in
# tests asserts they stay equal). Never compare against ORM constants here.
SESSION_STATUS_ACTIVE = "active"
SESSION_STATUS_COMPLETED = "completed"
SESSION_STATUS_ABANDONED = "abandoned"

# ── Dimension B: execution state (today only) ────────────────────────────────
# ``execution_recorded`` means non-marker WorkoutLog rows exist for today — this
# is RECORDED EXECUTION EVIDENCE, NOT proof of an active, interrupted or resumable
# session. No interactive workout session is persisted server-side (the client
# session in static/training.js is ephemeral/in-memory), and these rows can be
# written entirely outside any interactive flow — e.g. the AI-coach
# ``commit_workout_log`` per-exercise tool (app/services/ai_coach.py:417). The
# contract therefore never derives a resumable "in progress" session from them
# (see docs/WORKOUT_STATE.md "Execution evidence vs. active session").
EXEC_NONE = "no_execution"
EXEC_RECORDED = "execution_recorded"
EXEC_COMPLETED = "completed"

# ── Dimension C: plan ↔ performance relationship ─────────────────────────────
REL_MATCHES_SCHEDULED = "matches_scheduled"
REL_UNSCHEDULED = "unscheduled"
REL_UNRELATED_DATE = "unrelated_date"
REL_INDETERMINATE = "indeterminate"

# ── Dimension D: current action eligibility ──────────────────────────────────
# Deliberately only START / NONE / BLOCKED. Two client-side affordances are
# intentionally ABSENT from the read model:
#   * ``complete`` — the client "complete" is a mutation (POST /workout/complete);
#     no started-session is persisted server-side to gate it as a read-model action.
#   * ``resume`` — resuming presupposes trusted persisted evidence that a *resumable*
#     session exists. None is persisted (recorded execution evidence is NOT a
#     resumable session — see EXEC_RECORDED), so the contract MUST NOT emit
#     ``resume``. It would over-claim an active session the server cannot prove
#     (docs/WORKOUT_STATE.md §Action).
ACTION_START = "start"
ACTION_NONE = "none"
ACTION_BLOCKED = "blocked"

# ── Dimension E: dominant consumer state ─────────────────────────────────────
PRIMARY_REST_DAY = "rest_day"
PRIMARY_SCHEDULED_NOT_STARTED = "scheduled_not_started"
PRIMARY_EXECUTION_RECORDED = "execution_recorded"        # evidence today, not confirmed done
PRIMARY_COMPLETED = "completed"
PRIMARY_UNSCHEDULED_EXECUTION = "unscheduled_execution"  # evidence off-schedule, not confirmed done
PRIMARY_UNSCHEDULED_COMPLETED = "unscheduled_completed"
PRIMARY_NO_PLAN = "no_plan"
PRIMARY_NEEDS_ATTENTION = "needs_attention"

# Additive canonical vocabulary guard for consumers that must be total over the
# dominant state dimension.  The owner exports the set; consumers still own how
# each state is presented or acted upon.
CANONICAL_PRIMARY_STATES = (
    PRIMARY_IN_PROGRESS,
    PRIMARY_REST_DAY,
    PRIMARY_SCHEDULED_NOT_STARTED,
    PRIMARY_EXECUTION_RECORDED,
    PRIMARY_COMPLETED,
    PRIMARY_UNSCHEDULED_EXECUTION,
    PRIMARY_UNSCHEDULED_COMPLETED,
    PRIMARY_NO_PLAN,
    PRIMARY_NEEDS_ATTENTION,
)

# ── Anomaly categories (safe operational labels — never PII/health data) ──────
ANOMALY_SCHEDULE_UNPARSEABLE = "schedule_unparseable"
ANOMALY_COMPLETION_MARKER_MISMATCH = "completion_marker_mismatch"
ANOMALY_RESOLUTION_ERROR = "resolution_error"
# Session lifecycle disagrees with completion proof: the day has a confirmed
# PumpCheck completion while a session for it is still ACTIVE (Sprint 7 PR3).
ANOMALY_SESSION_INCONSISTENT = "session_lifecycle_inconsistent"
# The session read failed while enriching a v2 snapshot — the base state stays
# valid; the session dimension is reported empty and this is logged.
ANOMALY_SESSION_READ_ERROR = "session_read_error"


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
    — recorded execution evidence only: never proof of completion, and never proof
    of an active or resumable session (those rows can be written outside any
    interactive workout flow).
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
class ActiveSessionFacts:
    """Trusted, already-loaded persisted-session facts for the v2 enrichment.

    Built by the query layer from the ``workout_session`` read model (impure);
    consumed by ``resolver.enrich_with_session`` (pure). ``status`` is the raw
    persisted status string (``SESSION_STATUS_*``) or ``None`` when no session is
    relevant to today. ``resumable`` is only meaningful when ``status`` is active.
    """
    present: bool
    status: Optional[str]
    resumable: bool
    public_id: Optional[str]
    workout_date: Optional[str]
    relationship: Optional[str]
    stale_reason: Optional[str]
    # Carried intact for read composition; classification uses only facts above.
    execution_view: Optional["SessionView"] = None


# The "no session at all" facts — used when the flag is on but nothing is
# persisted, or when the session read fails (fail-safe empty dimension).
ACTIVE_SESSION_FACTS_NONE = ActiveSessionFacts(
    present=False, status=None, resumable=False, public_id=None,
    workout_date=None, relationship=None, stale_reason=None,
)


@dataclass(frozen=True)
class WorkoutStateSnapshot:
    """Deterministic, serializable snapshot of the user's current workout state.

    The single canonical answer API/client consumers read instead of recombining
    raw flags. ``primary_state`` is the one dominant field; the individual
    dimensions and diagnostics are exposed for consumers that need them.

    ``session_state`` / ``session`` are the additive Sprint 7 PR3 dimensions and
    are serialized ONLY at ``CONTRACT_VERSION_SESSIONS`` — at ``CONTRACT_VERSION``
    (flag OFF) they are absent from ``to_dict`` so the projection stays byte-for-
    byte identical to the PR1 contract.
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
    session_state: str = SESSION_STATE_NONE
    session: Optional[dict] = None
    # Durable execution has its own authority and is not part of state.to_dict().
    execution_view: Optional["SessionView"] = None

    def to_dict(self) -> dict:
        """Stable snake_case projection for JSON responses (additive-only).

        At ``CONTRACT_VERSION`` this is exactly the PR1 key set; the session keys
        appear only once the contract has advanced to ``CONTRACT_VERSION_SESSIONS``.
        """
        data = {
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
        if self.contract_version >= CONTRACT_VERSION_SESSIONS:
            data["session_state"] = self.session_state
            data["session"] = self.session
        return data
