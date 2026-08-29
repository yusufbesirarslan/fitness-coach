"""AI eşzamanlılık kapısı (A1): ağır AI route'ları için thread rezervi.

Uygulama tek gunicorn worker + 8 thread çalışır (Dockerfile). Koç/menü/plan AI
çağrıları senkron ve bloklayıcıdır (60s Bedrock / 30s OpenAI × araç döngüsü);
8 eşzamanlı AI isteği TÜM thread'leri doldurup /health dahil her isteği
kuyruğa sokar → Docker HEALTHCHECK + deploy health gate'i yük yüzünden
düşürebilirdi. Bu semafor ağır AI route'larında aynı anda en fazla
AI_MAX_CONCURRENCY isteğe izin verir; kalan thread'ler /health ve ucuz
route'lara her zaman boş kalır.

Semafor SÜREÇ-İÇİDİR — mevcut tek-worker invariantına dayanır (in-memory
cache/limiter fallback'leriyle aynı varsayım, bkz. CLAUDE.md). Worker sayısı
artarsa sınır worker-başına uygulanır; o noktada asıl çözüm AI uçlarını ayrı
worker/queue'ya taşımaktır.

Env ayarları:
- AI_MAX_CONCURRENCY (vars. 4): aynı anda AI route'u işleyen thread tavanı.
- AI_GATE_WAIT_SECONDS (vars. 0): route slotu doluysa beklemeden 503 + Retry-After.
- AI_MODEL_MAX_CONCURRENCY (vars. AI_MAX_CONCURRENCY): gerçek model çağrılarının
  ayrı süreç-içi tavanı; route içindeki paralel fan-out'u da sınırlar.
- FITX_WEB_THREADS (vars. 8): gunicorn --threads değeri (Dockerfile ile eş
  tutulmalı); iki kapının toplamı bunun en az 2 altında kalmalı ki /health ve
  ucuz route'lara gerçek bir rezerv kalsın (I1).
"""
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps

from flask import current_app, jsonify

AI_MAX_CONCURRENCY = max(1, int(os.getenv("AI_MAX_CONCURRENCY", "4")))
AI_GATE_WAIT_SECONDS = float(os.getenv("AI_GATE_WAIT_SECONDS", "0"))
AI_MODEL_MAX_CONCURRENCY = max(
    1,
    int(os.getenv("AI_MODEL_MAX_CONCURRENCY", str(AI_MAX_CONCURRENCY))),
)

WEB_WORKERS = max(1, int(os.getenv("FITX_WEB_WORKERS", "1")))
WEB_THREADS = max(1, int(os.getenv("FITX_WEB_THREADS", "8")))
THREAD_RESERVE_MIN = 2

# INF-5: Menü scrape'i (proxy_scan_menu) AĞ-bağımlıdır; model çağrısı YAPMAZ
# (çıkarım /api/menu/analyze'da olur) ama onlarca saniyelik fetch + alt-sayfa
# crawl boyunca bir AI slotu tutuyordu. 5 eşzamanlı yavaş-site taraması tüm AI
# slotlarını doldurup koç/plan/analyze isteklerini 503'e sokabiliyordu — oysa
# hiçbir model çağrısı uçmuyordu. Scrape'e AYRI ve daha küçük bir semafor ver;
# LLM slotları scrape starvation'ından korunur. (A1 rezerv-thread tasarımını tamamlar.)
SCRAPE_MAX_CONCURRENCY = max(1, int(os.getenv("SCRAPE_MAX_CONCURRENCY", "2")))
SCRAPE_GATE_WAIT_SECONDS = float(os.getenv("SCRAPE_GATE_WAIT_SECONDS", "10"))

_ai_slots = threading.BoundedSemaphore(AI_MAX_CONCURRENCY)
_model_slots = threading.BoundedSemaphore(AI_MODEL_MAX_CONCURRENCY)
_scrape_slots = threading.BoundedSemaphore(SCRAPE_MAX_CONCURRENCY)

# Hardening PR4: rezerv artık YALNIZCA boot-zamanı bir aritmetik değil, ölçülen
# bir büyüklüktür. `docs/ROLLOUT.md` ve MOBILE_AUTH_ENABLED yaşam döngüsü kaydı
# rollout'u durdurma sinyali olarak "ThreadReserve taban değerine yaklaşıyor"
# diyordu ama böyle bir gauge HİÇ yayınlanmıyordu — belgelenmiş ama var olmayan
# bir abort tetikleyicisi. Aşağıdaki sayaçlar semaforların TUTULAN izinlerini
# sayar; `capacity_snapshot()` bunu rezerve çevirir, flush thread'i gauge olarak
# yayar. Sayaçlar semaforun kendi durumunu ÇOĞALTMAZ: karar hâlâ semaforundur,
# bunlar yalnızca gözlemdir (kaydın patlaması bir isteği ASLA düşürmez).
_active_lock = threading.Lock()
_active = {"ai": 0, "model": 0, "scrape": 0}


def _enter(kind):
    with _active_lock:
        _active[kind] += 1


def _leave(kind):
    with _active_lock:
        if _active[kind] > 0:
            _active[kind] -= 1


def capacity_snapshot():
    """Anlık thread kapasitesi görünümü (süreç-içi, ağ'a çıkmaz).

    Model slots normally nest inside AI route slots. Any excess model activity
    represents work outside those route slots and consumes additional threads.
    """
    with _active_lock:
        ai_active = _active["ai"]
        model_active = _active["model"]
        scrape_active = _active["scrape"]
    return {
        "threads": WEB_THREADS,
        "ai_active": ai_active,
        "model_active": model_active,
        "scrape_active": scrape_active,
        "thread_reserve": (WEB_THREADS - ai_active - scrape_active
                           - max(0, model_active - ai_active)),
        "thread_reserve_floor": THREAD_RESERVE_MIN,
    }


def record_capacity_gauges():
    """Kapasite anlık görünümünü gauge olarak yaz (flush thread'inden çağrılır).

    Boyut YOK: bunlar süreç-geneli tekil sinyallerdir → kardinalite sabit kalır.
    """
    from app.services import runtime_metrics
    snapshot = capacity_snapshot()
    runtime_metrics.set_gauge("ThreadReserve", snapshot["thread_reserve"])
    runtime_metrics.set_gauge("AiSlotsActive", snapshot["ai_active"])
    runtime_metrics.set_gauge("AiModelSlotsActive", snapshot["model_active"])
    runtime_metrics.set_gauge("ScrapeSlotsActive", snapshot["scrape_active"])


class BlockingConcurrencyLimit(RuntimeError):
    pass


def _acquire_before_deadline(
        semaphore, wait_seconds, *, deadline=None, clock=None):
    monotonic = clock or time.monotonic
    acquire_deadline = monotonic() + max(0.0, wait_seconds)
    if deadline is not None:
        acquire_deadline = min(acquire_deadline, deadline)
    return semaphore.acquire(
        timeout=max(0.0, acquire_deadline - monotonic()))


@contextmanager
def blocking_concurrency_slot(wait_seconds=None):
    """Bound one blocking AI route sequence with the shared capacity gate."""
    wait = AI_GATE_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not _acquire_before_deadline(_ai_slots, wait):
        raise BlockingConcurrencyLimit("shared blocking capacity exhausted")
    _enter("ai")
    try:
        yield
    finally:
        _leave("ai")
        _ai_slots.release()


def _provider_error_category(exc):
    """Sağlayıcı istisnasını SABİT kümeli bir metrik kategorisine indir.

    Sağlayıcı SDK'ları (openai / anthropic) burada import EDİLMEZ — bu modül
    onlara bağımlı değildir ve olmamalıdır. İki SDK da aynı sınıf adlarını
    (RateLimitError / APITimeoutError / APIConnectionError / APIError) kullandığı
    için sınıflandırma AD üzerinden yapılır. Tanınmayan her şey tek bir "error"
    kovasına düşer → kardinalite sabit kalır.
    """
    # İstemci akışın ortasında koparsa generator kapatılır (ai_stream.produce).
    # Bu bir ARIZA DEĞİLDİR; "error" sayılırsa normal kullanıcı davranışı
    # sağlayıcı hata oranını şişirir ve alarmı yanlış tetikler.
    if isinstance(exc, GeneratorExit):
        return "cancelled"
    name = type(exc).__name__
    if "RateLimit" in name:
        return "rate_limit"
    if "Timeout" in name:
        return "timeout"
    if "Connection" in name:
        return "connection"
    if "Transient" in name:
        return "transient"
    if "APIStatus" in name or "APIError" in name:
        return "api_error"
    return "error"


@contextmanager
def model_concurrency_slot(
        provider="unknown", wait_seconds=None, *, deadline=None):
    """Bound one provider call and retain provider-scoped runtime metrics.

    Acquisition is bounded by both the configured gate wait and an optional
    outer monotonic deadline. Numeric first arguments remain compatible with
    the pre-observability ``model_concurrency_slot(wait_seconds)`` API.
    """
    from app.services import runtime_metrics

    if not isinstance(provider, str) and wait_seconds is None:
        wait_seconds = provider
        provider = "unknown"

    wait = AI_GATE_WAIT_SECONDS if wait_seconds is None else wait_seconds
    metrics_enabled = runtime_metrics.is_enabled()
    dims = {"Provider": str(provider)}
    wait_start = time.monotonic() if metrics_enabled else None

    acquired = False
    if metrics_enabled:
        acquired = _model_slots.acquire(blocking=False)
        if not acquired:
            runtime_metrics.increment("AiModelSlotContended", dimensions=dims)
    if not acquired:
        acquired = _acquire_before_deadline(
            _model_slots, wait, deadline=deadline)
    if not acquired:
        raise BlockingConcurrencyLimit("model blocking capacity exhausted")

    _enter("model")
    call_start = time.monotonic() if metrics_enabled else None
    outcome = "success"
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - classify, then re-raise
        outcome = _provider_error_category(exc)
        raise
    finally:
        _leave("model")
        _model_slots.release()
        if metrics_enabled:
            try:
                now = time.monotonic()
                runtime_metrics.record_latency(
                    "AiModelSlotWait", (call_start - wait_start) * 1000,
                    dimensions=dims)
                runtime_metrics.record_latency(
                    "AiProviderLatency", (now - call_start) * 1000,
                    dimensions=dims)
                runtime_metrics.increment(
                    "AiProviderCalls",
                    dimensions={"Provider": str(provider), "Outcome": outcome})
            except Exception:
                pass



def _db_pool_problems(app):
    """Havuz kapasitesi thread kapasitesini karşılıyor mu?

    Bir istek thread'i işini yaparken bir DB bağlantısı TUTAR. Havuz
    (`pool_size + max_overflow`) thread sayısının altındaysa, thread'ler birbirini
    havuzda bekler: kapılar sağlayıcı beklemesini sınırlar ama DB checkout'u
    sınırsız kalırsa rezerv edilen thread'ler /health'i yine servis EDEMEZ —
    rezerv kâğıt üzerinde kalırdı. Havuz ayarları yalnızca PostgreSQL'de
    uygulanır (SQLite lokal/test yolu havuzlamaz), bu yüzden yalnızca config'e
    yazıldıklarında kontrol edilir.
    """
    size = app.config.get("DB_POOL_SIZE")
    overflow = app.config.get("DB_POOL_MAX_OVERFLOW")
    if size is None or overflow is None:
        return []
    capacity = size + overflow
    if capacity >= WEB_THREADS:
        return []
    return [
        "db pool invariant failed: "
        f"pool_size({size}) + max_overflow({overflow}) = {capacity} "
        f"cannot serve {WEB_THREADS} request threads"
    ]


def blocking_thread_ceiling():
    """Sağlayıcı beklemesine park edebilecek EN FAZLA istek thread'i.

    Model izinleri route/`blocking_concurrency_slot` izinlerinin İÇİNDE alınır,
    yani normalde ek thread talebi YARATMAZ. Ama `AI_MODEL_MAX_CONCURRENCY` AYRI
    yapılandırılabilir bir tavandır: dış kapıdan BÜYÜK bırakılırsa, iç içe geçme
    ileride herhangi bir yerde bozulduğu anda fazlalık kadar EK thread park
    edilebilir. Rezerv aritmetiği bu fazlalığı da sayar — böylece invariant
    "bugünkü çağrı grafiği"ne değil, yapılandırmanın kendisine dayanır.
    """
    model_excess = max(0, AI_MODEL_MAX_CONCURRENCY - AI_MAX_CONCURRENCY)
    return AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY + model_excess


def enforce_gate_invariants(app):
    """Fail unsafe process-local gate configuration outside development."""
    ceiling = blocking_thread_ceiling()
    reserve = WEB_THREADS - ceiling
    problems = []
    if reserve < THREAD_RESERVE_MIN:
        model_excess = max(0, AI_MODEL_MAX_CONCURRENCY - AI_MAX_CONCURRENCY)
        problems.append(
            "thread reserve invariant failed: "
            f"AI({AI_MAX_CONCURRENCY}) + scrape({SCRAPE_MAX_CONCURRENCY}) "
            f"+ model excess({model_excess}) "
            f"against {WEB_THREADS} threads leaves {reserve}; "
            f"at least {THREAD_RESERVE_MIN} required"
        )
    if WEB_WORKERS != 1:
        problems.append(
            "single worker invariant failed: process-local gates require "
            f"FITX_WEB_WORKERS=1, got {WEB_WORKERS}"
        )
    problems.extend(_db_pool_problems(app))
    if not problems:
        return

    message = "; ".join(problems)
    if app.config.get("FITX_IS_DEV", False):
        app.logger.warning("[AI-GATE] %s", message)
        return
    raise RuntimeError(f"[AI-GATE] {message}")


def _record_gate_rejection(label):
    """Kapı reddini SLI olarak say. Boyut yalnızca sabit kümeli kapı etiketi —
    view adı BOYUT DEĞİL (yeni route eklendikçe kardinalite büyürdü); view adı
    zaten log satırında duruyor."""
    try:
        from app.services import runtime_metrics
        runtime_metrics.increment("GateRejections", dimensions={"Gate": label})
    except Exception:
        pass


def _concurrency_gate(fn, semaphore, wait_seconds, max_concurrency, label,
                      kind):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not semaphore.acquire(timeout=wait_seconds):
            from app.i18n import t
            current_app.logger.warning(
                "[%s] Eşzamanlı tavan dolu (%s) — istek reddedildi: %s",
                label, max_concurrency, getattr(fn, "__name__", "?"))
            _record_gate_rejection(label)
            resp = jsonify({"error": t("error.ai_busy")})
            resp.status_code = 503
            resp.headers["Retry-After"] = "15"
            return resp
        _enter(kind)
        try:
            return fn(*args, **kwargs)
        finally:
            _leave(kind)
            semaphore.release()

    # İçe-bakış işareti (test_ai_gate coverage): hangi view'ların kapıyı taşıdığını
    # dış dekoratörlerden (login_required/limiter/premium — hepsi functools.wraps
    # kullanır, __dict__'i DIŞA kopyalar) tespit edebilmek için. @wraps fn.__dict__'i
    # wrapper'a kopyaladıktan SONRA set edilir ki fn'in kendi işareti üzerine yazmasın.
    wrapper._ai_concurrency_gated = True
    return wrapper


def ai_concurrency_gate(fn):
    """Route dekoratörü: AI slotu al, yoksa kısa bekle, yine yoksa 503 dön.

    @login_required ve limiter dekoratörlerinin İÇİNE (en yakın fn'e) konmalı ki
    kimliksiz/limitli istekler slot tüketmesin.
    """
    return _concurrency_gate(fn, _ai_slots, AI_GATE_WAIT_SECONDS,
                             AI_MAX_CONCURRENCY, "AI-GATE", "ai")


def mobile_ai_concurrency_gate(code, message):
    """Mobile variant that preserves the stable mobile error envelope."""
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not _ai_slots.acquire(timeout=AI_GATE_WAIT_SECONDS):
                from app.observability import current_request_id
                _record_gate_rejection("AI-GATE-MOBILE")
                response = jsonify({"error": {
                    "code": code,
                    "message": message,
                    "retryable": True,
                    "request_id": current_request_id(),
                }})
                response.status_code = 503
                response.headers["Retry-After"] = "15"
                return response
            _enter("ai")
            try:
                return fn(*args, **kwargs)
            finally:
                _leave("ai")
                _ai_slots.release()
        wrapper._ai_concurrency_gated = True
        return wrapper
    return decorate


def ai_stream_concurrency_gate(fn):
    """Streaming (SSE) route'ları için AI kapısı — slotu YANIT KAPANANA dek tutar.

    Normal `ai_concurrency_gate` slotu view DÖNDÜĞÜNDE bırakır. Streaming'de view
    anında bir Response(generator) döner ve asıl üretim generator tüketilirken
    (view'dan SONRA) çalışır → slot daha ilk token üretilmeden serbest kalır,
    yani kapı stream'leri HİÇ sınırlamazdı: 8 eşzamanlı stream tüm thread'leri
    doldurup /health'i düşürebilirdi (A1'in tam olarak engellediği senaryo).

    Bu yüzden slot burada alınır (dolu ise stream HİÇ başlamadan 503 + Retry-After
    döner — SSE içinde hata göndermekten iyidir) ve `call_on_close` ile yanıt
    kapanınca (normal bitiş VEYA istemci bağlantıyı koparınca) bırakılır."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _ai_slots.acquire(timeout=AI_GATE_WAIT_SECONDS):
            from app.i18n import t
            current_app.logger.warning(
                "[AI-GATE] Eşzamanlı tavan dolu (%s) — stream reddedildi: %s",
                AI_MAX_CONCURRENCY, getattr(fn, "__name__", "?"))
            _record_gate_rejection("AI-GATE-STREAM")
            resp = jsonify({"error": t("error.ai_busy")})
            resp.status_code = 503
            resp.headers["Retry-After"] = "15"
            return resp

        _enter("ai")
        released = threading.Event()

        def _release():
            # Tam olarak bir kez bırak: BoundedSemaphore çift release'te ValueError
            # fırlatır ve kapının sayacını kalıcı bozardı. Aktif sayaç da AYNI
            # koruma altında düşer — istemci akış ortasında koparsa (call_on_close)
            # izin ve sayaç birlikte geri döner, kapasite sızmaz.
            if not released.is_set():
                released.set()
                _leave("ai")
                _ai_slots.release()

        try:
            rv = fn(*args, **kwargs)
        except Exception:
            _release()
            raise

        # Erken çıkışlar (400/402 gibi jsonify(...), status tuple'ları) stream
        # DEĞİLDİR — slotu hemen bırak, yalnızca gerçek akış onu tutsun.
        response = rv[0] if isinstance(rv, tuple) else rv
        if hasattr(response, "call_on_close") and getattr(response, "is_streamed", False):
            response.call_on_close(_release)
        else:
            _release()
        return rv

    wrapper._ai_concurrency_gated = True
    return wrapper


def scrape_concurrency_gate(fn):
    """Menü scrape route'ları için AYRI (LLM'den bağımsız) eşzamanlılık kapısı.
    Ağ-bağımlı taramanın AI slotlarını tutmasını önler (INF-5)."""
    return _concurrency_gate(fn, _scrape_slots, SCRAPE_GATE_WAIT_SECONDS,
                             SCRAPE_MAX_CONCURRENCY, "SCRAPE-GATE", "scrape")
