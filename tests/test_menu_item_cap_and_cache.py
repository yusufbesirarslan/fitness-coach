"""Menü hattı: kategoriler arası adil (round-robin) kırpma + makro önbelleğinde
baz (per-100g / per-porsiyon) ayrımı.

Saha hatası (ai-chatbot-menu.txt, 2026-06-12):
  - Düz [:MAX_MENU_ITEMS] kesimi çıkarım sırasındaki SON kategorileri toptan
    düşürüyordu → kategoriler "eksik seçenekli" görünüyordu.
  - _macro_cache tek sözlüktü: koç araması 100g-bazlı, menü hattı porsiyon-bazlı
    değer yazıyordu → aynı ad altında çapraz zehirlenme (4-5x sapma).

Saf testler: ağ / DB / OpenAI yok.
    python -m pytest tests/test_menu_item_cap_and_cache.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai_nutrition import MAX_MENU_ITEMS, _cap_items_round_robin  # noqa: E402
from app.services import foodcache  # noqa: E402


# ---------------------------------------------------------------------------
# Round-robin kırpma
# ---------------------------------------------------------------------------

def _menu(n_cats, per_cat):
    return [(f"Kat{c}", f"Yemek{c}-{i}") for c in range(n_cats) for i in range(per_cat)]


def test_under_limit_returns_input_unchanged():
    items = _menu(3, 4)  # 12 öğe < 80
    assert _cap_items_round_robin(items, MAX_MENU_ITEMS) == items


def test_every_category_survives_the_cap():
    # BigChefs senaryosu: 17 kategori × 8 öğe = 136 > 80. Eski [:80] kesimi son
    # ~7 kategoriyi siliyordu; round-robin her kategoriden öğe bırakmalı.
    items = _menu(17, 8)
    capped = _cap_items_round_robin(items, 80)
    assert len(capped) == 80
    surviving_cats = {cat for cat, _ in capped}
    assert surviving_cats == {f"Kat{c}" for c in range(17)}
    # Adil dağılım: hiçbir kategori diğerinden 1'den fazla öğe farklı olamaz.
    counts = [sum(1 for cat, _ in capped if cat == f"Kat{c}") for c in range(17)]
    assert max(counts) - min(counts) <= 1


def test_category_and_item_order_preserved():
    items = [("A", "a1"), ("A", "a2"), ("B", "b1"), ("B", "b2"), ("C", "c1")]
    capped = _cap_items_round_robin(items, 4)
    # 1. tur: a1, b1, c1 — 2. tur: a2 (kategori sırası ve kategori-içi sıra korunur).
    assert capped == [("A", "a1"), ("B", "b1"), ("C", "c1"), ("A", "a2")]


def test_uneven_categories_exhaust_gracefully():
    items = [("A", "a1"), ("A", "a2"), ("A", "a3"), ("B", "b1")]
    capped = _cap_items_round_robin(items, 3)
    assert capped == [("A", "a1"), ("B", "b1"), ("A", "a2")]


# ---------------------------------------------------------------------------
# Önbellek baz ayrımı
# ---------------------------------------------------------------------------

def _clear_caches():
    for cache in foodcache._macro_cache.values():
        cache.clear()


def test_per_100g_write_invisible_to_per_serving_read():
    _clear_caches()
    # Koç araması 'Pizza'yı 100g bazında yazar (267 kcal/100g)...
    foodcache._cache_macros({"Pizza": {"calories": 267, "protein": 11, "carbs": 33, "fat": 10}},
                            basis="per_100g")
    # ...menü hattı per-porsiyon okur: 100g değeri tam tabak sanılMAmalı.
    hits, misses = foodcache._get_cached_macros(["Pizza"], basis="per_serving")
    assert hits == {}
    assert misses == ["Pizza"]


def test_per_serving_write_invisible_to_per_100g_read():
    _clear_caches()
    foodcache._cache_macros({"Margarita": {"calories": 850, "protein": 38, "carbs": 110, "fat": 28}},
                            basis="per_serving")
    hits, misses = foodcache._get_cached_macros(["Margarita"], basis="per_100g")
    assert hits == {}
    assert misses == ["Margarita"]


def test_same_basis_roundtrip():
    _clear_caches()
    macros = {"calories": 850.0, "protein": 38.0, "carbs": 110.0, "fat": 28.0}
    foodcache._cache_macros({"Margarita": macros}, basis="per_serving")
    hits, misses = foodcache._get_cached_macros(["Margarita"], basis="per_serving")
    assert hits == {"Margarita": macros}
    assert misses == []


def test_zero_calorie_entries_not_cached():
    _clear_caches()
    foodcache._cache_macros({"Boş": {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}},
                            basis="per_serving")
    hits, _ = foodcache._get_cached_macros(["Boş"], basis="per_serving")
    assert hits == {}
