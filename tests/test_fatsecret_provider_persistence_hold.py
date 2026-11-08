"""D-B2 launch hold: no NEW provider-backed diary / saved-meal persistence.

Founder direction 2026-11-08: hold until written FatSecret retention permission
is verified. Fail-closed code constant (no environment channel). Manual logging,
diary history, idempotent replay of existing rows and deletion stay usable.
Synthetic inputs only; no provider request is made.
"""
import pytest

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog
from app.services import fatsecret_boundary, provider_food_snapshot
from app.timeutil import app_today, day_key

from test_mobile_log_food_api import (  # noqa: F401
    as_mobile, manual_command, mobile_user, provider, provider_command)
from test_sprint13_nutrition_write_convergence import (
    BAR_SERVING, FOOD_ID, _web_provider_log)

KEY = "018f47d2-a2c7-7f52-a5b0-123456789abd"


@pytest.fixture
def no_provider(monkeypatch):
    """Any provider request under the hold is a failure."""
    from app.services import fatsecret, mobile_food_discovery

    def boom(*a, **k):
        raise AssertionError("no provider request may be made while persistence is held")

    monkeypatch.setattr(mobile_food_discovery, "servings", boom)
    monkeypatch.setattr(fatsecret, "_food_get_raw", boom)


# ── The boundary itself ────────────────────────────────────────────────────

def test_hold_is_the_default_and_fail_closed(monkeypatch):
    assert fatsecret_boundary.PROVIDER_PERSISTENCE_PERMISSION_REF is None
    assert fatsecret_boundary.provider_persistence_allowed() is False
    for value in ("", "   ", 0, 1, True, ["ref"], {"ref": 1}):
        monkeypatch.setattr(fatsecret_boundary, "PROVIDER_PERSISTENCE_PERMISSION_REF", value)
        assert fatsecret_boundary.provider_persistence_allowed() is False, value
    monkeypatch.setattr(fatsecret_boundary, "PROVIDER_PERSISTENCE_PERMISSION_REF", "WRITTEN-REF")
    assert fatsecret_boundary.provider_persistence_allowed() is True


def test_hold_has_no_environment_or_config_channel(app, monkeypatch):
    monkeypatch.setenv("FATSECRET_PROVIDER_PERSISTENCE_PERMISSION_REF", "env-ref")
    app.config["PROVIDER_PERSISTENCE_PERMISSION_REF"] = "config-ref"
    assert fatsecret_boundary.provider_persistence_allowed() is False


def test_shared_resolver_refuses_before_any_provider_request(no_provider):
    from decimal import Decimal
    with pytest.raises(provider_food_snapshot.ProviderPersistenceHeld):
        provider_food_snapshot.resolve_provider_food("fatsecret", "1", "2", Decimal(1))


# ── Native LogFood ─────────────────────────────────────────────────────────

def test_native_provider_backed_log_is_held(client, mobile_user, as_mobile, no_provider):
    response = client.post("/api/v1/nutrition/logs", json=provider_command(),
                           headers=as_mobile(mobile_user))
    assert response.status_code == 503
    body = response.get_json()
    assert body["error"]["code"] == "PROVIDER_LOGGING_UNAVAILABLE"
    assert body["error"]["retryable"] is False
    assert MealLog.query.filter_by(user_id=mobile_user.id).count() == 0


def test_native_manual_log_still_works_under_the_hold(client, mobile_user, as_mobile, no_provider):
    response = client.post("/api/v1/nutrition/logs", json=manual_command(),
                           headers=as_mobile(mobile_user, key="log-food-manual-0001"))
    assert response.status_code == 201
    row = MealLog.query.filter_by(user_id=mobile_user.id).one()
    assert row.source == "manual" and row.kalori == 420


def test_native_replay_of_an_existing_provider_row_is_preserved(
        client, mobile_user, as_mobile, provider, monkeypatch):
    # A row written before the hold (permission modeled, test-only) ...
    monkeypatch.setattr(fatsecret_boundary, "PROVIDER_PERSISTENCE_PERMISSION_REF", "TEST-ONLY")
    headers = as_mobile(mobile_user)
    first = client.post("/api/v1/nutrition/logs", json=provider_command(), headers=headers)
    assert first.status_code == 201
    # ... keeps answering its own idempotent replay after the hold: history is
    # preserved (the retained snapshot itself is the open D-B4 retention item).
    monkeypatch.setattr(fatsecret_boundary, "PROVIDER_PERSISTENCE_PERMISSION_REF", None)
    replay = client.post("/api/v1/nutrition/logs", json=provider_command(), headers=headers)
    assert replay.status_code == 200
    assert replay.get_json()["meal"] == first.get_json()["meal"]
    assert MealLog.query.filter_by(user_id=mobile_user.id).count() == 1


# ── Web meal-log, diary / saved meals, barcode, social ─────────────────────

def test_web_provider_meal_log_is_held(client, auth_user, no_provider):
    response = _web_provider_log(client)
    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "provider_logging_unavailable" and body["retryable"] is False
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def _meal(user_id):
    meal = CustomMeal(user_id=user_id, meal_name="Öğle", date_key=app_today().isoformat())
    db.session.add(meal)
    db.session.commit()
    return meal


def test_saved_meal_provider_item_add_is_held(client, auth_user, no_provider):
    meal = _meal(auth_user.id)
    response = client.post(f"/api/diary/meal/{meal.id}/item", json={
        "food_name": "Provider bar", "fatsecret_food_id": FOOD_ID, "serving_id": BAR_SERVING, "serving_quantity": 1})
    assert response.status_code == 503
    assert response.get_json() == {"error": "provider_logging_unavailable", "retryable": False}
    assert CustomMealItem.query.filter_by(custom_meal_id=meal.id).count() == 0


def test_logging_a_saved_meal_with_a_provider_item_is_held(client, auth_user, no_provider):
    meal = _meal(auth_user.id)
    db.session.add(CustomMealItem(
        custom_meal_id=meal.id, food_name="Provider bar", grams=50.0, calories=120.0,
        protein=4.0, carbs=20.0, fat=3.0, fatsecret_food_id=FOOD_ID, serving_id=BAR_SERVING,
        serving_quantity=1.0))
    db.session.commit()
    response = client.post(f"/api/diary/meal/{meal.id}/log")
    assert response.status_code == 503
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    db.session.expire_all()
    stored = CustomMealItem.query.filter_by(custom_meal_id=meal.id).one()
    assert (stored.food_name, stored.calories) == ("Provider bar", 120.0)  # saved meal intact
    assert not db.session.get(CustomMeal, meal.id).is_logged


def test_manual_saved_meal_still_logs_and_deletes_under_the_hold(client, auth_user, no_provider):
    meal = _meal(auth_user.id)
    for name in ("Tavuk", "Yanlış eklenen"):
        db.session.add(CustomMealItem(
            custom_meal_id=meal.id, food_name=name, grams=150.0, calories=248.0,
            protein=46.5, carbs=0.0, fat=5.4, per_100g_calories=165.0, per_100g_protein=31.0,
            per_100g_carbs=0.0, per_100g_fat=3.6))
    db.session.commit()
    wrong = CustomMealItem.query.filter_by(custom_meal_id=meal.id, food_name="Yanlış eklenen").one()
    assert client.delete(f"/api/diary/item/{wrong.id}").status_code in (200, 204)
    assert client.post(f"/api/diary/meal/{meal.id}/log").status_code == 200
    assert MealLog.query.filter_by(user_id=auth_user.id, source="diary").count() == 1


def test_web_barcode_add_is_held_but_existing_replay_answers(client, auth_user, monkeypatch):
    from app.services import barcode as barcode_svc

    def boom(*a, **k):
        raise AssertionError("no barcode lookup while persistence is held")

    monkeypatch.setattr(barcode_svc, "get_barcode_product", boom)
    body = {"barcode": "5000159407236", "meal": "Ara Öğün", "serving_id": "s", "serving_quantity": 1}
    held = client.post("/api/food/barcode/add", json=body)
    assert held.status_code == 503
    assert held.get_json()["error"] == "provider_logging_unavailable"
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0

    db.session.add(MealLog(user_id=auth_user.id, ogun="Ara Öğün", yemekler="Earlier bar",
                           kalori=120, protein=4, karb=20, yag=3, tarih=day_key(),
                           source="barcode", idempotency_key=KEY))
    db.session.commit()
    replay = client.post("/api/food/barcode/add", json=body, headers={"Idempotency-Key": KEY})
    assert replay.status_code == 200 and replay.get_json()["message"] == "logged"
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


def test_social_meal_suggestion_never_fetches_provider_totals(app, monkeypatch):
    from types import SimpleNamespace
    from app.blueprints import social

    def boom(*a, **k):
        raise AssertionError("no FatSecret lookup for a persisted suggestion")

    monkeypatch.setattr(social, "_parse_structured_suggestion_items", lambda body: ["Tavuk"])
    monkeypatch.setattr(social, "_get_cached_macros", lambda items, basis=None: ({}, list(items)))
    monkeypatch.setattr(social, "_get_fatsecret_token", boom)
    monkeypatch.setattr(social, "_lookup_macros_fatsecret", boom)
    with app.test_request_context():
        assert social._calculate_meal_suggestion(SimpleNamespace(body="x", message_id=1)) is None


# ── R-5 web protein nudge ──────────────────────────────────────────────────

def test_protein_nudge_is_withheld_on_any_unproven_week(make_user):
    from datetime import date
    from app.models import UserSession
    from test_analytics_engine import _nudge_types
    user = make_user("r5-protein", last_login=date.today())
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    db.session.add(MealLog(user_id=user.id, ogun="öğün", yemekler="manual-looking row",
                           kalori=4000, protein=2000, source="manual", tarih=day_key()))
    db.session.commit()
    nudges = _nudge_types(user)
    assert "NUDGE_PROTEIN_GOAL" not in nudges
    assert "NUDGE_NO_NUTRITION" not in nudges  # log-existence nudges are unaffected
