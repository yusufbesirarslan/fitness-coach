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
- AI_MAX_CONCURRENCY (vars. 5): aynı anda AI route'u işleyen thread tavanı.
- AI_GATE_WAIT_SECONDS (vars. 10): slot için en fazla bekleme; dolarsa 503 +
  Retry-After (kısa bekleme burst'leri yumuşatır, thread'i uzun süre tutmaz).
"""
import os
import threading
from functools import wraps

from flask import current_app, jsonify

AI_MAX_CONCURRENCY = max(1, int(os.getenv("AI_MAX_CONCURRENCY", "5")))
AI_GATE_WAIT_SECONDS = float(os.getenv("AI_GATE_WAIT_SECONDS", "10"))

# INF-5: Menü scrape'i (proxy_scan_menu) AĞ-bağımlıdır; model çağrısı YAPMAZ
# (çıkarım /api/menu/analyze'da olur) ama onlarca saniyelik fetch + alt-sayfa
# crawl boyunca bir AI slotu tutuyordu. 5 eşzamanlı yavaş-site taraması tüm AI
# slotlarını doldurup koç/plan/analyze isteklerini 503'e sokabiliyordu — oysa
# hiçbir model çağrısı uçmuyordu. Scrape'e AYRI ve daha küçük bir semafor ver;
# LLM slotları scrape starvation'ından korunur. (A1 rezerv-thread tasarımını tamamlar.)
SCRAPE_MAX_CONCURRENCY = max(1, int(os.getenv("SCRAPE_MAX_CONCURRENCY", "3")))
SCRAPE_GATE_WAIT_SECONDS = float(os.getenv("SCRAPE_GATE_WAIT_SECONDS", "10"))

_ai_slots = threading.BoundedSemaphore(AI_MAX_CONCURRENCY)
_scrape_slots = threading.BoundedSemaphore(SCRAPE_MAX_CONCURRENCY)


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


def scrape_concurrency_gate(fn):
    """Menü scrape route'ları için AYRI (LLM'den bağımsız) eşzamanlılık kapısı.
    Ağ-bağımlı taramanın AI slotlarını tutmasını önler (INF-5)."""
    return _concurrency_gate(fn, _scrape_slots, SCRAPE_GATE_WAIT_SECONDS,
                             SCRAPE_MAX_CONCURRENCY, "SCRAPE-GATE")
