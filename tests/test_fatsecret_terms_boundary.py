"""FatSecret terms boundary with the provider configured, and search and barcode kept.

Terms 1.5 allows only IDs past 24 hours and 1.7(iii) prohibits advice from the API.
Reuses the PR #485 trace. Everything is synthetic and no request leaves the process.

Path 1 (diary totals reaching AI advice) was closed by #497, which shipped the #485
boundary. Its gate below runs with every approved D1 purpose registered.
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
from app.services import context_builder, fatsecret_boundary, menu_analysis, mobile_auth
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


@pytest.mark.usefixtures("provider_persistence_permitted")  # D-B2 mechanics
def test_web_barcode_add_uses_live_truth_and_logs(live, stored_row, client, auth_user):
    response = client.post("/api/food/barcode/add", json={
        "barcode": CODE, "meal": "Ara Öğün", "serving_id": SERVING_ID, "serving_quantity": 1})
    assert response.status_code == 200
    assert not ADVICE_KEYS & set(response.get_json())
    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert LIVE_NAME in row.yemekler and row.source == "barcode"


@pytest.mark.usefixtures("provider_persistence_permitted")  # D-B2 mechanics
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
    assert "Estimated Dish" not in cached  # independent estimates use the attested namespace
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


@pytest.mark.usefixtures("provider_persistence_permitted")  # D-B2 mechanics
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


@pytest.mark.usefixtures("provider_persistence_permitted")  # D-B2 mechanics
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


# ── Path 1: diary totals. Closed by the #497 advice boundary ───────────────
# This gate does NOT rely on AI being closed. It runs against the real D1
# registry with every approved purpose published, and proves the fail-closed
# rule directly. docs/compliance/fatsecret-launch-boundary.md records the basis.

D1_PURPOSES = {"training.plan", "coach.conversation", "nutrition.plan",
               "pump_check.analysis", "qr_menu.analysis"}


def _no_provider(*_a, **_k):
    raise AssertionError("FatSecret call from an AI advice path")


@pytest.fixture
def advice_probe(app, make_user, monkeypatch):
    user = make_user("fs-terms-advice")
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    db.session.commit()
    monkeypatch.setattr(foodcache, "_macro_cache", {"per_100g": {}, "per_serving": {}})
    monkeypatch.setattr(foodcache, "_get_redis", lambda: None)
    monkeypatch.setattr(menu_analysis, "redis_client", None)
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **k: {"Main": ["Independent dish"]})
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", lambda names, *a, **k: {
        n: {"calories": 330., "protein": 30., "carbs": 25., "fat": 12.} for n in names})
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", _no_provider)
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", _no_provider)
    monkeypatch.setattr(ai_coach, "_food_search_fatsecret", _no_provider)

    def analyze():
        authority = ai_consent.OperationAuthority(user.id, "menu_extract", 1, "menu_extract")
        with app.test_request_context(), ai_consent.operation_scope(authority):
            return menu_analysis.web_analysis_payload(
                menu_analysis._analyze_menu_text(user.id, "Independent restaurant menu text"))
    return user, analyze


def test_fatsecret_advice_boundary_holds_with_all_d1_purposes_registered(app, advice_probe):
    """With all five D1 purposes published, no FatSecret-derived value reaches AI advice.

    A new or removed purpose fails here first: re-decide the FatSecret basis for it.
    """
    assert {p.purpose for p in ai_consent.POLICIES.values()} == D1_PURPOSES
    user, analyze = advice_probe

    # Measured-empty day: the existing personal budget is unchanged (no reduction).
    empty = analyze()
    assert empty["advice_available"] is True and empty["remaining"] is not None
    assert ai_coach._remaining_macros_for_user(user.id) is not None

    # A FatSecret-sourced row today makes the whole day unproven for advice.
    db.session.add(MealLog(user_id=user.id, ogun="Lunch", yemekler="Provider food",
                           kalori=PROVIDER_KCAL, protein=21, karb=23, yag=6,
                           tarih=day_key(), source="search"))
    db.session.commit()
    sentinel = str(int(PROVIDER_KCAL))
    assert fatsecret_boundary.intake_unproven(user.id)

    # Coach: no remaining budget, no calorie tool value, no number in context.
    assert ai_coach._remaining_macros_for_user(user.id) is None
    raw = ai_coach._tool_query_fitx_metrics(user.id, "calories", "today")
    assert json.loads(coach_privacy.tool_result("query_fitx_metrics", raw)) == {
        "status": "unsupported", "metric_type": "calories"}
    context = context_builder.fetch_coach_context(
        user.id, question="nutrition", language="en", handoff="nutrition-day")
    assert sentinel not in context
    assert fatsecret_boundary.notice("en") in context  # the handoff ran, not swallowed

    # QR: advice withheld (never zero), no ranking, no provider call, no number.
    payload = analyze()
    assert payload["advice_available"] is False
    assert payload["consumed"] is None and payload["remaining"] is None
    assert payload["coach_picks"] == []
    assert sentinel not in json.dumps(payload)


def test_d1_coach_food_lookup_never_calls_fatsecret(app, monkeypatch):
    monkeypatch.setattr(ai_coach, "_food_search_fatsecret", _no_provider)
    monkeypatch.setattr(ai_coach, "_food_search_static", lambda _q: [])
    monkeypatch.setattr(ai_coach, "_food_search_llm", lambda _q: [{"name": "estimate"}])
    authority = ai_consent.OperationAuthority(1, "coach", 1, "coach")
    with app.test_request_context(), ai_consent.operation_scope(authority):
        assert ai_coach._coach_search_food("tavuk") == [{"name": "estimate"}]


def test_nutrition_plan_generation_reads_no_ledger_or_provider():
    # Static pin on the native generation path's own functions and the shared
    # generator module; an indirect read through a new helper needs its own test.
    generation = (ROOT / "app" / "services" / "nutrition_plan_generation.py").read_text(
        encoding="utf-8")
    plan = ast.parse((ROOT / "app" / "services" / "nutrition_native" / "plan.py").read_text(
        encoding="utf-8"))
    path = {"generate_proposals", "_generate_authorized_proposals", "_generation_target"}
    names = {n.id if isinstance(n, ast.Name) else n.attr
             for fn in ast.walk(plan) if isinstance(fn, ast.FunctionDef) and fn.name in path
             for n in ast.walk(fn) if isinstance(n, (ast.Name, ast.Attribute))}
    forbidden = {"MealLog", "fatsecret", "fatsecret_boundary", "provider_retention",
                 "CustomMealItem", "BarcodeFoodCache"}
    assert len(path & {fn.name for fn in ast.walk(plan) if isinstance(fn, ast.FunctionDef)}) == 3
    assert not names & forbidden
    assert not [w for w in forbidden if w in generation]


def test_web_search_llm_fallback_stays_closed(env_absent, client, auth_user, monkeypatch):
    dispatched = []
    monkeypatch.setattr(ai_consent, "dispatch", lambda *a, **k: dispatched.append(a))
    assert client.get("/api/food/search?q=bilinmeyen-yemek").status_code == 200
    assert dispatched == []


def test_provider_derived_diary_calories_must_not_enter_coach_tools(app, make_user):
    user = make_user("fs-terms-history")
    db.session.add(MealLog(user_id=user.id, ogun="Lunch", yemekler="Provider food", kalori=731,
                           protein=25, karb=60, yag=15, tarih=day_key(), source="search"))
    db.session.commit()
    raw = ai_coach._tool_query_fitx_metrics(user.id, "calories", "today")
    assert "731" not in json.dumps(json.loads(coach_privacy.tool_result("query_fitx_metrics", raw)))
