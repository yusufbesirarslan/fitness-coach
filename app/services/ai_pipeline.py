# AI yanıt hattı orkestratörü (Sprint 4 WS3 + WS1).
#
#   Kullanıcı Mesajı
#     → moderation.validate_question   (girdi kapısı — model-dışı, deterministik)
#     → memory_manager                 (WS1: aktif konuşma + lazy özetleme +
#                                       rolling context window; arızada client
#                                       history fallback — sohbet asla kırılmaz)
#     → context_builder.fetch_coach_context
#     → ai_coach._run_coach_conversation
#         (içeride: prompt_builder.* montajı → Bedrock/Claude araç döngüsü,
#          OpenAI yedeği)
#     → response_formatter.finalize_reply  (hata-yedeği kararı + dostça metin)
#     → moderation.moderate_reply          (çıktı denetimi genişleme noktası)
#     → memory_manager.record_turn         (yalnızca GERÇEK yanıtlar — B16:
#                                           hata-yedeği hafızaya yazılmaz)
#
# /ask route'u bu fonksiyonu çağırır; kota rezervasyon/iade kararı route'ta
# kalır (dönen is_error_fallback bayrağıyla).
from flask import current_app

from app.services import context_builder, memory_manager, moderation, response_formatter


def generate_answer(user_id, question, client_history=None, language="tr"):
    """Koç sorusu için uçtan uca modüler hat.

    Dönüş: {"answer": str, "is_error_fallback": bool, "conversation_id": int|None}.
    Geçersiz girdi için ValueError(i18n-anahtarı) fırlatır — HTTP durum/çeviri
    kararı route'undur."""
    err_key = moderation.validate_question(question)
    if err_key:
        raise ValueError(err_key)

    # WS1 kalıcı hafıza: aktif konuşma + pencere. Her adım arızaya dayanıklı —
    # hafıza katmanı çökerse eski client-history davranışına düşülür.
    conversation = None
    prepared_history = None
    if current_app.config.get("AI_MEMORY_ENABLED", True):
        try:
            conversation = memory_manager.get_or_create_active_conversation(user_id)
            memory_manager.maybe_summarize(conversation)  # lazy; WS8'de RQ'ya taşınacak
            prepared_history = memory_manager.build_context_window(conversation)
        except Exception:
            # Commit yarıda kaldıysa session kirli kalır; rollback etmezsek
            # sonraki bağlam sorguları da patlar (hafıza arızası tüm yanıtı
            # düşürürdü). Rollback → temiz session → eski yola sorunsuz iniş.
            from app.extensions import db
            db.session.rollback()
            current_app.logger.warning("[PIPELINE] kalıcı hafıza kurulamadı — "
                                       "client history'ye düşülüyor", exc_info=True)
            conversation = None
            prepared_history = None

    # Bağlam sorgularında geçici DB arızası olsa bile function-calling akışı
    # (FatSecret + SQLAlchemy) bağımsız olarak çalışmaya devam eder.
    try:
        context = context_builder.fetch_coach_context(user_id, question, language=language)
    except Exception:
        current_app.logger.warning("[PIPELINE] koç bağlamı kurulamadı", exc_info=True)
        context = ""

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
    if conversation is not None and not is_fallback:
        try:
            memory_manager.record_turn(conversation, question, answer)
        except Exception:
            from app.extensions import db
            db.session.rollback()
            current_app.logger.warning("[PIPELINE] tur kalıcılaştırılamadı", exc_info=True)

    return {"answer": answer,
            "is_error_fallback": is_fallback,
            "conversation_id": conversation.id if conversation is not None else None}
