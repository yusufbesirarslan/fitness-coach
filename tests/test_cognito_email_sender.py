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
