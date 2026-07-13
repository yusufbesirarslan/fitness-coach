"""Resend e-posta gönderimi — Cognito CustomEmailSender Lambda'sı için.

Flask uygulamasındaki app/services/email_service.py sözleşmesinin Lambda
uyarlaması (landing repo'daki urllib taşıyıcısıyla aynı desen):
- graceful hata: gönderim loglar ve None döner — e-posta hatası Cognito
  akışını (SignUp/ForgotPassword) ASLA düşürmemeli
- is_enabled() RESEND_API_KEY'e bakar; anahtar yoksa gönderim sessiz no-op
- API anahtarı hiçbir zaman loglanmaz; alıcı adresi maskeli loglanır (PII)

Taşıyıcı urllib ile düz HTTPS POST — Lambda paketine tek uç nokta için SDK
eklemeye değmez. Testler _post_json'ı monkeypatch'ler.
"""
import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "AxisAI")
EMAIL_FROM_ADDRESS = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@mail.axisaiapp.com")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO", "hello@axisaiapp.com")


def is_enabled():
    """E-posta altyapısı kullanılabilir mi (RESEND_API_KEY ayarlı mı)?"""
    return bool(RESEND_API_KEY)


def mask_email(addr):
    """PII maskesi: 'yusuf@example.com' → 'y***@example.com' (loglar için)."""
    if not isinstance(addr, str) or "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    return local[:1] + "***@" + domain


def _sanitize(text):
    """Log/istisna metninden API anahtarını temizle (savunma katmanı)."""
    if RESEND_API_KEY and RESEND_API_KEY in text:
        return text.replace(RESEND_API_KEY, "***")
    return text


def _post_json(url, payload, headers, timeout=4):
    """Küçük urllib POST shim'i — testlerin monkeypatch'lediği dikiş.

    Özel User-Agent ZORUNLU: api.resend.com önündeki Cloudflare, urllib'in
    varsayılan 'Python-urllib/x.y' agent'ını 403 ile engeller. Timeout, Lambda'nın
    invoke bütçesinin (template.yaml Timeout=20s; import + KMS decrypt + Resend
    TLS bu bütçeyi paylaşır) rahatça altında kalacak şekilde kısadır."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "axisai-cognito-email-sender/1.0", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_html_email(to, subject, html, text=None, reply_to=None):
    """HTML e-posta gönder (isteğe bağlı düz-metin alternatifiyle).

    Başarıda Resend mesaj id'sini döndürür; servis kapalıysa, alıcı yoksa veya
    gönderim başarısızsa None döner (hata loglanır, ASLA yükseltilmez).
    """
    if not is_enabled():
        logger.info("[EMAIL] gönderim atlandı (servis kapalı, RESEND_API_KEY yok): to=%s",
                    mask_email(to))
        return None
    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [r for r in recipients if isinstance(r, str) and r.strip()]
    if not recipients:
        logger.warning("[EMAIL] gönderim atlandı: alıcı yok (subject=%r)", subject)
        return None
    payload = {
        "from": "%s <%s>" % (EMAIL_FROM_NAME, EMAIL_FROM_ADDRESS),
        "to": recipients,
        "subject": subject,
        "html": html,
        "reply_to": reply_to or EMAIL_REPLY_TO,
    }
    if text:
        payload["text"] = text
    try:
        result = _post_json(RESEND_API_URL, payload,
                            {"Authorization": "Bearer %s" % RESEND_API_KEY})
    except Exception as exc:  # noqa: BLE001 — e-posta Cognito akışını düşüremez
        detail = str(exc)
        if callable(getattr(exc, "read", None)):  # HTTPError: gövde nedeni söyler
            try:
                detail += " body=" + exc.read(512).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — loglama da yükseltemez
                pass
        logger.warning("[EMAIL] gönderim hatası: %s: %s",
                       type(exc).__name__, _sanitize(detail))
        return None
    message_id = result.get("id") if isinstance(result, dict) else None
    logger.info("[EMAIL] gönderildi: id=%s to=%s subject=%r",
                message_id, [mask_email(r) for r in recipients], subject)
    return message_id
