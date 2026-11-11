"""Idempotent replay never republishes due FatSecret per-item values.

AXISAI-FATSECRET-LAUNCH-FALLBACK-IMPLEMENTATION. ``meal_idempotency.find_existing``
is keyed by (owner, key), not by endpoint, so every web route that answers a
replay from the stored row can hand back a provider row. Each one must answer
through ``provider_retention.published`` like every other reader: once a row is
due (22h by provenance clock, even before the expiry pass has run, and always
for legacy ``search``/``barcode`` rows) its per-item macros are null, never the
stored number. Stored rows are never deleted or rewritten by a replay.

Synthetic inputs, stubbed provider, no network.
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import MealLog
from app.services import mobile_food_discovery, provider_retention
from app.timeutil import day_key

SENTINEL = 731.0
FOOD = {
    "provider": "fatsecret", "food_id": "food-r1", "name": "Synthetic Provider Food",
    "brand": "", "servings": [{
        "serving_id": "serving-r1", "description": "1 portion",
        "metric_mass": {"amount": 100.0, "unit": "g"},
        "nutrition": {"energy_kcal": SENTINEL, "protein_g": 25.0,
                      "carbohydrate_g": 60.0, "fat_g": 15.0},
        "nutrition_per_100g": {"energy_kcal": SENTINEL, "protein_g": 25.0,
                               "carbohydrate_g": 60.0, "fat_g": 15.0},
    }],
}
PROVIDER_BODY = {"ogun": "Öğle", "provider_food": {
    "provider": "fatsecret", "food_id": "food-r1", "serving_id": "serving-r1",
    "quantity": 1, "discovery_source": "search"}}
KEY = "fs-replay-retention-0001"
NULL_NUTRIENTS = {"kalori": None, "protein": None, "karb": None, "yag": None}


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def servings(food_id):
        calls.append(food_id)
        return dict(FOOD, food_id=food_id)

    monkeypatch.setattr(mobile_food_discovery, "servings", servings)
    return calls


def _age(row, hours):
    when = datetime.utcnow() - timedelta(hours=hours)
    row.created_at = when
    if row.provider_acquired_at is not None:
        row.provider_acquired_at = when
    db.session.commit()


def _logged_provider_row(client, user):
    first = client.post("/meal-log", json=PROVIDER_BODY, headers={"Idempotency-Key": KEY})
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["nutrients"]["kalori"] == SENTINEL  # fresh: shown
    return MealLog.query.filter_by(user_id=user.id).one()


def _legacy_row(user, source="search"):
    row = MealLog(user_id=user.id, ogun="Öğle", yemekler="Legacy provider food",
                  kalori=SENTINEL, protein=25, karb=60, yag=15, tarih=day_key(),
                  source=source, idempotency_key=KEY,
                  created_at=datetime.utcnow() - timedelta(days=3))
    db.session.add(row)
    db.session.commit()
    return row


REPLAYS = {
    "provider": lambda c: c.post("/meal-log", json=PROVIDER_BODY,
                                 headers={"Idempotency-Key": KEY}),
    "manual": lambda c: c.post("/meal-log", json={"ogun": "Öğle", "yemekler": "x"},
                               headers={"Idempotency-Key": KEY}),
    "quick_add": lambda c: c.post("/api/quick-add-meal", json={"meal_key": "ogle"},
                                  headers={"Idempotency-Key": KEY}),
}


@pytest.mark.parametrize("route", sorted(REPLAYS))
def test_replay_of_a_due_row_before_the_expiry_pass_publishes_no_values(
        app, client, auth_user, provider, route):
    row = _logged_provider_row(client, auth_user)
    _age(row, 23)  # due by provenance clock; the worker has not run yet
    assert row.kalori == SENTINEL  # storage still holds it (pass pending)

    response = REPLAYS[route](client)

    assert response.status_code == 200
    assert response.get_json()["nutrients"] == NULL_NUTRIENTS
    assert str(int(SENTINEL)) not in response.get_data(as_text=True)
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


@pytest.mark.parametrize("source", ["search", "barcode"])
@pytest.mark.parametrize("route", sorted(REPLAYS))
def test_replay_of_a_legacy_provider_row_publishes_no_values(
        app, client, auth_user, provider, route, source):
    row = _legacy_row(auth_user, source)

    response = REPLAYS[route](client)

    if route == "provider":
        # The canonical LogFood replay compares semantic fingerprints; a
        # legacy row has none, so the key is a defined conflict, no values.
        assert response.status_code == 409
        assert str(int(SENTINEL)) not in response.get_data(as_text=True)
    else:
        assert response.status_code == 200
        assert response.get_json()["nutrients"] == NULL_NUTRIENTS
    # Historical storage is preserved: a replay never purges or rewrites.
    db.session.refresh(row)
    assert row.kalori == SENTINEL
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


@pytest.mark.parametrize("route", sorted(REPLAYS))
def test_replay_of_a_fresh_row_is_unchanged(app, client, auth_user, provider, route):
    _logged_provider_row(client, auth_user)

    response = REPLAYS[route](client)

    assert response.status_code == 200
    assert response.get_json()["nutrients"]["kalori"] == SENTINEL


@pytest.mark.parametrize("source", ["manual", "ai_plan", None])
def test_replay_of_an_independent_row_keeps_its_values(app, client, auth_user, source):
    row = MealLog(user_id=auth_user.id, ogun="Öğle", yemekler="Home meal",
                  kalori=420, protein=25, karb=40, yag=14, tarih=day_key(),
                  source=source or "manual", idempotency_key=KEY,
                  created_at=datetime.utcnow() - timedelta(days=3))
    db.session.add(row)
    db.session.commit()
    if source is None:
        row.source = None
        db.session.commit()

    response = REPLAYS["manual"](client)

    assert response.status_code == 200
    assert response.get_json()["nutrients"] == {
        "kalori": 420, "protein": 25, "karb": 40, "yag": 14}


def test_replay_after_the_expiry_pass_stays_null_and_day_total_is_kept(
        app, client, auth_user, provider):
    row = _logged_provider_row(client, auth_user)
    _age(row, 23)
    provider_retention.expire_due()

    replay = REPLAYS["provider"](client)

    assert replay.get_json()["nutrients"] == NULL_NUTRIENTS
    today = client.get("/meal-log/today").get_json()
    assert today["totals"]["kalori"] == pytest.approx(SENTINEL)
