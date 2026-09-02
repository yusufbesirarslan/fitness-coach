"""AxisAI UX-1 PR2 — üretim gezinme kabuğu (render) testleri.

Product IA dört-hedefli kabuk (Today · Plan · Coach · Progress) üretim
otoritesidir. `UIUX_NAV_V2_ENABLED` artık kullanıcıya ulaşan beş-sekme
legacy dalını seçmez. Hamburger/çekmece yoktur; bell/avatar utility'dir.
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


def _assert_production_four_destinations(html):
    order = _unique_order(_nav_ids(html))
    assert order == ["today", "plan", "coach", "progress"]
    assert "nutrition" not in order
    assert "profile" not in order
    assert html.count('class="header-nav"') == 1
    assert html.count('class="action-bar"') == 1
    assert 'id="nav-drawer"' not in html
    assert 'id="header-menu-btn"' not in html
    assert "nav-drawer" not in html
    assert 'class="header-bell"' in html
    assert 'class="header-avatar"' in html
    assert 'href="/notifications"' in html
    assert 'href="/edit-profile"' in html
    assert 'href="/nutrition" class="ab-tab' not in html
    assert 'href="/edit-profile" class="ab-tab' not in html


# ── Üretim kabuğu bayraktan bağımsız ──

def test_production_shell_is_four_destinations_by_default(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    _assert_production_four_destinations(html)


def test_production_shell_ignores_nav_v2_flag_off(app, client, make_user, login):
    app.config["UIUX_NAV_V2_ENABLED"] = False
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    _assert_production_four_destinations(html)


def test_production_shell_ignores_missing_nav_v2_flag(app, client, make_user, login):
    app.config.pop("UIUX_NAV_V2_ENABLED", None)
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    _assert_production_four_destinations(html)


def test_no_duplicate_interactive_nav(app, client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert html.count('class="header-nav"') == 1
    assert html.count('class="action-bar"') == 1
    assert _nav_ids(html).count("today") == 2  # header + bottom bar
    assert _nav_ids(html).count("coach") == 2


# ── Rota → aktif durum (alan sahipliği) ──

@pytest.mark.parametrize("route,expected", [
    ("/", "today"),
    ("/training", "plan"),
    ("/nutrition", "plan"),
    ("/supplements", "plan"),
    ("/coach", "coach"),
    ("/progress-page", "progress"),
    ("/pump-check-gallery", "progress"),
])
def test_route_activates_expected_primary(client, make_user, login, route, expected):
    _seed_login(client, make_user, login)
    html = client.get(route).get_data(as_text=True)
    active = _active_ids(html)
    assert active.get(expected) is True, (route, expected, active)
    for other in ("today", "plan", "coach", "progress"):
        if other != expected:
            assert active.get(other) is False, (route, other, active)


def test_progress_alias_redirects_and_keeps_progress_active(client, make_user, login):
    _seed_login(client, make_user, login)
    resp = client.get("/progress")
    assert resp.status_code in (301, 302)
    html = client.get("/progress", follow_redirects=True).get_data(as_text=True)
    assert _active_ids(html).get("progress") is True
    for other in ("today", "plan", "coach"):
        assert _active_ids(html).get(other) is False


def test_utility_and_community_routes_activate_no_primary(client, make_user, login):
    _seed_login(client, make_user, login)
    for route in (
        "/notifications",
        "/edit-profile",
        "/premium",
        "/friends",
        "/feed",
        "/leaderboard",
        "/quests",
        "/challenges",
    ):
        html = client.get(route).get_data(as_text=True)
        assert not any(_active_ids(html).values()), (route, _active_ids(html))
        _assert_production_four_destinations(html)


# ── Coach hedefi ──

def test_coach_route_requires_auth(client):
    resp = client.get("/coach")
    assert resp.status_code in (301, 302, 401, 403)


def test_coach_page_renders_shell_and_hosts_widget(client, make_user, login):
    _seed_login(client, make_user, login)
    resp = client.get("/coach")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="global-header' in html
    assert "/static/coach_widget.js" in html
    assert 'id="main-content"' in html
    assert _active_ids(html).get("coach") is True


def test_coach_fab_remains_on_core_pages(client, make_user, login):
    _seed_login(client, make_user, login)
    for route in ("/", "/nutrition", "/training", "/progress-page", "/coach"):
        html = client.get(route).get_data(as_text=True)
        assert "/static/coach_widget.js" in html, route


# ── Yerelleştirme ──

def test_nav_labels_localized_no_raw_keys(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert ">nav.today<" not in html
    assert ">nav.coach<" not in html
    assert ">nav.plan<" not in html
    assert ("Today" in html) or ("Bugün" in html)
    assert ("Coach" in html) or ("Koç" in html)
    assert ("Plan" in html)


# ── Erişilebilirlik / utility ──

def test_landmarks_unique_ids_and_no_orphan_drawer(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert 'aria-label="' in html
    assert html.count('id="notif-badge"') == 1
    assert 'id="nav-drawer"' not in html
    assert 'id="header-menu-btn"' not in html
    assert "aria-controls=\"nav-drawer\"" not in html


def test_bell_and_avatar_have_accessible_names(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert 'class="header-bell"' in html
    assert 'aria-label="' in html
    assert 'class="header-avatar"' in html


def test_logout_reachable_from_account_not_global_chrome(client, make_user, login):
    _seed_login(client, make_user, login)
    home = client.get("/").get_data(as_text=True)
    assert 'href="/logout"' not in home
    account = client.get("/edit-profile").get_data(as_text=True)
    assert 'href="/logout"' in account
