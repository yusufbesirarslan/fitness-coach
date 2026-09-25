"""AXIS INSIGHT frontend guards (Progress V2 PR3 — one coherent surface).

The rule these enforce — *the browser renders a server-selected insight and
decides nothing* — is about what the files may **contain**, not about one
rendered string, so most of this reads `static/progress_insights.js`,
`static/progress_presentation.js` and the Axis partial structurally. The
view model's behaviour is executed under node in
`tests/test_progress_axis_insight.py`.

Three-way symmetry is the load-bearing part: server enum ↔ client table ↔
locale catalog. A code the server can emit that the client cannot render, or
a client table entry with no copy behind it, is a bug in either direction.

    python -m pytest tests/test_progress_insights_ui.py -v
"""
import re

from app.services.progress_insights import (
    EVIDENCE_CODES,
    INSIGHT_CODES,
    NEXT_MOVE_CODES,
)

# code → the i18n key the client must map it to. Written out here rather than
# imported so the test is an independent statement of the contract.
INSIGHT_KEYS = {code: f"progress.axis_insight_{code}" for code in (
    "baseline", "consistency_gaps", "deload_due", "stalled",
    "ready_to_progress", "holding_steady", "steady_with_dip")}
EVIDENCE_KEYS = {code: f"progress.axis_evidence_{code}" for code in (
    "sessions_across_weeks", "trained_weeks", "unbroken_block",
    "volume_flat_run", "volume_rising", "volume_holding", "volume_falling",
    "strength_rising", "strength_falling")}
ACTION_KEYS = {code: f"progress.axis_action_{code}" for code in (
    "build_baseline", "prioritize_consistency", "deload",
    "maintain_and_consolidate", "progress_training",
    "maintain_current_training")}

# Server-rendered structure copy.
TEMPLATE_KEYS = ("progress.insights_label", "progress.axis_evidence_label",
                 "progress.axis_action_label", "progress.axis_review_cta")

# The retired three-slot model. None of these may come back.
RETIRED_KEYS = (
    "progress.axis_working_label", "progress.axis_watch_label",
    "progress.axis_next_label", "progress.axis_working_empty",
    "progress.axis_watch_empty", "progress.axis_next_empty",
    "progress.axis_working_insufficient", "progress.axis_watch_insufficient",
    "progress.axis_next_insufficient", "progress.axis_active_weeks",
    "progress.ask_axis",
)


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _executable_js(js):
    """Strip comments so a guard scans code, not the prose documenting it."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(
        line for line in js.splitlines() if not line.strip().startswith("//"))


def _progress_html(client, make_user, login, username):
    make_user(username)
    login(username)
    return _get(client, "/progress-page")


def _rendered(html):
    """The document without HTML comments or Jinja-free prose about guards."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def _section(html):
    return _rendered(html).split('id="ai-h"', 1)[1].split("</section>", 1)[0]


# ── Structure: one surface, in coaching order ────────────────────────────

def test_axis_insight_is_one_surface_not_three_cards(app, client, make_user, login):
    section = _section(_progress_html(client, make_user, login, "axuione"))

    assert section.count('class="ax-card"') == 1
    # The three-slot model is gone: no slot articles, no slot ids or labels.
    assert "<article" not in section
    for gone in ('class="ax-slot"', 'id="ax-working"', 'id="ax-watch"',
                 'id="ax-next"', 'id="insight-list"', 'data-slot="headline"'):
        assert gone not in section, gone


def test_order_is_interpretation_evidence_action_cta(app, client, make_user, login):
    section = _section(_progress_html(client, make_user, login, "axuiorder"))
    ids = ("ax-interpretation", "ax-meaning", "ax-evidence", "ax-action",
           "ax-review")
    positions = [section.index(f'id="{i}"') for i in ids]
    assert positions == sorted(positions)
    for slot in ("interpretation", "meaning", "evidence", "action"):
        assert f'data-slot="{slot}"' in section


def test_heading_hierarchy_and_labels_are_real_text(app, client, make_user, login):
    from app.i18n import _CATALOG

    html = _progress_html(client, make_user, login, "axuiheads")
    section = _section(html)
    # h2 section label → h3 action label: no skipped level.
    assert '<h2 class="sec-label" id="ai-h">' in _rendered(html)
    assert re.search(r'<h3 class="ax-action-label"[^>]*>', section)
    assert "<h4" not in section and "<h1" not in section
    for key in TEMPLATE_KEYS:
        assert _CATALOG["tr"][key] in section, key


def test_exactly_one_cta_and_it_is_the_contextual_coach_review(
        app, client, make_user, login):
    from app.i18n import _CATALOG

    section = _section(_progress_html(client, make_user, login, "axuicta"))
    links = re.findall(r"<a\b[^>]*>", section)
    buttons = re.findall(r"<button\b", section)
    assert len(links) == 1 and not buttons
    cta = links[0]
    assert 'id="ax-review"' in cta
    assert "btn-ghost" in cta and "btn-volt" not in cta
    assert "hidden" not in cta                       # server-rendered, cannot vanish
    assert _CATALOG["tr"]["progress.axis_review_cta"] in section
    assert _CATALOG["tr"]["progress.axis_review_cta"] != "AxisAI'ya sor"


def test_cta_url_carries_one_constant_and_no_user_data(app, client, make_user, login):
    """Analytics records the page location including its query string, so
    the handoff URL may name only the KIND of handoff — never an insight code,
    a count or an id. The Coach route re-derives the insight server-side."""
    section = _section(_progress_html(client, make_user, login, "axuiurl"))
    hrefs = re.findall(r'href="([^"]*)"', section)
    assert hrefs == ["/coach?review=progress-insight"]

    from app.coach_handoff import REVIEW_PROGRESS_INSIGHT
    assert hrefs[0].endswith("=" + REVIEW_PROGRESS_INSIGHT)

    js = _executable_js(_get(client, "/static/progress_insights.js"))
    for banned in ("href", "location", "sessionStorage", "localStorage",
                   "ax-review"):
        assert banned not in js, banned


def test_only_the_interpretation_line_is_a_live_region(app, client, make_user, login):
    """No giant live region: an async update announces one sentence."""
    section = _section(_progress_html(client, make_user, login, "axuilive"))
    assert section.count("aria-live") == 1
    live = re.search(r'<p[^>]*aria-live="polite"[^>]*>', section).group(0)
    assert 'id="ax-interpretation"' in live
    assert 'aria-busy="true"' in section              # loading is announced as busy


def test_markup_survives_without_javascript(app, client, make_user, login):
    """Before the fetch resolves: a loading line, empty parts hidden."""
    section = _section(_progress_html(client, make_user, login, "axuinojs"))
    assert 'data-status="loading"' in section
    assert re.search(r'<ul[^>]*id="ax-evidence"[^>]*hidden', section)
    assert re.search(r'<div[^>]*id="ax-action"[^>]*hidden', section)


def test_no_carousel_tabs_or_chart_is_introduced(app, client, make_user, login):
    html = _rendered(_progress_html(client, make_user, login, "axuinocarousel"))
    for banned in ('id="insight-row"', 'class="tab-bar"', 'data-action="switchTab"',
                   "<canvas", "chart.umd", "carousel", "swiper"):
        assert banned not in html, banned

    js = _executable_js(_get(client, "/static/progress_insights.js"))
    for banned in ("Chart(", "canvas", "scrollLeft", "switchTab", "carousel",
                   "heatmap", "setInterval", "setTimeout",
                   "requestAnimationFrame", "getBoundingClientRect",
                   "offsetWidth", "matchMedia", "ResizeObserver"):
        assert banned not in js, banned

    css = _get(client, "/static/progress.css")
    section = css.split("4 · AXIS INSIGHT", 1)[1].split("5 ·", 1)[0]
    for banned in ("overflow-x", "animation", "gradient", "box-shadow",
                   "color-warning"):
        assert banned not in section, banned
    assert ".ax-slot" not in css


# ── The client decides nothing ───────────────────────────────────────────

def test_client_reads_only_the_canonical_axis_insights_endpoint(app, client):
    js = _executable_js(_get(client, "/static/progress_insights.js"))
    assert js.count("fetch(") == 1
    assert "'/api/progress/axis-insights'" in js
    for banned in ("/api/progress/insights'", "/api/progress/summary",
                   "/api/progress/workout", "/api/progress/achievements",
                   "/checkin-history", "/coach", "/ask"):
        assert banned not in js, banned


def test_client_contains_no_decision_threshold(app, client):
    """A comparison against any canonical field is exactly how a browser
    starts owning coaching logic, so none may exist — in the renderer or in
    the Axis part of the view model."""
    renderer = _executable_js(_get(client, "/static/progress_insights.js"))
    model = _executable_js(_get(client, "/static/progress_presentation.js"))
    model = model.split("Axis Insight view model", 1)[1] if \
        "Axis Insight view model" in model else model
    axis_model = model.split("function unavailableAxisInsight", 1)[1].split(
        "window.FitXProgressPresentation", 1)[0]

    for js in (renderer, axis_model):
        for field in ("sessions", "active", "total", "weeks", "volume_delta_pct",
                      "volume_delta", "week_focus", "volume_action",
                      "intensity_action", "trajectory", "weight", "status"):
            assert not re.search(rf"\.{field}\s*(>=|<=|>|<)\s*", js), field
        assert not re.search(r"(>=|<=|>)\s*-?\d", js), "numeric comparison"
        for banned in ("* 100", "/ 100", "Math.round", "Math.abs", "toFixed",
                       "score", "adherence", "streak", "calorie"):
            assert banned not in js, banned

    # The only ordering comparisons in the Axis view model are the evidence
    # ceiling and loop bounds — never a fact about the user.
    assert "AXIS_MAX_EVIDENCE" in axis_model
    # The one equality allowed: "a hold renders no delta line" (display).
    assert axis_model.count("!== 0") == 1
    assert "=== 0" not in renderer and "!== 0" not in renderer


def test_client_tables_match_the_server_contract_exactly(app, client):
    """Three-way symmetry: server enum ↔ client table ↔ catalog."""
    from app.i18n import _CATALOG

    assert set(INSIGHT_KEYS) == set(INSIGHT_CODES)
    assert set(EVIDENCE_KEYS) == set(EVIDENCE_CODES)
    assert set(ACTION_KEYS) == set(NEXT_MOVE_CODES)

    model = _get(client, "/static/progress_presentation.js")
    meaning = {code: key + "_why" for code, key in INSIGHT_KEYS.items()}
    for table in (INSIGHT_KEYS, meaning, EVIDENCE_KEYS, ACTION_KEYS):
        for code, key in table.items():
            assert f"{code}: '{key}'" in model, f"{code} is not mapped"
            for locale in ("en", "tr"):
                assert _CATALOG[locale].get(key), f"{key} missing from {locale}"
    for key in TEMPLATE_KEYS + ("progress.axis_unavailable",
                                "progress.axis_next_volume_delta"):
        for locale in ("en", "tr"):
            assert _CATALOG[locale].get(key), f"{key} missing from {locale}"


def test_retired_three_slot_copy_is_gone(app, client, make_user, login):
    from app.i18n import _CATALOG

    for key in RETIRED_KEYS:
        for locale in ("en", "tr"):
            assert key not in _CATALOG[locale], key
    html = _progress_html(client, make_user, login, "axuiretired")
    for stale in ("WHAT'S WORKING", "WATCH THIS", "NEXT MOVE", "İYİ GİDEN",
                  "DİKKAT ET", "SONRAKİ ADIM", "Ask AxisAI", "AxisAI'ya sor"):
        assert stale not in _rendered(html).split("window.I18N", 1)[0], stale


def test_locale_keys_are_symmetric_across_en_and_tr(app, client):
    from app.i18n import _CATALOG

    en = {k for k in _CATALOG["en"] if k.startswith("progress.axis_")}
    tr = {k for k in _CATALOG["tr"] if k.startswith("progress.axis_")}
    assert en == tr and en
    for key in en:
        assert _CATALOG["en"][key] != _CATALOG["tr"][key], key
        for locale in ("en", "tr"):
            assert "{{" not in _CATALOG[locale][key]


def test_copy_is_not_diagnostic_judgemental_or_generic(app, client):
    """Coach-like, never a verdict on the person, never empty motivation."""
    from app.i18n import _CATALOG

    banned = ("failing", "bad ", "worse", "overtrained", "medical", "injur",
              "diagnos", "keep going", "you're doing great", "stay consistent",
              "!", "başarısız", "kötü", "hasta", "aşırı antrenman", "sakatl",
              "harika gidiyorsun")
    for locale in ("en", "tr"):
        for key, value in _CATALOG[locale].items():
            if not key.startswith(("progress.axis_insight_",
                                   "progress.axis_evidence_",
                                   "progress.axis_action_")):
                continue
            for word in banned:
                assert word not in value.lower(), f"{key}: {word}"


def test_no_internal_identifier_is_ever_copy(app, client):
    """Machine vocabulary never reaches the reader, in either locale."""
    from app.i18n import _CATALOG

    identifiers = set(INSIGHT_CODES) | set(EVIDENCE_CODES) | set(NEXT_MOVE_CODES) | {
        "needs_attention", "building_baseline", "insufficient_data",
        "week_focus", "NEEDS_CONSISTENCY", "BUILDING_BASELINE"}
    for locale in ("en", "tr"):
        for key, value in _CATALOG[locale].items():
            if not key.startswith("progress.axis_"):
                continue
            # Single English words ("deload", "stalled") are legitimate
            # coaching vocabulary; compound identifiers never are.
            for ident in identifiers:
                if "_" in ident:
                    assert ident not in value, (key, ident)
            assert not re.search(r"\b[a-z]+_[a-z_]+\b", value), (key, value)


# ── Failure is a distinct state ──────────────────────────────────────────

def test_failure_state_is_distinct_from_any_insight(app, client):
    js = _executable_js(_get(client, "/static/progress_insights.js"))
    unavailable = js.split("function _unavailable", 1)[1].split("\n  }", 1)[0]
    assert "_view(null)" in unavailable
    assert "progress.axis_unavailable" in unavailable
    assert ".catch(_unavailable)" in js
    model = _executable_js(_get(client, "/static/progress_presentation.js"))
    fallback = model.split("function unavailableAxisInsight", 1)[1].split("\n  }", 1)[0]
    assert "progress.axis_unavailable" in fallback
    assert "action: null" in fallback and "evidence: []" in fallback
    assert "baseline" not in fallback


def test_status_is_never_carried_by_colour_alone(app, client):
    js = _executable_js(_get(client, "/static/progress_insights.js"))
    render = js.split("function _render(", 1)[1].split("\n  }\n", 1)[0]
    assert "setAttribute('data-status'" in render
    assert "interp.textContent" in render


# ── Ownership boundary with progress.js ──────────────────────────────────

def test_progress_js_hands_off_and_keeps_no_insight_rendering(app, client):
    progress_js = _get(client, "/static/progress.js")
    assert "FitXAxisInsights" in progress_js
    for gone in ("/api/progress/insights", "insight-card", "ic-tone",
                 "progress.tone_", "loadInsights", "progress.axis_"):
        assert gone not in progress_js, gone


def test_scripts_load_in_dependency_order(app, client, make_user, login):
    html = _progress_html(client, make_user, login, "axuiscripts")
    order = [html.index(f"/static/{name}") for name in (
        "progress_presentation.js", "progress_insights.js", "progress.js")]
    assert order == sorted(order)


def test_a_successful_checkin_refreshes_the_insight(app, client):
    progress_js = _get(client, "/static/progress.js")
    load_progress = progress_js.split("function loadProgress", 1)[1].split("\n}", 1)[0]
    assert "loadAxisInsights()" in load_progress
    assert "loadProgress();" in progress_js.split("async function submitCheckin", 1)[1]
