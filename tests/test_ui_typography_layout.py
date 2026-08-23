"""PR1 layout + typography contracts for Home, Nutrition, Training, Progress.

Guards the screenshot-highlighted defects: mid-word page-title splits,
7-column week-strip overflow, full-bleed empty meal slots, and grid tracks
that cannot shrink below min-content.
"""
from pathlib import Path
import json
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
LOCALES = ROOT / "locales"


def _css(name):
    return (STATIC / name).read_text(encoding="utf-8")


def _locale(name):
    with open(LOCALES / f"{name}.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_page_hdr_uses_fluid_display_size_and_keeps_words_intact():
    css = _css("theme.css")
    assert re.search(
        r"\.page-hdr\s+h1\s*\{[^}]*font-size:\s*var\(--text-display-lg\)", css
    ), "page titles must use the fluid display scale, not a hardcoded 52px"
    assert re.search(r"\.page-hdr\s+h1\s*\{[^}]*word-break:\s*keep-all", css)
    assert re.search(r"\.page-hdr\s+h1\s*\{[^}]*overflow-wrap:\s*normal", css)
    assert "font-size: 52px" not in css.split(".page-hdr")[1].split(".review-card")[0]


def test_progress_title_parts_are_complete_words():
    en = _locale("en")
    tr = _locale("tr")
    assert en["progress.h1_a"] == "PROGRESS"
    assert en["progress.h1_b"] == "TRACKING"
    assert en["progress.h1_a"] not in ("PROG", "PRO", "P")
    assert en["progress.h1_b"] not in ("RESS", "GRESS")
    assert tr["progress.h1_a"] == "İLERLEME"
    assert tr["progress.h1_b"] == "TAKİBİ"


def test_progress_heading_does_not_force_a_mid_word_break(
        app, client, make_user, login):
    make_user("pr1title", profile_complete=True, language="en")
    login("pr1title")
    html = client.get("/progress-page").get_data(as_text=True)
    assert "PROG<br>" not in html
    assert ">PROG<" not in html
    hdr = re.search(r'<div class="page-hdr">\s*<h1>(.*?)</h1>', html, re.S)
    assert hdr, "progress page-hdr h1 is missing"
    heading = hdr.group(1)
    assert "<br" not in heading
    assert "PROGRESS" in heading
    assert "TRACKING" in heading


def test_week_strip_columns_can_shrink_below_min_content():
    css = _css("training.css")
    assert re.search(
        r"\.week-strip\s*\{[^}]*grid-template-columns:\s*repeat\(7,\s*minmax\(0,\s*1fr\)\)",
        css,
    ), "week-strip 1fr tracks must have a 0 min so nowrap labels ellipsis instead of overflowing"
    assert re.search(r"\.week-chip\s*\{[^}]*min-width:\s*0", css)
    assert re.search(
        r"\.wc-focus\s*\{[^}]*white-space:\s*nowrap", css
    )


def test_week_strip_markup_exposes_full_focus_as_title():
    js = (STATIC / "training.js").read_text(encoding="utf-8")
    assert "title=\"' + focusTitle + '\"" in js
    assert "esc(focusRaw)" in js
    assert '.replace(/"/g, \'&quot;\')' in js


def test_wstats_tracks_remain_shrinkable():
    css = _css("training.css")
    assert re.search(
        r"\.wstats\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)",
        css,
    )


def test_meal_empty_slot_is_a_compact_button_not_full_bleed():
    css = _css("nutrition.css")
    assert re.search(r"\.slot-empty\s*\{[^}]*display:\s*inline-flex", css)
    assert re.search(r"\.slot-empty\s*\{[^}]*white-space:\s*nowrap", css)
    assert re.search(r"\.slot-name\s*\{[^}]*white-space:\s*nowrap", css)
    js = (STATIC / "nutrition.js").read_text(encoding="utf-8")
    assert 'class="slot-empty"' in js
    assert "<button type=\"button\" class=\"slot-empty\"" in js
    assert "slot-emoji" not in js
    assert "slot-ic" in js


@pytest.mark.parametrize("emoji", ["🍳", "🥗", "🍽️", "🥜"])
def test_meal_timeline_slots_do_not_use_emoji_icons(emoji):
    js = (STATIC / "nutrition.js").read_text(encoding="utf-8")
    timeline = js.split("var SLOTS = [")[1].split("];")[0]
    assert emoji not in timeline


def test_home_dashboard_grids_use_shrinkable_tracks():
    css = _css("dashboard.css")
    assert "repeat(2, minmax(0, 1fr))" in css
    assert "repeat(4, minmax(0, 1fr))" in css
    assert re.search(r"\.qa-lbl\s*\{[^}]*white-space:\s*nowrap", css)
    assert re.search(r"\.ach\s*\{[^}]*height:\s*100%", css)
    assert re.search(r"\.tip-text\s*\{[^}]*max-width:\s*65ch", css)


def test_sec_label_does_not_wrap_the_caption():
    css = _css("components.css")
    assert re.search(r"\.sec-label\s*\{[^}]*white-space:\s*nowrap", css)
    assert re.search(r"\.sec-label\s*\{[^}]*min-width:\s*0", css)
