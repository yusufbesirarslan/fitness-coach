"""Progress page render tests (Progress redesign PR1 + PR2, Progress V2 PR1).

PR1 guards the redesigned information architecture: the five sections render in
order, the legacy dashboard surfaces are gone, the weekly check-in stays
reachable, and no fabricated progress value is baked into the template.

PR2 adds the client-side half of the trajectory contract: the page must
TRANSLATE the canonical summary and never compute one. Those guards read
static/progress.js structurally, because the rule they enforce ("no threshold
lives here") is about what the file may contain, not about one rendered string.

V2 PR1 re-cuts the page into Current State / Trends / Axis Insight / Physique /
Recent Check-ins and moves every state → copy table into
static/progress_presentation.js (its behaviour is executed under node in
tests/test_progress_presentation_js.py). The guards below therefore read the
page controller AND the presentation model.
"""

import json
import re
from pathlib import Path

SECTION_IDS = ("ps-h", "tr-h", "ai-h", "pp-h", "ph-h")
# V2 PR1 information architecture, top to bottom. Each is its own partial.
SECTIONS = ("current-state", "trends", "axis-insight", "physique",
            "recent-checkins")
# One Trends card per measurable progress signal: element id → view-model metric.
METRIC_CARDS = {"tr-weight": "weight", "tr-volume": "training_volume",
                "tr-consistency": "consistency"}
ROOT = Path(__file__).resolve().parents[1]

# Every state the server may publish, and the i18n key the client maps it to.
TRAJECTORY_KEYS = {
    "building_baseline": "progress.traj_building_baseline",
    "on_track": "progress.traj_on_track",
    "needs_attention": "progress.traj_needs_attention",
}
# Current State's supporting sentence is keyed on the TRAJECTORY, not on the
# signal behind it — which signal needs attention is Axis Insight's to say.
CURRENT_STATE_SUMMARY_KEYS = {
    "building_baseline": "progress.state_summary_building_baseline",
    "on_track": "progress.state_summary_on_track",
    "needs_attention": "progress.state_summary_needs_attention",
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


def _presentation_js(client):
    r = client.get("/static/progress_presentation.js")
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

    # ...and appear in the prescribed top-to-bottom order.
    positions = [html.index(f'id="{a}"') for a in SECTION_IDS]
    assert positions == sorted(positions), "sections are out of order"

    # V2 PR1: each section is an explicit, independently replaceable boundary:
    # Current State → Trends → Axis Insight → Physique → Recent Check-ins.
    main = html.split("<main", 1)[1].split("</main>", 1)[0]
    found = re.findall(r'<section[^>]*data-progress-section="([a-z-]+)"', main)
    assert tuple(found) == SECTIONS
    assert 'data-progress-section="header"' in main
    assert main.index('data-progress-section="header"') < main.index("<section")

    # TRENDS carries exactly three cards, one per measurable signal — not the
    # legacy Body / Performance / Consistency categories.
    assert html.count('class="wc-card"') == 3
    for card, metric in METRIC_CARDS.items():
        assert re.search(rf'id="{card}" data-metric="{metric}"', html), card
    for legacy in ("wc-body", "wc-perf", "wc-cons"):
        assert f'id="{legacy}"' not in html

    # Data-driven regions the JS fills in.
    for slot in ("ps-window", "ps-evidence", "ps-next-move", "ax-card",
                 "physique-body", "history-list"):
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


def test_weekly_checkin_is_the_one_primary_progress_action(app, client, make_user, login):
    html = _get_progress_html(client, make_user, login, "proguicheckin")

    # The sheet and every field id the POST flow depends on are intact.
    assert 'id="checkin-sheet"' in html
    for field in ("ci-weight", "ci-yogunluk", "ci-fatigue", "ci-uyku", "ci-beslenme", "ci-note"):
        assert f'id="{field}"' in html
    assert 'data-action="submitCheckin"' in html

    # Progress owns check-ins, so this existing action closes the page's
    # interpretation -> action loop.  It is the only primary-ranked control;
    # contextual Coach remains secondary.
    opener = re.search(r'<button[^>]*data-action="openCheckin"[^>]*>', html)
    assert opener, "weekly check-in opener is missing"
    assert "btn-volt" in opener.group(0)
    assert "w-full" not in opener.group(0)

    body = html.split('<main', 1)[1].split('</main>', 1)[0]
    assert body.count('btn-volt') == 1


def test_sparse_axis_insight_does_not_repeat_the_current_state_baseline():
    """V2 PR3: the three per-slot "insufficient" lines are gone; a sparse
    user gets ONE baseline interpretation + ONE data-collection action, and
    neither repeats Current State's baseline sentences."""
    for locale in ("en", "tr"):
        catalog = json.loads((ROOT / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
        axis = {catalog["progress.axis_insight_baseline"],
                catalog["progress.axis_action_build_baseline"]}
        current = {catalog["progress.state_summary_building_baseline"],
                   catalog["progress.state_next_building_baseline"],
                   catalog["progress.traj_building_baseline"]}
        assert len(axis) == 2 and not axis & current, locale


def test_coach_entry_is_the_axis_insight_review_link(app, client, make_user, login):
    """UX-1 PR3 made the Progress Coach entry a server-rendered link to /coach
    (it cannot vanish when a script fails). V2 PR3 moves it: the generic
    "Ask AxisAI" beside the check-in is gone, and the ONE Coach entry is the
    Axis Insight's contextual "Review with AxisAI" continuation."""
    html = _get_progress_html(client, make_user, login, "proguiask")

    assert 'id="ps-ask"' not in html
    body = html.split("<main", 1)[1].split("</main>", 1)[0]
    coach_links = [a for a in re.findall(r"<a\b[^>]*>", body) if "/coach" in a]
    assert len(coach_links) == 1
    review = coach_links[0]
    assert 'id="ax-review"' in review
    assert 'href="/coach?review=progress-insight"' in review
    assert "hidden" not in review
    assert "btn-ghost" in review and "btn-volt" not in review
    assert 'data-action="askAxis"' not in html

    # No client half: no gate, no toggle, no widget reference.
    js = _progress_js(client)
    assert "askAxis" not in js
    assert "window.CW" not in js


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

    main = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.DOTALL)
    assert main, "Progress main landmark is missing"
    body = main.group(1)
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

    for slot in ("ps-card", "ps-window", "ps-state", "ps-lede", "ps-evidence",
                 "ps-next-text"):
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
    """Symmetry guard: server enum ↔ client table ↔ catalog, all three ways.

    The tables live in ONE place (progress_presentation.js); the page
    controller holds no mapping of its own, so a state cannot read two ways.
    """
    from app.i18n import _CATALOG

    tables = _presentation_js(client)
    controller = _progress_js(client)
    for table in (TRAJECTORY_KEYS, CURRENT_STATE_SUMMARY_KEYS, PERFORMANCE_KEYS,
                  CONSISTENCY_KEYS):
        for state, key in table.items():
            assert f"{state}: '{key}'" in tables, f"{state} is not mapped"
            assert key not in controller, f"{key} is mapped outside the model"
            for locale in ("en", "tr"):
                assert _CATALOG[locale].get(key), f"{key} missing from {locale}"


def test_client_states_match_the_server_contract_exactly(app, client, make_user, login):
    """A state the server can emit that the client cannot render is a bug."""
    from app.services.progress_summary import (
        CONSISTENCY_STATES, PERFORMANCE_STATES, TRAJECTORY_STATES,
    )
    assert set(TRAJECTORY_KEYS) == set(TRAJECTORY_STATES)
    assert set(CURRENT_STATE_SUMMARY_KEYS) == set(TRAJECTORY_STATES)
    assert set(PERFORMANCE_KEYS) == set(PERFORMANCE_STATES)
    assert set(CONSISTENCY_KEYS) == set(CONSISTENCY_STATES)


def test_signal_ledes_are_retired_from_current_state(app, client):
    """V2 PR1 dedup: the per-signal lede restated the Axis WATCH/WORKING
    headline (build_consistency rendered the identical sentence twice).
    Signal interpretation now has exactly one owner — Axis Insight."""
    from app.i18n import _CATALOG

    for source in (_presentation_js(client), _progress_js(client)):
        assert "traj_lede" not in source
        assert "trajectory.reason" not in source
    for locale in ("en", "tr"):
        assert not [k for k in _CATALOG[locale] if "traj_lede" in k]


def test_client_never_fabricates_a_trajectory(app, client, make_user, login):
    """Rule 4: the browser may translate a state, never decide one.

    Scanned structurally rather than by eyeballing: the forbidden shape is a
    comparison against a session count, a weight delta or a streak anywhere in
    the page controller or the presentation model, which is exactly how the
    pre-PR2 cards worked.
    """
    code = (_executable_js(_progress_js(client)) + "\n"
            + _executable_js(_presentation_js(client)))

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
    # The only fractional comparison allowed is the display epsilon that
    # decides "+0.0 kg" vs "no change" on the Weight card — a rounding
    # tie-break, not a verdict. Historical deltas are server-owned.
    comparisons = re.findall(r"[<>]=?\s*0?\.\d+", code)
    assert comparisons == ["< 0.05"], comparisons
    assert "/checkin-history" not in code


def test_summary_failure_does_not_read_as_insufficient_data(
        app, client, make_user, login):
    """Rule 9 on the client too: a failed fetch is not 'building baseline'.

    The controller renders the presentation model's unavailable view; that
    view is defined once, and executed under node in
    tests/test_progress_presentation_js.py."""
    js = _progress_js(client)
    unavailable = js.split("function summaryUnavailable", 1)[1].split("\n}", 1)[0]
    assert "buildSummaryView(null)" in unavailable
    assert "building_baseline" not in unavailable

    model = _presentation_js(client)
    state = model.split("function unavailableCurrentState", 1)[1].split("\n  }", 1)[0]
    assert "progress.traj_unavailable" in state
    assert "progress.load_error" in state
    assert "building_baseline" not in state


def test_trajectory_is_not_communicated_by_colour_alone(app, client, make_user, login):
    """Every accent rule must have a text label set on the same render path."""
    css = client.get("/static/progress.css").get_data(as_text=True)
    for state in ("on_track", "needs_attention", "building_baseline"):
        assert f'.ps-card[data-state="{state}"]' in css

    js = _progress_js(client)
    setter = js.split("function _setCurrentState", 1)[1].split("\n}", 1)[0]
    # data-state and the visible label are written by the same function, so an
    # accent can never appear without its text.
    assert "setAttribute('data-state'" in setter
    assert "stateEl.textContent" in setter


def test_no_chart_or_dashboard_is_reintroduced(app, client, make_user, login):
    """V2 PR2 adds lightweight inline-SVG trend drawings on purpose; what
    stays banned is a chart LIBRARY, a canvas dashboard, and any drawing
    built from markup strings or driven by runtime layout/animation."""
    for js in (_progress_js(client), _presentation_js(client)):
        code = _executable_js(js)
        for banned in ("Chart(", "chart.umd", "canvas", "switchTab", "heatmap",
                       "<svg", "d3.", "apexcharts", "recharts", "echarts"):
            assert banned not in code, banned
        for runtime in ("ResizeObserver", "requestAnimationFrame", "setInterval",
                        "getBoundingClientRect", "offsetWidth", "clientWidth",
                        "matchMedia"):
            assert runtime not in code, runtime


# ── V2 PR2: Current State + Trends ───────────────────────────────────────

def test_trends_cards_ship_the_five_slots_with_empty_ones_hidden(
        app, client, make_user, login):
    """value → change → viz → note → meta, in falling prominence. Nothing
    that would be an empty frame (change, viz, meta, unit) is visible before
    the summary fills it — no empty graph container, no fake line."""
    html = _get_progress_html(client, make_user, login, "pr2trendslots")
    for card in METRIC_CARDS:
        body = html.split(f'id="{card}"', 1)[1].split("</article>", 1)[0]
        order = [body.index(f'data-slot="{slot}"')
                 for slot in ("value", "change", "viz", "note", "meta")]
        assert order == sorted(order), card
        for slot in ("unit", "change", "viz", "meta"):
            tag = re.search(rf'<[^>]*data-slot="{slot}"[^>]*>', body).group(0)
            assert "hidden" in tag, (card, slot)
        assert "<svg" not in body and "<polyline" not in body


def test_current_state_ships_evidence_and_next_move_hidden_until_real(
        app, client, make_user, login):
    html = _get_progress_html(client, make_user, login, "pr2csslots")
    evidence = re.search(r'<ul[^>]*id="ps-evidence"[^>]*>', html).group(0)
    assert "hidden" in evidence and "aria-label=" in evidence
    move = re.search(r'<div[^>]*id="ps-next-move"[^>]*>', html).group(0)
    assert "hidden" in move
    # STATE → EVIDENCE → ACTION reading order inside the one dominant card.
    card = html.split('id="ps-card"', 1)[1]
    order = [card.index(f'id="{i}"') for i in
             ("ps-window", "ps-state", "ps-lede", "ps-evidence", "ps-next-move")]
    assert order == sorted(order)
    assert card.index('id="ps-next-move"') < card.index('data-action="openCheckin"')


def test_drawings_are_built_as_dom_nodes_and_hidden_from_assistive_tech(
        app, client):
    """The SVG is created with createElementNS (never a markup string) and is
    aria-hidden; its meaning reaches screen readers as text."""
    js = _executable_js(_progress_js(client))
    viz = js.split("function _renderViz", 1)[1].split("\n}", 1)[0]
    assert "createElementNS" in js and "innerHTML" not in viz
    assert "'aria-hidden': 'true'" in viz
    assert "tr-sr" in viz          # visually-hidden text equivalent
    assert "'ol'" in viz           # the week strip is a real list
    change = js.split("function _renderChange", 1)[1].split("\n}", 1)[0]
    assert "setAttribute('aria-hidden', 'true')" in change   # the glyph only


def test_progress_css_respects_reduced_motion_and_adds_no_animation(app, client):
    css = client.get("/static/progress.css").get_data(as_text=True)
    trends = css.split("── 3 · TRENDS", 1)[1].split("── 4 · AXIS", 1)[0]
    current = css.split("── 2 · CURRENT STATE", 1)[1].split("── 3 · TRENDS", 1)[0]
    for block in (trends, current):
        assert "animation" not in block and "transition" not in block
        assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S)  # tokens only
    assert "prefers-reduced-motion" in css
