"""Progress V2 PR5 — static contract for the final hardening pass.

Structural guards that keep the PR5 fixes from regressing without a browser.
The integrated geometry / accessibility matrix is
tests/test_progress_v2_final_browser.py.

    python -m pytest tests/test_progress_v2_final_ui.py -v
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


def _read(path):
    return path.read_text(encoding="utf-8")


def _css():
    return re.sub(r"/\*.*?\*/", "", _read(STATIC / "progress.css"), flags=re.S)


def _rule(css, selector):
    match = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def _locale(code):
    return json.loads(_read(ROOT / "locales" / f"{code}.json"))


# ── hidden always hides ─────────────────────────────────────────────────────

def test_hidden_attribute_always_wins_on_the_progress_page():
    """PR3 shipped a component rule that overrode `hidden`; PR5 found the same
    defect on #ps-evidence. One page-scoped rule makes `hidden` win over every
    flex/grid slot rule, and no per-component copy is left to drift."""
    css = _css()
    assert re.search(r"\.progress-main \[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", css)
    assert css.count("[hidden]") == 1, "one owner for the hidden rule"


# ── retired structures stay retired ─────────────────────────────────────────

RETIRED = (
    # PR3: the three equal Axis cards
    "ax-slot", "ax-slots", "ax-slot-label", "ax-headline", "ax-detail",
    # PR4: per-row drilldown, colour rails, facts table
    "hist-asof", "hist-detail", "hist-fact", "hist-facts", "hist-fact-term",
    "hist-fact-value", "hist-traj", "hist-trigger", "hist-weight",
    # PR4: bordered limited-comparison block
    "pp-limited",
)


@pytest.mark.parametrize("name", RETIRED)
def test_retired_progress_selectors_have_no_style_or_consumer(name):
    pattern = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
    assert not pattern.search(_css()), name
    for path in ("progress.js", "progress_physique.js", "progress_insights.js",
                 "progress_presentation.js"):
        assert not pattern.search(_read(STATIC / path)), (name, path)
    for partial in TEMPLATES.glob("_progress_*.html"):
        assert not pattern.search(_read(partial)), (name, partial.name)


def test_every_progress_component_class_has_a_consumer():
    """No dead Progress CSS: each section-prefixed class is rendered by a
    Progress template or script."""
    css = _css()
    classes = set(re.findall(r"\.((?:ps|wc|tr|ax|pp|hist|prog)-[\w-]+)", css))
    sources = "".join(_read(p) for p in TEMPLATES.glob("*progress*.html"))
    sources += "".join(_read(STATIC / n) for n in (
        "progress.js", "progress_physique.js", "progress_insights.js"))
    dead = sorted(c for c in classes
                  if not re.search(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])", sources))
    assert dead == []


# ── typography: no dashboard-style labels ───────────────────────────────────

@pytest.mark.parametrize("selector", [".pp-area", ".pp-reliability", ".pp-list-h",
                                      ".ax-action-label", ".hist-summary", ".hist-meta"])
def test_progress_content_labels_are_sentence_case_body_type(selector):
    """PR4 P2: "AREA: …", "LIMITED COMPARISON" and list headers were all-caps
    tracked labels. Section headings (.sec-label, shared) stay as they are;
    content inside the sections reads in the body voice."""
    body = _rule(_css(), selector)
    assert "uppercase" not in body, selector
    assert "tracking" not in body, selector
    assert "font-display" not in body, selector


def test_physique_list_headers_do_not_skip_a_heading_level():
    js = _read(STATIC / "progress_physique.js")
    assert "'h4'" not in js
    assert js.count("_text('h3'") == 2


# ── interaction: one filled primary, subordinate secondaries ────────────────

def test_physique_action_is_the_shared_secondary_button():
    """The Physique action used a bespoke blue-tinted pill that out-shouted the
    Axis Insight CTA. It is now .btn-ghost (44px floor, shared focus/hover),
    and progress.css only spaces it."""
    js = _read(STATIC / "progress_physique.js")
    assert "a.className = 'btn-ghost pp-link';" in js
    body = _rule(_css(), ".pp-link")
    assert set(p.split(":")[0].strip() for p in body.split(";") if p.strip()) == {"margin-top"}


def test_the_page_has_one_filled_primary_action():
    markup = "".join(_read(p) for p in TEMPLATES.glob("_progress_*.html")
                     if p.name != "_progress_checkin_sheet.html")
    assert markup.count("btn-volt") == 1
    for name in ("progress.js", "progress_physique.js", "progress_insights.js"):
        assert "btn-volt" not in _read(STATIC / name), name


def test_same_day_disclosure_hit_area_is_structural():
    """PR4 P2: `.hist-more` reached 44px through `margin: -12px 0`."""
    body = _rule(_css(), ".hist-more")
    assert re.search(r"min-height:\s*44px", body)
    assert "margin" not in body
    assert not re.search(r"margin[^;]*-\d", _css().split(".hist-more")[1].split("}")[0])


# ── colour semantics ────────────────────────────────────────────────────────

def test_trend_bars_are_neutral_not_brand_blue():
    css = _css()
    for selector in (".tr-bar", ".tr-bar-latest"):
        assert "primary" not in _rule(css, selector), selector


def test_failed_current_state_is_quiet_and_neutral():
    css = _css()
    assert "border-color" in _rule(css, '.ps-card[data-status="unavailable"]') or \
        "border-left-color" in _rule(css, '.ps-card[data-status="unavailable"]')
    headline = _rule(css, '.ps-card[data-status="unavailable"] .ps-state')
    assert "var(--font-body)" in headline


# ── accessibility: live regions ─────────────────────────────────────────────

@pytest.mark.parametrize("partial, container", [
    ("_progress_recent_checkins.html", "history-list"),
    ("_progress_physique.html", "physique-body"),
])
def test_secondary_sections_are_busy_not_live(partial, container):
    """PR4 P2: #history-list was aria-live, so "Show earlier check-ins"
    announced every appended row. Both secondary sections are aria-busy until
    their one read settles; disclosures speak through aria-expanded."""
    html = _read(TEMPLATES / partial)
    tag = re.search(r'<div id="%s"[^>]*>' % container, html).group(0)
    assert "aria-live" not in tag
    assert 'aria-busy="true"' in tag


def test_busy_is_cleared_on_every_settle_path():
    js = _read(STATIC / "progress.js")
    history = js.split("BEGIN progress history module")[1]
    assert history.count("removeAttribute('aria-busy')") == 2   # render + unavailable
    assert "container.removeAttribute('aria-busy')" in js       # module missing
    physique = _read(STATIC / "progress_physique.js")
    assert ".then(function () { box.removeAttribute('aria-busy'); });" in physique


# ── typographic minus ───────────────────────────────────────────────────────

def test_signed_numbers_use_the_typographic_minus():
    presentation = _read(STATIC / "progress_presentation.js")
    assert "var MINUS = '\\u2212';" in presentation
    insights = _read(STATIC / "progress_insights.js")
    assert ".replace('-', '\\u2212')" in insights


# ── localization ────────────────────────────────────────────────────────────

def test_failed_metric_note_is_not_the_empty_state_copy():
    en, tr = _locale("en"), _locale("tr")
    for catalog in (en, tr):
        assert "progress.card_nodata" not in catalog
        assert catalog["progress.card_unavailable"]
    assert "copy('progress.card_unavailable')" in _read(STATIC / "progress_presentation.js")


def test_removed_key_has_no_live_consumer():
    for path in list(STATIC.glob("*.js")) + list(TEMPLATES.glob("**/*.html")) + \
            list((ROOT / "app").rglob("*.py")):
        assert "progress.card_nodata" not in _read(path), path


def test_progress_locale_keys_are_symmetric():
    en = {k for k in _locale("en") if k.startswith("progress.")}
    tr = {k for k in _locale("tr") if k.startswith("progress.")}
    assert en == tr
