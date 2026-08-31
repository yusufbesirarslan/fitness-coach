"""Production barcode workflow: cache, analysis, recommendations, diary add."""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import BarcodeFoodCache, MealLog, UserSession, WorkoutLog, WORKOUT_COMPLETION_MARKER
from app.services import barcode as barcode_svc


def _protein_bar_payload():
    return {
        "food_id": "77777",
        "name": "Acme Protein Bar",
        "brand": "Acme",
        "servings": [{
            "serving_id": "bar-1",
            "serving_description": "1 bar",
            "metric_serving_amount": 60,
            "metric_serving_unit": "g",
            "calories": 210,
            "protein": 21,
            "carbs": 23,
            "fat": 6,
            "fiber": 7,
            "sugar": 14,
            "sodium": 420,
        }],
    }


def test_analysis_engine_scores_and_labels_packaged_food():
    food = barcode_svc.normalize_food_model("5000159407236", _protein_bar_payload())
    analysis = barcode_svc.analyze_food(food)

    assert analysis["protein_density"] == pytest.approx(0.1)
    assert analysis["macro_distribution"]["protein_pct"] == 40
    assert analysis["fiber_rating"] == "high"
    assert analysis["sugar_warning"] is True
    assert analysis["sodium_warning"] is True
    assert "High Protein" in analysis["badges"]
    assert "High Sugar" in analysis["badges"]
    assert 0 <= analysis["axisai_food_score"] <= 100


def test_recommendation_engine_uses_goal_remaining_targets_and_workout():
    food = barcode_svc.normalize_food_model("5000159407236", _protein_bar_payload())
    context = {
        "goal": "cut",
        "remaining": {"calories": 160, "protein": 42, "carbs": 40, "fat": 12},
        "workout_completed_today": True,
        "meal_time": "post_workout",
        "daily_progress": {},
    }

    out = barcode_svc.recommend_for_food(food, context)

    assert out["portion"]["servings"] == pytest.approx(0.8)
    assert out["portion"]["basis"] == "calories"
    assert "Consider eating a smaller serving." in out["messages"]
    assert "This food is an excellent choice after today's workout." in out["messages"]


def test_barcode_lookup_uses_database_cache_before_fatsecret(app, client, auth_user, monkeypatch):
    cached_food = barcode_svc.normalize_food_model("5000159407236", _protein_bar_payload())
    db.session.add(BarcodeFoodCache(
        barcode="5000159407236",
        food_id="77777",
        food_name=cached_food["name"],
        brand=cached_food["brand"],
        payload=cached_food,
    ))
    db.session.commit()

    monkeypatch.setattr(
        barcode_svc,
        "_food_find_by_barcode",
        lambda code: (_ for _ in ()).throw(AssertionError("FatSecret should not be called")),
    )

    resp = client.get("/api/food/barcode?code=5000159407236")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["source"] == "cache"
    assert data["food"]["name"] == "Acme Protein Bar"
    assert data["analysis"]["axisai_food_score"] >= 1
    assert data["food_id"] == "77777"  # backwards-compatible legacy key


def test_barcode_lookup_fatsecret_result_is_normalized_and_cached(app, client, auth_user, monkeypatch):
    calls = {"count": 0}

    def fake_lookup(code):
        calls["count"] += 1
        return _protein_bar_payload()

    monkeypatch.setattr(barcode_svc, "_food_find_by_barcode", fake_lookup)

    resp = client.get("/api/food/barcode?code=5000159407236")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["source"] == "fatsecret"
    assert data["food"]["servings"][0]["macros"]["calories"] == 210
    assert data["analysis"]["badges"]
    assert BarcodeFoodCache.query.filter_by(barcode="5000159407236").one().food_id == "77777"
    assert calls["count"] == 1


def test_barcode_not_found_offers_manual_and_future_ocr_actions(client, auth_user, monkeypatch):
    monkeypatch.setattr(barcode_svc, "get_barcode_product", lambda code: None)

    resp = client.get("/api/food/barcode?code=5000159407236")

    assert resp.status_code == 404
    assert resp.get_json()["actions"] == ["manual_search", "scan_nutrition_label"]


def _seed_barcode_cache(barcode="5000159407236"):
    """Server-side provider truth for the deprecated add surface.

    Sprint 13 PR3 / F6: the route no longer reads the caller's `food` object,
    so the product must be resolvable from the server's own cache/provider.
    """
    food = barcode_svc.normalize_food_model(barcode, _protein_bar_payload())
    db.session.add(BarcodeFoodCache(
        barcode=barcode,
        food_id=food["food_id"],
        food_name=food["name"],
        brand=food["brand"],
        payload=food,
    ))
    db.session.commit()
    return food


def test_barcode_add_to_diary_writes_canonical_meal_log(app, client, auth_user):
    # Pre-PR3 this test supplied the whole `food` object in the request body and
    # the route persisted the caller's numbers - the F6 defect (N2: no supported
    # writer may persist caller-supplied provider nutrition). The corrected
    # invariant seeds server-side truth and sends deliberately tampered macros:
    # the ledger must reflect the server's product, not the request's.
    _seed_barcode_cache()
    tampered = barcode_svc.normalize_food_model(
        "5000159407236", _protein_bar_payload())
    tampered["servings"][0]["macros"]["calories"] = 1.0
    db.session.add(UserSession(
        user_id=auth_user.id,
        name="Test",
        goal="kas kazanma",
        target_calories=2400,
    ))
    db.session.add(WorkoutLog(
        user_id=auth_user.id,
        exercise_name=WORKOUT_COMPLETION_MARKER,
        sets=0,
        reps=0,
        weight_kg=0,
        volume=0,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    resp = client.post("/api/food/barcode/add", json={
        "barcode": "5000159407236",
        "meal": "Ara Öğün",
        "food": tampered,
        "serving_id": "bar-1",
        "serving_quantity": 2,
    })

    assert resp.status_code == 200
    assert resp.headers["Deprecation"] == "true"
    data = resp.get_json()
    assert "meal_log_id" not in data, (
        "The deprecated surface must not expose a raw ledger row id.")
    assert data["nutrients"] == {"kalori": 420.0, "protein": 42.0, "karb": 46.0, "yag": 12.0}
    assert data["goal_impact"]["after"]["protein"] == 42.0
    row = MealLog.query.filter_by(user_id=auth_user.id, source="barcode").one()
    assert row.yemekler == "Acme Protein Bar (2x 1 bar)"
    assert row.kalori == 420.0


def test_barcode_add_idempotency_writes_one_row(client, auth_user):
    # Pre-PR3 the replay was proved by comparing the `meal_log_id` both
    # responses leaked. PR3 stops exposing raw ledger ids on every variant
    # of this surface, so replay is proved by the ledger itself: one row,
    # and the replayed response repeats the same nutrients.
    _seed_barcode_cache()
    headers = {"Idempotency-Key": "018f47d2-a2c7-7f52-a5b0-123456789abc"}
    payload = {
        "barcode": "5000159407236",
        "meal": "\u0041ra \u00d6\u011f\u00fcn",
        "serving_id": "bar-1",
        "serving_quantity": 1,
    }

    first = client.post("/api/food/barcode/add", json=payload, headers=headers)
    second = client.post("/api/food/barcode/add", json=payload, headers=headers)

    assert first.status_code == second.status_code == 200
    assert "meal_log_id" not in first.get_json()
    assert "meal_log_id" not in second.get_json()
    assert second.get_json()["nutrients"] == first.get_json()["nutrients"]
    assert MealLog.query.filter_by(user_id=auth_user.id, source="barcode").count() == 1
