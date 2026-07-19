"""AI koç akış (streaming) katmanı testleri — Sprint 4 WS2.

İki seviye sabitlenir:
  1) app/services/ai_stream.py — sağlayıcı akış döngüsü: Bedrock gerçek akış
     (delta → done), araç turları, B-kuralı sağlayıcı geçişi (yalnızca HİÇ delta
     gitmemiş ve HİÇ araç yan etki üretmemişken OpenAI yedeğine düşülür), araç
     tavanında dostça yedek metin.
  2) app/services/ai_pipeline.py stream_answer — meta çerçevesi, done'da hafızaya
     yazma (B16: yalnızca gerçek yanıtlar), istemci koparsa (GeneratorExit) kısmi
     yanıtı interrupted=True kalıcılaştırma, hata çerçevesi geçişi.

Tümü hermetik: sahte Bedrock stream context manager (text_stream +
get_final_message), gerçek AWS/OpenAI yok.

    python -m pytest tests/test_ai_stream.py -v
"""
import threading
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import CoachConversation, CoachMessage
from app.services import ai_coach, ai_gate, ai_pipeline, ai_stream, context_builder


# ── Sahte Bedrock akış altyapısı ────────────────────────────────────────────

class _FakeStream:
    """`bedrock_client.messages.stream(...)` bağlam yöneticisinin sahtesi:
    text_stream (parça üreteci) + get_final_message()."""

    def __init__(self, chunks, final):
        self._chunks = list(chunks)
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for c in self._chunks:
            yield c

    def get_final_message(self):
        return self._final


class _RaisingStream:
    """Belli sayıda parça yayınladıktan SONRA hata fırlatan sahte akış —
    'ilk delta öncesi' ve 'akış ortasında' hata yollarını ayırt etmek için."""

    def __init__(self, pre_chunks=(), exc=None):
        self._pre = list(pre_chunks)
        self._exc = exc or RuntimeError("bedrock stream boom")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for c in self._pre:
            yield c
        raise self._exc

    def get_final_message(self):  # pragma: no cover - çağrılmamalı
        raise AssertionError("hata sonrası get_final_message çağrılmamalı")


def _usage(input_tokens=11, output_tokens=22):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


def _final(stop_reason="end_turn", content=None, usage=None):
    return SimpleNamespace(stop_reason=stop_reason, content=content or [],
                           usage=usage if usage is not None else _usage())


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, block_id, tool_input):
    return SimpleNamespace(type="tool_use", name=name, id=block_id, input=tool_input)


class _FakeBedrock:
    """messages.stream(...) çağrısı başına, verilen sırayla bir akış döndürür."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []

    @property
    def messages(self):
        return SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        assert self._turns, "beklenenden fazla stream çağrısı"
        return self._turns.pop(0)


@pytest.fixture
def bedrock_on(monkeypatch):
    """Bedrock akış yolunu aç ve fake istemciyi enjekte et."""
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())

    def _use(*turns):
        fake = _FakeBedrock(turns)
        monkeypatch.setattr(ai_coach, "bedrock_client", fake)
        return fake

    return _use


def _collect(user_id, question, context="", history=None, language="tr"):
    """stream_coach_answer'ı sonuna kadar tüket, olay listesini döndür."""
    return list(ai_stream.stream_coach_answer(
        user_id, question, context, history or [], language=language))


def _run_generator_in_thread(generator, timeout=1):
    '''Consume a generator without letting a deadlock hang the test suite.'''
    result = []
    errors = []

    def consume():
        try:
            result.extend(generator)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume, daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    assert not worker.is_alive(), 'stream generator did not finish'
    if errors:
        raise errors[0]
    return result


# ── _usage_of yardımcı ──────────────────────────────────────────────────────

def test_usage_of_reads_provider_tokens():
    msg = SimpleNamespace(usage=_usage(input_tokens=7, output_tokens=13))
    assert ai_stream._usage_of(msg) == {"prompt_tokens": 7, "completion_tokens": 13}


def test_usage_of_none_when_absent():
    assert ai_stream._usage_of(SimpleNamespace(usage=None)) is None
    assert ai_stream._usage_of(SimpleNamespace()) is None


# ── Bedrock akış yolu: tek tur ───────────────────────────────────────────────

def test_bedrock_streams_deltas_then_done(app, bedrock_on):
    bedrock_on(_FakeStream(
        ["Bugün ", "1800 ", "kcal aldın."],
        _final(content=[_text_block("Bugün 1800 kcal aldın.")])))

    with app.app_context():
        events = _collect(1, "bugün ne yedim?")

    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas == ["Bugün ", "1800 ", "kcal aldın."]
    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == "Bugün 1800 kcal aldın."
    assert done["provider"] == "bedrock"
    assert done["usage"] == {"prompt_tokens": 11, "completion_tokens": 22}


def test_bedrock_empty_chunks_are_skipped(app, bedrock_on):
    # Sağlayıcı bazen boş string delta yollar — istemciye gitmemeli.
    bedrock_on(_FakeStream(
        ["", "cevap", ""],
        _final(content=[_text_block("cevap")])))

    with app.app_context():
        events = _collect(1, "soru")

    assert [e["text"] for e in events if e["type"] == "delta"] == ["cevap"]


# ── Bedrock akış yolu: araç döngüsü ──────────────────────────────────────────

def test_bedrock_tool_loop_runs_tool_then_streams_final(app, bedrock_on, monkeypatch):
    calls = []

    def fake_dispatch(user_id, name, arguments_json):
        calls.append((user_id, name, arguments_json))
        return "araç sonucu"
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", fake_dispatch)

    tool_turn = _FakeStream(
        [],  # araç turu metin akıtmıyor
        _final(stop_reason="tool_use",
               content=[_tool_use_block("query_fitx_metrics", "t1",
                                        {"metric_type": "nutrition"})]))
    final_turn = _FakeStream(
        ["Bugün 1800 kcal aldın."],
        _final(content=[_text_block("Bugün 1800 kcal aldın.")]))
    fake = bedrock_on(tool_turn, final_turn)

    with app.app_context():
        events = _collect(42, "bugün ne yedim?")

    assert calls == [(42, "query_fitx_metrics", '{"metric_type": "nutrition"}')]
    assert [e["text"] for e in events if e["type"] == "delta"] == ["Bugün 1800 kcal aldın."]
    assert events[-1]["type"] == "done"
    assert events[-1]["text"] == "Bugün 1800 kcal aldın."
    # İki tur = iki stream çağrısı; ikinci çağrının convo'su araç sonucunu taşır.
    assert len(fake.calls) == 2
    second_convo = fake.calls[1]["messages"]
    assert any(m["role"] == "user" and isinstance(m["content"], list)
               and m["content"] and m["content"][0].get("type") == "tool_result"
               for m in second_convo)


def test_stream_bedrock_releases_model_slot_before_tool_dispatch(
        bedrock_on, monkeypatch):
    monkeypatch.setattr(ai_gate, '_model_slots', threading.BoundedSemaphore(1))

    def nested_slot_tool(*args, **kwargs):
        with ai_gate.model_concurrency_slot():
            return 'araç sonucu'

    monkeypatch.setattr(ai_coach, '_dispatch_coach_tool', nested_slot_tool)
    tool_turn = _FakeStream([], _final(
        stop_reason='tool_use',
        content=[_tool_use_block('query_fitx_metrics', 't1', {})]))
    final_turn = _FakeStream(
        ['Tamam'], _final(content=[_text_block('Tamam')]))
    bedrock_on(tool_turn, final_turn)

    result = _run_generator_in_thread(
        ai_stream._stream_bedrock(7, 'foto', '', [], 'tr'), timeout=1)

    assert result[-1]['type'] == 'done'


def test_slow_consumer_does_not_retain_model_slot(bedrock_on, monkeypatch):
    provider_finished = threading.Event()

    class NotifyingBoundedSemaphore(threading.BoundedSemaphore):
        def release(self, n=1):
            super().release(n)
            provider_finished.set()

    monkeypatch.setattr(ai_gate, '_model_slots', NotifyingBoundedSemaphore(1))
    bedrock_on(_FakeStream(
        ['Merhaba'], _final(content=[_text_block('Merhaba')])))

    gen = ai_stream._stream_bedrock(7, 'merhaba', '', [], 'tr')
    try:
        assert next(gen) == {'type': 'delta', 'text': 'Merhaba'}
        assert provider_finished.wait(timeout=1)
        assert ai_gate._model_slots.acquire(blocking=False)
        ai_gate._model_slots.release()
    finally:
        gen.close()


def test_bedrock_tool_loop_cap_emits_friendly_fallback(app, bedrock_on, monkeypatch):
    # Model sürekli araç isteyip final metne hiç varmazsa döngü tavana dayanır;
    # kullanıcı yanıtsız kalmamalı — dostça yedek metin akıtılır.
    monkeypatch.setattr(ai_coach, "_COACH_TOOL_LOOP_CAP", 2)
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool",
                        lambda *a, **k: "sonuç")

    def _tool_turn():
        return _FakeStream([], _final(
            stop_reason="tool_use",
            content=[_tool_use_block("query_fitx_metrics", "t", {})]))
    bedrock_on(_tool_turn(), _tool_turn())

    with app.app_context():
        events = _collect(1, "soru")

    fallback = ai_coach._COACH_FALLBACKS[ai_coach._coach_lang("tr")]["tool"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["text"] == fallback
    assert done["provider"] == "bedrock"


# ── B-kuralı: sağlayıcı geçişi yalnızca ilk delta/araç ÖNCESİ ────────────────

def test_bedrock_error_before_first_delta_falls_back_to_openai(
        app, bedrock_on, monkeypatch):
    # İlk token'dan ÖNCE ve hiç araç çalışmadan patlarsa OpenAI yedeğine düşülür;
    # kullanıcı akışta hiçbir Bedrock parçası GÖRMEDİĞİ için geçiş güvenli.
    bedrock_on(_RaisingStream(pre_chunks=()))
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: "openai cevabı")

    with app.app_context():
        events = _collect(1, "soru")

    assert "".join(e["text"] for e in events if e["type"] == "delta") == "openai cevabı"
    done = events[-1]
    assert done["type"] == "done"
    assert done["provider"] == "openai"


def test_bedrock_error_after_first_delta_emits_error_no_fallback(
        app, bedrock_on, monkeypatch):
    # İlk delta İSTEMCİYE GİTTİKTEN sonra patlarsa sağlayıcı DEĞİŞTİRİLMEZ
    # (görülen metni bozardı / yan etkiyi tekrarlardı) — dostça hata çerçevesi.
    bedrock_on(_RaisingStream(pre_chunks=["yarım cümle"]))
    called = {"openai": False}
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: called.update(openai=True) or "X")

    with app.app_context():
        events = _collect(1, "soru")

    assert [e["text"] for e in events if e["type"] == "delta"] == ["yarım cümle"]
    assert events[-1] == {
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": True,
        "partial_text": "yar\u0131m c\u00fcmle",
    }
    assert called["openai"] is False  # yan etki tekrarı YOK


def test_bedrock_error_after_tool_side_effect_no_fallback(
        app, bedrock_on, monkeypatch):
    # Bir araç YAN ETKİ ürettikten sonra ikinci tur patlarsa: hiç delta gitmemiş
    # olsa bile sağlayıcı değiştirmek yan etkiyi tekrarlar → dostça hata çerçevesi.
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", lambda *a, **k: "ok")
    tool_turn = _FakeStream([], _final(
        stop_reason="tool_use",
        content=[_tool_use_block("confirm_and_commit_meal_log", "t", {})]))
    bedrock_on(tool_turn, _RaisingStream(pre_chunks=()))
    called = {"openai": False}
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: called.update(openai=True) or "X")

    with app.app_context():
        events = _collect(1, "soru")

    assert events[-1] == {
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": True,
        "partial_text": "",
    }
    assert called["openai"] is False


def test_bedrock_processing_error_after_tool_work_emits_work_aware_error(
        app, bedrock_on, monkeypatch, caplog):
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", lambda *a, **k: "ok")
    tool_turn = _FakeStream([], _final(
        stop_reason="tool_use",
        content=[_tool_use_block("confirm_and_commit_meal_log", "t", {})]))

    class BrokenFinal:
        stop_reason = "end_turn"
        content = []

        @property
        def usage(self):
            raise RuntimeError("sensitive parsing detail")

    bedrock_on(tool_turn, _FakeStream([], BrokenFinal()))
    monkeypatch.setattr(
        ai_coach,
        "_run_coach_conversation_openai",
        lambda *a, **k: pytest.fail("tool work must prevent provider fallback"),
    )

    with app.app_context():
        events = _collect(1, "soru")

    assert events[-1] == {
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": True,
        "partial_text": "",
    }
    assert "sensitive parsing detail" not in caplog.text


def test_bedrock_empty_answer_no_tools_falls_back(app, bedrock_on, monkeypatch):
    # Metin bloğu yok + hiç araç çalışmadı → soru cevaplanabilirdi, OpenAI'ya düş
    # (bloklayıcı yoldaki B4 mantığının akış eşdeğeri).
    bedrock_on(_FakeStream([], _final(stop_reason="end_turn", content=[])))
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: "openai cevabı")

    with app.app_context():
        events = _collect(1, "soru")

    assert events[-1]["provider"] == "openai"
    assert events[-1]["text"] == "openai cevabı"


# ── OpenAI yedek yolu (gerçek akış değil; parçalanmış blok) ───────────────────

def test_disabled_bedrock_uses_openai_fallback(app, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: "openai cevabı")

    with app.app_context():
        events = _collect(1, "soru")

    assert "".join(e["text"] for e in events if e["type"] == "delta") == "openai cevabı"
    assert events[-1] == {"type": "done", "text": "openai cevabı",
                          "usage": None, "provider": "openai"}


def test_openai_fallback_chunks_long_text(app, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    long = "x" * (ai_stream.CHUNK_CHARS * 2 + 5)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: long)

    with app.app_context():
        events = _collect(1, "soru")

    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert len(deltas) == 3                    # 48 + 48 + 5
    assert "".join(deltas) == long
    assert events[-1]["text"] == long


def test_openai_fallback_error_emits_error_event(app, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)

    def boom(*a, **k):
        raise RuntimeError("openai down")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai", boom)

    with app.app_context():
        events = _collect(1, "soru")

    assert events == [{
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": False,
        "partial_text": "",
    }]


# ── ai_pipeline.stream_answer (orkestrasyon) ─────────────────────────────────

def _fake_provider(events):
    """ai_stream.stream_coach_answer yerine geçen sabit olay üreteci."""
    def _gen(user_id, question, context, history, language="tr"):
        for e in events:
            yield e
    return _gen


@pytest.fixture
def stream_env(app, make_user, monkeypatch):
    """Hafıza açık, bağlam sabit; kullanıcı + app context sağlar."""
    app.config["AI_MEMORY_ENABLED"] = True
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    user = make_user("streamer")
    return user


def _drive(user_id, question, language="tr", client_history=None):
    return list(ai_pipeline.stream_answer(user_id, question,
                                          client_history=client_history,
                                          language=language))


def test_stream_answer_meta_deltas_done_and_records(stream_env, app, monkeypatch):
    from app.services import ai_stream as _s
    monkeypatch.setattr(_s, "stream_coach_answer", _fake_provider([
        {"type": "delta", "text": "mer"},
        {"type": "delta", "text": "haba"},
        {"type": "done", "text": "merhaba", "usage":
            {"prompt_tokens": 5, "completion_tokens": 9}},
    ]))

    with app.test_request_context("/"):
        events = _drive(stream_env.id, "selam")

        assert events[0]["type"] == "meta"
        conv_id = events[0]["conversation_id"]
        assert conv_id is not None
        assert [e["text"] for e in events if e["type"] == "delta"] == ["mer", "haba"]
        done = events[-1]
        assert done == {"type": "done", "text": "merhaba",
                        "is_error_fallback": False,
                        "usage": {"prompt_tokens": 5, "completion_tokens": 9}}

        # B16: gerçek yanıt hafızaya yazıldı — user + assistant satırı.
        rows = (CoachMessage.query.filter_by(conversation_id=conv_id)
                .order_by(CoachMessage.id.asc()).all())
        assert [(r.role, r.content) for r in rows] == [
            ("user", "selam"), ("assistant", "merhaba")]
        assistant = rows[1]
        assert assistant.prompt_tokens == 5
        assert assistant.completion_tokens == 9
        assert assistant.interrupted is False


def test_stream_answer_error_event_passthrough_no_record(stream_env, app, monkeypatch):
    from app.services import ai_stream as _s
    monkeypatch.setattr(_s, "stream_coach_answer", _fake_provider([
        {"type": "error", "key": "coach.reply_failed"},
    ]))

    with app.test_request_context("/"):
        events = _drive(stream_env.id, "selam")
        conv_id = events[0]["conversation_id"]
        assert events[-1] == {
            "type": "error",
            "key": "coach.reply_failed",
            "work_performed": False,
            "partial_text": "",
        }
        # Hata çerçevesi hafızaya YAZILMAZ.
        assert CoachMessage.query.filter_by(conversation_id=conv_id).count() == 0

def test_stream_answer_processing_error_after_delta_persists_interruption(
        stream_env, app, bedrock_on, monkeypatch, caplog):
    def dispatch_boom(*args, **kwargs):
        raise RuntimeError("sensitive dispatch detail")

    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", dispatch_boom)
    bedrock_on(_FakeStream(
        ["K\u0131smi yan\u0131t"],
        _final(
            stop_reason="tool_use",
            content=[_tool_use_block("confirm_and_commit_meal_log", "t", {})],
        ),
    ))

    with app.test_request_context("/"):
        events = _drive(stream_env.id, "soru")
        conv_id = events[0]["conversation_id"]

        assert events[-1] == {
            "type": "error",
            "key": "coach.reply_failed",
            "work_performed": True,
            "partial_text": "K\u0131smi yan\u0131t",
        }
        rows = (CoachMessage.query.filter_by(conversation_id=conv_id)
                .order_by(CoachMessage.id.asc()).all())
        assert [(row.role, row.content) for row in rows] == [
            ("user", "soru"),
            ("assistant", "K\u0131smi yan\u0131t"),
        ]
        assert rows[1].interrupted is True

    assert "sensitive dispatch detail" not in caplog.text



def test_stream_answer_fallback_text_not_recorded(stream_env, app, monkeypatch):
    from app.services import ai_stream as _s
    from app.services.response_formatter import COACH_FALLBACKS
    fallback = COACH_FALLBACKS["tr"]["error"]
    monkeypatch.setattr(_s, "stream_coach_answer", _fake_provider([
        {"type": "done", "text": fallback, "usage": None},
    ]))

    with app.test_request_context("/"):
        events = _drive(stream_env.id, "selam")
        done = events[-1]
        assert done["is_error_fallback"] is True
        assert done["text"] == fallback
        conv_id = events[0]["conversation_id"]
        # B16: hata-yedeği turu kalıcılaşmaz (kota iadesiyle tutarlı).
        assert CoachMessage.query.filter_by(conversation_id=conv_id).count() == 0


def test_stream_answer_client_disconnect_persists_partial(stream_env, app, monkeypatch):
    from app.services import ai_stream as _s
    monkeypatch.setattr(_s, "stream_coach_answer", _fake_provider([
        {"type": "delta", "text": "mer"},
        {"type": "delta", "text": "haba"},
        {"type": "done", "text": "merhaba dünya", "usage": None},
    ]))

    with app.test_request_context("/"):
        gen = ai_pipeline.stream_answer(stream_env.id, "selam")
        meta = next(gen)
        conv_id = meta["conversation_id"]
        assert next(gen)["type"] == "delta"   # mer
        assert next(gen)["type"] == "delta"   # haba
        gen.close()                           # istemci koptu → GeneratorExit

        rows = (CoachMessage.query.filter_by(conversation_id=conv_id)
                .order_by(CoachMessage.id.asc()).all())
        assert [(r.role, r.content) for r in rows] == [
            ("user", "selam"), ("assistant", "merhaba")]
        assert rows[1].interrupted is True    # kesilen yanıt işaretlendi


def test_stream_answer_disconnect_before_delta_writes_nothing(
        stream_env, app, monkeypatch):
    from app.services import ai_stream as _s
    monkeypatch.setattr(_s, "stream_coach_answer", _fake_provider([
        {"type": "delta", "text": "x"},
        {"type": "done", "text": "x", "usage": None},
    ]))

    with app.test_request_context("/"):
        gen = ai_pipeline.stream_answer(stream_env.id, "selam")
        meta = next(gen)                      # yalnızca meta tüketildi
        conv_id = meta["conversation_id"]
        gen.close()

        # Hiç delta akmadı → kısmi yanıt yok → hiçbir mesaj yazılmaz.
        assert CoachMessage.query.filter_by(conversation_id=conv_id).count() == 0


def test_stream_answer_rejects_invalid_question(app, monkeypatch):
    called = {"provider": False}
    from app.services import ai_stream as _s
    monkeypatch.setattr(_s, "stream_coach_answer",
                        lambda *a, **k: called.update(provider=True) or iter(()))

    with app.test_request_context("/"):
        with pytest.raises(ValueError, match="coach.ask_something"):
            list(ai_pipeline.stream_answer(1, "   "))
        with pytest.raises(ValueError, match="coach.question_too_long"):
            list(ai_pipeline.stream_answer(1, "x" * 4001))
    assert called["provider"] is False


def test_stream_answer_memory_disabled_uses_client_history(app, monkeypatch):
    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    seen = {}
    from app.services import ai_stream as _s

    def capture(user_id, question, context, history, language="tr"):
        seen["history"] = history
        yield {"type": "done", "text": "cevap", "usage": None}
    monkeypatch.setattr(_s, "stream_coach_answer", capture)

    client_history = [{"role": "user", "text": "önceki"},
                      {"role": "bot", "text": "yanıt"}]
    with app.test_request_context("/"):
        events = _drive(1, "yeni soru", client_history=client_history)

    assert events[0] == {"type": "meta", "conversation_id": None}
    # Hafıza kapalı → sanitize edilmiş client history sağlayıcıya geçer.
    assert seen["history"] == [{"role": "user", "content": "önceki"},
                               {"role": "assistant", "content": "yanıt"}]


def test_stream_answer_drops_trailing_user_from_client_history(app, monkeypatch):
    # Widget soruyu geçmişin sonuna da iter; hafıza kapalıyken çift eklemeyi
    # önlemek için sondaki user turları düşürülür (B15 zeminini de temizler).
    app.config["AI_MEMORY_ENABLED"] = False
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda uid, q, language="tr": "")
    seen = {}
    from app.services import ai_stream as _s

    def capture(user_id, question, context, history, language="tr"):
        seen["history"] = history
        yield {"type": "done", "text": "cevap", "usage": None}
    monkeypatch.setattr(_s, "stream_coach_answer", capture)

    client_history = [{"role": "bot", "text": "önceki yanıt"},
                      {"role": "user", "text": "yeni soru"}]
    with app.test_request_context("/"):
        _drive(1, "yeni soru", client_history=client_history)

    assert seen["history"] == [{"role": "assistant", "content": "önceki yanıt"}]


# ── Triage 2026-07-19 #6: istemci kopunca üretici thread durmalı ────────────

def test_stream_bedrock_turn_cancels_producer_on_consumer_close():
    """Tüketici generator kapanınca (istemci koptu → GeneratorExit) üretici
    thread Bedrock akışını sürmeye devam ETMEMELİ: faturalanan token üretimi
    ve tutulan model slotu, giden kullanıcı için sürüp gidiyordu. İptal bayrağı
    text_stream döngüsünde görülür görülmez üretici çıkar ve stream context'i
    (dolayısıyla slot) kapanır."""
    produced = []
    consumer_closed = threading.Event()
    stream_exited = threading.Event()

    class _EndlessStream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            stream_exited.set()
            return False

        @property
        def text_stream(self):
            def gen():
                produced.append(1)
                yield "ilk"
                # Tüketici kapatana dek bekle — yarışsız, deterministik senaryo.
                consumer_closed.wait(5.0)
                for i in range(2, 1000):
                    produced.append(i)
                    yield f"parça{i}"
            return gen()

        def get_final_message(self):
            return SimpleNamespace(stop_reason="end_turn", content=[], usage=None)

    client = SimpleNamespace(stream=lambda **kw: _EndlessStream())

    gen = ai_stream._stream_bedrock_turn(client, {})
    assert next(gen) == {"kind": "delta", "text": "ilk"}
    gen.close()                # istemci koptu (GeneratorExit)
    consumer_closed.set()      # üretici devam etmeyi DENER — iptali görmeli

    assert stream_exited.wait(2.0), \
        "üretici thread iptali görmedi — Bedrock akışı açık kaldı"
    # İptal sonrası en fazla BİR parça daha çekilmiş olabilir (bayrak döngü
    # başında kontrol edilir); 999'a kadar tüketim = iptal yok demektir.
    assert len(produced) <= 2, f"üretici {len(produced)} parça tüketti (iptal yok)"
