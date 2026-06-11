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

from app.services.ai_nutrition import _dish_types, _is_relevant_food, _is_specific_match, _primary_dish_type, _token_match_count  # noqa: E402
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
# Strict specificity gate — FatSecret "family collapse" (the 2nd-pass bug).
#
# Multi-word dishes share only a generic category token with FatSecret's generic
# entry, so the relevance gate (1 shared token) let the SAME generic land on every
# variant → 7 burgers all identical at 705/82/0/40 with 0 g carb; 3 salads all at
# 40 kcal. The strict gate requires >=2 token matches for multi-token queries, so
# generic-only matches are rejected and the item falls through to the LLM.
# ---------------------------------------------------------------------------

def test_generic_family_collapse_rejected_by_strict_gate():
    # All of these passed the loose gate via the single 'burger'/'salad' token —
    # the strict gate must reject them so they don't collapse onto a generic.
    assert _is_specific_match("Tavuk Burger", "Burger") is False        # chicken→generic
    assert _is_specific_match("Vejetaryen Burger", "Beef Burger") is False  # veggie→beef!
    assert _is_specific_match("Mantar Burger", "Burger") is False
    assert _is_specific_match("caesar salad", "Salad") is False
    assert _is_specific_match("chicken burger", "Beef Burger") is False


def test_strict_gate_keeps_specific_matches():
    assert _is_specific_match("chicken burger", "Chicken Burger") is True
    assert _is_specific_match("caesar salad", "Caesar Salad") is True
    assert _is_specific_match("bacon burger", "Bacon Burger") is True
    assert _is_specific_match("chicken soup", "Chicken Soup") is True
    # Single-token (atomic) items keep the 1-match behaviour — no family-collapse.
    assert _is_specific_match("Cheeseburger", "Cheeseburger") is True
    assert _is_specific_match("tea", "Black Tea") is True
    assert _is_specific_match("cola", "Coca-Cola") is True


def test_token_match_count_orders_by_specificity():
    # The candidate sort key: the most-specific (most query tokens covered) wins.
    assert _token_match_count("chicken burger", "Chicken Burger") == 2
    assert _token_match_count("chicken burger", "Beef Burger") == 1
    assert _token_match_count("chicken burger", "Soy Nuts") == 0


# ---------------------------------------------------------------------------
# Dish-type head-noun gate — a composed dish must not collapse onto a COMPONENT.
#
# Field bug (ai-chatbot-menu.txt): 'Kızarmış Keçi Peyniri Salatası' (a SALAD)
# resolved to plain 'Goat Cheese' — goat+cheese share 2 tokens so the strict gate
# let it pass, but the match is a cheese block, not a salad → scaled to a salad
# serving weight it produced 1264 kcal / 101 g fat. The gate now also requires the
# matched name to share the query's dish-TYPE (salad/soup/burger/pizza/pasta).
# ---------------------------------------------------------------------------

def test_dish_type_gate_rejects_component_collapse():
    # SALAD → cheese component: 2 tokens match but the 'salad' head-noun is absent.
    assert _is_specific_match("Fried Goat Cheese Salad", "Goat Cheese") is False
    assert _is_specific_match("Keçi Peyniri Salatası", "Keçi Peyniri") is False
    # A salad must not collapse onto its dressing/base either.
    assert _is_specific_match("caesar chicken salad", "Caesar Chicken") is False


def test_dish_type_gate_keeps_same_type_matches():
    assert _is_specific_match("Fried Goat Cheese Salad", "Goat Cheese Salad") is True
    assert _is_specific_match("chicken soup", "Chicken Soup") is True
    assert _is_specific_match("Mantar Çorbası", "Mantar Çorbası") is True
    # Items with NO dish-type token keep the plain token-count behaviour.
    assert _is_specific_match("Cheeseburger", "Cheeseburger") is True
    assert _is_specific_match("tea", "Black Tea") is True


# ---------------------------------------------------------------------------
# Category → dish-type resolution. The menu pipeline threads the item's category
# heading into the FatSecret candidate gate so an ambiguous name is forced to the
# right food family: 'Margarita' under 'Pizzalar' must resolve to a pizza, not the
# cocktail. A drink-category Margarita (no identity dish-type) stays the cocktail.
# ---------------------------------------------------------------------------

def test_category_headings_map_to_identity_dish_types():
    assert _dish_types("Pizzalar") == {"pizza"}
    assert _dish_types("Salatalar") == {"salad"}
    assert _dish_types("Çorbalar") == {"soup"}
    assert _dish_types("Makarnalar") == {"pasta"}
    assert _dish_types("Margherita Pizza") == {"pizza"}


def test_non_identity_categories_impose_no_dish_type():
    # Serving-vessel / generic headings must NOT force a type (Beef Stroganoff under
    # 'Sıcak Kaseler' should still resolve normally — bowl is not an identity type).
    assert _dish_types("Sıcak Kaseler") == set()
    assert _dish_types("Tavuklar") == set()
    assert _dish_types("İçecekler") == set()
    # A bare cocktail name carries no dish-type, so the Pizzalar gate rejects it.
    assert _dish_types("Margarita") == set()


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


# ---------------------------------------------------------------------------
# Porsiyon-makullük kapısının tür çözümü (docs/menu-porsiyon-eslesme-hatasi.md):
# bant/gram kuralları yalnız KESİN türde uygulanır — ad önce, sonra kategori.
# ---------------------------------------------------------------------------

def test_primary_dish_type_from_name():
    assert _primary_dish_type("Vegan Burger") == "burger"
    assert _primary_dish_type("Sezar Salata") == "salad"
    assert _primary_dish_type("Mercimek Çorbası") == "soup"


def test_primary_dish_type_category_fallback():
    # Ad tür içermiyor → kategori çözer (saha vakaları: Penne, Margarita).
    assert _primary_dish_type("Penne Arrabbiata", "Makarnalar") == "pasta"
    assert _primary_dish_type("Margarita", "Pizzalar") == "pizza"


def test_primary_dish_type_name_wins_over_category():
    # Ad zaten kesin tür veriyorsa kategoriye bakılmaz.
    assert _primary_dish_type("Tavuk Burger", "Ana Yemekler") == "burger"


def test_primary_dish_type_unknown_or_ambiguous_is_none():
    assert _primary_dish_type("Tavuk Mangal", "Ana Yemekler") is None  # taksonomi dışı
    assert _primary_dish_type("Pizza Burger") is None                  # belirsiz ad
    assert _primary_dish_type("", None) is None


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
