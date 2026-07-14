# AI yanıt hattı — Güvenlik Katmanı aşaması (Sprint 4 WS3).
# Girdi doğrulama (boyut/boşluk) + çıktı denetimi genişleme noktası. İçerik
# güvenliğinin asıl ağırlığı sistem promptu direktiflerinde ve bağlam
# fence'lemesinde (context_builder.neutralize_friend_content) yaşar; bu modül
# hattın deterministik, model-dışı kapısıdır.

# H2: Soru uzunluğu sınırı. tool_choice="auto" + 5'e kadar araç döngüsüyle her
# istek sistem promptu + bağlam + soruyu modele defalarca yeniden gönderebilir →
# token-maliyeti amplifikasyonu (özellikle pahalı Bedrock yolunda). Aşırı uzun
# girdi sessizce kırpılmaz, 400 ile reddedilir.
MAX_QUESTION_CHARS = 4000


def validate_question(question):
    """Geçerliyse None, değilse i18n hata anahtarı döndürür (route t() ile çevirir)."""
    if not (question or "").strip():
        return "coach.ask_something"
    if len(question) > MAX_QUESTION_CHARS:
        return "coach.question_too_long"
    return None


def moderate_reply(text):
    """Çıktı denetimi genişleme noktası: bugün geçirgen (no-op). Sağlayıcı ham
    hata metinleri buraya hiç ulaşmaz — response_formatter.finalize_reply onları
    dostça yedeğe çevirir; bu kanca gelecekteki içerik filtreleri içindir."""
    return text
