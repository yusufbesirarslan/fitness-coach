"""FatSecret terms boundary with the provider configured, and search and barcode kept.

Terms 1.5 allows only IDs past 24 hours and 1.7(iii) prohibits advice from the API.
Reuses the PR #485 trace. Everything is synthetic and no request leaves the process.

The strict expected failure records the input this candidate does not close: diary
totals reach Coach once AI opens. It is unreachable while AI stays closed.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config as config_mod
from app.blueprints import food as food_bp
from app.extensions import db
from app.models import BarcodeFoodCache, CustomMeal, CustomMealItem, MealLog, UserSession
from app.services import ai_coach, ai_consent, ai_launch, barcode, coach_privacy, fatsecret, foodcache
from app.services import menu_analysis, mobile_auth
from app.timeutil import day_key

CODE = "5000159407236"
FOOD_ID = "77777"
SERVING_ID = "s1"
STORED_NAME = "Stored Provider Bar"
LIVE_NAME = "Live Provider Bar"
PROVIDER_KCAL = 731.0
ADVICE_KEYS = {"analysis", "recommendations", "portion_recommendation", "daily_context",
               "goal_impact"}
ADVICE_FUNCTIONS = ("recommend_for_food", "analyze_food", "goal_impact_for_add")
ROOT = Path(__file__).resolve().parents[1]


def _provider_food(name=LIVE_NAME):
    return {"food_id": FOOD_ID, "name": name, "brand": "Acme", "servings": [{
        "serving_id": SERVING_ID, "serving_description": "1 bar",
        "metric_serving_amount": 60, "metric_serving_unit": "g",
        "calories": PROVIDER_KCAL, "protein": 21, "carbs": 23, "fat": 6}]}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(barcode, "_food_find_by_barcode", lambda _code: _provider_food())


@pytest.fixture
def stored_row(app):
    stale = barcode.normalize_food_model(CODE, _provider_food(STORED_NAME))
    db.session.add(BarcodeFoodCache(barcode=CODE, food_id=FOOD_ID, food_name=STORED_NAME,
                                    brand="Acme", payload=stale))
    db.session.commit()


@pytest.fixture
def env_absent(monkeypatch):
    """Provider unavailable: removed configuration, or an outage. Nothing may leave."""
    def forbidden(*_a, **_k):
        raise AssertionError("network call with no provider configured")
    monkeypatch.setattr(config_mod, "FATSECRET_BASE_URL", "")
    monkeypatch.delenv("FATSECRET_CLIENT_ID", raising=False)
    monkeypatch.delenv("FATSECRET_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(fatsecret, "_fs_token_cache", {"token": None, "expires_at": 0})
    monkeypatch.setattr(fatsecret, "_fs_get", forbidden)
    monkeypatch.setattr(fatsecret, "_fs_post", forbidden)


@pytest.fixture
def native(make_user, monkeypatch):
    user = make_user("fs-terms-native")
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: mobile_auth.MobilePrincipal(
        user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    return user, {"Authorization": "Bearer fixture"}


@pytest.fixture
def surviving_redis(monkeypatch):
    """A deployed Redis still holding old mixed-origin v1 values, and an empty L1."""
    store = {}
    for basis in ("per_100g", "per_serving"):
        digest = foodcache._macro_redis_key(basis, "tavuk").rsplit(":", 1)[1]
        store[f"food:macros:v1:{basis}:{digest}"] = json.dumps(
            {"calories": PROVIDER_KCAL, "protein": 70.0, "carbs": 2.0, "fat": 12.0})
    redis = SimpleNamespace(mget=lambda keys: [store.get(k) for k in keys],
                            pipeline=lambda transaction=False: SimpleNamespace(
                                setex=lambda k, _t, v: store.__setitem__(k, v), execute=lambda: []))
    monkeypatch.setattr(foodcache, "_get_redis", lambda: redis)
    foodcache._macro_cache.clear()
    yield store
    foodcache._macro_cache.clear()


# ── Path 3: web barcode ────────────────────────────────────────────────────

def test_web_barcode_keeps_working_with_facts_and_no_advice(live, client, auth_user):
    response = client.get(f"/api/food/barcode?code={CODE}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["name"] == LIVE_NAME and data["servings"][0]["calories"] == PROVIDER_KCAL
    assert not ADVICE_KEYS & set(data)


def test_web_barcode_reads_live_truth_over_a_stored_row(live, stored_row, client, auth_user):
    data = client.get(f"/api/food/barcode?code={CODE}").get_json()
    assert data["name"] == LIVE_NAME and data["source"] == "fatsecret"
    assert STORED_NAME not in json.dumps(data)
    assert BarcodeFoodCache.query.one().food_name == STORED_NAME  # kept, not refreshed or purged


def test_web_barcode_stores_no_new_row(live, client, auth_user):
    client.get(f"/api/food/barcode?code={CODE}")
    assert BarcodeFoodCache.query.count() == 0


def test_web_barcode_add_uses_live_truth_and_logs(live, stored_row, client, auth_user):
    response = client.post("/api/food/barcode/add", json={
        "barcode": CODE, "meal": "Ara Öğün", "serving_id": SERVING_ID, "serving_quantity": 1})
    assert response.status_code == 200
    assert not ADVICE_KEYS & set(response.get_json())
    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert LIVE_NAME in row.yemekler and row.source == "barcode"


def test_web_barcode_add_replay_carries_no_targets(live, client, auth_user):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2000, goal="kas kazanma"))
    db.session.commit()
    body = {"barcode": CODE, "meal": "Ara Öğün", "serving_id": SERVING_ID, "serving_quantity": 1}
    headers = {"Idempotency-Key": "018f47d2-a2c7-7f52-a5b0-123456789abd"}
    for _ in range(2):
        response = client.post("/api/food/barcode/add", json=body, headers=headers)
        assert response.status_code == 200
        assert not ADVICE_KEYS & set(response.get_json())
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


def test_no_route_can_reach_the_advice_functions():
    for path in (ROOT / "app" / "blueprints").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not [name for name in ADVICE_FUNCTIONS if name in text], path
    source = (ROOT / "app" / "services" / "barcode.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "build_lookup_response":
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert not names & {"recommend_for_food", "analyze_food"}


# ── Path 2: macro cache ────────────────────────────────────────────────────

def test_fatsecret_module_caches_static_values_only():
    source = (ROOT / "app" / "services" / "fatsecret.py").read_text(encoding="utf-8")
    writers = {fn.name for fn in ast.walk(ast.parse(source)) if isinstance(fn, ast.FunctionDef)
               for n in ast.walk(fn) if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "_cache_macros"}
    assert writers == {"_food_search_static"}


def test_menu_analysis_keeps_provider_values_out_of_the_shared_cache(app, make_user, monkeypatch):
    user = make_user("fs-terms-menu")
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    db.session.commit()
    foodcache._macro_cache.clear()
    monkeypatch.setattr(foodcache, "_get_redis", lambda: None)
    monkeypatch.setattr(menu_analysis, "redis_client", None)
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **k: {"Main": ["Provider Dish", "Estimated Dish"]})
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", lambda names, *_a, **_k: (
        {"Provider Dish": {"calories": PROVIDER_KCAL, "protein": 21., "carbs": 23., "fat": 6.}}, {}))
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", lambda names, *_a, **_k: {
        n: {"calories": 300., "protein": 20., "carbs": 30., "fat": 10.} for n in names})
    menu_analysis._analyze_menu_text(user.id, "Restaurant menu: Provider Dish, Estimated Dish")
    cached = foodcache._macro_cache.get("per_serving", {})
    assert "Provider Dish" not in cached
    assert "Estimated Dish" in cached
    foodcache._macro_cache.clear()


def test_old_cache_does_not_reach_web_search(surviving_redis, client, auth_user, monkeypatch):
    monkeypatch.setattr(food_bp, "_coach_search_food", lambda q: [])
    response = client.get("/api/food/search?q=tavuk")
    assert response.status_code == 200 and response.get_json()["results"] == []


# ── Search and barcode stay ────────────────────────────────────────────────

def test_web_search_still_returns_provider_results(client, auth_user, monkeypatch):
    foodcache._macro_cache.clear()
    monkeypatch.setattr(food_bp, "_coach_search_food", lambda q: [{"name": "Chicken", "macros": {"calories": 173}}])
    assert client.get("/api/food/search?q=chicken").get_json()["results"][0]["name"] == "Chicken"


def test_manual_logging_needs_no_provider(env_absent, native, raw_client):
    user, headers = native
    response = raw_client.post(
        "/api/v1/nutrition/logs", headers={**headers, "Idempotency-Key": "fs-terms-log-0001"},
        json={"kind": "manual", "description": "Ev yemeği", "slot": "ogle", "nutrition": {
            "energy_kcal": 400, "protein_g": 20, "carbohydrate_g": 40, "fat_g": 10}})
    assert response.status_code == 201
    assert MealLog.query.filter_by(user_id=user.id).one().source == "manual"


# ── With no provider reachable, stored values are no fallback ──────────────

def test_unavailable_provider_serves_no_stored_payload_on_web(env_absent, stored_row, client, auth_user):
    response = client.get(f"/api/food/barcode?code={CODE}")
    assert response.status_code == 404
    assert STORED_NAME not in response.get_data(as_text=True)


def test_unavailable_provider_writes_no_diary_row_from_a_stored_payload(
        env_absent, stored_row, client, auth_user):
    response = client.post("/api/food/barcode/add", json={
        "barcode": CODE, "meal": "Ara Öğün", "serving_id": SERVING_ID, "serving_quantity": 1})
    assert response.status_code == 404
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


@pytest.mark.parametrize("path", [
    "/api/v1/nutrition/foods/search?q=tavuk",
    f"/api/v1/nutrition/foods/fatsecret/{FOOD_ID}/servings",
    f"/api/v1/nutrition/foods/barcode?code={CODE}",
])
def test_unavailable_provider_on_native_is_typed_and_leaks_nothing(
        env_absent, stored_row, native, raw_client, path):
    _, headers = native
    response = raw_client.get(path, headers=headers)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "FOOD_PROVIDER_UNAVAILABLE"
    assert STORED_NAME not in response.get_data(as_text=True)


def test_stored_provider_item_does_not_log_without_provider(env_absent, client, auth_user):
    meal = CustomMeal(user_id=auth_user.id, meal_name="Mix", date_key=day_key())
    db.session.add(meal)
    db.session.flush()
    db.session.add(CustomMealItem(
        custom_meal_id=meal.id, food_name=STORED_NAME, grams=60, calories=PROVIDER_KCAL,
        protein=21, carbs=23, fat=6, fatsecret_food_id=FOOD_ID, serving_id=SERVING_ID,
        serving_quantity=1))
    db.session.commit()
    response = client.post(f"/api/diary/meal/{meal.id}/log")
    assert response.status_code == 503
    assert response.get_json()["error"] == "food_provider_unavailable"
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_health_reports_unconfigured_without_failing(env_absent, client):
    assert client.get("/health?deep=1").get_json()["fatsecret_proxy"] == "unconfigured"


# ── Path 1: diary totals. Closed only because AI is closed ─────────────────

def test_ai_stays_closed_at_this_candidate():
    """Failing here means D1 is being published: FatSecret advice must be decided first.

    docs/compliance/fatsecret-launch-boundary.md lists what that decision needs.
    """
    assert ai_consent.POLICIES == {} and ai_launch.APPROVED_SERVICE_COHORT_VERSION is None


def test_web_search_llm_fallback_stays_closed(env_absent, client, auth_user, monkeypatch):
    dispatched = []
    monkeypatch.setattr(ai_consent, "dispatch", lambda *a, **k: dispatched.append(a))
    assert client.get("/api/food/search?q=bilinmeyen-yemek").status_code == 200
    assert dispatched == []


@pytest.mark.xfail(strict=True, reason=(
    "OPEN BEFORE AI OPENS: diary totals have no reliable lineage and enter Coach tools "
    "(#485 blocker 2). Needs FatSecret's written permission or the #485 boundary."))
def test_provider_derived_diary_calories_must_not_enter_coach_tools(app, make_user):
    user = make_user("fs-terms-history")
    db.session.add(MealLog(user_id=user.id, ogun="Lunch", yemekler="Provider food", kalori=731,
                           protein=25, karb=60, yag=15, tarih=day_key(), source="search"))
    db.session.commit()
    raw = ai_coach._tool_query_fitx_metrics(user.id, "calories", "today")
    assert "731" not in json.dumps(json.loads(coach_privacy.tool_result("query_fitx_metrics", raw)))
