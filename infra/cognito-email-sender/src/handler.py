"""Cognito CustomEmailSender Lambda — markalı auth e-postaları (Resend Sprint 3).

Kullanıcı havuzuna CustomEmailSender trigger'ı bağlandığında Cognito kendi
e-postasını GÖNDERMEZ; bunun yerine bu Lambda'yı çağırır. Kod (doğrulama /
sıfırlama) olayda AWS Encryption SDK ile KMS_KEY_ARN altında ŞİFRELİ gelir:
burada çözülür, markalı şablona (email_templates — Flask'taki
app/services/email_templates.py'nin bayt-bayt kopyası) yerleştirilir ve
Resend üzerinden gönderilir.

SÖZLEŞME: handler ASLA exception yükseltmez. Yükseltirse Cognito SignUp /
ForgotPassword çağrısı kullanıcıya hata döner — "auth, e-posta yüzünden asla
başarısız olmaz" kuralının Lambda ayağı budur. Her hata loglanır ve yutulur.

Loglama: trigger + MASKELİ alıcı + Resend id. Düz kod, ham e-posta, attribute
dökümü veya KMS ciphertext'i ASLA loglanmaz.
"""
import base64
import logging
import os

import email_sender
import email_templates

# Agir bagimliligi INIT asamasinda yukle: INIT tam CPU boost'uyla calisir ve
# suresi invoke timeout'una sayilmaz. Flask test venv'inde paket kurulu degil —
# ImportError yutulur; _decrypt_code icindeki lazy import test seam'i olarak
# kalir (testler zaten _decrypt_code'u monkeypatch'ler).
try:
    import aws_encryption_sdk as _preloaded_aws_encryption_sdk  # noqa: F401
except ImportError:
    _preloaded_aws_encryption_sdk = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# triggerSource → şablon türü. Listede olmayan trigger'lar (örn.
# AccountTakeOverNotification, AdminCreateUser geçici şifresi) bilinçli
# atlanır: şifre/geçici-parola taşıyan olaylar için şablon tasarlanmadan
# e-posta üretmek sessiz veri sızıntısı riskidir.
TRIGGER_TEMPLATES = {
    "CustomEmailSender_SignUp": "verification",
    "CustomEmailSender_ResendCode": "verification",
    "CustomEmailSender_VerifyUserAttribute": "verification",
    "CustomEmailSender_UpdateUserAttribute": "verification",
    "CustomEmailSender_ForgotPassword": "reset",
}


def _decrypt_code(b64_ciphertext):
    """Cognito'nun KMS_KEY_ARN altında şifrelediği kodu çöz.

    Lazy import: aws_encryption_sdk yalnızca Lambda paketinde kurulu — Flask
    test ortamı handler'ı bu bağımlılık olmadan import edebilsin (testler bu
    fonksiyonu monkeypatch'ler).

    Commitment policy notu: Cognito, kodu key commitment OLMADAN şifreler;
    SDK'nın varsayılan REQUIRE_ENCRYPT_REQUIRE_DECRYPT politikası bu
    ciphertext'i reddeder → FORBID_ENCRYPT_ALLOW_DECRYPT zorunludur."""
    import aws_encryption_sdk
    from aws_encryption_sdk import CommitmentPolicy

    client = aws_encryption_sdk.EncryptionSDKClient(
        commitment_policy=CommitmentPolicy.FORBID_ENCRYPT_ALLOW_DECRYPT)
    key_provider = aws_encryption_sdk.StrictAwsKmsMasterKeyProvider(
        key_ids=[os.environ["KMS_KEY_ARN"]])
    plaintext, _header = client.decrypt(
        source=base64.b64decode(b64_ciphertext), key_provider=key_provider)
    return plaintext.decode("utf-8")


def _handle(event):
    trigger = event.get("triggerSource", "")
    template = TRIGGER_TEMPLATES.get(trigger)
    if template is None:
        logger.info("[EMAIL-SENDER] desteklenmeyen trigger, atlandı: %s", trigger)
        return

    request = event.get("request") or {}
    attrs = request.get("userAttributes") or {}
    email = (attrs.get("email") or "").strip()
    if not email:
        logger.warning("[EMAIL-SENDER] alıcı e-posta yok, atlandı (trigger=%s)", trigger)
        return

    encrypted = request.get("code")
    if not encrypted:
        logger.warning("[EMAIL-SENDER] şifreli kod yok, atlandı (trigger=%s)", trigger)
        return
    code = _decrypt_code(encrypted)

    name = (attrs.get("name") or event.get("userName") or "").strip()
    if template == "verification":
        subject, html, text = email_templates.verification_code_email(name, code)
    else:
        subject, html, text = email_templates.reset_code_email(name, code)

    message_id = email_sender.send_html_email(email, subject, html, text=text)
    logger.info("[EMAIL-SENDER] trigger=%s to=%s id=%s",
                trigger, email_sender.mask_email(email), message_id)


def handler(event, context):
    """Lambda giriş noktası. Olayı işler ve HER DURUMDA olayı geri döndürür."""
    try:
        _handle(event)
    except Exception:  # noqa: BLE001 — bkz. modül docstring'i: asla yükseltme
        logger.exception("[EMAIL-SENDER] işlenemedi (trigger=%s)",
                         (event or {}).get("triggerSource"))
    return event
