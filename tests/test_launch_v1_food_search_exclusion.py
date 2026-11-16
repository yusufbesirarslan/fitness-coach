"""V1 closed defaults. Synthetic tests only; no provider or runtime access."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog
from app.services import (fatsecret, fatsecret_boundary, launch_food_search,
                          mobile_auth, mobile_food_discovery, mobile_log_food)
from app.services.provider_food_snapshot import resolve_provider_food, ProviderPersistenceHeld


def forbidden(*args, **kwargs):
    pytest.fail("Excluded path reached a provider/cache/transport")


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/nutrition/foods/search?q=synthetic"),
    ("GET", "/api/v1/nutrition/foods/fatsecret/fixture/servings"),
    ("GET", "/api/v1/nutrition/foods/barcode?code=5000159407236"),
    ("GET", "/api/food/search?q=synthetic"),
    ("GET", "/api/food/fixture/servings"),
    ("GET", "/api/food/servings-by-name?name=synthetic"),
    ("GET", "/api/food/barcode?code=5000159407236"),
    ("POST", "/api/food/barcode/add"),
])
def test_closed_before_auth_cache_or_fallback(app, raw_client, monkeypatch, method, path):
    for name in ("search", "servings", "barcode_lookup"):
        monkeypatch.setattr(mobile_food_discovery, name, forbidden)
    monkeypatch.setattr(fatsecret, "_get_fs_session", forbidden)
    # Neither a release-like env setting nor config/headers/consent can reopen.
    monkeypatch.setenv("FATSECRET_FOOD_SEARCH_ENABLED", "1")
    app.config["FOOD_SEARCH_AVAILABLE"] = True
    response = raw_client.open(path, method=method)
    assert response.status_code == 404
    assert response.json["error"]["code"] == launch_food_search.ERROR_CODE
    assert response.json["error"]["retryable"] is False
    assert response.headers["Cache-Control"] == "no-store"


def test_entire_browser_blueprint_closed(app, raw_client):
    rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("food.")]
    assert rules
    for rule in rules:
        assert not launch_food_search.available()
        path = rule.build({key: "fixture" for key in rule.arguments}, append_unknown=False)[1]
        for method in rule.methods - {"OPTIONS", "HEAD"}:
            assert raw_client.open(path, method=method).status_code == 404


@pytest.mark.parametrize("warm", [False, True])
def test_provider_egress_closed_even_with_credentials_and_cached_token(app, monkeypatch, warm):
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "synthetic")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "synthetic")
    monkeypatch.setattr(fatsecret, "_fs_token_cache", {
        "token": "fixture" if warm else None, "expires_at": 10**12})
    monkeypatch.setattr(fatsecret, "_get_fs_session", forbidden)
    for call in (fatsecret._get_fatsecret_token,
                 lambda: fatsecret._fs_get("https://fatsecret.invalid"),
                 lambda: fatsecret._fs_post("https://fatsecret.invalid")):
        with pytest.raises(launch_food_search.FoodSearchExcluded):
            call()


@pytest.mark.parametrize("call", [
    lambda: mobile_food_discovery.search("synthetic"),
    lambda: mobile_food_discovery.servings("fixture"),
    lambda: mobile_food_discovery.barcode_lookup("5000159407236"),
])
def test_service_discovery_denied_before_cached_provider_data(app, monkeypatch, call):
    monkeypatch.setattr(fatsecret, "_food_search_raw", forbidden)
    monkeypatch.setattr(fatsecret, "_food_get_raw", forbidden)
    with pytest.raises(launch_food_search.FoodSearchExcluded):
        call()


def test_retention_permission_does_not_grant_market_authority(app):
    assert fatsecret_boundary.PROVIDER_PERSISTENCE_PERMISSION_REF
    assert not fatsecret_boundary.provider_persistence_allowed()
    with pytest.raises(ProviderPersistenceHeld):
        resolve_provider_food("fatsecret", "fixture", "serving", Decimal("1"))


def test_native_manual_write_read_edit_delete_preserved(app, client, make_user, monkeypatch):
    from tests.test_mobile_log_food_api import manual_command
    user = make_user("v1-manual")
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: mobile_auth.MobilePrincipal(
        user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    monkeypatch.setattr(fatsecret, "_get_fs_session", forbidden)
    headers = {"Authorization": "Bearer fixture", "Idempotency-Key": "v1-manual-fixture"}
    response = client.post("/api/v1/nutrition/logs", json=manual_command(), headers=headers)
    assert response.status_code == 201, response.json
    assert response.json["meal"]["source"] == "manual"
    assert response.json["meal"]["nutrition"]["energy_kcal"] == 420
    assert client.get("/api/v1/nutrition/diary/today", headers=headers).status_code == 200
    meal = client.get("/api/v1/nutrition/diary/today", headers=headers).json["meals"][0]
    mutation_headers = dict(headers, **{"If-Match": f'"{meal["revision"]}"'})
    path = f'/api/v1/nutrition/logs/{meal["id"]}'
    changed = client.patch(path, headers=mutation_headers,
                           json={"operation": "set_slot", "slot": "aksam"})
    assert changed.status_code == 200, changed.json
    current = client.get("/api/v1/nutrition/diary/today", headers=headers).json["meals"][0]
    mutation_headers["If-Match"] = f'"{current["revision"]}"'
    assert client.delete(path, headers=mutation_headers).status_code == 204
    assert client.get("/api/v1/nutrition/diary/today", headers=headers).json["meals"] == []
    assert MealLog.query.filter_by(user_id=user.id).count() == 0


def test_provider_import_refused_before_replay_read(app, monkeypatch):
    from tests.test_mobile_log_food_api import provider_command
    from app.services.mobile_log_food import service
    monkeypatch.setattr(service, "_existing_or_conflict", forbidden)
    with pytest.raises(ProviderPersistenceHeld):
        mobile_log_food.log_food(1, "fixture-key", mobile_log_food.parse_command(provider_command()))


def test_provider_import_denied_even_before_replay_lookup(app, monkeypatch):
    from tests.test_mobile_log_food_api import provider_command
    from app.services.mobile_log_food import service
    monkeypatch.setattr(service, "_existing_or_conflict", forbidden)
    command = mobile_log_food.parse_command(provider_command())
    with pytest.raises(ProviderPersistenceHeld):
        mobile_log_food.log_food(1, "fixture-replay", command)


def test_identifiable_provider_copy_cannot_become_new_manual_import(app, make_user, monkeypatch):
    from tests.test_mobile_log_food_api import manual_command
    from app.services import provider_retention
    monkeypatch.setattr(provider_retention, "copied_provenance", lambda *a: ([], None))
    user = make_user("held-copy")
    command = mobile_log_food.parse_command(manual_command())
    with pytest.raises(ProviderPersistenceHeld):
        mobile_log_food.log_food(user.id, "held-copy-fixture", command)
    assert MealLog.query.filter_by(user_id=user.id).count() == 0


from test_training_execution_boundary import training_page  # noqa: F401


@pytest.mark.parametrize("language", ["en", "tr"])
def test_browser_shows_no_provider_controls_or_automatic_serving_refresh(
        app, auth_user, training_page, language):
    from playwright.sync_api import expect
    from test_nutrition_vnext_pr3_daily_browser import seed, open_today
    seed(app, auth_user.id, language)
    page, traffic, _, _ = training_page
    open_today(page)
    page.locator("#log-food-btn").click()
    for method in ("search", "barcode", "build-meal"):
        expect(page.locator(f'#log-sheet [data-method="{method}"]')).to_be_hidden()
        expect(page.locator(f'#log-sheet [data-method="{method}"]')).to_be_disabled()
    expect(page.locator('#log-sheet [data-method="menu"]')).to_be_visible()
    expect(page.locator('#food-search-input')).to_be_disabled()
    page.evaluate("""() => renderDiary({meals: [{id: 1, meal_name: 'Kahvaltı',
      is_logged: false, totals: {calories: 10, protein: 0, carbs: 1, fat: 0},
      items: [{id: 1, food_name: 'Recorded provider item', fatsecret_food_id: 'fixture',
        serving_id: 's1', serving_quantity: 1, serving_description: 'Recorded serving',
        calories: 10, protein: 0, carbs: 1, fat: 0}]}],
      totals: {calories: 10, protein: 0, carbs: 1, fat: 0}})""")
    assert page.locator('.diary-serving-select, .diary-qty-input').count() == 0
    assert page.locator('.diary-food-search:enabled').count() == 0
    assert not [url for url, _, _ in traffic if '/api/food/' in url]
