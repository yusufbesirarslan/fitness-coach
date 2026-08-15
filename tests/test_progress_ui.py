"""Progress page render tests (Progress redesign PR1).

Guards the redesigned information architecture: the five sections render in
order, the legacy dashboard surfaces are gone, the weekly check-in stays
reachable, and no fabricated progress value is baked into the template.
"""

import re

SECTION_IDS = ("ps-h", "wc-h", "ai-h", "pp-h", "ph-h")


def _get_progress_html(client, make_user, login, username):
    make_user(username)
    login(username)
    r = client.get("/progress-page")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_progress_page_renders_new_information_architecture(app, client, make_user, login):
    html = _get_progress_html(client, make_user, login, "proguiuser")

    # The five sections exist...
    for anchor in SECTION_IDS:
        assert f'id="{anchor}"' in html, f"missing section heading {anchor}"

    # ...and appear in the prescribed top-to-bottom order:
    # YOUR PROGRESS → WHAT CHANGED → AXIS INSIGHTS → PHYSIQUE → HISTORY.
    positions = [html.index(f'id="{a}"') for a in SECTION_IDS]
    assert positions == sorted(positions), "sections are out of order"

    # WHAT CHANGED carries exactly three top-level cards.
    assert html.count('class="wc-card"') == 3
    for card in ("wc-body", "wc-perf", "wc-cons"):
        assert f'id="{card}"' in html

    # Data-driven regions the JS fills in.
    for slot in ("ps-meta", "insight-list", "physique-body", "history-list"):
        assert f'id="{slot}"' in html

    assert "/static/progress.js" in html
    assert "/static/progress.css" in html
    # Canonical-tokens-only guard (mirrors other page checks).
    assert "--volt" not in html


def test_progress_page_drops_legacy_dashboard_surfaces(app, client, make_user, login):
    html = _get_progress_html(client, make_user, login, "proguilegacy")

    # Activity map, level/XP/streak hero strip, dashboard tabs, and every
    # chart canvas are gone from the primary Progress experience.
    for gone in (
        'id="heatmap-grid"',      # activity map
        'class="prog-overview"',  # level / XP / streak hero strip
        'data-action="switchTab"',
        'class="tab-bar"',
        "<canvas",
        "chart.umd",              # Chart.js is no longer loaded by this page
        'id="insight-row"',       # horizontally scrolling insight carousel
    ):
        assert gone not in html, f"legacy Progress surface still present: {gone}"

    for name in ("weight", "nutrition", "workout", "achievements"):
        assert f'id="tab-{name}"' not in html


def test_weekly_checkin_remains_reachable_and_secondary(app, client, make_user, login):
    html = _get_progress_html(client, make_user, login, "proguicheckin")

    # The sheet and every field id the POST flow depends on are intact.
    assert 'id="checkin-sheet"' in html
    for field in ("ci-weight", "ci-yogunluk", "ci-fatigue", "ci-uyku", "ci-beslenme", "ci-note"):
        assert f'id="{field}"' in html
    assert 'data-action="submitCheckin"' in html

    # The opener still exists, but is no longer a full-width volt CTA.
    opener = re.search(r'<button[^>]*data-action="openCheckin"[^>]*>', html)
    assert opener, "weekly check-in opener is missing"
    assert "btn-volt" not in opener.group(0)
    assert "w-full" not in opener.group(0)


def test_ask_axis_action_is_hidden_until_the_coach_widget_exists(app, client, make_user, login):
    """It defaults to hidden server-side, and the CSS must actually hide it.

    `.btn-ghost` sets `display: inline-flex`, which beats the UA
    `[hidden] { display: none }` rule — without a page-scoped guard the action
    would render even when the coach widget never loaded.
    """
    html = _get_progress_html(client, make_user, login, "proguiask")

    ask = re.search(r'<button[^>]*id="ps-ask"[^>]*>', html)
    assert ask, "Ask-AxisAI action is missing"
    assert "hidden" in ask.group(0)

    css = client.get("/static/progress.css")
    assert css.status_code == 200
    assert ".ps-actions [hidden]" in css.get_data(as_text=True)


def test_overload_chips_are_keyboard_operable(app, client, make_user, login):
    """role=button + tabindex=0 must come with real keyboard behaviour.

    The chips are plain divs, so Enter/Space only work because
    data-action-keydown forwards them. actions.js dispatches every handler as
    fn.apply(el, dataArgs.concat([el, event])) — the chips also carry data-args
    for their click action, so those values are PREPENDED to the keydown
    handler too. activateOnEnter must therefore read the element and event off
    the END of the argument list; fixed (el, e) parameters silently receive
    "kismen" and the element, `e.key` is undefined, and the chips become
    focusable-but-dead for keyboard and screen-reader users.
    """
    html = _get_progress_html(client, make_user, login, "proguichip")

    chips = re.findall(r'<div class="overload-chip[^"]*"[^>]*>', html)
    assert len(chips) == 3
    for chip in chips:
        assert 'data-action="selectOverload"' in chip
        assert 'data-action-keydown="activateOnEnter"' in chip
        assert 'tabindex="0"' in chip
        assert 'role="button"' in chip
        assert "aria-pressed=" in chip

    js = client.get("/static/progress.js").get_data(as_text=True)
    body = js.split("function activateOnEnter", 1)[1].split("\n}", 1)[0]
    assert "arguments[arguments.length - 1]" in body
    assert "arguments[arguments.length - 2]" in body
    # No fixed positional parameters — that is the shape that broke.
    assert re.match(r"\s*\(\s*\)", body), "activateOnEnter must not take positional params"


def test_progress_page_hardcodes_no_progress_values(app, client, make_user, login):
    """A brand-new user must not see invented trajectory/adherence numbers."""
    html = _get_progress_html(client, make_user, login, "proguiempty")

    body = html.split('<main class="main-content">')[1].split("</main>")[0]
    # Comments explain what is deliberately NOT implemented (e.g. the deferred
    # On Track classification) — only rendered copy is under test here.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    for fabricated in ("ON TRACK", "On Track", "86%", "12 workouts", "Good recovery"):
        assert fabricated not in body

    # No percentage is rendered server-side anywhere in the page body.
    assert not re.search(r"\d+\s*%", body)
