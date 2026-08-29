"""AI yanıt hattı testleri (app/services/ai_pipeline.py + moderation + formatter).

Sprint 4 WS3: hattın kanonik bileşimi (moderation → context → conversation →
formatter) sağlayıcı mock'larıyla sabitlenir; /ask route davranışı
test_coach_routes.py'de ayrıca korunur.

    python -m pytest tests/test_ai_pipeline.py -v
"""
from types import SimpleNamespace

import pytest

from app.services import (ai_coach, ai_metrics, ai_pipeline, ai_stream, context_builder,
                          moderation, response_formatter)
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
    # Hafıza aşaması KAPALI: burada sınanan, hattın diğer aşamalarının sırası ve
    # argüman aktarımı (WS1 kalıcı hafızanın kendisi test_memory_manager.py'de).
    app.config["AI_MEMORY_ENABLED"] = False
    seen = {}
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "BAĞLAM")

    def fake_run(user_id, question, context, client_history, language="tr",
                 prepared_history=None):
        seen.update(user_id=user_id, question=question, context=context,
                    history=client_history, language=language,
                    prepared_history=prepared_history)
        return "cevap"
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", fake_run)

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(7, "protein?", client_history=[], language="en")

    assert out == {"answer": "cevap", "is_error_fallback": False,
                   "conversation_id": None, "deferred_summarize": None}
    # Hafıza kapalıyken prepared_history None kalır → ai_coach eski client-history
    # yolunu kullanır (davranış Sprint 3 ile birebir aynı).
    assert seen == {"user_id": 7, "question": "protein?", "context": "BAĞLAM",
                    "history": [], "language": "en", "prepared_history": None}


def test_generate_answer_finalize_once(app, monkeypatch):
    """The pipeline owns finalization; the provider router returns raw text."""
    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *args, **kwargs: "ham cevap")
    calls = []

    def counted_finalize(text, language="tr"):
        calls.append((text, language))
        return text, False

    monkeypatch.setattr(response_formatter, "finalize_reply", counted_finalize)
    # Catch a future direct import/call in the legacy router too.
    monkeypatch.setattr(ai_coach, "finalize_reply", counted_finalize, raising=False)

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(1, "soru")

    assert out["answer"] == "ham cevap"
    assert calls == [("ham cevap", "tr")]

def test_pipeline_context_failure_degrades_to_empty(app, monkeypatch):
    app.config["AI_MEMORY_ENABLED"] = False

    def boom(uid, q, language="tr"):
        raise RuntimeError("db down")
    monkeypatch.setattr(context_builder, "fetch_coach_context", boom)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, q, context, h, language="tr",
                        prepared_history=None: f"context=[{context}]")

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(1, "soru")

    assert out["answer"] == "context=[]"


def test_pipeline_memory_failure_degrades_to_client_history(app, monkeypatch):
    # WS1 dayanıklılık sözleşmesi: hafıza katmanı çökerse sohbet KIRILMAZ —
    # conversation_id None döner, eski client-history yolu devreye girer ve
    # sonraki DB sorguları için session temiz bırakılır (rollback).
    from app.services import memory_manager

    app.config["AI_MEMORY_ENABLED"] = True

    def boom(user_id):
        raise RuntimeError("memory down")
    monkeypatch.setattr(memory_manager, "get_or_create_active_conversation", boom)
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "BAĞLAM")
    seen = {}
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, q, c, h, language="tr", prepared_history=None:
                        seen.update(context=c, prepared_history=prepared_history) or "cevap")

    with app.test_request_context("/"):
        out = ai_pipeline.generate_answer(1, "soru", client_history=[{"role": "user"}])

    assert out == {"answer": "cevap", "is_error_fallback": False,
                   "conversation_id": None, "deferred_summarize": None}
    assert seen == {"context": "BAĞLAM", "prepared_history": None}


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


def test_blocking_answer_emits_turn_metric(app, monkeypatch):
    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *args, **kwargs: "cevap")
    increments = []
    monkeypatch.setattr(
        ai_metrics,
        "increment",
        lambda name, dimensions=None: increments.append((name, dimensions)),
    )

    with app.test_request_context("/"):
        ai_pipeline.generate_answer(1, "soru")

    assert increments == [("AITurn", {"mode": "blocking"})]


def test_blocking_fallback_emits_error_metric(app, monkeypatch):
    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *args, **kwargs: COACH_FALLBACKS["tr"]["error"])
    increments = []
    monkeypatch.setattr(
        ai_metrics,
        "increment",
        lambda name, dimensions=None: increments.append((name, dimensions)),
    )

    with app.test_request_context("/"):
        ai_pipeline.generate_answer(1, "soru")

    assert increments == [("AIErrors", {"mode": "blocking"})]


def test_blocking_exception_emits_error_metric_before_reraise(app, monkeypatch):
    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_coach, "_run_coach_conversation", boom)
    increments = []
    monkeypatch.setattr(
        ai_metrics,
        "increment",
        lambda name, dimensions=None: increments.append((name, dimensions)),
    )

    with app.test_request_context("/"):
        with pytest.raises(RuntimeError, match="provider down"):
            ai_pipeline.generate_answer(1, "soru")

    assert increments == [("AIErrors", {"mode": "blocking"})]


def test_stream_defers_inline_summarize_after_answer(app, make_user, monkeypatch):
    """Triage 2026-07-19 #4 (akış ikizi): worker yokken satır-içi özetleme akışın
    İLK token'ından önce koşuyordu. Artık tüm yanıt üretildikten sonra (done
    çerçevesi hazırlanırken) koşar — ilk-token gecikmesine binmez."""
    import app.jobs.tasks as jobs_tasks

    user = make_user("streamsum")
    events = []
    monkeypatch.setattr(jobs_tasks, "summarize_conversation",
                        lambda conv_id: events.append(("summarize", conv_id)) or False)
    monkeypatch.setattr(ai_pipeline, "_context_stage",
                        lambda user_id, question, language: "")

    def fake_stream(*a, **k):
        events.append("stream")
        yield {"type": "done", "text": "cevap", "usage": None}
    monkeypatch.setattr(ai_stream, "stream_coach_answer", fake_stream)

    with app.test_request_context("/"):
        gen = ai_pipeline.stream_answer(user.id, "soru")
        meta = next(gen)
        done = next(gen)
        conv_id = meta["conversation_id"]
        assert done["type"] == "done"
        assert conv_id is not None
        assert events == ["stream"]
        gen.close()
        gen.close()

    assert events == ["stream", ("summarize", conv_id)]


def test_partial_stream_error_is_recorded_as_interruption(app, monkeypatch):
    conversation = SimpleNamespace(id=17)
    recorded = []
    monkeypatch.setattr(ai_pipeline, "_memory_stage",
                        lambda user_id: (conversation, [], None))
    monkeypatch.setattr(ai_pipeline, "_context_stage",
                        lambda user_id, question, language: "")

    def fake_delta_then_error(*args, **kwargs):
        yield {"type": "delta", "text": "K\u0131smi "}
        yield {"type": "delta", "text": "yan\u0131t"}
        yield {"type": "error", "key": "coach.reply_failed",
               "work_performed": True, "partial_text": "K\u0131smi yan\u0131t"}

    monkeypatch.setattr(ai_stream, "stream_coach_answer", fake_delta_then_error)
    monkeypatch.setattr(
        ai_pipeline,
        "_record",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    with app.test_request_context("/"):
        events = list(ai_pipeline.stream_answer(7, "soru"))

    assert events[-1] == {
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": True,
        "partial_text": "K\u0131smi yan\u0131t",
    }
    assert recorded == [
        ((conversation, "soru", "K\u0131smi yan\u0131t"), {"interrupted": True})
    ]
