# AI yanıt hattı orkestratörü (Sprint 4 WS3).
#
#   Kullanıcı Mesajı
#     → moderation.validate_question   (girdi kapısı — model-dışı, deterministik)
#     → context_builder.fetch_coach_context
#     → ai_coach._run_coach_conversation
#         (içeride: memory_manager.sanitize_client_history → prompt_builder.*
#          → Bedrock/Claude araç döngüsü, OpenAI yedeği)
#     → response_formatter.finalize_reply  (hata-yedeği kararı + dostça metin)
#     → moderation.moderate_reply          (çıktı denetimi genişleme noktası)
#
# NOT: /ask route'u kota rezervasyonu aşamalar ARASINA girdiği için bugün aynı
# aşamaları kendi gövdesinde çağırıyor; WS1'de (kalıcı konuşma hafızası) route
# bu fonksiyona taşınacak. Bu fonksiyon hattın kanonik bileşimidir ve doğrudan
# birim-testlidir (tests/test_ai_pipeline.py).
from flask import current_app

from app.services import context_builder, moderation, response_formatter


def generate_answer(user_id, question, client_history=None, language="tr"):
    """Koç sorusu için uçtan uca modüler hat.

    Dönüş: {"answer": str, "is_error_fallback": bool}. Geçersiz girdi için
    ValueError(i18n-anahtarı) fırlatır — HTTP durum/çeviri kararı route'undur."""
    err_key = moderation.validate_question(question)
    if err_key:
        raise ValueError(err_key)

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
        user_id, question, context, client_history, language=language)

    answer, is_fallback = response_formatter.finalize_reply(answer, language)
    return {"answer": moderation.moderate_reply(answer),
            "is_error_fallback": is_fallback}
