"""app/services/email_service.py — merkezi Resend e-posta altyapısı testleri.

Hermetik: gerçek SDK/ağ yok. _get_resend, kayıt tutan sahte bir modülle
monkeypatch'lenir (test_cognito_idp._FakeIdpClient kalıbı). conftest.py
RESEND_API_KEY'i boşalttığından servis varsayılan KAPALI başlar; açık senaryolar
bayrakları testin kendi içinde patch'ler.
"""
import logging

import pytest

from app.services import email_service
from app.services.email_service import EmailServiceError

_TEST_KEY = "re_test_secret_key_do_not_log"


class _FakeResend:
    """`resend` modülünü taklit eder: Emails.send parametrelerini kaydeder,
    istenirse hata fırlatır."""

    api_key = None

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = {"id": "msg-123"} if result is None else result
        self._raises = raises
        fake = self

        class Emails:
            @staticmethod
            def send(params):
                fake.calls.append(params)
                if fake._raises:
                    raise fake._raises
                return fake._result

        self.Emails = Emails


def _use_fake(monkeypatch, fake):
    """Servisi 'açık' yap ve SDK yerine sahteyi bağla."""
    monkeypatch.setattr(email_service, "EMAIL_ENABLED", True)
    monkeypatch.setattr(email_service, "RESEND_API_KEY", _TEST_KEY)
    monkeypatch.setattr(email_service, "_get_resend", lambda: fake)


def _forbid_sdk(monkeypatch):
    """SDK'ya hiç dokunulmaması gereken senaryolar için nöbetçi."""
    def _boom():
        raise AssertionError("_get_resend çağrılmamalıydı")
    monkeypatch.setattr(email_service, "_get_resend", _boom)


# ── Mutlu yol: parametre kompozisyonu ───────────────────────────────────────

def test_send_html_email_composes_params(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    message_id = email_service.send_html_email(
        "kisi@example.com", "Merhaba", "<b>selam</b>")

    assert message_id == "msg-123"
    assert len(fake.calls) == 1
    params = fake.calls[0]
    assert params["from"] == (
        f"{email_service.EMAIL_FROM_NAME} <{email_service.EMAIL_FROM_ADDRESS}>")
    assert params["to"] == ["kisi@example.com"]  # str → list normalize
    assert params["subject"] == "Merhaba"
    assert params["html"] == "<b>selam</b>"
    assert params["reply_to"] == email_service.EMAIL_REPLY_TO
    assert "text" not in params


def test_send_html_email_with_text_alternative(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    email_service.send_html_email(
        "kisi@example.com", "Konu", "<p>html</p>", text="düz metin")

    assert fake.calls[0]["html"] == "<p>html</p>"
    assert fake.calls[0]["text"] == "düz metin"


def test_send_text_email_has_no_html(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    message_id = email_service.send_text_email(
        "kisi@example.com", "Konu", "sadece metin")

    assert message_id == "msg-123"
    assert fake.calls[0]["text"] == "sadece metin"
    assert "html" not in fake.calls[0]


def test_reply_to_override_wins(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    email_service.send_text_email(
        "kisi@example.com", "Konu", "m", reply_to="destek@axisaiapp.com")

    assert fake.calls[0]["reply_to"] == "destek@axisaiapp.com"


def test_recipient_list_passthrough(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    email_service.send_text_email(
        ["a@example.com", "b@example.com"], "Konu", "m")

    assert fake.calls[0]["to"] == ["a@example.com", "b@example.com"]


# ── Servis kapalı (RESEND_API_KEY yok) ──────────────────────────────────────

def test_disabled_returns_none_without_sdk_call(monkeypatch):
    _forbid_sdk(monkeypatch)  # conftest: anahtar boş → EMAIL_ENABLED False

    assert email_service.is_enabled() is False
    assert email_service.send_html_email("a@b.c", "Konu", "<p>x</p>") is None


def test_disabled_raise_on_error(monkeypatch):
    _forbid_sdk(monkeypatch)

    with pytest.raises(EmailServiceError) as exc_info:
        email_service.send_text_email("a@b.c", "Konu", "m", raise_on_error=True)
    assert exc_info.value.code == "disabled"


# ── Sağlayıcı hatası: graceful + anahtar sızmaz ─────────────────────────────

def test_provider_error_is_graceful_and_key_never_logged(monkeypatch, caplog):
    fake = _FakeResend(raises=RuntimeError(f"boom token={_TEST_KEY}"))
    _use_fake(monkeypatch, fake)

    with caplog.at_level(logging.WARNING, logger="app.services.email_service"):
        result = email_service.send_html_email("a@b.c", "Konu", "<p>x</p>")

    assert result is None  # istek çökmedi
    assert _TEST_KEY not in caplog.text  # API anahtarı loglanmaz
    assert "gönderim hatası" in caplog.text


def test_provider_error_raise_on_error_wraps(monkeypatch):
    fake = _FakeResend(raises=RuntimeError("boom"))
    _use_fake(monkeypatch, fake)

    with pytest.raises(EmailServiceError) as exc_info:
        email_service.send_html_email(
            "a@b.c", "Konu", "<p>x</p>", raise_on_error=True)
    assert exc_info.value.code == "RuntimeError"
    assert "boom" not in exc_info.value.message  # ham sağlayıcı çıktısı sızmaz


def test_empty_recipient_is_graceful(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    assert email_service.send_text_email("", "Konu", "m") is None
    assert email_service.send_text_email([], "Konu", "m") is None
    assert fake.calls == []
    with pytest.raises(EmailServiceError) as exc_info:
        email_service.send_text_email("  ", "Konu", "m", raise_on_error=True)
    assert exc_info.value.code == "no_recipient"


# ── PII maskesi: alıcı adresi loglara ham yazılmaz ──────────────────────────

def test_mask_email_cases():
    assert email_service.mask_email("yusuf@example.com") == "y***@example.com"
    assert email_service.mask_email("a@b.co") == "a***@b.co"
    assert email_service.mask_email("@example.com") == "***@example.com"
    assert email_service.mask_email("not-an-email") == "***"
    assert email_service.mask_email("") == "***"
    assert email_service.mask_email(None) == "***"


def test_delivery_log_masks_recipient(monkeypatch, caplog):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    with caplog.at_level(logging.INFO, logger="app.services.email_service"):
        email_service.send_html_email("yusuf@example.com", "Konu", "<p>x</p>")

    assert "y***@example.com" in caplog.text
    assert "yusuf@example.com" not in caplog.text  # ham adres loglanmaz
    # SDK parametreleri elbette gerçek adresi taşır (maskeleme yalnızca logda).
    assert fake.calls[0]["to"] == ["yusuf@example.com"]


def test_disabled_skip_log_masks_recipient(monkeypatch, caplog):
    _forbid_sdk(monkeypatch)  # conftest: anahtar boş → servis kapalı

    with caplog.at_level(logging.INFO, logger="app.services.email_service"):
        email_service.send_html_email("yusuf@example.com", "Konu", "<p>x</p>")

    assert "yusuf@example.com" not in caplog.text


# ── send_test_email (altyapı doğrulama) ─────────────────────────────────────

def test_send_test_email_branding_and_timestamp(monkeypatch):
    fake = _FakeResend()
    _use_fake(monkeypatch, fake)

    message_id = email_service.send_test_email("dev@example.com")

    assert message_id == "msg-123"
    params = fake.calls[0]
    assert params["to"] == ["dev@example.com"]
    assert "AxisAI" in params["subject"]
    assert "AxisAI" in params["html"]
    assert "Gönderim zamanı" in params["html"]
    assert "Europe/Istanbul" in params["text"]
