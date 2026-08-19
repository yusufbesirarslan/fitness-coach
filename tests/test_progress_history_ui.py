"""PROGRESS HISTORY frontend guards (Progress Redesign PR5).

    python -m pytest tests/test_progress_history_ui.py -v
"""
import re

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

CLIENT_KEYS = (
    "progress.history_empty_title",
    "progress.history_empty_desc",
    "progress.history_no_weight",
    "progress.history_no_delta",
    "progress.history_has_more",
    "progress.history_unavailable",
    "progress.history_asof",
    "progress.history_expand",
    "progress.history_performance",
    "progress.history_consistency",
    "progress.history_body",
    "progress.history_window",
    "progress.history_window_range",
    "progress.history_sessions",
    "progress.history_weeks_active",
    "progress.history_weight",
    "progress.history_delta",
    "progress.history_volume",
)


def _js(client):
    r = client.get("/static/progress_history.js")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _progress_js(client):
    r = client.get("/static/progress.js")
    assert r.status_code == 200
    return r.get_data(as_text=True)


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
    assert "/static/progress_history.js" in html
    assert _CATALOG["tr"]["progress.history_label"] in html


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
    assert "empty: true" in js and "available: true" in js
    for table in (TRAJECTORY_KEYS, PERFORMANCE_KEYS, CONSISTENCY_KEYS):
        for state, key in table.items():
            assert f"{state}: '{key}'" in js, state
            for locale in ("en", "tr"):
                assert _CATALOG[locale].get(key), f"{key} missing from {locale}"
    for key in CLIENT_KEYS:
        assert key in js, key
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


def test_expand_control_is_a_real_button(app, client, make_user, login):
    js = _js(client)
    assert "button" in js
    assert "aria-expanded" in js
    assert "aria-controls" in js
    assert "hist-trigger" in js
    css = client.get("/static/progress.css").get_data(as_text=True)
    assert ".hist-trigger:focus-visible" in css
    for state in TRAJECTORY_STATES:
        assert f'.hist-item[data-state="{state}"]' in css


def test_unavailable_is_isolated_and_not_empty(app, client, make_user, login):
    js = _js(client)
    unavailable = js.split("function _unavailable", 1)[1].split("\n  }", 1)[0]
    assert "progress.history_unavailable" in unavailable
    assert "history_empty" not in unavailable
    assert "building_baseline" not in unavailable


def test_ia_five_sections_unchanged(app, client, make_user, login):
    html = _progress_html(client, make_user, login, "phuiia")
    for anchor in ("ps-h", "wc-h", "ai-h", "pp-h", "ph-h"):
        assert f'id="{anchor}"' in html
    assert html.count('class="wc-card"') == 3
    assert 'id="ax-working"' in html
    assert 'id="physique-body"' in html


def test_xss_payload_cannot_become_html(app, client, make_user, login):
    js = _executable_js(_js(client))
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js
