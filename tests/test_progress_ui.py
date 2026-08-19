"""Progress page render tests (Progress redesign PR1 + PR2).

PR1 guards the redesigned information architecture: the five sections render in
order, the legacy dashboard surfaces are gone, the weekly check-in stays
reachable, and no fabricated progress value is baked into the template.

PR2 adds the client-side half of the trajectory contract: the page must
TRANSLATE the canonical summary and never compute one. Those guards read
static/progress.js structurally, because the rule they enforce ("no threshold
lives here") is about what the file may contain, not about one rendered string.
"""

import re

SECTION_IDS = ("ps-h", "wc-h", "ai-h", "pp-h", "ph-h")

# Every state the server may publish, and the i18n key the client maps it to.
TRAJECTORY_KEYS = {
    "building_baseline": "progress.traj_building_baseline",
    "on_track": "progress.traj_on_track",
    "needs_attention": "progress.traj_needs_attention",
}
SIGNAL_LEDE_KEYS = {
    "insufficient_data": "progress.traj_lede_insufficient_data",
    "progressing": "progress.traj_lede_progressing",
    "keep_pushing": "progress.traj_lede_keep_pushing",
    "build_consistency": "progress.traj_lede_build_consistency",
    "plateau": "progress.traj_lede_plateau",
    "deload": "progress.traj_lede_deload",
}
PERFORMANCE_KEYS = {
    "building_baseline": "progress.perf_state_building_baseline",
    "progressing": "progress.perf_state_progressing",
    "steady": "progress.perf_state_steady",
    "building_consistency": "progress.perf_state_building_consistency",
    "plateau": "progress.perf_state_plateau",
    "deload": "progress.perf_state_deload",
}
CONSISTENCY_KEYS = {
    "consistent": "progress.cons_state_consistent",
    "inconsistent": "progress.cons_state_inconsistent",
    "insufficient_data": "progress.cons_state_insufficient_data",
}


def _progress_js(client):
    r = client.get("/static/progress.js")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _executable_js(js):
    """Strip comments so a guard scans code, not the prose documenting it.

    Both forms matter: the file's banner is a /* ... */ block that names the
    very patterns it forbids ("no streak-as-consistency"), which would
    otherwise trip the guards below.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(
        line for line in js.splitlines() if not line.strip().startswith("//"))


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
    """A brand-new user must not see invented trajectory/adherence numbers.

    Still true after PR2: the trajectory is fetched, never server-rendered, so
    the shipped markup carries the same neutral copy it did before.
    """
    html = _get_progress_html(client, make_user, login, "proguiempty")

    body = html.split('<main class="main-content">')[1].split("</main>")[0]
    # Comments explain what is deliberately NOT implemented (e.g. the deferred
    # PR3 interpretation) — only rendered copy is under test here.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"\{#.*?#\}", "", body, flags=re.S)
    for fabricated in ("ON TRACK", "On Track", "86%", "12 workouts", "Good recovery"):
        assert fabricated not in body

    # No percentage is rendered server-side anywhere in the page body.
    assert not re.search(r"\d+\s*%", body)


# ── PR2: canonical summary consumption ───────────────────────────────────

def test_your_progress_slots_are_addressable_and_announced(
        app, client, make_user, login):
    """The trajectory needs fillable slots and a live region to announce them."""
    html = _get_progress_html(client, make_user, login, "pr2slots")

    for slot in ("ps-card", "ps-state", "ps-lede", "ps-meta"):
        assert f'id="{slot}"' in html, slot
    # The three lines update together after an async fetch, so they live in one
    # polite status region rather than three competing announcements.
    assert 'role="status"' in html and 'aria-live="polite"' in html


def test_page_ships_the_neutral_state_not_a_trajectory(app, client, make_user, login):
    """Nothing is classified server-side; the shell renders neutral copy.

    Scoped to <main>: the whole locale catalog is injected page-wide as
    window.I18N, so the state names legitimately occur elsewhere in the
    document as *key* names ("progress.traj_on_track"). What matters is that
    no state reaches the rendered body before the payload does.
    """
    html = _get_progress_html(client, make_user, login, "pr2neutral")
    body = html.split("<main", 1)[1].split("</main>", 1)[0]
    for state in TRAJECTORY_KEYS:
        assert state not in body
    assert 'data-state=' not in body          # set by JS from the payload only


def test_client_reads_the_canonical_summary_endpoint(app, client, make_user, login):
    js = _progress_js(client)
    assert "/api/progress/summary" in js
    # ...and the surfaces it replaced no longer drive Body/Performance/Consistency.
    assert "/api/progress/achievements" not in js   # streak is not consistency
    assert "/api/progress/workout" not in js        # sessions are not a trajectory


def test_client_maps_every_published_state_to_a_locale_key(app, client, make_user, login):
    """Symmetry guard: server enum ↔ client table ↔ catalog, all three ways."""
    from app.i18n import _CATALOG

    js = _progress_js(client)
    for table in (TRAJECTORY_KEYS, SIGNAL_LEDE_KEYS, PERFORMANCE_KEYS,
                  CONSISTENCY_KEYS):
        for state, key in table.items():
            assert f"{state}: '{key}'" in js, f"{state} is not mapped in progress.js"
            for locale in ("en", "tr"):
                assert _CATALOG[locale].get(key), f"{key} missing from {locale}"


def test_client_states_match_the_server_contract_exactly(app, client, make_user, login):
    """A state the server can emit that the client cannot render is a bug."""
    from app.services.progress_summary import (
        CONSISTENCY_STATES, PERFORMANCE_STATES, TRAJECTORY_BY_SIGNAL,
        TRAJECTORY_STATES,
    )
    assert set(TRAJECTORY_KEYS) == set(TRAJECTORY_STATES)
    assert set(SIGNAL_LEDE_KEYS) == set(TRAJECTORY_BY_SIGNAL)
    assert set(PERFORMANCE_KEYS) == set(PERFORMANCE_STATES)
    assert set(CONSISTENCY_KEYS) == set(CONSISTENCY_STATES)


def test_client_never_fabricates_a_trajectory(app, client, make_user, login):
    """Rule 4: the browser may translate a state, never decide one.

    Scanned structurally rather than by eyeballing: the forbidden shape is a
    comparison against a session count, a weight delta or a streak anywhere in
    the file, which is exactly how the pre-PR2 cards worked.
    """
    js = _progress_js(client)
    code = _executable_js(js)

    # No streak anywhere: it is a login counter, not training consistency.
    assert "streak" not in code
    # No comparison operators against the summary's numeric fields.
    for field in ("sessions", "active_weeks", "analyzed_weeks", "weight_delta_kg",
                  "current_weight_kg", "distance_to_target_kg", "weeks"):
        assert not re.search(
            rf"{field}\s*(>=|<=|>|<)\s*\d", code), f"threshold on {field}"
    # No percentage/score arithmetic.
    for banned in ("* 100", "/ 100", "score", "adherence", "percent"):
        assert banned not in code, banned
    # The only fractional comparison the file is allowed to make is the display
    # epsilon that decides "+0.0 kg" vs "no change" on the BODY card — a
    # rounding tie-break, not a verdict. Historical deltas are server-owned.
    comparisons = set(re.findall(r"[<>]=?\s*0?\.\d+", code))
    assert comparisons == {"< 0.05"}, comparisons
    assert "/checkin-history" not in code


def test_summary_failure_does_not_read_as_insufficient_data(
        app, client, make_user, login):
    """Rule 9 on the client too: a failed fetch is not 'building baseline'."""
    js = _progress_js(client)
    unavailable = js.split("function summaryUnavailable", 1)[1].split("\n}", 1)[0]
    assert "progress.traj_unavailable" in unavailable
    assert "progress.load_error" in unavailable
    assert "building_baseline" not in unavailable
    assert "traj_building_baseline" not in unavailable


def test_trajectory_is_not_communicated_by_colour_alone(app, client, make_user, login):
    """Every accent rule must have a text label set on the same render path."""
    css = client.get("/static/progress.css").get_data(as_text=True)
    for state in ("on_track", "needs_attention", "building_baseline"):
        assert f'.ps-card[data-state="{state}"]' in css

    js = _progress_js(client)
    setter = js.split("function _setTrajectory", 1)[1].split("\n}", 1)[0]
    # data-state and the visible label are written by the same function, so an
    # accent can never appear without its text.
    assert "setAttribute('data-state'" in setter
    assert "stateEl.textContent" in setter


def test_no_chart_or_dashboard_is_reintroduced(app, client, make_user, login):
    js = _progress_js(client)
    for banned in ("Chart(", "chart.umd", "canvas", "switchTab", "heatmap"):
        assert banned not in js, banned
