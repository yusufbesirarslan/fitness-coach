"""Bounded per-user store for one in-flight Coach plan clarification.

A needs-input turn (missing prescription, missing half, or a fuzzy
exercise suggestion) writes the grounded day/exercise/proposed values
here. The next turn may accept or complete that record. Chat history is
not an authority: assistant prose cannot mint a prescription, and a
client-supplied history cannot either.

Lives on the signed Flask session so it survives the next HTTP request
without a schema migration or a new conversation-state framework. The
payload is a closed field set. TTL is short; a new mutation-shaped turn
discards a leftover record.
"""
import time

_KEY = "_coach_plan_clarification"
_TTL_SECONDS = 30 * 60

_REMEMBERABLE = frozenset({
    "missing_prescription",
    "missing_sets",
    "missing_reps",
    "exercise_suggest",
})


def remember(user_id, payload):
    """Persist one grounded clarification for this authenticated user."""
    if not _valid_user(user_id) or not isinstance(payload, dict):
        return
    reason = str(payload.get("reason") or "")
    if reason not in _REMEMBERABLE:
        clear(user_id)
        return
    record = {
        "user_id": int(user_id),
        "day": str(payload.get("day") or ""),
        "exercise": str(payload.get("exercise") or ""),
        "suggestion": str(payload.get("suggestion") or ""),
        "sets": _as_int(payload.get("sets")),
        "reps": _as_text(payload.get("reps")),
        "proposed_sets": _as_int(payload.get("proposed_sets")),
        "proposed_reps": _as_text(payload.get("proposed_reps")),
        "reason": reason,
        "created_at": time.time(),
    }
    store = _store()
    if store is None:
        return
    store[_KEY] = record
    try:
        store.modified = True
    except Exception:
        pass


def load(user_id):
    """The current user's still-valid clarification, or ``None``."""
    record = load_current()
    if record is None:
        return None
    if not _valid_user(user_id) or record.get("user_id") != int(user_id):
        return None
    return record


def load_current():
    """Session-scoped clarification for this request, if still fresh."""
    store = _store()
    if store is None:
        return None
    record = store.get(_KEY)
    if not isinstance(record, dict):
        return None
    created = record.get("created_at")
    try:
        created = float(created)
    except (TypeError, ValueError):
        created = 0.0
    if created <= 0 or (time.time() - created) > _TTL_SECONDS:
        store.pop(_KEY, None)
        return None
    return record


def clear(user_id=None):
    """Drop the stored clarification. Owner-checked when ``user_id`` is set."""
    store = _store()
    if store is None:
        return
    record = store.get(_KEY)
    if user_id is not None and isinstance(record, dict):
        if record.get("user_id") != int(user_id):
            return
    store.pop(_KEY, None)
    try:
        store.modified = True
    except Exception:
        pass


def _store():
    try:
        from flask import session
        session.get(_KEY)
        return session
    except RuntimeError:
        return None
    except Exception:
        return None


def _valid_user(user_id):
    return isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0


def _as_int(value):
    if value is None or value is False or value is True:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _as_text(value):
    if value is None or value is False or value is True:
        return None
    text = str(value).strip()
    return text or None
