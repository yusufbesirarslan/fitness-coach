"""G2/G3 synthetic regressions (AXISAI-NUTRITION-READINESS-FOLLOWUP-02, -03).

G2 proves only that HISTORICAL `food:macros:v1:*` values cannot reach QR scores,
picks, proofs or confirmed logs, and that social suggestion logging cannot write
FatSecret values into the per-serving cache. It does not establish retention
compliance, historical provenance or the absence of FatSecret-derived diary
totals in advice; v2 values carry no lineage either.

G3 pins what the Coach's model is told about nutrition values. It does not test
live model output. No network, provider or production resource is used.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.blueprints import social
from app.models import MealLog
from app.prompts import system
from app.services import ai_coach, ai_consent, coach_privacy, foodcache, menu_analysis, fatsecret_boundary

from test_lp15c_native_menu_analysis import (  # noqa: F401
    MACROS, MENU_URL, alice, analyze, bearer, mobile, provider, routed_wire, wire,
)

LOG_PATH = "/api/v1/nutrition/menu/log"
DISH = "Izgara Tavuk"
# Plausible and clearly different from the LLM's 330 kcal, so a leak is visible.
OLD_VALUE = {"calories": 400.0, "protein": 70.0, "carbs": 2.0, "fat": 12.0}
OLD_KEY_PREFIX = "food:macros:v1:per_serving:"


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis

    def setex(self, key, ttl, value):
        self.redis.writes.append(key)
        self.redis.store[key] = value

    def execute(self):
        return []


class _Redis:
    def __init__(self):
        self.store, self.writes = {}, []

    def mget(self, keys):
        return [self.store.get(key) for key in keys]

    def pipeline(self, transaction=False):
        return _Pipeline(self)


def _v1_key(name):
    # The historical key format, spelled out: it must not follow the constant.
    digest = foodcache._macro_redis_key("per_serving", name).rsplit(":", 1)[1]
    return OLD_KEY_PREFIX + digest


@pytest.fixture
def old_cache(monkeypatch):
    """A surviving deployment's Redis: one v1 value, a fresh process's empty L1."""
    redis = _Redis()
    redis.store[_v1_key(DISH)] = json.dumps(OLD_VALUE)
    monkeypatch.setattr(foodcache, "_get_redis", lambda: redis)
    return redis


def _candidates(body):
    return {candidate["name"]: candidate
            for category in body["menu_analysis"]["categories"]
            for candidate in category["candidates"]}


def test_key_format_is_v2():
    assert foodcache._macro_redis_key("per_serving", DISH).startswith(
        "food:macros:v2:per_serving:")
    assert foodcache._macro_redis_key("per_100g", DISH).startswith(
        "food:macros:v2:per_100g:")


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_old_value_reaches_neither_score_nor_pick_nor_proof_nor_log(
        mobile, bearer, alice, wire, provider, old_cache):
    headers = bearer(alice)
    body = analyze(mobile, headers).get_json()
    item = _candidates(body)[DISH]
    assert item["estimate"]["source"] == "llm"
    assert item["nutrition"]["energy_kcal"] == MACROS[DISH]["calories"]

    logged = mobile.post(
        LOG_PATH, headers={**headers, "Idempotency-Key": "menu:" + "e" * 32},
        json={"confirmation_token": item["confirmation_token"], "quantity": 1,
              "slot": "ogle", "confirmed": True})
    assert logged.status_code == 201
    row = MealLog.query.filter_by(user_id=alice.id).one()
    assert row.kalori == MACROS[DISH]["calories"]
    assert row.source == "menu_estimated"


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_old_value_is_not_renewed_so_it_can_expire(
        mobile, bearer, alice, wire, provider, old_cache):
    assert analyze(mobile, bearer(alice)).status_code == 200
    assert old_cache.writes and all(
        key.startswith(fatsecret_boundary.CACHE_PREFIX) for key in old_cache.writes)
    # Left exactly as found: not rewritten, not deleted.
    assert json.loads(old_cache.store[_v1_key(DISH)]) == OLD_VALUE


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_old_value_is_ignored_inside_a_d1_operation_and_no_token_is_requested(
        app, alice, provider, old_cache, monkeypatch):
    def no_token():
        raise AssertionError("no FatSecret token inside a D1 operation")
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", no_token)
    authority = ai_consent.OperationAuthority(alice.id, "menu_extract", 5, "menu_extract")
    with app.test_request_context(), ai_consent.operation_scope(authority):
        result = menu_analysis._analyze_menu_text(
            alice.id, "Ana Yemekler\nIzgara Tavuk\nAdana Kebap\nKunefe yemek menu")
    items = {i["name"]: i for category in result["categories"].values() for i in category}
    assert items[DISH]["macro_source"] == "llm"
    assert items[DISH]["macros"]["calories"] == MACROS[DISH]["calories"]
    assert OLD_VALUE["calories"] not in [
        pick["macros"]["calories"] for pick in result["coach_picks"]]


def _qr_in_d1(app, alice):
    authority = ai_consent.OperationAuthority(alice.id, "menu_extract", 5, "menu_extract")
    with app.test_request_context(), ai_consent.operation_scope(authority):
        result = menu_analysis._analyze_menu_text(
            alice.id, "Ana Yemekler\nIzgara Tavuk\nAdana Kebap\nKunefe yemek menu")
    return {i["name"]: i for category in result["categories"].values() for i in category}


@pytest.fixture
def empty_cache(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(foodcache, "_get_redis", lambda: redis)
    foodcache._macro_cache.clear()
    yield redis
    foodcache._macro_cache.clear()


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_social_fatsecret_values_cannot_reenter_the_qr_cache(
        app, alice, provider, empty_cache, monkeypatch):
    monkeypatch.setattr(social, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(social, "_lookup_macros_fatsecret",
                        lambda items, token: ({name: dict(OLD_VALUE) for name in items}, {}))
    snapshot = SimpleNamespace(message_id=1, body=f"- {DISH}\n- Adana Kebap")
    with app.test_request_context():
        nutrients = social._calculate_meal_suggestion(snapshot)

    # The accepted suggestion still calculates; only the shared write is gone.
    assert nutrients["kalori"] == 2 * OLD_VALUE["calories"]
    assert empty_cache.writes == []
    assert DISH not in foodcache._macro_cache.get("per_serving", {})

    item = _qr_in_d1(app, alice)[DISH]
    assert item["macro_source"] == "llm"
    assert item["macros"]["calories"] == MACROS[DISH]["calories"]


def test_only_menu_analysis_writes_the_per_serving_cache():
    """QR reads `per_serving`; any new writer must be a deliberate decision.
    `basis` defaults to per_serving, so a call without it counts as a writer."""
    root = Path(menu_analysis.__file__).resolve().parents[2]
    writers = set()
    for folder in ("app", "fitx_mcp"):
        for path in (root / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "_cache_macros"):
                    basis = next((k.value for k in node.keywords if k.arg == "basis"), None)
                    if not (isinstance(basis, ast.Constant) and basis.value == "per_100g"):
                        writers.add(path.relative_to(root).as_posix())
    assert writers == set()  # advisory estimates have a separate attested writer


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_per_100g_values_do_not_reach_qr(app, alice, provider, empty_cache):
    # The namespace FatSecret search writes when live.
    foodcache._cache_macros({DISH: dict(OLD_VALUE)}, basis="per_100g")
    item = _qr_in_d1(app, alice)[DISH]
    assert item["macro_source"] == "llm"
    assert item["macros"]["calories"] == MACROS[DISH]["calories"]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_llm_estimates_are_still_estimated_and_cached_for_qr(app, alice, provider, empty_cache):
    assert _qr_in_d1(app, alice)[DISH]["macro_source"] == "llm"
    assert fatsecret_boundary._key(DISH) in empty_cache.store
    foodcache._macro_cache.clear()  # the next scan hits Redis, as a fresh process would
    again = _qr_in_d1(app, alice)[DISH]
    assert again["macro_source"] == "cache"
    assert again["macros"]["calories"] == MACROS[DISH]["calories"]


def test_prompt_limits_the_deterministic_claim_to_what_the_server_computes():
    line = next(l for l in system.COACH_SYSTEM_PROMPT.splitlines()
                if "`compatibility_score` ve porsiyon" in l)
    assert "deterministik" in line and "makrolar ise tahmin olabilir" in line
    assert "ASLA değiştirme" in line and "AYNEN" in line  # must still match what is saved
    assert "makrolar ve `compatibility_score` DETERMİNİSTİK" not in system.COACH_SYSTEM_PROMPT


def test_stale_exactness_instruction_in_the_tool_result_never_reaches_the_model():
    raw = json.dumps({"status": "staged", "staged_food": {"calories": 100.0},
                      "instruction": "sayılar kesindir"})
    projected = coach_privacy.tool_result("fetch_nutrition_and_stage_log", raw)
    assert "kesindir" not in json.dumps(projected, ensure_ascii=False)


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("plan_tools", [False, True])
def test_coach_prompt_names_no_provider_and_states_estimates(language, adaptive, plan_tools):
    prompt = system.build_coach_system(
        language, adaptive_plan_context=adaptive, plan_mutation_tools=plan_tools)
    assert "fatsecret" not in prompt.lower()
    assert "ASLA tahmin etme" not in prompt
    assert "gerçek" not in prompt.split("TEMEL GÖREV")[0].split("VERİ KAYNAKLARIN")[1].split(
        "Günlük aktivite")[0]
    assert "yaklaşık tahmin" in prompt  # tool values
    assert "günlüğüne kaydettiği değerler" in prompt  # user-entered values
    assert "statik besin tablosu" in prompt and "yapay zekâ tahmini" in prompt
    assert "kendin makro uydurma" in prompt  # still must call the tool


def test_food_tool_description_names_no_provider_and_says_estimate():
    tool = next(t for t in ai_coach._COACH_TOOL_DEFS
                if t["name"] == "fetch_nutrition_and_stage_log")
    assert "fatsecret" not in tool["description"].lower()
    assert "gerçek makro" not in tool["description"]
    assert "tahmin" in tool["description"]
    assert "KALICI KAYIT" in tool["description"]  # staging contract is unchanged
