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
