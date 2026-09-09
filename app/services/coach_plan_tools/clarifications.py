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
import math
import re
import time

from flask import current_app

from app.observability import current_request_id

_KEY = "_coach_plan_clarification"
_TTL_SECONDS = 30 * 60
_REDIS_PREFIX = "fitx:coach:plan_clarification:"
_TAKEN_ATTR = "_coach_plan_clarification_taken"
_MEMORY = {}

_OPERATION_LABELS = {
    "add_exercise": "add",
    "replace_exercise": "replace",
    "update_exercise_prescription": "update",
    "remove_exercise": "remove",
}

_MISSING_FIELDS = {
    "missing_prescription": "sets,reps",
    "missing_sets": "sets",
    "missing_reps": "reps",
    "exercise_suggest": "exercise",
    "ambiguous_workout": "day",
}

_SAFE_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

_EVENTS = frozenset({
    "created", "updated", "superseded", "consumed", "deleted",
    "expired_or_missing", "rejected",
})

_LIFECYCLE_REASONS = frozenset({
    "initial_write",
    "monotonic_merge",
    "replaced_by_newer_record",
    "incompatible_request",
    "request_boundary_cancel",
    "request_boundary_new_exercise",
    "request_boundary_noncontinuation",
    "continuation_consume",
    "explicit_retirement",
    "nonrememberable_state",
    "mutation_applied",
    "proposal_created",
    "authoritative_record_mismatch",
    "redis_unavailable",
    "record_missing",
    "ttl_expired",
    "invalid_lifecycle_reason",
})


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
        clear(user_id, reason="nonrememberable_state")
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
    return record


def load(user_id):
    """The current user's still-valid clarification, or ``None``."""
    if not _valid_user(user_id):
        return None
    record = _read(int(user_id))
    if record is None:
        return None
    if record.get("user_id") != int(user_id):
        _emit_lifecycle(
            event="rejected", reason="authoritative_record_mismatch",
            record=record, backend=_backend_name(),
            record_present_before=True, record_present_after=True)
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


def reject(record, reason):
    """Record a bounded fail-closed decision after authority was inspected."""
    _emit_lifecycle(
        event="rejected", reason=reason, record=record,
        backend=_backend_name(), record_present_before=False,
        record_present_after=False)


def clear(user_id=None, *, reason="explicit_retirement", event="deleted"):
    """Drop the stored clarification. Owner-checked when ``user_id`` is set."""
    if user_id is None:
        try:
            from flask import g
            user_id = getattr(g, "_coach_plan_user_id", None)
        except RuntimeError:
            user_id = None
    _drop_session(user_id)
    if _valid_user(user_id):
        _drop(int(user_id), reason=reason, event=event)


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
        previous = None
        ttl_before = None
        try:
            previous = _decode(redis_client.get(_redis_key(user_id)))
            ttl_before = _redis_ttl(redis_client, user_id)
        except Exception:
            # Observability must not become another availability dependency.
            pass
        try:
            redis_client.setex(
                _redis_key(user_id),
                _TTL_SECONDS,
                json.dumps(record, ensure_ascii=False),
            )
        except Exception:
            _emit_lifecycle(
                event="rejected", reason="redis_unavailable", record=record,
                backend="redis", record_present_before=None,
                record_present_after=None,
                ttl_before=ttl_before, ttl_after=ttl_before)
            return
        _drop_memory(user_id)
        _mirror_session(record)
        same_lineage = bool(
            previous
            and previous.get("request_id") == record.get("request_id"))
        _emit_lifecycle(
            event="updated" if previous is not None else "created",
            reason=("monotonic_merge" if same_lineage else
                    "replaced_by_newer_record" if previous is not None else
                    "initial_write"),
            record=record, backend="redis",
            record_present_before=previous is not None,
            record_present_after=True, ttl_before=ttl_before,
            ttl_after=_redis_ttl(redis_client, user_id, default=_TTL_SECONDS))
        return
    memory = _memory()
    previous = _fresh(memory.get(user_id)) if memory is not None else None
    if memory is not None:
        memory[user_id] = record
    _mirror_session(record)
    _emit_lifecycle(
        event="updated" if previous is not None else "created",
        reason=("monotonic_merge" if previous is not None and
                previous.get("request_id") == record.get("request_id") else
                "replaced_by_newer_record" if previous is not None else
                "initial_write"),
        record=record,
        backend="memory",
        record_present_before=previous is not None,
        record_present_after=True,
        ttl_before=_remaining_ttl(previous),
        ttl_after=_TTL_SECONDS,
    )


def _remaining_ttl(record):
    if not isinstance(record, dict):
        return None
    try:
        remaining = _TTL_SECONDS - (time.time() - float(record["created_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, min(_TTL_SECONDS, int(math.ceil(remaining))))


def _redis_key(user_id):
    return _REDIS_PREFIX + str(user_id)


def _decode(raw):
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _redis_ttl(redis_client, user_id, default=None):
    ttl = getattr(redis_client, "ttl", None)
    if not callable(ttl):
        return default
    try:
        value = int(ttl(_redis_key(user_id)))
    except Exception:
        return default
    return value if value >= 0 else None


def _backend_name():
    return "redis" if _redis() is not None else "memory"


def _safe_request_id(record):
    value = str((record or {}).get("request_id") or "")
    return value if _SAFE_ID.fullmatch(value) else "-"


def _emit_lifecycle(*, event, reason, record, backend,
                    record_present_before, record_present_after,
                    ttl_before=None, ttl_after=None):
    """Emit one bounded, PII-free clarification lifecycle transition."""
    operation = _OPERATION_LABELS.get(
        str((record or {}).get("operation") or ""), "-")
    missing_fields = _MISSING_FIELDS.get(
        str((record or {}).get("reason") or ""), "-")
    event = event if event in _EVENTS else "rejected"
    reason = (reason if reason in _LIFECYCLE_REASONS
              else "invalid_lifecycle_reason")
    backend = backend if backend in {"redis", "memory"} else "-"
    try:
        correlation_id = current_request_id()
    except RuntimeError:
        correlation_id = "-"
    if not _SAFE_ID.fullmatch(str(correlation_id)):
        correlation_id = "-"
    try:
        current_app.logger.info(
            "component=coach_clarification event=%s reason=%s "
            "operation=%s request_id=%s backend=%s missing_fields=%s "
            "record_present_before=%s record_present_after=%s "
            "ttl_before=%s ttl_after=%s correlation_id=%s",
            event, reason, operation, _safe_request_id(record), backend,
            missing_fields,
            _format_presence(record_present_before),
            _format_presence(record_present_after),
            "null" if ttl_before is None else int(ttl_before),
            "null" if ttl_after is None else int(ttl_after),
            correlation_id,
        )
    except (RuntimeError, AttributeError, TypeError, ValueError):
        pass


def _format_presence(value):
    if value is None:
        return "null"
    return str(bool(value)).lower()


def _read(user_id):
    redis_client = _redis()
    if redis_client is not None:
        try:
            raw = redis_client.get(_redis_key(user_id))
        except Exception:
            _emit_lifecycle(
                event="rejected", reason="redis_unavailable",
                record=_diagnostic_record(user_id),
                backend="redis", record_present_before=None,
                record_present_after=None)
            raise ClarificationAuthorityUnavailable
        if not raw:
            taken = _request_taken(user_id)
            if taken is not None:
                return taken
            _emit_lifecycle(
                event="expired_or_missing", reason="record_missing",
                record=_diagnostic_record(user_id), backend="redis",
                record_present_before=False,
                record_present_after=False,
                ttl_before=_redis_ttl(redis_client, user_id), ttl_after=None)
            return None
        record = _decode(raw)
        if record is None:
            _emit_lifecycle(
                event="rejected", reason="authoritative_record_mismatch",
                record=None, backend="redis", record_present_before=True,
                record_present_after=True)
            return None
        fresh = _fresh(record)
        if fresh is None:
            _emit_lifecycle(
                event="expired_or_missing", reason="ttl_expired",
                record=record, backend="redis", record_present_before=True,
                record_present_after=False, ttl_before=0, ttl_after=0)
        return fresh
    taken = _request_taken(user_id)
    if taken is not None:
        return taken
    memory = _memory()
    raw = memory.get(user_id) if memory is not None else None
    if raw is None:
        _emit_lifecycle(
            event="expired_or_missing", reason="record_missing",
            record=_diagnostic_record(user_id),
            backend="memory", record_present_before=False,
            record_present_after=False)
        return None
    fresh = _fresh(raw)
    if fresh is None:
        _emit_lifecycle(
            event="expired_or_missing", reason="ttl_expired", record=raw,
            backend="memory", record_present_before=True,
            record_present_after=False, ttl_before=0, ttl_after=0)
    return fresh


def _take(user_id):
    redis_client = _redis()
    if redis_client is not None:
        ttl_before = _redis_ttl(redis_client, user_id)
        try:
            raw = _take_redis(redis_client, user_id)
        except Exception:
            _emit_lifecycle(
                event="rejected", reason="redis_unavailable",
                record=_diagnostic_record(user_id),
                backend="redis", record_present_before=None,
                record_present_after=None, ttl_before=ttl_before)
            raise ClarificationAuthorityUnavailable
        _drop_memory(user_id)
        _drop_session(user_id)
        if not raw:
            _emit_lifecycle(
                event="expired_or_missing", reason="record_missing",
                record=_diagnostic_record(user_id), backend="redis",
                record_present_before=False,
                record_present_after=False, ttl_before=ttl_before)
            return None
        record = _decode(raw)
        if record is None:
            _emit_lifecycle(
                event="rejected", reason="authoritative_record_mismatch",
                record=None, backend="redis", record_present_before=True,
                record_present_after=False, ttl_before=ttl_before)
            return None
        fresh = _fresh(record)
        if fresh is None:
            _emit_lifecycle(
                event="expired_or_missing", reason="ttl_expired",
                record=record, backend="redis", record_present_before=True,
                record_present_after=False, ttl_before=ttl_before)
            return None
        if fresh.get("user_id") != user_id:
            _emit_lifecycle(
                event="rejected", reason="authoritative_record_mismatch",
                record=fresh, backend="redis", record_present_before=True,
                record_present_after=False, ttl_before=ttl_before)
            return None
        _stash_taken(fresh)
        _emit_lifecycle(
            event="consumed", reason="continuation_consume", record=fresh,
            backend="redis", record_present_before=True,
            record_present_after=False, ttl_before=ttl_before)
        return fresh
    memory = _memory()
    raw = None
    if memory is not None:
        raw = memory.pop(user_id, None)
    record = _fresh(raw)
    _drop_session(user_id)
    if raw is None:
        _emit_lifecycle(
            event="expired_or_missing", reason="record_missing",
            record=_diagnostic_record(user_id),
            backend="memory", record_present_before=False,
            record_present_after=False)
        return None
    if record is None:
        _emit_lifecycle(
            event="expired_or_missing", reason="ttl_expired", record=raw,
            backend="memory", record_present_before=True,
            record_present_after=False, ttl_before=0)
        return None
    if record.get("user_id") != user_id:
        _emit_lifecycle(
            event="rejected", reason="authoritative_record_mismatch",
            record=record, backend="memory", record_present_before=True,
            record_present_after=False)
        return None
    _stash_taken(record)
    _emit_lifecycle(
        event="consumed", reason="continuation_consume", record=record,
        backend="memory", record_present_before=True,
        record_present_after=False, ttl_before=_remaining_ttl(record))
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
    except Exception:
        return None
    fresh = _fresh(record)
    if fresh is None or fresh.get("user_id") != int(user_id):
        return None
    return fresh


def _take_redis(redis_client, user_id):
    key = _redis_key(user_id)
    getter = getattr(redis_client, "getdel", None)
    if callable(getter):
        return getter(key)
    raw = redis_client.get(key)
    if raw:
        redis_client.delete(key)
    return raw


def _drop(user_id, *, reason, event):
    redis_client = _redis()
    if redis_client is not None:
        record = None
        ttl_before = None
        try:
            record = _decode(redis_client.get(_redis_key(user_id)))
            ttl_before = _redis_ttl(redis_client, user_id)
        except Exception:
            pass
        # Preserve the original cleanup order: the process-local copy is never
        # executable while Redis is configured, even if Redis DELETE fails.
        _drop_memory(user_id)
        try:
            deleted = bool(redis_client.delete(_redis_key(user_id)))
        except Exception:
            _emit_lifecycle(
                event="rejected", reason="redis_unavailable", record=record,
                backend="redis", record_present_before=None,
                record_present_after=None,
                ttl_before=ttl_before, ttl_after=ttl_before)
            return
        _emit_lifecycle(
            event=event, reason=reason, record=record, backend="redis",
            record_present_before=deleted, record_present_after=False,
            ttl_before=ttl_before)
        return
    memory = _memory()
    record = memory.get(user_id) if memory is not None else None
    _drop_memory(user_id)
    _emit_lifecycle(
        event=event, reason=reason, record=record, backend="memory",
        record_present_before=record is not None, record_present_after=False,
        ttl_before=_remaining_ttl(record))


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


def _diagnostic_record(user_id):
    """Return only bounded metadata input; never executable authority."""
    store = _session_store()
    if store is None:
        return None
    record = store.get(_KEY)
    if not isinstance(record, dict):
        return None
    if record.get("user_id") != int(user_id):
        return None
    return record


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
