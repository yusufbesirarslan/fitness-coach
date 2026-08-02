"""Transaction-owning orchestration for the persisted workout-session lifecycle
(Sprint 7 PR3).

Every transition authenticates through the caller (``user_id`` is always the
trusted current user), verifies ownership, loads the current persisted state,
validates the transition, enforces concurrency safety at the DB level and commits
or rolls back at one clear boundary — never returning success before commit.

Design invariants (see docs/WORKOUT_STATE.md "Sprint 7 PR3"):
  * The active-session partial unique index is the single concurrency-safe claim.
  * Reads NEVER mutate (get_current / resume-of-a-stale-session do not write).
  * The heartbeat is a lock-free, replay-idempotent conditional update.
  * Completion delegates to the PR2 canonical mutation; this module never creates
    a PumpCheck / marker itself.
  * "Stale" is derived from concrete lifecycle/relationship evidence — never from
    elapsed inactivity.
"""
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
)
from app.observability import current_request_id
from app.timeutil import app_today

from .models import (
    HEARTBEAT_COALESCE_SECONDS,
    STALE_NONE,
    RelationshipInputs,
    SessionOutcome,
    SessionResult,
    SessionView,
    _iso,
    classify,
    classify_relationship,
)
from .queries import (
    PlanSnapshot,
    compute_plan_snapshot,
    completed_today,
    current_plan_facts,
    get_active_session,
    get_latest_session_for_day,
    get_owned_session,
    heartbeat,
    insert_active_session,
    is_active_session_owner_violation,
    terminalize_abandon,
    touch_active,
)

__all__ = [
    "start_session",
    "get_current_session",
    "resume_session",
    "checkpoint_session",
    "abandon_session",
    "complete_session",
    "resolve_for_completion",
    "build_session_view",
    "read_session_for_state",
]


def _build_view(session, today: date) -> SessionView:
    """Read-only projection + derived classification. Performs NO writes."""
    facts = current_plan_facts(session.user_id, session.weekday_slot)
    if session.status == WORKOUT_SESSION_ACTIVE:
        cls = classify(RelationshipInputs(
            status=session.status,
            workout_date=session.workout_date,
            source=session.source,
            stored_fingerprint=session.plan_fingerprint,
            today=today,
            completed_today=completed_today(session.user_id, today),
            has_current_plan=facts.has_current_plan,
            current_weekday_is_workout=facts.current_weekday_is_workout,
            current_fingerprint=facts.current_fingerprint,
        ))
        relationship, stale_reason, resumable = (
            cls.relationship, cls.stale_reason, cls.resumable)
    else:
        relationship = classify_relationship(
            source=session.source,
            has_current_plan=facts.has_current_plan,
            current_weekday_is_workout=facts.current_weekday_is_workout,
            stored_fingerprint=session.plan_fingerprint,
            current_fingerprint=facts.current_fingerprint,
        )
        stale_reason, resumable = STALE_NONE, False
    return SessionView(
        public_id=session.public_id,
        status=session.status,
        workout_date=session.workout_date,
        weekday_slot=session.weekday_slot,
        source=session.source,
        started_at=_iso(session.started_at),
        last_activity_at=_iso(session.last_activity_at),
        completed_at=_iso(session.completed_at),
        abandoned_at=_iso(session.abandoned_at),
        terminal_reason=session.terminal_reason,
        relationship=relationship,
        stale_reason=stale_reason,
        resumable=resumable,
    )


# Public alias (the resolver / route read the view without importing a private name).
def build_session_view(session, today: date) -> SessionView:
    return _build_view(session, today)


def _same_intended_workout(existing, today: date, snapshot: PlanSnapshot) -> bool:
    """A genuine idempotent-start replay targets the same day + slot + source."""
    return (
        existing.workout_date == today.isoformat()
        and existing.weekday_slot == snapshot.weekday_slot
        and existing.source == snapshot.source
    )


def _existing_or_conflict(existing, today: date, snapshot: PlanSnapshot) -> SessionResult:
    view = _build_view(existing, today)
    if _same_intended_workout(existing, today, snapshot):
        return SessionResult(SessionOutcome.EXISTING_ACTIVE, session=view)
    return SessionResult(SessionOutcome.CONFLICT, session=view)


def start_session(user_id: int, *, today: Optional[date] = None) -> SessionResult:
    """Start (or idempotently replay) the user's current workout session.

    Server-derives the user-local date + plan snapshot/fingerprint; never trusts
    client-supplied ownership or plan content. The insert is the atomic claim: a
    partial-index conflict means an active session already exists — the same
    intended workout replays as EXISTING_ACTIVE, a different one is a CONFLICT.
    """
    day = today or app_today()
    now = datetime.utcnow()
    snapshot = compute_plan_snapshot(user_id, day)

    existing = get_active_session(user_id)
    if existing is not None:
        return _existing_or_conflict(existing, day, snapshot)

    try:
        session = insert_active_session(user_id, day, snapshot, now)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if is_active_session_owner_violation(exc):
            existing = get_active_session(user_id)
            if existing is not None:
                return _existing_or_conflict(existing, day, snapshot)
            # Violation but no active row visible now — treat conservatively.
            _log("start_conflict", user_id)
            return SessionResult(SessionOutcome.CONFLICT)
        _log("start_integrity_error", user_id)
        raise
    _log("started", user_id)
    return SessionResult(SessionOutcome.CREATED, session=_build_view(session, day))


def get_current_session(user_id: int, *, today: Optional[date] = None) -> SessionResult:
    """Read-only: the user's current ACTIVE session + derived classification.

    Never mutates; a stale/blocked session is reported (via the view) but not
    auto-abandoned or terminalized.
    """
    day = today or app_today()
    session = get_active_session(user_id)
    if session is None:
        return SessionResult(SessionOutcome.NOT_FOUND)
    return SessionResult(SessionOutcome.EXISTING_ACTIVE, session=_build_view(session, day))


def read_session_for_state(
    user_id: int, *, today: Optional[date] = None
) -> Optional[SessionView]:
    """Read-only projection of the session most relevant to today's workout state,
    for the PR1 resolver's flag-ON enrichment: the current ACTIVE session if one
    exists (regardless of its start date — a previous-day active session is still
    the relevant one), else the latest session that STARTED today (terminal), else
    ``None``. Never mutates."""
    day = today or app_today()
    active = get_active_session(user_id)
    if active is not None:
        return _build_view(active, day)
    latest = get_latest_session_for_day(user_id, day.isoformat())
    return _build_view(latest, day) if latest is not None else None


def resume_session(user_id: int, public_id: str, *, today: Optional[date] = None) -> SessionResult:
    """Resume an owned ACTIVE session iff it is eligible (same-day + relationship
    matches / unscheduled). A terminal or stale session yields a deterministic
    outcome and is PRESERVED — never silently re-attached or auto-resolved."""
    day = today or app_today()
    session = get_owned_session(user_id, public_id)
    if session is None:
        return SessionResult(SessionOutcome.NOT_FOUND)
    if session.status == WORKOUT_SESSION_COMPLETED:
        return SessionResult(SessionOutcome.ALREADY_COMPLETED, session=_build_view(session, day))
    if session.status == WORKOUT_SESSION_ABANDONED:
        return SessionResult(SessionOutcome.ALREADY_ABANDONED, session=_build_view(session, day))

    view = _build_view(session, day)
    if not view.resumable:
        _log("resume_stale", user_id)
        return SessionResult(SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION, session=view)

    # Eligible: an explicit resume may bump last_activity_at (conditional on ACTIVE
    # so it can never resurrect a just-terminalized session).
    touch_active(user_id, public_id, datetime.utcnow())
    session = get_owned_session(user_id, public_id)
    _log("resumed", user_id)
    return SessionResult(SessionOutcome.RESUMED, session=_build_view(session, day))


def checkpoint_session(user_id: int, public_id: str) -> SessionResult:
    """Replay-idempotent heartbeat. Touches ONLY ``last_activity_at``; a retry
    inside the coalesce window is a success no-op, never a false conflict."""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=HEARTBEAT_COALESCE_SECONDS)
    if heartbeat(user_id, public_id, now, cutoff) > 0:
        return SessionResult(SessionOutcome.CHECKPOINTED)
    # rowcount 0 → either coalesced (recent heartbeat) or not-active/not-found.
    session = get_owned_session(user_id, public_id)
    if session is None:
        return SessionResult(SessionOutcome.NOT_FOUND)
    if session.status == WORKOUT_SESSION_COMPLETED:
        return SessionResult(SessionOutcome.ALREADY_COMPLETED)
    if session.status == WORKOUT_SESSION_ABANDONED:
        return SessionResult(SessionOutcome.ALREADY_ABANDONED)
    # Active but coalesced — idempotent success (no write amplification).
    return SessionResult(SessionOutcome.CHECKPOINTED)


def abandon_session(
    user_id: int, public_id: str, *, reason: Optional[str] = None
) -> SessionResult:
    """Explicitly abandon an owned ACTIVE session. Idempotent; preserves the row
    and all WorkoutLog/PumpCheck evidence (never physically deletes)."""
    day = app_today()
    session = get_owned_session(user_id, public_id)
    if session is None:
        return SessionResult(SessionOutcome.NOT_FOUND)
    if session.status == WORKOUT_SESSION_ABANDONED:
        return SessionResult(SessionOutcome.ALREADY_ABANDONED, session=_build_view(session, day))
    if session.status == WORKOUT_SESSION_COMPLETED:
        return SessionResult(SessionOutcome.ALREADY_COMPLETED, session=_build_view(session, day))

    if terminalize_abandon(user_id, public_id, reason, datetime.utcnow()) == 0:
        # Lost the race to a concurrent terminal transition — report the winner.
        session = get_owned_session(user_id, public_id)
        if session is not None and session.status == WORKOUT_SESSION_COMPLETED:
            return SessionResult(SessionOutcome.ALREADY_COMPLETED, session=_build_view(session, day))
        view = _build_view(session, day) if session is not None else None
        return SessionResult(SessionOutcome.ALREADY_ABANDONED, session=view)

    session = get_owned_session(user_id, public_id)
    _log("abandoned", user_id)
    return SessionResult(SessionOutcome.ABANDONED, session=_build_view(session, day))


def resolve_for_completion(
    user_id: int, public_id: str, today: date
) -> Tuple[Optional[int], Optional[SessionResult]]:
    """Ownership-checked resolution of a public id to an internal session id for
    the completion route. Returns ``(session_id, None)`` to proceed, or
    ``(None, error_result)`` for a stale/terminal/not-found session. ``today`` is
    the completion caller's authoritative day; this resolver never recalculates
    it independently."""
    session = get_owned_session(user_id, public_id)
    if session is None:
        return None, SessionResult(SessionOutcome.NOT_FOUND)
    if session.workout_date != today.isoformat():
        return None, SessionResult(
            SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION,
            session=_build_view(session, today),
        )
    if session.status == WORKOUT_SESSION_ABANDONED:
        return None, SessionResult(
            SessionOutcome.ALREADY_ABANDONED, session=_build_view(session, today))
    # ACTIVE or already COMPLETED → proceed; complete_workout is idempotent and
    # reconciles a duplicate into ALREADY_COMPLETED without duplicate side effects.
    return session.id, None


def complete_session(
    user_id: int, public_id: str, *, today: Optional[date] = None, **completion_kwargs
) -> SessionResult:
    """Complete a session THROUGH the PR2 canonical completion authority.

    Never creates a PumpCheck/marker itself: it builds a session-scoped
    :class:`CompleteWorkoutCommand` and delegates to ``complete_workout``, which
    terminalizes the session atomically with the completion artifacts.
    """
    from app.services.workout_completion import (
        CompleteWorkoutCommand,
        SessionCompletionConflict,
        complete_workout,
    )

    day = today or app_today()
    session_id, error = resolve_for_completion(user_id, public_id, day)
    if error is not None:
        return error

    completion_kwargs.setdefault("entry_path", "session_service")
    command = CompleteWorkoutCommand(
        user_id=user_id, today=day, session_id=session_id, **completion_kwargs
    )
    try:
        completion = complete_workout(command)
    except SessionCompletionConflict:
        session = get_owned_session(user_id, public_id)
        view = _build_view(session, day) if session is not None else None
        _log("complete_conflict", user_id)
        return SessionResult(SessionOutcome.CONFLICT, session=view)

    session = get_owned_session(user_id, public_id)
    view = _build_view(session, day) if session is not None else None
    outcome = (
        SessionOutcome.COMPLETED if completion.created
        else SessionOutcome.ALREADY_COMPLETED
    )
    _log("completed" if completion.created else "already_completed", user_id)
    return SessionResult(outcome, session=view, completion=completion)


def _log(event: str, user_id: int) -> None:
    """Safe, bounded operational log — no PII/workout content. Best-effort."""
    try:
        current_app.logger.info(
            "[WORKOUT_SESSION] rid=%s event=%s user_id=%s",
            current_request_id(), event, user_id,
        )
    except Exception:  # noqa: BLE001 — logging must never break a transition
        pass
