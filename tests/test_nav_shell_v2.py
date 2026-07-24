"""AxisAI UIUX Sprint 1 PR1 — bayrak-kapılı gezinme kabuğu (render) testleri.

Hem KAPALI (legacy) hem AÇIK (v2) yollarını kapsar; aynı anda yalnızca birinin
mount olduğunu, rota→aktif eşlemesini, Coach hedefini, yerelleştirmeyi ve temel
erişilebilirlik yapısını doğrular. Bayrak `app.config["UIUX_NAV_V2_ENABLED"]`
ile test içinde açılır (inject_nav bunu render-zamanında okur).
"""
import re

import pytest


def _seed_login(client, make_user, login, username="navuser"):
    make_user(username, profile_complete=True)
    login(username)
    return client


def _nav_ids(html):
    return re.findall(r'data-nav-id="([a-z]+)"', html)


def _unique_order(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _active_ids(html):
    """id -> aktif mi (header + alt çubuk örnekleri OR'lanır)."""
    out = {}
    for m in re.finditer(r'<a\b[^>]*\bdata-nav-id="([a-z]+)"[^>]*>', html):
        nid = m.group(1)
        out[nid] = out.get(nid, False) or ('aria-current="page"' in m.group(0))
    return out


# ── Feature-flag davranışı ──

def test_flag_default_off_renders_legacy_shell(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "data-nav-id=" not in html                    # v2 işareti yok
    assert 'href="/edit-profile" class="ab-tab' in html  # legacy 5. sekme (Profile)
    assert 'href="/nutrition" class="ab-tab' in html     # legacy Nutrition birincil


def test_flag_on_renders_v2_four_destination_shell(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert 'data-nav-id="today"' in html
    assert 'data-nav-id="plan"' in html
    assert 'data-nav-id="coach"' in html
    assert 'data-nav-id="progress"' in html


def test_missing_flag_state_fails_safe_to_legacy(app, client, make_user, login):
    app.config.pop("UIUX_NAV_V2_ENABLED", None)           # eksik/geçersiz durum
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "data-nav-id=" not in html                    # güvenli varsayılan: legacy


def test_no_duplicate_interactive_nav_in_either_state(app, client, make_user, login):
    _seed_login(client, make_user, login)
    off = client.get("/").get_data(as_text=True)
    assert off.count('class="header-nav"') == 1
    assert off.count('class="action-bar"') == 1
    assert "data-nav-id=" not in off

    app.config["UIUX_NAV_V2_ENABLED"] = True
    on = client.get("/").get_data(as_text=True)
    assert on.count('class="header-nav"') == 1            # tek masaüstü birincil nav
    assert on.count('class="action-bar"') == 1            # tek mobil birincil nav
    assert "data-nav-id=" in on                           # legacy+v2 aynı anda mount olmaz


# ── Birincil bilgi mimarisi ──

def test_primary_order_and_membership_when_on(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    order = _unique_order(_nav_ids(html))
    assert order == ["today", "plan", "coach", "progress"]
    assert "nutrition" not in order                       # indirildi
    assert "profile" not in order                         # utility, birincil değil


# ── Rota → aktif durum (çocuk rotalar dahil eşleme) ──

@pytest.mark.parametrize("route,expected", [
    ("/", "today"),
    ("/training", "plan"),
    ("/coach", "coach"),
    ("/progress-page", "progress"),
])
def test_route_activates_expected_primary(app, client, make_user, login, route, expected):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get(route).get_data(as_text=True)
    active = _active_ids(html)
    assert active.get(expected) is True, (route, expected, active)
    for other in ("today", "plan", "coach", "progress"):
        if other != expected:
            assert active.get(other) is False, (route, other, active)


def test_nutrition_route_activates_no_primary_when_on(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/nutrition").get_data(as_text=True)
    assert not any(_active_ids(html).values())            # indirilmiş → yanlış aktif yok


# ── Coach hedefi ──

def test_coach_route_requires_auth(client):
    resp = client.get("/coach")                           # anonim
    assert resp.status_code in (301, 302, 401, 403)


def test_coach_page_renders_shell_and_hosts_widget(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    resp = client.get("/coach")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="global-header' in html                 # ortak kabuk
    assert "/static/coach_widget.js" in html              # mevcut widget barındırılır
    assert 'id="main-content"' in html                    # sabit ana landmark


# ── Yerelleştirme ──

def test_nav_labels_localized_no_raw_keys(app, client, make_user, login):
    # Not: _head.html tüm i18n kataloğunu window.I18N'e gömer (anahtar adları JSON'da
    # geçer) — bu yüzden anahtarın GÖRÜNÜR nav metni olarak sızmadığını (>anahtar<)
    # kontrol ederiz, sayfada hiç geçmediğini DEĞİL.
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert ">nav.today<" not in html                      # ham anahtar metin olarak sızmadı
    assert ">nav.coach<" not in html
    assert ">nav.plan<" not in html
    assert ("Today" in html) or ("Bugün" in html)
    assert ("Coach" in html) or ("Koç" in html)


# ── Erişilebilirlik yapısı / benzersiz kimlikler ──

def test_landmarks_and_unique_ids_when_on(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert 'aria-label="' in html                         # nav erişilebilir adı
    assert html.count('id="nav-drawer"') == 1
    assert html.count('id="header-menu-btn"') == 1
    assert html.count('id="notif-badge"') == 1


def test_utility_logout_still_reachable_when_on(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert 'href="/logout"' in html                       # utility korunur (çekmecede)
    assert 'href="/edit-profile"' in html                 # hesap/profil erişilir (avatar)
