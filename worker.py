"""FitX RQ worker entrypoint (Sprint 4 WS8).

Docker compose 'worker' servisi bunu koşar: `python worker.py`. Redis'e bağlanır,
AI iş kuyruğunu (fitx:ai) tüketir ve periyodik canlılık heartbeat'i yazar
(/health?deep=1 bilgilendirici alanı).

- Linux/prod (konteyner): fork'lu `rq.Worker`.
- Windows/dev: fork yok → `rq.SimpleWorker` (RQ_SIMPLE_WORKER=1 ya da win32).

REDIS_URL yoksa ya da rq kurulu değilse hızlı ve GÖRÜNÜR biçimde çıkar (exit 1);
compose restart backoff'u yönetir. Prod'da REDIS_URL zaten zorunludur.
"""
import logging
import os
import sys
import threading
import time
import uuid

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(),
                                  logging.INFO))
_log = logging.getLogger("fitx.worker")


PROXY_SAMPLE_INTERVAL_SECONDS = 600
# FatSecret 24h retention pass (app/services/provider_retention.py). Values are
# removed at 22h (EXPIRY_MARGIN = 2h, many times this interval), so a value is
# gone before its deadline even when an enqueue waits behind other jobs.
RETENTION_INTERVAL_SECONDS = 300
# F01 withdrawal erasure retry (app/services/health_erasure.py, A1 = O2-B). The
# withdrawal request erases at once; this only finishes ones that failed, so an
# hourly pass keeps the 30-day bound with no dependence on user traffic.
HEALTH_ERASURE_INTERVAL_SECONDS = 3600


def _sample_fatsecret_safely():
    """Run the optional proxy sample so nothing can propagate to the caller.

    The heartbeat daemon owns the #251 worker-liveness contract. It is a bare
    `while` loop in a thread: an exception escaping here does not skip ONE
    sample, it ends the thread, and every FUTURE heartbeat write is lost. The
    worker then reads dead in /health?deep=1 while it is still consuming jobs.
    FatSecret is an OPTIONAL dependency and must never have that power.
    """
    from app.jobs.tasks import sample_fatsecret_proxy
    try:
        sample_fatsecret_proxy()
    except Exception:
        _log.warning("FatSecret ornegi basarisiz — heartbeat devam ediyor",
                     exc_info=True)


def _heartbeat_tick(last_proxy_sample, now):
    """One heartbeat iteration; returns the next ``last_proxy_sample``.

    The heartbeat write comes FIRST and is independent of the optional sample.
    The timestamp advances even when the sample fails, so a persistently broken
    proxy is probed on its own interval instead of on every tick."""
    from app.jobs import record_worker_heartbeat
    record_worker_heartbeat()
    if now - last_proxy_sample < PROXY_SAMPLE_INTERVAL_SECONDS:
        return last_proxy_sample
    _sample_fatsecret_safely()
    return now


def _retention_tick(last_retention, now):
    """Enqueue the retention pass on its own interval; never raise.

    Only an ENQUEUE happens on this thread: the pass itself runs as an ordinary
    job in the forked work horse (fresh app/DB per job, as every job does), so
    no database connection is opened in the long-lived parent process. If the
    queue cannot be reached nothing runs inline here; the next tick retries.
    Returns the next ``last_retention``.
    """
    if now - last_retention < RETENTION_INTERVAL_SECONDS:
        return last_retention
    try:
        from app.jobs import get_queue
        from app.jobs.tasks import expire_provider_nutrition
        queue = get_queue()
        if queue is not None:
            queue.enqueue_call(expire_provider_nutrition,
                               timeout=240, ttl=RETENTION_INTERVAL_SECONDS,
                               result_ttl=0, failure_ttl=86400)
    except Exception:
        _log.warning("Saklama gecisi kuyruga atilamadi — sonraki tikte denenecek",
                     exc_info=True)
    return now


def _health_erasure_tick(last_erasure, now):
    """Enqueue the pending-erasure retry on its own interval; never raise.

    Same contract as ``_retention_tick``: only an enqueue happens here, the
    retry runs as an ordinary job in the work horse, and an unreachable queue
    runs nothing inline (the next tick retries). Returns the next
    ``last_erasure``.
    """
    if now - last_erasure < HEALTH_ERASURE_INTERVAL_SECONDS:
        return last_erasure
    try:
        from app.jobs import get_queue
        from app.jobs.tasks import retry_health_erasures
        queue = get_queue()
        if queue is not None:
            retry_slot = int(time.time() // HEALTH_ERASURE_INTERVAL_SECONDS)
            queue.enqueue_call(retry_health_erasures,
                               kwargs={"retry_slot": retry_slot},
                               job_id=f"f01-erasure-retry-{retry_slot}-{uuid.uuid4().hex}",
                               description="axisai.f01.erasure-retry.v1",
                               timeout=900, ttl=HEALTH_ERASURE_INTERVAL_SECONDS,
                               result_ttl=7200, failure_ttl=86400)
    except Exception:
        _log.warning("Saglik verisi silme yeniden denemesi kuyruga atilamadi — "
                     "sonraki tikte denenecek", exc_info=True)
    return now


def _monitoring_tick(sampler, now):
    """R6 M1/M2: publish worker liveness + queue health; never raise.

    Redis only (no database connection in this long-lived parent). Without
    Redis nothing is published, which the M1 alarm treats as breaching.
    """
    try:
        from app.jobs import _rq_connection, get_queue
        sampler.tick(now, get_queue, _rq_connection)
    except Exception:
        _log.warning("Izleme ornegi basarisiz — heartbeat devam ediyor", exc_info=True)


def _start_heartbeat():
    """Heartbeat'i arka-plan daemon thread'de TTL/2'de bir tazele. Boşta (job yok)
    worker bile canlı görünsün diye — RQ'nun kendi worker-heartbeat'i bizim
    bilgilendirici anahtarımızı yazmaz."""
    from app.jobs import WORKER_HEARTBEAT_TTL
    from app.services.release_monitoring import SAMPLE_INTERVAL_SECONDS, WorkerSampler
    stop = threading.Event()
    sampler = WorkerSampler()

    def _loop():
        last_proxy_sample = 0.0
        last_retention = -float(RETENTION_INTERVAL_SECONDS)
        last_erasure = -float(HEALTH_ERASURE_INTERVAL_SECONDS)
        while not stop.is_set():
            last_proxy_sample = _heartbeat_tick(last_proxy_sample,
                                                time.monotonic())
            last_retention = _retention_tick(last_retention, time.monotonic())
            last_erasure = _health_erasure_tick(last_erasure, time.monotonic())
            _monitoring_tick(sampler, time.monotonic())
            # The M1 sample must keep its 60 s cadence even if the heartbeat
            # TTL is raised: wake at least once per sampling interval.
            stop.wait(max(min(WORKER_HEARTBEAT_TTL // 2, SAMPLE_INTERVAL_SECONDS), 5))

    t = threading.Thread(target=_loop, name="worker-heartbeat", daemon=True)
    t.start()
    return stop


def main():
    # Worker ŞEMAYI yönetmez (web konteyneri sahibi): create_app'te create_all/
    # migration çalışmasın — hem pahalı hem web ile boot-migration yarışı olurdu.
    # Süreç geneli ayarla ki fork'lu child'lar da miras alsın.
    os.environ.setdefault("FITX_SKIP_DB_INIT", "1")
    from app.jobs import QUEUE_NAME, _rq, _rq_connection
    rq = _rq()
    if rq is None:
        _log.error("rq kurulu degil — worker baslatilamiyor (pip install rq).")
        sys.exit(1)
    conn = _rq_connection()
    if conn is None:
        _log.error("REDIS_URL yok — worker baslatilamiyor. Prod'da REDIS_URL zorunlu.")
        sys.exit(1)

    use_simple = os.getenv("RQ_SIMPLE_WORKER", "0") == "1" or sys.platform == "win32"
    WorkerCls = rq.SimpleWorker if use_simple else rq.Worker
    queue = rq.Queue(QUEUE_NAME, connection=conn)

    _start_heartbeat()
    _log.info("FitX worker basliyor (queue=%s, worker=%s)", QUEUE_NAME, WorkerCls.__name__)
    worker = WorkerCls([queue], connection=conn)
    worker.work(logging_level=os.getenv("LOG_LEVEL", "INFO"))


if __name__ == "__main__":
    main()
