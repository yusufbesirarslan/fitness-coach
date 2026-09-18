"""Phase 2 app shell guards: tüm uygulama sayfaları ortak kabuk parçalarını kullanır."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

APP_TEMPLATES = [
    # UX-2 PR4 replaced the legacy `index.html` dashboard with `today.html` as
    # the one production Home; the shell contract itself is unchanged.
    "today.html", "nutrition.html", "plan.html", "progress.html",
    "quests.html", "friends.html", "leaderboard.html", "manage_stack.html",
    "chat.html", "edit_profile.html", "feed.html", "premium.html",
    "pump_check_gallery.html", "coach.html",
]


def test_app_templates_use_shared_shell_partials():
    for name in APP_TEMPLATES:
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert '{% include "_nav.html" %}' in html, name
        assert '{% include "_actionbar.html" %}' in html, name
        # inline kopya kalmadı
        assert html.count('class="global-header"') == 0, name
        assert html.count('class="action-bar"') == 0, name


def test_drawer_and_nav_js_are_gone():
    for name in APP_TEMPLATES + ["_nav.html", "_actionbar.html"]:
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "fx-drawer" not in html, name
        assert "drawer-trigger" not in html, name
        assert "nav.js" not in html, name
        assert "id=\"nav-drawer\"" not in html, name
        assert "id=\"header-menu-btn\"" not in html, name
    assert not (ROOT / "static" / "nav.js").exists()
    css = (ROOT / "static" / "nav.css").read_text(encoding="utf-8")
    assert ".drawer" not in css
    assert ".header-menu-btn" not in css
    assert ".nav-drawer" not in css


def test_bottom_nav_has_four_primary_destinations_and_marks_active(client, make_user, login):
    make_user("shelluser", profile_complete=True)
    login("shelluser")
    html = client.get("/").get_data(as_text=True)
    for nav_id in ("today", "plan", "coach", "progress"):
        assert f'data-nav-id="{nav_id}"' in html
    assert 'data-nav-id="today"' in html
    assert 'aria-current="page"' in html
    assert 'href="/nutrition" class="ab-tab' not in html
    assert 'href="/edit-profile" class="ab-tab' not in html


def test_secondary_pages_activate_no_primary_tab(client, auth_user):
    html = client.get("/friends").get_data(as_text=True)
    assert 'aria-current="page"' not in html
    assert 'class="ab-tab active"' not in html
    assert 'class="hn-link active"' not in html


def test_viewport_fit_cover_for_safe_areas():
    head = (ROOT / "templates" / "_head.html").read_text(encoding="utf-8")
    assert "viewport-fit=cover" in head


SHELL_ROUTES = [
    "/", "/nutrition", "/training", "/progress-page", "/quests", "/friends",
    "/feed", "/leaderboard", "/supplements", "/premium", "/edit-profile",
    "/pump-check-gallery", "/coach",
]


def test_all_app_pages_render_shared_shell(client, make_user, login):
    make_user("smokeuser", profile_complete=True)
    login("smokeuser")
    for route in SHELL_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, route
        html = resp.get_data(as_text=True)
        assert 'class="global-header"' in html, route
        assert 'class="action-bar"' in html, route
        assert "fx-drawer" not in html, route


def test_plan_and_coach_header_avatar_matches_working_page(app, client, make_user, login):
    photo = "data:image/png;base64,cHJvZmlsZQ=="
    make_user("avataruser", profile_complete=True, profile_picture=photo)
    login("avataruser")
    avatar = re.compile(r'<a href="/edit-profile" class="header-avatar"[^>]*>\s*<img src="([^"]+)" alt="">\s*</a>')

    working = avatar.search(client.get("/progress-page").get_data(as_text=True))
    assert working is not None and working.group(1) == photo

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    plan = avatar.search(client.get("/training").get_data(as_text=True))
    assert plan is not None and plan.group(1) == photo

    for coach_v2 in (False, True):
        app.config["UIUX_COACH_PAGE_V2_ENABLED"] = coach_v2
        coach = avatar.search(client.get("/coach").get_data(as_text=True))
        assert coach is not None and coach.group(1) == photo


def test_profile_hub_lists_community_and_account_destinations(client, auth_user):
    html = client.get("/edit-profile").get_data(as_text=True)
    for href in ("/friends", "/feed", "/leaderboard", "/quests",
                 "/challenges", "/logout"):
        assert f'href="{href}" class="hub-link' in html, href
    assert 'href="/pump-check-gallery" class="hub-link' not in html
    assert 'href="/supplements" class="hub-link' not in html
    assert 'href="/premium" class="hub-link' not in html
    assert 'data-action="setLang"' in html
    assert 'href="/premium"' in html
    assert 'href="/supplements"' in html
