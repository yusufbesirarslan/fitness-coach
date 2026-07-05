"""Phase 2 app shell guards: tüm uygulama sayfaları ortak kabuk parçalarını kullanır."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APP_TEMPLATES = [
    "index.html", "nutrition.html", "training.html", "progress.html",
    "quests.html", "friends.html", "leaderboard.html", "manage_stack.html",
    "chat.html", "edit_profile.html", "feed.html", "premium.html",
    "pump_check_gallery.html",
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
    assert not (ROOT / "static" / "nav.js").exists()
    css = (ROOT / "static" / "nav.css").read_text(encoding="utf-8")
    assert ".drawer" not in css


def test_bottom_nav_has_five_tabs_and_marks_active(client, make_user, login):
    make_user("shelluser", profile_complete=True)
    login("shelluser")
    html = client.get("/").get_data(as_text=True)
    for href in ("/nutrition", "/training", "/progress-page", "/edit-profile"):
        assert f'href="{href}" class="ab-tab' in html
    assert 'href="/" class="ab-tab active"' in html
    assert 'aria-current="page"' in html


def test_secondary_pages_activate_profile_tab(client, auth_user):
    html = client.get("/friends").get_data(as_text=True)
    assert 'href="/edit-profile" class="ab-tab active"' in html


def test_viewport_fit_cover_for_safe_areas():
    head = (ROOT / "templates" / "_head.html").read_text(encoding="utf-8")
    assert "viewport-fit=cover" in head


def test_profile_hub_lists_secondary_destinations(client, auth_user):
    html = client.get("/edit-profile").get_data(as_text=True)
    for href in ("/friends", "/feed", "/leaderboard", "/quests",
                 "/supplements", "/premium", "/logout"):
        assert f'href="{href}" class="hub-link' in html, href
    assert 'data-action="setLang"' in html
