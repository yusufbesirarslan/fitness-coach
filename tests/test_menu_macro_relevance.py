"""Regression tests for the menu-scanner macro pipeline (the AI pop-up chatbot's
"menu tarama" feature returning wrong calories/macros).

Field bug (ai-chatbot-menu.txt): the menu scanner queried FatSecret with raw
Turkish item names and blindly accepted the first result — with NO relevance
check and NO TR→EN normalization — so it surfaced physically-consistent-but-wrong
generics:
    - 'Çay' (tea)        → 330 kcal / 53 g protein
    - 'Bonfile'/'Biftek' → ~0 g protein meat
    - 'Izgara Tavuk' ...  → identical 110/1/3/10 garbage across distinct items

The coach food search already had a relevance gate (_is_relevant_food) + TR→EN
normalization; these tests pin the same gate into the menu path. Pure functions:
NO network / DB / OpenAI calls.

    python -m pytest tests/test_menu_macro_relevance.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_nutrition import _is_relevant_food  # noqa: E402
from app.services.menu_extract import _is_price_noise, _extract_page_sections  # noqa: E402


# ---------------------------------------------------------------------------
# Relevance gate — the garbage matches that produced the wrong macros.
# ---------------------------------------------------------------------------

def test_raw_turkish_garbage_matches_rejected():
    # 'Çay' returned a high-protein product (53 g protein tea) — must be rejected.
    assert _is_relevant_food("Çay", "Whey Protein Powder") is False
    # Meat items came back with ~0 g protein from unrelated entries.
    assert _is_relevant_food("Biftek", "Steak Sauce") is False
    assert _is_relevant_food("Izgara Tavuk", "Potato Chips") is False
    assert _is_relevant_food("Bonfile", "Soy Nuts") is False
    assert _is_relevant_food("Kuzu Şiş", "Shish Kebab Seasoning Mix") is False


def test_english_normalized_queries_match_correct_foods():
    # After TR→EN normalization the gate accepts the genuinely-correct food, so
    # these items resolve to real macros instead of falling through / garbage.
    assert _is_relevant_food("tea", "Black Tea") is True
    assert _is_relevant_food("beef steak", "Beef Steak") is True
    assert _is_relevant_food("grilled chicken", "Skinless Chicken Breast") is True
    assert _is_relevant_food("cola", "Coca-Cola") is True


def test_specific_turkish_names_still_match_turkish_entries():
    # Accent-folding: a raw Turkish query must still match a Turkish DB entry.
    assert _is_relevant_food("Köfte", "Köfte") is True
    assert _is_relevant_food("Omlet", "Omlet") is True


# ---------------------------------------------------------------------------
# HTML parsing — price/number noise filter.
# ---------------------------------------------------------------------------

def test_price_and_count_tokens_are_noise():
    for token in ("120", "₺120", "120 TL", "85,50", "10", "1.250,00 ₺", "45 kr"):
        assert _is_price_noise(token) is True, token


def test_real_food_names_are_not_noise():
    # Anything containing letters is a candidate dish name — never dropped.
    for token in ("Acılı Burger", "Burger 120₺", "7Up", "100% Juice", "Çay"):
        assert _is_price_noise(token) is False, token


# ---------------------------------------------------------------------------
# HTML parsing — dedup of parent/child double-capture.
# ---------------------------------------------------------------------------

def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def test_section_extraction_dedups_nested_repeats_and_drops_prices():
    html = (
        "<body><h2>Burgerler</h2>"
        "<div class='card'><span>Acılı Burger</span></div>"
        "<div class='card'><span>Acılı Burger</span></div>"   # nested dup of the above
        "<div class='card'>Klasik Burger</div>"
        "<li>10</li>"                                          # category-count noise
        "<li>120 TL</li></body>"                               # price noise
    )
    sections = _extract_page_sections(html, _soup(html))
    text = "\n".join(s["text"] for s in sections)
    lines = [ln for ln in text.split("\n") if ln]

    assert lines.count("Acılı Burger") == 1   # parent+child captured once, not twice
    assert "Klasik Burger" in lines
    assert "10" not in lines                   # count dropped
    assert "120 TL" not in lines               # price dropped


def test_legitimate_cross_category_repeat_is_preserved():
    # The same dish under two different headings is NOT a duplicate — dedup is
    # scoped per category, so it must survive in both (e.g. a salad listed under
    # both 'Makarnalar' and 'Salatalar').
    html = (
        "<body>"
        "<h2>Makarnalar</h2><li>Makarna Salatası</li>"
        "<h2>Salatalar</h2><li>Makarna Salatası</li>"
        "</body>"
    )
    sections = _extract_page_sections(html, _soup(html))
    cats = {s["category"]: s["text"] for s in sections}
    assert "Makarna Salatası" in cats.get("Makarnalar", "")
    assert "Makarna Salatası" in cats.get("Salatalar", "")
