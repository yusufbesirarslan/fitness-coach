"""M4 source qualification; synthetic DB, Redis and metric transport only."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.health_consent_models import HealthErasure, HealthErasureRetry, HealthLegalHold
from app.services import health_erasure_monitoring as m, release_monitoring


def obligation(owner=1, age=0):
    row = HealthErasure(owner_id=owner, generation=1,
        requested_at=datetime.utcnow() - timedelta(seconds=age))
    db.session.add(row)
    db.session.commit()
    return row.id


def test_two_distinct_hours_are_durable_and_duplicate_safe(app):
    key = obligation(age=100)
    start = m.start_pass()
    assert m.finish_pass(start, 100)["twice"] == 0
    db.session.remove()  # a worker restart must not reset evidence
    assert m.finish_pass(start, 100)["twice"] == 0
    assert m.finish_pass(start, 101)["twice"] == 1
    assert m.finish_pass(start, 100)["twice"] == 1
    assert db.session.get(HealthErasureRetry, key).passes == 2


def test_out_of_order_distinct_retry_hours_still_page(app):
    obligation(age=100)
    start = m.start_pass()
    assert m.finish_pass(start, 101)["twice"] == 0
    assert m.finish_pass(start, 100)["twice"] == 1


def test_only_surviving_initial_obligations_count_and_batches_do_not_starve(app):
    keys = [obligation(owner=i, age=100) for i in range(1, 6)]
    start = m.start_pass()
    fresh = obligation(owner=10)
    db.session.get(HealthErasure, keys[0]).completed_at = datetime.utcnow()
    db.session.commit()
    m.finish_pass(start, 100, batch_size=2)
    snapshot = m.finish_pass(start, 101, batch_size=2)
    assert snapshot["pending"] == 5 and snapshot["twice"] == 4
    assert db.session.get(HealthErasureRetry, fresh) is None
    assert db.session.get(HealthErasureRetry, keys[0]) is None


def test_completed_obligation_clears_pending_signal(app):
    key = obligation(age=100)
    start = m.start_pass()
    m.finish_pass(start, 1)
    assert m.finish_pass(start, 2)["twice"] == 1
    db.session.get(HealthErasure, key).completed_at = datetime.utcnow()
    db.session.commit()
    snapshot = m.finish_pass(m.start_pass(), 3)
    assert snapshot["pending"] == snapshot["twice"] == 0
    assert snapshot["oldest"] is None


def test_age_thresholds_advance_without_health_traffic(app):
    obligation(age=30 * 86400 - 10)
    now = datetime.now(timezone.utc)
    snapshot = m.finish_pass(m.start_pass(), 1, now=now)
    redis = Mock(get=lambda key: json.dumps(snapshot))
    at75 = m.sample(redis, now + timedelta(minutes=75))
    after75 = m.sample(redis, now + timedelta(minutes=75, seconds=1))
    assert at75[m.PASS_AGE] == (4500, "Seconds")
    assert after75[m.PASS_AGE] == (4501, "Seconds")
    assert m.sample(redis, now)[m.OVERDUE] == (0, "Count")
    assert m.sample(redis, now + timedelta(seconds=11))[m.OVERDUE] == (1, "Count")
    assert after75[m.OLDEST][0] > 30 * 86400


def test_legal_hold_excludes_deadline_breach_but_not_two_retry_page(app, make_user):
    user = make_user("held_monitor")
    obligation(user.id, age=31 * 86400)
    db.session.add(HealthLegalHold(owner_id=user.id, categories=["coach"], matter_ref="synthetic",
        created_at=datetime.utcnow(), review_at=datetime.utcnow() - timedelta(days=1)))
    db.session.commit()
    start = m.start_pass()
    m.finish_pass(start, 1)
    snapshot = m.finish_pass(start, 2)
    redis = Mock(get=lambda key: json.dumps(snapshot))
    values = m.sample(redis, datetime.now(timezone.utc))
    assert values[m.TWICE] == (1, "Count") and values[m.OVERDUE] == (0, "Count")
    assert values[m.PENDING] == (1, "Count")


@pytest.mark.parametrize("slot", [None, True, -1, "123"])
def test_unqualified_slot_cannot_supply_success_evidence(app, slot):
    with pytest.raises(ValueError):
        m.finish_pass(m.start_pass(), slot)


def test_missing_and_corrupt_snapshot_do_not_publish_false_success():
    assert m.sample(Mock(get=lambda _: None), datetime.now(timezone.utc)) == {}
    with pytest.raises(ValueError):
        m.sample(Mock(get=lambda _: "bad"), datetime.now(timezone.utc))


def test_sampler_includes_m4_and_survives_corrupt_snapshot(monkeypatch):
    redis = Mock()
    monkeypatch.setattr(release_monitoring, "is_enabled", lambda: True)
    monkeypatch.setattr(release_monitoring, "sample_queue", lambda *a, **k: dict(depth=0, oldest_age=0, failed=0))
    sent = []
    monkeypatch.setattr(release_monitoring, "publish", lambda values: sent.append(values) or len(values))
    monkeypatch.setattr(m, "sample", lambda *a: {m.PASS_AGE: (4501, "Seconds")})
    sampler = release_monitoring.WorkerSampler()
    sampler.tick(0, Mock, lambda: redis)
    assert sent[-1][m.PASS_AGE] == (4501, "Seconds")
    monkeypatch.setattr(m, "sample", Mock(side_effect=ValueError("synthetic")))
    sampler.tick(60, Mock, lambda: redis)
    assert m.PASS_AGE not in sent[-1]  # missing data breaches; no fabricated zero


def test_hourly_job_records_monitoring_only_after_completed_retry(app, monkeypatch):
    from app.jobs import tasks
    from app.services import health_erasure
    stored = []
    monkeypatch.setattr(m, "store_snapshot", stored.append)
    monkeypatch.setattr(health_erasure, "run_pending", lambda: {"overdue": 0})
    assert tasks.retry_health_erasures(retry_slot=1) == {"overdue": 0}
    assert len(stored) == 1 and stored[0]["pending"] == 0
    monkeypatch.setattr(health_erasure, "run_pending", Mock(side_effect=RuntimeError("failure")))
    with pytest.raises(RuntimeError):
        tasks.retry_health_erasures(retry_slot=2)
    assert len(stored) == 1


def test_timer_enqueues_scheduled_hour_without_inline_execution(monkeypatch):
    import worker
    from app import jobs
    queue = Mock()
    monkeypatch.setattr(jobs, "get_queue", lambda: queue)
    monkeypatch.setattr(worker.time, "time", lambda: 360000)
    assert worker._health_erasure_tick(0, 3600) == 3600
    args, kwargs = queue.enqueue_call.call_args
    assert args[0].__name__ == "retry_health_erasures"
    assert kwargs["kwargs"] == {"retry_slot": 100}
    assert kwargs["result_ttl"] == 7200  # M5 one-hour registry readback remains possible
    assert kwargs["description"] == "axisai.f01.erasure-retry.v1"
    assert kwargs["job_id"].startswith("f01-erasure-retry-100-")
    worker._health_erasure_tick(3600, 7199)
    assert queue.enqueue_call.call_count == 1


def test_alarm_plan_matches_approved_m4_thresholds_and_sdk_schema():
    from scripts.f01_erasure_alarm_plan import alarm_plan
    from botocore.session import get_session
    from botocore.validate import validate_parameters
    shape = get_session().get_service_model("cloudwatch").operation_model("PutMetricAlarm").input_shape
    alarms = alarm_plan("arn:aws:sns:eu-central-1:123456789012:synthetic-page",
                        "arn:aws:sns:eu-central-1:123456789012:synthetic-critical")
    for alarm in alarms:
        validate_parameters(alarm, shape)  # offline service model; no client
        assert alarm["ActionsEnabled"] is False
        assert alarm["Period"] == 60
    assert [(a["MetricName"], a["Threshold"]) for a in alarms] == [
        (m.PASS_AGE, 4500), (m.TWICE, 0), (m.OVERDUE, 0)]
    assert alarms[0]["TreatMissingData"] == "breaching"


def test_redis_lua_cas_rejects_stale_and_duplicate_snapshots(monkeypatch):
    import fakeredis
    from app import jobs
    redis = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(jobs, "_rq_connection", lambda: redis)
    newer = dict(sequence=2, completed=200, pending=1, oldest=1, oldest_unheld=1, twice=1)
    m.store_snapshot(newer)
    m.store_snapshot(dict(newer, sequence=1, completed=300, twice=0))
    m.store_snapshot(dict(newer, twice=0))
    assert json.loads(redis.get(m.KEY)) == newer
    m.store_snapshot(dict(newer, sequence=3, completed=150, pending=0, twice=0))
    assert json.loads(redis.get(m.KEY))["pending"] == 0


def test_real_rq_completion_remains_in_finished_registry_for_m5(app, monkeypatch):
    import fakeredis
    from rq import Queue, SimpleWorker
    from rq.registry import FinishedJobRegistry
    import worker
    from app import jobs
    redis = fakeredis.FakeStrictRedis()
    queue = Queue("f01-qualification", connection=redis)
    monkeypatch.setattr(jobs, "get_queue", lambda: queue)
    monkeypatch.setattr(jobs, "_rq_connection", lambda: redis)
    worker._health_erasure_tick(-3600, 0)
    job_id = queue.get_job_ids()[0]
    SimpleWorker([queue], connection=redis).work(burst=True, with_scheduler=False)
    assert job_id in FinishedJobRegistry(queue=queue).get_job_ids(cleanup=False)
    job = queue.fetch_job(job_id)
    assert job.is_finished and job.result_ttl == 7200
    assert job.func_name == "app.jobs.tasks.retry_health_erasures"
    assert job.return_value()["overdue"] == 0
    # The later reader must use only Redis reads and expose no arguments/result.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("m5_readback",
        Path(__file__).parents[1] / "qualification/f01-d1/m5_readback.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    commands = []
    original = redis.execute_command
    def capture(*args, **kwargs):
        commands.append(str(args[0]).upper())
        if str(args[0]).upper() == "HMGET":
            assert str(args[1]) != "rq:job:unrelated"
            assert set(args[2:]) <= set(reader.FIELDS)
        return original(*args, **kwargs)
    monkeypatch.setattr(redis, "execute_command", capture)
    # RQ Job.restore/HGETALL would deserialize these. The observer must never
    # retrieve serialized fields, even on a source-marked candidate job.
    redis.hset("rq:job:" + job_id, mapping={
        "data": b"HOSTILE_PICKLE", "result": b"HOSTILE_PICKLE", "meta": b"HOSTILE_PICKLE"})
    redis.hset("rq:job:unrelated", mapping={"data": b"HOSTILE_PICKLE"})
    redis.zadd(FinishedJobRegistry(queue=queue).key, {"unrelated": 9999999999})
    commands.clear()
    rows = reader.observe(redis, queue.name)
    assert len(rows) == 1 and rows[0]["status"] == "finished"
    assert set(rows[0]) == {"id", "function", "status", "enqueued_at", "started_at", "ended_at"}
    assert set(commands) <= {"LRANGE", "ZRANGE", "HMGET"}
    start = datetime.fromisoformat(rows[0]["enqueued_at"]) - timedelta(seconds=1)
    end = datetime.fromisoformat(rows[0]["ended_at"]) + timedelta(seconds=1)
    assert reader.qualifying_rows(rows, start, end) == rows
    for key, value in [("enqueued_at", (start - timedelta(seconds=1)).isoformat()),
                       ("started_at", (end + timedelta(seconds=2)).isoformat()),
                       ("ended_at", (end + timedelta(seconds=1)).isoformat()),
                       ("ended_at", start.isoformat()), ("ended_at", "bad"),
                       ("started_at", None), ("started_at", "2026-01-01T00:00:00")]:
        assert reader.qualifying_rows([dict(rows[0], **{key: value})], start, end) == []
