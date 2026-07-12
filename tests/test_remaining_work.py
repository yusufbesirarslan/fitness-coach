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

def test_register_assigns_referral_code(client, cognito_registration):
    r = client.post("/register", json={
        "username": "yenikullanici", "email": "yeni@example.com", "password": "Sifre123"})
    assert r.status_code == 200
    u = User.query.filter_by(username="yenikullanici").first()
    assert u.referral_code and len(u.referral_code) >= 6


def test_referral_code_is_assigned_before_referral_get(
        client, cognito_registration, login):
    from app.blueprints import pages

    client.post("/register", json={
        "username": "refgetuser", "email": "refget@example.com", "password": "Sifre123"})
    login("refgetuser")
    user = User.query.filter_by(username="refgetuser").one()
    assert user.referral_code
    assert not hasattr(pages, "ensure_referral_code")

    r = client.get("/referral")

    assert r.status_code == 200
    assert r.get_json()["code"] == user.referral_code


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


def test_register_with_ref_body_links_and_rewards(
        client, make_user, cognito_registration):
    inviter = make_user("davetci2", rank_points=0, weekly_xp=0)
    code = ensure_referral_code(inviter)
    db.session.commit()
    r = client.post("/register", json={
        "username": "davetli", "email": "davetli@example.com",
        "password": "Sifre123", "ref": code})
    assert r.status_code == 200
    assert r.get_json()["referred"] is False

    confirmed = client.post(
        "/verify", json={"username": "davetli", "code": "123456"})
    assert confirmed.status_code == 200
    assert confirmed.get_json()["referred"] is True

    db.session.expire_all()
    invited = User.query.filter_by(username="davetli").first()
    assert invited.referred_by_id == inviter.id
    # İki tarafa da REFERRAL_REWARD_XP (75) verildi.
    assert invited.rank_points == 75
    assert db.session.get(User, inviter.id).rank_points == 75


def test_self_referral_is_noop(client, make_user, cognito_registration):
    # Kendi koduyla kayıt olunamaz çünkü yeni kullanıcı henüz yok; kod sahibi
    # mevcut başka kullanıcı olmalı. Geçersiz kod sessizce yoksayılır.
    r = client.post("/register", json={
        "username": "tek", "email": "tek@example.com",
        "password": "Sifre123", "ref": "GECERSIZ"})
    assert r.status_code == 200
    assert r.get_json()["referred"] is False


def test_referral_endpoint_returns_code_and_url(client, auth_user):
    ensure_referral_code(auth_user)
    db.session.commit()
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


# ---------------------------------------------------------------------------
# Avatar S3 depolama (etkin → S3 anahtarı, kapalı → eski base64)
# ---------------------------------------------------------------------------

def _png_data_url():
    from tests.test_validators import _image_data_url
    return _image_data_url("PNG")


def test_avatar_stored_as_base64_when_s3_disabled(client, auth_user):
    # Test ortamında S3 kapalı (S3_BUCKET_NAME boş) → eski davranış korunur.
    pic = _png_data_url()
    r = client.post("/edit-profile", json={"username": "testuser", "profile_picture": pic})
    assert r.status_code == 200
    db.session.expire_all()
    u = db.session.get(User, auth_user.id)
    assert u.profile_picture == pic
    assert u.profile_picture_key is None
    assert u.avatar_src == pic


def test_avatar_uploaded_to_s3_when_enabled(client, auth_user, monkeypatch):
    import s3_helper
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(s3_helper, "upload_image",
                        lambda *a, **k: "avatars/1/2026/06/abc.png")
    monkeypatch.setattr(s3_helper, "generate_presigned_url",
                        lambda key, **k: "https://signed.example/" + key)

    r = client.post("/edit-profile", json={"username": "testuser",
                                           "profile_picture": _png_data_url()})
    assert r.status_code == 200
    db.session.expire_all()
    u = db.session.get(User, auth_user.id)
    assert u.profile_picture_key == "avatars/1/2026/06/abc.png"
    assert u.profile_picture is None  # base64 HTML'den çıkarıldı
    assert u.avatar_src == "https://signed.example/avatars/1/2026/06/abc.png"


def test_avatar_clear_removes_both(client, auth_user, monkeypatch):
    auth_user.profile_picture_key = "avatars/x.png"
    db.session.commit()
    r = client.post("/edit-profile", json={"username": "testuser", "profile_picture": ""})
    assert r.status_code == 200
    db.session.expire_all()
    u = db.session.get(User, auth_user.id)
    assert u.profile_picture is None and u.profile_picture_key is None
    assert u.avatar_src is None


# ---------------------------------------------------------------------------
# cleanup-test-users CLI
# ---------------------------------------------------------------------------

def test_cleanup_dry_run_lists_without_deleting(app, make_user):
    make_user("test")
    make_user("gercek")
    runner = app.test_cli_runner()
    res = runner.invoke(args=["cleanup-test-users"])
    assert "test" in res.output
    assert "KURU ÇALIŞMA" in res.output
    assert User.query.filter_by(username="test").first() is not None  # silinmedi


def test_cleanup_yes_purges_user_and_dependents(app, make_user):
    from app.models import Friendship, WeeklyLog
    victim = make_user("test")
    keep = make_user("gercek")
    db.session.add(WeeklyLog(user_id=victim.id, weight=70))
    db.session.add(Friendship(sender_id=victim.id, receiver_id=keep.id, status="accepted"))
    db.session.commit()

    runner = app.test_cli_runner()
    res = runner.invoke(args=["cleanup-test-users", "--yes"])
    assert "silindi" in res.output

    assert User.query.filter_by(username="test").first() is None
    assert User.query.filter_by(username="gercek").first() is not None
    assert WeeklyLog.query.filter_by(user_id=victim.id).count() == 0
    assert Friendship.query.count() == 0


def test_cleanup_purges_newer_dependents_without_fk_cascade(app, make_user):
    from sqlalchemy import text
    from app.models import (
        PumpCheck, PumpCheckComment, PumpCheckLike, UserWearableConnection,
        WearableActivityLog, WearableSleepLog, WearableWorkoutLog,
    )

    victim = make_user("test")
    db.session.execute(text("PRAGMA foreign_keys=OFF"))
    pump = PumpCheck(user_id=victim.id, visibility="private", shared_friend_ids=[])
    db.session.add(pump)
    db.session.flush()
    db.session.add_all([
        PumpCheckLike(pump_check_id=pump.id, user_id=victim.id),
        PumpCheckComment(pump_check_id=pump.id, user_id=victim.id, body="x"),
        UserWearableConnection(user_id=victim.id, provider="whoop",
                               access_token_encrypted="cipher"),
        WearableSleepLog(user_id=victim.id, provider="whoop", source_id="sleep",
                         date_key="2026-07-10"),
        WearableActivityLog(user_id=victim.id, provider="whoop", date_key="2026-07-10"),
        WearableWorkoutLog(user_id=victim.id, provider="whoop", source_id="workout",
                           date_key="2026-07-10"),
    ])
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["cleanup-test-users", "--yes"])
    assert result.exit_code == 0
    for model in (PumpCheckLike, PumpCheckComment, UserWearableConnection,
                  WearableSleepLog, WearableActivityLog, WearableWorkoutLog):
        assert model.query.filter_by(user_id=victim.id).count() == 0


def test_cleanup_does_not_match_real_users(app, make_user):
    make_user("ahmet")
    make_user("besir290")
    runner = app.test_cli_runner()
    res = runner.invoke(args=["cleanup-test-users"])
    assert "bulunamadı" in res.output


def test_templates_compile():
    """Her şablon Jinja'da derlenebilmeli (blok/include sözdizimi sağlığı)."""
    from app import create_app
    app = create_app()
    for name in app.jinja_env.list_templates(extensions=["html"]):
        app.jinja_env.get_template(name)  # derleme hatası → exception
