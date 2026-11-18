"""Food Search release gate: default closed, endpoint-scoped when released.

Deterministic FatSecret fixture at the HTTP session seam. No real provider.
"""
from types import SimpleNamespace

import pytest

from app.models import MealLog
from app.services import fatsecret, launch_food_search, mobile_auth

FLAG = launch_food_search.FLAG_KEY

SEARCH_PAYLOAD = {"foods": {"food": [
    {"food_id": "33691", "food_name": "Banana", "brand_name": None,
     "food_description": "Per 100g - Calories: 89kcal | Fat: 0.33g"},
    {"food_id": "4881223", "food_name": "Banana Chips", "brand_name": "Acme"},
]}}

FOOD_PAYLOAD = {"food": {
    "food_id": "33691",
    "food_name": "Banana",
    "servings": {"serving": [
        {"serving_id": "32978", "serving_description": "1 medium",
         "metric_serving_amount": "118.000", "metric_serving_unit": "g",
         "calories": "105", "protein": "1.29", "carbohydrate": "26.95",
         "fat": "0.39"},
        {"serving_id": "34009", "serving_description": "100 g",
         "metric_serving_amount": "100.000", "metric_serving_unit": "g",
         "calories": "89", "protein": "1.09", "carbohydrate": "22.84",
         "fat": "0.33"},
    ]},
}}


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("status")


class FakeFatSecret:
    """Answers token, foods.search and food.get*; records every method used."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def post(self, url, **kwargs):
        self.calls.append("token")
        return _Response({"access_token": "fixture-token", "expires_in": 3600})

    def get(self, url, params=None, **kwargs):
        method = (params or {}).get("method")
        self.calls.append(method)
        if self.fail:
            raise TimeoutError("fixture timeout")
        if method == "foods.search":
            return _Response(SEARCH_PAYLOAD)
        if method and method.startswith("food.get"):
            return _Response(FOOD_PAYLOAD)
        return _Response({"error": {"code": "2"}})


@pytest.fixture
def provider(monkeypatch):
    fake = FakeFatSecret()
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "fixture-secret")
    monkeypatch.setattr(fatsecret, "_fs_token_cache", {"token": None, "expires_at": 0})
    monkeypatch.setattr(fatsecret, "_get_fs_session", lambda: fake)
    return fake


@pytest.fixture
def released(app, monkeypatch):
    monkeypatch.setitem(app.config, FLAG, True)


@pytest.fixture
def owner(make_user):
    return make_user("food-search-release")


@pytest.fixture
def headers(monkeypatch, owner):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            owner, SimpleNamespace(id=1), {"sub": owner.cognito_sub}))
    return {"Authorization": "Bearer opaque-access-credential"}


def _log(client, headers, key, quantity=1.5, serving="32978", source="search"):
    return client.post("/api/v1/nutrition/logs", json={
        "kind": "provider_backed", "provider": "fatsecret", "food_id": "33691",
        "serving_id": serving, "quantity": quantity, "slot": "kahvalti",
        "discovery_source": source,
    }, headers={**headers, "Idempotency-Key": key})


# ── Default closed ─────────────────────────────────────────────────────────
def test_flag_defaults_off(app):
    assert app.config[FLAG] is False
    with app.test_request_context():
        assert not launch_food_search.released()
        assert not launch_food_search.available()


def test_unreleased_journey_is_closed_and_sends_nothing(raw_client, headers, provider):
    assert raw_client.get("/api/v1/nutrition/foods/search?q=banana",
                          headers=headers).status_code == 404
    assert raw_client.get("/api/v1/nutrition/foods/fatsecret/33691/servings",
                          headers=headers).status_code == 404
    response = _log(raw_client, headers, "unreleased-key-1")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "FOOD_PROVIDER_UNAVAILABLE"
    assert provider.calls == []
    assert MealLog.query.count() == 0


# ── Released: the full journey ─────────────────────────────────────────────
def test_search_returns_provider_identities(raw_client, headers, provider, released):
    response = raw_client.get("/api/v1/nutrition/foods/search?q=banana", headers=headers)
    assert response.status_code == 200
    assert response.json == {"foods": [
        {"provider": "fatsecret", "food_id": "33691", "name": "Banana", "brand": ""},
        {"provider": "fatsecret", "food_id": "4881223", "name": "Banana Chips",
         "brand": "Acme"},
    ]}
    assert response.headers["Cache-Control"] == "no-store"
    assert MealLog.query.count() == 0


def test_empty_results_are_an_empty_list(raw_client, headers, provider, released,
                                         monkeypatch):
    monkeypatch.setattr(fatsecret, "_fs_get",
                        lambda url, **kw: _Response({"foods": {"total_results": "0"}}))
    response = raw_client.get("/api/v1/nutrition/foods/search?q=zzzz", headers=headers)
    assert response.status_code == 200
    assert response.json == {"foods": []}


@pytest.mark.parametrize("query", ["a", "x" * 101, "   "])
def test_malformed_query_rejected_before_provider(raw_client, headers, provider,
                                                  released, query):
    response = raw_client.get("/api/v1/nutrition/foods/search",
                              query_string={"q": query}, headers=headers)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_FOOD_QUERY"
    assert provider.calls == []


def test_servings_and_log_agree_numerically(raw_client, headers, provider, released):
    food = raw_client.get("/api/v1/nutrition/foods/fatsecret/33691/servings",
                          headers=headers).json["food"]
    serving = next(s for s in food["servings"] if s["serving_id"] == "32978")
    assert serving["nutrition"] == {"energy_kcal": 105.0, "protein_g": 1.29,
                                    "carbohydrate_g": 26.95, "fat_g": 0.39}

    response = _log(raw_client, headers, "release-log-key-1", quantity=1.5)
    assert response.status_code == 201
    meal = response.json["meal"]
    # Preview × quantity == persisted, computed server-side from provider truth.
    assert meal["nutrition"]["energy_kcal"] == pytest.approx(157.5)
    assert meal["nutrition"]["protein_g"] == pytest.approx(1.935)
    assert meal["nutrition"]["carbohydrate_g"] == pytest.approx(40.425)
    assert meal["nutrition"]["fat_g"] == pytest.approx(0.585)
    assert meal["source"] == "search"

    row = MealLog.query.one()
    assert row.provider_items and row.provider_acquired_at is not None


def test_replay_is_idempotent_and_diary_totals_update(raw_client, headers, provider,
                                                      released):
    first = _log(raw_client, headers, "release-log-key-2", quantity=2)
    second = _log(raw_client, headers, "release-log-key-2", quantity=2)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json["meal"]["id"] == second.json["meal"]["id"]
    assert MealLog.query.count() == 1

    conflict = _log(raw_client, headers, "release-log-key-2", quantity=3)
    assert conflict.status_code == 409
    assert MealLog.query.count() == 1

    diary = raw_client.get("/api/v1/nutrition/diary/today", headers=headers).json
    assert len(diary["meals"]) == 1
    assert diary["meals"][0]["id"] == first.json["meal"]["id"]
    assert diary["totals"]["energy_kcal"] == pytest.approx(210.0)
    assert diary["totals"]["protein_g"] == pytest.approx(2.58)


def test_unknown_serving_is_not_found_and_writes_nothing(raw_client, headers, provider,
                                                         released):
    response = _log(raw_client, headers, "release-log-key-3", serving="missing")
    assert response.status_code == 404
    assert MealLog.query.count() == 0


def test_serving_missing_a_macro_is_not_found_not_retryable(raw_client, headers,
                                                            provider, released,
                                                            monkeypatch):
    food = {"food": {**FOOD_PAYLOAD["food"], "servings": {"serving": [
        {**FOOD_PAYLOAD["food"]["servings"]["serving"][0], "fat": None}]}}}
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Response(food))
    response = _log(raw_client, headers, "release-log-key-5")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "FOOD_NOT_FOUND"
    assert MealLog.query.count() == 0


def test_barcode_sourced_provider_log_stays_refused(raw_client, headers, provider,
                                                   released):
    response = _log(raw_client, headers, "release-log-key-6", source="barcode")
    assert response.status_code == 503
    assert response.json["error"] == {**response.json["error"],
                                      "code": "FOOD_PROVIDER_UNAVAILABLE",
                                      "retryable": False}
    assert provider.calls == []
    assert MealLog.query.count() == 0


def test_provider_failure_keeps_manual_logging_working(raw_client, headers, provider,
                                                       released, caplog):
    provider.fail = True
    search = raw_client.get("/api/v1/nutrition/foods/search?q=secret-query",
                            headers=headers)
    assert search.status_code == 503
    assert search.json["error"] == {**search.json["error"],
                                    "code": "FOOD_PROVIDER_UNAVAILABLE",
                                    "retryable": True}
    assert "secret-query" not in caplog.text
    assert "fixture-token" not in caplog.text

    provider_log = _log(raw_client, headers, "release-log-key-4")
    assert provider_log.status_code in (404, 503)
    assert MealLog.query.count() == 0

    manual = raw_client.post("/api/v1/nutrition/logs", json={
        "kind": "manual", "description": "Banana", "slot": "ara_ogun",
        "nutrition": {"energy_kcal": 100, "protein_g": 1, "carbohydrate_g": 25,
                      "fat_g": 0},
    }, headers={**headers, "Idempotency-Key": "release-manual-key-1"})
    assert manual.status_code == 201
    assert MealLog.query.count() == 1


# ── Released: everything else stays closed ─────────────────────────────────
def test_barcode_stays_closed_when_released(raw_client, headers, provider, released):
    response = raw_client.get("/api/v1/nutrition/foods/barcode?code=5000159407236",
                              headers=headers)
    assert response.status_code == 404
    assert response.json["error"]["code"] == launch_food_search.ERROR_CODE
    assert provider.calls == []


def test_browser_food_blueprint_stays_closed_when_released(app, raw_client, provider,
                                                           released):
    rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("food.")]
    assert rules
    for rule in rules:
        path = rule.build({key: "fixture" for key in rule.arguments},
                          append_unknown=False)[1]
        for method in rule.methods - {"OPTIONS", "HEAD"}:
            assert raw_client.open(path, method=method).status_code == 404
    assert provider.calls == []


def test_other_fatsecret_callers_stay_closed_when_released(app, provider, released):
    """Coach, QR Menu, social and workers share the client; none is in scope."""
    with app.test_request_context("/ask", method="POST"):
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            fatsecret._get_fatsecret_token()
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            fatsecret._fs_get("https://fatsecret.invalid")
    with app.app_context():  # worker / CLI
        assert launch_food_search.released()
        assert not launch_food_search.available()
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            fatsecret._get_fatsecret_token()
    assert provider.calls == []


def test_browser_template_context_stays_closed_when_released(app, released):
    with app.test_request_context("/nutrition"):
        app.preprocess_request()
        context = {}
        app.update_template_context(context)
        assert context["food_search_available"] is False


def test_worker_probe_follows_the_flag(app, monkeypatch):
    import app.config as config_mod
    from app.jobs import tasks
    monkeypatch.setattr(config_mod, "FATSECRET_BASE_URL", "")
    with app.app_context():
        assert tasks.sample_fatsecret_proxy() == "disabled"
        monkeypatch.setitem(app.config, FLAG, True)
        assert tasks.sample_fatsecret_proxy() == "unconfigured"


def test_invalid_flag_value_refuses_boot():
    from app.feature_flags import FeatureFlagConfigurationError, resolve_rollout_flags
    with pytest.raises(FeatureFlagConfigurationError):
        resolve_rollout_flags({FLAG: "true"})
    assert resolve_rollout_flags({})[FLAG] is False
    assert resolve_rollout_flags({FLAG: "1"})[FLAG] is True
