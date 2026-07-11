"""A1: AI eşzamanlılık kapısı (app/services/ai_gate.py).

Tek worker + 8 thread'te bloklayıcı AI çağrıları tüm thread'leri doldurup
/health dahil her isteği kilitleyebilirdi; kapı ağır AI route'larını semaforla
sınırlar. Slot doluyken 503 + Retry-After döner, slot boşalınca istek geçer.

    python -m pytest tests/test_ai_gate.py -v
"""
import threading

from app.services import ai_gate


def _gated_ok():
    @ai_gate.ai_concurrency_gate
    def route():
        return {"ok": True}
    return route


def test_gate_passes_when_slot_available(app):
    with app.test_request_context("/"):
        assert _gated_ok()() == {"ok": True}


def test_gate_returns_503_when_full(app, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)
    assert sem.acquire(blocking=False)  # tek slotu doldur
    try:
        with app.test_request_context("/"):
            resp = _gated_ok()()
            assert resp.status_code == 503
            assert resp.headers["Retry-After"]
            assert "error" in resp.get_json()
    finally:
        sem.release()


def test_gate_releases_slot_after_request_and_on_error(app, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    @ai_gate.ai_concurrency_gate
    def boom():
        raise RuntimeError("AI patladı")

    with app.test_request_context("/"):
        try:
            boom()
        except RuntimeError:
            pass
        # Hata yolunda da slot geri verilmiş olmalı — sızsaydı bir sonraki
        # istek 503 alırdı.
        assert _gated_ok()() == {"ok": True}


# ── Kapı KAPSAMI (N1/N2 regresyon önlemi) ──────────────────────────────────
# Bloklayıcı ağır-AI route'larının HEPSİ eşzamanlılık kapısını taşımalı. İki route
# (/workout/complete Bedrock görü, /checkin Bedrock/OpenAI) uzun süre kapısız kaldı
# ve tüm thread'leri doldurup /health'i düşürebiliyordu; bu testin yokluğu o eksiği
# gizledi. ai_gate wrapper'ı `_ai_concurrency_gated` işaretini set eder ve
# functools.wraps bunu dış dekoratörler (login_required/limiter/premium) boyunca
# view fonksiyonuna kadar taşır.
EXPECTED_GATED_ENDPOINTS = {
    "nutrition.nutrition_plan_generate",  # plan üretimi (Sonnet)
    "menu.analyze_menu",                  # menü makro analizi (Sonnet)
    "menu.proxy_scan_menu",               # menü scrape (ayrı scrape kapısı)
    "coach.ask_coach",                    # koç (araç döngüsü, Bedrock/OpenAI)
    "coach.chat",                         # koç sohbet
    "training.training_plan_generate",    # antrenman planı (Sonnet)
    "training.complete_workout",          # pump-check görü doğrulama (N1)
    "tracking.checkin",                   # haftalık check-in geri bildirimi (N2)
}


def _is_gated(view_func):
    return bool(getattr(view_func, "_ai_concurrency_gated", False))


def test_all_heavy_ai_routes_carry_the_gate(app):
    """Beklenen ağır-AI endpoint'lerinin her biri kapıyı taşımalı."""
    missing = [ep for ep in EXPECTED_GATED_ENDPOINTS
               if ep not in app.view_functions
               or not _is_gated(app.view_functions[ep])]
    assert not missing, f"Eşzamanlılık kapısı EKSİK route'lar: {missing}"


def test_gate_marker_set_only_on_heavy_ai_routes(app):
    """İşaret beklenmedik bir route'a sızmasın (yanlış-pozitif kapsam koruması)."""
    gated = {ep for ep, vf in app.view_functions.items() if _is_gated(vf)}
    unexpected = gated - EXPECTED_GATED_ENDPOINTS
    assert not unexpected, f"Beklenmeyen route kapıyı taşıyor: {sorted(unexpected)}"


# ── Thread rezervi (I1 regresyon önlemi) ───────────────────────────────────
# AI kapısı (5) + scrape kapısı (3) birlikte 8-thread havuzunun TAMAMINI
# doldurabiliyordu — A1'in /health için ayırdığı rezerv fiilen sıfırlanmıştı.
# İki kapının toplamı thread sayısının en az 2 altında kalmalı.

def test_default_gate_caps_leave_thread_reserve():
    reserve = ai_gate.WEB_THREADS - (
        ai_gate.AI_MAX_CONCURRENCY + ai_gate.SCRAPE_MAX_CONCURRENCY)
    assert reserve >= 2, (
        f"AI({ai_gate.AI_MAX_CONCURRENCY}) + scrape({ai_gate.SCRAPE_MAX_CONCURRENCY}) "
        f"kapıları {ai_gate.WEB_THREADS} thread'e karşı yalnız {reserve} rezerv bırakıyor")


def test_warns_when_gates_exhaust_thread_pool(app, monkeypatch, caplog):
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 5)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 3)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    with caplog.at_level("WARNING"):
        ai_gate.warn_if_gates_exhaust_threads(app)
    assert any("rezerv" in r.message for r in caplog.records)


def test_no_warning_when_reserve_intact(app, monkeypatch, caplog):
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    with caplog.at_level("WARNING"):
        ai_gate.warn_if_gates_exhaust_threads(app)
    assert not any("rezerv" in r.message for r in caplog.records)
