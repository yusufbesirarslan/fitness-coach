"""Strict whole-meal estimation; no provider, food-search or cache network."""
import json
import pytest

from app.services.nutrition_plan_mutation import EditError
from app.services.nutrition_plan_mutation import estimation

FACTS = {"calories": 170, "protein": 10, "carbs": 10, "fat": 10}


@pytest.mark.parametrize("item", ["Eggs", "Chicken - 100", "Rice - one bowl",
    "Rice - 1 bowl", "Bread - 0 g", "Bread - -5 g", "Bread - 100000 g",
    "Bread - 1-2 g", "Bread - 1/2 g", "Bread - 100 g or 200 g", "Eggs - 31 adet"])
def test_unknown_portions_refused_before_dispatch(item, monkeypatch):
    from app.services import ai_nutrition
    monkeypatch.setattr(ai_nutrition, "_estimate_macros_llm_batch", lambda *a, **k: pytest.fail("AI dispatched"))
    with pytest.raises(EditError, match="item_quantity_unavailable"):
        estimation.estimate_meal([item])


@pytest.mark.parametrize("item", ["Eggs - 2 adet", "Chicken - 100 g", "Milk - 0.5 l", "Rice - 150gr"])
def test_bounded_explicit_quantities(item):
    estimation.validate_quantity(item)


def test_entire_meal_uses_one_strict_batch_and_sums_only_new_facts(monkeypatch):
    from app.services import ai_nutrition
    calls = []
    def batch(items, *, strict):
        calls.append((items, strict))
        return {item: dict(FACTS) for item in items}
    monkeypatch.setattr(ai_nutrition, "_estimate_macros_llm_batch", batch)
    items = ["Chicken - 100 g", "Bread - 60 g"]
    assert estimation.estimate_meal(items) == {"kalori": 340, "protein": 20, "karb": 20, "yag": 20}
    assert calls == [(items, True)]


@pytest.mark.parametrize("bad", ["", "{}", "[]", '```json\n{}\n```',
    '{"Rice - 100 g": {"calories": 170}}',
    '{"Rice - 100 g": {"calories": 170}, "Rice - 100 g": {}}',
    json.dumps({"other": FACTS}),
    *[json.dumps({"Rice - 100 g": {**FACTS, "calories": v}})
      for v in ("170", True, -1, float("nan"), float("inf"), 999999, 0)],
    json.dumps({"Rice - 100 g": {**FACTS, "source": "claimed"}})])
def test_partial_ambiguous_inconsistent_or_unbounded_response_is_not_repaired(bad):
    with pytest.raises(EditError, match="estimate_unavailable"):
        estimation.strict_response(bad, ["Rice - 100 g"])


def test_strict_adapter_never_uses_legacy_zero_fill_or_json_repair(monkeypatch):
    from app.services import ai_nutrition
    calls = []
    def heavy(*args, **kwargs):
        calls.append(kwargs)
        return '{"Rice - 100 g": {"calories": "170"}}'
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", heavy)
    with pytest.raises(EditError):
        ai_nutrition._estimate_macros_llm_batch(["Rice - 100 g"], strict=True)
    assert len(calls) == 1 and calls[0]["feature"] == "nutrition_plan"


def test_aggregate_bounds_are_checked(monkeypatch):
    from app.services import ai_nutrition
    items = [f"Food {chr(65+i)} - 5000 g" for i in range(30)]
    facts = {"calories": 500, "protein": 50, "carbs": 75, "fat": 0}
    monkeypatch.setattr(ai_nutrition, "_estimate_macros_llm_batch", lambda *a, **k: {x: facts for x in items})
    with pytest.raises(EditError, match="estimate_unavailable"):
        estimation.estimate_meal(items)
