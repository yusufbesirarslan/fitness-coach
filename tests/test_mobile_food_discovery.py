"""Mobile food discovery is read-only and preserves provider uncertainty."""
from types import SimpleNamespace

import pytest

from app.models import BarcodeFoodCache, MealLog
from app.services import mobile_auth
from app.services import mobile_food_discovery
from app.services import fatsecret


class ProviderResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload


@pytest.fixture
def mobile_user(make_user):
    return make_user("mobile-food-discovery")


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user):
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        return {"Authorization": "Bearer opaque-access-credential"}
    return _headers


def raw_food(metric_amount=None, metric_unit=None):
    return {
        "food_name": "Provider Food",
        "brand_name": "Provider Brand",
        "servings": {"serving": [{
            "serving_id": "serving-1",
            "serving_description": "1 mystery portion",
            "metric_serving_amount": metric_amount,
            "metric_serving_unit": metric_unit,
            "calories": "120",
            "protein": "4",
            "carbohydrate": "20",
            "fat": "3",
        }]},
    }


def test_serving_projection_closes_gap_five_with_null_not_zero():
    projected = mobile_food_discovery.project_food("food-1", raw_food())
    serving = projected["servings"][0]

    assert serving["metric_mass"] is None
    assert serving["nutrition_per_100g"] is None
    assert serving["nutrition"]["energy_kcal"] == 120.0


def test_search_is_read_only(raw_client, as_mobile, mobile_user, monkeypatch):
    monkeypatch.setattr(
        mobile_food_discovery, "search",
        lambda query: [{
            "provider": "fatsecret", "food_id": "food-1",
            "name": "Provider Food", "brand": "Provider Brand",
        }])

    response = raw_client.get(
        "/api/v1/nutrition/foods/search?q=provider",
        headers=as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json["foods"][0]["food_id"] == "food-1"
    assert MealLog.query.count() == 0
    assert BarcodeFoodCache.query.count() == 0


def test_servings_route_publishes_provider_identity_and_null_mass(
        raw_client, as_mobile, mobile_user, monkeypatch):
    monkeypatch.setattr(
        mobile_food_discovery, "servings",
        lambda food_id: mobile_food_discovery.project_food(
            food_id, raw_food()))

    response = raw_client.get(
        "/api/v1/nutrition/foods/fatsecret/food-1/servings",
        headers=as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json["food"]["provider"] == "fatsecret"
    assert response.json["food"]["servings"][0]["metric_mass"] is None
    assert MealLog.query.count() == 0


def test_barcode_is_never_logged_and_lookup_does_not_fill_cache(
        raw_client, as_mobile, mobile_user, monkeypatch, caplog):
    barcode = "0012345678905"
    monkeypatch.setattr(
        mobile_food_discovery, "barcode_lookup",
        lambda code: mobile_food_discovery.project_food("food-1", raw_food()))

    response = raw_client.get(
        f"/api/v1/nutrition/foods/barcode?code={barcode}",
        headers=as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json["food"]["food_id"] == "food-1"
    assert barcode not in caplog.text
    assert BarcodeFoodCache.query.count() == 0
    assert MealLog.query.count() == 0


def test_invalid_barcode_is_rejected_before_provider_lookup(
        raw_client, as_mobile, mobile_user, monkeypatch):
    monkeypatch.setattr(
        mobile_food_discovery, "barcode_lookup",
        lambda code: pytest.fail("invalid barcode reached provider"))

    response = raw_client.get(
        "/api/v1/nutrition/foods/barcode?code=not-a-barcode",
        headers=as_mobile(mobile_user))

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_BARCODE"


def test_raw_search_adapter_preserves_provider_identity(app, monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "token")
    monkeypatch.setattr(
        fatsecret, "_fs_get", lambda *args, **kwargs: ProviderResponse({
            "foods": {"food": [{
                "food_id": "food-77", "food_name": "Raw Food",
                "brand_name": "Raw Brand", "food_description": "ignored",
            }]}
        }))

    with app.app_context():
        result = mobile_food_discovery.search("raw food")

    assert result == [{
        "provider": "fatsecret", "food_id": "food-77",
        "name": "Raw Food", "brand": "Raw Brand",
    }]


def test_raw_serving_adapter_never_logs_provider_exception_detail(
        app, monkeypatch, caplog):
    food_id = "sensitive-provider-food-id"
    provider_detail = "upstream echoed credential-shaped detail"
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "token")

    def fail(*args, **kwargs):
        raise RuntimeError(provider_detail)

    monkeypatch.setattr(fatsecret, "_fs_get", fail)

    with app.app_context():
        assert mobile_food_discovery.servings(food_id) is None

    assert provider_detail not in caplog.text
    assert food_id not in caplog.text


def test_raw_serving_adapter_never_logs_token_failure_detail(
        app, monkeypatch, caplog):
    provider_detail = "token endpoint echoed provider credential"

    def fail():
        raise RuntimeError(provider_detail)

    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", fail)

    with app.app_context():
        assert mobile_food_discovery.servings("food-77") is None

    assert provider_detail not in caplog.text


def test_raw_serving_adapter_never_logs_provider_error_payload(
        app, monkeypatch, caplog):
    provider_detail = "raw upstream response detail"
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "token")
    monkeypatch.setattr(
        fatsecret, "_fs_get", lambda *args, **kwargs: ProviderResponse({
            "error": {"code": 13, "message": provider_detail},
        }))

    with app.app_context():
        assert mobile_food_discovery.servings("food-77") is None

    assert provider_detail not in caplog.text


def test_raw_barcode_resolution_never_logs_the_value(app, monkeypatch, caplog):
    barcode = "0012345678905"
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "token")
    monkeypatch.setattr(
        fatsecret, "_fs_get", lambda *args, **kwargs: ProviderResponse(
            {"food_id": {"value": "food-77"}}))

    with app.app_context():
        assert fatsecret._food_find_id_by_barcode_raw(barcode) == "food-77"

    assert barcode not in caplog.text
