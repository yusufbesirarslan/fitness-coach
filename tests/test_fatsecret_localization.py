"""FatSecret Premier localization seam (PRELAUNCH-02, TR/tr).

Unset keeps every request byte-identical. Set, every call that issues or
resolves a provider food identity carries the same region/language, and the
provider's own names and IDs pass through untranslated.
"""
import pytest

from app import config
from app.services import fatsecret, mobile_food_discovery


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


FOOD = {"food_id": "4242", "food_name": "Mercimek Çorbası", "brand_name": "",
        "servings": {"serving": [{"serving_id": "1", "serving_description": "1 kase",
                                  "metric_serving_amount": "250", "metric_serving_unit": "g",
                                  "calories": "160", "protein": "9", "carbohydrate": "24", "fat": "3"}]}}


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(dict(params))
        method = params["method"]
        if method == "foods.search":
            return Response({"foods": {"food": [{"food_id": "4242", "food_name": "Mercimek Çorbası",
                                                 "food_description": "Per 100g - Calories: 64kcal | Fat: 1.20g | Carbs: 9.60g | Protein: 3.60g"}]}})
        if method == "food.find_id_for_barcode":
            return Response({"food_id": {"value": "4242"}})
        return Response({"food": FOOD})

    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "token")
    monkeypatch.setattr(fatsecret, "_fs_get", fake_get)
    return calls


def _localize(monkeypatch, region, language):
    monkeypatch.setattr(fatsecret, "FATSECRET_REGION", region)
    monkeypatch.setattr(fatsecret, "FATSECRET_LANGUAGE", language)


def _identity_calls(app):
    with app.app_context():
        found = mobile_food_discovery.search("mercimek")
        food = mobile_food_discovery.servings("4242")
        fatsecret._food_find_id_by_barcode_raw("8690000000001", strict=True)
        fatsecret._food_find_by_barcode("8690000000001")
    return found, food


def test_unset_keeps_requests_unchanged(app, provider, monkeypatch):
    _localize(monkeypatch, "", "")
    _identity_calls(app)
    assert provider and all("region" not in p and "language" not in p for p in provider)


def test_tr_localizes_every_identity_call_and_keeps_provider_truth(app, provider, monkeypatch):
    _localize(monkeypatch, "TR", "tr")
    found, food = _identity_calls(app)
    methods = {p["method"] for p in provider}
    assert {"foods.search", "food.find_id_for_barcode", "food.get.v4"} <= methods
    assert all(p.get("region") == "TR" and p.get("language") == "tr" for p in provider)
    # Provider identity and text cross the boundary verbatim: no translation.
    assert found == [{"provider": "fatsecret", "food_id": "4242", "name": "Mercimek Çorbası", "brand": ""}]
    assert food["name"] == "Mercimek Çorbası" and food["servings"][0]["description"] == "1 kase"


def test_region_without_language_uses_provider_default_language(app, provider, monkeypatch):
    _localize(monkeypatch, "TR", "")
    _identity_calls(app)
    assert all(p.get("region") == "TR" and "language" not in p for p in provider)


def test_macro_estimate_search_stays_on_provider_default(app, provider, monkeypatch):
    _localize(monkeypatch, "TR", "tr")
    with app.app_context():
        fatsecret._fs_search_foods("lentil soup", "token")
    assert "region" not in provider[-1] and "language" not in provider[-1]


@pytest.mark.parametrize("region,language,ok", [
    ("", "", True), ("TR", "", True), ("TR", "tr", True),
    ("tr", "", False), ("TUR", "", False), ("TR", "TR", False), ("TR", "tur", False), ("", "tr", False),
])
def test_boot_refuses_malformed_or_language_only_settings(monkeypatch, region, language, ok):
    monkeypatch.setattr(config, "FATSECRET_REGION", region)
    monkeypatch.setattr(config, "FATSECRET_LANGUAGE", language)
    if ok:
        config._enforce_fatsecret_localization()
    else:
        with pytest.raises(RuntimeError):
            config._enforce_fatsecret_localization()
