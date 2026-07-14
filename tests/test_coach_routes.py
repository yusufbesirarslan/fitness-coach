"""Route tests for the coach blueprint (app/blueprints/coach.py).

/chat (form-tabanlı koç) ve /ask (function-calling chatbot) uçları;
LLM katmanı monkeypatch'lidir. /ask artık ai_pipeline.generate_answer'dan
geçtiği için (WS3) patch noktaları hat aşamalarıdır: context_builder.
fetch_coach_context ve ai_coach._run_coach_conversation (çağrı-anı çözümlü).
Karşılaştırma verisi, geçmiş aktarımı ve graceful-degrade yolları sabitlenir.

    python -m pytest tests/test_coach_routes.py -v
"""
import pytest

from app.blueprints import coach as coach_bp
from app.extensions import db
from app.models import User, UserSession
from app.services import ai_coach, context_builder, premium
from app.services.response_formatter import COACH_FALLBACKS

CHAT_PAYLOAD = {
    "weight": 80, "height": 180, "age": 30, "gender": "male",
    "goal": "kilo verme", "fitness_level": "beginner",
    "current_activity": "active", "message": "selam",
}


@pytest.fixture
def fake_reply(monkeypatch):
    monkeypatch.setattr(coach_bp, "generate_coach_reply",
                        lambda *args, **kwargs: "mock koç yanıtı")


# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------

def test_chat_missing_field_rejected(client, auth_user):
    payload = dict(CHAT_PAYLOAD)
    del payload["gender"]
    response = client.post("/chat", json=payload)
    assert response.status_code == 400
    assert "gender" in response.get_json()["reply"]


def test_chat_non_numeric_rejected(client, auth_user):
    response = client.post("/chat", json={**CHAT_PAYLOAD, "weight": "seksen"})
    assert response.status_code == 400


@pytest.mark.parametrize("field,value", [
    ("weight", []), ("height", {}), ("age", [30]),
])
def test_chat_non_scalar_numeric_rejected(client, auth_user, field, value):
    response = client.post("/chat", json={**CHAT_PAYLOAD, field: value})
    assert response.status_code == 400


def test_chat_first_session_no_comparison(client, auth_user, fake_reply):
    response = client.post("/chat", json=CHAT_PAYLOAD)
    assert response.status_code == 200
    body = response.get_json()
    assert body["coach_reply"] == "mock koç yanıtı"
    assert body["bmr"] == 1780
    assert body["comparison"] is None
    assert UserSession.query.filter_by(user_id=auth_user.id).count() == 1


def test_chat_second_session_returns_weight_comparison(client, auth_user, fake_reply):
    client.post("/chat", json=CHAT_PAYLOAD)
    response = client.post("/chat", json={**CHAT_PAYLOAD, "weight": 78.5})
    comparison = response.get_json()["comparison"]
    assert comparison["previous_weight"] == 80
    assert comparison["weight_diff"] == -1.5
    assert comparison["days_passed"] == 0


# ---------------------------------------------------------------------------
# /ask
# ---------------------------------------------------------------------------

def test_ask_requires_question(client, auth_user):
    assert client.post("/ask", json={"question": "  "}).status_code == 400


def test_ask_passes_context_and_history(client, auth_user, monkeypatch):
    seen = {}
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "bağlam")

    def fake_conversation(user_id, question, context, history,
                          language="tr", prepared_history=None):
        seen.update(user_id=user_id, question=question, context=context,
                    history=history, prepared_history=prepared_history)
        return "cevap"
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fake_conversation)

    history = [{"role": "user", "content": "önceki"}]
    response = client.post("/ask", json={"question": "protein?", "history": history})
    body = response.get_json()
    assert body["answer"] == "cevap"
    assert body["conversation_id"] is not None  # WS1: kalıcı konuşma açıldı
    assert seen["user_id"] == auth_user.id
    assert seen["question"] == "protein?"
    assert seen["context"] == "bağlam"
    assert seen["history"] == history            # client geçmişi hâlâ iletiliyor
    assert seen["prepared_history"] == []        # taze konuşma → boş DB penceresi


def test_ask_non_list_history_dropped(client, auth_user, monkeypatch):
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    seen = {}
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, q, c, h, language="tr", prepared_history=None:
                        seen.setdefault("history", h) or "ok")
    client.post("/ask", json={"question": "soru", "history": "bozuk"})
    assert seen["history"] is None


def test_ask_context_failure_degrades_gracefully(client, auth_user, monkeypatch):
    def boom(uid, q, language="tr"):
        raise RuntimeError("psycopg2 yok")
    monkeypatch.setattr(context_builder, "fetch_coach_context", boom)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, q, context, h, language="tr", prepared_history=None:
                        f"context=[{context}]")
    response = client.post("/ask", json={"question": "soru"})
    assert response.get_json()["answer"] == "context=[]"


def test_ask_conversation_failure_returns_500(client, auth_user, monkeypatch):
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")

    def boom(*args, **kwargs):
        raise RuntimeError("openai down")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", boom)
    response = client.post("/ask", json={"question": "soru"})
    assert response.status_code == 500
    assert "tekrar dene" in response.get_json()["error"]
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS


def test_ask_rejects_oversized_question(client, auth_user, monkeypatch):
    # H2: aşırı uzun soru token-maliyeti amplifikasyonu vektörüdür; modele
    # gönderilmeden 400 ile reddedilmeli (sessiz kırpma değil).
    called = {"run": False}
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")

    def fake_run(*args, **kwargs):
        called["run"] = True
        return "x"
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fake_run)
    response = client.post("/ask", json={"question": "x" * 4001})
    assert response.status_code == 400
    assert called["run"] is False  # pahalı döngüye hiç girilmedi


def test_ask_quota_exhausted_returns_402(client, auth_user, monkeypatch):
    # M4: haftalık AI sohbet kotası dolduğunda /ask 402 (premium_required) döner
    # ve pahalı koç döngüsü hiç çalışmaz.
    called = {"run": False}
    monkeypatch.setattr(coach_bp, "reserve_ai_quota",
                        lambda user, counter_key, limit: False)
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")

    def fake_run(*args, **kwargs):
        called["run"] = True
        return "x"
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fake_run)
    response = client.post("/ask", json={"question": "protein?"})
    assert response.status_code == 402
    assert response.get_json()["premium_required"] is True
    assert called["run"] is False


def test_ask_fallback_refunds_reserved_quota(client, auth_user, monkeypatch):
    # Sağlayıcı hata-yedeği metni döndüğünde (finalize_reply bunu bayraklar)
    # rezerve edilen haftalık hak iade edilir.
    used_during_call = []
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")

    def fallback(uid, question, context, history, language="tr", prepared_history=None):
        fresh_meta = db.session.query(User.user_metadata).filter_by(
            id=auth_user.id).scalar() or {}
        quota = fresh_meta.get("ai_plan_quota") or {}
        used_during_call.append(quota.get("chat", 0))
        return COACH_FALLBACKS["tr"]["error"]

    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fallback)

    response = client.post("/ask", json={"question": "protein?"})

    assert response.status_code == 200
    assert used_during_call == [1]
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS


def test_ask_fallback_preserves_200_when_refund_fails(
        client, auth_user, monkeypatch):
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *args, **kwargs: COACH_FALLBACKS["tr"]["error"])
    monkeypatch.setattr(
        coach_bp, "refund_ai_quota",
        lambda user, counter_key: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    response = client.post("/ask", json={"question": "protein?"})

    assert response.status_code == 200
    assert response.get_json()["answer"] == COACH_FALLBACKS["tr"]["error"]
