"""Cognito CustomEmailSender Lambda'sı (infra/cognito-email-sender) testleri.

Hermetik: KMS çözümü (handler._decrypt_code) ve Resend HTTP'si
(email_sender._post_json) monkeypatch'lenir — aws_encryption_sdk hiç import
edilmez (lazy import dikişi), ağa çıkılmaz. Sözleşmenin özü: handler HİÇBİR
KOŞULDA exception yükseltmez ve düz kod ASLA loglanmaz.

    python -m pytest tests/test_cognito_email_sender.py -v
"""
import logging
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parent.parent
           / "infra" / "cognito-email-sender" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import email_sender  # noqa: E402 — Lambda src'sinden
import handler  # noqa: E402

_CODE = "654321"
_KEY = "re_lambda_secret_key_do_not_log"


def _event(trigger, email="ali@example.com", name="Ali", code="ZW5jcnlwdGVk"):
    request = {"type": "customEmailSenderRequestV1", "code": code,
               "userAttributes": {}}
    if email is not None:
        request["userAttributes"]["email"] = email
    if name is not None:
        request["userAttributes"]["name"] = name
    return {"version": "1", "triggerSource": trigger, "userName": "ali",
            "request": request}


@pytest.fixture
def lambda_env(monkeypatch):
    """Şifre çözümünü ve Resend HTTP'sini sahtele; gönderimleri kaydet."""
    monkeypatch.setattr(handler, "_decrypt_code", lambda ciphertext: _CODE)
    monkeypatch.setattr(email_sender, "RESEND_API_KEY", _KEY)
    posts = []

    def fake_post(url, payload, headers, timeout=4):
        posts.append({"url": url, "payload": payload, "headers": headers})
        return {"id": "msg-lambda-1"}

    monkeypatch.setattr(email_sender, "_post_json", fake_post)
    return posts


# ── Trigger → şablon eşlemesi ───────────────────────────────────────────────

@pytest.mark.parametrize("trigger", [
    "CustomEmailSender_SignUp",
    "CustomEmailSender_ResendCode",
    "CustomEmailSender_VerifyUserAttribute",
])
def test_verification_triggers_send_verification_template(lambda_env, trigger):
    event = _event(trigger)
    assert handler.handler(event, None) is event  # olay her zaman geri döner
    assert len(lambda_env) == 1
    payload = lambda_env[0]["payload"]
    assert payload["to"] == ["ali@example.com"]
    assert "doğrulama kodun" in payload["subject"]
    assert _CODE in payload["html"]
    assert _CODE in payload["text"]
    assert _CODE not in payload["subject"]
    assert "reply_to" in payload


def test_forgot_password_sends_reset_template(lambda_env):
    handler.handler(_event("CustomEmailSender_ForgotPassword"), None)
    payload = lambda_env[0]["payload"]
    assert "sıfırlama kodun" in payload["subject"]
    assert _CODE in payload["html"]
    assert "şifren değişmedi" in payload["text"]


def test_auth_headers_and_user_agent(lambda_env):
    handler.handler(_event("CustomEmailSender_SignUp"), None)
    headers = lambda_env[0]["headers"]
    assert headers["Authorization"] == "Bearer %s" % _KEY
    # Not: özel User-Agent _post_json içinde eklenir (Cloudflare 403 koruması);
    # burada gerçek _post_json sahtelendiği için yalnızca auth başlığı görünür.


# ── Atlama durumları: gönderim yok ama YÜKSELTME de yok ─────────────────────

def test_unknown_trigger_skips_without_send(lambda_env):
    event = _event("CustomEmailSender_AccountTakeOverNotification")
    assert handler.handler(event, None) is event
    assert lambda_env == []


def test_missing_email_skips_without_send(lambda_env):
    event = _event("CustomEmailSender_SignUp", email=None)
    assert handler.handler(event, None) is event
    assert lambda_env == []


def test_missing_code_skips_without_send(lambda_env):
    event = _event("CustomEmailSender_SignUp", code=None)
    event["request"]["code"] = None
    assert handler.handler(event, None) is event
    assert lambda_env == []


# ── Asla-yükseltme sözleşmesi ───────────────────────────────────────────────

def test_decrypt_failure_never_raises(lambda_env, monkeypatch, caplog):
    def boom(ciphertext):
        raise RuntimeError("kms unavailable")
    monkeypatch.setattr(handler, "_decrypt_code", boom)

    event = _event("CustomEmailSender_SignUp")
    with caplog.at_level(logging.ERROR):
        assert handler.handler(event, None) is event  # Cognito akışı düşmez
    assert lambda_env == []
    assert "işlenemedi" in caplog.text


def test_sender_failure_never_raises(monkeypatch, lambda_env):
    def boom(url, payload, headers, timeout=4):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_sender, "_post_json", boom)

    event = _event("CustomEmailSender_SignUp")
    assert handler.handler(event, None) is event


def test_garbage_event_never_raises():
    assert handler.handler({}, None) == {}
    assert handler.handler(None, None) is None


# ── Log hijyeni: düz kod ve ham PII loglanmaz ───────────────────────────────

def test_plaintext_code_and_raw_email_never_logged(lambda_env, caplog):
    with caplog.at_level(logging.DEBUG):
        handler.handler(_event("CustomEmailSender_SignUp"), None)
    assert _CODE not in caplog.text                 # düz kod asla loglanmaz
    assert "ali@example.com" not in caplog.text     # ham alıcı loglanmaz
    assert "a***@example.com" in caplog.text        # maskeli alıcı loglanır
    assert _KEY not in caplog.text                  # API anahtarı loglanmaz


def test_sender_disabled_is_silent_noop(monkeypatch, caplog):
    monkeypatch.setattr(email_sender, "RESEND_API_KEY", "")
    monkeypatch.setattr(handler, "_decrypt_code", lambda ciphertext: _CODE)
    event = _event("CustomEmailSender_SignUp")
    with caplog.at_level(logging.INFO):
        assert handler.handler(event, None) is event
    assert "atlandı" in caplog.text


# ---------------------------------------------------------------------------
# H5 — GERÇEK şifre-çözme yolu (_decrypt_code) ARTIK test ediliyor.
#
# Bu dosyadaki DİĞER her test _decrypt_code'u monkeypatch'liyor; sonuç olarak
# gerçek AWS Encryption SDK yolu — ve özellikle Cognito'nun ciphertext'inin
# ZORUNLU kıldığı FORBID_ENCRYPT_ALLOW_DECRYPT commitment policy'si — CI'da HİÇ
# çalışmıyordu. SDK'nın varsayılanı REQUIRE_ENCRYPT_REQUIRE_DECRYPT'tir ve
# Cognito'nun (key commitment OLMADAN şifrelenmiş) kodunu REDDEDER. Yani bir
# bağımlılık yükseltmesi ya da masum bir refactor bu politikayı düşürseydi 1331
# testin hepsi yeşil kalır, PROD'DA HESAP OLUŞTURMA KIRILIRDI.
#
# Gerçek fonksiyon gövdesini, sys.modules'a enjekte edilen sahte bir SDK ile
# çalıştırıyoruz: ağ yok, KMS yok, ama sözleşme (politika + key id + base64)
# gerçekten sınanıyor.
# ---------------------------------------------------------------------------

import base64  # noqa: E402
import types  # noqa: E402

_KMS_ARN = "arn:aws:kms:eu-central-1:123456789012:key/abc-123"


class _FakeCommitmentPolicy:
    FORBID_ENCRYPT_ALLOW_DECRYPT = "FORBID_ENCRYPT_ALLOW_DECRYPT"
    REQUIRE_ENCRYPT_REQUIRE_DECRYPT = "REQUIRE_ENCRYPT_REQUIRE_DECRYPT"  # SDK varsayılanı


@pytest.fixture
def fake_sdk(monkeypatch):
    """aws_encryption_sdk'yi sys.modules'a enjekte et; GERÇEK _decrypt_code koşsun."""
    seen = {}

    class _FakeClient:
        def __init__(self, commitment_policy=None):
            seen["commitment_policy"] = commitment_policy

        def decrypt(self, source=None, key_provider=None):
            seen["source"] = source
            seen["key_provider"] = key_provider
            return b"424242", {"header": "fake"}

    def _fake_key_provider(key_ids=None):
        seen["key_ids"] = key_ids
        return "key-provider"

    module = types.ModuleType("aws_encryption_sdk")
    module.EncryptionSDKClient = _FakeClient
    module.CommitmentPolicy = _FakeCommitmentPolicy
    module.StrictAwsKmsMasterKeyProvider = _fake_key_provider

    monkeypatch.setitem(sys.modules, "aws_encryption_sdk", module)
    monkeypatch.setenv("KMS_KEY_ARN", _KMS_ARN)
    return seen


def test_real_decrypt_uses_forbid_encrypt_allow_decrypt_policy(fake_sdk):
    """Cognito, kodu key commitment OLMADAN şifreler. SDK'nın varsayılan
    REQUIRE_ENCRYPT_REQUIRE_DECRYPT politikası bu ciphertext'i REDDEDER —
    FORBID_ENCRYPT_ALLOW_DECRYPT zorunludur. Regresyon kapısı."""
    handler._decrypt_code(base64.b64encode(b"ciphertext").decode())

    assert fake_sdk["commitment_policy"] == \
        _FakeCommitmentPolicy.FORBID_ENCRYPT_ALLOW_DECRYPT
    assert fake_sdk["commitment_policy"] != \
        _FakeCommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT


def test_real_decrypt_scopes_key_provider_to_configured_kms_key(fake_sdk):
    handler._decrypt_code(base64.b64encode(b"ciphertext").decode())
    assert fake_sdk["key_ids"] == [_KMS_ARN]


def test_real_decrypt_base64_decodes_ciphertext_and_returns_plaintext(fake_sdk):
    """Cognito kodu base64 taşır; SDK'ya HAM bayt verilmeli."""
    result = handler._decrypt_code(base64.b64encode(b"ciphertext").decode())
    assert fake_sdk["source"] == b"ciphertext"
    assert result == "424242"


def test_handler_end_to_end_through_real_decrypt(fake_sdk, monkeypatch):
    """_decrypt_code monkeypatch'siz TAM akış: olay → gerçek çözme dikişi →
    şablon → Resend. Şifre çözme yolu artık handler sözleşmesine dahil."""
    monkeypatch.setattr(email_sender, "RESEND_API_KEY", _KEY)
    posts = []
    monkeypatch.setattr(email_sender, "_post_json",
                        lambda url, payload, headers, timeout=4:
                        posts.append(payload) or {"id": "msg-real-1"})

    event = _event("CustomEmailSender_SignUp",
                   code=base64.b64encode(b"ciphertext").decode())
    assert handler.handler(event, None) is event

    assert len(posts) == 1
    assert "424242" in posts[0]["html"]  # çözülen kod e-postaya girdi


def test_handler_still_never_raises_when_real_decrypt_fails(monkeypatch, caplog):
    """Sözleşme korunuyor: gerçek SDK yolu patlasa BİLE handler yükseltmez —
    aksi halde Cognito SignUp cagrisi kullanıcıya hata dönerdi."""
    broken = types.ModuleType("aws_encryption_sdk")

    class _Boom:
        def __init__(self, commitment_policy=None):
            raise RuntimeError("SDK bozuk")

    broken.EncryptionSDKClient = _Boom
    broken.CommitmentPolicy = _FakeCommitmentPolicy
    broken.StrictAwsKmsMasterKeyProvider = lambda key_ids=None: None
    monkeypatch.setitem(sys.modules, "aws_encryption_sdk", broken)
    monkeypatch.setenv("KMS_KEY_ARN", _KMS_ARN)

    event = _event("CustomEmailSender_SignUp",
                   code=base64.b64encode(b"ciphertext").decode())
    with caplog.at_level(logging.ERROR):
        assert handler.handler(event, None) is event
    # ...ama SESSİZ de kalmaz: bu satır CloudWatch metric filter'ının ([ERROR])
    # yakaladığı ve alarma bağlandığı satırdır (H5).
    assert "[EMAIL-SENDER]" in caplog.text
