"""PR2 premium-visual-refinement contracts for Home, Nutrition, Training, Progress.

These guard the *structural* decisions PR2 made, not pixel aesthetics:

* `.card` carries its own padding (the Home dashboard had none),
* one metric type scale instead of per-page one-off font sizes,
* no emoji left acting as an interface icon on the four core surfaces,
* the week strip can tell Cuma from Cumartesi,
* the log FAB no longer stacks on the coach FAB's rail.

Deliberately NOT asserted: exact px values, colours, shadow strings — those are
design decisions that should be free to move without a test failing.
"""
from pathlib import Path
import json
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"

# The four PR2 surfaces plus the shared shell they all load.
CORE_TEMPLATES = ("index.html", "nutrition.html", "training.html", "progress.html")
CORE_SCRIPTS = ("nutrition.js", "training.js", "coach_widget.js")

# Pictographic ranges, minus the dingbats the app uses as typographic marks
# (✓ ✗ ★ ➕ →) which are not emoji icons.
_EMOJI = re.compile(
    r"[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    r"\U0001F900-\U0001FAFF\U00002600-\U000026FF]"
)

# The same pictograms written as escapes, which the ranges above cannot see
# in source.
_ESCAPED_EMOJI = re.compile(r"\\u[{]?1F[0-9A-Fa-f]{3}")


def _read(path):
    return path.read_text(encoding="utf-8")


def _css(name):
    return _read(STATIC / name)


def _rule(css, selector):
    """Return the body of the first rule whose selector matches exactly."""
    match = re.search(
        r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M
    )
    assert match, f"missing rule for {selector}"
    return match.group(1)


# ── Shared design system ──────────────────────────────────────────────────


def test_card_component_ships_its_own_padding():
    """Home's eight `.card`s rendered flush to the border without this."""
    body = _rule(_css("components.css"), ".card")
    assert re.search(r"padding:\s*var\(--space-\d+\)", body), (
        ".card must carry a default padding so consumers cannot forget one"
    )
    assert ".card-flush" in _css("components.css"), (
        "cards that manage their own insets need a documented opt-out"
    )


def test_home_dashboard_cards_do_not_reintroduce_inline_padding():
    """The fix is the component default, not eight inline styles."""
    html = _read(TEMPLATES / "index.html")
    for match in re.finditer(r'<section class="card[^"]*"([^>]*)>', html):
        assert "padding" not in match.group(1), (
            "Home cards must inherit .card padding, not re-declare it inline"
        )


def test_metric_scale_tokens_exist_and_are_fluid():
    css = _css("tokens.css")
    for token in ("--text-metric-sm", "--text-metric-md", "--text-metric-lg"):
        match = re.search(re.escape(token) + r":\s*([^;]+);", css)
        assert match, f"tokens.css missing {token}"
        assert match.group(1).strip().startswith("clamp("), (
            f"{token} must be fluid so 390px never has to fight 1440px"
        )


@pytest.mark.parametrize(
    "css_file,selector",
    [
        ("components.css", ".stat-value"),
        ("progress.css", ".ps-state"),
        ("progress.css", ".wc-value"),
        ("dashboard.css", ".hero-num"),
        ("dashboard.css", ".wt-big"),
        ("training.css", ".wh-focus"),
    ],
)
def test_primary_metrics_use_the_metric_scale(css_file, selector):
    body = _rule(_css(css_file), selector)
    assert "var(--text-metric-" in body, (
        f"{selector} is a primary metric and must use the shared metric scale, "
        "not a one-off size"
    )


def test_card_surfaces_share_one_radius():
    """A 12px stat card next to a 16px card read as two design systems."""
    assert re.search(
        r"\.stat-card\s*\{[^}]*border-radius:\s*var\(--radius-lg\)",
        _css("components.css"),
    )


def test_icon_tile_primitive_is_defined_and_svg_only():
    css = _css("components.css")
    body = _rule(css, ".icon-tile")
    assert "border-radius" in body and "width" in body
    assert ".icon-tile svg" in css, "the tile must size the glyph, not the glyph the tile"
    assert ".icon-inline" in css


# ── Iconography ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", CORE_TEMPLATES)
def test_core_templates_have_no_emoji_interface_icons(name):
    found = sorted(set(_EMOJI.findall(_read(TEMPLATES / name))))
    assert not found, f"{name} still renders emoji as icons: {found}"


@pytest.mark.parametrize("name", CORE_SCRIPTS)
def test_core_scripts_do_not_inject_emoji_icons(name):
    source = _read(STATIC / name)
    # Localised copy may contain emoji; markup this code *builds* may not.
    markup_lines = [
        line for line in source.splitlines()
        if "<" in line and ">" in line and _EMOJI.search(line)
    ]
    assert not markup_lines, f"{name} injects emoji into markup: {markup_lines[:3]}"


@pytest.mark.parametrize("name", CORE_SCRIPTS)
def test_core_scripts_hide_no_emoji_behind_unicode_escapes(name):
    r"""`icon: '\u{1F373}'` reads as data, not as a pictogram, so the literal
    scan above walked straight past the diary tab's four emoji."""
    escaped = _ESCAPED_EMOJI.findall(_read(STATIC / name))
    assert not escaped, f"{name} still carries escaped emoji: {escaped[:5]}"


def test_home_tips_carry_a_semantic_topic_not_a_glyph():
    html = _read(TEMPLATES / "index.html")
    for block in ("TIPS_TR", "TIPS_EN"):
        body = html.split("const %s = [" % block, 1)[1].split("];", 1)[0]
        assert "icon:'" not in body, f"{block} must not hardcode a glyph per tip"
        assert body.count("topic:'") == 20, f"{block} must declare a topic per tip"
    assert "const ICONS = {" in html, "topics resolve through one shared registry"


def test_nutrition_meal_icons_come_from_the_single_slot_icon_set():
    js = _read(STATIC / "nutrition.js")
    assert js.count("_SLOT_ICONS.breakfast") >= 4, (
        "plan detail, quick-add and the diary builder must reuse the "
        "timeline's icon set, not carry their own copies"
    )


# ── Training ──────────────────────────────────────────────────────────────


def test_week_strip_day_abbreviations_are_unambiguous():
    js = _read(STATIC / "training.js")
    assert "dayShort(" in js, "week chips must use the abbreviation map"
    assert "esc(dayLabel(gun.gun)).slice(0, 3)" not in js, (
        "slicing the full name to 3 chars renders Cuma and Cumartesi identically"
    )
    for table in ("DAY_SHORT_TR", "DAY_SHORT_EN"):
        body = js.split("var %s = {" % table, 1)[1].split("}", 1)[0]
        values = re.findall(r"'([^']+)'\s*(?:,|$)", body)
        labels = [v for v in values if v not in
                  ("Pazartesi", "Salı", "Çarşamba", "Perşembe",
                   "Cuma", "Cumartesi", "Pazar")]
        assert len(labels) == 7, f"{table} must abbreviate all seven days"
        assert len(set(labels)) == 7, f"{table} abbreviations collide: {labels}"


def test_week_chip_state_is_not_carried_by_hue_alone():
    css = _css("training.css")
    assert ".week-chip::before" in css, "every chip needs a type bar"
    cardio = _rule(css, ".week-chip.is-cardio::before")
    assert "repeating-linear-gradient" in cardio, (
        "cardio must differ from a strength day by pattern, not only by shade"
    )
    today = _rule(css, ".week-chip.is-today")
    assert "background" in today and "border-color" in today, (
        "today must differ by fill AND border, not by border alone"
    )


def test_workout_hero_is_not_a_card_inside_a_card():
    body = _rule(_css("training.css"), ".workout-hero")
    for own_surface in ("background:", "border:", "border-radius:"):
        assert own_surface not in body, (
            ".workout-hero sits inside .card and must not repeat its surface"
        )


def test_empty_workout_cta_does_not_reserve_a_row():
    assert re.search(
        r"\.wh-cta:empty\s*\{[^}]*display:\s*none", _css("training.css")
    ), "renderHero leaves the CTA empty on rest days; an empty row is dead space"


def test_stat_labels_wrap_between_words():
    body = _rule(_css("components.css"), ".stat-label")
    assert "overflow-wrap: normal" in body, (
        "body's break-word split 'ANTRENMAN GÜNÜ' into 'ANTRENMA / N GÜNÜ'"
    )


# ── Nutrition ─────────────────────────────────────────────────────────────


def test_log_fab_does_not_share_the_coach_fab_rail():
    """Two identical primary FABs stacked, and the upper one covered each meal
    card's score badge and quick-edit button at 390px."""
    body = _rule(_css("nutrition.css"), ".log-fab")
    assert "left:" in body, "the log FAB moved off the coach FAB's right rail"
    assert "right:" not in body


def test_log_fab_yields_while_the_meal_list_scrolls():
    css = _css("nutrition.css")
    assert ".log-fab.is-tucked" in css
    tucked = _rule(css, ".log-fab.is-tucked")
    assert "transform:" in tucked and "opacity:" in tucked, (
        "tucking must be transform/opacity only — never layout"
    )
    assert "is-tucked" in _read(STATIC / "nutrition.js")
    assert "prefers-reduced-motion" in css


# ── Progress ──────────────────────────────────────────────────────────────


def test_progress_empty_states_share_the_page_card_grammar():
    css = _css("progress.css")
    body = _rule(css, ".prog-section .empty-state")
    assert "background" in body and "border" in body and "border-radius" in body


def test_progress_empty_state_action_is_a_control_not_a_bare_link():
    body = _rule(_css("progress.css"), ".pp-link")
    assert re.search(r"min-height:\s*44px", body), (
        "the only CTA in an empty state must meet the touch minimum"
    )
    assert "border" in body and "background" in body


def test_page_shell_reserves_the_floating_fab_rail():
    """Measured at the document end on all four surfaces: the coach FAB covered
    trailing content on 15 of 16 viewport cells (including Nutrition's "day
    review" button) because only the action bar was reserved."""
    body = _rule(_css("nav.css"), ".page-body")
    assert "var(--fab-rail-h)" in body, (
        "the shell must reserve the FAB rail, not just the action bar"
    )
    tokens = _css("tokens.css")
    assert re.search(r"--fab-rail-h:\s*calc\(", tokens), (
        "--fab-rail-h must be derived from the inset and button size"
    )
    # One number, three consumers: drift here silently un-fixes the occlusion.
    for name in ("coach_widget.css", "nutrition.css"):
        assert "--fab-rail-inset" in _css(name), (
            f"{name} must position its FAB from the shared rail inset"
        )


# ── Cross-surface guards ──────────────────────────────────────────────────


def test_no_core_surface_reintroduces_a_hardcoded_display_title_size():
    """PR1 made page titles fluid; PR2 must not walk that back."""
    for name in ("dashboard.css", "nutrition.css", "training.css", "progress.css"):
        assert not re.search(r"\.page-hdr[^{]*\{[^}]*font-size:\s*\d+px", _css(name)), (
            f"{name} overrides the fluid page-title scale"
        )


def test_locale_catalogs_still_parse_and_stay_in_sync():
    tr = json.loads(_read(ROOT / "locales" / "tr.json"))
    en = json.loads(_read(ROOT / "locales" / "en.json"))
    assert set(tr) == set(en)
