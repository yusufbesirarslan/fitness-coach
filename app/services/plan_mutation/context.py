"""The mutation envelope: who asked, why, and under which operation identity.

Separate from the *command* on purpose. The command is canonical intent — what
should become true of the plan. The context is everything about the request that
must never influence what the mutation does: replay identity, actor, and a
bounded audit note (brief §§15-17).

Nothing here is authorization. ``actor`` is trusted metadata supplied by the
server, and a record carrying ``actor="ai_coach"`` grants the AI Coach exactly
nothing — ownership still comes from the authenticated session and the
owner-scoped plan query, as it did in PR1.
"""
import re
from dataclasses import dataclass
from typing import Optional

from .errors import InvalidMutation


#: The bounded actor vocabulary. Closed on purpose: an unbounded string would
#: turn a future request field into an audit identity a caller controls.
ACTOR_USER = "user"
ACTOR_AI_COACH = "ai_coach"
ACTOR_SYSTEM = "system"
PLAN_MUTATION_ACTORS = frozenset({ACTOR_USER, ACTOR_AI_COACH, ACTOR_SYSTEM})

#: Column-backed ceiling for the audit note.
MAX_REASON_CHARS = 200

#: Same shape the repository's existing ``Idempotency-Key`` contract accepts
#: (``meal_idempotency``). Restated rather than imported: that module reaches for
#: ``flask.request`` and ``MealLog``, neither of which belongs in this domain.
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


@dataclass(frozen=True)
class MutationContext:
    """The envelope every mutation and undo must carry.

    ``idempotency_key`` is REQUIRED, not optional. PR1 shipped with no caller,
    so nothing is being migrated by making it mandatory — and a required key is
    the only version of this contract in which a future consumer cannot quietly
    opt out of replay protection and reintroduce the duplicate-``add`` bug PR2
    exists to close.

    The key is scoped to one user. Two users may send the same textual key and
    stay completely independent operations.
    """
    idempotency_key: str
    actor: str = ACTOR_USER
    reason: Optional[str] = None


def validate_context(context) -> MutationContext:
    """Return a normalized context, or raise ``InvalidMutation``.

    Runs before any database read, so a malformed envelope can never claim an
    operation identity or take a row lock.
    """
    if not isinstance(context, MutationContext):
        raise InvalidMutation("a mutation context is required")

    key = context.idempotency_key
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise InvalidMutation("idempotency key is malformed")

    if context.actor not in PLAN_MUTATION_ACTORS:
        raise InvalidMutation("unsupported mutation actor")

    reason = context.reason
    if reason is not None:
        if not isinstance(reason, str):
            raise InvalidMutation("reason must be text")
        reason = reason.strip()
        if len(reason) > MAX_REASON_CHARS:
            # Rejected, never truncated: a silently shortened audit note is a
            # different note, and the caller is not told it was altered.
            raise InvalidMutation("reason exceeds the audit bound")
        reason = reason or None

    return MutationContext(
        idempotency_key=key, actor=context.actor, reason=reason)
