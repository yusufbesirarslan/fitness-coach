"""Sprint 4 WS8 — arka-plan iş kuyruğu (RQ) altyapısı.

Tasarım ilkesi: **worker OPSİYONEL**. `rq` kurulu değilse ya da Redis
yapılandırılmamışsa `get_queue()` None döner ve `enqueue_or_run(...)` işi
SATIR-İÇİ (inline) çalıştırır — yani mevcut senkron davranış birebir korunur.
Arka-plan altyapısı hiçbir koşulda asıl isteği bozmaz (boto3/anthropic lazy-guard
deseniyle aynı felsefe).

RQ, iş argümanlarını Redis'te pickled bytes olarak saklar → RQ bağlantısı
`decode_responses=False` OLMALI. `app.extensions.redis_client` decode_responses=
True ile kurulu (uygulama geneli string kullanımı için); bu yüzden RQ için
_REDIS_URL'den AYRI bir bytes-bağlantı kurulur. Basit string heartbeat anahtarı
ise sıradan `redis_client` üzerinden yazılır/okunur.
"""
import logging
import os
import threading

from app.config import _REDIS_URL

_log = logging.getLogger(__name__)

QUEUE_NAME = "fitx:ai"
WORKER_HEARTBEAT_KEY = "fitx:worker:alive"
WORKER_HEARTBEAT_TTL = int(os.getenv("WORKER_HEARTBEAT_TTL", "120"))

_rq_module = None       # rq modülü ya da False (import denendi, yok)
_bytes_redis = None     # RQ için decode_responses=False bağlantı


def _rq():
    """rq modülünü guard'lı (lazy) import et. Kurulu değilse None döner."""
    global _rq_module
    if _rq_module is None:
        try:
            import rq
            _rq_module = rq
        except Exception:
            _rq_module = False
    return _rq_module or None


def _rq_connection():
    """RQ için decode_responses=False redis bağlantısı (_REDIS_URL yoksa None)."""
    global _bytes_redis
    if not _REDIS_URL:
        return None
    if _bytes_redis is None:
        import redis
        _bytes_redis = redis.from_url(_REDIS_URL)  # decode_responses varsayılan False
    return _bytes_redis


def get_queue():
    """AI arka-plan iş kuyruğu (rq.Queue) — rq kurulu VE Redis varsa; aksi halde
    None (çağıran satır-içi çalışır). Hiçbir hata yükseltmez."""
    rq = _rq()
    if rq is None:
        return None
    conn = _rq_connection()
    if conn is None:
        return None
    try:
        return rq.Queue(QUEUE_NAME, connection=conn)
    except Exception as e:
        _log.warning("[JOBS] kuyruk olusturulamadi: %s: %s", type(e).__name__, e)
        return None


def _default_retry():
    """Kalıcı arıza için üstel-benzeri yeniden deneme (10s → 60s → 300s)."""
    rq = _rq()
    if rq is None:
        return None
    try:
        return rq.Retry(max=3, interval=[10, 60, 300])
    except Exception:
        return None


def enqueue_or_run(func, *args, **kwargs):
    """İşi kuyruğa at (worker/Redis varsa) yoksa SATIR-İÇİ çalıştır. Kuyruğa atma
    başarısız olursa da satır-içine düşülür — arka-plan asla asıl akışı bozmaz.

    Döner: kuyruğa atıldıysa {"queued": True, "job_id": ...}, satır-içi çalıştıysa
    {"queued": False, "result": ...}."""
    q = get_queue()
    if q is not None:
        try:
            job = q.enqueue_call(func, args=args, kwargs=kwargs,
                                 retry=_default_retry())
            return {"queued": True, "job_id": job.id}
        except Exception as e:
            _log.warning("[JOBS] enqueue basarisiz, satir-ici calistiriliyor: %s: %s",
                         type(e).__name__, e)
    result = func(*args, **kwargs)
    return {"queued": False, "result": result}


def dispatch_background(func, *args, **kwargs):
    """Queue maintenance work, or run it on a daemon thread without a queue.

    Unlike ``enqueue_or_run``, this helper never executes the function on the
    caller's request thread. Maintenance is idempotent and owns its app/session
    context, so a short-lived daemon is the safe worker-less development path.
    """
    q = get_queue()
    if q is not None:
        try:
            job = q.enqueue_call(func, args=args, kwargs=kwargs,
                                 retry=_default_retry())
            return {"queued": True, "job_id": job.id}
        except Exception as e:
            _log.warning(
                "[JOBS] background enqueue basarisiz, daemon thread kullaniliyor: %s: %s",
                type(e).__name__, e)
    try:
        worker = threading.Thread(
            target=func, args=args, kwargs=kwargs, daemon=True)
        worker.start()
        return {"queued": False, "threaded": True}
    except Exception as e:
        _log.warning("[JOBS] background thread baslatilamadi: %s: %s",
                     type(e).__name__, e)
        return {"queued": False, "threaded": False}


# ── Worker canlılık (heartbeat) — /health?deep=1 için BİLGİLENDİRİCİ ────────

def record_worker_heartbeat():
    """Worker canlılık anahtarını SETEX ile tazele. worker.py periyodik çağırır.
    Redis yoksa / hata → sessiz no-op."""
    from app.extensions import redis_client
    if redis_client is None:
        return
    try:
        redis_client.setex(WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_TTL, "1")
    except Exception as e:
        _log.warning("[JOBS] worker heartbeat yazilamadi: %s: %s", type(e).__name__, e)


def worker_alive():
    """Son heartbeat penceresinde bir worker gördük mü? /health?deep=1 için
    BİLGİLENDİRİCİ (gating DEĞİL — worker yokluğu deploy'u düşürmez, iş satır-içine
    düşer). Redis yok → None (bilinmiyor); anahtar yok/süresi geçti → False."""
    from app.extensions import redis_client
    if redis_client is None:
        return None
    try:
        return bool(redis_client.exists(WORKER_HEARTBEAT_KEY))
    except Exception:
        return None


# ── Ölü-mektup (dead-letter) — RQ FailedJobRegistry sarmalayıcıları (CLI için) ──

def failed_jobs(limit=50):
    """Başarısız (retry tükenmiş) iş id'lerini döndür. rq/Redis yoksa []."""
    q = get_queue()
    if q is None:
        return []
    try:
        from rq.registry import FailedJobRegistry
        reg = FailedJobRegistry(queue=q)
        return list(reg.get_job_ids())[:limit]
    except Exception as e:
        _log.warning("[JOBS] failed registry okunamadi: %s: %s", type(e).__name__, e)
        return []


def requeue_failed(job_id):
    """Başarısız bir işi yeniden kuyruğa al. Başarılıysa True. rq/Redis yoksa False."""
    q = get_queue()
    if q is None:
        return False
    try:
        from rq.registry import FailedJobRegistry
        FailedJobRegistry(queue=q).requeue(job_id)
        return True
    except Exception as e:
        _log.warning("[JOBS] requeue basarisiz (%s): %s: %s", job_id, type(e).__name__, e)
        return False
