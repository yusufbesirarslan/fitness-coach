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
    "progress.physique_limited_label",
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
                "progress.physique_limited_label",
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
    assert "progress.physique_limited_label" in js


def test_stale_comparison_copy_is_present(app, client):
    js = _executable_js(_js(client))
    assert "progress.physique_latest_saved" in js
    assert "current_is_latest_check" in js


def test_other_progress_sections_remain_in_the_page(
        app, client, make_user, login):
    html = _progress_html(client, make_user, login, "ppuikeep")
    for anchor in ("ps-h", "wc-h", "ai-h", "pp-h", "ph-h"):
        assert f'id="{anchor}"' in html
    assert 'id="insight-list"' in html
    assert 'id="history-list"' in html


def test_gallery_action_remains_reachable(app, client):
    js = _executable_js(_js(client))
    assert "/pump-check-gallery" in js
    assert "progress.physique_view_all" in js
    assert "progress.physique_add_check" in js
