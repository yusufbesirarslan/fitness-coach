"""i18n (TR/EN) altyapısı testleri: t() fallback, locale çözümü, /set-language,
kayıtta dil kalıcılığı ve koç sistem promptunun dile göre değişmesi."""


def test_t_explicit_locale_and_fallback():
    from app.i18n import t
    # EN sözlükten birebir
    assert t("login.submit", locale="en") == "LOG IN"
    # TR sözlükten birebir
    assert t("login.submit", locale="tr") == "GİRİŞ YAP"
    # Eksik anahtar → anahtarın kendisi (kırılmaz)
    assert t("does.not.exist", locale="en") == "does.not.exist"
    # Interpolasyon
    assert t("auth.welcome", locale="en", username="Sam") == "Welcome Sam!"
    assert t("auth.welcome", locale="tr", username="Sam") == "Hoş geldin Sam!"


def test_en_falls_back_to_tr_for_missing_key(monkeypatch):
    import app.i18n as i18n
    # en'de olmayan ama tr'de olan kurgusal bir anahtar → tr değerine düşer
    monkeypatch.setitem(i18n._CATALOG["tr"], "only.in.tr", "yalnız tr")
    assert "only.in.tr" not in i18n._CATALOG.get("en", {})
    assert i18n.t("only.in.tr", locale="en") == "yalnız tr"


def test_build_coach_system_language():
    from app.services.ai_coach import build_coach_system
    en = build_coach_system("en")
    tr = build_coach_system("tr")
    assert "ENGLISH" in en or "English" in en
    assert "Türkçe" in tr
    # Geçersiz dil → tr'ye düşer
    assert build_coach_system("zz") == tr


def test_set_language_endpoint_switches_session(client):
    r = client.post("/set-language", json={"lang": "en"})
    assert r.status_code == 200
    assert r.get_json()["language"] == "en"
    # Sonraki istek İngilizce render etmeli (anon → session['lang'])
    page = client.get("/login")
    body = page.get_data(as_text=True)
    assert "Sign in to your account" in body
    assert "Hesabına giriş yap" not in body


def test_set_language_rejects_invalid(client):
    r = client.post("/set-language", json={"lang": "de"})
    assert r.status_code == 400


def test_default_locale_is_turkish(client):
    body = client.get("/login").get_data(as_text=True)
    assert "Hesabına giriş yap" in body


def test_register_persists_language(app, client):
    from app.models import User
    r = client.post("/register", json={
        "username": "enuser", "email": "enuser@example.com",
        "password": "Sifre123", "language": "en",
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    user = User.query.filter_by(username="enuser").first()
    assert user is not None
    assert user.language == "en"


def test_register_defaults_language_to_tr(app, client):
    from app.models import User
    client.post("/register", json={
        "username": "truser", "email": "truser@example.com",
        "password": "Sifre123",
    })
    user = User.query.filter_by(username="truser").first()
    assert user is not None
    assert user.language == "tr"


def test_landing_renders_localized(client):
    # Landing /welcome'da (anonim); / login-gated dashboard.
    tr_body = client.get("/welcome").get_data(as_text=True)
    assert "Ücretsiz Başla" in tr_body
    # EN'e geç → İngilizce gövde
    client.post("/set-language", json={"lang": "en"})
    en_body = client.get("/welcome").get_data(as_text=True)
    assert "Start Free" in en_body
    assert "Ücretsiz Başla" not in en_body


def test_dashboard_renders_localized(app, client, make_user, login):
    # Dashboard (/) login + profile_complete ister.
    make_user("dashen", profile_complete=True, language="en")
    login("dashen")
    body = client.get("/").get_data(as_text=True)
    assert "Daily Calories" in body and "Activity Tracking" in body
    assert "Günlük Kalori" not in body
    # EN tip dizisi seçili olmalı (TR tip metni gövdede olmamalı)
    assert "Sports Physiology" in body


def test_authenticated_locale_follows_user(app, client, make_user, login):
    """Girişli kullanıcının language alanı locale'i belirler (session'dan bağımsız)."""
    make_user("enfan", language="en")
    login("enfan")
    body = client.get("/login").get_data(as_text=True)
    # /login GET girişliyken de render edilir; EN locale beklenir
    assert "Sign in to your account" in body
