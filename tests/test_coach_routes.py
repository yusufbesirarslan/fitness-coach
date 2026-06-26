"""Route tests for the coach blueprint (app/blueprints/coach.py).

/chat (form-tabanlı koç) ve /ask (function-calling chatbot) uçları;
LLM katmanı monkeypatch'lidir. Karşılaştırma verisi, geçmiş aktarımı ve
graceful-degrade yolları sabitlenir.

    python -m pytest tests/test_coach_routes.py -v
"""
import pytest

from app.blueprints import coach as coach_bp
from app.models import UserSession

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
    monkeypatch.setattr(coach_bp, "_fetch_coach_context", lambda uid, q, language="tr": "bağlam")

    def fake_conversation(user_id, question, context, history, language="tr"):
        seen.update(user_id=user_id, question=question, context=context, history=history)
        return "cevap"
    monkeypatch.setattr(coach_bp, "_run_coach_conversation", fake_conversation)

    history = [{"role": "user", "content": "önceki"}]
    response = client.post("/ask", json={"question": "protein?", "history": history})
    assert response.get_json() == {"answer": "cevap"}
    assert seen == {"user_id": auth_user.id, "question": "protein?",
                    "context": "bağlam", "history": history}


def test_ask_non_list_history_dropped(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp, "_fetch_coach_context", lambda uid, q, language="tr": "")
    seen = {}
    monkeypatch.setattr(coach_bp, "_run_coach_conversation",
                        lambda uid, q, c, h, language="tr": seen.setdefault("history", h) or "ok")
    client.post("/ask", json={"question": "soru", "history": "bozuk"})
    assert seen["history"] is None


def test_ask_context_failure_degrades_gracefully(client, auth_user, monkeypatch):
    def boom(uid, q, language="tr"):
        raise RuntimeError("psycopg2 yok")
    monkeypatch.setattr(coach_bp, "_fetch_coach_context", boom)
    monkeypatch.setattr(coach_bp, "_run_coach_conversation",
                        lambda uid, q, context, h, language="tr": f"context=[{context}]")
    response = client.post("/ask", json={"question": "soru"})
    assert response.get_json()["answer"] == "context=[]"


def test_ask_conversation_failure_returns_500(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp, "_fetch_coach_context", lambda uid, q, language="tr": "")

    def boom(*args, **kwargs):
        raise RuntimeError("openai down")
    monkeypatch.setattr(coach_bp, "_run_coach_conversation", boom)
    response = client.post("/ask", json={"question": "soru"})
    assert response.status_code == 500
    assert "tekrar dene" in response.get_json()["error"]
