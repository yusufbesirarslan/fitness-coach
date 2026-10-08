"""Hata izleme (Sentry) + yapısal istek logu.

Sentry YALNIZCA SENTRY_DSN ayarlıysa kurulur — DSN yoksa no-op (lokal/test ağ'a
çıkmaz). sentry-sdk kurulu değilse import guard'lanır (authlib/boto3 lazy-guard
deseniyle aynı): eksik bağımlılık uygulamayı düşürmez, yalnızca hata izlemeyi
kapatır.

İstek logu logfmt biçimindedir (method=.. path=.. status=.. dur_ms=.. user=..) —
gunicorn/stdout log'larında grep'lenebilir ve log toplayıcılarca ayrıştırılabilir.
"""
import os
import re
import time
import uuid

from flask import current_app, g, request, has_request_context
from flask_login import current_user
from sqlalchemy import inspect as sa_inspect


def _private_note_request():
    return (has_request_context()
            and request.path.startswith("/api/v1/training/exercises/")
            and request.path.endswith("/note"))


def _note_safe_event(event, hint):
    # Sentry may capture JSON bodies or stack locals even with default PII off.
    # Drop note-request events/traces rather than transmitting private prose.
    from urllib.parse import urlsplit
    path = urlsplit(event.get("request", {}).get("url", "")).path
    if (_private_note_request()
            or (path.startswith("/api/v1/training/exercises/") and path.endswith("/note"))):
        return None
    return event


def _note_safe_breadcrumb(crumb, hint):
    return None if _private_note_request() else crumb


_MENU_ROUTES = frozenset({
    "/api/v1/nutrition/menu/analyze", "/api/v1/nutrition/menu/log",
    "/api/menu/analyze", "/api/proxy/scan-menu",
})
# These shared helpers also run in workers without a Flask request context.
_MENU_MODULES = frozenset({
    "app.services.menu_analysis", "app.services.menu_fetch",
    "app.services.menu_extract", "app.services.menu_parse",
    "app.services.menu_remote", "app.services.menu_ocr",
    "app.services.mobile_menu", "app.services.mobile_log_food.menu_confirmation",
    "app.services.ai_nutrition", "app.services.fatsecret", "app.services.ai",
})


def _menu_reporting(event, hint):
    from urllib.parse import urlsplit
    if has_request_context() and request.path in _MENU_ROUTES:
        return True
    if urlsplit(event.get("request", {}).get("url", "")).path in _MENU_ROUTES:
        return True
    if event.get("transaction") in _MENU_ROUTES:
        return True
    record = hint.get("log_record")
    if record is not None and record.name in _MENU_MODULES:
        return True
    for value in event.get("exception", {}).get("values", []):
        for frame in value.get("stacktrace", {}).get("frames", []):
            if frame.get("module") in _MENU_MODULES:
                return True
    return False


def _reporting_safe_event(event, hint):
    if _note_safe_event(event, hint) is None:
        return None
    if not _menu_reporting(event, hint):
        return event
    # Attachments are envelope items, outside the event dictionary.
    hint["attachments"] = []
    # Traces contain span descriptions, SQL parameters and provider metadata.
    if event.get("type") == "transaction":
        return None
    # Reconstruct, rather than redact, arbitrary SDK/scope/integration data.
    safe = {key: event[key] for key in ("event_id", "timestamp", "level", "platform")
            if key in event}
    safe["message"] = "menu_failure"
    safe["tags"] = {"event": "menu_failure"}
    rid = getattr(g, "request_id", None) if has_request_context() else None
    if isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{16}", rid):
        safe["tags"]["request_id"] = rid
    if has_request_context() and request.url_rule is not None:
        route = request.url_rule.rule
        if route in _MENU_ROUTES:
            safe["request"] = {"url": route, "method": request.method}
    values = []
    for value in event.get("exception", {}).get("values", []):
        kind = value.get("type", "")
        if isinstance(kind, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", kind):
            values.append({"type": kind})
    if values:
        safe["exception"] = {"values": values}
        safe["tags"]["error_type"] = values[-1]["type"]
    # Existing handled-error logs have fixed event names and type-only text.
    record = hint.get("log_record")
    if record is not None:
        match = re.fullmatch(
            r"mobile_(?:menu|nutrition) event=(acquisition_failed|analysis_failed|menu_log_failed) "
            r"error_type=([A-Za-z_][A-Za-z0-9_]{0,79}) request_id=[0-9a-f]{16}",
            record.getMessage())
        if match:
            safe["tags"].update(event=match[1], error_type=match[2])
    return safe


def _reporting_safe_breadcrumb(crumb, hint):
    if _note_safe_breadcrumb(crumb, hint) is None:
        return None
    if _menu_reporting({}, hint) or crumb.get("category") in _MENU_MODULES:
        return None
    return crumb


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
        include_local_variables=False,
        max_request_body_size="never",
        enable_logs=False,
        before_send=_reporting_safe_event,
        before_send_transaction=_reporting_safe_event,
        before_breadcrumb=_reporting_safe_breadcrumb,
    )
    app.logger.info("[SENTRY] hata izleme etkin (environment=%s).",
                    os.getenv("SENTRY_ENVIRONMENT", "production"))


def start_request_timer():
    g._req_start = time.monotonic()


def _is_mobile_boundary():
    """Classify matched and routing-error requests in the reserved mobile API."""
    return (
        request.blueprint == "mobile_api"
        or request.path == "/api/v1"
        or request.path.startswith("/api/v1/")
    )


def client_class():
    """İsteğin istemci sınıfı: "mobile" | "web".

    SUNUCU-TARAFI olgudan türetilir (isteği hangi blueprint karşıladı veya ayrılmış
    mobil API ad alanında kaldı), istemcinin yolladığı bir başlıktan DEĞİL.
    Doğrulanmamış istemci etiketleri güvenlik ya da kapasite kararlarında
    kullanılmaz (prod-hardening §6 "Mobile versus web"); burada yalnızca metrik
    boyutu olarak kullanılsa bile aynı kural geçerli — sahte bir başlık metrikleri
    kirletebilirdi.
    """
    return "mobile" if _is_mobile_boundary() else "web"


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


def _logged_user_id(response):
    """Log satırı için kullanıcı kimliği.

    5xx yanıtlarında kimlik anahtarından, SQL'siz okunur: `current_user.id` süresi
    dolmuş (expired) bir örnekte yenileme SELECT'i atar ve 500 kurtarma yolu oturumu
    geri aldıktan (rollback her örneği expire eder) sonra bu, kopuk DB'ye yeni bir
    sorgu demekti (F11). Birincil anahtar identity key'de durur; aynı değeri
    sorgusuz verir. Diğer yanıtlarda davranış değişmez."""
    if not current_user.is_authenticated:
        return "-"
    if response.status_code >= 500:
        state = sa_inspect(current_user._get_current_object(), raiseerr=False)
        if state is not None and state.identity:
            return state.identity[0]
    return current_user.id


def log_request(response):
    """Her isteği logfmt satırı olarak logla. /health (sağlık probe'u) atlanır."""
    if request.path == "/health":
        return response
    start = getattr(g, "_req_start", None)
    dur_ms = round((time.monotonic() - start) * 1000, 1) if start is not None else "-"
    _record_request_metrics(response, dur_ms)
    if _is_mobile_boundary():
        # The mobile boundary uses opaque credentials and owner-bound handles;
        # keep user identity out of its request log systematically.
        uid = "-"
    else:
        try:
            uid = _logged_user_id(response)
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
    # rule and use a bounded placeholder instead of sensitive path text.
    safe_path = request.url_rule.rule if request.url_rule is not None else "<unmatched>"
    current_app.logger.info(
        "request id=%s method=%s path=%s status=%s dur_ms=%s user=%s ip=%s",
        current_request_id(), request.method, safe_path, response.status_code,
        dur_ms, uid, request.remote_addr or "-",
    )
    return response
