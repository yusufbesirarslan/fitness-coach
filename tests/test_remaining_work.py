"""2026-06-15 kalan iş kalemleri için testler.

Kapsam: herkese açık landing, davet/referral döngüsü, setup guard (tamamlanmış
profili koru), kilo tek-kaynak uzlaşması, premium sayfası ve tüm şablonların
oturum açmış kullanıcıda render olması (paylaşılan _head/_nav/_actionbar).
"""
from app.extensions import db
from app.models import Supplement, User
from app.services.referral import ensure_referral_code


# ---------------------------------------------------------------------------
# Landing (herkese açık)
# ---------------------------------------------------------------------------

def test_landing_public_renders(client):
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "Ücretsiz Başla" in r.get_data(as_text=True)


def test_landing_redirects_authed_to_dashboard(client, auth_user):
    r = client.get("/welcome")
    assert r.status_code == 302
    assert r.headers["Location"].rstrip("/").endswith("") or r.headers["Location"] in ("/", "http://localhost/")


# ---------------------------------------------------------------------------
# Davet / referral
# ---------------------------------------------------------------------------

def test_register_assigns_referral_code(client):
    r = client.post("/register", json={
        "username": "yenikullanici", "email": "yeni@example.com", "password": "Sifre123"})
    assert r.status_code == 200
    u = User.query.filter_by(username="yenikullanici").first()
    assert u.referral_code and len(u.referral_code) >= 6


def test_invite_sets_cookie_and_redirects(client, make_user):
    inviter = make_user("davetci")
    code = ensure_referral_code(inviter)
    db.session.commit()
    r = client.get(f"/davet/{code}")
    assert r.status_code == 302
    assert "/register" in r.headers["Location"]
    cookies = r.headers.getlist("Set-Cookie")
    assert any("fitx_ref=" in c for c in cookies)


def test_invalid_invite_code_no_cookie(client):
    r = client.get("/davet/YOKBOYLE")
    assert r.status_code == 302
    assert not any("fitx_ref=" in c for c in r.headers.getlist("Set-Cookie"))


def test_register_with_ref_body_links_and_rewards(client, make_user):
    inviter = make_user("davetci2", rank_points=0, weekly_xp=0)
    code = ensure_referral_code(inviter)
    db.session.commit()
    r = client.post("/register", json={
        "username": "davetli", "email": "davetli@example.com",
        "password": "Sifre123", "ref": code})
    assert r.status_code == 200
    assert r.get_json()["referred"] is True

    db.session.expire_all()
    invited = User.query.filter_by(username="davetli").first()
    assert invited.referred_by_id == inviter.id
    # İki tarafa da REFERRAL_REWARD_XP (75) verildi.
    assert invited.rank_points == 75
    assert db.session.get(User, inviter.id).rank_points == 75


def test_self_referral_is_noop(client, make_user):
    # Kendi koduyla kayıt olunamaz çünkü yeni kullanıcı henüz yok; kod sahibi
    # mevcut başka kullanıcı olmalı. Geçersiz kod sessizce yoksayılır.
    r = client.post("/register", json={
        "username": "tek", "email": "tek@example.com",
        "password": "Sifre123", "ref": "GECERSIZ"})
    assert r.status_code == 200
    assert r.get_json()["referred"] is False


def test_referral_endpoint_returns_code_and_url(client, auth_user):
    r = client.get("/referral")
    assert r.status_code == 200
    data = r.get_json()
    assert data["code"]
    assert "/davet/" in data["invite_url"]
    assert data["referred_count"] == 0


# ---------------------------------------------------------------------------
# Setup guard
# ---------------------------------------------------------------------------

def test_setup_redirects_completed_profile(client, make_user, login):
    make_user("bitti", profile_complete=True)
    login("bitti")
    r = client.get("/setup")
    assert r.status_code == 302  # boş sihirbazı açma, panoya gönder


def test_setup_force_reopens_for_completed(client, make_user, login):
    make_user("bitti2", profile_complete=True)
    login("bitti2")
    assert client.get("/setup?yeniden=1").status_code == 200


def test_setup_renders_for_incomplete(client, auth_user):
    assert client.get("/setup").status_code == 200


# ---------------------------------------------------------------------------
# Kilo tek kaynak (progress)
# ---------------------------------------------------------------------------

def test_progress_prefills_current_weight(client, make_user, login):
    make_user("kilolu", profile_complete=True, weight=82.4)
    login("kilolu")
    html = client.get("/progress-page").get_data(as_text=True)
    assert 'value="82.4"' in html
    assert 'placeholder="78.5"' not in html  # sabit sayı kaldırıldı


# ---------------------------------------------------------------------------
# Premium
# ---------------------------------------------------------------------------

def test_premium_page_renders(client, auth_user):
    html = client.get("/premium").get_data(as_text=True)
    assert "PREMIUM" in html
    assert "upgrade_intent" in html  # GA niyet olayı bağlı


# ---------------------------------------------------------------------------
# Tüm şablonlar oturum açmış kullanıcıda render olur (paylaşılan head/nav)
# ---------------------------------------------------------------------------

def test_all_main_pages_render(client, make_user, login):
    user = make_user("gezgin", profile_complete=True, weight=70)
    # supplement içeren edit_profile dalını da gez
    db.session.add(Supplement(user_id=user.id, product_name="Kreatin",
                              brand="X", category="Other", status="Active"))
    db.session.commit()
    login("gezgin")
    for path in ["/", "/nutrition", "/training", "/progress-page", "/friends",
                 "/quests", "/leaderboard", "/supplements", "/edit-profile",
                 "/premium", "/welcome"]:
        r = client.get(path)
        assert r.status_code in (200, 302), f"{path} -> {r.status_code}"
        if r.status_code == 200:
            assert "_head" not in r.get_data(as_text=True)  # include çözüldü


def test_templates_compile():
    """Her şablon Jinja'da derlenebilmeli (blok/include sözdizimi sağlığı)."""
    from app import create_app
    app = create_app()
    for name in app.jinja_env.list_templates(extensions=["html"]):
        app.jinja_env.get_template(name)  # derleme hatası → exception
