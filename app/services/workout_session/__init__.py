"""Canonical persisted workout-session lifecycle (Sprint 7 PR3).

The single server-owned owner of workout-session identity + lifecycle: start,
idempotent replay, current-session read, safe resume eligibility, heartbeat
checkpoint, explicit abandonment, stale classification/recovery, and completion
delegated to the PR2 canonical mutation. A persisted ACTIVE row here is the only
thing that makes a workout "resumable" — a WorkoutLog row is execution evidence
only, and localStorage is an untrusted cache.

Layering mirrors ``workout_state`` / ``workout_completion``:
``queries`` (impure reads/writes) + ``service`` (transactions) + ``models``
(framework-free value objects + pure classification).

Rollout: gated by ``FITX_WORKOUT_SESSIONS_ENABLED`` (default OFF) at the route and
resolver boundary — the service itself always enforces ownership.
"""
from .models import (
    FINGERPRINT_VERSION,
    REL_INDETERMINATE,
    REL_MATCHES_CURRENT_PLAN,
    REL_PLAN_MISSING,
    REL_PLAN_REGENERATED,
    REL_SCHEDULE_SLOT_CHANGED,
    REL_UNSCHEDULED,
    SOURCE_SCHEDULED,
    SOURCE_UNSCHEDULED,
    STALE_INDETERMINATE,
    STALE_LIFECYCLE_INCONSISTENT,
    STALE_NONE,
    STALE_PLAN_MISSING,
    STALE_PLAN_REGENERATED,
    STALE_PREVIOUS_DAY,
    STALE_SCHEDULE_SLOT_CHANGED,
    SessionOutcome,
    SessionResult,
    SessionView,
    compute_fingerprint,
)
from .service import (
    abandon_session,
    build_session_view,
    checkpoint_session,
    complete_session,
    get_current_session,
    read_session_for_state,
    resolve_for_completion,
    resume_session,
    start_session,
)

__all__ = [
    # service API
    "start_session",
    "get_current_session",
    "resume_session",
    "checkpoint_session",
    "abandon_session",
    "complete_session",
    "resolve_for_completion",
    "build_session_view",
    "read_session_for_state",
    # outcome + projection
    "SessionOutcome",
    "SessionResult",
    "SessionView",
    # fingerprint + vocabulary
    "compute_fingerprint",
    "FINGERPRINT_VERSION",
    "SOURCE_SCHEDULED",
    "SOURCE_UNSCHEDULED",
    "REL_MATCHES_CURRENT_PLAN",
    "REL_PLAN_REGENERATED",
    "REL_PLAN_MISSING",
    "REL_SCHEDULE_SLOT_CHANGED",
    "REL_UNSCHEDULED",
    "REL_INDETERMINATE",
    "STALE_NONE",
    "STALE_PREVIOUS_DAY",
    "STALE_PLAN_MISSING",
    "STALE_PLAN_REGENERATED",
    "STALE_SCHEDULE_SLOT_CHANGED",
    "STALE_LIFECYCLE_INCONSISTENT",
    "STALE_INDETERMINATE",
]
