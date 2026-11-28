"""One worker-side analytics pass: retention cleanup, dispatch, vendor erasure.

Scheduled by the worker heartbeat (``worker.py``) as a single unique RQ job,
also runnable as ``flask product-analytics-run``. Never runs in a request.

* Cleanup always runs (AxisAI-side retention only; no vendor call).
* Dispatch runs only while ``FITX_PRODUCT_ANALYTICS_ENABLED`` is ON and a
  complete vendor configuration exists, including erasure credentials
  (nothing is captured that could not be erased), AND the retention
  configuration fits the 90-day raw ceiling (``settings.retention_bounded``),
  AND no erasure job is overdue. Otherwise capture halts (fail closed) while
  erasure keeps running.
* Vendor erasure runs whenever erasure configuration exists, also while the
  flag is OFF (a rollback must not strand deletions owed for data already sent).
* Missing configuration: no vendor call at all (fail closed).

Logs carry counts and fixed outcome codes only.
"""
import os
from collections import ChainMap
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import OperationalError

from app.services import product_analytics
from app.services.product_analytics import dispatch, posthog_transport, retention, settings

import time

MAX_DISPATCH_BATCHES = 20
#: Wall-clock budget for the dispatch loop. With the 60 s erasure budget run
#: first, one pass stays well inside the 300 s RQ job timeout (review B-2).
DISPATCH_BUDGET_SECONDS = 150.0


def configured_transport(config):
    return posthog_transport.build(settings.vendor_settings(config))


def _dispatch(transport, now, policy, clock):
    totals = {"sent": 0, "fenced": 0, "retry": 0, "dropped": 0, "skipped": 0}
    stop_at = clock() + DISPATCH_BUDGET_SECONDS
    for _ in range(MAX_DISPATCH_BATCHES):
        if clock() >= stop_at:
            break
        try:
            step = dispatch.dispatch_once(transport, now=now, max_age=policy.outbox_max_age)
        except dispatch.DispatchAborted as aborted:
            # Typically the lock-hold cap ended a send that outlived it (B-3):
            # locks released, nothing committed. Count the attempt anyway so
            # backoff and MAX_ATTEMPTS bound re-sends (review P2-1).
            totals["aborted_after_send"] = totals.get("aborted_after_send", 0) + 1
            try:
                step = dispatch.record_aborted_attempt(aborted.row_ids, now=now)
                totals["retry"] += step["retry"]
                totals["dropped"] += step["dropped"]
            except OperationalError:
                totals["attempt_unrecorded"] = totals.get("attempt_unrecorded", 0) + 1
            break
        except OperationalError:
            # A database fault before any send (unavailable, deadlock, timeout).
            totals["aborted_before_send"] = totals.get("aborted_before_send", 0) + 1
            break
        for key in step:
            totals[key] += step[key]
        if step["sent"] + step["fenced"] + step["dropped"] == 0:
            break
    return totals


def run_pass(*, transport=None, now=None, config=None, clock=time.monotonic):
    # App config wins; deployment secrets otherwise arrive as environment.
    config = config if config is not None else ChainMap(current_app.config, os.environ)
    now = now or datetime.utcnow()
    policy = settings.retention_policy(config)
    stats = {"vendor": "unconfigured", **retention.cleanup(policy, now=now)}
    transport = transport if transport is not None else configured_transport(config)
    if transport is not None:
        stats["vendor"] = "configured"
        # Owed deletions first: a slow vendor can never starve erasure.
        if transport.can_erase:
            erasure = dispatch.process_erasures_once(transport, now=now,
                                                     overdue=policy.erasure_overdue)
            stats.update({f"erasure_{key}": value for key, value in erasure.items()})
        if product_analytics.enabled() and transport.can_capture and transport.can_erase:
            if not settings.retention_bounded(policy):
                stats["capture"] = "halted_retention_unbounded"
            elif stats.get("erasure_overdue"):
                stats["capture"] = "halted_erasure_overdue"
            else:
                stats["capture"] = "running"
                stats.update(_dispatch(transport, now, policy, clock))
    current_app.logger.info(
        "product_analytics event=pass %s",
        " ".join(f"{key}={stats[key]}" for key in sorted(stats)))
    if stats.get("erasure_overdue"):
        current_app.logger.error("product_analytics event=erasure_overdue count=%d",
                                 stats["erasure_overdue"])
    for stage in ("before_send", "after_send"):
        if stats.get(f"aborted_{stage}"):
            current_app.logger.error("product_analytics event=dispatch_aborted stage=%s count=%d",
                                     stage, stats[f"aborted_{stage}"])
    if stats.get("capture") == "halted_retention_unbounded":
        # Also stops rotation of already-sent subjects if the rotation key was
        # removed or made invalid: an operator must see it (review P3-1).
        current_app.logger.error("product_analytics event=capture_halted reason=retention_unbounded")
    return stats
