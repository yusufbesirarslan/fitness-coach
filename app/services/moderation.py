# AI yanıt hattı — Güvenlik Katmanı aşaması (Sprint 4 WS3).
# Girdi doğrulama (boyut/boşluk) + çıktı denetimi genişleme noktası. İçerik
# güvenliğinin asıl ağırlığı sistem promptu direktiflerinde ve bağlam
# fence'lemesinde (context_builder.neutralize_friend_content) yaşar; bu modül
# hattın deterministik, model-dışı kapısıdır.

import re

# H2: Soru uzunluğu sınırı. tool_choice="auto" + 5'e kadar araç döngüsüyle her
# istek sistem promptu + bağlam + soruyu modele defalarca yeniden gönderebilir →
# token-maliyeti amplifikasyonu (özellikle pahalı Bedrock yolunda). Aşırı uzun
# girdi sessizce kırpılmaz, 400 ile reddedilir.
MAX_QUESTION_CHARS = 4000

# Exact internal vocabulary is a presentation failure, regardless of locale.
# Keep this narrow: semantic claims are governed by the public prompt policy,
# not rewritten with speculative regexes.
_INTERNAL_COACH_TERMS = re.compile(
    r"(?<![\w])(?:inconsistent_training|build_consistency|next_signal|"
    r"reason_codes|schema_version|load_consistency|week_focus|volume_action|"
    r"intensity_action|volume_delta_pct|overload_ready|deload_due|"
    r"maintenance_recommended|nudge[_ ]triggered|adaptiveplan(?: contract)?|"
    r"canonical contract)(?![\w])",
    re.IGNORECASE,
)


def leaks_internal_coach_term(text):
    """Detect clear machine vocabulary before publishing an adaptive reply."""
    return bool(_INTERNAL_COACH_TERMS.search(text or ""))


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
