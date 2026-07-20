# AI koç akış (streaming) katmanı — Sprint 4 WS2.
#
# Neden ayrı modül: ai_coach'taki bloklayıcı döngüler AYNEN durur (/ask hâlâ
# onları kullanır — kanıtlanmış yol bozulmasın). Burada aynı araç ekosistemi
# (_dispatch_coach_tool, _COACH_TOOL_LOOP_CAP, tool tanımları) akış semantiğiyle
# yeniden koşulur.
#
# Üretilen olaylar (dict) — SSE çerçeveleme route'un işi:
#   {"type": "delta", "text": "..."}    → istemciye anında yazılacak parça
#   {"type": "done",  "text": tam metin, "usage": {...}|None, "provider": "..."}
#   {"type": "error", "key": "coach.reply_failed"}  → i18n ANAHTARI; sağlayıcı
#                                        istisna metni İSTEMCİYE ASLA sızmaz
#
# Sağlayıcı seçimi ve yedek kuralı (B-kuralı: yan etki tekrarlanmaz):
#   - İlk delta İSTEMCİYE GİTTİKTEN ya da bir araç YAN ETKİ ürettikten sonra
#     sağlayıcı DEĞİŞTİRİLMEZ. Bedrock akışı daha ilk token'dan önce (ve hiç araç
#     çalışmadan) patlarsa OpenAI yedeğine şeffafça düşülür; sonrasında patlarsa
#     dostça hata olayı gönderilir.
#   - OpenAI yedeği GERÇEK akış değildir: mevcut (araç-döngülü, doğruluğu
#     kanıtlanmış) bloklayıcı loop koşar, sonucu parçalara bölünüp akıtılır.
#     İlk-token gecikmesi kazandırmaz; UX'i ve tek bir olay sözleşmesini korur.
#     Asıl akış yolu Bedrock'tır (prod'da BEDROCK_ENABLED=1).
import json
import queue
import threading

from flask import current_app

from app.config import BEDROCK_MAX_TOKENS, BEDROCK_MODEL
from app.services import prompt_builder
from app.services.ai_gate import model_concurrency_slot

# Akış yokken metni sahte-akıtırken kullanılan parça boyutu (karakter).
CHUNK_CHARS = 48

# Bedrock akışında ilk token'dan önce hata olursa OpenAI'ya düşülür.
_COACH_MAX_TOKENS = 700


def _usage_of(message):
    """Sağlayıcı yanıtındaki gerçek token kullanımı (varsa) — WS6 muhasebesi."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    prompt_tokens = getattr(usage, "input_tokens", None)
    completion_tokens = getattr(usage, "output_tokens", None)
    if prompt_tokens is None and completion_tokens is None:
        return None
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


def _chunks(text):
    for i in range(0, len(text), CHUNK_CHARS):
        yield text[i:i + CHUNK_CHARS]


def _bedrock_work_error(parts, tools_ran):
    return {
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": bool(parts or tools_ran),
        "partial_text": "".join(parts).strip(),
    }



def _stream_bedrock_turn(messages_client, call_kwargs):
    messages = queue.SimpleQueue()
    # Triage 2026-07-19 #6: tüketici (istemci) kopunca üretici thread Bedrock
    # akışını doğal bitimine dek sürüyordu — giden kullanıcı için faturalanan
    # token üretimi + rehin model slotu. Bayrak text_stream döngüsünde görülür
    # görülmez üretici çıkar; stream context'i ve slot hemen kapanır.
    cancelled = threading.Event()

    def produce():
        try:
            with model_concurrency_slot():
                with messages_client.stream(**call_kwargs) as stream:
                    for text in stream.text_stream:
                        if cancelled.is_set():
                            return
                        if text:
                            messages.put({"kind": "delta", "text": text})
                    if cancelled.is_set():
                        return
                    final = stream.get_final_message()
            messages.put({"kind": "final", "message": final})
        except Exception as exc:
            messages.put({"kind": "exception", "exception": exc})

    threading.Thread(target=produce, daemon=True).start()
    try:
        while True:
            message = messages.get()
            yield message
            if message["kind"] in {"final", "exception"}:
                return
    finally:
        cancelled.set()


def stream_coach_answer(user_id, question, context, history, language="tr"):
    """Koç yanıtını akış olarak üret. Olay üreticisi (generator).

    ai_coach'ın araç ekosistemini kullanır; sağlayıcı yedeği yalnızca HİÇBİR
    delta gönderilmemişken mümkündür."""
    from app.services import ai_coach

    ai_coach._begin_coach_turn()  # S1: tur-içi stage→confirm kilidini sıfırla

    if ai_coach.BEDROCK_ENABLED and ai_coach._anthropic is not None:
        try:
            yield from _stream_bedrock(user_id, question, context, history, language)
            return
        except ai_coach._BedrockFallback:
            # Buraya YALNIZCA hiç delta gitmemişken ve hiç araç çalışmamışken
            # gelinir (_stream_bedrock garantisi) → sağlayıcı değişimi güvenli.
            current_app.logger.warning(
                "[COACH][stream] Bedrock failed before work; trying OpenAI fallback")

    yield from _stream_openai_fallback(user_id, question, context, history, language)


def _stream_bedrock(user_id, question, context, history, language):
    """Bedrock (Anthropic Messages) akışlı araç-kullanım döngüsü.

    Her tur AKITILIR: model araç çağırmadan önce bir giriş cümlesi yazarsa
    ("Öğünlerine bakıyorum...") kullanıcı onu anında görür; araç turları
    çalışır ve final tur cevabı aynı akışta devam eder. Yayınlanan tüm metin
    parçaları birleşip kalıcılaşan yanıtı oluşturur."""
    from app.services import ai_coach

    system = ai_coach._build_bedrock_system(context, language)
    tools = ai_coach._anthropic_tools_for_call()
    convo = prompt_builder.build_anthropic_messages(history, question)
    max_tokens = min(_COACH_MAX_TOKENS, BEDROCK_MAX_TOKENS)

    parts = []
    tools_ran = 0
    usage = None
    deadline = ai_coach._coach_turn_deadline()

    for _ in range(ai_coach._COACH_TOOL_LOOP_CAP):
        remaining = ai_coach._remaining_coach_turn_seconds(deadline)
        if remaining <= 0:
            current_app.logger.warning(
                "[COACH][stream] Bedrock turn budget exhausted")
            yield from _emit_text(ai_coach._COACH_FALLBACKS[
                ai_coach._coach_lang(language)]["tool"],
                provider="bedrock", usage=usage)
            return
        call_kwargs = {
            "model": BEDROCK_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": convo,
            "tools": tools,
            "timeout": min(ai_coach.BEDROCK_CALL_TIMEOUT_SECONDS, remaining),
        }
        try:
            for message in _stream_bedrock_turn(
                    ai_coach.bedrock_client.messages, call_kwargs):
                if message["kind"] == "delta":
                    text = message["text"]
                    parts.append(text)
                    yield {"type": "delta", "text": text}
                elif message["kind"] == "final":
                    final = message["message"]
                else:
                    raise message["exception"]
        except Exception as e:
            if ai_coach._remaining_coach_turn_seconds(deadline) <= 0:
                current_app.logger.warning(
                    "[COACH][stream] Bedrock turn budget exhausted during provider call")
                yield from _emit_text(ai_coach._COACH_FALLBACKS[
                    ai_coach._coach_lang(language)]["tool"],
                    provider="bedrock", usage=usage)
                return
            # Yedek YALNIZCA hiçbir şey yayınlanmamış VE hiçbir araç yan etki
            # üretmemişken güvenlidir; aksi halde sağlayıcı değiştirmek
            # kullanıcının gördüğü metni bozar / yan etkiyi tekrarlar.
            if not parts and tools_ran == 0:
                raise ai_coach._BedrockFallback(f"{type(e).__name__}: {e}")
            current_app.logger.warning(
                "[COACH][stream] Bedrock stream failed after work")
            yield _bedrock_work_error(parts, tools_ran)
            return

        try:
            usage = _usage_of(final) or usage

            if getattr(final, "stop_reason", None) != "tool_use":
                text = "".join(parts)
                if not text and tools_ran == 0:
                    # Hiç metin yok ve hiç araç çalışmadı → soru cevaplanabilirdi,
                    # OpenAI'ya düş (bloklayıcı yoldaki B4 mantığının aynısı).
                    raise ai_coach._BedrockFallback("boş Bedrock akışı (metin yok)")
                yield {"type": "done", "text": text, "usage": usage,
                       "provider": "bedrock"}
                return

            convo.append({"role": "assistant", "content": final.content})
            tool_results = []
            for block in final.content:
                if getattr(block, "type", None) == "tool_use":
                    out = ai_coach._dispatch_coach_tool(
                        user_id, block.name, json.dumps(block.input or {}))
                    tools_ran += 1
                    tool_results.append({"type": "tool_result",
                                         "tool_use_id": block.id, "content": out})
            convo.append({"role": "user", "content": tool_results})
        except Exception as e:
            if not parts and tools_ran == 0:
                raise ai_coach._BedrockFallback(f"{type(e).__name__}: {e}")
            current_app.logger.warning(
                "[COACH][stream] Bedrock response processing failed after work")
            yield _bedrock_work_error(parts, tools_ran)
            return

    # Araç döngüsü tavana dayandı — dostça yedek metni akıt (yanıtsız bırakma).
    yield from _emit_text(ai_coach._COACH_FALLBACKS[
        ai_coach._coach_lang(language)]["tool"], provider="bedrock", usage=usage)


def _stream_openai_fallback(user_id, question, context, history, language):
    """OpenAI yedeği: kanıtlanmış BLOKLAYICI araç döngüsünü koşar, sonucu akıtır.

    Gerçek token akışı değildir (ilk-token kazancı yok) — amaç tek olay
    sözleşmesini ve UX'i korumak. Araç çağrılarının streaming'de birikimli
    ayrıştırılması ayrı bir doğruluk riski; yedek yolda bu riski almıyoruz."""
    from app.services import ai_coach

    try:
        text = ai_coach._run_coach_conversation_openai(
            user_id, question, context, history, language)
    except Exception:
        current_app.logger.warning("[COACH][stream] OpenAI fallback failed before work")
        yield {
            "type": "error",
            "key": "coach.reply_failed",
            "work_performed": False,
            "partial_text": "",
        }
        return

    yield from _emit_text(text or "", provider="openai", usage=None)


def _emit_text(text, provider, usage=None):
    for chunk in _chunks(text):
        yield {"type": "delta", "text": chunk}
    yield {"type": "done", "text": text, "usage": usage, "provider": provider}
