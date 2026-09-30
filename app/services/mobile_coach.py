"""Native wire projection of the canonical Coach conversation (LP-09).

NOT a second Coach authority. The conversation is the persisted
`CoachConversation`/`CoachMessage` memory that web `/ask` already writes through
`ai_pipeline.generate_answer` -> `memory_manager.record_turn`, and the history
window is the one web `GET /coach/history` reads (`memory_manager.recent_messages`,
active conversation, newest `HISTORY_LIMIT` rows by id, returned oldest first).
This module only decides what of that record a native client may see:

  * role, text, created_at and the stream `interrupted` marker - nothing else.
    Never the conversation summary (a model-written note that is fed back to the
    model), token accounting, provider usage, the conversation row or the owner.
  * an OPAQUE message identity instead of `CoachMessage.id`. The derivation is
    the one `mobile_nutrition/identity.py` established for ledger rows - a keyed
    digest over (owner, row) under a domain-separated subkey of `SECRET_KEY` -
    so the id is stable, unguessable, carries no row count and is bound to its
    owner: user A's id for row N is not user B's id for row N.

Pure projection plus one owner-scoped read; no provider call and no write.
"""
import base64
import hashlib
import hmac
from datetime import timezone

from app.services import memory_manager


CONTRACT_VERSION = 1

# The same bounded window web `GET /coach/history` serves. Not a pagination
# framework: the Coach has one active conversation per user and the widget
# hydrates from exactly this window, so native reads the same slice.
HISTORY_LIMIT = 50

# Domain separation from every other use of SECRET_KEY (cookies, CSRF, the
# nutrition/training/pump-check identities); the version suffix lets a future
# format be a new label instead of a silent reinterpretation.
_SUBKEY_INFO = b"axisai/mobile-coach/message-id/v1"

# 144 bits, base64url without padding (same width as the sibling identities).
_TOKEN_BYTES = 18


def _subkey(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()


def message_id(secret, user_id, row_id):
    """The opaque, owner-bound API identity of one persisted Coach message."""
    message = f"{int(user_id)}\x00{int(row_id)}".encode("ascii")
    digest = hmac.new(_subkey(secret), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_TOKEN_BYTES]).decode("ascii")


def _iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def project_message(row, user_id, secret):
    return {
        "id": message_id(secret, user_id, row.id),
        "role": row.role,
        "text": row.content,
        "created_at": _iso(row.created_at),
        "interrupted": bool(row.interrupted),
    }


def unsaved_message(role, text):
    """A turn message the server produced but could not persist.

    Only reachable when the Coach memory is switched off (`AI_MEMORY_ENABLED=0`)
    or its write failed - the reply is still real, so it is returned, but with
    no identity: `id: null` is the contract's way of saying "this is not in
    your history and the next turn will not see it".
    """
    return {"id": None, "role": role, "text": text, "created_at": None,
            "interrupted": False}


def turn_payload(recorded_turn, question, answer, user_id, secret):
    if recorded_turn is None:
        messages = [unsaved_message("user", question),
                    unsaved_message("assistant", answer)]
    else:
        messages = [project_message(row, user_id, secret) for row in recorded_turn]
    return {"contract_version": CONTRACT_VERSION, "messages": messages}


def history_payload(user_id, secret):
    """The owner's canonical Coach history window, oldest first.

    Scope is the `user_id` argument, which the route takes from the verified
    Bearer principal and from nowhere else.
    """
    _conversation, rows = memory_manager.recent_messages(
        user_id, limit=HISTORY_LIMIT)
    return {
        "contract_version": CONTRACT_VERSION,
        "messages": [project_message(row, user_id, secret) for row in rows],
    }
