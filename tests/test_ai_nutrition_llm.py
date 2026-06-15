"""Tests for the LLM-backed nutrition helpers (app/services/ai_nutrition.py).

Mevcut alaka-kapısı testlerini tamamlar: TR→EN normalizasyon (tekil + toplu),
LLM besin araması, porsiyon ağırlığı/makro tahmini (kesik-JSON onarımı dahil),
Türkçe ek üretimi ve deterministik skor. _openai_chat mock'lu — ağ yok.

    python -m pytest tests/test_ai_nutrition_llm.py -v
"""
import json

import pytest

from app.services import ai_nutrition
from app.services.ai_nutrition import (
    _estimate_macros_llm,
    _estimate_macros_llm_batch,
    _estimate_serving_weights_llm,
    _food_search_llm,
    _normalize_food_queries_en,
    _normalize_food_query_en,
    _parse_suggestion_items,
    _repair_truncated_json,
    _turkish_ablative_suffix,
)


def _fake_chat(monkeypatch, reply, capture=None):
    def fake(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        if isinstance(reply, Exception):
            raise reply
        return reply
    # Hafif siteler _openai_chat, ağır siteler _heavy_chat çağırır; ikisini de yakala.
    monkeypatch.setattr(ai_nutrition, "_openai_chat", fake)
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", fake)


# ---------------------------------------------------------------------------
# TR→EN normalizasyon
# ---------------------------------------------------------------------------

def test_normalize_single_query(monkeypatch):
    _fake_chat(monkeypatch, ' "grilled chicken breast" ')
    assert _normalize_food_query_en("ızgara tavuk göğsü") == "grilled chicken breast"


def test_normalize_single_rejects_sentences_and_failures(monkeypatch):
    _fake_chat(monkeypatch, "Bu bir yemek değil,\nçeviremem")
    assert _normalize_food_query_en("hmm") == ""
    _fake_chat(monkeypatch, "x" * 100)
    assert _normalize_food_query_en("uzun") == ""
    _fake_chat(monkeypatch, RuntimeError("api down"))
    assert _normalize_food_query_en("tavuk") == ""
    assert _normalize_food_query_en("") == ""


def test_normalize_batch_with_category_context(monkeypatch):
    calls = []
    _fake_chat(monkeypatch, json.dumps({
        "Margarita": "margherita pizza",
        "çay": "tea",
        "Kola": "Kola",          # ham ile aynı → atlanmalı
    }), capture=calls)

    result = _normalize_food_queries_en(["Margarita", "Çay", "Kola"],
                                        {"Margarita": "Pizzalar"})
    assert result == {"Margarita": "margherita pizza", "Çay": "tea"}  # küçük/büyük toleransı
    assert "menü kategorisi: Pizzalar" in calls[0]["messages"][0]["content"]


def test_normalize_batch_failures(monkeypatch):
    _fake_chat(monkeypatch, "[1, 2, 3]")              # dict değil
    assert _normalize_food_queries_en(["çay"]) == {}
    _fake_chat(monkeypatch, RuntimeError("down"))
    assert _normalize_food_queries_en(["çay"]) == {}
    assert _normalize_food_queries_en([]) == {}


# ---------------------------------------------------------------------------
# LLM besin araması (FatSecret tamamen erişilemezken son çare)
# ---------------------------------------------------------------------------

def test_food_search_llm_parses_fenced_list(monkeypatch):
    _fake_chat(monkeypatch, '```json\n[{"name": "Muz", "calories": 89, '
                            '"protein": 1.1, "carbs": 23, "fat": 0.3}]\n```')
    results = _food_search_llm("muz")
    assert results[0]["name"] == "Muz"
    assert results[0]["per_100g"]["calories"] == 89.0
    assert results[0]["is_per_serving"] is False


def test_food_search_llm_failure_returns_empty(monkeypatch):
    _fake_chat(monkeypatch, "üzgünüm, yapamam")
    assert _food_search_llm("muz") == []


# ---------------------------------------------------------------------------
# Türkçe -dan/-den/-tan/-ten eki (öneri başlıkları)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,suffix", [
    ("Yusuf", "'tan"),    # sert ünsüz + kalın ünlü
    ("Mehmet", "'ten"),   # sert ünsüz + ince ünlü
    ("Arda", "'dan"),     # ünlü + kalın
    ("Ayşe", "'den"),     # ünlü + ince
    ("", "'dan"),
])
def test_turkish_ablative_suffix(name, suffix):
    assert _turkish_ablative_suffix(name) == suffix


# ---------------------------------------------------------------------------
# Öneri metninden öğe çıkarımı
# ---------------------------------------------------------------------------

def test_parse_suggestion_items(app, monkeypatch):
    _fake_chat(monkeypatch, '["200g tavuk göğsü", "1 kase pilav", "  "]')
    assert _parse_suggestion_items("tavuk ve pilav ye") == ["200g tavuk göğsü", "1 kase pilav"]

    _fake_chat(monkeypatch, RuntimeError("down"))
    assert _parse_suggestion_items("x") == []


# ---------------------------------------------------------------------------
# Porsiyon ağırlığı tahmini
# ---------------------------------------------------------------------------

def test_serving_weights_validation_and_fallback(app, monkeypatch):
    _fake_chat(monkeypatch, json.dumps({
        "makarna": 350,
        "salata": 9000,               # aralık dışı (50-600) → fallback
        "ÇORBA": 280,                 # anahtar büyük harf → toleranslı eşleşme
    }))
    weights = _estimate_serving_weights_llm(
        ["makarna", "salata", "çorba", "bilinmeyen"],
        fallback_weights={"salata": 300.0, "bilinmeyen": 250.0})
    assert weights == {"makarna": 350.0, "salata": 300.0,
                       "çorba": 280.0, "bilinmeyen": 250.0}


def test_serving_weights_total_failure_uses_fallbacks(app, monkeypatch):
    _fake_chat(monkeypatch, RuntimeError("down"))
    assert _estimate_serving_weights_llm(["x"], {"x": 400.0}) == {"x": 400.0}
    assert _estimate_serving_weights_llm([]) == {}


# ---------------------------------------------------------------------------
# Kesik JSON onarımı + toplu makro tahmini
# ---------------------------------------------------------------------------

def test_repair_truncated_json():
    complete = '{"a": {"b": 1}} sonradan gelen çöp'
    assert json.loads(_repair_truncated_json(complete)) == {"a": {"b": 1}}

    truncated = '{"Adana Kebap": {"calories": 650, "protein": 38}, "Yarım Ka'
    repaired = json.loads(_repair_truncated_json(truncated))
    assert repaired["Adana Kebap"]["calories"] == 650


def test_macros_batch_parses_and_filters(app, monkeypatch):
    _fake_chat(monkeypatch, json.dumps({
        "Adana Kebap": {"calories": 650, "protein": 38, "carbs": 20, "fat": 45},
        "izgara tavuk": {"calories": "330,5 kcal", "protein": "62", "carbs": 0, "fat": 7},
        "Boş Yemek": {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
    }))
    result = _estimate_macros_llm_batch(["Adana Kebap", "Izgara Tavuk", "Boş Yemek"])
    assert result["Adana Kebap"]["calories"] == 650.0
    assert result["Izgara Tavuk"]["calories"] == 330.5   # string + virgül ayrıştırıldı
    assert "Boş Yemek" not in result                      # 0 kalori elendi


def test_macros_batch_recovers_truncated_output(app, monkeypatch):
    _fake_chat(monkeypatch,
               '{"Adana Kebap": {"calories": 650, "protein": 38, "carbs": 20, "fat": 45}, "Yarım')
    result = _estimate_macros_llm_batch(["Adana Kebap"])
    assert result["Adana Kebap"]["calories"] == 650.0


def test_macros_batch_no_json_returns_empty(app, monkeypatch):
    _fake_chat(monkeypatch, "hesaplayamadım")
    assert _estimate_macros_llm_batch(["x"]) == {}


def test_macros_llm_batches_large_lists(app, monkeypatch):
    batches = []
    monkeypatch.setattr(ai_nutrition, "_estimate_macros_llm_batch",
                        lambda items, category_map=None: batches.append(list(items)) or
                        {n: {"calories": 100.0} for n in items})
    items = [f"yemek{i}" for i in range(20)]
    result = _estimate_macros_llm(items)
    assert len(result) == 20
    assert [len(b) for b in batches] == [15, 5]  # _LLM_MACRO_BATCH_SIZE = 15
