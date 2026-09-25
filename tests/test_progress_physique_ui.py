"""PHYSIQUE PROGRESS frontend guards (Progress Redesign PR4).

    python -m pytest tests/test_progress_physique_ui.py -v
"""
import json
import re

from app.i18n import _CATALOG
from app.services.progress_physique import (
    BODY_REGIONS,
    STATES,
)

REGION_KEYS = {
    "full_body": "progress.physique_region_full_body",
    "upper_body": "progress.physique_region_upper_body",
    "lower_body": "progress.physique_region_lower_body",
    "back": "progress.physique_region_back",
    "arms": "progress.physique_region_arms",
    "legs": "progress.physique_region_legs",
}

CLIENT_KEYS = (
    "progress.physique_empty_title",
    "progress.physique_empty_desc",
    "progress.physique_add_check",
    "progress.physique_empty_cta",
    "progress.physique_legacy_note",
    "progress.physique_single_title",
    "progress.physique_single_desc",
    "progress.physique_history_title",
    "progress.physique_history_desc",
    "progress.physique_area_label",
    "progress.physique_selected_area",
    "progress.physique_baseline",
    "progress.physique_current",
    "progress.physique_latest_saved",
    "progress.physique_comparability_comparable",
    "progress.physique_comparability_limited",
    "progress.physique_comparability_not_comparable",
    "progress.physique_not_comparable_title",
    "progress.physique_unavailable",
    "progress.physique_view_all",
    "progress.physique_observed",
    "progress.physique_alt_baseline",
    "progress.physique_alt_current",
)


def _js(client):
    r = client.get("/static/progress_physique.js")
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


def _rendered(html):
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def test_physique_heading_is_preserved(app, client, make_user, login):
    html = _progress_html(client, make_user, login, "ppuih")
    assert 'id="pp-h"' in html
    assert _CATALOG["tr"]["progress.physique_label"] in html
    assert 'id="physique-body"' in html


def test_section_stays_one_block_without_carousel_or_chart(
        app, client, make_user, login):
    html = _rendered(_progress_html(client, make_user, login, "ppuistruct"))
    section = html.split('id="pp-h"', 1)[1].split("</section>", 1)[0]
    assert "<canvas" not in section
    assert "carousel" not in section.lower()
    assert "swiper" not in section.lower()
    js = _executable_js(_js(client))
    for banned in ("Chart(", "carousel", "swiper", "scrollLeft", "heatmap"):
        assert banned not in js, banned


def test_script_is_loaded_before_progress_js(app, client, make_user, login):
    html = _progress_html(client, make_user, login, "ppuiscript")
    assert "/static/progress_physique.js" in html
    assert html.index("/static/progress_physique.js") < html.index(
        "/static/progress.js")


def test_progress_js_hands_off_and_keeps_no_physique_rendering(
        app, client, make_user, login):
    progress_js = client.get("/static/progress.js").get_data(as_text=True)
    assert "FitXPhysiqueProgress" in progress_js
    assert "/pump-check-gallery/data" not in progress_js
    assert "PHYSIQUE_THUMBS" not in progress_js
    load_progress = progress_js.split("function loadProgress", 1)[1].split(
        "\n}", 1)[0]
    assert "loadPhysique()" in load_progress


def test_client_reads_only_the_canonical_physique_endpoint(app, client):
    js = _executable_js(_js(client))
    assert "/api/progress/physique" in js
    for banned in ("/api/progress/summary", "/api/progress/axis-insights",
                   "/api/progress/insights", "/api/v1/pump-checks",
                   "/api/v1/pump-check-comparisons"):
        assert banned not in js, banned


def test_client_never_decides_physique_progress(app, client):
    js = _executable_js(_js(client))
    for banned in ("improved", "grew", "growth", "body fat", "body-fat",
                   "score", "percent", "bigger", "smaller"):
        assert banned not in js.lower(), banned
    assert "limited → comparable" not in js
    assert "not_comparable → limited" not in js


def test_untrusted_prose_is_assigned_via_text_content(app, client):
    js = _executable_js(_js(client))
    assert "textContent" in js
    # Provider strings must never be concatenated into HTML.
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js


def test_malicious_markup_would_be_text_not_html(app, client):
    """The renderer has no innerHTML path a stored string could ride."""
    js = _js(client)
    assert "createElement" in js
    assert ".textContent =" in js or "node.textContent" in js
    assert "<script>" not in _executable_js(js)


def test_all_states_have_explicit_copy(app, client):
    js = _executable_js(_js(client))
    for state in STATES:
        assert state in js
    for key in ("progress.physique_empty_title",
                "progress.physique_single_title",
                "progress.physique_history_title",
                "progress.physique_not_comparable_title",
                "progress.physique_comparability_limited",
                "progress.physique_unavailable"):
        assert key in js


def test_region_labels_match_canonical_vocabulary():
    assert set(REGION_KEYS) == set(BODY_REGIONS)
    for locale in ("en", "tr"):
        for key in list(REGION_KEYS.values()) + list(CLIENT_KEYS):
            assert key in _CATALOG[locale], f"{locale} missing {key}"


def test_locale_catalogs_are_symmetric():
    en = {k: v for k, v in _CATALOG["en"].items() if k.startswith("progress.physique_")}
    tr = {k: v for k, v in _CATALOG["tr"].items() if k.startswith("progress.physique_")}
    assert set(en) == set(tr)


def test_region_control_is_keyboard_and_aria_described(app, client):
    js = _executable_js(_js(client))
    assert 'role", "radiogroup"' in js or "radiogroup" in js
    assert "aria-checked" in js
    assert "ArrowRight" in js
    assert "tabindex" in js


def test_images_have_meaningful_alt_keys(app, client):
    js = _executable_js(_js(client))
    assert "progress.physique_alt_baseline" in js
    assert "progress.physique_alt_current" in js
    assert "progress.physique_alt_check" in js


def test_comparability_is_textual_not_colour_only(app, client):
    js = _executable_js(_js(client))
    assert "progress.physique_comparability_limited" in js
    assert "progress.physique_comparability_not_comparable" in js
    # V2 PR4: the reliability is said once, in words, in a neutral colour —
    # no duplicate "Limited comparison" line and no warning-coloured rail.
    assert "progress.physique_limited_label" not in js
    css = client.get("/static/progress.css").get_data(as_text=True)
    physique = css.split("/* ── 5 · PHYSIQUE", 1)[1].split("/* ── 6 ·", 1)[0]
    assert "--color-warning" not in physique
    assert "pp-limited" not in physique


def test_unknown_comparability_is_not_a_client_vocabulary(app, client):
    js = _executable_js(_js(client))
    assert "progress.physique_comparability_unknown" not in js
    assert "unknown:" not in js
    for locale in ("en", "tr"):
        assert "progress.physique_comparability_unknown" not in _CATALOG[locale]
    # A drifted comparability token must not render comparison_available
    # content; the client degrades to the isolated unavailable state.
    assert "progress.physique_unavailable" in js
    render = js.split("function _render", 1)[1].split("function load", 1)[0]
    assert "COMPARABILITY_LABELS" in render
    assert "_unavailable" in render


def test_stale_comparison_copy_is_present(app, client):
    js = _executable_js(_js(client))
    assert "progress.physique_latest_saved" in js
    assert "current_is_latest_check" in js


def test_other_progress_sections_remain_in_the_page(
        app, client, make_user, login):
    html = _progress_html(client, make_user, login, "ppuikeep")
    for anchor in ("ps-h", "tr-h", "ai-h", "pp-h", "ph-h"):
        assert f'id="{anchor}"' in html
    assert 'id="ax-card"' in html          # V2 PR3: one Axis Insight surface
    assert 'id="history-list"' in html


def test_gallery_action_remains_reachable(app, client):
    js = _executable_js(_js(client))
    assert "/pump-check-gallery" in js
    assert "progress.physique_view_all" in js
    assert "progress.physique_add_check" in js


# ── V2 PR4: compact, actionable empty state; quiet secondary section ────────

RETIRED_KEYS = (
    "progress.physique_limited_label",
    "progress.physique_stable",
    "progress.physique_focus",
    "progress.physique_recent",
    "progress.physique_alt",
)


def _function(js, name):
    return js.split(f"function {name}", 1)[1].split("\n  }\n", 1)[0]


def test_empty_state_is_compact_with_exactly_one_action(app, client):
    js = _executable_js(_js(client))
    empty = _function(js, "_empty")
    # No shared bordered .empty-state card, no placeholder illustration.
    assert "empty-state" not in empty
    assert "pp-empty" in empty
    assert "<svg" not in empty and "img" not in empty
    # One action on either branch: Training (where a web Pump Check is
    # taken) or, when only legacy Pump Checks exist, the gallery.
    branches = empty.split("} else {")
    assert len(branches) == 2
    for branch in branches:
        assert branch.count("_link(") + branch.count("_galleryLink(") == 1, branch
    assert "progress.physique_empty_cta" in empty
    assert "TRAINING" in empty and "'/training'" in js


def test_empty_copy_is_purposeful_and_claims_no_analysis():
    for locale in ("en", "tr"):
        title = _CATALOG[locale]["progress.physique_empty_title"]
        desc = _CATALOG[locale]["progress.physique_empty_desc"]
        assert title and desc
        text = (title + " " + desc).lower()
        for banned in ("no data", "veri yok", "analy", "analiz", "score", "skor"):
            assert banned not in text, (locale, banned)
    assert _CATALOG["en"]["progress.physique_empty_title"] == "Build your visual timeline"


def test_physique_is_visually_quieter_than_the_intelligence_layer(app, client):
    css = client.get("/static/progress.css").get_data(as_text=True)
    physique = css.split("/* ── 5 · PHYSIQUE", 1)[1].split("/* ── 6 ·", 1)[0]
    # Normal UI typography, no glow, no surface card of its own.
    assert "--font-display" not in physique
    assert "box-shadow: var(--glow" not in physique
    assert ".pp-empty {" in physique
    empty_rule = physique.split(".pp-empty {", 1)[1].split("}", 1)[0]
    assert "border" not in empty_rule and "background" not in empty_rule
    # Photos are bounded thumbnails, never full-column frames.
    grid = physique.split(".pp-strip {", 1)[1].split("}", 1)[0]
    assert "minmax(0, 140px)" in grid
    assert ".prog-section .empty-state" not in css


def test_images_stay_lazy_and_reserve_their_box(app, client):
    js = _executable_js(_js(client))
    figure = _function(js, "_figure")
    assert "img.loading = 'lazy'" in figure
    assert "img.decoding = 'async'" in figure
    assert "img.width" in figure and "img.height" in figure
    # Only the server's signed https URL is ever used.
    assert "_safeHttpUrl(url)" in figure


def test_retired_physique_keys_have_no_consumer(app, client):
    js = _js(client)
    for key in RETIRED_KEYS:
        assert f"'{key}'" not in js, key
        for locale in ("en", "tr"):
            assert key not in _CATALOG[locale], (locale, key)


def test_stable_and_focus_lists_are_not_rendered_on_progress(app, client):
    js = _executable_js(_js(client))
    assert "stable_areas" not in js
    assert "focus_areas" not in js
    assert "observed_changes" in js
