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
