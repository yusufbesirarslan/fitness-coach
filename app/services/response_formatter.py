# AI yanıt hattı — Biçimlendirici aşaması (Sprint 4 WS3; ai_coach.py'den taşındı).
# Sağlayıcı hata-yedeği metinlerinin tek kaynağı ve final yanıt kararı burada.
from app.prompts.system import coach_lang

# Sağlayıcı hatasında gösterilen dostça yedek metinler (dile göre).
COACH_FALLBACKS = {
    "tr": {"error": "Bir şeyler ters gitti, tekrar dener misin?",
           "tool": "İşlemi tamamlayamadım, tekrar dener misin?"},
    "en": {"error": "Something went wrong — could you try again?",
           "tool": "I couldn't complete that — could you try again?"},
}

_ALL_FALLBACKS = {t for d in COACH_FALLBACKS.values() for t in d.values()}


def error_fallback(language="tr"):
    return COACH_FALLBACKS[coach_lang(language)]["error"]


def tool_fallback(language="tr"):
    return COACH_FALLBACKS[coach_lang(language)]["tool"]


def is_coach_error_fallback(text):
    """Bir koç yanıtının sağlayıcı hata-yedeği olup olmadığını söyler. /ask bunu
    kullanıp HAFTALIK AI-chat kotasını yalnızca GERÇEK yanıtta tüketir; Bedrock+
    OpenAI ikisi de degrade olduğunda dönen 'Bir şeyler ters gitti' mesajı kredi
    yakmamalı (INF-4 — premium.py'deki status_code==200 disiplinini yansıtır)."""
    if not text:
        return True
    return text in _ALL_FALLBACKS


def finalize_reply(text, language="tr"):
    """(final_text, is_error_fallback) döndürür.

    C3: sağlayıcı döngülerinin döndürdüğü yedek metinler de hata-yedeğidir —
    boş metinle aynı muamele; aksi halde non-boş yedek geçmişe yazılıp sonraki
    turda bağlam olarak modele geri besleniyordu (B16'nın tamamlayıcısı)."""
    is_fallback = is_coach_error_fallback(text)
    if not text:
        text = error_fallback(language)
    return text, is_fallback
