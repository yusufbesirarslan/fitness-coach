"""R6 release monitoring M1-M3: worker liveness, queue health, FatSecret expiry.

Three operational signals had no alarm in production (R6 readback 2026-11-09):

* M1 worker liveness: ``WorkerHeartbeat`` (1 per sample) from the worker's
  heartbeat thread, published only after the worker reached Redis. The
  alarm treats MISSING data as breaching, so a dead worker, a dead heartbeat
  thread, an unreachable Redis or a broken metric path all page the same way.
* M2 queue health: ``QueueDepth``, ``QueueOldestJobAgeSeconds``,
  ``QueueFailedJobs`` (RQ FailedJobRegistry size) and ``QueueFailedJobsNew``
  (its increase since the previous sample), sampled by the same thread from
  Redis only. The heartbeat thread enqueues the retention pass every 300 s
  and the erasure retry hourly, so the queue carries work even with zero
  user traffic. ``QueueDepth`` is LLEN, which keeps counting ids whose job
  hash expired (retention uses ttl 300), so a worker whose main loop stops
  consuming shows up as a queue that never drains; age covers the jobs
  without that short ttl.
* M3 FatSecret 24h expiry: ``ProviderRetentionPassCompleted`` (1 per finished
  pass) and ``ProviderRetentionOverdue`` (provider values past the FULL 24h
  still present, i.e. a licence breach; normally 0 because values go at
  22h), published by the retention job itself after ``expire_due``.

Design constraints:

* The publisher talks to CloudWatch directly (``put_metric_data``) with tight
  timeouts and one attempt. It does not use ``runtime_metrics``' buffer: the
  RQ worker forks a work horse per job and a buffer lock or a pooled HTTPS
  connection must never cross ``fork()``. The client is per process (pid).
* Namespace and switch are the existing ``FitX/Runtime`` ones
  (``RUNTIME_METRICS_ENABLED``): the production runtime role already allows
  ``cloudwatch:PutMetricData`` on exactly that namespace, so no IAM change.
* Every entry point is a no-op when disabled and never raises: monitoring
  must not be able to stop the heartbeat, the queue or the retention pass.
* The sampler never opens a database connection in the worker parent.
* One fixed dimension (``Service=worker``); no user, job id or payload.
"""
import logging
import os
import threading
from datetime import datetime, timezone

from app.config import BEDROCK_REGION, RUNTIME_METRICS_ENABLED, RUNTIME_METRICS_NAMESPACE

_log = logging.getLogger(__name__)

try:
    import boto3
    _BOTO3_AVAILABLE = True
except Exception:  # pragma: no cover - boto3 optional (lazy guard)
    boto3 = None
    _BOTO3_AVAILABLE = False

NAMESPACE = RUNTIME_METRICS_NAMESPACE
DIMENSIONS = ({"Name": "Service", "Value": "worker"},)

# M1
WORKER_HEARTBEAT = "WorkerHeartbeat"
# M2
QUEUE_DEPTH = "QueueDepth"
QUEUE_OLDEST_AGE = "QueueOldestJobAgeSeconds"
QUEUE_FAILED = "QueueFailedJobs"
QUEUE_FAILED_NEW = "QueueFailedJobsNew"
# M3
RETENTION_PASS = "ProviderRetentionPassCompleted"
RETENTION_OVERDUE = "ProviderRetentionOverdue"

METRICS = (WORKER_HEARTBEAT, QUEUE_DEPTH, QUEUE_OLDEST_AGE, QUEUE_FAILED,
           QUEUE_FAILED_NEW, RETENTION_PASS, RETENTION_OVERDUE)

# How often the worker heartbeat thread samples and publishes M1/M2.
SAMPLE_INTERVAL_SECONDS = 60
# Queued jobs inspected for the oldest age (head of the list; RQ is FIFO).
OLDEST_SCAN = 20

_client = None
_client_pid = None
_client_lock = threading.Lock()


def _reset_after_fork():
    """In the forked RQ work horse: forget the parent's client AND lock.

    The parent's heartbeat thread may hold ``_client_lock`` (or be inside a
    credential refresh of boto3's shared default session) at the instant the
    main thread forks; a child that inherited either would block forever.
    """
    global _client, _client_pid, _client_lock
    _client = None
    _client_pid = None
    _client_lock = threading.Lock()


if hasattr(os, "register_at_fork"):  # POSIX; the worker runs on Linux
    os.register_at_fork(after_in_child=_reset_after_fork)


def is_enabled():
    return RUNTIME_METRICS_ENABLED and _BOTO3_AVAILABLE


def _get_client():
    """A CloudWatch client owned by THIS process (recreated after fork).

    Built from a fresh ``boto3.session.Session`` so no credential cache or
    lock is shared with any other user of boto3's default session."""
    global _client, _client_pid
    pid = os.getpid()
    if _client_pid != pid:          # belt and braces if the fork hook did not run
        _reset_after_fork()
    with _client_lock:
        if _client is None:
            from botocore.config import Config as _BotoConfig
            _client = boto3.session.Session().client(
                "cloudwatch", region_name=BEDROCK_REGION,
                config=_BotoConfig(connect_timeout=2, read_timeout=3,
                                   retries={"max_attempts": 1}))
            _client_pid = pid
        return _client


def _datum(name, value, unit):
    return {"MetricName": name, "Dimensions": [dict(d) for d in DIMENSIONS],
            "Value": float(value), "Unit": unit}


def publish(values):
    """Send ``{metric: (value, unit)}``; returns the number sent. Never raises."""
    if not is_enabled() or not values:
        return 0
    data = [_datum(name, value, unit) for name, (value, unit) in values.items()]
    try:
        client = _get_client()
        for i in range(0, len(data), 20):
            client.put_metric_data(Namespace=NAMESPACE, MetricData=data[i:i + 20])
    except Exception as exc:  # noqa: BLE001 - monitoring must never propagate
        _log.warning("[RELEASE MONITORING] put_metric_data failed: %s", type(exc).__name__)
        return 0
    return len(data)


# ── M1 + M2: worker heartbeat thread (Redis only) ───────────────────────────

def _aware(moment):
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def sample_queue(queue, connection, now=None):
    """Read M2 from Redis. Returns a dict of raw values (no network to AWS)."""
    from rq.job import Job
    from rq.registry import FailedJobRegistry

    now = _aware(now) or datetime.now(timezone.utc)
    depth = queue.count
    oldest = 0.0
    ids = queue.get_job_ids(0, OLDEST_SCAN)
    for job in Job.fetch_many(ids, connection=connection):
        enqueued = _aware(getattr(job, "enqueued_at", None)) if job is not None else None
        if enqueued is not None:
            oldest = max(oldest, (now - enqueued).total_seconds())
    # cleanup=False: a pure read (no ZREMRANGEBYSCORE). Expired entries can
    # only make the count fall, which QueueFailedJobsNew already clamps at 0.
    failed = FailedJobRegistry(queue=queue).get_job_count(cleanup=False)
    return {"depth": int(depth), "oldest_age": max(oldest, 0.0), "failed": int(failed)}


class WorkerSampler:
    """Owned by the worker heartbeat thread. ``tick`` never raises."""

    def __init__(self, interval=SAMPLE_INTERVAL_SECONDS):
        self.interval = interval
        self.last = -float(interval)
        self.previous_failed = None

    def tick(self, monotonic_now, queue_factory, connection_factory, now=None):
        if monotonic_now - self.last < self.interval:
            return 0
        self.last = monotonic_now
        if not is_enabled():
            return 0
        try:
            connection = connection_factory()
            queue = queue_factory()
            if connection is None or queue is None:
                return 0   # no Redis: publish NOTHING, so M1 alarms on missing data
            connection.ping()
            values = {WORKER_HEARTBEAT: (1, "Count")}
            try:
                sample = sample_queue(queue, connection, now=now)
            except Exception as exc:  # noqa: BLE001 - heartbeat still counts
                _log.warning("[RELEASE MONITORING] queue sample failed: %s",
                             type(exc).__name__)
            else:
                new_failed = (0 if self.previous_failed is None
                              else max(sample["failed"] - self.previous_failed, 0))
                self.previous_failed = sample["failed"]
                values.update({
                    QUEUE_DEPTH: (sample["depth"], "Count"),
                    QUEUE_OLDEST_AGE: (sample["oldest_age"], "Seconds"),
                    QUEUE_FAILED: (sample["failed"], "Count"),
                    QUEUE_FAILED_NEW: (new_failed, "Count"),
                })
            return publish(values)
        except Exception as exc:  # noqa: BLE001 - Redis unreachable: no heartbeat
            _log.warning("[RELEASE MONITORING] worker sample failed: %s", type(exc).__name__)
            return 0


# ── M3: from the retention job, after the pass ──────────────────────────────

def record_retention_pass(overdue):
    """Called by the retention job once ``expire_due`` returned. Never raises."""
    try:
        return publish({RETENTION_PASS: (1, "Count"),
                        RETENTION_OVERDUE: (int(overdue), "Count")})
    except Exception:  # noqa: BLE001
        return 0
