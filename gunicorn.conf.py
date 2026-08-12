import os


def _positive_int(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


bind = "0.0.0.0:5000"
workers = _positive_int("FITX_WEB_WORKERS", 1)
threads = _positive_int("FITX_WEB_THREADS", 8)
timeout = 300
graceful_timeout = 30
# Flask emits the canonical structured request log with route templates and
# request IDs. A second Gunicorn access log would duplicate it and expose the
# raw request URI before application-level redaction.
accesslog = None
errorlog = "-"


def worker_exit(server, worker):
    """Worker kapanırken son runtime-metrik penceresini boşalt (Hardening PR4).

    Flush thread'i daemon'dır → graceful shutdown'da pencere ortasında ölür ve o
    pencere kaybolur. Kaybedilen pencere tam olarak yeniden başlatmadan ÖNCEKİ
    penceredir; yani deploy'u ya da rollback'i tetikleyen doygunluk/hata artışı.
    `atexit` kancası da kuruludur — bu, gunicorn'un worker'ı kendi yönettiği
    yolda ikinci ve daha güvenilir tetikleyicidir (flush idempotenttir: tampon
    atomik olarak boşaltılır, çift çağrı veri ÇOĞALTMAZ).
    """
    try:
        from app.services import runtime_metrics
        runtime_metrics.flush_on_shutdown()
    except Exception:
        pass
