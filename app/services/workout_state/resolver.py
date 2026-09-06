"""Pure workout-state classification.

Given already-loaded ``WorkoutStateInputs`` (trusted persistence facts), derive
the deterministic ``WorkoutStateSnapshot``. No DB, no Flask, no side effects, no
external calls — every branch is a total function of the inputs, so the same
inputs always yield the same snapshot. All impure data access lives in
``queries.py``; all logging/error handling lives in ``__init__.py``.
"""
from dataclasses import replace

from .models import (
    ACTION_BLOCKED,
    ACTION_NONE,
    ACTION_RESUME,
    ACTION_START,
    ANOMALY_COMPLETION_MARKER_MISMATCH,
    ANOMALY_SCHEDULE_UNPARSEABLE,
    ANOMALY_SESSION_INCONSISTENT,
    CONTRACT_VERSION_SESSIONS,
    EXEC_COMPLETED,
    EXEC_IN_PROGRESS,
    EXEC_NONE,
    EXEC_RECORDED,
    KIND_REST,
    KIND_WORKOUT,
    PRIMARY_COMPLETED,
    PRIMARY_EXECUTION_RECORDED,
    PRIMARY_IN_PROGRESS,
    PRIMARY_NEEDS_ATTENTION,
    PRIMARY_NO_PLAN,
    PRIMARY_REST_DAY,
    PRIMARY_SCHEDULED_NOT_STARTED,
    PRIMARY_UNSCHEDULED_COMPLETED,
    PRIMARY_UNSCHEDULED_EXECUTION,
    REL_INDETERMINATE,
    REL_MATCHES_SCHEDULED,
    REL_UNRELATED_DATE,
    REL_UNSCHEDULED,
    SCHEDULE_NO_PLAN,
    SCHEDULE_REST_DAY,
    SCHEDULE_SCHEDULED,
    SCHEDULE_UNAVAILABLE,
    SESSION_STATE_ABANDONED,
    SESSION_STATE_ACTIVE_BLOCKED,
    SESSION_STATE_ACTIVE_RESUMABLE,
    SESSION_STATE_COMPLETED,
    SESSION_STATE_EXECUTION_WITHOUT_SESSION,
    SESSION_STATE_INCONSISTENT,
    SESSION_STATE_NONE,
    SESSION_STATUS_ABANDONED,
    SESSION_STATUS_ACTIVE,
    SESSION_STATUS_COMPLETED,
    ActiveSessionFacts,
    WorkoutStateInputs,
    WorkoutStateSnapshot,
)


def _schedule_state(i: WorkoutStateInputs) -> str:
    if not i.has_plan:
        return SCHEDULE_NO_PLAN
    if not i.schedule_valid:
        return SCHEDULE_UNAVAILABLE
    if i.today_schedule_kind == KIND_REST:
        return SCHEDULE_REST_DAY
    if i.today_schedule_kind == KIND_WORKOUT:
        return SCHEDULE_SCHEDULED
    # Structure parsed, but today's weekday is absent or its ``tip`` is
    # unrecognized — never silently assume a rest day (spec §5/§12).
    return SCHEDULE_UNAVAILABLE


def _execution_state(i: WorkoutStateInputs) -> str:
    # Completion (PumpCheck) is canonical and outranks raw exercise rows, so a
    # completed session with logged exercises is COMPLETED, never mere evidence.
    if i.completed_today:
        return EXEC_COMPLETED
    # Non-marker rows with no completion = RECORDED EXECUTION EVIDENCE only (they
    # can come from the AI-coach per-exercise tool, outside any interactive flow).
    # This is never "completed" and never an active/resumable session — the server
    # persists no started session, so no `resume` is derivable from it.
    if i.real_entry_count_today > 0:
        return EXEC_RECORDED
    return EXEC_NONE


def _plan_relationship(i: WorkoutStateInputs, schedule_state: str,
                       execution_state: str) -> str:
    # Association is by local-date + schedule kind only; WorkoutLog carries no
    # planned-workout identifier, so identifier-level matching is not derivable
    # (documented gap — docs/WORKOUT_STATE.md).
    if execution_state in (EXEC_COMPLETED, EXEC_RECORDED):
        if schedule_state == SCHEDULE_SCHEDULED:
            return REL_MATCHES_SCHEDULED
        if schedule_state in (SCHEDULE_REST_DAY, SCHEDULE_NO_PLAN):
            return REL_UNSCHEDULED
        return REL_INDETERMINATE  # schedule_unavailable
    # No execution today: the only performed evidence (if any) is from another
    # date — a historical record must never be treated as today's workout.
    if i.stale_previous_workout:
        return REL_UNRELATED_DATE
    return REL_INDETERMINATE


def _action(schedule_state: str, execution_state: str) -> str:
    # Inconsistent/unavailable schedule → grant no action (spec §6/§12): a
    # malformed schedule can never yield START (an unsafe "begin your workout").
    if schedule_state == SCHEDULE_UNAVAILABLE:
        return ACTION_BLOCKED
    if execution_state == EXEC_COMPLETED:
        return ACTION_NONE
    if execution_state == EXEC_RECORDED:
        # Recorded evidence, but the server holds NO resumable session — it must
        # not fabricate a `resume` (over-claims an active session) nor a `start`
        # (ignores the evidence). Offer no directed action; the client "complete"
        # affordance is a separate mutation, always available.
        return ACTION_NONE
    # no_execution:
    if schedule_state == SCHEDULE_SCHEDULED:
        return ACTION_START
    return ACTION_NONE  # rest_day / no_plan with nothing done


def _primary_state(schedule_state: str, execution_state: str,
                   plan_relationship: str) -> str:
    if schedule_state == SCHEDULE_UNAVAILABLE:
        return PRIMARY_NEEDS_ATTENTION
    if execution_state == EXEC_COMPLETED:
        return (PRIMARY_UNSCHEDULED_COMPLETED
                if plan_relationship == REL_UNSCHEDULED else PRIMARY_COMPLETED)
    if execution_state == EXEC_RECORDED:
        return (PRIMARY_UNSCHEDULED_EXECUTION
                if plan_relationship == REL_UNSCHEDULED else PRIMARY_EXECUTION_RECORDED)
    # no_execution:
    if schedule_state == SCHEDULE_NO_PLAN:
        return PRIMARY_NO_PLAN
    if schedule_state == SCHEDULE_REST_DAY:
        return PRIMARY_REST_DAY
    return PRIMARY_SCHEDULED_NOT_STARTED  # scheduled, nothing done


def _anomaly(i: WorkoutStateInputs, schedule_state: str) -> str | None:
    # A plan exists but its schedule cannot be read → surface for repair, but the
    # resolver still returns a safe (blocked) state rather than a misleading one.
    if schedule_state == SCHEDULE_UNAVAILABLE and i.has_plan:
        return ANOMALY_SCHEDULE_UNPARSEABLE
    # Completion proof and its corroborating marker disagree (one writer wrote a
    # PumpCheck without a marker, or vice-versa). PumpCheck stays canonical; we
    # only flag the inconsistency for a later reliability PR.
    if i.completed_today != i.has_completion_marker_today:
        return ANOMALY_COMPLETION_MARKER_MISMATCH
    return None


def resolve(i: WorkoutStateInputs) -> WorkoutStateSnapshot:
    """Classify trusted inputs into the canonical snapshot (pure/deterministic)."""
    schedule_state = _schedule_state(i)
    execution_state = _execution_state(i)
    plan_relationship = _plan_relationship(i, schedule_state, execution_state)
    action = _action(schedule_state, execution_state)
    primary_state = _primary_state(schedule_state, execution_state, plan_relationship)
    return WorkoutStateSnapshot(
        today=i.today,
        schedule_state=schedule_state,
        execution_state=execution_state,
        plan_relationship=plan_relationship,
        action=action,
        primary_state=primary_state,
        completed_today=i.completed_today,
        is_rest_day=schedule_state == SCHEDULE_REST_DAY,
        stale_previous_workout=i.stale_previous_workout,
        anomaly=_anomaly(i, schedule_state),
    )


# ── Sprint 7 PR3: flag-conditional v2 session enrichment (PURE) ───────────────
def _session_projection(base: WorkoutStateSnapshot, facts: ActiveSessionFacts):
    """Return ``(session_state, action, execution_state, primary_state)`` for the
    v2 contract. ``resume``/``in_progress`` are produced ONLY for an ELIGIBLE
    (resumable) ACTIVE session — never from evidence-only WorkoutLog; and ``start``
    is never emitted when an ACTIVE session exists (a conflicting active session
    blocks a fresh start)."""
    if not facts.present or facts.status is None:
        # No persisted session. Evidence-only execution is labeled as such, but it
        # can never become resume/in_progress — the base action/state are kept.
        if base.execution_state == EXEC_RECORDED:
            return (SESSION_STATE_EXECUTION_WITHOUT_SESSION,
                    base.action, base.execution_state, base.primary_state)
        return SESSION_STATE_NONE, base.action, base.execution_state, base.primary_state

    if facts.status == SESSION_STATUS_ACTIVE:
        if base.completed_today:
            # Confirmed completion (PumpCheck) with a still-ACTIVE session — a
            # lifecycle inconsistency. The day IS done: keep the completed base
            # action/state (never offer resume), only flag the inconsistency.
            return (SESSION_STATE_INCONSISTENT,
                    base.action, base.execution_state, base.primary_state)
        if facts.resumable:
            return (SESSION_STATE_ACTIVE_RESUMABLE,
                    ACTION_RESUME, EXEC_IN_PROGRESS, PRIMARY_IN_PROGRESS)
        # ACTIVE but not resumable (stale / requires explicit resolution): never a
        # safe resume, never a fresh start — block until the user resolves it.
        return (SESSION_STATE_ACTIVE_BLOCKED,
                ACTION_BLOCKED, base.execution_state, PRIMARY_NEEDS_ATTENTION)

    if facts.status == SESSION_STATUS_COMPLETED:
        return SESSION_STATE_COMPLETED, base.action, base.execution_state, base.primary_state
    if facts.status == SESSION_STATUS_ABANDONED:
        return SESSION_STATE_ABANDONED, base.action, base.execution_state, base.primary_state
    # Unknown status → treat as no directed session influence (fail safe).
    return SESSION_STATE_NONE, base.action, base.execution_state, base.primary_state


def _session_dict(facts: ActiveSessionFacts):
    if not facts.present or facts.status is None:
        return None
    return {
        "public_id": facts.public_id,
        "status": facts.status,
        "resumable": facts.resumable,
        "relationship": facts.relationship,
        "stale_reason": facts.stale_reason,
        "workout_date": facts.workout_date,
    }


def enrich_with_session(
    base: WorkoutStateSnapshot, facts: ActiveSessionFacts
) -> WorkoutStateSnapshot:
    """Pure v2 projection: fold persisted-session truth into ``base`` and advance
    the contract to ``CONTRACT_VERSION_SESSIONS``. Returns a NEW snapshot; ``base``
    (the exact PR1 v1 snapshot) is never mutated, so the flag-OFF path that skips
    this call stays byte-identical."""
    session_state, action, execution_state, primary_state = _session_projection(base, facts)
    anomaly = base.anomaly
    if anomaly is None and session_state == SESSION_STATE_INCONSISTENT:
        anomaly = ANOMALY_SESSION_INCONSISTENT
    return replace(
        base,
        contract_version=CONTRACT_VERSION_SESSIONS,
        execution_state=execution_state,
        action=action,
        primary_state=primary_state,
        session_state=session_state,
        session=_session_dict(facts),
        execution_view=facts.execution_view,
        anomaly=anomaly,
    )
