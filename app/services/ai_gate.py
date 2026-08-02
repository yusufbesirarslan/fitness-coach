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


class BlockingConcurrencyLimit(RuntimeError):
    pass


def _acquire_before_deadline(semaphore, wait_seconds, *, clock=None):
    monotonic = clock or time.monotonic
    deadline = monotonic() + max(0.0, wait_seconds)
    return semaphore.acquire(timeout=max(0.0, deadline - monotonic()))


@contextmanager
def blocking_concurrency_slot(wait_seconds=None):
    """Bound one blocking AI route sequence with the shared capacity gate."""
    wait = AI_GATE_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not _acquire_before_deadline(_ai_slots, wait):
        raise BlockingConcurrencyLimit("shared blocking capacity exhausted")
    try:
        yield
    finally:
        _ai_slots.release()


@contextmanager
def model_concurrency_slot(wait_seconds=None):
    """Bound one complete provider/fallback sequence independently of routes."""
    wait = AI_GATE_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not _acquire_before_deadline(_model_slots, wait):
        raise BlockingConcurrencyLimit("model blocking capacity exhausted")
    try:
        yield
    finally:
        _model_slots.release()



def enforce_gate_invariants(app):
    """Fail unsafe process-local gate configuration outside development."""
    reserve = WEB_THREADS - (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY)
    problems = []
    if reserve < THREAD_RESERVE_MIN:
        problems.append(
            "thread reserve invariant failed: "
            f"AI({AI_MAX_CONCURRENCY}) + scrape({SCRAPE_MAX_CONCURRENCY}) "
            f"against {WEB_THREADS} threads leaves {reserve}; "
            f"at least {THREAD_RESERVE_MIN} required"
        )
    if WEB_WORKERS != 1:
        problems.append(
            "single worker invariant failed: process-local gates require "
            f"FITX_WEB_WORKERS=1, got {WEB_WORKERS}"
        )
    if not problems:
        return

    message = "; ".join(problems)
    if app.config.get("FITX_IS_DEV", False):
        app.logger.warning("[AI-GATE] %s", message)
        return
    raise RuntimeError(f"[AI-GATE] {message}")


def _concurrency_gate(fn, semaphore, wait_seconds, max_concurrency, label):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not semaphore.acquire(timeout=wait_seconds):
            from app.i18n import t
            current_app.logger.warning(
                "[%s] Eşzamanlı tavan dolu (%s) — istek reddedildi: %s",
                label, max_concurrency, getattr(fn, "__name__", "?"))
            resp = jsonify({"error": t("error.ai_busy")})
            resp.status_code = 503
            resp.headers["Retry-After"] = "15"
            return resp
        try:
            return fn(*args, **kwargs)
        finally:
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
                             AI_MAX_CONCURRENCY, "AI-GATE")


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
            resp = jsonify({"error": t("error.ai_busy")})
            resp.status_code = 503
            resp.headers["Retry-After"] = "15"
            return resp

        released = threading.Event()

        def _release():
            # Tam olarak bir kez bırak: BoundedSemaphore çift release'te ValueError
            # fırlatır ve kapının sayacını kalıcı bozardı.
            if not released.is_set():
                released.set()
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
                             SCRAPE_MAX_CONCURRENCY, "SCRAPE-GATE")
