"""Native workout-session write contracts (Mobile Training PR5).

The bearer-authenticated write boundary for real native workout execution:
start, current, resume, durable checkpoint, abandon and complete. Layering
mirrors the rest of the mobile surface -- ``checkpoint`` (pure request contract),
``projection`` (pure public shape), ``errors`` (typed public vocabulary) and
``service`` (the adapter over canonical authorities).

Authority stays where it already is:

* ``app.services.workout_session`` owns session identity, the one-active-session
  invariant, lifecycle classification and terminal transitions;
* ``app.services.workout_completion`` owns confirmed completion and every one of
  its side effects, including the Pump Check completion proof;
* ``app.services.mobile_training`` owns canonical workout identity and content.

Nothing in this package creates a completion artifact, mints a workout, or calls
a Training provider.
"""
from .checkpoint import (
    MAX_ELAPSED_SECONDS,
    MAX_EXERCISES,
    MAX_REPS,
    MAX_SETS_PER_EXERCISE,
    MAX_SNAPSHOT_BYTES,
    MAX_WEIGHT_KG,
    Checkpoint,
    parse_checkpoint,
    parse_idempotency_key,
    parse_optional_revision,
    parse_reason,
    parse_revision,
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
from .projection import project_session
from .service import (
    SessionCommandResult,
    abandon,
    checkpoint,
    complete,
    current,
    prepare_complete,
    resume,
    start,
)

__all__ = [
    # commands
    "start",
    "current",
    "resume",
    "checkpoint",
    "abandon",
    "complete",
    "prepare_complete",
    "SessionCommandResult",
    # request contract
    "Checkpoint",
    "parse_checkpoint",
    "parse_idempotency_key",
    "parse_revision",
    "parse_optional_revision",
    "parse_reason",
    "MAX_EXERCISES",
    "MAX_SETS_PER_EXERCISE",
    "MAX_REPS",
    "MAX_WEIGHT_KG",
    "MAX_ELAPSED_SECONDS",
    "MAX_SNAPSHOT_BYTES",
    # projection
    "project_session",
    # typed errors
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
]
