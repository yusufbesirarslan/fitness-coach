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
