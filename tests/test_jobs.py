"""Sprint 4 WS8 — arka-plan iş kuyruğu altyapısı (app/jobs).

Hermetik: rq kurulu OLMAYABİLİR ve Redis yoktur → varsayılan test ortamında
`get_queue()` None döner ve `enqueue_or_run` SATIR-İÇİ çalışır. Kuyruklu yol
sahte bir Queue ile doğrulanır (gerçek rq/worker gerekmez).

    python -m pytest tests/test_jobs.py -v
"""
import pytest

from app import jobs


# ── get_queue / enqueue_or_run ──────────────────────────────────────────────

def test_get_queue_none_without_rq_or_redis():
    # Varsayılan test ortamı: REDIS_URL="" → kuyruk yok.
    assert jobs.get_queue() is None


def test_enqueue_or_run_runs_inline_without_queue(monkeypatch):
    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    calls = []
    out = jobs.enqueue_or_run(lambda x: calls.append(x) or (x * 2), 21)
    assert out == {"queued": False, "result": 42}
    assert calls == [21]


class _FakeJob:
    id = "job-123"


class _FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue_call(self, func, args=None, kwargs=None, retry=None):
        self.calls.append((func, args, kwargs, retry))
        return _FakeJob()


def test_enqueue_or_run_enqueues_when_queue_present(monkeypatch):
    q = _FakeQueue()
    monkeypatch.setattr(jobs, "get_queue", lambda: q)
    ran = []
    out = jobs.enqueue_or_run(lambda x: ran.append(x), 7)
    assert out == {"queued": True, "job_id": "job-123"}
    assert ran == []  # kuyruğa atıldı → satır-içi ÇALIŞMADI
    assert q.calls[0][1] == (7,)  # args geçti


def test_enqueue_or_run_falls_back_inline_on_enqueue_error(monkeypatch):
    class _BoomQueue:
        def enqueue_call(self, *a, **k):
            raise RuntimeError("redis down")
    monkeypatch.setattr(jobs, "get_queue", lambda: _BoomQueue())
    ran = []
    out = jobs.enqueue_or_run(lambda x: ran.append(x) or "ok", 5)
    assert out == {"queued": False, "result": "ok"}
    assert ran == [5]  # enqueue patladı → satır-içine düştü


def test_dispatch_background_uses_daemon_thread_without_queue(monkeypatch):
    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    started = []

    class ThreadProbe:
        def __init__(self, *, target, daemon):
            started.append((target, daemon))

        def start(self):
            started.append("started")

    monkeypatch.setattr(jobs.threading, "Thread", ThreadProbe)
    ran = []

    result = jobs.dispatch_background(lambda *a, **k: ran.append((a, k)), 1,
                                      flag=True)

    assert result == {"queued": False, "threaded": True}
    assert len(started) == 2 and started[1] == "started"
    runner, daemon = started[0]
    assert daemon is True
    assert ran == []                       # not on the caller's thread
    runner()                               # the bound wrapper carries the args
    assert ran == [((1,), {"flag": True})]


# ── P2: worker-less fallback must REUSE the live app (triage 2026-08-28) ────
#
# Without binding, the daemon thread has no app context, so
# ``tasks._in_app_context`` takes its WORKER branch inside the web process: a
# second ``create_app()`` with its own SQLAlchemy engine and pool, plus a
# process-wide ``FITX_SKIP_DB_INIT=1`` that silently disables boot migrations
# for the real application.

def test_dispatch_background_runs_under_the_live_application(app, monkeypatch):
    import threading as _threading

    from flask import current_app, has_app_context

    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    live = current_app._get_current_object()
    done = _threading.Event()
    seen = {}

    def target(marker):
        try:
            seen["marker"] = marker
            seen["has_context"] = has_app_context()
            seen["app"] = (current_app._get_current_object()
                           if has_app_context() else None)
        finally:
            done.set()

    assert jobs.dispatch_background(target, "m1") == {"queued": False,
                                                     "threaded": True}
    assert done.wait(10), "background daemon never ran"

    assert seen["marker"] == "m1"
    assert seen["has_context"] is True
    assert seen["app"] is live          # the SAME app object, not a new one


def test_dispatch_background_creates_no_second_app_and_no_env_mutation(
        app, monkeypatch):
    import os
    import threading as _threading

    import app as app_pkg
    from app.jobs import tasks

    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    monkeypatch.setattr(tasks, "_worker_app", None)
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError("a second Flask application was created")

    monkeypatch.setattr(app_pkg, "create_app", _forbidden)

    order = _maintenance_probes(monkeypatch)
    done = _threading.Event()
    real = tasks.run_daily_maintenance

    def traced(now_iso):
        try:
            return real(now_iso)
        finally:
            done.set()

    jobs.dispatch_background(traced, _NOW_ISO)
    assert done.wait(10), "maintenance daemon never ran"

    assert order == ["sessions", "mobile_auth", "notifications",
                     "fatsecret_proxy"]
    assert tasks._worker_app is None           # no cached second application
    assert "FITX_SKIP_DB_INIT" not in os.environ   # no process-global mutation


# ── run_daily_maintenance: every operation runs; a failure is ISOLATED ──────

_NOW_ISO = "2026-08-28T03:00:00"


def _maintenance_probes(monkeypatch, failing=None):
    """Patch every maintenance operation; return the ordered call log."""
    from app.jobs import tasks
    from app.services import mobile_auth, notifications, session_store

    order = []

    def probe(name):
        def _run(*args, **kwargs):
            order.append(name)
            if name == failing:
                raise RuntimeError("%s patladi" % name)
            return name
        return _run

    monkeypatch.setattr(session_store, "purge_expired", probe("sessions"))
    monkeypatch.setattr(mobile_auth, "purge_expired", probe("mobile_auth"))
    monkeypatch.setattr(notifications, "purge_old", probe("notifications"))
    monkeypatch.setattr(tasks, "sample_fatsecret_proxy", probe("fatsecret_proxy"))
    return order


def test_daily_maintenance_runs_every_intended_operation(app, monkeypatch):
    from app.jobs import tasks

    order = _maintenance_probes(monkeypatch)

    results = tasks.run_daily_maintenance(_NOW_ISO)

    assert order == ["sessions", "mobile_auth", "notifications",
                     "fatsecret_proxy"]
    assert results == {"sessions": "sessions", "mobile_auth": "mobile_auth",
                       "notifications": "notifications",
                       "fatsecret_proxy": "fatsecret_proxy"}


@pytest.mark.parametrize("failing", ["sessions", "mobile_auth", "notifications",
                                     "fatsecret_proxy"])
def test_daily_maintenance_isolates_one_failing_operation(app, monkeypatch,
                                                          failing):
    """One broken operation rolls back and is recorded — the rest still run."""
    from app.extensions import db
    from app.jobs import tasks

    order = _maintenance_probes(monkeypatch, failing=failing)

    rollbacks = []
    real_rollback = db.session.rollback
    monkeypatch.setattr(db.session, "rollback",
                        lambda: rollbacks.append(1) or real_rollback())

    results = tasks.run_daily_maintenance(_NOW_ISO)

    # Attempted in order, none skipped: the failure isolates, it does not abort.
    assert order == ["sessions", "mobile_auth", "notifications",
                     "fatsecret_proxy"]
    assert results[failing] == "error"
    assert [k for k, v in results.items() if v == "error"] == [failing]
    assert len(rollbacks) == 1        # rollback happened, for the failed op only


# ── Worker heartbeat ────────────────────────────────────────────────────────

class _HeartbeatRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    def exists(self, key):
        return 1 if key in self.store else 0

    def get(self, key):
        return self.store.get(key)


def test_worker_heartbeat_roundtrip(monkeypatch):
    r = _HeartbeatRedis()
    monkeypatch.setattr("app.extensions.redis_client", r)
    assert jobs.worker_alive() is False  # henüz heartbeat yok
    jobs.record_worker_heartbeat()
    assert jobs.worker_alive() is True
    assert r.ttls[jobs.WORKER_HEARTBEAT_KEY] == jobs.WORKER_HEARTBEAT_TTL


def test_worker_alive_none_without_redis(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", None)
    assert jobs.worker_alive() is None  # bilinmiyor (fail-open)
    jobs.record_worker_heartbeat()      # patlamamalı


def test_worker_heartbeat_swallows_redis_error(monkeypatch):
    class _BoomRedis:
        def setex(self, *a, **k):
            raise RuntimeError("down")
        def exists(self, *a, **k):
            raise RuntimeError("down")
    monkeypatch.setattr("app.extensions.redis_client", _BoomRedis())
    jobs.record_worker_heartbeat()      # patlamamalı
    assert jobs.worker_alive() is None  # hata → None


def test_fatsecret_status_cache_roundtrip(monkeypatch):
    r = _HeartbeatRedis()
    monkeypatch.setattr("app.extensions.redis_client", r)

    jobs.record_fatsecret_status("error", checked_at=1000.0)

    assert jobs.fatsecret_status(now=1001.0, max_age=900) == "error"
    assert jobs.fatsecret_status(now=2000.0, max_age=900) is None


def test_fatsecret_sampler_records_result_without_raising(monkeypatch):
    from app.jobs import tasks
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "FATSECRET_BASE_URL", "https://proxy.example")
    recorded = []
    monkeypatch.setattr(jobs, "record_fatsecret_status",
                        lambda status, **kwargs: recorded.append(status))

    class Response:
        status_code = 502

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    assert tasks.sample_fatsecret_proxy() == "error"
    assert recorded == ["error"]


# ── Worker heartbeat daemon — #251 liveness contract (PR #253 hardening) ─

def test_heartbeat_tick_survives_a_failing_fatsecret_sample(monkeypatch):
    """A sampling failure must not end the daemon.

    The heartbeat runs in a bare ``while`` loop in a thread. An exception
    escaping the OPTIONAL FatSecret sample does not skip one probe — it kills
    the thread, so every FUTURE heartbeat write is lost and the worker reads
    dead in /health?deep=1 while it is still consuming jobs.
    """
    import worker
    from app.jobs import tasks

    r = _HeartbeatRedis()
    monkeypatch.setattr("app.extensions.redis_client", r)

    def boom():
        raise RuntimeError("proxy exploded")

    monkeypatch.setattr(tasks, "sample_fatsecret_proxy", boom)

    writes = []
    real_setex = r.setex
    monkeypatch.setattr(
        r, "setex",
        lambda k, ttl, v: writes.append((k, ttl)) or real_setex(k, ttl, v))

    interval = worker.PROXY_SAMPLE_INTERVAL_SECONDS
    last = 0.0
    for now in (1000.0, 1000.0 + interval, 1000.0 + 2 * interval):
        last = worker._heartbeat_tick(last, now)   # must not raise

    assert len(writes) == 3                        # every tick still wrote
    assert {k for k, _ in writes} == {jobs.WORKER_HEARTBEAT_KEY}
    assert {ttl for _, ttl in writes} == {jobs.WORKER_HEARTBEAT_TTL}
    assert jobs.worker_alive() is True


def test_failing_sample_still_advances_the_probe_throttle(monkeypatch):
    """A dead proxy is retried on its interval, not on every single tick."""
    import worker
    from app.jobs import tasks

    monkeypatch.setattr("app.extensions.redis_client", _HeartbeatRedis())
    attempts = []

    def boom():
        attempts.append(1)
        raise RuntimeError("proxy exploded")

    monkeypatch.setattr(tasks, "sample_fatsecret_proxy", boom)

    last = worker._heartbeat_tick(0.0, 1000.0)
    assert (last, len(attempts)) == (1000.0, 1)
    last = worker._heartbeat_tick(last, 1001.0)     # inside the interval
    assert (last, len(attempts)) == (1000.0, 1)     # no retry storm


def test_heartbeat_is_written_before_and_independently_of_the_sample(monkeypatch):
    import worker
    from app.jobs import tasks

    seq = []
    monkeypatch.setattr(jobs, "record_worker_heartbeat",
                        lambda: seq.append("beat"))
    monkeypatch.setattr(tasks, "sample_fatsecret_proxy",
                        lambda: seq.append("sample"))

    interval = worker.PROXY_SAMPLE_INTERVAL_SECONDS
    last = worker._heartbeat_tick(0.0, 1000.0)
    assert seq == ["beat", "sample"]

    last = worker._heartbeat_tick(last, 1001.0)     # throttled sample
    assert seq == ["beat", "sample", "beat"]        # heartbeat is NOT throttled

    worker._heartbeat_tick(last, 1000.0 + interval)
    assert seq == ["beat", "sample", "beat", "beat", "sample"]


def test_worker_heartbeat_protocol_is_unchanged(monkeypatch):
    """No new key, no new value, no new TTL — #251's contract verbatim."""
    import worker
    from app.jobs import tasks

    r = _HeartbeatRedis()
    monkeypatch.setattr("app.extensions.redis_client", r)
    monkeypatch.setattr(tasks, "sample_fatsecret_proxy", lambda: None)

    worker._heartbeat_tick(0.0, 1000.0)

    assert jobs.WORKER_HEARTBEAT_KEY == "fitx:worker:alive"
    assert list(r.store) == [jobs.WORKER_HEARTBEAT_KEY]   # ONLY that key
    assert r.store[jobs.WORKER_HEARTBEAT_KEY] == "1"
    assert r.ttls[jobs.WORKER_HEARTBEAT_KEY] == jobs.WORKER_HEARTBEAT_TTL


def test_fatsecret_probe_does_not_follow_redirects(monkeypatch):
    """A 3xx must classify THIS proxy, not whatever the hop points at."""
    import app.config as config_mod
    from app.jobs import tasks

    monkeypatch.setattr(config_mod, "FATSECRET_BASE_URL", "https://proxy.example")
    monkeypatch.setattr(jobs, "record_fatsecret_status", lambda status, **k: None)

    seen = {}

    class Response:
        status_code = 302

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr("requests.get", fake_get)

    assert tasks.sample_fatsecret_proxy() == "ok"
    assert seen["kwargs"]["allow_redirects"] is False
    assert seen["kwargs"]["timeout"] == 3


# ── Dead-letter helpers ─────────────────────────────────────────────────────

def test_failed_jobs_empty_without_queue(monkeypatch):
    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    assert jobs.failed_jobs() == []


def test_requeue_failed_false_without_queue(monkeypatch):
    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    assert jobs.requeue_failed("x") is False


# ── Pipeline entegrasyonu: özetleme enqueue_or_run'a bağlı (WS8) ───────────

def test_memory_stage_enqueues_summarize(app, make_user, monkeypatch):
    from app.services import ai_pipeline
    user = make_user("jobsu")
    seen = []
    # enqueue_or_run lazy import'la çözülür → modül attribute'unu patch'le.
    monkeypatch.setattr(jobs, "get_queue", lambda: object())  # worker/kuyruk VAR
    monkeypatch.setattr(jobs, "enqueue_or_run",
                        lambda func, *a, **k: seen.append((func.__name__, a)))
    conv, window, deferred = ai_pipeline._memory_stage(user.id)
    assert conv is not None
    assert seen == [("summarize_conversation", (conv.id,))]
    assert deferred is None  # kuyruk varken erteleme gerekmez (iş zaten async)


def test_memory_stage_defers_summarize_without_queue(app, make_user, monkeypatch):
    # Triage 2026-07-19 #4: kuyruk YOKKEN özetleme istek yolunda satır-içi
    # KOŞMAZ — yanıt sonrasında çağrılacak ertelenmiş bir callable döner.
    from app.jobs import tasks as jobs_tasks
    from app.services import ai_pipeline
    user = make_user("jobsd")
    calls = []
    monkeypatch.setattr(jobs, "get_queue", lambda: None)  # worker YOK
    monkeypatch.setattr(jobs_tasks, "summarize_conversation",
                        lambda conv_id: calls.append(conv_id) or False)
    conv, window, deferred = ai_pipeline._memory_stage(user.id)
    assert conv is not None
    assert calls == []            # kritik yolda LLM özet çağrısı YOK
    assert deferred is not None
    deferred()                    # yanıt-sonrası tetikleme görev gövdesine delege eder
    assert calls == [conv.id]


def test_summarize_task_inline_runs_without_queue(app, make_user, monkeypatch):
    # Kuyruk yokken görev SATIR-İÇİ çalışır (mevcut app context'i yeniden kullanır,
    # yeni create_app KURMAZ) ve maybe_summarize'a delege eder.
    from app.jobs import tasks
    from app.services import memory_manager
    user = make_user("inlineu")
    conv = memory_manager.get_or_create_active_conversation(user.id)
    called = {"n": 0}
    monkeypatch.setattr(memory_manager, "maybe_summarize",
                        lambda c: called.__setitem__("n", called["n"] + 1) or True)
    result = tasks.summarize_conversation(conv.id)
    assert result is True
    assert called["n"] == 1
