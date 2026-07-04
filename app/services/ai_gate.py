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

_ai_slots = threading.BoundedSemaphore(AI_MAX_CONCURRENCY)


def ai_concurrency_gate(fn):
    """Route dekoratörü: AI slotu al, yoksa kısa bekle, yine yoksa 503 dön.

    @login_required ve limiter dekoratörlerinin İÇİNE (en yakın fn'e) konmalı ki
    kimliksiz/limitli istekler slot tüketmesin.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _ai_slots.acquire(timeout=AI_GATE_WAIT_SECONDS):
            from app.i18n import t
            current_app.logger.warning(
                "[AI-GATE] Eşzamanlı AI tavanı dolu (%s) — istek reddedildi: %s",
                AI_MAX_CONCURRENCY, getattr(fn, "__name__", "?"))
            resp = jsonify({"error": t("error.ai_busy")})
            resp.status_code = 503
            resp.headers["Retry-After"] = "15"
            return resp
        try:
            return fn(*args, **kwargs)
        finally:
            _ai_slots.release()
    return wrapper
