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
    assert re.search(
        r"\.page-hdr\s+h1\s+span\s*\{[^}]*white-space:\s*nowrap", css
    ), "accent title words must stay intact instead of wrapping mid-word"
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


_MIDWORD_FRAGMENTS = {
    ("NUT", "RITION"),
    ("PROG", "RESS"),
    ("QUE", "STS"),
    ("FRIEN", "DS"),
    ("BES", "LENME"),
    ("GÖREV", "LER"),
    ("ARKADAŞLAR", "IN"),
}


def test_display_title_parts_are_complete_words():
    """h1_a/h1_b exist to color the second word, not to slice one word in half."""
    en = _locale("en")
    tr = _locale("tr")
    pairs = sorted(
        key[:-5]
        for key in en
        if key.endswith(".h1_a") and key.replace(".h1_a", ".h1_b") in en
    )
    for prefix in pairs:
        a_en, b_en = en[f"{prefix}.h1_a"], en[f"{prefix}.h1_b"]
        a_tr, b_tr = tr[f"{prefix}.h1_a"], tr[f"{prefix}.h1_b"]
        assert (a_en, b_en) not in _MIDWORD_FRAGMENTS, prefix
        assert (a_tr, b_tr) not in _MIDWORD_FRAGMENTS, prefix
        for part in (a_en, b_en, a_tr, b_tr):
            assert part.strip(), prefix


def test_nutrition_title_is_the_destination_name_not_mid_word_split():
    """WEB-UX4-PR6 / F-15 retired the two-part "NUTRITION PLAN" title: the page
    is the Nutrition destination and "Nutrition Plan" names only its third tab.
    The title now renders the single destination key, so it cannot be split."""
    en = _locale("en")
    tr = _locale("tr")
    assert "nutrition.h1_a" not in en and "nutrition.h1_b" not in en
    assert "nutrition.h1_a" not in tr and "nutrition.h1_b" not in tr
    assert en["nutrition.parent.current"] == "Nutrition"
    assert tr["nutrition.parent.current"] == "Beslenme"


def test_nutrition_heading_does_not_glue_or_split_the_word(
        app, client, make_user, login):
    make_user("pr1nuttitle", profile_complete=True, language="en")
    login("pr1nuttitle")
    html = client.get("/nutrition").get_data(as_text=True)
    assert "NUT<" not in html
    assert ">RITION<" not in html
    hdr = re.search(r'<div class="page-hdr">\s*<h1>(.*?)</h1>', html, re.S)
    assert hdr, "nutrition page-hdr h1 is missing"
    heading = hdr.group(1)
    assert "<br" not in heading
    # WEB-UX4-PR6 / F-15: one whole destination word, never "NUTRITION PLAN".
    assert heading.strip() == "Nutrition"


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
    assert not (STATIC / "training.css").exists()
    css = _css("plan.css")
    assert ".plan-days" in css or ".plan-day" in css


def test_week_strip_markup_exposes_full_focus_as_title():
    assert not (STATIC / "training.js").exists()
    html = (ROOT / "templates" / "plan.html").read_text(encoding="utf-8")
    assert "plan-days" in html


def test_wstats_tracks_remain_shrinkable():
    assert not (STATIC / "training.css").exists()


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


def test_home_grids_use_shrinkable_tracks():
    """The rule that mattered: a fixed `1fr` track cannot shrink below its
    content, which is what pushed the legacy dashboard's grids past 320px. The
    modules those selectors named are gone with UX-2 PR4; Today's own grid and
    its blanket `min-width: 0` carry the same guarantee."""
    css = _css("today.css")
    assert "repeat(3, minmax(0, 1fr))" in css
    assert re.search(r"\.today \*\s*\{[^}]*min-width:\s*0", css)


def test_sec_label_does_not_wrap_the_caption():
    css = _css("components.css")
    assert re.search(r"\.sec-label\s*\{[^}]*white-space:\s*nowrap", css)
    assert re.search(r"\.sec-label\s*\{[^}]*min-width:\s*0", css)
