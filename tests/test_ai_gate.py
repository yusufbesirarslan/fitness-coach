"""A1: AI eşzamanlılık kapısı (app/services/ai_gate.py).

Tek worker + 8 thread'te bloklayıcı AI çağrıları tüm thread'leri doldurup
/health dahil her isteği kilitleyebilirdi; kapı ağır AI route'larını semaforla
sınırlar. Slot doluyken 503 + Retry-After döner, slot boşalınca istek geçer.

    python -m pytest tests/test_ai_gate.py -v
"""
import threading

import pytest
from flask import Response, jsonify

from app.services import ai_gate


def _gated_ok():
    @ai_gate.ai_concurrency_gate
    def route():
        return {"ok": True}
    return route


def test_gate_passes_when_slot_available(app):
    with app.test_request_context("/"):
        assert _gated_ok()() == {"ok": True}


def test_route_gate_defaults_to_fail_fast():
    assert ai_gate.AI_GATE_WAIT_SECONDS == 0


def test_model_slot_prevents_simultaneous_entry(monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_model_slots", sem)
    worker_started = threading.Event()
    worker_entered = threading.Event()

    def enter_model_slot():
        worker_started.set()
        with ai_gate.model_concurrency_slot():
            worker_entered.set()

    with ai_gate.model_concurrency_slot():
        worker = threading.Thread(target=enter_model_slot)
        worker.start()
        assert worker_started.wait(timeout=1)
        assert not worker_entered.wait(timeout=0.05)

    assert worker_entered.wait(timeout=1)
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_model_slot_releases_after_error(monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_model_slots", sem)

    with pytest.raises(RuntimeError, match="boom"):
        with ai_gate.model_concurrency_slot():
            raise RuntimeError("boom")

    assert sem.acquire(blocking=False)
    sem.release()


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
    "coach.ask_coach_stream",             # koç akışı (WS2 — slot yanıt kapanana dek)
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


def test_invalid_thread_reserve_is_fatal_in_production(app, monkeypatch):
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 5)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 3)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)

    with pytest.raises(RuntimeError, match="thread reserve"):
        ai_gate.enforce_gate_invariants(app)


def test_multiple_workers_are_fatal_in_production(app, monkeypatch):
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 2)

    with pytest.raises(RuntimeError, match="single worker"):
        ai_gate.enforce_gate_invariants(app)


def test_invalid_gate_configuration_only_warns_in_development(
        app, monkeypatch, caplog):
    app.config["FITX_IS_DEV"] = True
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 2)
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 5)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 3)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)

    with caplog.at_level("WARNING"):
        ai_gate.enforce_gate_invariants(app)

    assert "single worker" in caplog.text
    assert "thread reserve" in caplog.text


def test_valid_gate_configuration_is_silent(app, monkeypatch, caplog):
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 1)
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)

    with caplog.at_level("WARNING"):
        ai_gate.enforce_gate_invariants(app)

    assert "AI-GATE" not in caplog.text


# ── Akış (SSE) kapısı: slot YANIT KAPANANA dek tutulur (WS2) ────────────────
# Normal kapı slotu view döndüğünde bırakır; streaming'de asıl üretim view'dan
# SONRA (generator tüketilirken) çalışır. Slot erken bırakılsaydı kapı stream'leri
# HİÇ sınırlamaz, 8 eşzamanlı stream /health'i düşürebilirdi (A1'in engellediği).

def _streamed():
    return Response((c for c in ["a", "b"]), mimetype="text/event-stream")


def test_stream_gate_holds_slot_until_response_close(app, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    @ai_gate.ai_stream_concurrency_gate
    def route():
        return _streamed()

    with app.test_request_context("/"):
        resp = route()
        # Yanıt daha kapanmadı → slot HÂLÂ tutuluyor.
        assert not sem.acquire(blocking=False)
        resp.close()  # call_on_close → _release
        assert sem.acquire(blocking=False)
        sem.release()


def test_stream_gate_releases_immediately_for_non_stream(app, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    @ai_gate.ai_stream_concurrency_gate
    def route():
        return jsonify({"error": "x"}), 400  # erken çıkış — stream DEĞİL

    with app.test_request_context("/"):
        route()
        # 400 tuple'ı akış değil → slot hemen bırakıldı (yanıt kapanmasını beklemez).
        assert sem.acquire(blocking=False)
        sem.release()


def test_stream_gate_returns_503_when_full(app, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)
    assert sem.acquire(blocking=False)  # tek slotu doldur

    @ai_gate.ai_stream_concurrency_gate
    def route():
        return _streamed()

    try:
        with app.test_request_context("/"):
            resp = route()
            # Stream HİÇ başlamadan 503 döner — SSE içinde hata yollamaktan iyi.
            assert resp.status_code == 503
            assert resp.headers["Retry-After"]
            assert "error" in resp.get_json()
    finally:
        sem.release()


def test_stream_gate_releases_slot_on_exception(app, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    @ai_gate.ai_stream_concurrency_gate
    def boom():
        raise RuntimeError("stream patladı")

    with app.test_request_context("/"):
        with pytest.raises(RuntimeError):
            boom()
        assert sem.acquire(blocking=False)  # hata yolunda da slot geri verildi
        sem.release()


def test_stream_gate_double_close_does_not_over_release(app, monkeypatch):
    # BoundedSemaphore çift release'te ValueError fırlatır ve sayacı KALICI bozardı;
    # _release threading.Event ile tam bir kez çalışmalı.
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    @ai_gate.ai_stream_concurrency_gate
    def route():
        return _streamed()

    with app.test_request_context("/"):
        resp = route()
        resp.close()
        resp.close()  # ikinci kapanış sayacı BOZMAMALI
        assert sem.acquire(blocking=False)      # tam bir slot serbest
        assert not sem.acquire(blocking=False)  # ikincisi yok (fazla release olmadı)
        sem.release()
