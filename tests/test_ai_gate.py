"""A1: AI eşzamanlılık kapısı (app/services/ai_gate.py).

Tek worker + 8 thread'te bloklayıcı AI çağrıları tüm thread'leri doldurup
/health dahil her isteği kilitleyebilirdi; kapı ağır AI route'larını semaforla
sınırlar. Slot doluyken 503 + Retry-After döner, slot boşalınca istek geçer.

    python -m pytest tests/test_ai_gate.py -v
"""
import threading
from types import SimpleNamespace

import pytest
from flask import Response, jsonify

from app.blueprints import food as food_bp
from app.blueprints import social as social_bp
from app.services import mobile_auth
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


class _RecordingSemaphore:
    def __init__(self):
        self.timeouts = []
        self.releases = 0

    def acquire(self, *, timeout):
        self.timeouts.append(timeout)
        return True

    def release(self):
        self.releases += 1


def test_acquire_before_deadline_uses_remaining_monotonic_budget():
    """Catches a gate that gives acquire the original wait instead of remaining time."""
    sem = _RecordingSemaphore()
    clock_values = iter((10.0, 10.25))

    assert ai_gate._acquire_before_deadline(
        sem, 1, clock=lambda: next(clock_values))
    assert sem.timeouts == [0.75]


def test_model_slot_wait_is_capped_by_coach_deadline(monkeypatch):
    sem = _RecordingSemaphore()
    clock_values = iter((10.0, 10.25))
    monkeypatch.setattr(ai_gate, "_model_slots", sem)
    monkeypatch.setattr(
        ai_gate, "time",
        SimpleNamespace(monotonic=lambda: next(clock_values)),
    )

    with ai_gate.model_concurrency_slot(wait_seconds=5.0, deadline=11.0):
        pass

    assert sem.timeouts == [0.75]
    assert sem.releases == 1


def test_blocking_slot_fails_fast_when_full(monkeypatch):
    """Catches entering shared work after its fail-fast capacity is exhausted."""
    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    try:
        with pytest.raises(ai_gate.BlockingConcurrencyLimit):
            with ai_gate.blocking_concurrency_slot(0):
                pytest.fail("must not enter")
    finally:
        sem.release()


def test_model_slot_fails_fast_when_full(monkeypatch):
    """Catches the model slot's former indefinitely blocking acquisition."""
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_model_slots", sem)
    assert sem.acquire(blocking=False)
    try:
        with pytest.raises(ai_gate.BlockingConcurrencyLimit):
            with ai_gate.model_concurrency_slot(0):
                pytest.fail("must not enter")
    finally:
        sem.release()


def test_shared_then_model_nesting_releases_both(monkeypatch):
    """Catches leaked shared or model permits in the required nesting order."""
    monkeypatch.setattr(ai_gate, "_ai_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(ai_gate, "_model_slots", threading.BoundedSemaphore(1))

    for _ in range(2):
        with ai_gate.blocking_concurrency_slot(0):
            with ai_gate.model_concurrency_slot(0):
                pass


def test_shared_saturation_preserves_reserve_and_recovers_without_deadlock(
        app, monkeypatch):
    """Real caller contracts must fail fast while two cheap workers still run."""
    monkeypatch.setattr(ai_gate, "_ai_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(ai_gate, "_model_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    holders_ready = threading.Barrier(3)
    release_holders = threading.Event()

    def hold_shared_then_model():
        with ai_gate.blocking_concurrency_slot(0):
            with ai_gate.model_concurrency_slot(0):
                holders_ready.wait(timeout=2)
                release_holders.wait(timeout=5)

    holders = [
        threading.Thread(target=hold_shared_then_model, daemon=True)
        for _ in range(2)
    ]
    for worker in holders:
        worker.start()
    holders_ready.wait(timeout=2)

    provider_calls = {"food": 0, "suggestion": 0, "mobile": 0}
    monkeypatch.setattr(food_bp, "_get_cached_macros", lambda *args: ({}, ["muz"]))
    monkeypatch.setattr(
        social_bp, "_get_cached_macros", lambda items, **kwargs: ({}, list(items)))

    def provider(name):
        provider_calls[name] += 1
        raise AssertionError(f"{name} provider must remain untouched")

    monkeypatch.setattr(food_bp, "_coach_search_food", lambda _q: provider("food"))
    monkeypatch.setattr(
        social_bp, "_parse_suggestion_items", lambda _body: provider("suggestion"))
    monkeypatch.setattr(social_bp, "_get_fatsecret_token", lambda: provider("suggestion"))
    monkeypatch.setattr(
        mobile_auth.cognito_service, "authenticate",
        lambda *_args: provider("mobile"))

    def original_view(view):
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        return view

    def food_attempt():
        with app.test_request_context("/api/food/search?q=muz"):
            return original_view(food_bp.food_search)().get_json()

    def suggestion_attempt():
        snapshot = SimpleNamespace(
            message_id=1, receiver_id=1, sender_id=2,
            sender_name="Arkadaş", body="- tavuk\n- pilav",
        )
        with app.app_context():
            return social_bp._calculate_meal_suggestion(snapshot)

    def mobile_attempt():
        with app.app_context():
            with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
                mobile_auth.login("alice", "password")
            return caught.value.status, caught.value.code

    results = {}
    errors = {}
    cheap_finished = []

    def capture(name, fn):
        try:
            results[name] = fn()
        except BaseException as exc:  # surfaced in the main test thread below
            errors[name] = exc

    attempts = [
        threading.Thread(target=capture, args=("food", food_attempt), daemon=True),
        threading.Thread(
            target=capture, args=("suggestion", suggestion_attempt), daemon=True),
        threading.Thread(target=capture, args=("mobile", mobile_attempt), daemon=True),
        threading.Thread(target=lambda: cheap_finished.append("cheap-1"), daemon=True),
        threading.Thread(target=lambda: cheap_finished.append("cheap-2"), daemon=True),
    ]
    for worker in attempts:
        worker.start()
    for worker in attempts:
        worker.join(timeout=1)

    finished_before_release = all(not worker.is_alive() for worker in attempts)
    calls_before_release = dict(provider_calls)
    results_before_release = dict(results)
    errors_before_release = dict(errors)
    cheap_before_release = list(cheap_finished)

    release_holders.set()
    for worker in holders:
        worker.join(timeout=2)
    for worker in attempts:
        worker.join(timeout=2)

    assert finished_before_release
    assert not errors_before_release
    assert results_before_release == {
        "food": {"results": []},
        "suggestion": None,
        "mobile": (503, "AUTH_TEMPORARILY_UNAVAILABLE"),
    }
    assert sorted(cheap_before_release) == ["cheap-1", "cheap-2"]
    assert calls_before_release == {"food": 0, "suggestion": 0, "mobile": 0}

    for _ in range(2):
        with ai_gate.blocking_concurrency_slot(0):
            with ai_gate.model_concurrency_slot(0):
                pass


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
    "mobile_api.create_pump_check",       # canonical mobile pump-check analysis
    # iki görselli Pump Check karşılaştırması (Sprint 10 PR3) — tek Bedrock
    # çağrısı, tıpkı tekil pump-check gibi bloklayıcı ağır-AI yüzeyi
    "mobile_api.create_pump_check_comparison",
    "tracking.checkin",                   # haftalık check-in geri bildirimi (N2)
    # Mobile Training PR5: native antrenman tamamlanması, tarayıcıdaki
    # training.complete_workout ile AYNI bloklayıcı Bedrock görü doğrulamasını
    # (validate_pump_check) çalıştırır — aynı thread riski, aynı kapı.
    "mobile_api.complete_workout_session",
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


def test_capacity_snapshot_counts_model_work_outside_route_slots(monkeypatch):
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    monkeypatch.setitem(ai_gate._active, "ai", 1)
    monkeypatch.setitem(ai_gate._active, "model", 3)
    monkeypatch.setitem(ai_gate._active, "scrape", 1)

    assert ai_gate.capacity_snapshot()["thread_reserve"] == 4


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


# ── Production Hardening PR1: kapı/sağlayıcı SLI'ları ──────────────────────

@pytest.fixture
def metrics_on(monkeypatch):
    """runtime_metrics'i tamponlu modda aç (flush thread'i ve ağ YOK)."""
    from app.services import runtime_metrics
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", True)
    monkeypatch.setattr(runtime_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(runtime_metrics, "_ensure_flusher", lambda: None)
    runtime_metrics.reset_for_test()
    yield runtime_metrics
    runtime_metrics.reset_for_test()


def _counter(runtime_metrics, name):
    return {dims: total for (metric, dims), total
            in runtime_metrics._counters.items() if metric == name}


def test_model_slot_records_provider_outcome(metrics_on):
    with ai_gate.model_concurrency_slot("bedrock"):
        pass
    calls = _counter(metrics_on, "AiProviderCalls")
    assert calls == {(("Outcome", "success"), ("Provider", "bedrock")): 1.0}


class _FakeRateLimitError(RuntimeError):
    """Sağlayıcı SDK'sı taklidi — ai_gate SDK import ETMEDEN ad'a göre sınıflar."""


def test_model_slot_classifies_provider_errors_by_name(metrics_on):
    with pytest.raises(_FakeRateLimitError):
        with ai_gate.model_concurrency_slot("openai"):
            raise _FakeRateLimitError("429")
    calls = _counter(metrics_on, "AiProviderCalls")
    assert calls == {(("Outcome", "rate_limit"), ("Provider", "openai")): 1.0}


def test_client_disconnect_is_cancelled_not_error(metrics_on):
    """İstemci akışı koparınca generator kapanır. Bu NORMAL kullanıcı davranışı;
    "error" sayılırsa sağlayıcı hata oranı şişer ve alarm yanlış tetiklenir."""
    with pytest.raises(GeneratorExit):
        with ai_gate.model_concurrency_slot("bedrock-stream"):
            raise GeneratorExit()
    calls = _counter(metrics_on, "AiProviderCalls")
    assert calls == {
        (("Outcome", "cancelled"), ("Provider", "bedrock-stream")): 1.0}


def test_model_slot_records_contention(metrics_on, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_model_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 1)
    entered = threading.Event()

    def worker():
        with ai_gate.model_concurrency_slot("bedrock"):
            entered.set()

    with ai_gate.model_concurrency_slot("bedrock"):
        thread = threading.Thread(target=worker)
        thread.start()
        assert not entered.wait(timeout=0.1)  # slot dolu → beklemek zorunda
    assert entered.wait(timeout=1)
    thread.join(timeout=1)
    assert _counter(metrics_on, "AiModelSlotContended") == {
        (("Provider", "bedrock"),): 1.0}


def test_gate_rejection_is_counted(app, metrics_on, monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    monkeypatch.setattr(ai_gate, "AI_GATE_WAIT_SECONDS", 0)

    @ai_gate.ai_concurrency_gate
    def route():
        return jsonify({"ok": True})

    sem.acquire()  # kapıyı doldur
    try:
        with app.test_request_context("/"):
            assert route().status_code == 503
    finally:
        sem.release()
    assert _counter(metrics_on, "GateRejections") == {(("Gate", "AI-GATE"),): 1.0}


def test_slot_behaviour_unchanged_when_metrics_disabled(monkeypatch):
    """Varsayılan (KAPALI) yolda semafor semantiği PR1 öncesiyle aynı olmalı."""
    from app.services import runtime_metrics
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", False)
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_model_slots", sem)
    with ai_gate.model_concurrency_slot("bedrock"):
        assert not sem.acquire(blocking=False)  # slot gerçekten tutuluyor
    assert sem.acquire(blocking=False)          # ve sonra bırakılıyor
    sem.release()
    assert runtime_metrics._counters == {}      # kapalıyken tampon dolmaz
