"""Typed command + result contract for the canonical workout completion mutation
(Sprint 7 PR2).

These are plain, framework-free value objects: the entry paths (the
``POST /workout/complete`` route and the AI-coach gym-photo tool) adapt their
request/tool inputs into a :class:`CompleteWorkoutCommand`, the service returns a
:class:`CompletionResult`, and each entry path maps the result back to its own
response shape. Keeping the boundary explicit is what lets both writers share one
mutation without sharing transport concerns.
"""
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional, Tuple


class CompletionOutcome(Enum):
    """The deterministic domain outcomes of a confirmed-completion attempt.

    ``CREATED`` — this call performed the one canonical completion.
    ``ALREADY_COMPLETED`` — an idempotent replay or the concurrency race-loser;
    the day already has a completion. Never an error, never an HTTP 500.
    """

    CREATED = "created"
    ALREADY_COMPLETED = "already_completed"


class SessionCompletionConflict(Exception):
    """A session-scoped completion cannot proceed against the referenced session.

    Two causes, distinguished by ``reason`` so a caller can map each to its own
    public error without inspecting message text:

    ``"abandoned"`` (default)
        The session is in a terminal state incompatible with completion (it was
        explicitly ABANDONED).
    ``"revision"``
        The caller declared an expected checkpoint revision that is no longer
        canonical — durable progress landed after the caller last read the
        session, so completing now would silently discard it (PR5 section 31).

    Raised *after* rolling back, before any completion artifact is written —
    so no PumpCheck/marker/XP is produced. The caller maps it to a deterministic
    conflict outcome (never an HTTP 500).

    Defined here (not in ``workout_session``) so the completion authority never
    imports the session package — the dependency arrow stays one-way
    (``workout_session`` → ``workout_completion``), avoiding an import cycle.
    """

    def __init__(self, message: str = "", *, reason: str = "abandoned"):
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class CompleteWorkoutCommand:
    """Everything the canonical mutation needs — already validated by the caller.

    ``user_id`` and ``today`` are trusted server values (current user + Istanbul
    day), never client-supplied identity. Any remote work (image validation, S3
    upload) is done by the entry path *before* building this command, so the
    mutation transaction performs no network I/O.
    """

    user_id: int
    today: date
    # OPTIONAL persisted-session linkage (Sprint 7 PR3). When present, this
    # completion also terminalizes the owned ACTIVE ``WorkoutSession`` (id) as
    # COMPLETED, atomically, inside the single completion transaction. ``None``
    # (the default) is the unchanged legacy path — a completion without a session
    # stays fully valid and no session is fabricated.
    session_id: Optional[int] = None
    # OPTIONAL optimistic precondition (Mobile Training PR5). When not ``None``,
    # the linked session's ``checkpoint_revision`` must still equal this value at
    # the moment the session row is locked, otherwise the completion is refused
    # with ``SessionCompletionConflict(reason="revision")`` and NO artifact is
    # written. Checked inside the completion transaction, after the FOR UPDATE
    # lock, because that is the only place the check is race-free.
    expected_checkpoint_revision: Optional[int] = None
    image_key: Optional[str] = None
    location_type: str = ""
    description: str = ""
    workout_score: Optional[float] = None
    visibility: str = "private"
    # Tuple (not list) so the command stays hashable/frozen; the service converts
    # it to a list for the JSON PumpCheck column and the friend-message fan-out.
    shared_friend_ids: Tuple[int, ...] = ()
    valid: bool = True
    fallback: bool = False
    base_xp: int = 10
    photo_bonus: int = 25
    # User-facing feed content for the ``workout_completed`` activity row; the two
    # entry paths word this differently, so it is passed in rather than derived.
    activity_text: str = "Bugünkü antrenmanını tamamladı (foto eklendi)"
    # A short, bounded label for safe observability (no PII): "route" | "ai_tool".
    entry_path: str = "unknown"


@dataclass(frozen=True)
class CompletionResult:
    """The outcome plus everything an entry path needs to serialize a response.

    On ``ALREADY_COMPLETED`` only ``outcome`` is meaningful; the reward fields
    stay at their defaults because no new completion was written.
    """

    outcome: CompletionOutcome
    pump_check_id: Optional[int] = None
    new_total: Optional[int] = None
    level: Optional[int] = None
    title: Optional[str] = None
    quest_result: Optional[dict] = None
    xp_awarded: int = 0

    @property
    def created(self) -> bool:
        return self.outcome is CompletionOutcome.CREATED

    @property
    def already_completed(self) -> bool:
        return self.outcome is CompletionOutcome.ALREADY_COMPLETED
