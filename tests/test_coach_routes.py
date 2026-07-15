"""Route tests for the coach blueprint (app/blueprints/coach.py).

/chat (form-tabanlı koç) ve /ask (function-calling chatbot) uçları;
LLM katmanı monkeypatch'lidir. /ask artık ai_pipeline.generate_answer'dan
geçtiği için (WS3) patch noktaları hat aşamalarıdır: context_builder.
fetch_coach_context ve ai_coach._run_coach_conversation (çağrı-anı çözümlü).
Karşılaştırma verisi, geçmiş aktarımı ve graceful-degrade yolları sabitlenir.

    python -m pytest tests/test_coach_routes.py -v
"""
import json

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


# ---------------------------------------------------------------------------
# /ask/stream (WS2 — SSE akış ikizi; hat mock'lu)
# ---------------------------------------------------------------------------

def _parse_sse(text):
    """SSE metnini [(event, data_dict)] listesine ayrıştır."""
    frames = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        frames.append((event, data))
    return frames


def _fake_stream(events):
    """coach_bp.stream_answer yerine geçen sabit olay üreteci."""
    def _gen(user_id, question, client_history=None, language="tr"):
        for e in events:
            yield e
    return _gen


def _post_stream(client, question="selam"):
    """Akış ucuna POST at, gövdeyi oku ve yanıtı KAPAT.

    Kapatma şart: ai_stream_concurrency_gate slotu response.call_on_close ile
    bırakır. Test istemcisi get_data()'da close() TETİKLEMEZ (prod'da gunicorn
    tetikler) → kapatmazsak slot sızar ve sonraki stream testi 503 alır.
    Yanıt nesnesine ayrıştırılmış çerçeveleri (.frames) ve ham gövdeyi (.raw)
    iliştirip döndürür."""
    resp = client.post("/ask/stream", json={"question": question})
    resp.raw = resp.get_data(as_text=True)
    resp.frames = _parse_sse(resp.raw)
    resp.close()
    return resp


def test_ask_stream_requires_question(client, auth_user):
    resp = client.post("/ask/stream", json={"question": "  "})
    assert resp.status_code == 400
    resp.close()


def test_ask_stream_emits_meta_delta_done_frames(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": 7},
        {"type": "delta", "text": "mer"},
        {"type": "delta", "text": "haba"},
        {"type": "done", "text": "merhaba", "is_error_fallback": False,
         "usage": None},
    ]))

    resp = _post_stream(client)

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert resp.headers["X-Accel-Buffering"] == "no"
    assert resp.headers["Cache-Control"] == "no-cache"

    # meta çerçevesi conversation_id + WS6 request_id taşır (request_id dinamik).
    meta_event, meta_data = resp.frames[0]
    assert meta_event == "meta"
    assert meta_data["conversation_id"] == 7
    assert meta_data.get("request_id") and meta_data["request_id"] != "-"
    assert resp.frames[1:] == [
        ("delta", {"text": "mer"}),
        ("delta", {"text": "haba"}),
        ("done", {"text": "merhaba", "is_error_fallback": False}),
    ]


def test_ask_stream_preserves_turkish_chars(client, auth_user, monkeypatch):
    # _sse ensure_ascii=False: Türkçe karakterler \\uXXXX'e şişmemeli.
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": None},
        {"type": "delta", "text": "günaydın"},
        {"type": "done", "text": "günaydın", "is_error_fallback": False,
         "usage": None},
    ]))
    resp = _post_stream(client)
    assert "günaydın" in resp.raw
    assert "\\u" not in resp.raw


def test_ask_stream_quota_exhausted_returns_402(client, auth_user, monkeypatch):
    called = {"stream": False}
    monkeypatch.setattr(coach_bp, "reserve_ai_quota",
                        lambda user, counter_key, limit: False)

    def fake(*a, **k):
        called["stream"] = True
        yield {"type": "meta", "conversation_id": None}
    monkeypatch.setattr(coach_bp, "stream_answer", fake)

    resp = client.post("/ask/stream", json={"question": "protein?"})
    assert resp.status_code == 402
    assert resp.get_json()["premium_required"] is True
    assert called["stream"] is False  # pahalı akışa hiç girilmedi
    resp.close()


def test_ask_stream_error_frame_refunds_quota(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": 1},
        {"type": "error", "key": "coach.reply_failed"},
    ]))

    resp = _post_stream(client, "protein?")

    assert resp.frames[-1][0] == "error"
    assert "message" in resp.frames[-1][1]
    # Sağlayıcı hatasında rezerve edilen haftalık hak iade edilir.
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS


def test_ask_stream_fallback_done_refunds_quota(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": 1},
        {"type": "done", "text": COACH_FALLBACKS["tr"]["error"],
         "is_error_fallback": True, "usage": None},
    ]))

    resp = _post_stream(client, "protein?")

    assert resp.frames[-1] == ("done", {"text": COACH_FALLBACKS["tr"]["error"],
                                        "is_error_fallback": True})
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS


def test_ask_stream_success_consumes_quota(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": 1},
        {"type": "delta", "text": "gerçek"},
        {"type": "done", "text": "gerçek cevap", "is_error_fallback": False,
         "usage": None},
    ]))

    _post_stream(client, "protein?")
    # Gerçek yanıt → hak harcandı (iade YOK).
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS - 1


def test_ask_stream_provider_exception_emits_friendly_error(
        client, auth_user, monkeypatch):
    # Hat beklenmedik biçimde patlarsa istemciye sağlayıcı metni ASLA sızmaz;
    # dostça i18n hata çerçevesi gider ve hak iade edilir.
    def boom(*a, **k):
        yield {"type": "meta", "conversation_id": 1}
        raise RuntimeError("bedrock stack trace")
    monkeypatch.setattr(coach_bp, "stream_answer", boom)

    resp = _post_stream(client, "protein?")

    assert resp.frames[-1][0] == "error"
    assert "bedrock stack trace" not in resp.raw
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS


# ---------------------------------------------------------------------------
# WS7 — arıza soğuması (cooldown) + burst tavanı + ardışık-arıza sayacı
# ---------------------------------------------------------------------------

def _ok_answer(*a, **k):
    return {"answer": "ok", "conversation_id": 1, "is_error_fallback": False}


def test_ask_cooldown_returns_429_before_quota(client, auth_user, monkeypatch):
    # Kullanıcı soğumadayken /ask 429+Retry-After döner; pahalı hat HİÇ çalışmaz
    # ve haftalık hak HARCANMAZ (soğuma kotadan önce).
    monkeypatch.setattr(coach_bp.ai_recovery, "ai_cooldown_remaining", lambda uid: 30)
    monkeypatch.setattr(coach_bp, "generate_answer",
                        lambda *a, **k: pytest.fail("soğumada pahalı hat çalışmamalı"))
    resp = client.post("/ask", json={"question": "protein?"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "30"
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS


def test_ask_stream_cooldown_returns_429_before_quota(client, auth_user, monkeypatch):
    monkeypatch.setattr(coach_bp.ai_recovery, "ai_cooldown_remaining", lambda uid: 45)
    called = {"stream": False}

    def fake(*a, **k):
        called["stream"] = True
        yield {"type": "meta", "conversation_id": None}
    monkeypatch.setattr(coach_bp, "stream_answer", fake)
    resp = client.post("/ask/stream", json={"question": "protein?"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "45"
    assert called["stream"] is False
    assert premium.remaining_ai_chats(auth_user) == premium.FREE_WEEKLY_AI_CHATS
    resp.close()


def test_ask_success_clears_failure_streak(client, auth_user, monkeypatch):
    cleared = []
    monkeypatch.setattr(coach_bp.ai_recovery, "clear_ai_failures",
                        lambda uid: cleared.append(uid))
    monkeypatch.setattr(coach_bp.ai_recovery, "record_ai_failure",
                        lambda uid: pytest.fail("başarıda arıza kaydı olmamalı"))
    monkeypatch.setattr(coach_bp, "generate_answer", _ok_answer)
    resp = client.post("/ask", json={"question": "protein?"})
    assert resp.status_code == 200
    assert cleared == [auth_user.id]


def test_ask_error_fallback_records_failure(client, auth_user, monkeypatch):
    recorded = []
    monkeypatch.setattr(coach_bp.ai_recovery, "record_ai_failure",
                        lambda uid: recorded.append(uid))
    monkeypatch.setattr(coach_bp, "generate_answer",
                        lambda *a, **k: {"answer": COACH_FALLBACKS["tr"]["error"],
                                         "conversation_id": 1, "is_error_fallback": True})
    resp = client.post("/ask", json={"question": "protein?"})
    assert resp.status_code == 200
    assert recorded == [auth_user.id]


def test_ask_exception_records_failure(client, auth_user, monkeypatch):
    recorded = []
    monkeypatch.setattr(coach_bp.ai_recovery, "record_ai_failure",
                        lambda uid: recorded.append(uid))

    def boom(*a, **k):
        raise RuntimeError("pipeline down")
    monkeypatch.setattr(coach_bp, "generate_answer", boom)
    resp = client.post("/ask", json={"question": "protein?"})
    assert resp.status_code == 500
    assert recorded == [auth_user.id]


def test_ask_stream_error_records_failure(client, auth_user, monkeypatch):
    recorded = []
    monkeypatch.setattr(coach_bp.ai_recovery, "record_ai_failure",
                        lambda uid: recorded.append(uid))
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": 1},
        {"type": "error", "key": "coach.reply_failed"},
    ]))
    _post_stream(client, "protein?")
    assert recorded == [auth_user.id]


def test_ask_stream_success_clears_failure_streak(client, auth_user, monkeypatch):
    cleared = []
    monkeypatch.setattr(coach_bp.ai_recovery, "clear_ai_failures",
                        lambda uid: cleared.append(uid))
    monkeypatch.setattr(coach_bp, "stream_answer", _fake_stream([
        {"type": "meta", "conversation_id": 1},
        {"type": "delta", "text": "x"},
        {"type": "done", "text": "x", "is_error_fallback": False, "usage": None},
    ]))
    _post_stream(client, "protein?")
    assert cleared == [auth_user.id]


@pytest.fixture
def enabled_limiter():
    from app.extensions import limiter
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


def test_ask_burst_limit_trips_on_sixth(client, auth_user, enabled_limiter, monkeypatch):
    # AI_BURST_RATELIMIT = "5 per minute" → aynı dakikadaki 6. istek 429.
    monkeypatch.setattr(coach_bp.ai_recovery, "ai_cooldown_remaining", lambda uid: 0)
    monkeypatch.setattr(coach_bp, "generate_answer", _ok_answer)
    codes = [client.post("/ask", json={"question": "q"}).status_code
             for _ in range(6)]
    assert codes[:5] == [200, 200, 200, 200, 200]
    assert codes[5] == 429
