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
import uuid

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


def client_class():
    """İsteğin istemci sınıfı: "mobile" | "web".

    SUNUCU-TARAFI olgudan türetilir (isteği hangi blueprint karşıladı), istemcinin
    yolladığı bir başlıktan DEĞİL. Doğrulanmamış istemci etiketleri güvenlik ya da
    kapasite kararlarında kullanılmaz (prod-hardening §6 "Mobile versus web");
    burada yalnızca metrik boyutu olarak kullanılsa bile aynı kural geçerli —
    sahte bir başlık metrikleri kirletebilirdi.
    """
    return "mobile" if request.blueprint == "mobile_api" else "web"


def _status_class(status_code):
    """Status kodunu SABİT kümeli bir sınıfa indir (kardinalite sınırı)."""
    try:
        return f"{int(status_code) // 100}xx"
    except (TypeError, ValueError):
        return "unknown"


def _record_request_metrics(response, dur_ms):
    """HTTP SLI'larını tampona yaz. Metrik yolu isteği ASLA düşürmez."""
    try:
        from app.services import runtime_metrics
        if not runtime_metrics.is_enabled():
            return
        # Boyutlar SABİT kümeli: blueprint (~16) × status sınıfı (~5) × istemci (2).
        # Ham path ya da kullanıcı kimliği ASLA boyut olmaz (yüksek kardinalite).
        dims = {
            "Blueprint": request.blueprint or "root",
            "Status": _status_class(response.status_code),
            "Client": client_class(),
        }
        runtime_metrics.increment("HttpRequests", dimensions=dims)
        if isinstance(dur_ms, (int, float)):
            runtime_metrics.record_latency("HttpLatency", dur_ms, dimensions=dims)
        # Ayrı sayaçlar BİLEREK: 500 bir HATA (kod kusuru), 503 KASITLI yük atma
        # (kapı/limiter doluysa) ve 429 hız sınırı. Aynı "5xx" kovasına atılırlarsa
        # sağlıklı bir sırt-sırta yük atma, gerçek bir arıza gibi alarm üretirdi.
        narrow = {"Blueprint": dims["Blueprint"], "Client": dims["Client"]}
        if response.status_code == 503:
            runtime_metrics.increment("HttpOverload", dimensions=narrow)
        elif response.status_code >= 500:
            runtime_metrics.increment("HttpServerErrors", dimensions=narrow)
        elif response.status_code == 429:
            runtime_metrics.increment("HttpThrottled", dimensions=narrow)
    except Exception:
        pass


def assign_request_id():
    """WS6: her isteğe kısa bir izleme kimliği (request_id) ata. logfmt satırında,
    /ask/stream SSE `meta` çerçevesinde ve (Sentry açıksa) hata etiketinde görünür
    → bir kullanıcı raporunu/çöküşü sunucu loglarıyla ilişkilendirmeyi sağlar.
    İstemci sahte bir kimlik enjekte edememeli diye HER ZAMAN sunucuda üretilir."""
    g.request_id = uuid.uuid4().hex[:16]
    try:
        import sentry_sdk
        sentry_sdk.set_tag("request_id", g.request_id)
    except Exception:
        pass  # sentry yok/kapalı → etiket atlanır (izleme yine loglarda)


def current_request_id():
    """Bu isteğin request_id'si (yoksa "-"). SSE meta / servis katmanları için."""
    return getattr(g, "request_id", "-")


def log_request(response):
    """Her isteği logfmt satırı olarak logla. /health (sağlık probe'u) atlanır."""
    if request.path == "/health":
        return response
    start = getattr(g, "_req_start", None)
    dur_ms = round((time.monotonic() - start) * 1000, 1) if start is not None else "-"
    _record_request_metrics(response, dur_ms)
    if request.blueprint == "mobile_api":
        mobile_user = getattr(g, "mobile_user", None)
        uid = getattr(mobile_user, "id", "-")
    else:
        try:
            uid = current_user.id if current_user.is_authenticated else "-"
        except Exception:
            uid = "-"
    # L6: ham X-Forwarded-For istemci-kontrollü (birden çok IP, sahte değer, hatta
    # log-injection için satır-başı içerebilir). ProxyFix(x_for=1) zaten güvenilen
    # tek proxy (host nginx) hop'undan gerçek istemci IP'sini remote_addr'a
    # koyuyor; ham başlık yerine onu logla.
    # Dynamic resource identifiers are frequently opaque credentials or
    # owner-bound handles. Log the matched route template rather than the
    # concrete path so diagnostics retain route identity without persisting a
    # DiaryItemId (or any other path parameter). Unmatched requests have no
    # rule and keep their literal path for 404 diagnosis.
    safe_path = request.url_rule.rule if request.url_rule is not None else request.path
    current_app.logger.info(
        "request id=%s method=%s path=%s status=%s dur_ms=%s user=%s ip=%s",
        current_request_id(), request.method, safe_path, response.status_code,
        dur_ms, uid, request.remote_addr or "-",
    )
    return response
