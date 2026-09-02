"""Typed public errors for the native workout-session write contracts.

Every error carries the exact triple the ``/api/v1`` envelope needs — a stable
public code, an HTTP status and an explicit ``retryable`` classification — so a
native client never has to infer retry safety from the status code alone
(PR5 section 42). Three retry classes exist and each error names exactly one:

``retryable=True``
    Retry the SAME command unchanged (transient backend condition).
``retryable=False`` with ``requires_reread=True``
    The command can never succeed as sent; re-read canonical state
    (``GET /workout-sessions/current``) and rebuild it.
``retryable=False`` with ``requires_reread=False``
    Terminal/permanent for this input; neither retry nor re-read helps.
"""


class SessionCommandError(Exception):
    """Base class for every typed native session-write failure."""

    public_code = "TRAINING_SESSION_UNAVAILABLE"
    http_status = 503
    retryable = True
    requires_reread = False


class InvalidSessionRequest(SessionCommandError):
    public_code = "TRAINING_SESSION_INVALID_REQUEST"
    http_status = 400
    retryable = False


class InvalidIdempotencyKey(SessionCommandError):
    public_code = "TRAINING_SESSION_INVALID_IDEMPOTENCY_KEY"
    http_status = 400
    retryable = False


class InvalidRevision(SessionCommandError):
    """``If-Match`` is missing or is not a usable revision integer."""

    public_code = "TRAINING_SESSION_INVALID_REVISION"
    http_status = 428
    retryable = False
    requires_reread = True


class SessionNotFound(SessionCommandError):
    """Private not-found: also used for a session owned by somebody else, so no
    cross-owner existence fact is revealed."""

    public_code = "TRAINING_SESSION_NOT_FOUND"
    http_status = 404
    retryable = False


class NoActiveSession(SessionCommandError):
    public_code = "TRAINING_SESSION_NONE_ACTIVE"
    http_status = 404
    retryable = False


class WorkoutNotStartable(SessionCommandError):
    """The referenced workout is a rest slot, or is not today's workout."""

    public_code = "TRAINING_WORKOUT_NOT_STARTABLE"
    http_status = 409
    retryable = False
    requires_reread = True


class ActiveSessionExists(SessionCommandError):
    """A DIFFERENT active session already owns the one-active-session slot."""

    public_code = "TRAINING_SESSION_ALREADY_ACTIVE"
    http_status = 409
    retryable = False
    requires_reread = True


class SessionTerminal(SessionCommandError):
    """The session is COMPLETED or ABANDONED; the command cannot apply."""

    public_code = "TRAINING_SESSION_TERMINAL"
    http_status = 409
    retryable = False
    requires_reread = True


class SessionStale(SessionCommandError):
    """The session is ACTIVE but not resumable (previous day / plan drift)."""

    public_code = "TRAINING_SESSION_STALE"
    http_status = 409
    retryable = False
    requires_reread = True


class RevisionConflict(SessionCommandError):
    """The declared base revision is not the current canonical revision."""

    public_code = "TRAINING_SESSION_REVISION_CONFLICT"
    http_status = 409
    retryable = False
    requires_reread = True


class IdempotencyConflict(SessionCommandError):
    """The key was already used for a DIFFERENT semantic command."""

    public_code = "TRAINING_SESSION_IDEMPOTENCY_CONFLICT"
    http_status = 409
    retryable = False


class CompletionRejected(SessionCommandError):
    """The completion gate refused this attempt (unusable proof image)."""

    public_code = "TRAINING_SESSION_COMPLETION_REJECTED"
    http_status = 422
    retryable = False


class SessionPersistenceUnavailable(SessionCommandError):
    public_code = "TRAINING_SESSION_UNAVAILABLE"
    http_status = 503
    retryable = True
