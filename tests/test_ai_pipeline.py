"""AI yanıt hattı testleri (app/services/ai_pipeline.py + moderation + formatter).

Sprint 4 WS3: hattın kanonik bileşimi (moderation → context → conversation →
formatter) sağlayıcı mock'larıyla sabitlenir; /ask route davranışı
test_coach_routes.py'de ayrıca korunur.

    python -m pytest tests/test_ai_pipeline.py -v
"""
import pytest

from app.services import ai_coach, ai_pipeline, context_builder, moderation
from app.services.response_formatter import (COACH_FALLBACKS, finalize_reply,
                                             is_coach_error_fallback)


# ---------------------------------------------------------------------------
# moderation (Güvenlik Katmanı)
# ---------------------------------------------------------------------------

def test_validate_question_empty_and_whitespace():
    assert moderation.validate_question("") == "coach.ask_something"
    assert moderation.validate_question("   ") == "coach.ask_something"
    assert moderation.validate_question(None) == "coach.ask_something"


def test_validate_question_length_cap():
    assert moderation.validate_question("x" * moderation.MAX_QUESTION_CHARS) is None
    assert moderation.validate_question(
        "x" * (moderation.MAX_QUESTION_CHARS + 1)) == "coach.question_too_long"


def test_moderate_reply_passthrough():
    assert moderation.moderate_reply("cevap") == "cevap"


# ---------------------------------------------------------------------------
# response_formatter (Biçimlendirici)
# ---------------------------------------------------------------------------

def test_is_coach_error_fallback_truth_table():
    assert is_coach_error_fallback("") is True
    assert is_coach_error_fallback(None) is True
    for lang_texts in COACH_FALLBACKS.values():
        for text in lang_texts.values():
            assert is_coach_error_fallback(text) is True
    assert is_coach_error_fallback("Bugün 1800 kcal aldın.") is False


def test_finalize_reply_replaces_empty_with_language_fallback():
    text, is_fb = finalize_reply("", "en")
    assert text == COACH_FALLBACKS["en"]["error"]
    assert is_fb is True
    text, is_fb = finalize_reply(None, "tr")
    assert text == COACH_FALLBACKS["tr"]["error"]
    assert is_fb is True


def test_finalize_reply_flags_provider_fallback_but_keeps_text():
    # C3: sağlayıcı döngüsünün döndürdüğü yedek metin DEĞİŞTİRİLMEZ ama
    # hata-yedeği olarak işaretlenir (kota iadesi + geçmişe yazmama kararı için).
    tool_fb = COACH_FALLBACKS["tr"]["tool"]
    text, is_fb = finalize_reply(tool_fb, "tr")
    assert text == tool_fb
    assert is_fb is True


def test_finalize_reply_real_answer_untouched():
    text, is_fb = finalize_reply("Gerçek cevap", "tr")
    assert text == "Gerçek cevap"
    assert is_fb is False


# ---------------------------------------------------------------------------
# ai_pipeline.generate_answer (orkestrasyon)
# ---------------------------------------------------------------------------

def test_pipeline_stage_order_and_passthrough(app, monkeypatch):
    seen = {}
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "BAĞLAM")

    def fake_run(user_id, question, context, client_history, language="tr"):
        seen.update(user_id=user_id, question=question, context=context,
                    history=client_history, language=language)
        return "cevap"
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fake_run)

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(7, "protein?", client_history=[], language="en")

    assert out == {"answer": "cevap", "is_error_fallback": False}
    assert seen == {"user_id": 7, "question": "protein?", "context": "BAĞLAM",
                    "history": [], "language": "en"}


def test_pipeline_context_failure_degrades_to_empty(app, monkeypatch):
    def boom(uid, q, language="tr"):
        raise RuntimeError("db down")
    monkeypatch.setattr(context_builder, "fetch_coach_context", boom)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, q, context, h, language="tr": f"context=[{context}]")

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(1, "soru")

    assert out["answer"] == "context=[]"


def test_pipeline_rejects_invalid_question(app, monkeypatch):
    called = {"run": False}
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *a, **k: called.update(run=True) or "x")

    with app.test_request_context("/"):
        with pytest.raises(ValueError, match="coach.ask_something"):
            ai_pipeline.generate_answer(1, "   ")
        with pytest.raises(ValueError, match="coach.question_too_long"):
            ai_pipeline.generate_answer(1, "x" * 4001)
    assert called["run"] is False  # pahalı aşamalara hiç girilmedi


def test_pipeline_marks_provider_fallback(app, monkeypatch):
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *a, **k: COACH_FALLBACKS["tr"]["error"])

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(1, "soru")

    assert out["is_error_fallback"] is True
    assert out["answer"] == COACH_FALLBACKS["tr"]["error"]
