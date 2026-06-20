"""Integration tests for the request hooks (app/hooks.py).

Üç güvenlik/oyunlaştırma katmanını sabitler:
- Origin/Referer tabanlı CSRF koruması (cookie-auth POST'ların saldırı yüzeyi)
- Per-request nonce'lu CSP başlığı (satır-içi script enjeksiyonuna karşı)
- update_streak: gün-sınırı mantığı + 7/14/30/60/100 milestone XP ödülleri

    python -m pytest tests/test_hooks.py -v
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Activity, User


def _csp_directives(response):
    header = response.headers.get("Content-Security-Policy", "")
    return {d.split(" ", 1)[0]: d for d in (p.strip() for p in header.split(";")) if d}


# ---------------------------------------------------------------------------
# CSRF — state-changing istekler Origin/Referer doğrulamasından geçmeli.
# ---------------------------------------------------------------------------

def test_csrf_post_without_origin_or_referer_rejected(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"})
    assert response.status_code == 403


def test_csrf_cross_origin_post_rejected(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_csrf_cross_site_referer_rejected(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Referer": "http://evil.example/fake-form"})
    assert response.status_code == 403


def test_csrf_same_origin_post_passes(raw_client):
    # Aynı origin → CSRF kapısını geçer; yanlış şifre 401'i view'dan gelir.
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Origin": "http://localhost"})
    assert response.status_code == 401


def test_csrf_same_origin_referer_passes(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Referer": "http://localhost/login"})
    assert response.status_code == 401


def test_csrf_does_not_apply_to_get(raw_client):
    assert raw_client.get("/login").status_code == 200


# ---------------------------------------------------------------------------
# CSP — başlık her yanıtta, nonce per-request, 'unsafe-inline' script'te yok.
# ---------------------------------------------------------------------------

def test_csp_header_present_and_locked_down(client):
    directives = _csp_directives(client.get("/health"))
    assert directives["default-src"] == "default-src 'self'"
    assert "'nonce-" in directives["script-src"]
    assert "'unsafe-inline'" not in directives["script-src"]
    # Satır-içi nitelik-handler'ları (onclick=...) tamamen yasak — tüm on*
    # işleyicileri data-action delegasyonuna (static/actions.js) taşındı.
    assert directives["script-src-attr"] == "script-src-attr 'none'"
    assert directives["frame-ancestors"] == "frame-ancestors 'none'"
    assert directives["object-src"] == "object-src 'none'"
    # style-src-elem: <style> blokları nonce ister, 'unsafe-inline' YOK — XSS ile
    # enjekte edilen <style> çalışmaz. Dinamik style="" nitelikleri style-src-attr
    # üzerinden 'unsafe-inline' ile yaşamaya devam eder (progress-bar genişlikleri).
    assert "'nonce-" in directives["style-src-elem"]
    assert "'unsafe-inline'" not in directives["style-src-elem"]
    assert directives["style-src-attr"] == "style-src-attr 'unsafe-inline'"


def test_csp_style_nonce_matches_template(client):
    # Şablondaki <style nonce="..."> bloğu başlıktaki style-src-elem nonce'u ile
    # imzalanmalı; aksi halde modern tarayıcı stil bloğunu bloklar.
    response = client.get("/login")
    style_elem = _csp_directives(response)["style-src-elem"]
    nonce = style_elem.split("'nonce-")[1].split("'")[0]
    assert nonce and f'<style nonce="{nonce}"' in response.get_data(as_text=True)


def test_csp_nonce_changes_per_request(client):
    first = _csp_directives(client.get("/health"))["script-src"]
    second = _csp_directives(client.get("/health"))["script-src"]
    assert first != second


def test_csp_nonce_in_header_matches_template(client):
    # Şablondaki <script nonce="..."> başlıktaki nonce ile imzalanmalı,
    # yoksa tarayıcı script'i bloklar (CLAUDE.md kuralı).
    response = client.get("/login")
    script_src = _csp_directives(response)["script-src"]
    nonce = script_src.split("'nonce-")[1].split("'")[0]
    assert nonce and f'nonce="{nonce}"' in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# update_streak — gün-sınırı mantığı ve milestone ödülleri.
# ---------------------------------------------------------------------------

def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def test_streak_increments_on_consecutive_day(client, make_user, login):
    user = make_user("alice", last_login=date.today() - timedelta(days=1), streak_count=3)
    login("alice")
    client.get("/health")
    user = _fresh(user.id)
    assert user.streak_count == 4
    assert user.last_login == date.today()


def test_streak_resets_after_gap(client, make_user, login):
    user = make_user("bob", last_login=date.today() - timedelta(days=3), streak_count=10)
    login("bob")
    client.get("/health")
    assert _fresh(user.id).streak_count == 1


def test_streak_first_login_starts_at_one(client, make_user, login):
    user = make_user("carol")  # last_login=None
    login("carol")
    client.get("/health")
    assert _fresh(user.id).streak_count == 1


def test_streak_same_day_counts_once(client, make_user, login):
    user = make_user("dave", last_login=date.today() - timedelta(days=1), streak_count=1)
    login("dave")
    client.get("/health")
    client.get("/health")
    assert _fresh(user.id).streak_count == 2


def test_streak_milestone_awards_xp_and_activity(client, make_user, login):
    user = make_user("eve", last_login=date.today() - timedelta(days=1),
                     streak_count=6, rank_points=0, weekly_xp=0)
    login("eve")
    client.get("/health")
    user = _fresh(user.id)
    assert user.streak_count == 7
    assert user.rank_points == 14  # streak * 2
    assert user.weekly_xp == 14
    milestone = Activity.query.filter_by(user_id=user.id, activity_type="streak_milestone").first()
    assert milestone is not None


def test_streak_non_milestone_awards_nothing(client, make_user, login):
    user = make_user("frank", last_login=date.today() - timedelta(days=1),
                     streak_count=3, rank_points=0)
    login("frank")
    client.get("/health")
    assert _fresh(user.id).rank_points == 0


# ---------------------------------------------------------------------------
# Hata sayfaları
# ---------------------------------------------------------------------------

def test_unknown_path_renders_404(client):
    response = client.get("/boyle-bir-sayfa-yok")
    assert response.status_code == 404
