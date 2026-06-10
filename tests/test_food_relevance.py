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

from app.services.ai_nutrition import _food_tokens, _is_relevant_food  # noqa: E402


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
