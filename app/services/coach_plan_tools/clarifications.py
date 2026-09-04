"""Bounded per-user store for one in-flight Coach plan clarification.

A needs-input turn (missing prescription, missing half, ambiguous workout,
or a fuzzy exercise suggestion) writes the grounded day/exercise/proposed
values here. The next turn may accept or complete that record. Chat history
is not an authority: assistant prose cannot mint a prescription, and a
client-supplied history cannot either.

The payload is a closed field set. TTL is short; a new mutation-shaped turn
discards a leftover record.

Production executable authority is the shared Redis record. Process-local
memory is used only when Redis is not configured (tests/dev). The signed
Flask session may be mirrored for transport/UI, but is never read to
execute a mutation. If the shared store is configured and cannot be read
or written, continuation fails closed rather than running from stale
worker-local state.
"""
import json
import time

_KEY = "_coach_plan_clarification"
_TTL_SECONDS = 30 * 60
_REDIS_PREFIX = "fitx:coach:plan_clarification:"
_TAKEN_ATTR = "_coach_plan_clarification_taken"
_MEMORY = {}


class ClarificationAuthorityUnavailable(Exception):
    """Shared continuation store could not be read or mutated. Fail closed."""


_REMEMBERABLE = frozenset({
    "missing_prescription",
    "missing_sets",
    "missing_reps",
    "exercise_suggest",
    "ambiguous_workout",
})

_OPERATIONS = frozenset({
    "add_exercise",
    "replace_exercise",
    "update_exercise_prescription",
    "remove_exercise",
})


def remember(user_id, payload):
    """Persist one grounded clarification for this authenticated user."""
    if not _valid_user(user_id) or not isinstance(payload, dict):
        return
    reason = str(payload.get("reason") or "")
    if reason not in _REMEMBERABLE:
        clear(user_id)
        return
    operation = str(payload.get("operation") or "add_exercise")
    if operation not in _OPERATIONS:
        operation = "add_exercise"
    record = {
        "user_id": int(user_id),
        "operation": operation,
        # Which mutation REQUEST this record belongs to. A continuation may
        # only ever complete the request that minted the record it reads, so
        # the record has to name that request; without it, "Monday" executes
        # whichever record happened to survive.
        "request_id": str(payload.get("request_id") or ""),
        "day": str(payload.get("day") or ""),
        "exercise": str(payload.get("exercise") or ""),
        "replacement": str(payload.get("replacement") or ""),
        "suggestion": str(payload.get("suggestion") or ""),
        "sets": _as_int(payload.get("sets")),
        "reps": _as_text(payload.get("reps")),
        "proposed_sets": _as_int(payload.get("proposed_sets")),
        "proposed_reps": _as_text(payload.get("proposed_reps")),
        "candidate_days": _as_days(payload.get("candidate_days")),
        "reason": reason,
        "created_at": time.time(),
    }
    _write(int(user_id), record)


def load(user_id):
    """The current user's still-valid clarification, or ``None``."""
    if not _valid_user(user_id):
        return None
    record = _read(int(user_id))
    if record is None:
        return None
    if record.get("user_id") != int(user_id):
        return None
    return record


def load_current():
    """Still-valid clarification for the turn's authenticated user."""
    try:
        from flask import g
        user_id = getattr(g, "_coach_plan_user_id", None)
    except RuntimeError:
        user_id = None
    if _valid_user(user_id):
        return load(user_id)
    return None


def consume(user_id):
    """Atomically take the record so a second continuation cannot execute it."""
    if not _valid_user(user_id):
        return None
    return _take(int(user_id))


def clear(user_id=None):
    """Drop the stored clarification. Owner-checked when ``user_id`` is set."""
    if user_id is None:
        try:
            from flask import g
            user_id = getattr(g, "_coach_plan_user_id", None)
        except RuntimeError:
            user_id = None
    _drop_session(user_id)
    if _valid_user(user_id):
        _drop(int(user_id))


def _write(user_id, record):
    # A record written after a ``consume`` in the same request supersedes the
    # one that was taken: a half-answered request ("4 sets" answered, reps
    # still missing) consumes its own record and immediately stores the
    # merged one, and the next turn must read the merged one. Retiring the
    # per-request stash here is what keeps ``_request_taken`` from shadowing
    # it — see ``_stash_taken``.
    _clear_taken()
    redis_client = _redis()
    if redis_client is not None:
        try:
            redis_client.setex(
                _REDIS_PREFIX + str(user_id),
                _TTL_SECONDS,
                json.dumps(record, ensure_ascii=False),
            )
        except Exception:
            return
        _drop_memory(user_id)
        _mirror_session(record)
        return
    memory = _memory()
    if memory is not None:
        memory[user_id] = record
    _mirror_session(record)


def _read(user_id):
    redis_client = _redis()
    if redis_client is not None:
        try:
            raw = redis_client.get(_REDIS_PREFIX + str(user_id))
        except Exception:
            raise ClarificationAuthorityUnavailable
        if not raw:
            return _request_taken(user_id)
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return _fresh(record)
    taken = _request_taken(user_id)
    if taken is not None:
        return taken
    memory = _memory()
    if memory is not None:
        return _fresh(memory.get(user_id))
    return None


def _take(user_id):
    redis_client = _redis()
    if redis_client is not None:
        try:
            raw = _take_redis(redis_client, user_id)
        except Exception:
            raise ClarificationAuthorityUnavailable
        _drop_memory(user_id)
        _drop_session(user_id)
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            return None
        fresh = _fresh(record)
        if fresh is None or fresh.get("user_id") != user_id:
            return None
        _stash_taken(fresh)
        return fresh
    memory = _memory()
    record = None
    if memory is not None:
        record = _fresh(memory.pop(user_id, None))
    _drop_session(user_id)
    if record is None or record.get("user_id") != user_id:
        return None
    _stash_taken(record)
    return record


def _stash_taken(record):
    try:
        from flask import g
        setattr(g, _TAKEN_ATTR, record)
    except RuntimeError:
        pass


def _clear_taken():
    try:
        from flask import g
        if hasattr(g, _TAKEN_ATTR):
            delattr(g, _TAKEN_ATTR)
    except RuntimeError:
        pass


def _request_taken(user_id):
    try:
        from flask import g
        record = getattr(g, _TAKEN_ATTR, None)
    except RuntimeError:
        return None
    fresh = _fresh(record)
    if fresh is None or fresh.get("user_id") != int(user_id):
        return None
    return fresh


def _take_redis(redis_client, user_id):
    key = _REDIS_PREFIX + str(user_id)
    getter = getattr(redis_client, "getdel", None)
    if callable(getter):
        return getter(key)
    raw = redis_client.get(key)
    if raw:
        redis_client.delete(key)
    return raw


def _drop(user_id):
    _drop_memory(user_id)
    redis_client = _redis()
    if redis_client is not None:
        try:
            redis_client.delete(_REDIS_PREFIX + str(user_id))
        except Exception:
            pass


def _drop_memory(user_id):
    memory = _memory()
    if memory is not None:
        memory.pop(user_id, None)


def _mirror_session(record):
    store = _session_store()
    if store is None:
        return
    store[_KEY] = record
    try:
        store.modified = True
    except Exception:
        pass


def _drop_session(user_id):
    store = _session_store()
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


def _fresh(record):
    if not isinstance(record, dict):
        return None
    created = record.get("created_at")
    try:
        created = float(created)
    except (TypeError, ValueError):
        created = 0.0
    if created <= 0 or (time.time() - created) > _TTL_SECONDS:
        return None
    return record


def _session_store():
    try:
        from flask import session
        session.get(_KEY)
        return session
    except RuntimeError:
        return None
    except Exception:
        return None


def _memory():
    return _MEMORY


def _redis():
    try:
        from app.extensions import redis_client
    except Exception:
        return None
    return redis_client


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


def _as_days(value):
    if not value:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    out = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out
