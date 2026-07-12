"""Merkezi e-posta altyapısı (Resend) — Email Sprint 1.

Uygulamadan çıkan TÜM e-postalar bu servisten geçer; Resend SDK'sına başka
hiçbir modül dokunmaz. Konfigürasyon tek yerden gelir (app/config.py:
RESEND_API_KEY, EMAIL_FROM_NAME, EMAIL_FROM_ADDRESS, EMAIL_REPLY_TO) ve
gönderim domaini Resend'de doğrulanmıştır (mail.axisaiapp.com):

    From:     AxisAI <noreply@mail.axisaiapp.com>
    Reply-To: hello@axisaiapp.com

Hata sözleşmesi: gönderim hatası bir API isteğini ASLA çökertmemeli. Bu yüzden
public fonksiyonlar varsayılan olarak sessizce başarısız olur (yapısal log +
None döner); hataya tepki vermesi gereken çağıran `raise_on_error=True` ile
tipli `EmailServiceError` alır (S3Error/CognitoServiceError kalıbı). API
anahtarı hiçbir zaman loglanmaz.

Cognito KOD e-postaları (doğrulama/sıfırlama) bu servisten GEÇMEZ: kodları
Cognito üretir ve CustomEmailSender trigger'ı (infra/cognito-email-sender —
Lambda + Resend, Sprint 3) markalı gönderir. Bu servis uygulama-tetikli olay
e-postalarını taşır: hoş geldin ve şifren-değişti (app/blueprints/auth.py,
best-effort). Mimari: docs/auth-emails.md.

Yeni bir e-posta türü eklemek: şablonu app/services/email_templates.py'ye ekle
((subject, html, text) döndür), send_html_email(...) çağır — SDK, From/Reply-To
ve hata işleme burada kapsüllüdür. Alıcı adresleri loglara MASKELİ yazılır
(mask_email).
"""
import logging
from typing import List, Optional, Sequence, Union

from app.config import (EMAIL_ENABLED, EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME,
                        EMAIL_REPLY_TO, RESEND_API_KEY)
from app.timeutil import app_now

_logger = logging.getLogger(__name__)

Recipients = Union[str, Sequence[str]]


class EmailServiceError(Exception):
    """E-posta gönderimi başarısız olduğunda (yalnızca `raise_on_error=True`
    isteyen çağıranlara) yükseltilir. `message` güvenli/loglanabilir bir
    açıklamadır; `code` makine-okur hata sınıfıdır (örn. 'disabled',
    'validation_error', 'application_error') — ham sağlayıcı çıktısı ve API
    anahtarı asla taşınmaz."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code


def is_enabled() -> bool:
    """E-posta altyapısı kullanılabilir mi? (RESEND_API_KEY ayarlı mı?)

    Anahtar yoksa servis bilinçli olarak kapalıdır: gönderimler no-op olur
    (lokal/test ortamı ağa çıkmaz). Modül seviyesindeki EMAIL_ENABLED yerine
    bunu çağırın; testler bayrağı monkeypatch'leyebilir."""
    return EMAIL_ENABLED


def _get_resend():
    """Resend SDK modülünü tembel yükle ve API anahtarını bağla.

    Import burada (fonksiyon içinde) yapılır ki uygulama import'u hermetik
    kalsın (conftest kalıbı) ve paket eksikse yalnızca gönderim anında,
    sarılabilir bir hata üretsin. Testler bu fonksiyonu sahte modülle
    monkeypatch'ler (cognito_service._get_client kalıbı)."""
    import resend
    resend.api_key = RESEND_API_KEY
    return resend


def _sanitize(text: str) -> str:
    """Log/istisna metninden API anahtarını temizle (savunma katmanı —
    normalde sağlayıcı mesajlarında bulunmaz)."""
    if RESEND_API_KEY and RESEND_API_KEY in text:
        return text.replace(RESEND_API_KEY, "***")
    return text


def _wrap(exc: Exception) -> EmailServiceError:
    """SDK/ağ hatasını loglayıp tipli EmailServiceError'a çevir.

    Ham sağlayıcı istisnası dışarı sızdırılmaz; sınıf adı `code`, temizlenmiş
    özet `message` olur."""
    code = getattr(exc, "error_type", "") or type(exc).__name__
    detail = _sanitize(str(exc))
    _logger.warning("[EMAIL] gönderim hatası: %s: %s", type(exc).__name__, detail)
    return EmailServiceError(f"E-posta gönderilemedi ({code}).", code=code)


def _normalize_recipients(to: Recipients) -> List[str]:
    if isinstance(to, str):
        return [to] if to.strip() else []
    return [addr for addr in to if isinstance(addr, str) and addr.strip()]


def mask_email(addr) -> str:
    """PII maskesi: 'yusuf@example.com' → 'y***@example.com'.

    Loglara alıcı adresi HAM yazılmaz (KVKK/PII); ilk harf + domain teşhis için
    yeterlidir. E-posta olmayan/boş girdi '***' olur. Auth e-posta loglarında da
    kullanılır (app/blueprints/auth.py) — public tutulmasının nedeni bu."""
    if not isinstance(addr, str) or "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    return local[:1] + "***@" + domain


def _mask_recipients(recipients) -> List[str]:
    if isinstance(recipients, str):
        return [mask_email(recipients)]
    return [mask_email(addr) for addr in (recipients or [])]


def _deliver(params: dict, raise_on_error: bool) -> Optional[str]:
    """Ortak gönderim çekirdeği: enabled kontrolü, SDK çağrısı, hata sözleşmesi.

    Başarıda Resend mesaj id'sini döndürür. Başarısızlıkta yapısal log düşer ve
    None döner; `raise_on_error=True` ise EmailServiceError yükseltilir."""
    if not is_enabled():
        _logger.info("[EMAIL] gönderim atlandı (servis kapalı, RESEND_API_KEY yok): to=%s",
                     _mask_recipients(params.get("to")))
        if raise_on_error:
            raise EmailServiceError(
                "E-posta servisi kapalı (RESEND_API_KEY ayarlı değil).", code="disabled")
        return None
    if not params.get("to"):
        _logger.warning("[EMAIL] gönderim atlandı: alıcı yok (subject=%r)",
                        params.get("subject"))
        if raise_on_error:
            raise EmailServiceError("E-posta alıcısı belirtilmedi.", code="no_recipient")
        return None
    try:
        resend_mod = _get_resend()
        result = resend_mod.Emails.send(params)
    except Exception as exc:  # SDK/ağ/import — hiçbiri isteği çökertmemeli
        err = _wrap(exc)
        if raise_on_error:
            raise err
        return None
    message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    _logger.info("[EMAIL] gönderildi: id=%s to=%s subject=%r",
                 message_id, _mask_recipients(params["to"]), params.get("subject"))
    return message_id


def _base_params(to: Recipients, subject: str, reply_to: Optional[str]) -> dict:
    return {
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
        "to": _normalize_recipients(to),
        "subject": subject,
        "reply_to": reply_to or EMAIL_REPLY_TO,
    }


def send_html_email(to: Recipients, subject: str, html: str, *,
                    text: Optional[str] = None,
                    reply_to: Optional[str] = None,
                    raise_on_error: bool = False) -> Optional[str]:
    """HTML e-posta gönder (isteğe bağlı düz-metin alternatifiyle).

    Args:
        to: Alıcı adresi ya da adres listesi.
        subject: Konu satırı.
        html: HTML gövde.
        text: İsteğe bağlı düz-metin alternatifi (multipart).
        reply_to: Reply-To adresi; verilmezse EMAIL_REPLY_TO kullanılır.
        raise_on_error: True ise başarısızlıkta EmailServiceError yükseltilir;
            varsayılan (False) yalnızca loglar ve None döner — e-posta hatası
            bir API isteğini asla çökertmez.

    Returns:
        Resend mesaj id'si; başarısızlıkta (veya servis kapalıyken) None.
    """
    params = _base_params(to, subject, reply_to)
    params["html"] = html
    if text:
        params["text"] = text
    return _deliver(params, raise_on_error)


def send_text_email(to: Recipients, subject: str, text: str, *,
                    reply_to: Optional[str] = None,
                    raise_on_error: bool = False) -> Optional[str]:
    """Düz-metin e-posta gönder. Parametre/dönüş sözleşmesi send_html_email
    ile aynıdır; tek fark gövdenin yalnızca `text` olmasıdır."""
    params = _base_params(to, subject, reply_to)
    params["text"] = text
    return _deliver(params, raise_on_error)


def send_test_email(recipient: str) -> Optional[str]:
    """Altyapı doğrulama e-postası gönder (yalnızca geliştirme/doğrulama için).

    AxisAI markalı, sade bir onay mesajı + o anki zaman damgası (Istanbul,
    app/timeutil) içerir. İş mantığıyla ilişkisi yoktur; Resend entegrasyonunun
    uçtan uca çalıştığını göstermek için vardır.
    CLI: `flask --app starter send-test-email <alıcı>`

    Returns:
        Resend mesaj id'si; başarısızlıkta None (hata loglanır).
    """
    timestamp = app_now().strftime("%d.%m.%Y %H:%M:%S") + " (Europe/Istanbul)"
    subject = "AxisAI — e-posta altyapısı testi"
    text = (f"AxisAI\n\nBu bir test e-postasıdır: e-posta altyapısı çalışıyor.\n"
            f"Gönderim zamanı: {timestamp}\n")
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;'
        'margin:0 auto;padding:24px">'
        '<h2 style="color:#4f46e5;margin:0 0 16px">AxisAI</h2>'
        "<p>Bu bir test e-postasıdır: e-posta altyapısı çalışıyor.</p>"
        f'<p style="color:#6b7280;font-size:13px">Gönderim zamanı: {timestamp}</p>'
        "</div>"
    )
    return send_html_email(recipient, subject, html, text=text)
