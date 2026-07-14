"""Kalıcı AI koç hafızası testleri (Sprint 4 WS1 — app/services/memory_manager.py).

Kapsam: aktif konuşma değişmezi (kullanıcı başına bir tane), tur kalıcılaştırma,
rolling context window (bütçe + pruning + özet notu + B15 rol birleştirme),
lazy özetleme tetikleyicisi (sahte hafif LLM), /coach/history ve
/coach/conversation/reset uçları, kullanıcılar arası izolasyon.

    python -m pytest tests/test_memory_manager.py -v
"""
import pytest

from app.extensions import db
from app.models import CoachConversation, CoachMessage
from app.services import memory_manager as mm


def _add_msgs(conv, pairs):
    """[(role, content), ...] → CoachMessage satırları (id sırası = ekleme sırası)."""
    for role, content in pairs:
        db.session.add(CoachMessage(conversation_id=conv.id, role=role,
                                    content=content,
                                    token_estimate=mm.estimate_tokens(content)))
    db.session.commit()


# ---------------------------------------------------------------------------
# Aktif konuşma değişmezi
# ---------------------------------------------------------------------------

def test_get_or_create_returns_same_active_conversation(app, make_user):
    user = make_user("mem_user")
    first = mm.get_or_create_active_conversation(user.id)
    second = mm.get_or_create_active_conversation(user.id)

    assert first.id == second.id
    assert CoachConversation.query.filter_by(user_id=user.id).count() == 1
    assert first.archived_at is None


def test_archive_then_ask_opens_fresh_conversation(app, make_user):
    user = make_user("mem_user")
    old = mm.get_or_create_active_conversation(user.id)
    _add_msgs(old, [("user", "eski soru"), ("assistant", "eski cevap")])

    assert mm.archive_active_conversation(user.id) == 1
    new = mm.get_or_create_active_conversation(user.id)

    assert new.id != old.id
    # Arşivleme SİLMEZ: eski konuşma ve mesajları duruyor (yalnızca damgalandı).
    assert db.session.get(CoachConversation, old.id).archived_at is not None
    assert CoachMessage.query.filter_by(conversation_id=old.id).count() == 2
    # Taze konuşmanın penceresi boş → model eski turları görmez.
    assert mm.build_context_window(new) == []


def test_archive_with_no_active_conversation_is_noop(app, make_user):
    user = make_user("mem_user")
    assert mm.archive_active_conversation(user.id) == 0


# ---------------------------------------------------------------------------
# record_turn
# ---------------------------------------------------------------------------

def test_record_turn_persists_user_and_assistant_with_usage(app, make_user):
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)

    mm.record_turn(conv, "protein ne kadar?", "Günde 1.6 g/kg.",
                   usage={"prompt_tokens": 120, "completion_tokens": 30})

    rows = CoachMessage.query.filter_by(conversation_id=conv.id).order_by(
        CoachMessage.id).all()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == "protein ne kadar?"
    assert rows[0].token_estimate == mm.estimate_tokens("protein ne kadar?")
    # Gerçek `usage` yalnızca assistant turuna yazılır (WS6 token muhasebesi).
    assert rows[1].prompt_tokens == 120
    assert rows[1].completion_tokens == 30
    assert rows[1].interrupted is False
    assert rows[0].prompt_tokens is None


def test_record_turn_can_flag_interrupted(app, make_user):
    # WS2 hazırlığı: yarıda kesilen stream'in kısmi yanıtı işaretlenerek saklanır.
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    mm.record_turn(conv, "soru", "yarım cev", interrupted=True)

    assistant = CoachMessage.query.filter_by(
        conversation_id=conv.id, role="assistant").one()
    assert assistant.interrupted is True


# ---------------------------------------------------------------------------
# build_context_window — bütçe, pruning, özet notu
# ---------------------------------------------------------------------------

def test_window_returns_messages_oldest_to_newest(app, make_user):
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user", "bir"), ("assistant", "iki"),
                     ("user", "üç"), ("assistant", "dört")])

    window = mm.build_context_window(conv, budget=1000)

    assert [m["content"] for m in window] == ["bir", "iki", "üç", "dört"]
    assert [m["role"] for m in window] == ["user", "assistant", "user", "assistant"]


def test_window_prunes_oldest_when_budget_exceeded(app, make_user):
    # Pruning en ESKİden başlar: bütçe dolunca eski turlar pencere DIŞI kalır
    # (DB'den silinmez — sonraki özetleme onları katlayacak).
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user", "A" * 400), ("assistant", "B" * 400),
                     ("user", "C" * 400), ("assistant", "D" * 400)])

    window = mm.build_context_window(conv, budget=200)  # ≈ 2 mesaj (her biri 100 token)

    assert [m["content"][0] for m in window] == ["C", "D"]  # en yeni iki tur
    assert CoachMessage.query.filter_by(conversation_id=conv.id).count() == 4
    used = sum(mm.estimate_tokens(m["content"]) for m in window)
    assert used <= 200


def test_window_truncates_single_oversized_message(app, make_user):
    # Tek mesaj bile bütçeden büyükse: bağlamsız kalmaktansa KIRPILMIŞ son tur.
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user", "X" * 8000)])

    window = mm.build_context_window(conv, budget=50)

    assert len(window) == 1
    assert mm.estimate_tokens(window[0]["content"]) <= 50
    assert window[0]["content"].startswith("X")


def test_window_prepends_summary_note_and_skips_summarized_messages(app, make_user):
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user", "eski"), ("assistant", "eski cevap"),
                     ("user", "yeni")])
    folded = CoachMessage.query.filter_by(conversation_id=conv.id).order_by(
        CoachMessage.id).all()[1]  # ilk iki mesaj özetlendi
    conv.summary = "Kullanıcı kilo vermek istiyor."
    conv.summarized_upto_id = folded.id
    db.session.commit()

    window = mm.build_context_window(conv, budget=1000)

    # Özetlenen turlar pencereye TEKRAR girmez; özet notu en başa gelir.
    assert len(window) == 1
    assert "[KONUŞMA ÖZETİ" in window[0]["content"]
    assert "Kullanıcı kilo vermek istiyor." in window[0]["content"]
    assert window[0]["content"].endswith("yeni")
    assert "eski cevap" not in window[0]["content"]


def test_window_merges_consecutive_same_role_messages(app, make_user):
    # B15: Anthropic ardışık aynı-rol turlarında 400 verir (yarıda kesilen
    # stream'ler ardışık user turu bırakabilir) → birleştirilmeli.
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user", "ilk"), ("user", "ikinci"), ("assistant", "cevap")])

    window = mm.build_context_window(conv, budget=1000)

    assert [m["role"] for m in window] == ["user", "assistant"]
    assert window[0]["content"] == "ilk\nikinci"


def test_window_isolated_per_user(app, make_user):
    # Sızıntı olmamalı: pencere yalnızca kendi konuşmasının mesajlarını görür.
    alice = make_user("alice")
    bob = make_user("bob")
    a_conv = mm.get_or_create_active_conversation(alice.id)
    b_conv = mm.get_or_create_active_conversation(bob.id)
    _add_msgs(a_conv, [("user", "alice sırrı")])
    _add_msgs(b_conv, [("user", "bob sorusu")])

    b_window = mm.build_context_window(b_conv, budget=1000)

    assert [m["content"] for m in b_window] == ["bob sorusu"]
    assert a_conv.id != b_conv.id


# ---------------------------------------------------------------------------
# maybe_summarize — lazy özetleme tetikleyicisi (sahte hafif LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_summarizer(monkeypatch):
    """ai.py hafif yolunu (gpt-4o-mini) sahteler; çağrıları kaydeder."""
    from app.services import ai

    calls = []

    def _chat(messages, system_prompt=None, max_tokens=None, temperature=None):
        calls.append({"messages": messages, "system_prompt": system_prompt})
        return "  ÖZET: kullanıcı bulk dönemde.  "

    monkeypatch.setattr(ai, "_openai_chat", _chat)
    return calls


def test_summarize_not_triggered_below_threshold(app, make_user, fake_summarizer):
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user", "kısa"), ("assistant", "kısa cevap")])

    assert mm.maybe_summarize(conv) is False
    assert fake_summarizer == []          # pahalı çağrı hiç yapılmadı
    assert conv.summary is None


def test_summarize_folds_old_messages_and_advances_pointer(app, make_user,
                                                           fake_summarizer):
    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    # 10 mesaj × ~1000 token = eşiğin (6000) üzerinde bir özetlenmemiş kuyruk.
    _add_msgs(conv, [("user" if i % 2 == 0 else "assistant", f"m{i} " + "z" * 4000)
                     for i in range(10)])
    rows = CoachMessage.query.filter_by(conversation_id=conv.id).order_by(
        CoachMessage.id).all()

    assert mm.maybe_summarize(conv) is True

    assert len(fake_summarizer) == 1
    assert conv.summary == "ÖZET: kullanıcı bulk dönemde."       # strip'lenir
    assert conv.summary_tokens == mm.estimate_tokens(conv.summary)
    # En yeni SUMMARY_KEEP_RECENT mesaj ham bırakılır: işaretçi 10-6=4. mesajda.
    assert conv.summarized_upto_id == rows[10 - mm.SUMMARY_KEEP_RECENT - 1].id
    # Mesajlar SİLİNMEZ — yalnızca pencere dışında kalırlar.
    assert CoachMessage.query.filter_by(conversation_id=conv.id).count() == 10
    window = mm.build_context_window(conv, budget=100_000)
    assert len(window) == mm.SUMMARY_KEEP_RECENT
    assert "[KONUŞMA ÖZETİ" in window[0]["content"]


def test_summarize_failure_does_not_break_chat(app, make_user, monkeypatch):
    # Sağlayıcı çökerse özetleme False döner, konuşma bozulmaz ve bir sonraki
    # istekte yeniden denenir (işaretçi ilerlemez).
    from app.services import ai

    def boom(**_kwargs):
        raise RuntimeError("openai down")
    monkeypatch.setattr(ai, "_openai_chat", boom)

    user = make_user("mem_user")
    conv = mm.get_or_create_active_conversation(user.id)
    _add_msgs(conv, [("user" if i % 2 == 0 else "assistant", f"m{i} " + "z" * 4000)
                     for i in range(10)])

    assert mm.maybe_summarize(conv) is False
    assert conv.summary is None
    assert conv.summarized_upto_id == 0
    assert len(mm.build_context_window(conv, budget=100_000)) == 10


# ---------------------------------------------------------------------------
# Uçlar: GET /coach/history + POST /coach/conversation/reset
# ---------------------------------------------------------------------------

def test_history_endpoint_returns_active_conversation_messages(client, auth_user):
    conv = mm.get_or_create_active_conversation(auth_user.id)
    mm.record_turn(conv, "protein?", "1.6 g/kg")

    body = client.get("/coach/history").get_json()

    assert body["conversation_id"] == conv.id
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("user", "protein?"), ("assistant", "1.6 g/kg")]
    assert body["messages"][0]["created_at"].endswith("Z")


def test_history_endpoint_empty_before_first_question(client, auth_user):
    body = client.get("/coach/history").get_json()
    assert body == {"conversation_id": None, "messages": []}


def test_history_endpoint_requires_auth(client):
    assert client.get("/coach/history").status_code in (302, 401)


def test_history_endpoint_does_not_leak_other_users(client, auth_user, make_user):
    other = make_user("other")
    other_conv = mm.get_or_create_active_conversation(other.id)
    mm.record_turn(other_conv, "başkasının sorusu", "başkasının cevabı")

    body = client.get("/coach/history").get_json()

    assert body == {"conversation_id": None, "messages": []}


def test_reset_endpoint_archives_active_conversation(client, auth_user):
    conv = mm.get_or_create_active_conversation(auth_user.id)
    mm.record_turn(conv, "soru", "cevap")

    body = client.post("/coach/conversation/reset").get_json()

    assert body == {"ok": True, "archived": 1}
    assert db.session.get(CoachConversation, conv.id).archived_at is not None
    # Sonraki geçmiş çağrısı taze (boş) sohbet gösterir; eski mesajlar DB'de kalır.
    assert client.get("/coach/history").get_json()["messages"] == []
    assert CoachMessage.query.filter_by(conversation_id=conv.id).count() == 2


def test_reset_endpoint_is_idempotent(client, auth_user):
    mm.get_or_create_active_conversation(auth_user.id)
    assert client.post("/coach/conversation/reset").get_json()["archived"] == 1
    assert client.post("/coach/conversation/reset").get_json()["archived"] == 0


def test_reset_endpoint_only_archives_own_conversation(client, auth_user, make_user):
    other = make_user("other")
    other_conv = mm.get_or_create_active_conversation(other.id)
    mm.get_or_create_active_conversation(auth_user.id)

    client.post("/coach/conversation/reset")

    assert db.session.get(CoachConversation, other_conv.id).archived_at is None


# ---------------------------------------------------------------------------
# Uçtan uca: /ask turları kalıcılaşır (hat entegrasyonu)
# ---------------------------------------------------------------------------

def test_ask_persists_turn_and_next_ask_sees_it(client, auth_user, monkeypatch):
    from app.services import ai_coach, context_builder

    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    seen = {}

    def fake_run(uid, q, c, h, language="tr", prepared_history=None):
        seen["prepared"] = prepared_history
        return "cevap 1"
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fake_run)

    first = client.post("/ask", json={"question": "ilk soru"}).get_json()
    assert seen["prepared"] == []  # taze konuşma

    # İkinci istek: ilk turun DB'den gelen penceresini görmeli.
    seen.clear()
    second = client.post("/ask", json={"question": "ikinci soru"}).get_json()

    assert second["conversation_id"] == first["conversation_id"]
    assert seen["prepared"] == [{"role": "user", "content": "ilk soru"},
                                {"role": "assistant", "content": "cevap 1"}]
    assert CoachMessage.query.count() == 4  # iki tam tur


def test_ask_error_fallback_is_not_persisted(client, auth_user, monkeypatch):
    # B16: hata-yedeği metni hafızaya YAZILMAZ (kota iadesiyle tutarlı; bir
    # sonraki turun penceresine girip modeli kirletmez).
    from app.services import ai_coach, context_builder
    from app.services.response_formatter import COACH_FALLBACKS

    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *a, **k: COACH_FALLBACKS["tr"]["error"])

    response = client.post("/ask", json={"question": "soru"})

    assert response.status_code == 200
    assert CoachMessage.query.count() == 0


def test_ask_without_memory_flag_keeps_legacy_behaviour(app, client, auth_user,
                                                        monkeypatch):
    # AI_MEMORY_ENABLED=0 → kalıcı hafıza tamamen devre dışı (kill switch):
    # konuşma açılmaz, tur yazılmaz, client history yolu korunur.
    from app.services import ai_coach, context_builder

    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    seen = {}
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, q, c, h, language="tr", prepared_history=None:
                        seen.setdefault("prepared", prepared_history) or "cevap")

    body = client.post("/ask", json={"question": "soru"}).get_json()

    assert body["conversation_id"] is None
    assert seen["prepared"] is None
    assert CoachConversation.query.count() == 0
    assert CoachMessage.query.count() == 0
