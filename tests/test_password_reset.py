"""Şifre sıfırlama × e-posta katmanı (Resend Sprint 3) — route-level testler.

Akışın kendisi (jenerik yanıt, oturum bağlamı, tek-kullanımlık sıfırlama,
hata eşleme) tests/test_password_recovery.py'de sınanır. Buradaki testler
YALNIZCA e-posta bağlantısını sınar: başarıda best-effort "şifren değiştirildi"
bildirimi gider ve "e-posta hatası auth'u ASLA düşürmez" sözleşmesi tutar.

    python -m pytest tests/test_password_reset.py -v
"""
import time

import pytest

from app.services import cognito_service, email_service
from app.services.cognito_service import CognitoServiceError


@pytest.fixture
def reset_env(client, monkeypatch):
    """confirm_forgot_password'ı yakala ve geçerli sıfırlama bağlamı kur."""
    captured = {"confirm": []}

    def fake_confirm(username, code, new_password):
        captured["confirm"].append(
            {"username": username, "code": code, "password": new_password})

    monkeypatch.setattr(cognito_service, "confirm_forgot_password", fake_confirm)
    with client.session_transaction() as state:
        state["password_reset_username"] = "ali"
        state["password_reset_started_at"] = time.time()
    return captured


@pytest.fixture
def sent_emails(monkeypatch):
    """email_service.send_html_email'i kayıt tutan sahteyle değiştir."""
    calls = []

    def fake_send(to, subject, html, **kwargs):
        calls.append({"to": to, "subject": subject, "html": html, **kwargs})
        return "msg-1"

    monkeypatch.setattr(email_service, "send_html_email", fake_send)
    return calls


def _reset_payload():
    return {"code": "123456", "password": "YeniSifre1",
            "confirm_password": "YeniSifre1"}


def test_reset_password_happy_path_sends_notification(client, reset_env,
                                                      sent_emails, make_user):
    make_user("ali", email="ali@example.com")
    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 200
    assert reset_env["confirm"] == [
        {"username": "ali", "code": "123456", "password": "YeniSifre1"}]
    # Bilgilendirme e-postası gitti: doğru alıcı, kod İÇERMEYEN güvenlik maili.
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "ali@example.com"
    assert "şifren değiştirildi" in sent_emails[0]["subject"]
    assert sent_emails[0].get("text")  # düz-metin alternatifi eşlik eder


def test_reset_password_wrong_code_400_and_no_email(client, reset_env,
                                                    sent_emails, make_user,
                                                    monkeypatch):
    make_user("ali", email="ali@example.com")

    def boom(username, code, new_password):
        raise CognitoServiceError("Doğrulama kodu hatalı.", "CodeMismatchException")
    monkeypatch.setattr(cognito_service, "confirm_forgot_password", boom)

    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 400
    assert sent_emails == []  # başarısız sıfırlamada bildirim gitmez


def test_reset_password_email_failure_never_blocks(client, reset_env,
                                                   make_user, monkeypatch):
    # E-posta katmanı patlasa bile şifre Cognito'da değişti → 200 dönmeli.
    make_user("ali", email="ali@example.com")

    def boom(*args, **kwargs):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_service, "send_html_email", boom)

    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 200


def test_reset_password_resend_disabled_still_succeeds(client, reset_env, make_user):
    # conftest: RESEND_API_KEY boş → servis kapalı (no-op). Akış yine 200.
    make_user("ali", email="ali@example.com")
    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 200


def test_reset_password_no_local_user_no_email_no_crash(client, reset_env,
                                                        sent_emails):
    # Cognito'da var ama yerelde satır yok (edge) → e-posta atlanır, akış bozulmaz.
    response = client.post("/reset-password", json=_reset_payload())
    assert response.status_code == 200
    assert sent_emails == []
