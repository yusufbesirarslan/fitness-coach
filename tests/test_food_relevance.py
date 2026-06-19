"""Unit tests for the food-search relevance gate (app/services/ai_nutrition.py).

Saf fonksiyon testleri: ağ / DB / OpenAI çağrısı YAPMAZ. Bu kapı, FatSecret'ın
eşleyemediği (çoğunlukla Türkçe) sorgulara döndürdüğü ALAKASIZ jenerik besinleri
('patates kızartması' → 'Soy Nuts') elemek için eklendi — koç chatbot'unun her
besine aynı/yanlış makroyu sunması bu yüzden oluyordu.

    python -m pytest tests/test_food_relevance.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_nutrition import (  # noqa: E402
    _food_tokens,
    _is_relevant_food,
    _is_specific_match,
    _token_match_count,
)


def test_irrelevant_fatsecret_matches_are_rejected():
    # Sahadan üretilen gerçek çöp eşleşmeler (canlı FatSecret çıktısı).
    assert _is_relevant_food("patates kizartmasi", "Soy Nuts") is False
    assert _is_relevant_food("patates kizartmasi", "Pappadum") is False
    assert _is_relevant_food("muz", "Keto Mug Bread") is False
    assert _is_relevant_food("elma", "Mango Alma Berry Smoothie") is False


def test_relevant_matches_pass():
    assert _is_relevant_food("medium fries", "World Famous Fries - Medium") is True
    assert _is_relevant_food("banana", "Bananas") is True
    assert _is_relevant_food("grilled chicken breast", "Skinless Chicken Breast") is True
    assert _is_relevant_food("apple", "Apples") is True
    # Türkçe ham sorgu, Türkçe FatSecret kaydıyla eşleşmeli (aksan-katlama).
    assert _is_relevant_food("muz", "Muz") is True


def test_accent_folding_matches():
    # 'çilek' ↔ 'cilek'/'Strawberry' — aksan eşleşmeyi bozmamalı.
    assert _is_relevant_food("çilek", "Çilek") is True


def test_token_boundary_no_substring_false_positives():
    # Substring eşleştirme bu çöp eşleşmeleri üretiyordu; token-sınırı eler.
    assert _is_relevant_food("hindi gogsu", "Bhindi") is False        # hindi ⊄ bhindi
    assert _is_relevant_food("portakal suyu", "Hon Tsuyu Soup Stock") is False  # suyu ⊄ tsuyu
    assert _is_relevant_food("lor peyniri", "Low Calorie Mints") is False       # lor ⊄ calorie
    assert _is_relevant_food("turk kahvesi", "Big Turk Bites") is False         # mutfak sıfatı stopword


def test_plural_prefix_tolerance():
    # ≥4 harf önek toleransı çoğulları yakalar ama kısa tokenleri yakalamaz.
    assert _is_relevant_food("banana", "Bananas") is True
    assert _is_relevant_food("apple", "Apples") is True
    assert _is_relevant_food("ton baligi", "Tuna in Water (Canned)") is False  # 'ton' kısa, eşleşmez


def test_stopwords_do_not_drive_relevance():
    # Yalnızca miktar/pişirme sözcüğü paylaşmak alaka SAYILMAZ.
    assert _is_relevant_food("orta boy patates", "Medium Soda") is False
    assert _is_relevant_food("haslanmis yumurta", "Grilled Cheese") is False


def test_tokens_drop_short_and_stopwords():
    toks = _food_tokens("1 orta boy patates kizartmasi")
    assert "patates" in toks
    assert "kizartmasi" in toks
    assert "orta" not in toks   # stopword
    assert "boy" not in toks    # stopword
    assert "1" not in toks      # too short / numeric noise


def test_no_meaningful_token_does_not_overfilter():
    # Sorgu yalnızca miktar/stopword içeriyorsa (anlamlı token yok) sonuç elenmez.
    assert _food_tokens("2 adet") == set()
    assert _is_relevant_food("2 adet", "Anything At All") is True


def test_relevant_false_when_name_has_no_meaningful_tokens():
    # Ad anlamlı token içermiyorsa (yalnızca kısa/gürültü) eşleşme yok.
    assert _is_relevant_food("banana", "a x 1") is False


# ---------------------------------------------------------------------------
# _token_match_count — örtüşen anlamlı token SAYISI (spesifiklik sıralaması).
# ---------------------------------------------------------------------------

def test_token_match_count_exact_and_prefix():
    assert _token_match_count("tavuk burger", "Tavuk Burger Menu") == 2  # tam eşleşmeler
    assert _token_match_count("banana", "Bananas Split") == 1            # ≥4 harf önek
    assert _token_match_count("tavuk salata", "Tavuk Izgara") == 1       # yalnız 'tavuk'


def test_token_match_count_zero_when_no_meaningful_tokens():
    assert _token_match_count("2 adet", "Anything") == 0   # sorguda anlamlı token yok
    assert _token_match_count("banana", "a x") == 0        # adda anlamlı token yok


# ---------------------------------------------------------------------------
# _is_specific_match — kompozit adlar + yemek-türü tutarlılığı (katı kapı).
# ---------------------------------------------------------------------------

def test_specific_match_requires_two_tokens_for_composite_query():
    # 2+ token'lı sorgu, tek genel kategori kelimesini paylaşmak YETMEZ.
    assert _is_specific_match("tavuk burger", "Mantar Burger") is False  # yalnız 'burger'
    assert _is_specific_match("tavuk burger", "Tavuk Burger Klasik") is True


def test_specific_match_dish_type_must_agree():
    # 'Keçi Peyniri Salatası' (salata) → 'Goat Cheese' bileşene çökmesin diye reddedilir.
    assert _is_specific_match("keci peyniri salatasi", "Goat Cheese") is False


def test_specific_match_no_tokens_does_not_overfilter():
    assert _is_specific_match("2 adet", "Anything At All") is True
