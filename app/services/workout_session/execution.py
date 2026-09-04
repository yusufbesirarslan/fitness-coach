"""The canonical, transport-neutral workout EXECUTION authority (Sprint 14 PR2).

``#276`` gave native execution a durable, revision-gated checkpoint. The
orchestration that made it safe — resolve, refuse a terminal session, validate
the snapshot against the session's canonical workout, decide replay *before* any
mutation, then one conditional UPDATE keyed on the declared base revision —
lived inside the ``mobile_workout_sessions`` adapter. Sprint 14 PR2 moves it
here unchanged, because none of it is native: every step is a statement about
the shared ``WorkoutSession`` row.

That move is the whole point of PR2. The browser transport does not get a
*second* implementation of these rules; it calls this one. What remains
transport-specific is exactly two things:

* how the caller is authenticated and how the request is parsed
  (bearer + ``If-Match`` header for ``/api/v1``, cookie session + CSRF for the
  browser), and
* how a raised :mod:`~app.services.workout_session.errors` class is rendered.

Everything between those two — ownership, membership, bounds, replay identity,
revision ordering and terminality — has exactly one implementation, so the two
surfaces cannot drift apart the way they had by ``#276``.

What this module is NOT: it is not a second session lifecycle. Start, resume,
abandon and completion stay in :mod:`~app.services.workout_session.service`, and
the durable write itself is still
:func:`~app.services.workout_session.queries.advance_checkpoint`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional, Tuple

from app.models import WORKOUT_SESSION_ABANDONED, WORKOUT_SESSION_ACTIVE
from app.timeutil import app_today

from .checkpoint import Checkpoint
from .errors import (
    IdempotencyConflict,
    RevisionConflict,
    SessionNotFound,
    SessionStale,
    SessionTerminal,
)
from .models import SOURCE_SCHEDULED, SessionView, fingerprints_match
from .queries import advance_checkpoint, get_owned_session, planned_workout_for_slot
from .service import build_session_view

# A public session reference is an opaque, non-enumerable, owner-scoped token
# minted by the session authority (``secrets.token_urlsafe(32)``). Bounded here
# so a hostile path segment can never reach a query as an oversized string.
SESSION_REF_MAX = 64


@dataclass(frozen=True)
class CheckpointResult:
    """One accepted (or replayed) checkpoint: the refreshed row and its view.

    Both are returned because the two transports project differently — the
    ``/api/v1`` envelope carries native lineage fields that only exist on the
    row — while the *semantic* execution state (revision, snapshot, staleness)
    comes from the one canonical :class:`SessionView` either way.
    """

    row: object
    view: SessionView
    replayed: bool


def owned_session(user_id: int, session_ref: object):
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


def reject_terminal(row) -> None:
    """A COMPLETED or ABANDONED session accepts no further execution command."""
    if row.status != WORKOUT_SESSION_ACTIVE:
        raise SessionTerminal("the workout session is already terminal")


def require_revision(row, expected: Optional[int]) -> None:
    """Enforce an OPTIONAL revision guard on a NON-progress command.

    Resume and abandon do not write progress, so the guard is optional: abandon
    discards progress by definition and resume preserves it, and forcing a
    precondition on either would make a safe retry fail for no gain. When a
    client does send one, it is honoured exactly. Progress-writing commands do
    NOT use this — their precondition is the conditional UPDATE itself.
    """
    if expected is not None and (row.checkpoint_revision or 0) != expected:
        raise RevisionConflict("the declared revision is not current")


def planned_exercise_identities(row) -> Tuple[str, ...]:
    """The canonical exercise identities a checkpoint for ``row`` may name, for
    a session addressed WITHOUT an opaque native workout reference.

    The native adapter names a session's workout by re-resolving the HMAC-bound
    ``workout_ref`` it stored at start, which fails closed on any plan drift
    because the reference is bound to the plan lineage and mutation version.
    The browser contract has no such reference, so the equivalent question is
    asked of the canonical plan snapshot the session already recorded: is the
    CURRENT plan's workout for this session's weekday slot still the same
    workout it began with?

    That is decided by the session's own versioned ``plan_fingerprint``, using
    the same :func:`fingerprints_match` the lifecycle classifier uses — not by a
    second staleness rule. An unscheduled session has no planned workout to be
    a member of and is refused for the same reason a native session without a
    ``workout_ref`` is: there is nothing to validate membership against.

    Refusal is always :class:`SessionStale`, never a silent empty allow-list,
    because "we cannot tell which exercises belong to this workout" must fail
    the checkpoint rather than reject each entry individually.
    """
    if row.source != SOURCE_SCHEDULED:
        raise SessionStale("this session has no planned workout identity")
    planned = planned_workout_for_slot(row.user_id, row.weekday_slot)
    if fingerprints_match(row.plan_fingerprint, planned.fingerprint) is not True:
        raise SessionStale("the workout is no longer current")
    if not planned.exercise_ids:
        raise SessionStale("the workout has no canonical exercise identity")
    return planned.exercise_ids


def prepare_completion(user_id: int, session_ref: object, expected_revision: int):
    """Cheap fail-fast preflight run BEFORE any provider or storage work.

    Completion is the only command with an expensive tail (vision validation and
    an object-store upload), so every reason it can be refused deterministically
    is evaluated here first: ownership, an abandoned session, and the declared
    revision. This buys latency and cost, never correctness -- each of these is
    re-evaluated authoritatively inside the completion transaction, the revision
    under the session row lock in
    :func:`app.services.workout_completion.complete_workout`. A transport that
    treated this as the guard would be trivially racy; a transport that skipped
    it would merely be slower and more expensive.

    Returns the owned row so the caller can see whether proof work is needed at
    all (an already-COMPLETED session is a replay, not an error).
    """
    row = owned_session(user_id, session_ref)
    if row.status == WORKOUT_SESSION_ABANDONED:
        raise SessionTerminal("the workout session was abandoned")
    require_revision(row, expected_revision)
    return row


def record_checkpoint(
    user_id: int,
    session_ref: object,
    key: str,
    base_revision: int,
    resolve_allowed: Callable[[object], Tuple[str, ...]],
    parse_payload: Callable[[Tuple[str, ...]], Checkpoint],
    *,
    today: Optional[date] = None,
) -> CheckpointResult:
    """Durably record one bounded full progress snapshot.

    Ordering matters and is deliberate:

    1. resolve + reject a terminal session (a terminal session accepts nothing);
    2. resolve the canonical workout, so membership is validated against the
       workout this session actually began with;
    3. parse and bound the snapshot, producing its semantic fingerprint;
    4. REPLAY check before any mutation -- the same key with the same snapshot
       returns the committed state without advancing the revision;
    5. one conditional UPDATE keyed on the declared base revision, which is the
       only thing that decides who wins.

    ``resolve_allowed`` names the session's canonical workout (the native
    adapter re-resolves its opaque reference; the browser uses
    :func:`planned_exercise_identities`) and ``parse_payload`` turns the
    transport's already-extracted document into a validated
    :class:`~app.services.workout_session.checkpoint.Checkpoint` against those
    identities — so this function never has to know either transport's shape.
    """
    day = today or app_today()
    row = owned_session(user_id, session_ref)
    reject_terminal(row)
    parsed: Checkpoint = parse_payload(resolve_allowed(row))

    replay = _replay_or_conflict(row, key, parsed, day)
    if replay is not None:
        return replay

    if (row.checkpoint_revision or 0) != base_revision:
        raise RevisionConflict("the declared revision is not current")

    written = advance_checkpoint(
        user_id, row.public_id, base_revision, parsed.to_json(),
        parsed.fingerprint, key, datetime.utcnow(),
    )
    row = owned_session(user_id, row.public_id)
    if not written:
        # Lost a race. If the winner was OUR OWN duplicate request (same key,
        # same snapshot) this is one logical mutation observed twice, so replay
        # it; anything else is a genuine conflict the client must re-read.
        replay = _replay_or_conflict(row, key, parsed, day)
        if replay is not None:
            return replay
        reject_terminal(row)
        raise RevisionConflict("the declared revision is not current")
    return CheckpointResult(row, build_session_view(row, day), False)


def _replay_or_conflict(row, key: str, parsed: Checkpoint, day: date):
    """Same key + same snapshot replays; same key + different snapshot conflicts.

    Only the LAST accepted checkpoint's key is retained. An older key does not
    replay -- but it cannot silently mutate either, because its base revision is
    stale by construction and the revision check rejects it first.
    """
    if not row.checkpoint_idempotency_key or row.checkpoint_idempotency_key != key:
        return None
    if row.checkpoint_fingerprint != parsed.fingerprint:
        raise IdempotencyConflict("the key belongs to a different checkpoint")
    return CheckpointResult(row, build_session_view(row, day), True)
