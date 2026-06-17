"""Integration tests for the auth blueprint (app/blueprints/auth.py).

Kayıt/giriş/çıkış akışını ve sertleştirmeleri sabitler: hesap-numaralandırma
koruması (kullanıcı adı ve e-posta çakışmasında TEK jenerik mesaj), var olmayan
kullanıcı ile yanlış şifrenin ayırt edilememesi, logout CSRF kapısı ve
rate-limit 429 yanıtı.

    python -m pytest tests/test_auth.py -v
"""
from app.extensions import limiter
from app.models import User


def _register(client, username="yeniuser", email=None, password="Sifre123"):
    return client.post("/register", json={
        "username": username,
        "email": email or f"{username}@example.com",
        "password": password,
    })


# ---------------------------------------------------------------------------
# Kayıt
# ---------------------------------------------------------------------------

def test_register_creates_user_with_hashed_password(client):
    response = _register(client, "yeniuser")
    assert response.status_code == 200
    assert "yeniuser" in response.get_json()["message"]

    user = User.query.filter_by(username="yeniuser").one()
    assert user.email == "yeniuser@example.com"
    assert user.password_hash != "Sifre123"
    assert user.check_password("Sifre123")


def test_register_missing_fields_rejected(client):
    response = client.post("/register", json={"username": "x"})
    assert response.status_code == 400
    assert "zorunlu" in response.get_json()["error"]


def test_register_validation_errors_rejected(client):
    assert _register(client, password="kisa1").status_code == 400          # zayıf şifre
    assert _register(client, "ab").status_code == 400                       # kısa kullanıcı adı
    assert _register(client, email="gecersiz-eposta").status_code == 400


def test_register_collision_gives_single_generic_message(client, make_user):
    # Numaralandırma koruması: kullanıcı adı çakışması ile e-posta çakışması
    # AYNI mesajı döndürmeli; mesaj hangisinin kayıtlı olduğunu ele vermemeli.
    make_user("mevcut", email="mevcut@example.com")

    same_username = _register(client, "mevcut", email="bambaska@example.com")
    same_email = _register(client, "bambaska", email="mevcut@example.com")

    assert same_username.status_code == 400
    assert same_email.status_code == 400
    assert same_username.get_json()["error"] == same_email.get_json()["error"]


def test_registered_user_can_login(client):
    _register(client, "roundtrip")
    response = client.post("/login", json={"username": "roundtrip", "password": "Sifre123"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Giriş
# ---------------------------------------------------------------------------

def test_login_success_establishes_session(client, make_user):
    make_user("alice")
    response = client.post("/login", json={"username": "alice", "password": "Sifre123"})
    assert response.status_code == 200
    # Oturum kuruldu → login_required sayfa açılır.
    assert client.get("/supplements").status_code == 200


def test_login_wrong_password_and_unknown_user_look_identical(client, make_user):
    # Timing-eşitleme yanında mesaj da aynı olmalı: kayıtlı kullanıcıyı ele verme.
    make_user("bob")
    wrong_pw = client.post("/login", json={"username": "bob", "password": "Yanlis999"})
    no_user = client.post("/login", json={"username": "kimseyok", "password": "Yanlis999"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.get_json()["error"] == no_user.get_json()["error"]


def test_protected_route_redirects_anonymous_to_login(client):
    response = client.get("/supplements")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_username_rate_key_normalized():
    # Dağıtık brute-force kapısı kullanıcı adına göre anahtarlanır (IP'ye değil).
    from flask import Flask
    from app.blueprints.auth import _login_username_key
    app = Flask(__name__)
    with app.test_request_context("/login", method="POST", json={"username": "  Alice "}):
        assert _login_username_key() == "login-user:alice"
    with app.test_request_context("/login", method="POST", json={},
                                  environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert _login_username_key() == "10.0.0.9"  # kullanıcı adı yoksa IP'ye düş


# ---------------------------------------------------------------------------
# Çıkış — GET /logout, cross-site tetiklemeye kapalı olmalı.
# ---------------------------------------------------------------------------

def _login_as(client, make_user, username):
    make_user(username)
    client.post("/login", json={"username": username, "password": "Sifre123"})


def test_logout_cross_site_rejected_and_session_kept(client, make_user):
    _login_as(client, make_user, "carol")
    response = client.get("/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert client.get("/supplements").status_code == 200  # hâlâ oturumda


def test_logout_same_origin_signs_out(client, make_user):
    _login_as(client, make_user, "dave")
    response = client.get("/logout", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 302
    assert client.get("/supplements").status_code == 302  # oturum kapandı


def test_logout_cross_site_referer_fallback_rejected(client, make_user):
    # Sec-Fetch-Site göndermeyen eski tarayıcı yolu: Referer kontrolü devrede.
    _login_as(client, make_user, "eve")
    response = client.get("/logout", headers={"Referer": "http://evil.example/"})
    assert response.status_code == 403


def test_logout_without_any_headers_allowed(client, make_user):
    # Başlıksız doğrudan navigasyon (adres çubuğu) çalışmaya devam etmeli.
    _login_as(client, make_user, "frank")
    assert client.get("/logout").status_code == 302


# ---------------------------------------------------------------------------
# Native Amazon Cognito kayıt/doğrulama/giriş akışı (COGNITO_ENABLED True iken).
# Cognito ağ çağrıları (cognito_idp) tamamen mock'lanır — hermetik kalır.
# ---------------------------------------------------------------------------

import pytest

from app.blueprints import auth as auth_bp
from app.services import cognito_idp
from app.services.cognito_idp import CognitoIdpError


@pytest.fixture
def cognito_native(monkeypatch):
    """COGNITO_ENABLED'ı aç ve cognito_idp çağrılarını yakala. Yakalanan
    argümanları (özellikle Cognito'ya geçen `name`) sınama için kaydeder."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    captured = {"confirmed": set()}

    def fake_sign_up(username, password, email, name):
        captured["sign_up"] = {"username": username, "email": email, "name": name}
        return f"sub-{username}"

    def fake_confirm(username, code):
        captured["confirm"] = {"username": username, "code": code}
        captured["confirmed"].add(username)

    def fake_initiate(username, password):
        # Doğrulanmamışsa (henüz confirm edilmedi) Cognito gibi davran.
        if username not in captured["confirmed"]:
            raise CognitoIdpError("E-postan henüz doğrulanmadı.", "UserNotConfirmedException")
        return {"sub": f"sub-{username}", "email": f"{username}@example.com",
                "email_verified": True, "name": username}

    monkeypatch.setattr(cognito_idp, "sign_up", fake_sign_up)
    monkeypatch.setattr(cognito_idp, "confirm_sign_up", fake_confirm)
    monkeypatch.setattr(cognito_idp, "initiate_auth", fake_initiate)
    return captured


def test_cognito_register_passes_name_and_requires_verification(client, cognito_native):
    response = _register(client, "cognitouser")
    assert response.status_code == 200
    body = response.get_json()
    assert body["needs_verification"] is True
    assert body["username"] == "cognitouser"
    # name parametresi Cognito'ya GEÇMELİ (havuzun zorunlu attribute'u olabilir).
    assert cognito_native["sign_up"]["name"] == "cognitouser"

    # Yerel kayıt cognito_sub ile oluşmalı; parola hash'i kullanılamaz olmalı.
    user = User.query.filter_by(username="cognitouser").one()
    assert user.cognito_sub == "sub-cognitouser"
    assert not user.check_password("Sifre123")


def test_cognito_login_before_verify_redirects_to_verification(client, cognito_native):
    _register(client, "unverified")
    response = client.post("/login", json={"username": "unverified", "password": "Sifre123"})
    assert response.status_code == 403
    body = response.get_json()
    assert body["needs_verification"] is True
    assert body["username"] == "unverified"


def test_cognito_verify_then_login_succeeds(client, cognito_native):
    _register(client, "verifyme")

    confirm = client.post("/verify", json={"username": "verifyme", "code": "123456"})
    assert confirm.status_code == 200
    assert cognito_native["confirm"]["code"] == "123456"

    response = client.post("/login", json={"username": "verifyme", "password": "Sifre123"})
    assert response.status_code == 200
    assert client.get("/supplements").status_code == 200  # oturum kuruldu


def test_cognito_verify_resend(client, cognito_native, monkeypatch):
    _register(client, "resendme")
    sent = {}
    monkeypatch.setattr(cognito_idp, "resend_code", lambda username: sent.update(u=username))
    response = client.post("/verify/resend", json={"username": "resendme"})
    assert response.status_code == 200
    assert sent["u"] == "resendme"


def test_verify_routes_404_when_cognito_disabled(client):
    # COGNITO_ENABLED False (varsayılan) → doğrulama uçları kapalı.
    assert client.get("/verify?u=x").status_code == 404
    assert client.post("/verify", json={"username": "x", "code": "1"}).status_code == 404


# ---------------------------------------------------------------------------
# Rate limit — 5/saat kayıt limiti ve Türkçe 429 yanıtı.
# ---------------------------------------------------------------------------

def test_register_rate_limit_returns_429_json(client):
    limiter.reset()
    limiter.enabled = True
    try:
        # Zayıf şifreli ucuz istekler: doğrulama 400'ü de limite sayılır.
        for _ in range(5):
            assert _register(client, password="kisa1").status_code == 400
        blocked = _register(client, password="kisa1")
        assert blocked.status_code == 429
        assert "Çok fazla deneme" in blocked.get_json()["error"]
    finally:
        limiter.enabled = False
        limiter.reset()
