"""Sprint 4 WS11 — AI yük/stres testleri (VARSAYILAN koşuda ATLANIR).

`@pytest.mark.load` ile işaretli; `pytest.ini` `addopts = -m "not load"` bunları
normal/CI koşusundan çıkarır. Açıkça çalıştır:

    python -m pytest -m load -v

Hepsi hermetik (ağ yok, sahte sağlayıcılar). Üç eksen:
  1. Eşzamanlılık — model slotu (ai_gate) eş zamanlı model çağrılarını tavana bağlar.
  2. Arıza enjeksiyonu — yük altında her sağlayıcı düşse de akış dostça hata verir, çökmez.
  3. Token taşması — devasa geçmiş bağlam penceresi bütçesini AŞMAZ (pruning tutar).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import ai, ai_recovery
from app.services.ai_gate import AI_MODEL_MAX_CONCURRENCY


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(ai_recovery, "_sleep", lambda s: None)


@pytest.mark.load
def test_model_slot_bounds_concurrency(monkeypatch):
    """20 eş zamanlı _heavy_chat çağrısı, model slotu sayesinde aynı anda EN FAZLA
    AI_MODEL_MAX_CONCURRENCY gerçek model çağrısı yürütür (thread taşmasına karşı A1)."""
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())
    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}

    def fake_claude(*a, **k):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        return "ok"
    monkeypatch.setattr(ai, "_claude_chat", fake_claude)

    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(
            lambda _: ai._heavy_chat([{"role": "user", "content": "x"}]),
            range(20)))

    assert results == ["ok"] * 20
    assert state["peak"] <= AI_MODEL_MAX_CONCURRENCY  # slot tavanı hiç aşılmadı
    assert state["peak"] >= 1


@pytest.mark.load
def test_failure_injection_under_concurrency_stays_friendly(monkeypatch, no_backoff):
    """Yük altında HER İKİ sağlayıcı da düşse (last-good da yok) → _heavy_chat
    sağlayıcı katmanının ürettiği DOSTÇA RuntimeError'ı yükseltir; havuz
    kilitlenmez, 32 çağrının hepsi temiz tamamlanır (gerçek üretim yolunun eşi:
    _claude_chat/_openai_chat ham sağlayıcı metnini zaten dostça mesaja çevirir)."""
    monkeypatch.setattr("app.extensions.redis_client", None)  # last-good yok
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())
    FRIENDLY = "AI servisi hatası. Lütfen tekrar deneyin."

    def boom_bedrock(*a, **k):
        raise ai_recovery.TransientAIError("AI servisi şu an yoğun (rate limit).")
    def boom_openai(*a, **k):
        raise RuntimeError(FRIENDLY)
    monkeypatch.setattr(ai, "_claude_chat", boom_bedrock)
    monkeypatch.setattr(ai, "_openai_chat", boom_openai)

    def call(_):
        try:
            ai._heavy_chat([{"role": "user", "content": "x"}])
            return "unexpected-success"
        except RuntimeError as e:
            return str(e)

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(call, range(32)))

    assert len(results) == 32
    assert all(r == FRIENDLY for r in results)  # hepsi dostça hata, çökme yok


@pytest.mark.load
def test_token_overflow_pruning_holds(app, make_user):
    """Devasa geçmiş: bağlam penceresi AI_CONTEXT_TOKEN_BUDGET'i aşmaz (pruning)."""
    from app.config import AI_CONTEXT_TOKEN_BUDGET
    from app.services import memory_manager

    user = make_user("loaduser")
    conv = memory_manager.get_or_create_active_conversation(user.id)
    big = "x" * 2000  # ~500 token/mesaj
    for _ in range(60):  # ~30k token toplam — bütçenin çok üstünde
        memory_manager.record_turn(conv, big, big)

    window = memory_manager.build_context_window(conv, budget=AI_CONTEXT_TOKEN_BUDGET)
    used = sum(memory_manager.estimate_tokens(m.get("content", "")) for m in window)
    # Pencere bütçeye sığar (özet notu + en yeni mesajlar); sınırsız büyümez.
    # Tek bir mesaj bile bütçeden büyük olabileceğinden küçük bir tolerans bırak.
    assert used <= AI_CONTEXT_TOKEN_BUDGET + memory_manager.estimate_tokens(big)
    assert len(window) < 120  # 60 turun 120 mesajının hepsi girmez
