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

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(),
                                  logging.INFO))
_log = logging.getLogger("fitx.worker")


PROXY_SAMPLE_INTERVAL_SECONDS = 600


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


def _start_heartbeat():
    """Heartbeat'i arka-plan daemon thread'de TTL/2'de bir tazele. Boşta (job yok)
    worker bile canlı görünsün diye — RQ'nun kendi worker-heartbeat'i bizim
    bilgilendirici anahtarımızı yazmaz."""
    from app.jobs import WORKER_HEARTBEAT_TTL
    stop = threading.Event()

    def _loop():
        last_proxy_sample = 0.0
        while not stop.is_set():
            last_proxy_sample = _heartbeat_tick(last_proxy_sample,
                                                time.monotonic())
            stop.wait(max(WORKER_HEARTBEAT_TTL // 2, 5))

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
