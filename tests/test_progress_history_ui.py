"""PROGRESS HISTORY frontend guards (Progress Redesign PR5).

    python -m pytest tests/test_progress_history_ui.py -v
"""
import re

from markupsafe import escape

from app.i18n import _CATALOG
from app.services.progress_history import STATES
from app.services.progress_summary import (
    CONSISTENCY_STATES,
    PERFORMANCE_STATES,
    TRAJECTORY_STATES,
)

TRAJECTORY_KEYS = {
    "building_baseline": "progress.traj_building_baseline",
    "on_track": "progress.traj_on_track",
    "needs_attention": "progress.traj_needs_attention",
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

# Keys the renderer block names itself (section-level copy).
CLIENT_KEYS = (
    "progress.history_empty_title",
    "progress.history_empty_desc",
    "progress.history_has_more",
    "progress.history_unavailable",
    "progress.history_show_earlier",
    "progress.history_show_fewer",
)
# Keys the row view model names (static/progress_presentation.js).
VIEW_KEYS = (
    "progress.history_weight",
    "progress.history_delta",
    "progress.history_no_change",
    "progress.history_updates",
)
# V2 PR4 retired the per-row drilldown (window, performance, consistency,
# body facts) and the inline trend words it used. Nothing may name them.
RETIRED_KEYS = (
    "progress.history_asof",
    "progress.history_expand",
    "progress.history_performance",
    "progress.history_consistency",
    "progress.history_body",
    "progress.history_window",
    "progress.history_window_range",
    "progress.history_sessions",
    "progress.history_weeks_active",
    "progress.history_volume",
    "progress.history_no_weight",
    "progress.history_no_delta",
    "progress.history_sub",
    "progress.trend_up",
    "progress.trend_flat",
    "progress.trend_down",
)


# Since Progress V2 PR1 the history consumer ships INSIDE progress.js (the
# page keeps its pre-V2 static request count), as one self-contained IIFE
# between these markers. Every history guard below runs on that block alone,
# and every controller guard on progress.js WITHOUT it, so neither side can
# hide behind the other.
_HISTORY_BEGIN = "/* ═══ BEGIN progress history module"
_HISTORY_END = "/* ═══ END progress history module ═══ */"


def _progress_source(client):
    r = client.get("/static/progress.js")
    assert r.status_code == 200
    src = r.get_data(as_text=True)
    assert src.count(_HISTORY_BEGIN) == 1 and src.count(_HISTORY_END) == 1
    begin, end = src.index(_HISTORY_BEGIN), src.index(_HISTORY_END)
    assert begin < end
    return src, begin, end + len(_HISTORY_END)


def _js(client):
    src, begin, end = _progress_source(client)
    return src[begin:end]


def _progress_js(client):
    src, begin, end = _progress_source(client)
    return src[:begin] + src[end:]


def _executable_js(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(
        line for line in js.splitlines() if not line.strip().startswith("//"))


def _progress_html(client, make_user, login, username):
    make_user(username)
    login(username)
    r = client.get("/progress-page")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_history_heading_and_module_are_wired(app, client, make_user, login):
    html = _progress_html(client, make_user, login, "phuiwire")
    assert 'id="ph-h"' in html
    assert 'id="history-list"' in html
    assert "/static/progress.js" in html
    # The history consumer is no longer a separate request (see _js).
    assert "/static/progress_history.js" not in html
    # Rendered through Jinja autoescape ("SON CHECK-IN'LER" → &#39;).
    assert str(escape(_CATALOG["tr"]["progress.recent_checkins_label"])) in html


def test_progress_js_only_delegates_history(app, client, make_user, login):
    js = _executable_js(_progress_js(client))
    assert "FitXProgressHistory" in js
    assert "/checkin-history" not in js
    assert "HISTORY_LIMIT" not in js
    assert "renderHistory" not in js
    assert "function loadHistory" in _progress_js(client)


def test_client_reads_only_the_canonical_history_endpoint(app, client, make_user, login):
    js = _executable_js(_js(client))
    assert "/api/progress/history" in js
    for banned in ("/checkin-history", "/api/progress/summary",
                   "/api/progress/axis-insights", "/api/progress/insights",
                   "/api/progress/physique", "/api/progress/workout",
                   "/api/progress/achievements"):
        assert banned not in js, banned


def test_client_maps_every_published_state(app, client, make_user, login):
    js = _js(client)
    assert set(STATES) == {"empty", "available"}
    # V2 PR4: the renderer holds NO state table at all. The row view model
    # (grouping + state → key) is buildHistoryView in progress_presentation.js,
    # which reuses the page's shared tables — one wording per state.
    assert "P.buildHistoryView" in js
    tables = client.get("/static/progress_presentation.js").get_data(as_text=True)
    view = tables.split("function buildHistoryView", 1)[1].split("\n  }\n", 1)[0]
    assert "'empty'" in view and "'available'" in view
    helper = tables.split("function historyEntry", 1)[1].split("\n  }\n", 1)[0]
    assert "TRAINING_STATE" in helper and "TRAJECTORY" in helper
    for table in (TRAJECTORY_KEYS, PERFORMANCE_KEYS, CONSISTENCY_KEYS):
        for state, key in table.items():
            assert f"{state}: '{key}'" in tables, state
            assert f"'{key}'" not in js, f"{key} is mapped twice"
            for locale in ("en", "tr"):
                assert _CATALOG[locale].get(key), f"{key} missing from {locale}"
    for name in ("TRAJECTORY", "TRAINING_STATE", "CONSISTENCY_STATE"):
        assert f"P.{name}" not in js, name
    for key in CLIENT_KEYS:
        assert key in js, key
        for locale in ("en", "tr"):
            assert _CATALOG[locale].get(key), f"{key} missing from {locale}"
    for key in VIEW_KEYS:
        assert f"'{key}'" in tables, key
        for locale in ("en", "tr"):
            assert _CATALOG[locale].get(key), f"{key} missing from {locale}"
    assert set(TRAJECTORY_KEYS) == set(TRAJECTORY_STATES)
    assert set(PERFORMANCE_KEYS) == set(PERFORMANCE_STATES)
    assert set(CONSISTENCY_KEYS) == set(CONSISTENCY_STATES)


def test_client_does_not_derive_delta_or_state(app, client, make_user, login):
    js = _executable_js(_js(client))
    for field in ("sessions", "active_weeks", "analyzed_weeks", "weight_delta_kg",
                  "weight_kg", "weeks", "volume_trend"):
        assert not re.search(rf"{field}\s*(>=|<=|>|<)\s*", js), field
    assert "kilo" not in js
    assert "HISTORY_LIMIT" not in js
    for banned in ("* 100", "/ 100", "score", "adherence", "streak",
                   "building_baseline", "on_track"):
        if banned in ("building_baseline", "on_track"):
            # Table keys are translations, not decisions — allowed in the
            # lookup tables only. A comparison against them is not.
            assert not re.search(rf"{banned}\s*(>=|<=|>|<|===|==)", js), banned
            continue
        assert banned not in js, banned
    assert "innerHTML" not in js
    assert "textContent" in js
    assert "createElement" in js


def test_disclosures_are_real_buttons_that_build_on_demand(app, client, make_user, login):
    """Two disclosures: a day's individual check-ins, and the earlier groups
    past the visible bound. Both are <button aria-expanded aria-controls>,
    and both ADD nodes when opened and REMOVE them when closed — nothing is
    kept as hidden markup (so nothing hidden sits in the a11y tree)."""
    js = _executable_js(_js(client))
    assert "document.createElement('button')" in js
    assert js.count("'aria-expanded'") >= 4       # set + read, per control
    assert js.count("'aria-controls'") == 2
    assert "hist-more" in js and "hist-toggle" in js
    assert "'hidden'" not in js and ".hidden" not in js
    assert "display = 'none'" not in js
    assert ".remove()" in js and "removeChild" in js
    css = client.get("/static/progress.css").get_data(as_text=True)
    assert ".hist-more:focus-visible" in css
    assert ".hist-toggle:focus-visible" in css


def test_rows_are_a_flat_semantic_list_with_dates_as_metadata(app, client):
    js = _executable_js(_js(client))
    # An ordered list of days; each date is a <time datetime> in the meta line.
    assert "document.createElement('ol')" in js
    assert "_text('time'" in js and "'datetime'" in js
    assert "hist-meta" in js and "hist-date" in js
    css = client.get("/static/progress.css").get_data(as_text=True)
    history = css.split("/* ── 6 · RECENT CHECK-INS", 1)[1].split("/* ── 7 ·", 1)[0]
    date_rule = history.split(".hist-date", 1)[1].split("}", 1)[0]
    # Dates are quiet metadata: not the display face, not the link colour.
    assert "--font-display" not in history
    assert "--color-primary)" not in date_rule
    # No coloured state rail per row, no per-row card.
    assert "data-state" not in history
    assert "--color-warning" not in history and "--color-success" not in history
    assert "border-radius" not in history.split(".hist-item {", 1)[1].split("}", 1)[0]


def test_unavailable_is_isolated_and_not_empty(app, client, make_user, login):
    js = _js(client)
    unavailable = js.split("function _unavailable", 1)[1].split("\n  }", 1)[0]
    assert "progress.history_unavailable" in unavailable
    assert "history_empty" not in unavailable
    assert "building_baseline" not in unavailable


def test_ia_five_sections_unchanged(app, client, make_user, login):
    html = _progress_html(client, make_user, login, "phuiia")
    for anchor in ("ps-h", "tr-h", "ai-h", "pp-h", "ph-h"):
        assert f'id="{anchor}"' in html
    assert html.count('class="wc-card"') == 3
    assert 'id="ax-card"' in html          # V2 PR3: one Axis Insight surface
    assert 'id="physique-body"' in html


def test_xss_payload_cannot_become_html(app, client, make_user, login):
    js = _executable_js(_js(client))
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js


def test_retired_history_keys_have_no_consumer_and_no_catalog_entry(client):
    shipped = "".join(
        client.get(path).get_data(as_text=True)
        for path in ("/static/progress.js", "/static/progress_presentation.js"))
    for key in RETIRED_KEYS:
        assert key not in shipped, key
        for locale in ("en", "tr"):
            assert key not in _CATALOG[locale], (locale, key)


def test_history_catalogs_are_symmetric():
    en = {k for k in _CATALOG["en"] if k.startswith("progress.history_")}
    tr = {k for k in _CATALOG["tr"] if k.startswith("progress.history_")}
    assert en == tr
