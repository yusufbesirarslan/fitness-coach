"""Whether the runtime principal can actually invoke the configured model.

Deep health used to answer this with ``BEDROCK_ENABLED``, which is a copy of a
config line. For weeks that field read ``enabled`` while 100% of Bedrock calls
returned 403 — the app's IAM identity had no ``bedrock:InvokeModel`` — and the
deploy health gate passed every time, because nothing in the pipeline had ever
asked the provider a question it could answer wrongly.

So this module asks. Configuration and reachability are reported as SEPARATE
facts, because they fail separately and demand different fixes: ``configured``
is a deployment intent, ``reachable`` is a permission-and-network truth.

Three properties keep an honest probe from becoming its own outage:

* **It is the smallest request that still proves the claim.** One streaming
  message capped at a single output token. Streaming, not blocking, because the
  live Coach path is ``/ask/stream`` and ``InvokeModelWithResponseStream`` is a
  *different* IAM action from ``InvokeModel`` — a blocking probe would have gone
  green on a policy that still broke every real conversation.
* **It is bounded in time.** A short timeout of its own, never the Coach's turn
  budget: a health endpoint must answer before the thing polling it gives up.
* **It is cached, asymmetrically.** Success is held long enough that health
  polling cannot become an inference workload; failure is held only briefly, so
  the deploy gate's own retries re-probe instead of re-reading one bad moment.
  Both directions are bounded — a stuck cache is just a slower version of the
  config echo this module replaces.

Never raises. A probe that could break ``/health`` would be a worse defect than
the blind spot it closes.

**Deliberately NOT behind ``blocking_concurrency_slot``.** The house rule is
that a synchronous provider round-trip takes a capacity permit, and the reason
given for it is to keep ``/health`` out of the AI queue. Here ``/health`` *is*
the caller, and a saturation refusal would surface as a failed health check and
a false rollback — the gate would cause the outage it exists to prevent. The
cost is bounded the other way instead: only ``?deep=1`` probes (the container
healthcheck hits the shallow path), that view is internal-network only, one
short-timeout round trip, and the cache means at most one call per TTL per
process. Do not "fix" this by adding the permit.
"""
import threading
import time

from app.config import BEDROCK_ENABLED, BEDROCK_MODEL
from app.services import provider_failure

#: One output token is enough: authorization is settled before generation.
PROBE_MAX_TOKENS = 1
PROBE_TIMEOUT_SECONDS = 5.0
#: Asymmetric on purpose — see the module docstring.
CACHE_TTL_OK_SECONDS = 300.0
CACHE_TTL_FAIL_SECONDS = 15.0

_lock = threading.Lock()
#: ``(monotonic_deadline, result)``. Process-local: a shared cache would need a
#: dependency that can itself be down while this is the thing being asked.
_cached = None


def _probe_once():
    """Return ``(reachable, failure_category)``. Never raises."""
    from app.extensions import bedrock_client

    try:
        with bedrock_client.messages.stream(
            model=BEDROCK_MODEL,
            max_tokens=PROBE_MAX_TOKENS,
            messages=[{"role": "user", "content": "ping"}],
            timeout=PROBE_TIMEOUT_SECONDS,
        ) as stream:
            # Read to the end: opening the context manager is not proof the
            # provider accepted the call. The 403 arrives on the wire.
            stream.get_final_message()
        return True, None
    except Exception as exc:
        return False, provider_failure.classify(exc)


def probe(force=False):
    """Return the bedrock health block for deep health.

    ``{"configured": bool, "reachable": bool}``, plus ``"failure"`` (a bounded
    ``provider_failure`` category) only when a probe actually ran and failed.

    Not configured means not probed, and reports ``reachable: False`` — the
    caller decides what that means, and the gate only fails on the
    configured-but-unreachable pair. Reporting an unconfigured provider as
    "reachable" would be the same lie in the other direction.
    """
    if not BEDROCK_ENABLED:
        return {"configured": False, "reachable": False}

    global _cached
    now = time.monotonic()
    if not force:
        with _lock:
            cached = _cached
        if cached is not None and now < cached[0]:
            return dict(cached[1])

    reachable, failure = _probe_once()
    result = {"configured": True, "reachable": reachable}
    if not reachable:
        result["failure"] = failure
    ttl = CACHE_TTL_OK_SECONDS if reachable else CACHE_TTL_FAIL_SECONDS
    with _lock:
        _cached = (time.monotonic() + ttl, dict(result))
    return result


def reset_cache():
    """Drop the cached verdict. For tests and for an operator forcing a re-probe."""
    global _cached
    with _lock:
        _cached = None
