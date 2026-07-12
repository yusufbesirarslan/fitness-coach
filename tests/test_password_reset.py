"""Şifre sıfırlama akışı (Resend Sprint 3) — route-level testler.

/forgot-password ve /reset-password uçları: Cognito sarmalayıcıları tamamen
mock'lanır (hermetik), jenerik enumeration yanıtı, throttle dürüstlüğü,
best-effort bilgilendirme e-postası ve "e-posta hatası auth'u ASLA düşürmez"
sözleşmesi sınanır.

    python -m pytest tests/test_password_reset.py -v
"""
import pytest

from app.blueprints import auth as auth_bp
from app.extensions import limiter
from app.services import cognito_service, email_service
from app.services.cognito_service import CognitoServiceError


@pytest.fixture
def reset_env(monkeypatch):
    """COGNITO_ENABLED'ı aç, forgot/confirm sarmalayıcılarını yakala."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    captured = {"forgot": [], "confirm": []}

    def fake_forgot(username):
        captured["forgot"].append(username)

    def fake_confirm(username, code, new_password):
        captured["confirm"].append(
            {"username": username, "code": code, "password": new_password})

    monkeypatch.setattr(cognito_service, "forgot_password", fake_forgot)
    monkeypatch.setattr(cognito_service, "confirm_forgot_password", fake_confirm)
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


# ── Sayfalar ────────────────────────────────────────────────────────────────

def test_pages_render_with_prefill(client, reset_env):
    page = client.get("/forgot-password?u=ali")
    assert page.status_code == 200
    assert 'value="ali"' in page.get_data(as_text=True)

    page = client.get("/reset-password?u=ali")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'value="ali"' in body
    assert "password-meter" in body           # şifre güç göstergesi
    assert 'autocomplete="one-time-code"' in body


def test_routes_404_when_cognito_disabled(client):
    # COGNITO_ENABLED False (conftest varsayılanı) → uçlar kapalı.
    assert client.get("/forgot-password").status_code == 404
    assert client.get("/reset-password").status_code == 404
    assert client.post("/forgot-password", json={"username": "x"}).status_code == 404
    assert client.post("/reset-password",
                       json={"username": "x", "code": "1", "password": "Sifre123"}).status_code == 404


# ── /forgot-password: jenerik yanıt + enumeration koruması ──────────────────

def test_forgot_password_success_generic_message(client, reset_env):
    response = client.post("/forgot-password", json={"username": "ali"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["username"] == "ali"
    assert "sıfırlama kodu gönderildi" in body["message"]
    assert reset_env["forgot"] == ["ali"]


def test_forgot_password_unknown_user_same_generic_response(client, reset_env, monkeypatch):
    def boom(username):
        raise CognitoServiceError("Kullanıcı adı veya şifre hatalı.", "UserNotFoundException")
    monkeypatch.setattr(cognito_service, "forgot_password", boom)

    unknown = client.post("/forgot-password", json={"username": "hayaletkisi"})
    known = client.post("/forgot-password", json={"username": "hayaletkisi"})
    # Hesap yokken de yanıt başarıyla AYNI olmalı (enumeration koruması)...
    assert unknown.status_code == 200
    # ...gövde de: yalnızca status değil mesaj da sızdırmamalı.
    monkeypatch.setattr(cognito_service, "forgot_password", lambda username: None)
    ok = client.post("/forgot-password", json={"username": "hayaletkisi"})
    assert unknown.get_json() == ok.get_json() == known.get_json()


def test_forgot_password_unmapped_error_still_generic(client, reset_env, monkeypatch):
    def boom(username):
        raise CognitoServiceError("İşlem başarısız. Lütfen tekrar dene.", "SomeWeirdException")
    monkeypatch.setattr(cognito_service, "forgot_password", boom)
    response = client.post("/forgot-password", json={"username": "ali"})
    assert response.status_code == 200
    assert "sıfırlama kodu gönderildi" in response.get_json()["message"]


def test_forgot_password_throttle_is_honest_429(client, reset_env, monkeypatch):
    def boom(username):
        raise CognitoServiceError("Çok fazla deneme. Lütfen biraz sonra tekrar dene.",
                                  "LimitExceededException")
    monkeypatch.setattr(cognito_service, "forgot_password", boom)
    response = client.post("/forgot-password", json={"username": "ali"})
    assert response.status_code == 429
    assert "Çok fazla deneme" in response.get_json()["error"]


def test_forgot_password_missing_username_400(client, reset_env):
    response = client.post("/forgot-password", json={})
    assert response.status_code == 400
    assert reset_env["forgot"] == []


def test_forgot_password_rate_limit_429(client, reset_env):
    limiter.reset()
    limiter.enabled = True
    try:
        for _ in range(3):
            assert client.post("/forgot-password",
                               json={"username": "ali"}).status_code == 200
        blocked = client.post("/forgot-password", json={"username": "ali"})
        assert blocked.status_code == 429
    finally:
        limiter.enabled = False
        limiter.reset()


# ── /reset-password: doğrulama + bilgilendirme e-postası ────────────────────

def test_reset_password_happy_path_sends_notification(client, reset_env,
                                                      sent_emails, make_user):
    make_user("ali", email="ali@example.com")
    response = client.post("/reset-password", json={
        "username": "ali", "code": "123456", "password": "YeniSifre1"})
    assert response.status_code == 200
    assert "güncellendi" in response.get_json()["message"]
    assert reset_env["confirm"] == [
        {"username": "ali", "code": "123456", "password": "YeniSifre1"}]
    # Bilgilendirme e-postası gitti: doğru alıcı, kod İÇERMEYEN güvenlik maili.
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "ali@example.com"
    assert "şifren değiştirildi" in sent_emails[0]["subject"]
    assert sent_emails[0].get("text")  # düz-metin alternatifi eşlik eder


def test_reset_password_wrong_code_400_and_no_email(client, reset_env,
                                                    sent_emails, make_user, monkeypatch):
    make_user("ali", email="ali@example.com")

    def boom(username, code, new_password):
        raise CognitoServiceError("Doğrulama kodu hatalı.", "CodeMismatchException")
    monkeypatch.setattr(cognito_service, "confirm_forgot_password", boom)

    response = client.post("/reset-password", json={
        "username": "ali", "code": "000000", "password": "YeniSifre1"})
    assert response.status_code == 400
    assert "kodu hatalı" in response.get_json()["error"]
    assert sent_emails == []  # başarısız sıfırlamada bildirim gitmez


def test_reset_password_weak_password_rejected_before_cognito(client, reset_env):
    response = client.post("/reset-password", json={
        "username": "ali", "code": "123456", "password": "kisa"})
    assert response.status_code == 400
    assert reset_env["confirm"] == []  # Cognito'ya hiç gidilmedi


def test_reset_password_missing_fields_400(client, reset_env):
    for payload in ({}, {"username": "ali"}, {"username": "ali", "code": "1"},
                    {"code": "1", "password": "YeniSifre1"}):
        assert client.post("/reset-password", json=payload).status_code == 400
    assert reset_env["confirm"] == []


def test_reset_password_email_failure_never_blocks(client, reset_env,
                                                   make_user, monkeypatch):
    # E-posta katmanı patlasa bile şifre Cognito'da değişti → 200 dönmeli.
    make_user("ali", email="ali@example.com")

    def boom(*args, **kwargs):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_service, "send_html_email", boom)

    response = client.post("/reset-password", json={
        "username": "ali", "code": "123456", "password": "YeniSifre1"})
    assert response.status_code == 200


def test_reset_password_resend_disabled_still_succeeds(client, reset_env, make_user):
    # conftest: RESEND_API_KEY boş → servis kapalı (no-op). Akış yine 200.
    make_user("ali", email="ali@example.com")
    response = client.post("/reset-password", json={
        "username": "ali", "code": "123456", "password": "YeniSifre1"})
    assert response.status_code == 200


def test_reset_password_no_local_user_still_succeeds(client, reset_env, sent_emails):
    # Cognito'da var ama yerelde satır yok (edge) → e-posta atlanır, akış bozulmaz.
    response = client.post("/reset-password", json={
        "username": "yerelsiz", "code": "123456", "password": "YeniSifre1"})
    assert response.status_code == 200
    assert sent_emails == []
