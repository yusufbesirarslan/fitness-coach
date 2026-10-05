"""The ONE typed failure vocabulary of the workout-execution domain.

Sprint 14 PR2 moved this module out of the ``mobile_workout_sessions`` adapter
and into the canonical session domain, unchanged. It was never native-specific:
"the declared revision is not current" and "the session is terminal" are facts
about the shared ``WorkoutSession`` row, not about a transport. Both server
transports now raise and classify these same classes, so ``isinstance`` means
the same thing on either side and there is exactly one vocabulary to keep
consistent.

Each error carries the exact triple the ``/api/v1`` envelope needs — a stable
public code, an HTTP status and an explicit ``retryable`` classification — so a
native client never has to infer retry safety from the status code alone
(PR5 section 42). Those three attributes are the NATIVE envelope's rendering of
a domain failure and are retained verbatim here so that contract is unchanged;
a second transport is free to render the same class differently (the browser
transport maps them to its own lower-case session codes) but may never invent a
second failure taxonomy. Three retry classes exist and each error names exactly
one:

``retryable=True``
    Retry the SAME command unchanged (transient backend condition).
``retryable=False`` with ``requires_reread=True``
    The command can never succeed as sent; re-read canonical state
    (``GET /workout-sessions/current``) and rebuild it.
``retryable=False`` with ``requires_reread=False``
    Terminal/permanent for this input; neither retry nor re-read helps.
    "Terminal" here (``Session-Resolution: terminal``) is about the submitted
    command and input, NOT the session lifecycle. Only ``SessionTerminal``
    reports a COMPLETED/ABANDONED session.
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


class RevisionExhausted(SessionCommandError):
    """The V1 revision domain is full; replay and completion remain possible."""

    public_code = "TRAINING_SESSION_REVISION_EXHAUSTED"
    http_status = 409
    retryable = False


class IdempotencyConflict(SessionCommandError):
    """The key was already used for a DIFFERENT semantic command."""

    public_code = "TRAINING_SESSION_IDEMPOTENCY_CONFLICT"
    http_status = 409
    retryable = False


class CompletionRejected(SessionCommandError):
    """The completion gate refused this attempt (unusable proof image).

    Raised before the completion transaction, so nothing is written: the session
    stays ACTIVE, its checkpoint and revision are untouched, and no PumpCheck,
    marker, XP or upload exists. ``terminal`` means "do not resend this proof".
    It does not mean the session is over, and a later attempt with a different
    proof can complete it.
    """

    public_code = "TRAINING_SESSION_COMPLETION_REJECTED"
    http_status = 422
    retryable = False


class CompletionProofUnverified(SessionCommandError):
    """The completion proof could not be EVALUATED (LP-13 P2).

    Provider 4xx/5xx, timeout, transport failure, a malformed model answer or an
    image-preparation failure. This is not evidence that the proof is bad, so it
    is not a ``CompletionRejected``; it is not evidence that it is good either,
    so the completion never proceeds. Raised before the completion transaction:
    nothing is written and the session stays ACTIVE. On the wire it is the
    existing retryable ``TRAINING_SESSION_UNAVAILABLE`` envelope (503), which
    native clients already treat as "retry the same command later"; the
    distinct class keeps the cause explicit server-side.
    """

    public_code = "TRAINING_SESSION_UNAVAILABLE"
    http_status = 503
    retryable = True


class SessionPersistenceUnavailable(SessionCommandError):
    public_code = "TRAINING_SESSION_UNAVAILABLE"
    http_status = 503
    retryable = True
