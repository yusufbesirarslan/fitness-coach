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

def test_register_creates_cognito_user_without_local_password(client, cognito_native):
    response = _register(client, "yeniuser")
    assert response.status_code == 200
    assert response.get_json()["needs_verification"] is True

    user = User.query.filter_by(username="yeniuser").one()
    assert user.email == "yeniuser@example.com"
    assert user.cognito_sub == "sub-yeniuser"
    assert user.password_hash is None


def test_register_missing_fields_rejected(client, cognito_native):
    response = client.post("/register", json={"username": "x"})
    assert response.status_code == 400
    assert "zorunlu" in response.get_json()["error"]


def test_register_validation_errors_rejected(client, cognito_native):
    assert _register(client, password="kisa1").status_code == 400          # zayıf şifre
    assert _register(client, "ab").status_code == 400                       # kısa kullanıcı adı
    assert _register(client, email="gecersiz-eposta").status_code == 400


def test_register_collision_gives_single_generic_message(client, make_user, cognito_native):
    # Numaralandırma koruması: kullanıcı adı çakışması ile e-posta çakışması
    # AYNI mesajı döndürmeli; mesaj hangisinin kayıtlı olduğunu ele vermemeli.
    make_user("mevcut", email="mevcut@example.com")

    same_username = _register(client, "mevcut", email="bambaska@example.com")
    same_email = _register(client, "bambaska", email="mevcut@example.com")

    assert same_username.status_code == 400
    assert same_email.status_code == 400
    assert same_username.get_json()["error"] == same_email.get_json()["error"]


def test_register_normalizes_email_and_blocks_case_variant_duplicates(
        client, cognito_native):
    # E-posta kırpılıp küçük harfe indirilerek saklanmalı; aynı adresin farklı
    # büyük/küçük yazımıyla ikinci kayıt çakışma (jenerik "taken") almalı.
    response = _register(client, "epostauser", email="  Yeni.User@EXAMPLE.Com ")
    assert response.status_code == 200

    user = User.query.filter_by(username="epostauser").one()
    assert user.email == "yeni.user@example.com"

    duplicate = _register(client, "baskauser", email="YENI.USER@example.COM")
    assert duplicate.status_code == 400


def test_registered_user_can_login(client, cognito_native):
    _register(client, "roundtrip")
    client.post("/verify", json={"username": "roundtrip", "code": "123456"})
    response = client.post("/login", json={"username": "roundtrip", "password": "Sifre123"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Giriş
# ---------------------------------------------------------------------------

def test_login_success_establishes_session(client, make_user, login):
    make_user("alice")
    response = login("alice")
    assert response.status_code == 200
    # Oturum kuruldu → login_required sayfa açılır.
    assert client.get("/supplements").status_code == 200


def test_login_wrong_password_and_unknown_user_look_identical(
        client, make_user, cognito_native, monkeypatch):
    # Cognito'nun sabit yetkisiz yanıtı, yerel hesabın varlığını ele vermemeli.
    make_user("bob")
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: (_ for _ in ()).throw(
        CognitoServiceError("Kullanıcı adı veya şifre hatalı.", "NotAuthorizedException")))
    wrong_pw = client.post("/login", json={"username": "bob", "password": "Yanlis999"})
    no_user = client.post("/login", json={"username": "kimseyok", "password": "Yanlis999"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.get_json()["error"] == no_user.get_json()["error"]


def test_protected_route_redirects_anonymous_to_login(client):
    response = client.get("/supplements")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_fail_closed_when_redis_down(client, make_user, login, monkeypatch):
    """Redis (dağıtık brute-force throttle) erişilemiyorsa login 503 döner."""
    import app.extensions as ext

    class _DownRedis:
        def ping(self):
            raise ConnectionError("redis down")

    make_user("carol")
    monkeypatch.setattr(ext, "redis_client", _DownRedis())
    ext._LOGIN_THROTTLE_HEALTH["checked_at"] = 0.0   # cache'i sıfırla

    blocked = client.post("/login", json={"username": "carol", "password": "Sifre123"})
    assert blocked.status_code == 503
    assert "geçici" in blocked.get_json()["error"].lower()

    # Redis geri gelince login normal çalışır.
    monkeypatch.setattr(ext, "redis_client", None)
    ext._LOGIN_THROTTLE_HEALTH["checked_at"] = 0.0
    ok = client.post("/login", json={"username": "carol", "password": "Sifre123"})
    assert ok.status_code == 200


def test_login_fail_closed_disabled_by_config(client, make_user, login, monkeypatch):
    """LOGIN_FAIL_CLOSED kapalıysa Redis düşse bile eski fail-open davranış sürer."""
    import app.extensions as ext

    class _DownRedis:
        def ping(self):
            raise ConnectionError("redis down")

    make_user("dave")
    monkeypatch.setattr(ext, "redis_client", _DownRedis())
    ext._LOGIN_THROTTLE_HEALTH["checked_at"] = 0.0
    client.application.config["LOGIN_FAIL_CLOSED"] = False
    try:
        ok = client.post("/login", json={"username": "dave", "password": "Sifre123"})
        assert ok.status_code == 200
    finally:
        client.application.config["LOGIN_FAIL_CLOSED"] = True


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

def _login_as(client, make_user, login, username):
    make_user(username)
    assert login(username).status_code == 200


def test_logout_cross_site_rejected_and_session_kept(client, make_user, login):
    _login_as(client, make_user, login, "carol")
    response = client.get("/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert client.get("/supplements").status_code == 200  # hâlâ oturumda


def test_logout_same_origin_signs_out(client, make_user, login):
    _login_as(client, make_user, login, "dave")
    response = client.get("/logout", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 302
    assert client.get("/supplements").status_code == 302  # oturum kapandı


def test_logout_cross_site_referer_fallback_rejected(client, make_user, login):
    # Sec-Fetch-Site göndermeyen eski tarayıcı yolu: Referer kontrolü devrede.
    _login_as(client, make_user, login, "eve")
    response = client.get("/logout", headers={"Referer": "http://evil.example/"})
    assert response.status_code == 403


def test_logout_without_any_headers_rejected(client, make_user, login):
    # SEC-1: Sec-Fetch-Site VE Referer ikisi de yoksa default-DENY. Eskiden kontrol
    # düşüp logout devam ediyordu (fail-open) → her iki başlığı da göndermeyen
    # istemcilerde CSRF ile sessiz sign-out mümkündü.
    _login_as(client, make_user, login, "frank")
    assert client.get("/logout").status_code == 403
    assert client.get("/supplements").status_code == 200  # hâlâ oturumda


def test_logout_address_bar_navigation_signs_out(client, make_user, login):
    # Gerçek adres-çubuğu navigasyonu modern tarayıcılarda Sec-Fetch-Site: none
    # gönderir → çıkış çalışmaya devam eder (default-deny yalnızca başlıksız
    # legacy/non-browser istemcileri etkiler).
    _login_as(client, make_user, login, "grace")
    response = client.get("/logout", headers={"Sec-Fetch-Site": "none"})
    assert response.status_code == 302
    assert client.get("/supplements").status_code == 302  # oturum kapandı


# ---------------------------------------------------------------------------
# Native Amazon Cognito kayıt/doğrulama/giriş akışı (COGNITO_ENABLED True iken).
# Cognito ağ çağrıları (cognito_service) tamamen mock'lanır — hermetik kalır.
# ---------------------------------------------------------------------------

import pytest

from app.blueprints import auth as auth_bp
from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError
from app.services import cognito_jwt, session_store  # noqa: E402


@pytest.fixture
def cognito_native(monkeypatch):
    """COGNITO_ENABLED'ı aç ve cognito_service çağrılarını yakala. Yakalanan
    argümanları (özellikle Cognito'ya geçen `name`) sınama için kaydeder."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    captured = {"confirmed": set()}

    def fake_sign_up(username, password, email, name):
        captured["sign_up"] = {"username": username, "email": email, "name": name}
        return f"sub-{username}"

    def fake_confirm(username, code):
        captured["confirm"] = {"username": username, "code": code}
        captured["confirmed"].add(username)

    def fake_authenticate(username, password):
        # Doğrulanmamışsa (henüz confirm edilmedi) Cognito gibi davran.
        if username not in captured["confirmed"]:
            raise CognitoServiceError("E-postan henüz doğrulanmadı.", "UserNotConfirmedException")
        return {
            "tokens": {"access_token": f"acc-{username}", "id_token": f"id-{username}",
                       "refresh_token": f"ref-{username}", "expires_in": 3600},
            "claims": {"sub": f"sub-{username}", "email": f"{username}@example.com",
                       "email_verified": True, "name": username},
        }

    monkeypatch.setattr(cognito_service, "sign_up", fake_sign_up)
    monkeypatch.setattr(cognito_service, "confirm_sign_up", fake_confirm)
    monkeypatch.setattr(cognito_service, "authenticate", fake_authenticate)
    # JWKS doğrulaması testte sahte token'ları geçsin (kripto testi test_cognito_jwt'de).
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": tok.replace("id-", "sub-").replace("acc-", "sub-")})
    return captured


def test_login_never_calls_local_password_helper():
    assert not hasattr(User, "check_password")


def test_auth_posts_fail_controlled_when_cognito_disabled(client, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", False)
    assert client.post(
        "/login", json={"username": "userx", "password": "Password1"}
    ).status_code == 503
    assert client.post("/register", json={
        "username": "userx",
        "email": "x@example.com",
        "password": "Password1",
    }).status_code == 503


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
    assert user.password_hash is None


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


def test_cognito_referral_reward_waits_for_verification(client, cognito_native, make_user):
    from app.extensions import db
    from app.services.referral import REFERRAL_REWARD_XP, ensure_referral_code

    referrer = make_user("cognitoref", rank_points=0)
    ensure_referral_code(referrer)
    db.session.commit()

    registered = client.post("/register", json={
        "username": "cognitoinvited",
        "email": "cognitoinvited@example.com",
        "password": "Sifre123",
        "ref": referrer.referral_code.lower(),
    })
    assert registered.status_code == 200
    assert registered.get_json()["referred"] is False

    invited = User.query.filter_by(username="cognitoinvited").one()
    db.session.refresh(referrer)
    assert invited.referred_by_id is None
    assert invited.rank_points == 0
    assert referrer.rank_points == 0
    assert invited.user_metadata["pending_referral_code"] == referrer.referral_code.lower()

    confirmed = client.post(
        "/verify", json={"username": "cognitoinvited", "code": "123456"}
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["referred"] is True

    db.session.expire_all()
    invited = User.query.filter_by(username="cognitoinvited").one()
    referrer = User.query.filter_by(username="cognitoref").one()
    assert invited.referred_by_id == referrer.id
    assert invited.rank_points == REFERRAL_REWARD_XP
    assert referrer.rank_points == REFERRAL_REWARD_XP
    assert "pending_referral_code" not in (invited.user_metadata or {})

    # Idempotency: a repeated successful confirmation cannot award XP again.
    confirmed_again = client.post(
        "/verify", json={"username": "cognitoinvited", "code": "123456"}
    )
    assert confirmed_again.status_code == 200
    assert confirmed_again.get_json()["referred"] is False
    db.session.expire_all()
    assert User.query.filter_by(username="cognitoinvited").one().rank_points == REFERRAL_REWARD_XP
    assert User.query.filter_by(username="cognitoref").one().rank_points == REFERRAL_REWARD_XP


def test_cognito_verify_resend(client, cognito_native, monkeypatch):
    _register(client, "resendme")
    sent = {}
    monkeypatch.setattr(cognito_service, "resend_code", lambda username: sent.update(u=username))
    response = client.post("/verify/resend", json={"username": "resendme"})
    assert response.status_code == 200
    assert sent["u"] == "resendme"


def test_verify_routes_404_when_cognito_disabled(client):
    # COGNITO_ENABLED False (varsayılan) → doğrulama uçları kapalı.
    assert client.get("/verify?u=x").status_code == 404
    assert client.post("/verify", json={"username": "x", "code": "1"}).status_code == 404


# ---------------------------------------------------------------------------
# Hoş geldin e-postası (Resend Sprint 3) — doğrulama sonrası best-effort.
# ---------------------------------------------------------------------------

def _capture_emails(monkeypatch):
    from app.services import email_service
    calls = []

    def fake_send(to, subject, html, **kwargs):
        calls.append({"to": to, "subject": subject, "html": html, **kwargs})
        return "msg-1"

    monkeypatch.setattr(email_service, "send_html_email", fake_send)
    return calls


def test_verify_success_sends_welcome_email(client, cognito_native, monkeypatch):
    sent = _capture_emails(monkeypatch)
    _register(client, "hosgeldin")
    response = client.post("/verify", json={"username": "hosgeldin", "code": "123456"})
    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["to"] == "hosgeldin@example.com"
    assert "hoş geldin" in sent[0]["subject"]
    assert sent[0].get("text")  # düz-metin alternatifi


def test_verify_succeeds_even_if_welcome_email_raises(client, cognito_native, monkeypatch):
    from app.services import email_service

    def boom(*args, **kwargs):
        raise RuntimeError("resend down")
    monkeypatch.setattr(email_service, "send_html_email", boom)

    _register(client, "direncli")
    response = client.post("/verify", json={"username": "direncli", "code": "123456"})
    assert response.status_code == 200  # e-posta hatası doğrulamayı DÜŞÜRMEZ


def test_failed_confirm_sends_no_welcome_email(client, cognito_native, monkeypatch):
    sent = _capture_emails(monkeypatch)

    def boom(username, code):
        raise CognitoServiceError("Doğrulama kodu hatalı.", "CodeMismatchException")
    monkeypatch.setattr(cognito_service, "confirm_sign_up", boom)

    _register(client, "yanliskod")
    response = client.post("/verify", json={"username": "yanliskod", "code": "000000"})
    assert response.status_code == 400
    assert sent == []


def test_verify_without_local_user_row_no_crash_no_email(client, cognito_native, monkeypatch):
    # Cognito onayladı ama yerel satır yok (edge) → e-posta atlanır, 200 döner.
    sent = _capture_emails(monkeypatch)
    response = client.post("/verify", json={"username": "yerelsiz", "code": "123456"})
    assert response.status_code == 200
    assert sent == []


# ---------------------------------------------------------------------------
# Rate limit — 5/saat kayıt limiti ve Türkçe 429 yanıtı.
# ---------------------------------------------------------------------------

def test_register_rate_limit_returns_429_json(client, cognito_native):
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


# ---------------------------------------------------------------------------
# Cognito hata dalları — kayıt IDP hatası, giriş sub uyuşmazlığı/yetkisizlik.
# ---------------------------------------------------------------------------

def test_cognito_register_idp_error_returns_400(client, cognito_native, monkeypatch):
    def boom(username, password, email, name):
        raise CognitoServiceError("Bu kullanıcı adı zaten kayıtlı.", "UsernameExistsException")
    monkeypatch.setattr(cognito_service, "sign_up", boom)
    response = _register(client, "dupuser")
    assert response.status_code == 400
    assert "zaten kayıtlı" in response.get_json()["error"]
    # Yerel kayıt OLUŞMAMALI — IDP hatası erken döner.
    assert User.query.filter_by(username="dupuser").first() is None


def test_cognito_register_local_commit_failure_returns_clean_error(
        client, cognito_native, monkeypatch, caplog):
    # Cognito sign_up başarılı olduktan SONRA yerel commit patlarsa (eşzamanlı bir
    # kayıt aynı e-postayı pre-check ile commit ARASINA sıkıştırdı) → 500 yerine
    # temiz hata; Cognito orphan loglanır (#7).
    from app.extensions import db

    def racing_sign_up(username, password, email, name):
        # Pre-check geçtikten sonra çağrılır: araya çakışan bir kayıt sıkıştır
        # (aynı e-posta) → bizim INSERT'imiz email unique kısıtını ihlal etsin.
        db.session.add(User(username="araya_giren", email=email, password_hash="x"))
        db.session.commit()
        return f"sub-{username}"
    monkeypatch.setattr(cognito_service, "sign_up", racing_sign_up)

    resp = _register(client, "yarisan", email="dup@example.com")
    assert resp.status_code != 500
    assert resp.status_code == 409
    assert User.query.filter_by(username="yarisan").first() is None  # yerel kayıt oluşmadı
    assert "yarisan" not in caplog.text
    assert "dup@example.com" not in caplog.text


def test_cognito_login_sub_mismatch_rejected(client, cognito_native, monkeypatch):
    _register(client, "verifyme")
    client.post("/verify", json={"username": "verifyme", "code": "123456"})
    # Cognito kimlik bütünlüğü kapısı: dönen sub yerel kayıtla EŞLEŞMEZSE reddet.
    monkeypatch.setattr(cognito_service, "authenticate",
                        lambda u, p: {"tokens": {"access_token": "a", "id_token": "i",
                                                 "refresh_token": "r", "expires_in": 3600},
                                      "claims": {"sub": "BASKA-SUB", "email": f"{u}@example.com"}})
    response = client.post("/login", json={"username": "verifyme", "password": "Sifre123"})
    assert response.status_code == 401
    assert "hatalı" in response.get_json()["error"]


def test_cognito_login_not_authorized_returns_401(client, cognito_native, monkeypatch):
    _register(client, "verifyme")
    client.post("/verify", json={"username": "verifyme", "code": "123456"})
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: (_ for _ in ()).throw(
        CognitoServiceError("Kullanıcı adı veya şifre hatalı.", "NotAuthorizedException")))
    response = client.post("/login", json={"username": "verifyme", "password": "yanlis"})
    assert response.status_code == 401
    assert response.get_json().get("needs_verification") is None  # doğrulama değil, yetki hatası


def test_login_returns_quest_awarded_when_login_quest_exists(client, make_user, login):
    from app.extensions import db
    from app.models import DailyQuest
    make_user("questuser")
    db.session.add(DailyQuest(title="Günlük Giriş", points_reward=10, quest_type="login"))
    db.session.commit()

    response = login("questuser")
    assert response.status_code == 200
    assert response.get_json()["quest_awarded"]  # login quest tamamlandı → ödül döndü


# ---------------------------------------------------------------------------
# H2 — Cognito ORPHAN kurtarma.
#
# /register önce Cognito'da kullanıcı yaratır, SONRA yerel satırı commit'ler.
# Araya çakışan bir kayıt girip UNIQUE kısıtını ihlal ettirirse Cognito'da hesap
# KALIR ama yerelde satır YOKTUR. Eskiden bu kullanıcı KALICI olarak kilitliydi:
# yeniden kayıt UsernameExists alır, giriş ise yerel satır bulunamadığı için
# 401'ler; UNSIGNED public client Cognito tarafını temizleyemez de.
# Artık giriş, DOĞRULANMIŞ id-token claim'lerinden yerel kaydı bağlar/oluşturur.
# ---------------------------------------------------------------------------

def _verified_claims(monkeypatch, sub, email, name="", email_verified=True):
    """cognito_jwt.validate_token'ı TAM (doğrulanmış) claim seti dönecek şekilde kur."""
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {
        "sub": sub, "email": email, "email_verified": email_verified,
        "name": name,
    })


def _make_orphan(client, monkeypatch, username="yarisan", email="orphan@example.com"):
    """Gerçek orphan üret: sign_up başarılı, yerel commit UNIQUE ihlaliyle düşer."""
    from app.extensions import db

    def racing_sign_up(username, password, email, name):
        db.session.add(User(username="araya_giren", email=email, password_hash="x"))
        db.session.commit()
        return f"sub-{username}"

    monkeypatch.setattr(cognito_service, "sign_up", racing_sign_up)
    resp = _register(client, username, email=email)
    assert resp.status_code == 409
    assert User.query.filter_by(username=username).first() is None  # orphan doğrulandı
    # Yarışan satırı temizle ki e-posta serbest kalsın (gerçekte farklı e-posta olurdu).
    User.query.filter_by(username="araya_giren").delete()
    db.session.commit()
    return resp


def test_cognito_orphan_recovers_at_login(client, cognito_native, monkeypatch):
    """Orphan kullanıcı giriş yapabilmeli — yerel kayıt claim'lerden oluşturulur."""
    _make_orphan(client, monkeypatch)
    _verified_claims(monkeypatch, "sub-yarisan", "orphan@example.com", name="Yarisan")
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: {
        "tokens": {"access_token": "acc", "id_token": "id", "refresh_token": "ref",
                   "expires_in": 3600},
        "claims": {"sub": "sub-yarisan"},
    })

    resp = client.post("/login", json={"username": "yarisan", "password": "Sifre123"})
    assert resp.status_code == 200

    recovered = User.query.filter_by(username="yarisan").one()
    assert recovered.cognito_sub == "sub-yarisan"
    assert recovered.email == "orphan@example.com"
    assert recovered.password_hash is None  # giriş yalnızca Cognito üzerinden


def test_orphan_recovery_links_existing_unbound_row(client, cognito_native, monkeypatch):
    """Yerel satır VAR ama cognito_sub'u yok (sign_up sonrası sub yazımı düşmüş)
    → yeni satır AÇMA, mevcut satırı bağla."""
    from app.extensions import db
    db.session.add(User(username="bagsiz", email="bagsiz@example.com"))
    db.session.commit()

    _verified_claims(monkeypatch, "sub-bagsiz", "bagsiz@example.com")
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: {
        "tokens": {"access_token": "acc", "id_token": "id", "refresh_token": "ref",
                   "expires_in": 3600},
        "claims": {"sub": "sub-bagsiz"},
    })

    resp = client.post("/login", json={"username": "bagsiz", "password": "Sifre123"})
    assert resp.status_code == 200
    assert User.query.filter_by(email="bagsiz@example.com").count() == 1  # kopya YOK
    assert User.query.filter_by(username="bagsiz").one().cognito_sub == "sub-bagsiz"


def test_orphan_recovery_rejects_same_username_with_different_verified_email(
        client, cognito_native, monkeypatch):
    from app.extensions import db
    db.session.add(User(username="legacy-victim", email="victim@example.com"))
    db.session.commit()

    _verified_claims(monkeypatch, "sub-attacker", "attacker@example.com")
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: {
        "tokens": {"access_token": "acc", "id_token": "id", "refresh_token": "ref",
                   "expires_in": 3600},
        "claims": {"sub": "sub-attacker"},
    })

    response = client.post(
        "/login", json={"username": "legacy-victim", "password": "Sifre123"})
    assert response.status_code == 401
    victim = User.query.filter_by(username="legacy-victim").one()
    assert victim.cognito_sub is None
    assert victim.email == "victim@example.com"


def test_orphan_recovery_never_rebinds_row_owned_by_another_sub(
        client, cognito_native, monkeypatch):
    """Yerel kayıt BAŞKA bir Cognito kimliğine bağlıysa ASLA yeniden bağlama —
    aksi halde aynı e-postayla kayıt olan biri mevcut hesabı ele geçirebilirdi."""
    from app.extensions import db
    db.session.add(User(username="sahip", email="sahip@example.com",
                        cognito_sub="sub-GERCEK-SAHIP"))
    db.session.commit()

    # Saldırgan/ikinci kayıt: farklı sub, AYNI (doğrulanmış) e-posta.
    _verified_claims(monkeypatch, "sub-DAVETSIZ", "sahip@example.com")
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: {
        "tokens": {"access_token": "acc", "id_token": "id", "refresh_token": "ref",
                   "expires_in": 3600},
        "claims": {"sub": "sub-DAVETSIZ"},
    })

    resp = client.post("/login", json={"username": "davetsiz", "password": "Sifre123"})
    assert resp.status_code == 401
    # Kurbanın kaydı DOKUNULMAMIŞ olmalı.
    assert User.query.filter_by(username="sahip").one().cognito_sub == "sub-GERCEK-SAHIP"
    assert User.query.filter_by(username="davetsiz").first() is None


def test_orphan_recovery_requires_verified_email(client, cognito_native, monkeypatch):
    """email_verified false → uzlaştırma YOK. Bu, e-posta sahipliği kanıtının
    tek çıpası; olmadan başkasının adresiyle hesap bağlanabilirdi."""
    _verified_claims(monkeypatch, "sub-suphe", "suphe@example.com", email_verified=False)
    monkeypatch.setattr(cognito_service, "authenticate", lambda u, p: {
        "tokens": {"access_token": "acc", "id_token": "id", "refresh_token": "ref",
                   "expires_in": 3600},
        "claims": {"sub": "sub-suphe"},
    })

    resp = client.post("/login", json={"username": "suphe", "password": "Sifre123"})
    assert resp.status_code == 401
    assert User.query.filter_by(username="suphe").first() is None


def test_web_login_preserves_temporary_jwks_unavailability(
        client, cognito_native, monkeypatch):
    monkeypatch.setattr(
        cognito_service, "authenticate",
        lambda *args: (_ for _ in ()).throw(CognitoServiceError(
            "safe temporary identity failure", "JWKSUnavailable")))
    response = client.post(
        "/login", json={"username": "alice", "password": "Sifre123"})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "15"


# ---------------------------------------------------------------------------
# H1 / L3 — giriş yolunda geçici hata ve boş kimlik bilgisi.
# ---------------------------------------------------------------------------

def test_login_jwks_unavailable_returns_503_not_401(client, cognito_native, monkeypatch):
    """JWKS'e ulaşılamadı ≠ şifre yanlış. Parola DOĞRUYDU; 401 demek kullanıcıyı
    yanıltır ve olayı gizler."""
    _register(client, "jwksuser")
    client.post("/verify", json={"username": "jwksuser", "code": "123456"})

    def unavailable(tok, use):
        raise cognito_jwt.TokenValidationError("jwks_unavailable")

    monkeypatch.setattr(cognito_jwt, "validate_token", unavailable)
    resp = client.post("/login", json={"username": "jwksuser", "password": "Sifre123"})
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")


def test_login_empty_credentials_rejected_before_cognito_call(
        client, cognito_native, monkeypatch):
    """L3: boş kullanıcı adı Cognito'ya HİÇ gitmemeli. Gizli app client
    yapılandırılmışsa _secret_hash(None) TypeError → 500 üretirdi."""
    called = {"n": 0}

    def spy(username, password):
        called["n"] += 1
        raise AssertionError("Cognito boş kimlik bilgisiyle çağrılmamalı")

    monkeypatch.setattr(cognito_service, "authenticate", spy)

    for payload in ({"username": "", "password": "Sifre123"},
                    {"username": "user", "password": ""},
                    {}):
        resp = client.post("/login", json=payload)
        assert resp.status_code == 401
    assert called["n"] == 0


def test_secret_hash_null_username_does_not_raise(monkeypatch):
    """L3 (savunma katmanı): gizli client + None username TypeError YÜKSELTMEZ."""
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    monkeypatch.setattr(cognito_service, "COGNITO_APP_CLIENT_ID", "client-123")
    assert isinstance(cognito_service._secret_hash(None), str)
