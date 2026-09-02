"""Native workout-session write commands.

This module is an ADAPTER, not an authority. Session identity, the
one-active-session invariant, lifecycle classification and terminal transitions
belong to ``app.services.workout_session``; confirmed completion and all of its
side effects belong to ``app.services.workout_completion``. Everything here does
exactly three things:

1. resolve the caller's opaque references against canonical read authorities;
2. enforce the native request contract (bounds, membership, revision, replay);
3. translate canonical outcomes into the typed native error vocabulary.

It creates no completion artifact, no PumpCheck, no WorkoutLog and no XP of its
own, and it never invokes a Training provider.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.models import (
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
)
from app.services import mobile_training
from app.services.workout_completion import CompletionResult
from app.services.workout_session import (
    NativeWorkoutIdentity,
    SessionOutcome,
    abandon_session,
    advance_checkpoint,
    build_session_view,
    complete_session,
    get_current_session,
    get_owned_session,
    resume_session,
    start_session,
)
from app.timeutil import app_today

from .checkpoint import Checkpoint
from .errors import (
    ActiveSessionExists,
    IdempotencyConflict,
    RevisionConflict,
    SessionNotFound,
    SessionPersistenceUnavailable,
    SessionStale,
    SessionTerminal,
    WorkoutNotStartable,
)
from .projection import project_completion, project_session

# A public session reference is an opaque, non-enumerable, owner-scoped token
# minted by the session authority (``secrets.token_urlsafe(32)``). Bounded here
# so a hostile path segment can never reach a query as an oversized string.
SESSION_REF_MAX = 64

_TERMINAL_OUTCOMES = frozenset({
    SessionOutcome.ALREADY_COMPLETED, SessionOutcome.ALREADY_ABANDONED,
})


class SessionCommandResult:
    """One command's public result: the projection plus its HTTP disposition."""

    __slots__ = ("payload", "status", "replayed")

    def __init__(self, payload: dict, status: int, replayed: bool = False):
        self.payload = payload
        self.status = status
        self.replayed = replayed


# -- Resolution helpers -------------------------------------------------------

def _owned_session(user_id: int, session_ref: object):
    """Ownership-scoped lookup by opaque reference.

    A reference belonging to another owner resolves exactly like a reference
    that never existed, so no cross-owner existence fact is revealed.
    """
    if not isinstance(session_ref, str) or not session_ref:
        raise SessionNotFound("session reference is not usable")
    if len(session_ref) > SESSION_REF_MAX:
        raise SessionNotFound("session reference is not usable")
    row = get_owned_session(user_id, session_ref)
    if row is None:
        raise SessionNotFound("session was not found")
    return row


def _project(row, today=None) -> dict:
    day = today or app_today()
    return project_session(row, build_session_view(row, day))


def _reject_terminal(row) -> None:
    if row.status != WORKOUT_SESSION_ACTIVE:
        raise SessionTerminal("the workout session is already terminal")


def resolve_startable_workout(user_id: int, secret, workout_ref: object, today) -> dict:
    """Resolve an opaque native workout reference to TODAY's startable workout.

    Fails closed on every dimension the reference could be wrong about: shape,
    owner, current plan lineage/mutation version (all three are bound into the
    reference by the read contract's HMAC), and the schedule slot. A reference
    that is well-formed but names another day is NOT startable now -- returning
    the same error for a rest slot and a different weekday keeps the client from
    probing the schedule through this endpoint.
    """
    try:
        workout = mobile_training.build_workout(user_id, secret, workout_ref)["workout"]
    except mobile_training.WorkoutNotFound as error:
        raise SessionNotFound("workout reference is not usable") from error
    except (mobile_training.WorkoutStale,
            mobile_training.PlanUnprojectable) as error:
        raise WorkoutNotStartable("workout reference is not current") from error
    except mobile_training.TrainingReadUnavailable as error:
        raise SessionPersistenceUnavailable("Training read unavailable") from error
    if workout["slot"] != today.weekday() or workout["kind"] == "rest":
        raise WorkoutNotStartable("that workout cannot be started now")
    return workout


def _session_workout(user_id: int, secret, row) -> dict:
    """The canonical workout the SESSION was started for, re-resolved now.

    Re-resolution through the same reference is what makes plan drift visible:
    the reference is bound to the plan lineage and mutation version, so a
    regenerated or mutated plan can no longer produce it and the session is
    reported stale instead of being silently rebound to a newer definition.
    """
    if not row.workout_ref:
        raise SessionStale("this session has no native workout identity")
    try:
        workout = mobile_training.build_workout(
            user_id, secret, row.workout_ref)["workout"]
    except (mobile_training.WorkoutNotFound, mobile_training.WorkoutStale,
            mobile_training.PlanUnprojectable) as error:
        raise SessionStale("the workout is no longer current") from error
    except mobile_training.TrainingReadUnavailable as error:
        raise SessionPersistenceUnavailable("Training read unavailable") from error
    if (workout["plan_lineage"] != row.plan_lineage_id
            or workout["mutation_version"] != row.plan_mutation_version):
        raise SessionStale("the workout is no longer current")
    return workout


def allowed_exercise_ids(workout: dict) -> tuple:
    """The ordered canonical exercise identities a checkpoint may name."""
    return tuple(item["exercise_id"] for item in workout["exercises"])


# -- Commands -----------------------------------------------------------------

def start(user_id: int, secret, workout_ref: object) -> SessionCommandResult:
    """Start, or replay, the one canonical active session for this workout.

    Idempotency is INTRINSIC rather than key-based: the partial unique index
    ``uq_workout_session_active_owner`` is the atomic claim, so a duplicate tap,
    a network retry, a token refresh or a lost response all converge on the same
    session. A retry that reaches a committed session gets ``200`` with the same
    projection; only the call that actually created it gets ``201``. A DIFFERENT
    workout while one is active is a conflict, never a silent second session.
    """
    today = app_today()
    workout = resolve_startable_workout(user_id, secret, workout_ref, today)
    identity = NativeWorkoutIdentity(
        workout_ref=workout["workout_ref"],
        plan_lineage_id=workout["plan_lineage"],
        plan_mutation_version=workout["mutation_version"],
    )
    result = start_session(user_id, today=today, native=identity)
    if result.outcome is SessionOutcome.CONFLICT:
        if result.session is None:
            raise SessionPersistenceUnavailable("session state is unavailable")
        raise ActiveSessionExists("a different workout session is already active")
    if result.session is None:
        raise SessionPersistenceUnavailable("session state is unavailable")
    row = _owned_session(user_id, result.session.public_id)
    created = result.outcome is SessionOutcome.CREATED
    return SessionCommandResult(
        {"session": _project(row, today)}, 201 if created else 200,
        replayed=not created)


def current(user_id: int) -> SessionCommandResult:
    """The owner's current ACTIVE session, or a deliberate no-session state.

    This is the app-restart recovery contract: the returned projection carries
    the durable checkpoint AND its revision, so nothing a client needs to reopen
    a workout lives only in client memory. A terminal session is never reported
    here as current -- it has vacated the active slot by definition.
    """
    today = app_today()
    result = get_current_session(user_id, today=today)
    if result.outcome is SessionOutcome.NOT_FOUND or result.session is None:
        return SessionCommandResult({"session": None}, 200)
    row = _owned_session(user_id, result.session.public_id)
    return SessionCommandResult({"session": _project(row, today)}, 200)


def resume(
    user_id: int, session_ref: object, expected_revision: Optional[int] = None
) -> SessionCommandResult:
    """Re-attach to an owned, eligible ACTIVE session.

    Never creates a session and never duplicates a workout: it resolves an
    existing owned row and, at most, bumps its activity timestamp. Durable
    progress is untouched, so the revision cannot move and a retry is free.
    """
    today = app_today()
    row = _owned_session(user_id, session_ref)
    _reject_terminal(row)
    _require_revision(row, expected_revision)
    result = resume_session(user_id, row.public_id, today=today)
    if result.outcome is SessionOutcome.NOT_FOUND:
        raise SessionNotFound("session was not found")
    if result.outcome in _TERMINAL_OUTCOMES:
        raise SessionTerminal("the workout session is already terminal")
    if result.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION:
        raise SessionStale("the workout session needs resolution")
    return SessionCommandResult(
        {"session": _project(_owned_session(user_id, row.public_id), today)}, 200)


def checkpoint(
    user_id: int,
    secret,
    session_ref: object,
    key: str,
    base_revision: int,
    parse_payload,
) -> SessionCommandResult:
    """Durably record one bounded full progress snapshot.

    Ordering matters and is deliberate:

    1. resolve + reject a terminal session (a terminal session accepts nothing);
    2. re-resolve the canonical workout, so membership is validated against the
       workout this session actually began with;
    3. parse and bound the snapshot, producing its semantic fingerprint;
    4. REPLAY check before any mutation -- the same key with the same snapshot
       returns the committed state without advancing the revision;
    5. one conditional UPDATE keyed on the declared base revision, which is the
       only thing that decides who wins.

    ``parse_payload`` is a callable taking the allowed exercise identities, so
    the caller never has to know the workout before this function resolves it.
    """
    today = app_today()
    row = _owned_session(user_id, session_ref)
    _reject_terminal(row)
    workout = _session_workout(user_id, secret, row)
    parsed: Checkpoint = parse_payload(allowed_exercise_ids(workout))

    replay = _replay_or_conflict(row, key, parsed)
    if replay is not None:
        return replay

    if (row.checkpoint_revision or 0) != base_revision:
        raise RevisionConflict("the declared revision is not current")

    written = advance_checkpoint(
        user_id, row.public_id, base_revision, parsed.to_json(),
        parsed.fingerprint, key, datetime.utcnow(),
    )
    row = _owned_session(user_id, row.public_id)
    if not written:
        # Lost a race. If the winner was OUR OWN duplicate request (same key,
        # same snapshot) this is one logical mutation observed twice, so replay
        # it; anything else is a genuine conflict the client must re-read.
        replay = _replay_or_conflict(row, key, parsed)
        if replay is not None:
            return replay
        _reject_terminal(row)
        raise RevisionConflict("the declared revision is not current")
    return SessionCommandResult({"session": _project(row, today)}, 200)


def _replay_or_conflict(row, key: str, parsed: Checkpoint):
    """Same key + same snapshot replays; same key + different snapshot conflicts.

    Only the LAST accepted checkpoint's key is retained. An older key does not
    replay -- but it cannot silently mutate either, because its base revision is
    stale by construction and the revision check rejects it first.
    """
    if not row.checkpoint_idempotency_key or row.checkpoint_idempotency_key != key:
        return None
    if row.checkpoint_fingerprint != parsed.fingerprint:
        raise IdempotencyConflict("the key belongs to a different checkpoint")
    return SessionCommandResult({"session": _project(row)}, 200, replayed=True)


def abandon(
    user_id: int,
    session_ref: object,
    expected_revision: Optional[int] = None,
    reason: Optional[str] = None,
) -> SessionCommandResult:
    """Terminalize an owned ACTIVE session without any completion side effect.

    Retry-safe by construction: the transition is a conditional UPDATE on
    ``status='active'``, so exactly one caller wins and every later or losing
    caller observes the same terminal state instead of an error. The persisted
    row and its checkpoint history are preserved, never deleted.
    """
    today = app_today()
    row = _owned_session(user_id, session_ref)
    if row.status == WORKOUT_SESSION_ABANDONED:
        return SessionCommandResult({"session": _project(row, today)}, 200, True)
    if row.status == WORKOUT_SESSION_COMPLETED:
        raise SessionTerminal("the workout session is already completed")
    _require_revision(row, expected_revision)
    result = abandon_session(user_id, row.public_id, reason=reason)
    if result.outcome is SessionOutcome.ALREADY_COMPLETED:
        raise SessionTerminal("the workout session is already completed")
    if result.outcome is SessionOutcome.NOT_FOUND:
        raise SessionNotFound("session was not found")
    row = _owned_session(user_id, row.public_id)
    return SessionCommandResult(
        {"session": _project(row, today)}, 200,
        replayed=result.outcome is SessionOutcome.ALREADY_ABANDONED)


def prepare_complete(
    user_id: int, session_ref: object, expected_revision: int
):
    """Cheap fail-fast preflight run BEFORE any provider or storage work.

    Completion is the only command with an expensive tail (vision validation and
    an object-store upload), so every reason it can be refused deterministically
    is evaluated here first: ownership, an abandoned session, and the declared
    revision. This buys latency and cost, never correctness -- each of these is
    re-evaluated authoritatively inside the completion transaction, the revision
    under the session row lock.

    Returns the owned row so the caller can see whether proof work is needed at
    all (an already-COMPLETED session is a replay, not an error).
    """
    row = _owned_session(user_id, session_ref)
    if row.status == WORKOUT_SESSION_ABANDONED:
        raise SessionTerminal("the workout session was abandoned")
    _require_revision(row, expected_revision)
    return row


def complete(
    user_id: int,
    session_ref: object,
    expected_revision: int,
    **completion_kwargs,
) -> SessionCommandResult:
    """Complete THROUGH the canonical completion authority.

    This function writes nothing itself. It resolves ownership, then hands a
    session-scoped command to ``workout_session.complete_session``, which
    delegates to ``workout_completion.complete_workout`` -- the same single
    transaction the browser route and the AI-coach tool use. Exact-once is that
    transaction's ``uq_pump_check_day`` claim, so a lost response replayed here
    returns the completed outcome and produces no second PumpCheck, WorkoutLog,
    XP grant, quest claim or activity row.

    ``expected_revision`` is carried into the transaction and verified under the
    session row lock, so completion cannot silently discard progress that landed
    after the client last read the session.
    """
    today = app_today()
    row = _owned_session(user_id, session_ref)
    if row.status == WORKOUT_SESSION_ABANDONED:
        raise SessionTerminal("the workout session was abandoned")
    result = complete_session(
        user_id, row.public_id, today=today,
        expected_checkpoint_revision=expected_revision,
        **completion_kwargs,
    )
    if result.outcome is SessionOutcome.NOT_FOUND:
        raise SessionNotFound("session was not found")
    if result.outcome is SessionOutcome.ALREADY_ABANDONED:
        raise SessionTerminal("the workout session was abandoned")
    if result.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION:
        raise SessionStale("the workout session needs resolution")
    if result.outcome is SessionOutcome.CONFLICT:
        if result.conflict_reason == "revision":
            raise RevisionConflict("the declared revision is not current")
        raise SessionTerminal("the workout session was abandoned")
    row = _owned_session(user_id, row.public_id)
    completion: Optional[CompletionResult] = result.completion
    payload = {"session": _project(row, today)}
    payload["completion"] = project_completion(completion)
    return SessionCommandResult(
        payload, 200,
        replayed=result.outcome is SessionOutcome.ALREADY_COMPLETED)


def _require_revision(row, expected: Optional[int]) -> None:
    """Enforce an OPTIONAL ``If-Match`` guard on a non-progress command.

    Resume and abandon do not write progress, so the guard is optional: abandon
    discards progress by definition and resume preserves it, and forcing a
    precondition on either would make a safe retry fail for no gain. When a
    client does send one, it is honoured exactly.
    """
    if expected is not None and (row.checkpoint_revision or 0) != expected:
        raise RevisionConflict("the declared revision is not current")
