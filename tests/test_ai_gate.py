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
