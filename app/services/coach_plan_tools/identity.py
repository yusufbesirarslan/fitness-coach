"""Server-owned operation identity for AI Coach plan mutations.

PR2 made ``MutationContext.idempotency_key`` mandatory and left minting it to
the caller, with one requirement: the key must be **stable across the retry of
one logical user request**. This module is that minting, and it is trusted
server code — the key never appears in a tool schema, is never derived from
model output, and is never exposed to the model (brief §§15, 18).

Two ingredients, and nothing else:

``turn``
    ``g.request_id`` — the repository's existing server-generated per-request
    identifier (``app/observability.py``). It is generated server-side on every
    request precisely so a client cannot inject one, it is already the
    correlation id in the log line and the ``/ask/stream`` meta frame, and it
    survives everything PR3 has to survive: a provider retry, a Bedrock→OpenAI
    fallback, and each tool-continuation round all happen inside the same HTTP
    request. Reusing it avoids standing up a second, competing turn-identity
    system with no evidence that one is needed (brief §16).

``semantic``
    the domain's own ``semantic_fingerprint`` / ``undo_fingerprint``. The
    comparator PR2 already uses to decide "same operation?" is the right thing
    to key on, and reusing it means the key and the replay check can never
    disagree about what "the same mutation" means. Raw model JSON is explicitly
    NOT hashed (brief §18): whitespace, key order and an extra echoed field
    would each mint a fresh key for one intent, which is the failure this whole
    mechanism exists to prevent.

There is no occurrence counter. Adding one would make the second delivery of
one logical mutation a *new* operation — precisely the duplicate ``add`` and
double ``undo`` that briefs §35 and §52 forbid. Distinct commands already
separate themselves: different intent, different fingerprint, different key
(§36).

Honest scope (brief §19): this guarantees convergence **within one server Coach
turn**. It is not transport-level exactly-once. A genuinely new HTTP request
carries a new ``request_id`` and is a new intent — which is correct, because a
user who sends "add cable flies" twice has asked for it twice. No durable
client-supplied chat-request identity exists in this architecture today, and
inventing fragile state to claim a broader guarantee would be a promise the
system cannot keep.
"""
import hashlib

from app.observability import current_request_id
from app.services.plan_mutation.fingerprint import (
    UNDO_COMMAND_TYPE,
    command_type,
    semantic_fingerprint,
    undo_fingerprint,
)


class TurnIdentityUnavailable(RuntimeError):
    """No server turn identity exists, so no durable key can be derived.

    Fail closed. A key minted without a turn would either collide across turns
    (replaying an old mutation instead of applying a new one) or be unique per
    attempt (no replay protection at all). Both are worse than refusing.
    """


#: Domain separation + version. A change to what participates in the digest
#: requires a new prefix rather than silently redefining stored keys.
KEY_DOMAIN = "axisai/coach-plan-tool-operation/v1"

#: Marks a key as minted by this module. Human-readable in a journal row, and
#: it keeps the AI Coach's keys in their own namespace should another caller
#: ever mint keys for the same user.
KEY_PREFIX = "aic"

#: Digest length in hex characters. 64 bits over the (turn, command) pair,
#: which carries at most a couple of operations — collision is not a practical
#: concern, and the whole key stays well inside PR2's 8..64 character bound.
_DIGEST_CHARS = 16

#: The request id the observability layer returns when there is no request.
_NO_REQUEST = "-"


def current_turn_id():
    """The trusted identity of the Coach turn in progress.

    Raises ``TurnIdentityUnavailable`` outside a request. Coach tools only ever
    run inside ``/ask`` or ``/ask/stream`` (the streaming generator is wrapped
    in ``stream_with_context``, so the request context is still live when tools
    execute), and anything else calling in has no turn to be idempotent within.
    """
    turn = current_request_id()
    if not turn or turn == _NO_REQUEST:
        raise TurnIdentityUnavailable("no server turn identity")
    return turn


def _key(turn, kind, fingerprint):
    digest = hashlib.sha256(
        "\0".join((KEY_DOMAIN, turn, kind, fingerprint)).encode("utf-8")
    ).hexdigest()[:_DIGEST_CHARS]
    return f"{KEY_PREFIX}:{turn}:{digest}"


def mutation_operation_key(command):
    """The durable operation key for one typed mutation command in this turn."""
    return _key(current_turn_id(), command_type(command),
                semantic_fingerprint(command))


CONFIRMATION_KEY_DOMAIN = "axisai/coach-plan-confirmation/v1"


def confirmation_operation_key(proposal_public_id):
    """Stable mutation identity derived from a durable proposal.

    Independent of the HTTP turn, the provider tool_call_id, and the raw
    user message: a crash after PlanMutationService commits still retries
    with the same key, so PR2 replays instead of mutating twice.
    """
    if not isinstance(proposal_public_id, str) or not proposal_public_id:
        raise TurnIdentityUnavailable("no proposal identity")
    digest = hashlib.sha256(
        "\0".join((CONFIRMATION_KEY_DOMAIN, proposal_public_id)).encode("utf-8")
    ).hexdigest()[:_DIGEST_CHARS]
    return f"{KEY_PREFIX}:c:{digest}"


def undo_operation_key():
    """The durable operation key for an undo in this turn.

    ``undo_fingerprint()`` is a constant — undo takes no arguments, so every
    undo in one turn *is* the same operation. That is what makes a duplicated
    delivery replay the first undo instead of reaching back and reversing a
    second, earlier mutation.
    """
    return _key(current_turn_id(), UNDO_COMMAND_TYPE, undo_fingerprint())
