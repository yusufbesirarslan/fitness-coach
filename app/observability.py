"""Hata izleme (Sentry) + yapısal istek logu.

Sentry YALNIZCA SENTRY_DSN ayarlıysa kurulur — DSN yoksa no-op (lokal/test ağ'a
çıkmaz). sentry-sdk kurulu değilse import guard'lanır (authlib/boto3 lazy-guard
deseniyle aynı): eksik bağımlılık uygulamayı düşürmez, yalnızca hata izlemeyi
kapatır.

İstek logu logfmt biçimindedir (method=.. path=.. status=.. dur_ms=.. user=..) —
gunicorn/stdout log'larında grep'lenebilir ve log toplayıcılarca ayrıştırılabilir.
"""
import os
import time

from flask import current_app, g, request
from flask_login import current_user


def init_sentry(app):
    """SENTRY_DSN varsa Sentry'yi Flask entegrasyonuyla kur (yoksa no-op)."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except Exception:
        app.logger.warning(
            "[SENTRY] SENTRY_DSN ayarlı ama sentry-sdk kurulu değil — hata izleme "
            "kapalı (`pip install sentry-sdk[flask]`).")
        return
    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("SENTRY_RELEASE") or None,
        # Performans izini varsayılan KAPALI (maliyet); env ile açılır.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
        send_default_pii=False,  # gizlilik: kullanıcı PII'sini Sentry'ye yollama
    )
    app.logger.info("[SENTRY] hata izleme etkin (environment=%s).",
                    os.getenv("SENTRY_ENVIRONMENT", "production"))


def start_request_timer():
    g._req_start = time.monotonic()


def log_request(response):
    """Her isteği logfmt satırı olarak logla. /health (sağlık probe'u) atlanır."""
    if request.path == "/health":
        return response
    start = getattr(g, "_req_start", None)
    dur_ms = round((time.monotonic() - start) * 1000, 1) if start is not None else "-"
    try:
        uid = current_user.id if current_user.is_authenticated else "-"
    except Exception:
        uid = "-"
    # L6: ham X-Forwarded-For istemci-kontrollü (birden çok IP, sahte değer, hatta
    # log-injection için satır-başı içerebilir). ProxyFix(x_for=1) zaten güvenilen
    # tek proxy (host nginx) hop'undan gerçek istemci IP'sini remote_addr'a
    # koyuyor; ham başlık yerine onu logla.
    current_app.logger.info(
        "request method=%s path=%s status=%s dur_ms=%s user=%s ip=%s",
        request.method, request.path, response.status_code, dur_ms, uid,
        request.remote_addr or "-",
    )
    return response
