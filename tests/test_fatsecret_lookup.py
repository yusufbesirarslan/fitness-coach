"""Tests for the deeper FatSecret lookup paths (app/services/fatsecret.py).

test_fatsecret.py'nin devamı: food.get porsiyon çekimi (API sürüm fallback'i,
sağlık kontrolü) ve menü makro hattı _lookup_macros_fatsecret (per-serving /
per-100g / saf-yağ eleme / imkânsız girdi eleme). Ağ yok.

    python -m pytest tests/test_fatsecret_lookup.py -v
"""
import pytest

from app.services import fatsecret


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


def _desc(serving, cal, p, c, f):
    return (f"{serving} - Calories: {cal}kcal | Fat: {f}g | "
            f"Carbs: {c}g | Protein: {p}g")


# ---------------------------------------------------------------------------
# _food_get_servings — sürüm fallback'i + normalizasyon
# ---------------------------------------------------------------------------

GOOD_SERVINGS = {"food": {"servings": {"serving": [
    {"serving_id": "1", "serving_description": "100 g",
     "metric_serving_amount": "100", "metric_serving_unit": "g",
     "calories": "165", "protein": "31", "carbohydrate": "0", "fat": "3.6"},
    {"serving_id": "2", "serving_description": "1 porsiyon",
     "metric_serving_amount": "250", "metric_serving_unit": "g",
     "calories": "412", "protein": "77.5", "carbohydrate": "0", "fat": "9"},
]}}}


def test_servings_fetched_and_normalized(app, monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Resp(GOOD_SERVINGS))

    servings = fatsecret._food_get_servings("42")
    assert [s["serving_id"] for s in servings] == ["2", "1"]  # makul porsiyon önce
    assert all(s["is_bulk"] is False for s in servings)


def test_servings_single_dict_wrapped(app, monkeypatch):
    payload = {"food": {"servings": {"serving":
        {"serving_id": "1", "serving_description": "100 g",
         "metric_serving_amount": "100", "metric_serving_unit": "g",
         "calories": "89", "protein": "1.1", "carbohydrate": "23", "fat": "0.3"}}}}
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Resp(payload))
    servings = fatsecret._food_get_servings("42")
    assert len(servings) == 1


def test_servings_api_version_fallback(app, monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    methods = []

    def fake_get(url, **kwargs):
        method = kwargs["params"]["method"]
        methods.append(method)
        if method == "food.get.v4":
            return _Resp({"error": {"code": 2, "message": "unsupported"}})
        return _Resp(GOOD_SERVINGS)
    monkeypatch.setattr(fatsecret, "_fs_get", fake_get)

    assert fatsecret._food_get_servings("42") is not None
    assert methods == ["food.get.v4", "food.get.v2"]


def test_servings_token_failure_and_all_errors(app, monkeypatch):
    def boom():
        raise RuntimeError("no creds")
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", boom)
    assert fatsecret._food_get_servings("42") is None

    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fatsecret, "_fs_get",
                        lambda url, **kw: _Resp({"error": {"code": 13}}))
    assert fatsecret._food_get_servings("42") is None


def test_servings_implausible_entries_sanitized_away(app, monkeypatch):
    # 100g başına 1500 kcal — fiziksel olarak imkânsız → tüm liste elenir → None.
    payload = {"food": {"servings": {"serving": [
        {"serving_id": "1", "serving_description": "100 g",
         "metric_serving_amount": "100", "metric_serving_unit": "g",
         "calories": "1500", "protein": "10", "carbohydrate": "10", "fat": "10"}]}}}
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Resp(payload))
    assert fatsecret._food_get_servings("42") is None


# ---------------------------------------------------------------------------
# _lookup_macros_fatsecret — menü makro hattı
# ---------------------------------------------------------------------------

def _lookup(monkeypatch, candidates_by_name):
    monkeypatch.setattr(fatsecret, "_normalize_food_queries_en", lambda items, cmap: {})
    monkeypatch.setattr(fatsecret, "_fs_relevant_candidates",
                        lambda name, english, token, category=None:
                        candidates_by_name.get(name, []))


def test_lookup_per_serving_accepted_for_unbanded_dish(app, monkeypatch):
    _lookup(monkeypatch, {"Adana Kebap": [
        {"food_name": "Adana Kebab",
         "food_description": _desc("Per 1 serving", 650, 38, 20, 45)}]})
    per_serving, per_100g = fatsecret._lookup_macros_fatsecret(["Adana Kebap"], "tok")
    assert per_serving["Adana Kebap"]["calories"] == 650.0
    assert per_100g == {}


def test_lookup_per_100g_baseline(app, monkeypatch):
    _lookup(monkeypatch, {"Pilav": [
        {"food_name": "Rice Pilaf",
         "food_description": _desc("Per 100g", 130, 2.7, 28, 0.3)}]})
    per_serving, per_100g = fatsecret._lookup_macros_fatsecret(["Pilav"], "tok")
    assert per_serving == {}
    assert per_100g["Pilav"]["calories"] == 130.0


def test_lookup_skips_pure_fat_ingredient(app, monkeypatch):
    # 'Zeytin Tabağı' saha hatası: saf yağ bileşeni atlanır, gerçek aday kazanır.
    _lookup(monkeypatch, {"Zeytin Tabağı": [
        {"food_name": "Olive Oil",
         "food_description": _desc("Per 100g", 884, 0, 0, 100)},
        {"food_name": "Olives",
         "food_description": _desc("Per 100g", 115, 0.8, 6, 11)}]})
    _, per_100g = fatsecret._lookup_macros_fatsecret(["Zeytin Tabağı"], "tok")
    assert per_100g["Zeytin Tabağı"]["calories"] == 115.0


def test_lookup_skips_implausible_entry(app, monkeypatch):
    _lookup(monkeypatch, {"Çay": [
        {"food_name": "Magic Tea",
         "food_description": _desc("Per 100g", 1200, 53, 10, 10)}]})
    per_serving, per_100g = fatsecret._lookup_macros_fatsecret(["Çay"], "tok")
    assert per_serving == {} and per_100g == {}


def test_lookup_no_candidates_leaves_item_for_llm(app, monkeypatch):
    _lookup(monkeypatch, {})
    per_serving, per_100g = fatsecret._lookup_macros_fatsecret(["Bilinmeyen"], "tok")
    assert per_serving == {} and per_100g == {}
