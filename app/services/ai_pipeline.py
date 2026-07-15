# AI yanıt hattı orkestratörü (Sprint 4 WS3 + WS1 + WS2).
#
#   Kullanıcı Mesajı
#     → moderation.validate_question   (girdi kapısı — model-dışı, deterministik)
#     → memory_manager                 (WS1: aktif konuşma + lazy özetleme +
#                                       rolling context window; arızada client
#                                       history fallback — sohbet asla kırılmaz)
#     → context_builder.fetch_coach_context
#     → sağlayıcı:
#         generate_answer  → ai_coach._run_coach_conversation   (bloklayıcı)
#         stream_answer    → ai_stream.stream_coach_answer       (WS2, akış)
#     → response_formatter.finalize_reply  (hata-yedeği kararı + dostça metin)
#     → moderation.moderate_reply          (çıktı denetimi genişleme noktası)
#     → memory_manager.record_turn         (yalnızca GERÇEK yanıtlar — B16:
#                                           hata-yedeği hafızaya yazılmaz)
#
# /ask ve /ask/stream route'ları bu iki fonksiyonu çağırır; kota rezervasyon/
# iade kararı route'ta kalır (is_error_fallback bayrağıyla).
from flask import current_app

from app.services import context_builder, memory_manager, moderation, response_formatter


def _maybe_enqueue_summarize(conversation):
    """WS8: özetlemeyi arka-plan işine ver — worker varsa kuyruğa (async, istek
    yolunu bloklamaz), yoksa SATIR-İÇİ (sync, PR2'nin eski davranışı). Kendi
    hatalarını yutar: özetleme tetiklenemezse bağlam penceresi yine kurulur."""
    try:
        from app.jobs import enqueue_or_run
        from app.jobs.tasks import summarize_conversation
        enqueue_or_run(summarize_conversation, conversation.id)
    except Exception:
        current_app.logger.warning("[PIPELINE] özetleme tetiklenemedi", exc_info=True)


def _memory_stage(user_id):
    """WS1 kalıcı hafıza: aktif konuşma + bağlam penceresi.

    Arızaya dayanıklı: hafıza katmanı çökerse (conversation=None,
    prepared_history=None) eski client-history davranışına düşülür."""
    if not current_app.config.get("AI_MEMORY_ENABLED", True):
        return None, None
    try:
        conversation = memory_manager.get_or_create_active_conversation(user_id)
        _maybe_enqueue_summarize(conversation)  # WS8: kuyruğa ya da satır-içi
        return conversation, memory_manager.build_context_window(conversation)
    except Exception:
        # Commit yarıda kaldıysa session kirli kalır; rollback etmezsek sonraki
        # bağlam sorguları da patlar (hafıza arızası TÜM yanıtı düşürürdü).
        from app.extensions import db
        db.session.rollback()
        current_app.logger.warning("[PIPELINE] kalıcı hafıza kurulamadı — "
                                   "client history'ye düşülüyor", exc_info=True)
        return None, None


def _context_stage(user_id, question, language):
    """Bağlam sorgularında geçici DB arızası olsa bile function-calling akışı
    (FatSecret + SQLAlchemy) bağımsız olarak çalışmaya devam eder."""
    try:
        return context_builder.fetch_coach_context(user_id, question, language=language)
    except Exception:
        current_app.logger.warning("[PIPELINE] koç bağlamı kurulamadı", exc_info=True)
        return ""


def _emit_metrics(is_error=False, usage=None):
    """WS6: bir AI turunun sonunda CloudWatch metriklerini yolla (kapalıysa no-op).
    Metrik yazımı asla akışı bozmaz — ai_metrics tüm hataları yutar."""
    try:
        from app.services import ai_metrics
        ai_metrics.increment("AIErrors" if is_error else "AITurn",
                             dimensions={"mode": "stream"})
        if usage:
            ai_metrics.record_tokens(usage.get("prompt_tokens"),
                                     usage.get("completion_tokens"),
                                     dimensions={"mode": "stream"})
    except Exception:
        pass  # gözlemlenebilirlik asla asıl yolu düşürmez


def _record(conversation, question, answer, usage=None, interrupted=False):
    """Turu kalıcılaştır — hata sohbeti/akışı BOZMAZ (yalnızca loglanır)."""
    if conversation is None:
        return
    try:
        memory_manager.record_turn(conversation, question, answer,
                                   usage=usage, interrupted=interrupted)
    except Exception:
        from app.extensions import db
        db.session.rollback()
        current_app.logger.warning("[PIPELINE] tur kalıcılaştırılamadı", exc_info=True)


def generate_answer(user_id, question, client_history=None, language="tr"):
    """Koç sorusu için uçtan uca modüler hat (bloklayıcı — /ask).

    Dönüş: {"answer": str, "is_error_fallback": bool, "conversation_id": int|None}.
    Geçersiz girdi için ValueError(i18n-anahtarı) fırlatır — HTTP durum/çeviri
    kararı route'undur."""
    err_key = moderation.validate_question(question)
    if err_key:
        raise ValueError(err_key)

    conversation, prepared_history = _memory_stage(user_id)
    context = _context_stage(user_id, question, language)

    # Çağrı-anı çözümleme (modül attribute'u üzerinden): testler ve gelecekteki
    # sağlayıcı değişimleri ai_coach._run_coach_conversation'ı patch'leyebilsin.
    from app.services import ai_coach
    answer = ai_coach._run_coach_conversation(
        user_id, question, context, client_history,
        language=language, prepared_history=prepared_history)

    answer, is_fallback = response_formatter.finalize_reply(answer, language)
    answer = moderation.moderate_reply(answer)

    # B16 disiplini hafızada da geçerli: hata-yedeği turları kalıcılaşmaz
    # (sonraki pencereye girip modeli kirletmesin, kota iadesiyle tutarlı).
    if not is_fallback:
        _record(conversation, question, answer)

    return {"answer": answer,
            "is_error_fallback": is_fallback,
            "conversation_id": conversation.id if conversation is not None else None}


def stream_answer(user_id, question, client_history=None, language="tr"):
    """Koç sorusu için AKIŞLI hat (WS2 — /ask/stream). Olay üreticisi.

    Ürettiği olaylar (route bunları SSE çerçevesine sarar):
      {"type": "meta",  "conversation_id": int|None}
      {"type": "delta", "text": "..."}
      {"type": "done",  "text": str, "is_error_fallback": bool, "usage": ...}
      {"type": "error", "key": "<i18n>"}   → sağlayıcı istisnası ASLA sızmaz

    İSTEMCİ KOPARSA (GeneratorExit): o ana dek akan kısmi yanıt `interrupted=True`
    ile kalıcılaştırılır — kullanıcı ekranında gördüğü metin hafızada da durur,
    bir sonraki tur onu bağlam olarak görür. Kısmi yanıt yoksa hiçbir şey yazılmaz.

    Geçersiz girdi için ValueError(i18n-anahtarı) fırlatır (route 400'e çevirir).
    """
    err_key = moderation.validate_question(question)
    if err_key:
        raise ValueError(err_key)

    conversation, prepared_history = _memory_stage(user_id)
    context = _context_stage(user_id, question, language)

    # Kalıcı hafıza penceresi varsa onu kullan; yoksa (hafıza kapalı/arızalı)
    # bloklayıcı yoldaki client-history davranışının aynısına düş.
    if prepared_history is not None:
        history = list(prepared_history)
    else:
        history = memory_manager.sanitize_client_history(client_history)
        while history and history[-1]["role"] == "user":
            history.pop()  # widget soruyu geçmişin sonuna da iter — çift eklemeyi önle

    yield {"type": "meta",
           "conversation_id": conversation.id if conversation is not None else None}

    from app.services import ai_stream
    parts = []
    finished = False
    try:
        for event in ai_stream.stream_coach_answer(
                user_id, question, context, history, language=language):
            kind = event.get("type")
            if kind == "delta":
                parts.append(event.get("text") or "")
                yield event
            elif kind == "error":
                finished = True
                _emit_metrics(is_error=True)
                yield event
                return
            elif kind == "done":
                finished = True
                answer, is_fallback = response_formatter.finalize_reply(
                    event.get("text"), language)
                answer = moderation.moderate_reply(answer)
                if not is_fallback:
                    _record(conversation, question, answer,
                            usage=event.get("usage"))
                _emit_metrics(is_error=is_fallback, usage=event.get("usage"))
                yield {"type": "done", "text": answer,
                       "is_error_fallback": is_fallback,
                       "usage": event.get("usage")}
                return
    except GeneratorExit:
        # İstemci bağlantıyı kopardı (sekme kapandı / Durdur'a basıldı). Buradan
        # SONRA yield ETMEK YASAK — yalnızca kısmi yanıtı kalıcılaştır ve çık.
        if not finished:
            partial = "".join(parts).strip()
            if partial:
                _record(conversation, question, partial, interrupted=True)
                current_app.logger.info(
                    "[PIPELINE][stream] istemci koptu — kısmi yanıt kaydedildi (%s kr)",
                    len(partial))
        raise
