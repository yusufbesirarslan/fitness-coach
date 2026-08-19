"""AXIS INSIGHTS frontend guards (Progress Redesign PR3).

The rule these enforce — *the browser translates a server-selected code and
decides nothing* — is about what the files may **contain**, not about one
rendered string, so most of this reads `static/progress_insights.js` and
`templates/progress.html` structurally. Same approach as PR2's guards in
`tests/test_progress_ui.py`, and the same reason.

Three-way symmetry is the load-bearing part: server enum ↔ client table ↔
locale catalog. A code the server can emit that the client cannot render, or a
client table entry with no copy behind it, is a bug in either direction.

    python -m pytest tests/test_progress_insights_ui.py -v
"""
import re

from app.services.progress_insights import (
    NEXT_MOVE_CODES,
    SLOT_STATUSES,
    WATCH_CODES,
    WORKING_CODES,
)

SLOT_IDS = ("ax-working", "ax-watch", "ax-next")

# code → the i18n key the client must map it to. Written out here rather than
# imported so the test is an independent statement of the contract; the
# symmetry assertions below prove it matches the server and the catalog.
WORKING_KEYS = {
    "training_progressing": "progress.axis_working_training_progressing",
    "training_steady": "progress.axis_working_training_steady",
    "training_consistent": "progress.axis_working_training_consistent",
}
WATCH_KEYS = {
    "build_consistency": "progress.axis_watch_build_consistency",
    "deload_due": "progress.axis_watch_deload_due",
    "plateau_detected": "progress.axis_watch_plateau_detected",
    "volume_trend_down": "progress.axis_watch_volume_trend_down",
    "strength_trend_down": "progress.axis_watch_strength_trend_down",
}
NEXT_MOVE_KEYS = {
    "build_baseline": "progress.axis_next_build_baseline",
    "prioritize_consistency": "progress.axis_next_prioritize_consistency",
    "deload": "progress.axis_next_deload",
    "maintain_and_consolidate": "progress.axis_next_maintain_and_consolidate",
    "progress_training": "progress.axis_next_progress_training",
    "maintain_current_training": "progress.axis_next_maintain_current_training",
}

# The permanent slot names. Server-rendered, because they are structure rather
# than payload — the section is three labelled slots before any fetch resolves.
TEMPLATE_KEYS = (
    "progress.axis_working_label", "progress.axis_watch_label",
    "progress.axis_next_label",
)

# Client-owned copy that is not keyed by a code: neutral states and the two
# evidence lines.
CLIENT_KEYS = (
    "progress.axis_working_empty", "progress.axis_working_insufficient",
    "progress.axis_watch_empty", "progress.axis_watch_insufficient",
    "progress.axis_next_empty", "progress.axis_next_insufficient",
    "progress.axis_unavailable", "progress.axis_next_volume_delta",
    "progress.axis_active_weeks",
)


def _insights_js(client):
    r = client.get("/static/progress_insights.js")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _executable_js(js):
    """Strip comments so a guard scans code, not the prose documenting it.

    The banner names the very patterns it forbids ("if (sessions >= n)"), which
    would otherwise trip every guard below.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(
        line for line in js.splitlines() if not line.strip().startswith("//"))


def _progress_html(client, make_user, login, username):
    make_user(username)
    login(username)
    r = client.get("/progress-page")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _rendered(html):
    """The document without HTML comments.

    The template comments explain what is deliberately NOT built ("no
    carousel", "no ranking"), which is prose about the guard rather than a
    violation of it. Only rendered markup is under test.
    """
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


# ── Structure: exactly three slots, always ───────────────────────────────

def test_axis_insights_renders_exactly_three_labelled_slots(
        app, client, make_user, login):
    html = _rendered(_progress_html(client, make_user, login, "axuislots"))
    section = html.split('id="ai-h"', 1)[1].split("</section>", 1)[0]

    assert section.count('class="ax-slot"') == 3
    for slot_id in SLOT_IDS:
        assert f'id="{slot_id}"' in section
        assert f'aria-labelledby="{slot_id}-h"' in section
        assert f'id="{slot_id}-h"' in section

    # In the prescribed order: WHAT'S WORKING → WATCH THIS → NEXT MOVE.
    positions = [section.index(f'id="{s}"') for s in SLOT_IDS]
    assert positions == sorted(positions)

    # Every slot ships both fillable slots, so the client never builds markup.
    assert section.count('data-slot="headline"') == 3
    assert section.count('data-slot="detail"') == 3


def test_the_three_slot_labels_are_visible_text_not_only_colour(
        app, client, make_user, login):
    """Rule 16: the slot names must be real headings in the document."""
    html = _progress_html(client, make_user, login, "axuilabels")
    from app.i18n import _CATALOG

    for key in ("progress.axis_working_label", "progress.axis_watch_label",
                "progress.axis_next_label"):
        assert _CATALOG["tr"][key] in html


def test_slots_survive_without_javascript(app, client, make_user, login):
    """The section is structural: three labelled slots ship in the markup.

    That is what makes "no carousel" and "exactly three" true regardless of the
    payload — including when the fetch never resolves.
    """
    html = _rendered(_progress_html(client, make_user, login, "axuinojs"))
    section = html.split('id="ai-h"', 1)[1].split("</section>", 1)[0]
    assert section.count("<article") == 3
    assert "data-status=" not in section          # written by JS from the payload only


def test_no_carousel_tabs_or_chart_is_introduced(app, client, make_user, login):
    """Rule 17 + §3.17: structurally visible, no horizontal interaction."""
    html = _rendered(_progress_html(client, make_user, login, "axuinocarousel"))
    for banned in ('id="insight-row"', 'class="tab-bar"', 'data-action="switchTab"',
                   "<canvas", "chart.umd", "carousel", "swiper"):
        assert banned not in html, banned

    js = _executable_js(_insights_js(client))
    for banned in ("Chart(", "canvas", "scrollLeft", "switchTab", "carousel",
                   "heatmap"):
        assert banned not in js, banned

    css = client.get("/static/progress.css").get_data(as_text=True)
    section = css.split("4 · AXIS INSIGHTS", 1)[1].split("5 ·", 1)[0]
    assert "overflow-x" not in section
    assert "flex-direction: column" in section        # stacked, never a row


# ── The client decides nothing ───────────────────────────────────────────

def test_client_reads_only_the_canonical_axis_insights_endpoint(
        app, client, make_user, login):
    js = _executable_js(_insights_js(client))
    assert "/api/progress/axis-insights" in js
    # The surfaces it must NOT reach for: the legacy heuristics, the summary it
    # would have to re-interpret, or raw workout/achievement data.
    for banned in ("/api/progress/insights", "/api/progress/summary",
                   "/api/progress/workout", "/api/progress/achievements",
                   "/checkin-history"):
        assert banned not in js, banned


def test_client_contains_no_decision_threshold(app, client, make_user, login):
    """Rule 4 + §3.17: the forbidden shapes, scanned structurally.

    A comparison against any canonical field is exactly how a browser starts
    owning coaching logic, so none may exist — not even a "harmless" one.
    """
    js = _executable_js(_insights_js(client))

    for field in ("sessions", "active_weeks", "analyzed_weeks", "volume_delta_pct",
                  "week_focus", "volume_action", "intensity_action",
                  "performance_state", "consistency_state", "reason_code",
                  "trajectory", "weight"):
        assert not re.search(rf"{field}\s*(>=|<=|>|<)\s*", js), f"threshold on {field}"

    # No numeric comparison of ANY kind, and no arithmetic on the payload.
    assert not re.search(r"(>=|<=|>|<)\s*-?\d", js), "numeric comparison in client"
    for banned in ("* 100", "/ 100", "Math.round", "Math.abs", "toFixed",
                   "score", "adherence", "streak", "calorie"):
        assert banned not in js, banned

    # The one equality the file is allowed: "a hold renders no delta line".
    # It is a display tie-break, not a verdict about the number.
    assert js.count("=== 0") == 1


def test_client_maps_every_published_code_to_a_locale_key(
        app, client, make_user, login):
    """Three-way symmetry: server enum ↔ client table ↔ catalog."""
    from app.i18n import _CATALOG

    js = _insights_js(client)
    for table in (WORKING_KEYS, WATCH_KEYS, NEXT_MOVE_KEYS):
        for code, key in table.items():
            assert f"{code}: '{key}'" in js, f"{code} is not mapped in the client"
            for locale in ("en", "tr"):
                assert _CATALOG[locale].get(key), f"{key} missing from {locale}"

    for key in CLIENT_KEYS:
        assert key in js, f"{key} is not used by the client"
        for locale in ("en", "tr"):
            assert _CATALOG[locale].get(key), f"{key} missing from {locale}"

    for key in TEMPLATE_KEYS:
        for locale in ("en", "tr"):
            assert _CATALOG[locale].get(key), f"{key} missing from {locale}"


def test_client_tables_match_the_server_contract_exactly(app, client):
    """A code the server can emit that the client cannot render is a bug."""
    assert set(WORKING_KEYS) == set(WORKING_CODES)
    assert set(WATCH_KEYS) == set(WATCH_CODES)
    assert set(NEXT_MOVE_KEYS) == set(NEXT_MOVE_CODES)


def test_client_handles_every_server_slot_status(app, client, make_user, login):
    js = _insights_js(client)
    for status in SLOT_STATUSES:
        assert f"'{status}'" in js, status


def test_unknown_code_degrades_to_the_neutral_state_not_a_guess(
        app, client, make_user, login):
    """A code this build does not know must render empty, never invent copy."""
    js = _executable_js(_insights_js(client))
    renderer = js.split("function _renderSlot", 1)[1].split("\n  }", 1)[0]
    # The available branch only renders when the lookup produced copy...
    assert "if (headline)" in renderer
    # ...and every other path falls through to the slot's own empty copy.
    assert "config.empty" in renderer


def test_locale_keys_are_symmetric_across_en_and_tr(app, client):
    """§3.18: EN/TR added together, never one-sided."""
    from app.i18n import _CATALOG

    en = {k for k in _CATALOG["en"] if k.startswith("progress.axis_")}
    tr = {k for k in _CATALOG["tr"] if k.startswith("progress.axis_")}
    assert en == tr and en
    for key in en:
        assert _CATALOG["en"][key] != _CATALOG["tr"][key] or key.isupper(), key


def test_copy_is_not_diagnostic_or_judgemental(app, client):
    """§3.18: coach-like, never a verdict on the person."""
    from app.i18n import _CATALOG

    banned = ("failing", "bad ", "worse", "overtrained", "medical", "başarısız",
              "kötü", "hasta", "aşırı antrenman")
    for locale in ("en", "tr"):
        for key, value in _CATALOG[locale].items():
            if not key.startswith("progress.axis_"):
                continue
            for word in banned:
                assert word not in value.lower(), f"{key}: {word}"


# ── Failure is a distinct state ──────────────────────────────────────────

def test_failure_state_is_distinct_from_the_empty_state(
        app, client, make_user, login):
    """Rule 7 on the client too: a failed fetch is not "nothing to watch"."""
    js = _executable_js(_insights_js(client))
    unavailable = js.split("function _unavailable", 1)[1].split("\n  }", 1)[0]

    assert "progress.axis_unavailable" in unavailable
    assert "'unavailable'" in unavailable
    # It must not borrow the reassuring empty copy.
    for empty_key in ("progress.axis_watch_empty", "progress.axis_working_empty"):
        assert empty_key not in unavailable

    assert ".catch(_unavailable)" in js


def test_status_is_never_carried_by_colour_alone(app, client, make_user, login):
    """Rule 16 / WCAG 1.4.1: the accent and the text are written together."""
    js = _executable_js(_insights_js(client))
    render = js.split("function _render(", 1)[1].split("\n  }", 1)[0]
    assert "setAttribute('data-status'" in render
    assert "head.textContent" in render

    css = client.get("/static/progress.css").get_data(as_text=True)
    for status in ("available", "unavailable"):
        assert f'.ax-slot[data-status="{status}"]' in css


# ── Ownership boundary with progress.js ──────────────────────────────────

def test_progress_js_hands_off_and_keeps_no_insight_rendering(
        app, client, make_user, login):
    """One owner for the three-slot contract."""
    progress_js = client.get("/static/progress.js").get_data(as_text=True)
    assert "FitXAxisInsights" in progress_js
    # The legacy list renderer is gone from this file entirely.
    for gone in ("/api/progress/insights", "insight-card", "ic-tone",
                 "progress.tone_", "loadInsights"):
        assert gone not in progress_js, gone


def test_both_scripts_are_loaded_and_the_module_is_defined_first(
        app, client, make_user, login):
    html = _progress_html(client, make_user, login, "axuiscripts")
    assert "/static/progress_insights.js" in html
    assert html.index("/static/progress_insights.js") < html.index("/static/progress.js")


def test_a_successful_checkin_refreshes_the_insights(app, client, make_user, login):
    """Rule 19: the existing refresh mechanism picks the new section up."""
    progress_js = client.get("/static/progress.js").get_data(as_text=True)
    load_progress = progress_js.split("function loadProgress", 1)[1].split("\n}", 1)[0]
    assert "loadAxisInsights()" in load_progress
    # submitCheckin already calls loadProgress(); the check-in POST itself is
    # untouched.
    assert "loadProgress();" in progress_js.split("async function submitCheckin", 1)[1]
