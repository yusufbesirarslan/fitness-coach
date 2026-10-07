"""LP15-A contracts exercised through native routes and the HTTP boundary."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests

from app.extensions import db
from app.models import BarcodeFoodCache, MealLog
from app.services import fatsecret, mobile_auth

PREFIX = "/api/v1/nutrition"
READS = ["/foods/search?q=food", "/foods/fatsecret/A/servings",
         "/foods/barcode?code=012345678905"]
NUTRITION = {"energy_kcal": 120.0, "protein_g": 4.0,
             "carbohydrate_g": 20.0, "fat_g": 3.0}


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return deepcopy(self.payload)


def serving(identity, calories="120", unit="g", amount="50"):
    return {"serving_id": identity, "serving_description": "1 portion",
            "metric_serving_unit": unit, "metric_serving_amount": amount,
            "calories": calories, "protein": "4", "carbohydrate": "20", "fat": "3"}


def food(identity):
    return {"food_id": identity, "food_name": "Aynı yemek Çığ Şiş",
            "brand_name": "Brand", "servings": {"serving": [
                serving(identity + "-1"), serving(identity + "-2", "240")]}}


@pytest.fixture
def native(make_user, monkeypatch):
    user = make_user("lp15-owner", language="tr")
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda raw:
                        mobile_auth.MobilePrincipal(user, SimpleNamespace(id=1),
                                                    {"sub": user.cognito_sub}))
    return user, {"Authorization": "Bearer opaque-test",
                  "Idempotency-Key": "lp15-intent-0001"}


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "test-token")

    def get(url, **kwargs):
        params = kwargs["params"]
        calls.append(deepcopy(params))
        if params["method"] == "foods.search":
            return Response({"foods": {"food": [food("A"), food("B")]}})
        if params["method"] == "food.find_id_for_barcode":
            return Response({"food_id": {"value": "A"}})
        return Response({"food": food(params["food_id"])})

    monkeypatch.setattr(fatsecret, "_fs_get", get)
    return calls


def command(food_id="A", serving_id="A-1", quantity=1, **extra):
    return dict(kind="provider_backed", provider="fatsecret", food_id=food_id,
                serving_id=serving_id, quantity=quantity, slot="ogle",
                discovery_source="search", **extra)


def error(response, status, code, retryable=False):
    assert response.status_code == status
    assert set(response.json) == {"error"}
    envelope = response.json["error"]
    assert set(envelope) == {"code", "message", "retryable", "request_id"}
    assert envelope["code"] == code
    assert envelope["retryable"] is retryable
    assert isinstance(envelope["message"], str) and envelope["message"]
    assert isinstance(envelope["request_id"], str) and envelope["request_id"]
    assert response.headers["Cache-Control"] == "no-store"


def test_duplicate_name_identity(raw_client, native, upstream):
    user, headers = native
    first = raw_client.get(PREFIX + "/foods/search?q=şiş", headers=headers)
    assert first.status_code == 200
    assert first.json == {"foods": [
        {"provider": "fatsecret", "food_id": identity,
         "name": "Aynı yemek Çığ Şiş", "brand": "Brand"}
        for identity in ("A", "B")]}
    user.language = "en"
    db.session.commit()
    refresh = raw_client.get(PREFIX + "/foods/search?q=şiş",
                             headers=dict(headers, **{"Accept-Language": "tr"}))
    assert refresh.json == first.json
    for identity in ("A", "B"):
        detail = raw_client.get(PREFIX + f"/foods/fatsecret/{identity}/servings",
                                headers=headers)
        assert detail.json["food"]["food_id"] == identity
    assert MealLog.query.count() == 0


def test_duplicate_serving_label_identity(raw_client, native, upstream):
    _, headers = native
    response = raw_client.get(PREFIX + "/foods/fatsecret/A/servings", headers=headers)
    assert response.status_code == 200
    assert set(response.json["food"]) == {"provider", "food_id", "name", "brand", "servings"}
    options = response.json["food"]["servings"]
    assert [item["description"] for item in options] == ["1 portion", "1 portion"]
    assert [item["serving_id"] for item in options] == ["A-1", "A-2"]
    assert set(options[0]) == {"serving_id", "description", "nutrition", "metric_mass",
                               "nutrition_per_100g"}
    assert options[0]["nutrition"] == NUTRITION
    assert options[0]["metric_mass"] == {"amount": 50.0, "unit": "g"}
    for index, item in enumerate(options):
        response = raw_client.post(PREFIX + "/logs", json=command(serving_id=item["serving_id"]),
                                   headers=dict(headers, **{"Idempotency-Key": f"lp15-serving-{index}"}))
        assert response.status_code == 201
        assert response.json["meal"]["nutrition"]["energy_kcal"] == (index + 1) * 120


def test_cross_food_serving(raw_client, native, upstream):
    _, headers = native
    other = raw_client.get(PREFIX + "/foods/fatsecret/B/servings", headers=headers)
    foreign = other.json["food"]["servings"][0]["serving_id"]
    response = raw_client.post(PREFIX + "/logs", json=command(serving_id=foreign), headers=headers)
    error(response, 404, "FOOD_NOT_FOUND")
    assert MealLog.query.count() == 0


@pytest.mark.parametrize("quantity", [1, 1.5, "1.5", "1.123456789", 1e-4])
def test_quantity_and_macro_authority(raw_client, native, upstream, quantity):
    user, headers = native
    response = raw_client.post(PREFIX + "/logs", json=command(quantity=quantity), headers=headers)
    assert response.status_code == 201
    assert response.json["meal"]["nutrition"]["energy_kcal"] == pytest.approx(120 * float(quantity))
    assert MealLog.query.one().user_id == user.id
    count = len(upstream)
    replay = raw_client.post(PREFIX + "/logs", json=command(quantity=quantity), headers=headers)
    assert replay.status_code == 200 and replay.json == response.json
    assert len(upstream) == count and MealLog.query.count() == 1


@pytest.mark.parametrize("quantity", [0, -1, 1000.1, True, None, "1,5", "1.000,5", "NaN", "Infinity"])
def test_invalid_quantity(raw_client, native, upstream, quantity):
    response = raw_client.post(PREFIX + "/logs", json=command(quantity=quantity), headers=native[1])
    error(response, 400, "INVALID_LOG_FOOD_COMMAND")
    assert upstream == [] and MealLog.query.count() == 0


@pytest.mark.parametrize("query", ["", " ", "a", "x" * 101])
def test_bounded_query(raw_client, native, upstream, query):
    response = raw_client.get(PREFIX + "/foods/search", query_string={"q": query}, headers=native[1])
    error(response, 400, "INVALID_FOOD_QUERY")
    assert upstream == []


@pytest.mark.parametrize("query", ["  Çığ şiş  ", "x" * 100, "'&method=food.get?food_id=B"])
def test_query_passed_as_data(raw_client, native, upstream, query):
    response = raw_client.get(PREFIX + "/foods/search", query_string={"q": query}, headers=native[1])
    assert response.status_code == 200
    assert upstream == [{"method": "foods.search", "search_expression": query.strip(),
                         "format": "json", "max_results": 8}]


def test_search_bound_and_no_cursor(raw_client, native, upstream, monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["params"])
        return Response({"foods": {"food": [food(str(i)) for i in range(20)]}})

    monkeypatch.setattr(fatsecret, "_fs_get", get)
    response = raw_client.get(PREFIX + "/foods/search?q=food&cursor=malformed&limit=999999&page=9",
                              headers=native[1])
    assert response.status_code == 200
    assert len(response.json["foods"]) == 8
    assert len(calls) == 1 and calls[0]["max_results"] == 8
    assert "page_number" not in calls[0]


@pytest.mark.parametrize("code", ["012345", "01234567", "012345678905", "0012345678905"])
def test_barcode_normalization(raw_client, native, upstream, code):
    response = raw_client.get(PREFIX + "/foods/barcode", query_string={"code": code}, headers=native[1])
    assert response.status_code == 200
    assert upstream[0]["barcode"] == code.rjust(13, "0")
    detail = raw_client.get(PREFIX + "/foods/fatsecret/A/servings", headers=native[1])
    assert response.json == detail.json
    assert BarcodeFoodCache.query.count() == 0 and MealLog.query.count() == 0


@pytest.mark.parametrize("code", ["", "1234567", "1" * 14, "12-345678905", "٠١٢٣٤٥٦٧٨٩٠٥", "１２３４５６７８"])
def test_barcode_validation(raw_client, native, upstream, code):
    response = raw_client.get(PREFIX + "/foods/barcode", query_string={"code": code}, headers=native[1])
    error(response, 400, "INVALID_BARCODE")
    assert upstream == []


def test_barcode_cache_only_reuses_food_identity(raw_client, native, upstream):
    db.session.add(BarcodeFoodCache(barcode="012345678905", food_id="A", food_name="Legacy",
                                   brand="Old", payload={"servings": [{"id": "100g_calc"}]}))
    db.session.commit()
    response = raw_client.get(PREFIX + "/foods/barcode?code=012345678905", headers=native[1])
    assert response.status_code == 200
    assert [s["serving_id"] for s in response.json["food"]["servings"]] == ["A-1", "A-2"]
    assert all(p["method"] != "food.find_id_for_barcode" for p in upstream)
    logged = raw_client.post(PREFIX + "/logs", headers=native[1],
                             json=dict(command(), discovery_source="barcode"))
    assert logged.status_code == 201


@pytest.mark.parametrize("path,payload,code,status", [
    (READS[0], {"foods": {}}, None, 200),
    (READS[0], {"foods": {"food": []}}, None, 200),
    (READS[1], {"error": {"code": "106"}}, "FOOD_NOT_FOUND", 404),
    (READS[1], {"food": None}, "FOOD_NOT_FOUND", 404),
    (READS[2], {"food_id": {"value": "0"}}, "FOOD_NOT_FOUND", 404),
])
def test_true_empty_and_not_found(raw_client, native, upstream, monkeypatch, path, payload, code, status):
    monkeypatch.setattr(fatsecret, "_fs_get", lambda *a, **kw: Response(payload))
    response = raw_client.get(PREFIX + path, headers=native[1])
    if code:
        error(response, status, code)
    else:
        assert response.status_code == 200 and response.json == {"foods": []}


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("failure", ["timeout", "connection", "token", "rate", "unavailable", "auth",
                                     "http429", "http503", "json", "shape"])
def test_provider_error_sanitization(raw_client, native, upstream, monkeypatch, caplog, path, failure):
    secret = "UPSTREAM_SECRET https://provider.invalid/?credential=SECRET"

    def fail_token():
        raise RuntimeError(secret)

    def get(*a, **kw):
        if failure == "timeout":
            raise requests.Timeout(secret)
        if failure == "connection":
            raise requests.ConnectionError(secret)
        if failure in {"rate", "unavailable", "auth"}:
            return Response({"error": {"code": {"rate": 11, "unavailable": 20, "auth": 13}[failure],
                                       "message": secret}})
        if failure.startswith("http"):
            return Response({"foods": {}}, int(failure[4:]))
        return Response(ValueError(secret) if failure == "json" else [secret])

    monkeypatch.setattr(fatsecret, "_fs_get", get)
    if failure == "token":
        monkeypatch.setattr(fatsecret, "_get_fatsecret_token", fail_token)
    response = raw_client.get(PREFIX + path, headers=native[1])
    error(response, 503, "FOOD_PROVIDER_UNAVAILABLE", True)
    assert secret not in response.get_data(as_text=True) + caplog.text
    assert MealLog.query.count() == 0


@pytest.mark.parametrize("unit", ["ml", "oz", "", None])
def test_non_gram_serving_stays_loggable(raw_client, native, upstream, monkeypatch, unit):
    payload = food("A")
    payload["servings"]["serving"] = serving("A-1", unit=unit)
    monkeypatch.setattr(fatsecret, "_fs_get", lambda *a, **kw: Response({"food": payload}))
    response = raw_client.get(PREFIX + READS[1], headers=native[1])
    option = response.json["food"]["servings"][0]
    assert option["metric_mass"] is None and option["nutrition_per_100g"] is None
    logged = raw_client.post(PREFIX + "/logs", json=command(quantity=1.5), headers=native[1])
    assert logged.status_code == 201
    assert logged.json["meal"]["nutrition"]["energy_kcal"] == 180


@pytest.mark.parametrize("path", READS)
def test_native_auth_required(raw_client, upstream, path):
    response = raw_client.get(PREFIX + path)
    assert response.status_code == 401
    assert set(response.json["error"]) == {"code", "message", "retryable", "request_id"}
    assert upstream == []


@pytest.mark.parametrize("payload", [
    {"foods": {"food": ""}}, {"foods": {"food": [None]}},
    {"foods": {"food": [{"food_name": "Name without ID"}]}},
    {"foods": {"food": [{"food_id": {"secret": "hidden"}}]}},
])
def test_malformed_search_payload(raw_client, native, upstream, monkeypatch, payload):
    monkeypatch.setattr(fatsecret, "_fs_get", lambda *a, **kw: Response(payload))
    error(raw_client.get(PREFIX + READS[0], headers=native[1]), 503, "FOOD_PROVIDER_UNAVAILABLE", True)


@pytest.mark.parametrize("options", [[serving("same"), serving("same")],
                                      [dict(serving("A-1"), serving_id=None)], ["bad"]])
def test_malformed_serving_identity(raw_client, native, upstream, monkeypatch, options):
    payload = food("A")
    payload["servings"]["serving"] = options
    monkeypatch.setattr(fatsecret, "_fs_get", lambda *a, **kw: Response({"food": payload}))
    error(raw_client.get(PREFIX + READS[1], headers=native[1]), 503, "FOOD_PROVIDER_UNAVAILABLE", True)


def test_provider_returned_food_identity_must_match(raw_client, native, upstream, monkeypatch):
    monkeypatch.setattr(fatsecret, "_fs_get", lambda *a, **kw: Response({"food": food("B")}))
    error(raw_client.get(PREFIX + READS[1], headers=native[1]), 503, "FOOD_PROVIDER_UNAVAILABLE", True)


def test_normalized_nutrition_cannot_overflow_json(raw_client, native, upstream, monkeypatch):
    payload = food("A")
    payload["servings"]["serving"] = serving("A-1", calories="1e308", amount="1e-308")
    monkeypatch.setattr(fatsecret, "_fs_get", lambda *a, **kw: Response({"food": payload}))
    error(raw_client.get(PREFIX + READS[1], headers=native[1]), 503, "FOOD_PROVIDER_UNAVAILABLE", True)


def test_serving_fallback_success_and_mixed_failure(raw_client, native, upstream, monkeypatch):
    calls = []

    def get(*a, **kw):
        calls.append(kw["params"]["method"])
        if len(calls) == 1:
            return Response({"error": {"code": 10}})
        return Response({"food": food("A")})

    monkeypatch.setattr(fatsecret, "_fs_get", get)
    response = raw_client.get(PREFIX + READS[1], headers=native[1])
    assert response.status_code == 200 and calls == ["food.get.v4", "food.get.v2"]
    calls.clear()

    def mixed(*a, **kw):
        calls.append(kw["params"]["method"])
        if len(calls) == 1:
            raise requests.Timeout("private detail")
        return Response({"error": {"code": 106}})

    monkeypatch.setattr(fatsecret, "_fs_get", mixed)
    error(raw_client.get(PREFIX + READS[1], headers=native[1]), 503, "FOOD_PROVIDER_UNAVAILABLE", True)
    assert len(calls) == 3


def test_log_provider_failure_preserves_write_error_contract(raw_client, native, upstream, monkeypatch):
    def fail(*a, **kw):
        raise requests.Timeout("private upstream detail")

    monkeypatch.setattr(fatsecret, "_fs_get", fail)
    response = raw_client.post(PREFIX + "/logs", json=command(), headers=native[1])
    error(response, 503, "NUTRITION_TEMPORARILY_UNAVAILABLE", True)
    assert "private upstream detail" not in response.get_data(as_text=True)
    assert MealLog.query.count() == 0


def test_log_and_diary_are_owner_bound(raw_client, native, upstream, make_user, monkeypatch):
    owner, headers = native
    created = raw_client.post(PREFIX + "/logs", json=command(), headers=headers)
    assert created.status_code == 201
    other = make_user("lp15-other")
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda raw:
                        mobile_auth.MobilePrincipal(other, SimpleNamespace(id=2), {"sub": other.cognito_sub}))
    diary = raw_client.get(PREFIX + "/diary/today?user_id=" + str(owner.id), headers=headers)
    assert diary.status_code == 200 and diary.json["meals"] == []
    rejected = raw_client.post(PREFIX + "/logs", json=command(user_id=owner.id), headers=headers)
    error(rejected, 400, "INVALID_LOG_FOOD_COMMAND")
    logged = raw_client.post(PREFIX + "/logs", json=command(), headers=headers)
    assert logged.status_code == 201
    assert logged.json["meal"]["id"] != created.json["meal"]["id"]
    assert {row.user_id for row in MealLog.query.all()} == {owner.id, other.id}
