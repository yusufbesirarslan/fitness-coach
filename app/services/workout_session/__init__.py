"""Canonical persisted workout-session lifecycle (Sprint 7 PR3).

The single server-owned owner of workout-session identity + lifecycle: start,
idempotent replay, current-session read, safe resume eligibility, heartbeat
checkpoint, explicit abandonment, stale classification/recovery, and completion
delegated to the PR2 canonical mutation. A persisted ACTIVE row here is the only
thing that makes a workout "resumable" — a WorkoutLog row is execution evidence
only, and localStorage is an untrusted cache.

Since Sprint 14 PR2 it is also the single **execution** authority shared by both
server transports: the browser web transport and the native ``/api/v1``
transport observe the same session identity, the same checkpoint revision, the
same durable snapshot and the same completion revision authority. Neither owns
an execution state machine of its own; each owns only its auth adapter, its
request parsing and how it renders a typed failure.

Layering mirrors ``workout_state`` / ``workout_completion``:
``queries`` (impure reads/writes) + ``service`` (lifecycle transactions) +
``execution`` (the shared checkpoint command) + ``checkpoint`` (the pure request
contract: bounds, canonical ordering, replay fingerprint) + ``errors`` (the one
typed failure vocabulary) + ``models`` (framework-free value objects + pure
classification).

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
from .checkpoint import (
    MAX_ELAPSED_SECONDS,
    MAX_EXERCISES,
    MAX_REPS,
    MAX_REVISION,
    MAX_SETS_PER_EXERCISE,
    MAX_SNAPSHOT_BYTES,
    MAX_WEIGHT_KG,
    Checkpoint,
    load_snapshot,
    parse_checkpoint,
    parse_idempotency_key,
    parse_optional_revision,
    parse_reason,
    parse_revision,
    parse_revision_value,
)
from .errors import (
    ActiveSessionExists,
    CompletionRejected,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    InvalidRevision,
    InvalidSessionRequest,
    NoActiveSession,
    RevisionConflict,
    SessionCommandError,
    SessionNotFound,
    SessionPersistenceUnavailable,
    SessionStale,
    SessionTerminal,
    WorkoutNotStartable,
)
from .queries import (
    NativeWorkoutIdentity,
    PlannedWorkout,
    advance_checkpoint,
    get_owned_session,
    planned_workout_for_slot,
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

from .execution import (  # noqa: E402 — imports .service above
    SESSION_REF_MAX,
    CheckpointResult,
    owned_session,
    planned_exercise_identities,
    prepare_completion,
    record_checkpoint,
    reject_terminal,
    require_revision,
)

__all__ = [
    # service API
    "start_session",
    # PR5 native execution primitives
    "NativeWorkoutIdentity",
    "advance_checkpoint",
    "get_owned_session",
    "PlannedWorkout",
    "planned_workout_for_slot",
    # Sprint 14 PR2 shared execution authority
    "record_checkpoint",
    "prepare_completion",
    "CheckpointResult",
    "owned_session",
    "reject_terminal",
    "require_revision",
    "planned_exercise_identities",
    "SESSION_REF_MAX",
    # request contract
    "Checkpoint",
    "load_snapshot",
    "parse_checkpoint",
    "parse_idempotency_key",
    "parse_revision",
    "parse_revision_value",
    "parse_optional_revision",
    "parse_reason",
    "MAX_EXERCISES",
    "MAX_SETS_PER_EXERCISE",
    "MAX_REPS",
    "MAX_WEIGHT_KG",
    "MAX_ELAPSED_SECONDS",
    "MAX_SNAPSHOT_BYTES",
    "MAX_REVISION",
    # typed failure vocabulary
    "SessionCommandError",
    "InvalidSessionRequest",
    "InvalidIdempotencyKey",
    "InvalidRevision",
    "SessionNotFound",
    "NoActiveSession",
    "WorkoutNotStartable",
    "ActiveSessionExists",
    "SessionTerminal",
    "SessionStale",
    "RevisionConflict",
    "IdempotencyConflict",
    "CompletionRejected",
    "SessionPersistenceUnavailable",
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
