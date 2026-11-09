"""R6 release monitoring M1-M3 (app/services/release_monitoring.py + alarms).

Three layers:

1. Publisher and sampler units: fail-safe (never raise, nothing when disabled
   or when Redis is unreachable), one fixed dimension, fork-safe client.
2. The CloudFormation template (infra/r6-release-monitoring/template.yaml):
   every alarm reads a metric the code publishes, with the same namespace and
   dimension; missing-data treatment is deliberate per alarm; M1 escalates at
   60 minutes; every action targets a parameterised topic.
3. Alarm state transitions: the REAL sampler / retention reporter publish into
   a recording client on a simulated clock, and an explicit model of
   CloudWatch's M-of-N evaluation (``_evaluate``) decides each alarm's state
   from the template's own settings. The model is a test oracle, not AWS:
   real notification delivery is verified in production by the bounded
   SetAlarmState drill listed in the README (founder-approved step).
"""
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.services import release_monitoring as rm

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infra" / "r6-release-monitoring" / "template.yaml"


# ── fakes ───────────────────────────────────────────────────────────────────

class RecordingClient:
    def __init__(self, clock=None, fail=False):
        self.calls, self.points, self.clock, self.fail = [], [], clock, fail

    def put_metric_data(self, Namespace, MetricData):
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.calls.append((Namespace, MetricData))
        now = self.clock() if self.clock else 0.0
        for datum in MetricData:
            self.points.append((now, datum["MetricName"], datum["Value"], datum["Dimensions"]))


class FakeConnection:
    def __init__(self, up=True):
        self.up = up

    def ping(self):
        if not self.up:
            raise ConnectionError("redis down")
        return True


class FakeQueue:
    def __init__(self, depth=0, ids=()):
        self.depth, self.ids = depth, list(ids)

    @property
    def count(self):
        return self.depth

    def get_job_ids(self, offset, length):
        return self.ids[offset:offset + length]


@pytest.fixture
def enabled(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(rm, "is_enabled", lambda: True)
    monkeypatch.setattr(rm, "_get_client", lambda: client)
    return client


@pytest.fixture
def no_queue_reads(monkeypatch):
    """sample_queue against a depth-only fake: no jobs, empty failed registry."""
    state = {"failed": 0, "jobs": {}}

    class Registry:
        def __init__(self, queue):
            pass

        def get_job_count(self, cleanup=True):
            return state["failed"]

    class Job:
        @staticmethod
        def fetch_many(ids, connection):
            return [state["jobs"].get(i) for i in ids]

    import rq.job
    import rq.registry
    monkeypatch.setattr(rq.registry, "FailedJobRegistry", Registry)
    monkeypatch.setattr(rq.job, "Job", Job)
    return state


# ── 1. units ────────────────────────────────────────────────────────────────

def test_disabled_publishes_nothing_and_never_builds_a_client(monkeypatch):
    monkeypatch.setattr(rm, "is_enabled", lambda: False)
    monkeypatch.setattr(rm, "_get_client", lambda: pytest.fail("client built while disabled"))
    assert rm.publish({rm.WORKER_HEARTBEAT: (1, "Count")}) == 0
    assert rm.record_retention_pass(0) == 0
    sampler = rm.WorkerSampler()
    assert sampler.tick(0.0, lambda: FakeQueue(), lambda: FakeConnection()) == 0


def test_publish_uses_one_fixed_dimension_and_the_runtime_namespace(enabled):
    assert rm.publish({rm.WORKER_HEARTBEAT: (1, "Count")}) == 1
    (namespace, data), = enabled.calls
    assert namespace == rm.NAMESPACE == "FitX/Runtime"
    assert data == [{"MetricName": "WorkerHeartbeat", "Value": 1.0, "Unit": "Count",
                     "Dimensions": [{"Name": "Service", "Value": "worker"}]}]


def test_publish_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(rm, "is_enabled", lambda: True)
    monkeypatch.setattr(rm, "_get_client", lambda: RecordingClient(fail=True))
    assert rm.publish({rm.WORKER_HEARTBEAT: (1, "Count")}) == 0
    monkeypatch.setattr(rm, "_get_client", lambda: (_ for _ in ()).throw(RuntimeError("no creds")))
    assert rm.record_retention_pass(2) == 0


def test_client_is_rebuilt_in_a_forked_child(monkeypatch):
    built = []

    class FakeSession:
        def client(self, *args, **kwargs):
            built.append(kwargs["config"])
            return object()

    class FakeBoto3:
        class session:
            Session = FakeSession

    monkeypatch.setattr(rm, "boto3", FakeBoto3)
    monkeypatch.setattr(rm, "_client", None)
    monkeypatch.setattr(rm, "_client_pid", None)
    pid = [100]
    monkeypatch.setattr(rm.os, "getpid", lambda: pid[0])
    first = rm._get_client()
    assert rm._get_client() is first and len(built) == 1
    pid[0] = 101                                    # the RQ work horse after fork()
    assert rm._get_client() is not first and len(built) == 2
    config = built[0]
    assert config.connect_timeout == 2 and config.read_timeout == 3
    assert config.retries == {"max_attempts": 1}


def test_a_child_forked_while_the_parent_held_the_lock_does_not_block(monkeypatch):
    import threading

    class FakeBoto3:
        class session:
            class Session:
                def client(self, *args, **kwargs):
                    return object()

    monkeypatch.setattr(rm, "boto3", FakeBoto3)
    monkeypatch.setattr(rm, "_client", object())
    monkeypatch.setattr(rm, "_client_pid", 100)
    held = threading.Lock()
    held.acquire()                                  # parent's heartbeat thread mid-publish
    monkeypatch.setattr(rm, "_client_lock", held)
    monkeypatch.setattr(rm.os, "getpid", lambda: 101)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("c", rm._get_client()),
                              daemon=True)
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and result["c"] is not None
    assert rm._client_lock is not held


def test_the_fork_hook_is_registered():
    import os
    if not hasattr(os, "register_at_fork"):
        pytest.skip("no fork on this platform")
    pid = os.fork()
    if pid == 0:                                    # child: lock and client are fresh
        ok = rm._client is None and not rm._client_lock.locked()
        os._exit(0 if ok else 1)
    with rm._client_lock:                           # parent keeps its lock usable
        pass
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0


def test_no_redis_or_unreachable_redis_publishes_no_heartbeat(enabled):
    sampler = rm.WorkerSampler()
    assert sampler.tick(0.0, lambda: None, lambda: None) == 0
    sampler = rm.WorkerSampler()
    assert sampler.tick(0.0, lambda: FakeQueue(), lambda: FakeConnection(up=False)) == 0
    assert enabled.calls == []


def test_queue_read_failure_still_reports_the_heartbeat(enabled, monkeypatch):
    monkeypatch.setattr(rm, "sample_queue", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert rm.WorkerSampler().tick(0.0, lambda: FakeQueue(), lambda: FakeConnection()) == 1
    assert [d["MetricName"] for d in enabled.calls[0][1]] == ["WorkerHeartbeat"]


def test_sampler_keeps_its_interval(enabled, no_queue_reads):
    sampler = rm.WorkerSampler()
    factories = (lambda: FakeQueue(), lambda: FakeConnection())
    assert sampler.tick(0.0, *factories) == 5
    assert sampler.tick(59.0, *factories) == 0
    assert sampler.tick(60.0, *factories) == 5


def test_failed_jobs_new_is_the_increase_since_the_previous_sample(enabled, no_queue_reads):
    sampler = rm.WorkerSampler(interval=1)
    factories = (lambda: FakeQueue(), lambda: FakeConnection())
    news = []
    for t, failed in enumerate((4, 4, 6, 3, 3)):     # first sample is a baseline
        no_queue_reads["failed"] = failed
        sampler.tick(float(t), *factories)
        values = {d["MetricName"]: d["Value"] for d in enabled.calls[-1][1]}
        assert values["QueueFailedJobs"] == failed
        news.append(values["QueueFailedJobsNew"])
    assert news == [0, 0, 2, 0, 0]                   # expiry never goes negative


def test_oldest_age_reads_live_jobs_and_skips_expired_ids(no_queue_reads):
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

    class J:
        def __init__(self, seconds_ago, aware=True):
            moment = now - timedelta(seconds=seconds_ago)
            self.enqueued_at = moment if aware else moment.replace(tzinfo=None)

    no_queue_reads["jobs"] = {"a": J(30), "c": J(700, aware=False)}
    queue = FakeQueue(depth=3, ids=["a", "expired", "c"])
    sample = rm.sample_queue(queue, FakeConnection(), now=now)
    assert sample == {"depth": 3, "oldest_age": 700.0, "failed": 0}
    assert rm.sample_queue(FakeQueue(), FakeConnection(), now=now)["oldest_age"] == 0.0


def test_worker_monitoring_tick_never_raises(monkeypatch):
    import worker

    class Boom:
        def tick(self, *args, **kwargs):
            raise RuntimeError("sampler bug")

    worker._monitoring_tick(Boom(), 0.0)


def test_retention_job_reports_only_a_finished_pass(app, monkeypatch):
    from app.jobs import tasks
    from app.services import provider_retention

    reported = []
    monkeypatch.setattr(rm, "record_retention_pass", reported.append)
    monkeypatch.setattr(provider_retention, "expire_due",
                        lambda: provider_retention.ExpiryResult(1, 0, 2, 3))
    assert tasks.expire_provider_nutrition()["overdue"] == 3
    assert reported == [3]

    def failing():
        raise RuntimeError("db down")
    monkeypatch.setattr(provider_retention, "expire_due", failing)
    with pytest.raises(RuntimeError):
        tasks.expire_provider_nutrition()
    assert reported == [3]                           # a failed pass reports nothing


# ── 2. the template ─────────────────────────────────────────────────────────

class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {suffix: value}


_CfnLoader.add_multi_constructor("!", _tag)


def _template():
    return yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)


def _alarms():
    resources = _template()["Resources"]
    return {name: r["Properties"] for name, r in resources.items()
            if r["Type"] == "AWS::CloudWatch::Alarm"}


EXPECTED_MISSING = {
    "WorkerHeartbeat": "breaching",                  # M1: silence must page
    "ProviderRetentionPassCompleted": "breaching",   # M3: no pass must page
    "QueueDepth": "notBreaching",                    # covered by M1
    "QueueOldestJobAgeSeconds": "notBreaching",
    "QueueFailedJobsNew": "notBreaching",
    "ProviderRetentionOverdue": "notBreaching",
}


def test_every_alarm_reads_a_published_metric_with_the_code_contract():
    template = _template()
    assert template["Parameters"]["Namespace"]["Default"] == rm.NAMESPACE
    assert template["Parameters"]["ActionsEnabled"]["Default"] == "false"
    alarms = _alarms()
    assert len(alarms) == 7
    covered = set()
    for name, alarm in alarms.items():
        assert alarm["MetricName"] in rm.METRICS, name
        covered.add(alarm["MetricName"])
        assert alarm["Namespace"] == {"Ref": "Namespace"}
        assert alarm["Dimensions"] == [dict(d) for d in rm.DIMENSIONS]
        assert alarm["TreatMissingData"] == EXPECTED_MISSING[alarm["MetricName"]], name
        assert 1 <= alarm["DatapointsToAlarm"] <= alarm["EvaluationPeriods"]
        assert alarm["Period"] in (60, 300) and alarm["Period"] * alarm["EvaluationPeriods"] <= 86400
        assert alarm["AlarmActions"] in ([{"Ref": "AlarmTopicArn"}], [{"Ref": "EscalationTopicArn"}])
        assert alarm["ActionsEnabled"] == {"If": ["Actions", True, False]}
        assert alarm["AlarmName"].startswith("AxisAI-R6-M")
    assert covered == set(rm.METRICS) - {"QueueFailedJobs"}   # total is context only


def test_m1_pages_within_five_minutes_and_escalates_at_sixty():
    alarms = _alarms()
    primary, escalation = alarms["WorkerHeartbeatMissing"], alarms["WorkerHeartbeatEscalation"]
    assert primary["Period"] * primary["DatapointsToAlarm"] == 300
    # Fires on 50 empty minutes within the last 60: <= 60 min after the
    # outage starts wherever it starts, and brief heartbeats cannot reset it.
    assert escalation["Period"] == 60
    assert escalation["Period"] * escalation["EvaluationPeriods"] == 3600
    assert escalation["Period"] * escalation["DatapointsToAlarm"] <= 3600
    assert escalation["DatapointsToAlarm"] < escalation["EvaluationPeriods"]
    assert escalation["AlarmActions"] == [{"Ref": "EscalationTopicArn"}]
    rule = _template()["Rules"]["EscalationIsADifferentChannel"]["Assertions"][0]["Assert"]
    assert rule == {"Not": [{"Equals": [{"Ref": "EscalationTopicArn"}, {"Ref": "AlarmTopicArn"}]}]}
    # The worker samples every 60 s, so a healthy minute always has a point.
    assert rm.SAMPLE_INTERVAL_SECONDS <= primary["Period"]


# ── 3. state transitions on a simulated clock ───────────────────────────────

def _aggregate(values, statistic):
    if not values:
        return None
    return {"SampleCount": len(values), "Sum": sum(values), "Maximum": max(values),
            "Minimum": min(values), "Average": sum(values) / len(values)}[statistic]


_COMPARE = {
    "LessThanThreshold": lambda v, t: v < t,
    "GreaterThanThreshold": lambda v, t: v > t,
    "GreaterThanOrEqualToThreshold": lambda v, t: v >= t,
}


def _evaluate(alarm, points, now):
    """Model of CloudWatch M-of-N evaluation at time ``now`` (seconds)."""
    period = alarm["Period"]
    end = math.floor(now / period) * period          # last complete period
    breaching = 0
    for k in range(alarm["EvaluationPeriods"]):
        lo, hi = end - (k + 1) * period, end - k * period
        values = [v for t, name, v, _ in points if name == alarm["MetricName"] and lo <= t < hi]
        value = _aggregate(values, alarm["Statistic"])
        if value is None:
            breaching += alarm["TreatMissingData"] == "breaching"
        else:
            breaching += _COMPARE[alarm["ComparisonOperator"]](value, alarm["Threshold"])
    return "ALARM" if breaching >= alarm["DatapointsToAlarm"] else "OK"


class World:
    """The worker heartbeat thread and the retention job on a fake clock."""

    def __init__(self, monkeypatch, no_queue_reads):
        self.t = 0.0
        self.client = RecordingClient(clock=lambda: self.t)
        monkeypatch.setattr(rm, "is_enabled", lambda: True)
        monkeypatch.setattr(rm, "_get_client", lambda: self.client)
        self.reads = no_queue_reads
        self.sampler = rm.WorkerSampler()
        self.queue = FakeQueue()
        self.redis = FakeConnection()
        self.worker_alive = True
        self.retention_running = True
        self.overdue = 0
        self.last_pass = -300.0
        self.alarms = _alarms()

    def run(self, seconds, on_tick=None):
        stop = self.t + seconds
        while self.t < stop:
            if on_tick:
                on_tick(self)
            if self.worker_alive:
                self.sampler.tick(self.t, lambda: self.queue, lambda: self.redis)
                if self.retention_running and self.t - self.last_pass >= 300:
                    rm.record_retention_pass(self.overdue)
                    self.last_pass = self.t
            self.t += 10

    def state(self, name):
        return _evaluate(self.alarms[name], self.client.points, self.t)

    def states(self):
        return {name: self.state(name) for name in self.alarms}


@pytest.fixture
def world(monkeypatch, no_queue_reads):
    w = World(monkeypatch, no_queue_reads)
    w.run(3 * 3600)                                   # warm, zero user traffic
    return w


def test_zero_traffic_healthy_worker_keeps_every_alarm_ok(world):
    assert set(world.states().values()) == {"OK"}


@pytest.mark.parametrize("offset", [0, 130, 290])
def test_dead_worker_pages_within_six_minutes_escalates_within_sixty_and_recovers(world, offset):
    world.run(offset)                                 # outage starts anywhere in a period
    world.worker_alive = False
    world.run(240)
    assert world.state("WorkerHeartbeatMissing") == "OK"
    world.run(120)
    assert world.state("WorkerHeartbeatMissing") == "ALARM"
    world.run(39 * 60)                                # outage + 45 min
    assert world.state("WorkerHeartbeatEscalation") == "OK"
    world.run(15 * 60)                                # outage + 60 min
    assert world.state("WorkerHeartbeatEscalation") == "ALARM"
    assert world.state("ProviderRetentionPassMissing") == "ALARM"   # no pass either
    assert world.state("QueueNotDraining") == "OK"                  # no double page
    world.worker_alive = True
    world.run(10 * 60)
    assert world.state("WorkerHeartbeatMissing") == "OK"
    world.run(10 * 60)
    assert world.state("WorkerHeartbeatEscalation") == "OK"


def test_a_crash_looping_worker_with_brief_heartbeats_still_escalates(world):
    def flap(w):                                      # alive one minute in every ten
        w.worker_alive = (w.t % 600) < 60
    world.run(65 * 60, on_tick=flap)
    assert world.state("WorkerHeartbeatEscalation") == "ALARM"


def test_unreachable_redis_reads_as_a_dead_worker(world):
    world.redis = FakeConnection(up=False)
    world.run(6 * 60)
    assert world.state("WorkerHeartbeatMissing") == "ALARM"


def test_hung_worker_with_live_heartbeat_pages_on_a_queue_that_never_drains(world):
    world.retention_running = False                   # main loop stuck: no pass runs

    def enqueue(w):                                   # the timer still enqueues every 300 s
        if int(w.t) % 300 == 0:
            w.queue.depth += 1
    world.run(15 * 60, on_tick=enqueue)
    assert world.state("WorkerHeartbeatMissing") == "OK"
    assert world.state("QueueNotDraining") == "OK"
    world.run(20 * 60, on_tick=enqueue)               # hang + 35 min
    assert world.state("QueueNotDraining") == "ALARM"
    assert world.state("ProviderRetentionPassMissing") == "ALARM"


def test_a_long_job_or_brief_backlog_does_not_page(world):
    def burst(w):
        w.queue.depth = 2 if (w.t % 1800) < 240 else 0   # 4-minute backlog twice an hour
    world.run(3 * 3600, on_tick=burst)
    assert world.state("QueueNotDraining") == "OK"


def test_a_fifteen_minute_erasure_job_does_not_page(world, monkeypatch):
    """The longest legitimate job (retry_health_erasures, timeout 900 s) holds
    the single worker; work enqueued meanwhile waits up to 15 minutes."""
    start = world.t
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

    class Waiting:
        enqueued_at = base

    real = rm.sample_queue
    monkeypatch.setattr(rm, "sample_queue", lambda q, c, now=None: real(
        q, c, now=base + timedelta(seconds=world.t - start)))

    def busy(w):
        running = (w.t - start) < 900
        w.queue = FakeQueue(depth=1 if running else 0, ids=["summary"] if running else [])
    world.reads["jobs"] = {"summary": Waiting()}
    world.run(40 * 60, on_tick=busy)
    assert world.state("QueueNotDraining") == "OK"
    assert world.state("QueueOldestJobAge") == "OK"


def test_a_sample_catching_each_fresh_enqueue_does_not_page(world):
    """Every 5 minutes one 1-minute sample can see the just-enqueued retention
    job before the worker pops it. Only a queue that is NEVER empty may page."""
    def fresh(w):
        w.queue.depth = 1 if (w.t % 300) < 60 else 0
    world.run(3 * 3600, on_tick=fresh)
    assert world.state("QueueNotDraining") == "OK"


def test_stale_live_job_pages_on_age(world, monkeypatch):
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    start = world.t

    class Stuck:
        enqueued_at = base

    world.reads["jobs"] = {"erasure": Stuck()}
    world.queue = FakeQueue(depth=1, ids=["erasure"])
    real = rm.sample_queue
    monkeypatch.setattr(rm, "sample_queue", lambda q, c, now=None: real(
        q, c, now=base + timedelta(seconds=world.t - start)))
    world.run(19 * 60)
    assert world.state("QueueOldestJobAge") == "OK"
    world.run(6 * 60)
    assert world.state("QueueOldestJobAge") == "ALARM"


def test_one_exhausted_job_pages_once_then_clears(world):
    world.reads["failed"] += 1
    world.run(5 * 60)
    assert world.state("QueueFailedJobs") == "ALARM"
    world.run(10 * 60)
    assert world.state("QueueFailedJobs") == "OK"


def test_retention_pass_stopping_pages_within_thirty_five_minutes(world):
    world.retention_running = False
    world.run(20 * 60)
    assert world.state("ProviderRetentionPassMissing") == "OK"
    world.run(15 * 60)
    assert world.state("ProviderRetentionPassMissing") == "ALARM"
    world.retention_running = True
    world.run(10 * 60)
    assert world.state("ProviderRetentionPassMissing") == "OK"


def test_any_value_past_twenty_four_hours_pages_at_once(world):
    world.overdue = 3
    world.run(10 * 60)
    assert world.state("ProviderRetentionOverdue") == "ALARM"
    world.overdue = 0
    world.run(10 * 60)
    assert world.state("ProviderRetentionOverdue") == "OK"
