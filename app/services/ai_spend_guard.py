"""AI spend guard (Phase 2 P2-C): finite provider-call ceilings.

The per-route rate limits (`AI_RATELIMIT`, `BEDROCK_RATELIMIT`,
`AI_BURST_RATELIMIT`) are counted PER ROUTE and PER HOUR, and premium accounts
have no weekly quota at all. Summed across every AI route and multiplied by the
coach tool loop, one account could still drive an effectively unbounded number
of paid provider calls, and nothing bounded the aggregate across accounts.

This module is the emergency boundary, not a product quota:

- The unit is ONE PROVIDER CALL. It is known before the call is made; cost in
  tokens is only known afterwards and the app's own per-turn token telemetry is
  partial. Every call's worst-case cost is bounded (max_tokens caps output,
  context budgets cap input), so a call ceiling is a spend ceiling.
- `charge()` runs BEFORE the provider is invoked. Every provider call enters
  `ai_gate.model_concurrency_slot()` (menu OCR calls `charge()` itself), so a
  rejected call never reaches the provider — including tool-loop rounds,
  recovery retries and fan-out batches, each of which is a real paid call.
- Two classes: `heavy` (Bedrock/Sonnet) and `light` (OpenAI gpt-4o-mini).
  Per-account daily ceilings bound one account; global hourly/daily ceilings
  bound the sum over all accounts (multi-account abuse, runaway loops, bugs).
- Admission is race-safe across threads, processes and hosts: the Redis
  counters are incremented in one MULTI/EXEC transaction and a call is admitted
  only if every post-increment value is within its limit. A rejected call
  decrements what it added. Two concurrent callers can never both be admitted
  past a limit; at the boundary the error is always a spurious rejection, never
  an over-admission.
- Redis unavailable -> process-local counters with the same limits. That is
  neither fail-open (unbounded spend) nor fail-closed (AI outage on a Redis
  blip): the bound degrades to at most one limit per process (web + worker).

A rejection raises `AISpendLimitExceeded`, a `BlockingConcurrencyLimit`, so
every existing capacity handler already turns it into a deterministic
localized "busy" response (503 + Retry-After / mobile retryable envelope /
coach soft error) without another provider call. The message never states
which ceiling or threshold was hit.
"""
import logging
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

from app.services.ai_gate import BlockingConcurrencyLimit

_log = logging.getLogger(__name__)

HEAVY_PROVIDERS = frozenset({"bedrock", "bedrock-stream"})

_KEY_PREFIX = "ai:spend:v1"
_WINDOW_SECONDS = {"h": 3600, "d": 86400}
# Keys outlive their window so a late EXPIRE never truncates a live bucket.
_KEY_TTL_GRACE_SECONDS = 300


def _env_limit(name, default):
    """Non-negative int from env; 0 disables that one ceiling; junk -> default."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


ENABLED = os.getenv("AI_SPEND_GUARD_ENABLED", "1") != "0"

# (scope, class, window) -> maximum admitted provider calls per window.
# Defaults sit far above observed production use (30 days to 2026-09-23: 197
# Bedrock calls in total, busiest hour 40, busiest single account 30 coach
# turns in a day) so no normal account, free or premium, meets them.
LIMITS = {
    ("user", "heavy", "d"): _env_limit("AI_SPEND_USER_HEAVY_PER_DAY", 200),
    ("user", "light", "d"): _env_limit("AI_SPEND_USER_LIGHT_PER_DAY", 400),
    ("global", "heavy", "h"): _env_limit("AI_SPEND_GLOBAL_HEAVY_PER_HOUR", 300),
    ("global", "heavy", "d"): _env_limit("AI_SPEND_GLOBAL_HEAVY_PER_DAY", 1500),
    ("global", "light", "d"): _env_limit("AI_SPEND_GLOBAL_LIGHT_PER_DAY", 5000),
}


class AISpendLimitExceeded(BlockingConcurrencyLimit):
    """A provider call was refused by a spend ceiling before it was made.

    `scope` ("user" | "global") and `provider_class` are for logs and metrics
    only; the message is deliberately identical for every ceiling.
    """

    def __init__(self, scope, provider_class):
        super().__init__("AI capacity temporarily unavailable")
        self.scope = scope
        self.provider_class = provider_class


# ── Subject (whose budget a call spends) ────────────────────────────────────

_tls = threading.local()
_UNSET = object()


@contextmanager
def subject_scope(subject):
    """Attribute calls made on THIS thread to `subject` (user id or None).

    Needed where provider calls run on a thread with no request context: the
    streaming producer thread and fan-out executors.
    """
    previous = getattr(_tls, "subject", _UNSET)
    _tls.subject = subject
    try:
        yield
    finally:
        if previous is _UNSET:
            del _tls.subject
        else:
            _tls.subject = previous


def current_subject():
    """The account a provider call is attributed to, or None (global only)."""
    override = getattr(_tls, "subject", _UNSET)
    if override is not _UNSET:
        return override
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return None
        mobile_user = getattr(g, "mobile_user", None)
        if mobile_user is not None:
            return getattr(mobile_user, "id", None)
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return current_user.id
    except Exception:
        return None
    return None


def bind_subject(fn):
    """Capture the caller's subject now and apply it when `fn` runs elsewhere."""
    subject = current_subject()

    @wraps(fn)
    def wrapper(*args, **kwargs):
        with subject_scope(subject):
            return fn(*args, **kwargs)
    return wrapper


# ── Counters ────────────────────────────────────────────────────────────────

def provider_class(provider):
    return "heavy" if str(provider) in HEAVY_PROVIDERS else "light"


def _bucket(now, window):
    return int(now // _WINDOW_SECONDS[window])


def _planned_keys(cls, subject, now):
    """[(key, limit, ttl_seconds, scope)] for every active ceiling of `cls`."""
    planned = []
    for (scope, klass, window), limit in LIMITS.items():
        if klass != cls or limit <= 0:
            continue
        if scope == "user":
            if subject is None:
                continue
            owner = f"user:{subject}"
        else:
            owner = "global"
        key = f"{_KEY_PREFIX}:{owner}:{cls}:{window}:{_bucket(now, window)}"
        ttl = _WINDOW_SECONDS[window] + _KEY_TTL_GRACE_SECONDS
        planned.append((key, limit, ttl, scope))
    # Global first: when both are exceeded the operator must see the global one.
    planned.sort(key=lambda item: item[3] != "global")
    return planned


def _get_redis():
    try:
        from app.extensions import redis_client
        return redis_client
    except Exception:
        return None


class _RedisUnavailable(Exception):
    pass


def _redis_admit(client, planned):
    """Return the scope that rejects the call, or None when admitted."""
    try:
        pipe = client.pipeline(transaction=True)
        for key, _limit, ttl, _scope in planned:
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
        results = pipe.execute()
    except Exception as exc:
        raise _RedisUnavailable() from exc
    counts = results[0::2]
    for (_key, limit, _ttl, scope), count in zip(planned, counts):
        if int(count) > limit:
            try:
                undo = client.pipeline(transaction=True)
                for key, _l, _t, _s in planned:
                    undo.decr(key)
                undo.execute()
            except Exception:
                # Left over-counted until the window rolls: only ever stricter.
                _log.warning("[AI-SPEND] compensation failed; counters over-count until window end")
            return scope
    return None


_local_lock = threading.Lock()
_local_counts = {}
_LOCAL_MAX_KEYS = 10000


def _local_admit(planned, now):
    with _local_lock:
        if len(_local_counts) > _LOCAL_MAX_KEYS:
            current = {f"{w}:{_bucket(now, w)}" for w in _WINDOW_SECONDS}
            for key in list(_local_counts):
                if ":".join(key.rsplit(":", 2)[-2:]) not in current:
                    del _local_counts[key]
        for key, limit, _ttl, scope in planned:
            if _local_counts.get(key, 0) + 1 > limit:
                return scope
        for key, _limit, _ttl, _scope in planned:
            _local_counts[key] = _local_counts.get(key, 0) + 1
    return None


_redis_warned_at = 0.0


def _warn_redis_degraded():
    global _redis_warned_at
    now = time.monotonic()
    if now - _redis_warned_at >= 60:
        _redis_warned_at = now
        _log.warning("[AI-SPEND] Redis unavailable; enforcing process-local ceilings")


def _record_rejection(scope, cls, provider, subject):
    _log.warning("[AI-SPEND] provider call refused scope=%s class=%s provider=%s user=%s",
                 scope, cls, provider, subject if subject is not None else "-")
    try:
        from app.services import runtime_metrics
        runtime_metrics.increment(
            "AiSpendGuardRejections", dimensions={"Scope": scope, "Class": cls})
    except Exception:
        pass


def charge(provider):
    """Admit one provider call or raise AISpendLimitExceeded. Call BEFORE the call."""
    if not ENABLED:
        return
    cls = provider_class(provider)
    subject = current_subject()
    now = time.time()
    planned = _planned_keys(cls, subject, now)
    if not planned:
        return
    client = _get_redis()
    rejected = None
    if client is not None:
        try:
            rejected = _redis_admit(client, planned)
        except _RedisUnavailable:
            _warn_redis_degraded()
            rejected = _local_admit(planned, now)
    else:
        rejected = _local_admit(planned, now)
    if rejected is not None:
        _record_rejection(rejected, cls, provider, subject)
        raise AISpendLimitExceeded(rejected, cls)


def _reset_local_for_tests():
    with _local_lock:
        _local_counts.clear()
