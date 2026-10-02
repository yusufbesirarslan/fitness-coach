"""NUTR-PR7 — native planned meal → consumed food (MealLog).

    python -m pytest tests/test_nutrition_vnext_pr7_native_planned_meal.py -q

Identity = (exact plan revision, planned-meal token derived from the plan row
and slot) — never a name, a slot label or a calorie value. One explicit command
→ one MealLog. Same key + same command → the confirmed original; same key +
anything else → 409; stale plan → no write.
"""
import json

import pytest

from app.models import MealLog
from app.services.nutrition_native import plan as native_plan

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    PLAN_DOC, bearer, error_of, native, no_provider, quoted, save_plan_row,
)

PLAN = "/api/v1/nutrition/plan"
DIARY = "/api/v1/nutrition/diary/today"


def current(native, headers):
    return native.get(PLAN, headers=headers).get_json()["plan"]


def log(native, headers, meal_id, revision, key="planned-key-0001", body=None):
    hdrs = dict(headers)
    if revision is not None:
        hdrs["If-Match"] = quoted(revision)
    if key is not None:
        hdrs["Idempotency-Key"] = key
    kwargs = {} if body is None else {"json": body}
    return native.post(f"{PLAN}/meals/{meal_id}/log", headers=hdrs, **kwargs)


def ledger(user_id):
    return MealLog.query.filter_by(user_id=user_id).order_by(MealLog.id).all()


@pytest.fixture
def owner(app, make_user, bearer):
    user = make_user("planned")
    save_plan_row(user.id)
    return user, bearer(user)


def test_one_explicit_command_writes_one_canonical_meal(app, native, owner, no_provider):
    user, headers = owner
    plan = current(native, headers)
    breakfast = plan["meals"][0]
    response = log(native, headers, breakfast["id"], plan["revision"])
    assert response.status_code == 201, response.get_data(as_text=True)
    meal = response.get_json()["meal"]
    assert meal["slot"] == "kahvalti" and meal["source"] == "ai_plan"
    assert meal["nutrition"] == {"energy_kcal": 450.0, "protein_g": 30.0,
                                 "carbohydrate_g": 50.0, "fat_g": 12.0}
    rows = ledger(user.id)
    assert len(rows) == 1 and rows[0].source == "ai_plan"
    assert rows[0].yemekler == "Oats - 60g, Milk - 200ml"
    # Planned becomes Logged only through the canonical diary read.
    diary = native.get(DIARY, headers=headers).get_json()
    assert [m["id"] for m in diary["meals"]] == [meal["id"]]


def test_same_key_same_command_replays_the_confirmed_meal(app, native, owner, no_provider):
    user, headers = owner
    plan = current(native, headers)
    meal_id = plan["meals"][0]["id"]
    first = log(native, headers, meal_id, plan["revision"])
    again = log(native, headers, meal_id, plan["revision"])
    assert first.status_code == 201 and again.status_code == 200
    assert again.get_json() == first.get_json()
    assert len(ledger(user.id)) == 1


def test_same_key_different_command_is_a_conflict(app, native, owner, no_provider):
    user, headers = owner
    plan = current(native, headers)
    assert log(native, headers, plan["meals"][0]["id"], plan["revision"]).status_code == 201
    other = log(native, headers, plan["meals"][1]["id"], plan["revision"])
    assert other.status_code == 409
    assert error_of(other)["code"] == "IDEMPOTENCY_CONFLICT"
    assert error_of(other)["retryable"] is False
    assert len(ledger(user.id)) == 1


def test_a_logfood_key_cannot_be_replayed_as_a_planned_meal(app, native, owner, no_provider):
    user, headers = owner
    logged = native.post("/api/v1/nutrition/logs", headers=dict(
        headers, **{"Idempotency-Key": "shared-key-0001"}), json={
        "kind": "manual", "description": "Apple", "slot": "ara_ogun",
        "nutrition": {"energy_kcal": 52, "protein_g": 0, "carbohydrate_g": 14,
                      "fat_g": 0}})
    assert logged.status_code == 201
    plan = current(native, headers)
    response = log(native, headers, plan["meals"][0]["id"], plan["revision"],
                   key="shared-key-0001")
    assert response.status_code == 409
    assert len(ledger(user.id)) == 1


def test_intentional_second_serving_needs_a_distinct_key(app, native, owner, no_provider):
    user, headers = owner
    plan = current(native, headers)
    meal_id = plan["meals"][0]["id"]
    assert log(native, headers, meal_id, plan["revision"], key="serving-0001").status_code == 201
    assert log(native, headers, meal_id, plan["revision"], key="serving-0002").status_code == 201
    assert len(ledger(user.id)) == 2


def test_stale_plan_revision_writes_nothing(app, native, owner, no_provider):
    user, headers = owner
    plan = current(native, headers)
    meal_id = plan["meals"][0]["id"]
    save_plan_row(user.id, document=dict(PLAN_DOC, isim="Replaced"))  # newer row wins
    response = log(native, headers, meal_id, plan["revision"])
    assert response.status_code == 412
    assert error_of(response)["code"] == "STALE_NUTRITION_PLAN"
    assert ledger(user.id) == []


def test_meal_no_longer_in_the_current_plan_writes_nothing(app, native, owner, no_provider):
    user, headers = owner
    old = current(native, headers)
    save_plan_row(user.id, document={"ogle": PLAN_DOC["kahvalti"]})
    fresh = current(native, headers)
    # Old token under the fresh revision: the meal belongs to another plan row.
    response = log(native, headers, old["meals"][0]["id"], fresh["revision"])
    assert response.status_code == 404
    assert error_of(response)["code"] == "PLANNED_MEAL_NOT_FOUND"
    assert ledger(user.id) == []


def test_identity_is_not_the_display_name(app, native, owner, no_provider):
    """Same name, slot and calories in a NEW plan row = a different planned meal."""
    user, headers = owner
    old = current(native, headers)
    save_plan_row(user.id)  # identical document, new row
    fresh = current(native, headers)
    assert old["meals"][0]["id"] != fresh["meals"][0]["id"]
    assert old["revision"] != fresh["revision"]
    for forged in ("kahvalti", "Oats - 60g", "Lean plan"):
        response = log(native, headers, forged, fresh["revision"])
        assert response.status_code == 404
    assert ledger(user.id) == []


def test_non_loggable_planned_meal_is_refused(app, make_user, bearer, native, no_provider):
    user = make_user("planned-partial")
    save_plan_row(user.id, document={"ogle": {"yemekler": ["Soup"], "kalori": 300}})
    headers = bearer(user)
    plan = current(native, headers)
    response = log(native, headers, plan["meals"][0]["id"], plan["revision"])
    assert response.status_code == 422
    assert error_of(response)["code"] == "PLANNED_MEAL_NOT_LOGGABLE"
    assert ledger(user.id) == []


@pytest.mark.parametrize("missing", ["revision", "key"])
def test_precondition_and_key_are_required(app, native, owner, no_provider, missing):
    user, headers = owner
    plan = current(native, headers)
    response = log(native, headers, plan["meals"][0]["id"],
                   None if missing == "revision" else plan["revision"],
                   key=None if missing == "key" else "planned-key-0001")
    assert response.status_code == (428 if missing == "revision" else 400)
    assert ledger(user.id) == []


@pytest.mark.parametrize("body", [{"slot": "aksam"}, {"kalori": 1}, [], "x"])
def test_body_is_closed(app, native, owner, no_provider, body):
    user, headers = owner
    plan = current(native, headers)
    response = log(native, headers, plan["meals"][0]["id"], plan["revision"], body=body)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_PLANNED_MEAL_COMMAND"
    assert ledger(user.id) == []


def test_empty_object_body_is_accepted(app, native, owner, no_provider):
    _user, headers = owner
    plan = current(native, headers)
    assert log(native, headers, plan["meals"][0]["id"], plan["revision"],
               body={}).status_code == 201


def test_owner_isolation_for_planned_meals(app, make_user, bearer, native, no_provider):
    alice = make_user("planned-alice")
    bob = make_user("planned-bob")
    save_plan_row(alice.id)
    save_plan_row(bob.id)
    a_plan = current(native, bearer(alice))
    b_plan = current(native, bearer(bob))
    # Alice's meal token under Bob's own revision: unknown meal, no write.
    cross = log(native, bearer(bob), a_plan["meals"][0]["id"], b_plan["revision"])
    assert cross.status_code == 404
    # Alice's revision is meaningless for Bob.
    cross_rev = log(native, bearer(bob), b_plan["meals"][0]["id"], a_plan["revision"])
    assert cross_rev.status_code == 412
    # The same key text is independent per owner.
    assert log(native, bearer(alice), a_plan["meals"][0]["id"], a_plan["revision"],
               key="same-text-key").status_code == 201
    assert log(native, bearer(bob), b_plan["meals"][0]["id"], b_plan["revision"],
               key="same-text-key").status_code == 201
    assert len(ledger(alice.id)) == 1 and len(ledger(bob.id)) == 1


def test_fingerprint_binds_revision_and_meal():
    a = native_plan.planned_meal_fingerprint("rev-1", "meal-1")
    assert a == native_plan.planned_meal_fingerprint("rev-1", "meal-1")
    assert a != native_plan.planned_meal_fingerprint("rev-2", "meal-1")
    assert a != native_plan.planned_meal_fingerprint("rev-1", "meal-2")
    assert len(a) == 64


def test_web_quick_add_is_unchanged(app, client, make_user, login):
    user = make_user("quickadd-web")
    login("quickadd-web")
    save_plan_row(user.id)
    response = client.post("/api/quick-add-meal", json={"meal_key": "kahvalti"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["nutrients"] == {"kalori": 450.0, "protein": 30.0, "karb": 50.0, "yag": 12.0}
    assert json.loads(json.dumps(body))  # plain JSON
