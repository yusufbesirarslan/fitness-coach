"""AxisAI UX-1 PR3 — Coach entry convergence.

PR1 locked the Product IA; PR2 made Today · Plan · Coach · Progress the
production chrome. Coach is therefore a first-class destination, and the global
floating launcher that used to be the way to reach it is duplicate navigation.

This module locks the model PR3 converges on:

* **One primary Coach destination.** `/coach`, reached from the primary nav.
* **No global launcher.** `#cw-fab` is page-owned chrome on the Coach
  destination only; no other page opts in, and nothing reserves layout for it.
* **Selective contextual entry.** Progress, Nutrition and Plan each carry one
  secondary link to `/coach`, placed under the content it refers to and never
  competing with the screen's dominant CTA.

It also guards the two things that are easy to get wrong while deleting a
"widget": `static/coach_widget.js` is NOT only the launcher — it is the one
Coach implementation and it hosts Nutrition's menu scanner — and a launcher-less
host must not leave invisible focusable UI behind.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"

WIDGET = "/static/coach_widget.js"

# The production renderings of the core product pages. Nutrition is a Plan-owned
# route, not a fifth primary; it is here because it is a core page.
CORE_ROUTES = ("/", "/training", "/nutrition", "/progress-page")

# Core templates that hosted the widget only to get the launcher.
LAUNCHER_ONLY_TEMPLATES = ("index.html", "today.html", "training.html",
                           "plan.html", "progress.html")

# Only these still carry a floating control of their own.
FAB_RAIL_TEMPLATES = {"nutrition.html", "coach.html", "coach_v2.html"}


def _read(path):
    return path.read_text(encoding="utf-8")


def _seed(client, make_user, login, username):
    make_user(username, profile_complete=True)
    login(username)
    return client


def _html(client, route):
    resp = client.get(route)
    assert resp.status_code == 200, (route, resp.status_code)
    return resp.get_data(as_text=True)


def _executable_js(source):
    """Strip comments so a guard scans code, not the prose documenting it."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        re.sub(r"(^|\s)//.*$", "", line) for line in source.splitlines()
    )


@pytest.fixture(scope="module")
def widget_code():
    return _executable_js(_read(STATIC / "coach_widget.js"))


# ══════════════════════════════════════════════════════════════════════════
# A. THE GLOBAL FAB IS RETIRED
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("route", CORE_ROUTES)
def test_no_core_page_opts_into_the_floating_launcher(
        route, client, make_user, login):
    html = _html(_seed(client, make_user, login, "cecfab"), route)
    assert "data-coach-launcher" not in html, route
    assert 'id="cw-fab"' not in html, route


@pytest.mark.parametrize("name", LAUNCHER_ONLY_TEMPLATES)
def test_templates_that_only_wanted_the_launcher_stopped_loading_the_widget(name):
    """These five carried `coach_widget.js` for one reason: the FAB. None of them
    calls into `window.CW`, so the include is dead weight once the FAB is gone —
    and a loaded-but-unreachable widget is exactly the orphan this PR forbids."""
    source = _read(TEMPLATES / name)
    assert WIDGET not in source, name
    assert "window.CW" not in source, name


def test_nothing_reserves_layout_for_a_launcher_that_is_not_there():
    """The shell used to reserve the FAB rail on every `.page-body`. After the
    retirement only the pages that still carry a floating control do."""
    nav_css = _read(STATIC / "nav.css")
    shell = re.search(r"(?m)^\.page-body \{([^}]*)\}", nav_css)
    assert shell, "missing .page-body rule"
    assert "--fab-rail-h" not in shell.group(1)
    assert ".page-body.has-fab-rail" in nav_css

    for path in sorted(TEMPLATES.glob("*.html")):
        opted_in = "has-fab-rail" in _read(path)
        assert opted_in == (path.name in FAB_RAIL_TEMPLATES), path.name


def test_the_launcher_is_injected_only_behind_the_page_opt_in(widget_code):
    """Source-level, because the branch is what makes the markup conditional: an
    edit that moved `#cw-fab` back outside it would put a global launcher on
    every page that loads the widget, silently."""
    assert "data-coach-launcher" in widget_code
    branch = widget_code.index("(CW_LAUNCHER")
    closing = widget_code.index(": '') +", branch)
    assert branch < widget_code.index('id="cw-fab"') < closing


def test_the_widget_tolerates_a_host_with_no_launcher(widget_code):
    """Nutrition keeps the widget for its menu scanner and opts out of the
    launcher, so every `#cw-fab` read must survive a null."""
    assert "document.getElementById('cw-fab').addEventListener" not in widget_code
    assert "if (cwFab)" in widget_code
    assert widget_code.count("if (fab)") == 2       # both arms of toggle()


def test_the_unread_badge_and_its_cross_page_push_are_gone(widget_code):
    """`receiveCheckinFeedback` pushed a check-in reply into the floating panel
    from Progress and lit a badge on the FAB. Progress no longer loads the
    widget and there is no FAB to light, so writer, badge and notification all
    go — the reply itself is still shown by `#feedback-card` on Progress."""
    for dead in ("receiveCheckinFeedback", "_showNotify", "cw-badge", "unread",
                 "coach.new_message", "coach.checkin_feedback"):
        assert dead not in widget_code, dead
    assert "cw-badge" not in _read(STATIC / "coach_widget.css")
    assert "receiveCheckinFeedback" not in _read(STATIC / "progress.js")


def test_a_closed_widget_is_not_left_in_the_focus_order(widget_code):
    """`#cw-window` is hidden with opacity, which does not remove it from the tab
    order. With no launcher beside it that is focusable UI the user can neither
    see nor have opened."""
    assert "inert = true" in widget_code
    assert "win.inert = !this.open" in widget_code


# ══════════════════════════════════════════════════════════════════════════
# B. THE WIDGET IS NOT ONLY THE LAUNCHER
# ══════════════════════════════════════════════════════════════════════════

def test_nutrition_keeps_the_widget_because_it_hosts_the_menu_scanner(
        client, make_user, login):
    """`logMenuScan` in the Nutrition log sheet is a first-class logging option
    that runs on `CW.startScan`. Dropping the include here would delete a
    Nutrition capability, not a piece of global chrome."""
    html = _html(_seed(client, make_user, login, "cecscan"), "/nutrition")
    assert WIDGET in html
    assert 'data-action="logMenuScan"' in html
    assert "window.CW.startScan" in _read(STATIC / "nutrition.js")


def test_the_scanner_opens_its_own_host(widget_code):
    """`#cw-scan` is absolutely positioned inside `#cw-window`, which is
    `opacity: 0; pointer-events: none` while closed. With no launcher to open
    the window afterwards, a scanner that does not open its own host is
    unreachable."""
    start = widget_code.index("startScan: function ()")
    body = widget_code[start:widget_code.index("stopScan: function ()")]
    assert "if (!this.open) this.toggle();" in body


def test_the_coach_destination_still_hosts_the_one_widget(client, make_user, login):
    html = _html(_seed(client, make_user, login, "cecdest"), "/coach")
    assert html.count(WIDGET) == 1
    assert "data-coach-launcher" in html


def test_the_route_opener_no_longer_waits_on_the_launcher():
    """`/coach` auto-opens the widget. It used to poll for the `#cw-fab` element
    as its readiness signal; that element is now a page-level opt-in, so the
    signal is the widget's own API instead."""
    tpl = _read(TEMPLATES / "coach.html")
    assert "getElementById('cw-fab')" not in tpl
    assert "window.CW.toggle" in tpl


# ══════════════════════════════════════════════════════════════════════════
# C. COACH REMAINS A PRIMARY DESTINATION
# ══════════════════════════════════════════════════════════════════════════

def test_coach_is_reachable_and_active_in_the_primary_navigation(
        client, make_user, login):
    html = _html(_seed(client, make_user, login, "cecnav"), "/coach")
    link = re.search(r'<a\b[^>]*data-nav-id="coach"[^>]*>', html)
    assert link, "Coach is missing from the primary navigation"
    assert 'href="/coach"' in link.group(0)
    assert 'aria-current="page"' in link.group(0)


@pytest.mark.parametrize("route", CORE_ROUTES + ("/coach",))
def test_the_four_primary_destinations_are_unchanged(route, client, make_user, login):
    html = _html(_seed(client, make_user, login, "cecfour"), route)
    seen, order = set(), []
    for nid in re.findall(r'data-nav-id="([a-z]+)"', html):
        if nid not in seen:
            seen.add(nid)
            order.append(nid)
    assert order == ["today", "plan", "coach", "progress"], route
    assert 'id="nav-drawer"' not in html, route
    assert 'id="header-menu-btn"' not in html, route


# ══════════════════════════════════════════════════════════════════════════
# D. CONTEXTUAL COACH ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════

CONTEXTUAL_PAGES = (
    ("/progress-page", "cecctxp"),
    ("/nutrition", "cecctxn"),
    ("/training", "cecctxt"),
)

_ENTRY = re.compile(r'<a\b[^>]*class="btn-ghost"[^>]*href="/coach"[^>]*>(.*?)</a>',
                    re.S)


@pytest.mark.parametrize("route,username", CONTEXTUAL_PAGES)
def test_each_contextual_entry_is_one_secondary_link_to_the_coach_route(
        route, username, client, make_user, login):
    html = _html(_seed(client, make_user, login, username), route)

    # The primary nav owns the header tab and the bottom bar; the page adds
    # exactly one more. Three good entries beat ten decorative ones.
    nav_links = len(re.findall(r'<a\b[^>]*data-nav-id="coach"', html))
    all_links = len(re.findall(r'<a\b[^>]*href="/coach"', html))
    assert all_links == nav_links + 1, route

    entry = _ENTRY.search(html)
    assert entry, f"{route} has no secondary Coach entry"
    label = entry.group(1).strip()
    assert label, f"{route}: the Coach entry has no accessible name"
    assert not re.fullmatch(r"[a-z_]+\.[a-z_]+", label), (
        f"{route}: raw i18n key rendered instead of copy ({label!r})"
    )


@pytest.mark.parametrize("route,username", CONTEXTUAL_PAGES)
def test_a_contextual_entry_never_becomes_a_second_floating_control(
        route, username, client, make_user, login):
    """The point of the PR is not to swap one floating launcher for another."""
    html = _html(_seed(client, make_user, login, username), route)
    entry = _ENTRY.search(html)
    assert entry and "btn-volt" not in entry.group(0), route

    rule = re.search(r"\.coach-entry \{([^}]*)\}", _read(STATIC / "components.css"))
    assert rule, "missing .coach-entry rule"
    assert "position" not in rule.group(1)


def test_no_contextual_entry_serializes_user_data_into_the_url():
    """Honest handoff: the Coach reads plan, targets and metrics server-side, so
    the link carries nothing. A query string here would be a privacy decision,
    not a convenience."""
    for name in ("progress.html", "nutrition.html", "training.html", "plan.html"):
        for href in re.findall(r'href="(/coach[^"]*)"', _read(TEMPLATES / name)):
            assert href == "/coach", (name, href)


def test_training_keeps_its_dominant_cta_and_places_the_entry_below_the_week(
        client, make_user, login):
    """Start Workout stays the one volt CTA on this page; the Coach entry is a
    ghost link further down, under the week view."""
    html = _html(_seed(client, make_user, login, "cecdom"), "/training")
    start = re.search(r'<button[^>]*data-action="startWorkout"[^>]*>', html)
    assert start and "btn-volt" in start.group(0)

    entry = html.index('class="coach-entry"')
    assert html.index('data-action="startWorkout"') < entry
    assert html.index('id="wstats"') < entry


def test_the_nutrition_entry_sits_at_the_foot_of_the_target_card(
        client, make_user, login):
    """"Is this target right for me?" is asked at the target, and the page's own
    dominant control — the logging FAB — is untouched."""
    html = _html(_seed(client, make_user, login, "cecnut"), "/nutrition")
    assert 'id="log-fab"' in html
    entry = html.index('class="coach-entry"')
    assert html.index('id="ring-target"') < entry < html.index('id="meal-timeline"')


def test_the_progress_entry_sits_beside_the_check_in_action(
        client, make_user, login):
    """Both secondary, in the same action row: the interpreted insight above is
    what the user came to Progress to read."""
    html = _html(_seed(client, make_user, login, "cecprg"), "/progress-page")
    actions = html[html.index('class="ps-actions"'):]
    actions = actions[:actions.index("</section>")]
    assert 'data-action="openCheckin"' in actions
    assert 'id="ps-ask"' in actions
    assert 'href="/coach"' in actions


def test_plan_v2_carries_the_same_entry_as_the_legacy_training_page():
    """`/training` has two renderings behind `UIUX_PLAN_V2_ENABLED`. A shipped
    entry point that silently disappears when a flag flips is drift, not a
    rollout."""
    for name in ("training.html", "plan.html"):
        source = _read(TEMPLATES / name)
        assert source.count('class="coach-entry"') == 1, name
        assert "training.ask_coach" in source, name


def test_plan_v2_actually_renders_that_entry_with_an_active_plan(
        app, client, make_user, login):
    """The parity check above is structural; this proves the flag-on rendering
    really serves the entry, in the active-plan branch where it belongs."""
    import json

    from app.extensions import db
    from app.models import TrainingPlan

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = make_user("cecplanv2", profile_complete=True)
    login("cecplanv2")
    db.session.add(TrainingPlan(user_id=user.id, plan_data=json.dumps(
        [{"gun": "Pazartesi", "tip": "guc", "odak": "İtiş", "sure_dk": 45,
          "tahmini_kalori": 320,
          "egzersizler": [{"isim": "Bench Press", "set": "3", "tekrar": "8-12"}]}],
        ensure_ascii=False)))
    db.session.commit()

    html = _html(client, "/training")
    assert 'id="plan-days-label"' in html, "Plan v2 did not render"
    assert html.count('class="coach-entry"') == 1
    entry = _ENTRY.search(html)
    assert entry and entry.group(1).strip()


def test_surfaces_deliberately_left_alone_gained_no_coach_cta():
    """Coverage is not a reason to add an AI button. Today is PR4+ work, and
    Account / Community / Notifications reach Coach through the primary nav."""
    for name in ("index.html", "today.html", "edit_profile.html",
                 "notifications.html", "friends.html", "feed.html",
                 "leaderboard.html", "quests.html", "challenges.html",
                 "premium.html", "pump_check_gallery.html", "manage_stack.html"):
        assert "coach-entry" not in _read(TEMPLATES / name), name
