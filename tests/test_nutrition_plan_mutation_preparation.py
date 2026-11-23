"""C1 preparation proves canonical state, exact targeting and honest totals.

No live providers, no records outside the synthetic SQLite test fixture.
This is NOT qualification of the future confirmation/persistence boundary.
"""
from dataclasses import replace
from datetime import datetime
import json

import pytest

from app.extensions import db
from app.models import NutritionPlan
from app.services.nutrition_plan_mutation import (
    Edit, EditError, finish_edit, parse_snapshot, read_current_plan, target_edit,
)


@pytest.fixture
def document():
    return {
        "isim": "Synthetic plan",
        "kahvalti": {"yemekler": ["Eggs - 2", "Bread - 60 g"],
                     "kalori": 400, "protein": 20, "karb": 35, "yag": 15},
        "aksam": {"yemekler": ["Rice - 100 g"],
                  "kalori": 200, "protein": 5, "karb": 40, "yag": 2},
        # Deliberately inconsistent old day totals MUST NOT enter arithmetic.
        "toplam_kalori": 9999, "toplam_protein": 999,
        "toplam_karb": 999, "toplam_yag": 999,
    }


@pytest.fixture
def estimate():
    return {"kalori": 450, "protein": 32, "karb": 38, "yag": 19}


def _snapshot(document):
    return parse_snapshot(json.dumps(document, ensure_ascii=False, indent=2))


def test_read_uses_canonical_newest_row_owner_scope_and_changes_nothing(
        app, make_user, document):
    owner, stranger = make_user("c1-owner"), make_user("c1-stranger")
    created = datetime(2026, 11, 18, 10)
    for name in ("Old", "Current"):
        db.session.add(NutritionPlan(
            user_id=owner.id, created_at=created,
            plan_data=json.dumps({**document, "isim": name})))
    db.session.commit()
    before = [(row.id, row.plan_data, row.created_at)
              for row in NutritionPlan.query.order_by(NutritionPlan.id)]
    assert json.loads(read_current_plan(owner.id).raw)["isim"] == "Current"
    assert read_current_plan(stranger.id) is None
    assert [(row.id, row.plan_data, row.created_at)
            for row in NutritionPlan.query.order_by(NutritionPlan.id)] == before


@pytest.mark.parametrize("raw", [
    "not-json", "[]", "null", "{}", '{"kahvalti": {}, "kahvalti": {}}',
    '{"kahvalti": {"kalori": NaN}}',
    '{"kahvalti": {"kalori": 10}, "secret": "unrecognized"}',
    " " * (16 * 1024 + 1),
])
def test_unreadable_snapshot_is_refused_without_guessing(raw):
    with pytest.raises(EditError, match="^plan_unreadable$"):
        parse_snapshot(raw)


def test_replace_reestimates_whole_meal_and_rebuilds_day_totals(document, estimate):
    snapshot = _snapshot(document)
    target = target_edit(snapshot, Edit("replace", "kahvalti", "Eggs - 2", "Chicken - 100 g"))
    assert target.items == ("Chicken - 100 g", "Bread - 60 g")
    prepared = finish_edit(target, estimate)
    after = json.loads(prepared.after.raw)
    assert after["kahvalti"] == {"yemekler": list(target.items), **estimate, "tahmini": True}
    assert after["aksam"] == document["aksam"]
    assert [after[k] for k in ("toplam_kalori", "toplam_protein", "toplam_karb", "toplam_yag")] == [650, 37, 78, 21]
    assert prepared.requires_confirmation is True
    assert prepared.nutrient_source == "independent_whole_meal_estimate"
    assert prepared.before.raw == snapshot.raw
    assert json.loads(snapshot.raw) == document  # exact old bytes retained


@pytest.mark.parametrize("kind,item,new,items", [
    ("add", "Apple - 1", None, ["Eggs - 2", "Bread - 60 g", "Apple - 1"]),
    ("remove", "Eggs - 2", None, ["Bread - 60 g"]),
])
def test_add_and_remove_recalculate_the_entire_affected_meal(
        document, estimate, kind, item, new, items):
    target = target_edit(_snapshot(document), Edit(kind, "kahvalti", item, new))
    after = json.loads(finish_edit(target, estimate).after.raw)
    assert after["kahvalti"]["yemekler"] == items
    assert after["kahvalti"]["kalori"] == estimate["kalori"]


def test_noop_preserves_exact_bytes_and_requires_no_estimate(document):
    snapshot = _snapshot(document)
    target = target_edit(snapshot, Edit("replace", "kahvalti", "Eggs - 2", "Eggs - 2"))
    prepared = finish_edit(target, None)
    assert prepared.changed is False
    assert prepared.before == prepared.after == snapshot
    assert prepared.requires_confirmation is False
    assert prepared.nutrient_source is None


@pytest.mark.parametrize("edit,code", [
    (Edit("remove", "kahvalti", "Egg"), "item_not_found"),
    (Edit("remove", "ogle", "Eggs - 2"), "meal_not_found"),
    (Edit("remove", "aksam", "Rice - 100 g"), "empty_meal"),
    (Edit("add", "kahvalti", " eggs - 2 "), "item_already_present"),
    (Edit("replace", "kahvalti", "Eggs - 2", "Bread - 60 g"), "item_already_present"),
    (Edit("replace", "kahvalti", "Eggs - 2", "\nInjected"), "invalid_item"),
    (Edit("replace", "kahvalti", "Eggs - 2", ""), "invalid_item"),
    (Edit("remove", "kahvalti", "Eggs - 2", "unused"), "invalid_edit"),
    (Edit([], "kahvalti", "Eggs - 2"), "invalid_edit"),
    (Edit("add", "invalid", "Eggs - 2"), "invalid_meal"),
])
def test_bad_or_ambiguous_intent_is_rejected(document, edit, code):
    with pytest.raises(EditError, match=f"^{code}$"):
        target_edit(_snapshot(document), edit)


def test_duplicate_item_target_is_ambiguous(document):
    document["kahvalti"]["yemekler"].append(" eggs - 2 ")
    with pytest.raises(EditError, match="^ambiguous_item$"):
        target_edit(_snapshot(document), Edit("remove", "kahvalti", "Eggs - 2"))


@pytest.mark.parametrize("bad", [None, {}, {"kalori": 500},
    {"kalori": 500, "protein": None, "karb": 30, "yag": 10},
    {"kalori": "500", "protein": 20, "karb": 30, "yag": 10},
    {"kalori": 500, "protein": True, "karb": 30, "yag": 10},
    {"kalori": float("nan"), "protein": 20, "karb": 30, "yag": 10},
    {"kalori": float("inf"), "protein": 20, "karb": 30, "yag": 10},
    {"kalori": -1, "protein": 20, "karb": 30, "yag": 10},
    {"kalori": 500, "protein": 20, "karb": 30, "yag": 10, "source": "claimed"},
])
def test_bad_or_partial_estimate_is_never_zero_filled(document, bad):
    target = target_edit(_snapshot(document), Edit("remove", "kahvalti", "Eggs - 2"))
    with pytest.raises(EditError, match="^estimate_unavailable$"):
        finish_edit(target, bad)


def test_unknown_other_meal_macro_refuses_day_total_calculation(document, estimate):
    del document["aksam"]["protein"]
    target = target_edit(_snapshot(document), Edit("remove", "kahvalti", "Eggs - 2"))
    with pytest.raises(EditError, match="^meal_totals_unavailable$"):
        finish_edit(target, estimate)


def test_caller_cannot_substitute_a_different_target(document, estimate):
    target = target_edit(_snapshot(document), Edit("remove", "kahvalti", "Eggs - 2"))
    with pytest.raises(EditError, match="^invalid_target$"):
        finish_edit(replace(target, items=("Forged",)), estimate)


def test_fractional_totals_have_no_accumulated_binary_drift(document, estimate):
    document["aksam"]["protein"] = 0.2
    estimate["protein"] = 0.1
    target = target_edit(_snapshot(document), Edit("remove", "kahvalti", "Eggs - 2"))
    assert json.loads(finish_edit(target, estimate).after.raw)["toplam_protein"] == 0.3


def test_oversized_storage_number_is_a_bounded_refusal(document):
    document["kahvalti"]["kalori"] = 10 ** 400
    with pytest.raises(EditError, match="^plan_unreadable$"):
        _snapshot(document)


def test_oversized_estimate_is_a_bounded_refusal(document, estimate):
    target = target_edit(_snapshot(document), Edit("remove", "kahvalti", "Eggs - 2"))
    estimate["kalori"] = 10 ** 400
    with pytest.raises(EditError, match="^estimate_unavailable$"):
        finish_edit(target, estimate)
