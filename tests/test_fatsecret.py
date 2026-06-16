"""Tests for the FatSecret integration layer (app/services/fatsecret.py).

Ağ YOK: HTTP katmanı (_fs_get/_fs_post) ve arama (_fs_search_foods) monkeypatch
ile taklit edilir. Sabitlenenler: OAuth token önbelleği/yenilemesi, açıklama
ayrıştırma, porsiyon normalizasyonu (sentetik 100g + bulk işaretleme), alaka +
kategori kapısı ve in-process besin önbelleklerinin FIFO tavanı.

    python -m pytest tests/test_fatsecret.py -v
"""
import time

import pytest
import requests

from app.services import fatsecret, foodcache
from app.services.fatsecret import (
    _get_fatsecret_token,
    _is_per_serving,
    _normalize_servings,
    _parse_fatsecret_desc,
)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _reset_token_cache():
    fatsecret._fs_token_cache.update({"token": None, "expires_at": 0})
    yield
    fatsecret._fs_token_cache.update({"token": None, "expires_at": 0})


# ---------------------------------------------------------------------------
# OAuth token — önbellek, yenileme, eksik kimlik bilgisi.
# ---------------------------------------------------------------------------

def test_token_requires_credentials(monkeypatch):
    monkeypatch.delenv("FATSECRET_CLIENT_ID", raising=False)
    monkeypatch.delenv("FATSECRET_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="FATSECRET_CLIENT_ID"):
        _get_fatsecret_token()


def test_token_fetched_then_cached(monkeypatch):
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "cid")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "csecret")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return _Resp({"access_token": "tok1", "expires_in": 3600})

    monkeypatch.setattr(fatsecret, "_fs_post", fake_post)

    assert _get_fatsecret_token() == "tok1"
    assert calls[0]["auth"] == ("cid", "csecret")
    assert calls[0]["data"]["grant_type"] == "client_credentials"

    assert _get_fatsecret_token() == "tok1"  # ikinci çağrı önbellekten
    assert len(calls) == 1


def test_token_refreshed_near_expiry(monkeypatch):
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "cid")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "csecret")
    # 60 saniyelik tampon içinde kalan token yenilenmeli.
    fatsecret._fs_token_cache.update({"token": "eski", "expires_at": time.time() + 30})
    monkeypatch.setattr(fatsecret, "_fs_post",
                        lambda url, **kw: _Resp({"access_token": "yeni", "expires_in": 3600}))
    assert _get_fatsecret_token() == "yeni"


def test_token_http_error_propagates(monkeypatch):
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "cid")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(fatsecret, "_fs_post", lambda url, **kw: _Resp({}, status=401))
    with pytest.raises(requests.HTTPError):
        _get_fatsecret_token()


def test_token_error_payload_raises_and_leaves_cache_unset(monkeypatch):
    # 200 + OAuth hata yükü ({"error": ...}): data["access_token"] ham KeyError yerine
    # açık RuntimeError; önbellek kirletilmemeli (F1.4).
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "cid")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(fatsecret, "_fs_post",
                        lambda url, **kw: _Resp({"error": "invalid_client"}))
    with pytest.raises(RuntimeError, match="FatSecret token alınamadı"):
        _get_fatsecret_token()
    assert fatsecret._fs_token_cache["token"] is None


def test_token_missing_access_token_raises(monkeypatch):
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "cid")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(fatsecret, "_fs_post", lambda url, **kw: _Resp({"expires_in": 3600}))
    with pytest.raises(RuntimeError, match="FatSecret token alınamadı"):
        _get_fatsecret_token()
    assert fatsecret._fs_token_cache["token"] is None


# ---------------------------------------------------------------------------
# food_description ayrıştırma
# ---------------------------------------------------------------------------

def test_parse_desc_typical_entry():
    parsed = _parse_fatsecret_desc(
        "Per 100g - Calories: 89kcal | Fat: 0.30g | Carbs: 23.00g | Protein: 1.10g")
    assert parsed == {"serving": "Per 100g", "calories": 89.0,
                      "fat": 0.3, "carbs": 23.0, "protein": 1.1}


def test_parse_desc_comma_decimals():
    parsed = _parse_fatsecret_desc("Per 100g - Calories: 89kcal | Fat: 0,30g")
    assert parsed["fat"] == 0.3


def test_parse_desc_without_serving_segment():
    parsed = _parse_fatsecret_desc("Calories: 100kcal | Fat: 1g")
    assert "serving" not in parsed
    assert parsed["calories"] == 100.0


def test_parse_desc_garbage_returns_none():
    assert _parse_fatsecret_desc("") is None
    assert _parse_fatsecret_desc(None) is None
    assert _parse_fatsecret_desc("tamamen alakasız metin") is None


# ---------------------------------------------------------------------------
# _is_per_serving
# ---------------------------------------------------------------------------

def test_is_per_serving_classification():
    assert _is_per_serving("Per 100g") is False
    assert _is_per_serving("per 100 g") is False
    assert _is_per_serving("Per 1 serving") is True
    assert _is_per_serving("Per 1 cup") is True
    assert _is_per_serving("1 porsiyon") is True
    assert _is_per_serving("") is False
    assert _is_per_serving(None) is False


# ---------------------------------------------------------------------------
# Porsiyon normalizasyonu — sentetik 100g + bulk işaretleme/sıralama.
# ---------------------------------------------------------------------------

def _serving(amount, calories, sid="s", desc=None):
    return {"serving_id": sid, "serving_description": desc or f"{amount} g",
            "metric_serving_amount": amount, "metric_serving_unit": "g",
            "calories": calories, "protein": 10.0, "carbs": 20.0, "fat": 5.0}


def test_normalize_keeps_real_100g_and_sinks_bulk():
    results = _normalize_servings([
        _serving(800, 1600, "bulk"),   # tüm tarif
        _serving(100, 200, "yuz"),
        _serving(250, 500, "tek"),
    ])
    assert [r["serving_id"] for r in results] == ["tek", "yuz", "bulk"]
    assert [r["is_bulk"] for r in results] == [False, False, True]
    assert not any(r["serving_id"] == "100g_calc" for r in results)  # sentetik eklenmedi


def test_normalize_derives_synthetic_100g_from_sane_donor():
    results = _normalize_servings([
        _serving(200, 440, "tek"),
        _serving(600, 1320, "bulk"),  # bulk donor OLMAMALI
    ])
    synthetic = next(r for r in results if r["serving_id"] == "100g_calc")
    assert synthetic["calories"] == 220.0  # 440 * (100/200)
    assert synthetic["is_bulk"] is False
    assert results[-1]["serving_id"] == "bulk"  # bulk en sonda


def test_normalize_falls_back_to_bulk_donor_when_no_sane_serving():
    results = _normalize_servings([_serving(1000, 2000, "dev")])
    synthetic = next(r for r in results if r["serving_id"] == "100g_calc")
    assert synthetic["calories"] == 200.0


# ---------------------------------------------------------------------------
# Alaka + kategori kapısı (menü makro hattı)
# ---------------------------------------------------------------------------

def test_relevant_candidates_fall_through_to_english_query(monkeypatch):
    # Ham Türkçe sorguya gelen alakasız jenerik elenir; İngilizce pass kazanır.
    responses = {
        "Izgara Tavuk": [{"food_name": "Potato Chips"}],
        "grilled chicken": [{"food_name": "Grilled Chicken"}],
    }
    monkeypatch.setattr(fatsecret, "_fs_search_foods", lambda q, token: responses.get(q, []))
    found = fatsecret._fs_relevant_candidates("Izgara Tavuk", "grilled chicken", "tok")
    assert [f["food_name"] for f in found] == ["Grilled Chicken"]


def test_relevant_candidates_category_gate_blocks_cocktail_pizza(monkeypatch):
    # 'Pizzalar' kategorisindeki 'Margarita' kokteyle değil pizzaya eşleşmeli.
    responses = {
        "Margarita": [{"food_name": "Margarita"}],  # kokteyl — tür kapısı eler
        "margherita pizza": [{"food_name": "Margherita Pizza"}],
    }
    monkeypatch.setattr(fatsecret, "_fs_search_foods", lambda q, token: responses.get(q, []))
    found = fatsecret._fs_relevant_candidates("Margarita", "margherita pizza", "tok",
                                              category="Pizzalar")
    assert [f["food_name"] for f in found] == ["Margherita Pizza"]


def test_relevant_candidates_empty_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(fatsecret, "_fs_search_foods",
                        lambda q, token: [{"food_name": "Soy Nuts"}])
    assert fatsecret._fs_relevant_candidates("Bonfile", "tenderloin steak", "tok") == []


# ---------------------------------------------------------------------------
# foods.search sonuç hattı — sağlık kontrolü ve Generic-önce sıralama.
# ---------------------------------------------------------------------------

def test_food_search_filters_implausible_and_sorts_generic_first(monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    payload = {"foods": {"food": [
        {"food_name": "Branded Banana", "food_id": "1", "food_type": "Brand",
         "food_description": "Per 100g - Calories: 95kcal | Fat: 0.4g | Carbs: 24g | Protein: 1.2g"},
        {"food_name": "Banana", "food_id": "2", "food_type": "Generic",
         "food_description": "Per 100g - Calories: 89kcal | Fat: 0.3g | Carbs: 23g | Protein: 1.1g"},
        {"food_name": "Impossible Bar", "food_id": "3", "food_type": "Brand",
         # 100g başına 1500 kcal fiziksel olarak imkânsız → elenmeli
         "food_description": "Per 100g - Calories: 1500kcal | Fat: 10g | Carbs: 10g | Protein: 10g"},
    ]}}
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Resp(payload))

    results = fatsecret._food_search_fatsecret("muz")
    assert [r["name"] for r in results] == ["Banana", "Branded Banana"]  # Generic önce
    assert results[0]["per_100g"]["calories"] == 89.0
    assert results[0]["per_100g"] is not results[0]["macros"]  # önbellek referans paylaşımı yok


def test_food_search_error_payload_returns_none(monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fatsecret, "_fs_get",
                        lambda url, **kw: _Resp({"error": {"code": 13, "message": "rate limit"}}))
    assert fatsecret._food_search_fatsecret("muz") is None


def test_food_search_token_failure_returns_none(monkeypatch):
    def boom():
        raise RuntimeError("no creds")
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", boom)
    assert fatsecret._food_search_fatsecret("muz") is None


# ---------------------------------------------------------------------------
# In-process besin önbellekleri — FIFO tavanı (bellek-DoS koruması).
# ---------------------------------------------------------------------------

def test_macro_cache_skips_zero_calories_and_caps_size():
    # Önbellek artık baz (per_100g / per_serving) bazında ayrı namespace tutar;
    # FIFO tavanı her namespace için ayrı işler.
    cache = foodcache._macro_cache.setdefault("per_serving", {})
    cache.clear()
    foodcache._cache_macros({"sifir": {"calories": 0}}, basis="per_serving")
    assert "sifir" not in cache

    for i in range(foodcache._MACRO_CACHE_MAX):
        foodcache._cache_macros({f"yemek{i}": {"calories": 100}}, basis="per_serving")
    foodcache._cache_macros({"taze": {"calories": 100}}, basis="per_serving")
    assert len(cache) == foodcache._MACRO_CACHE_MAX
    assert "yemek0" not in cache  # en eski düştü
    assert "taze" in cache
    cache.clear()


def test_food_id_cache_normalizes_and_caps():
    foodcache._food_id_cache.clear()
    foodcache._cache_food_id("Muz", "42")
    assert foodcache._food_id_cache["muz"] == "42"
    foodcache._cache_food_id("", "42")
    foodcache._cache_food_id("elma", "")
    assert len(foodcache._food_id_cache) == 1

    for i in range(foodcache._FOOD_ID_CACHE_MAX):
        foodcache._cache_food_id(f"yemek{i}", str(i))
    assert len(foodcache._food_id_cache) == foodcache._FOOD_ID_CACHE_MAX
    foodcache._food_id_cache.clear()
