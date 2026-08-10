"""Canonical provider-backed/manual mobile LogFood write contract."""
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog
from app.services import mobile_auth, mobile_food_discovery


LOG_PATH = "/api/v1/nutrition/logs"


@pytest.fixture
def mobile_user(make_user):
    return make_user("mobile-log-food")


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user, key="log-food-key-0001"):
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        return {
            "Authorization": "Bearer opaque-access-credential",
            "Idempotency-Key": key,
        }
    return _headers


@pytest.fixture
def provider(monkeypatch):
    food = {
        "provider": "fatsecret",
        "food_id": "food-1",
        "name": "Provider Food",
        "brand": "Provider Brand",
        "servings": [{
            "serving_id": "serving-1",
            "description": "1 serving",
            "metric_mass": {"amount": 50.0, "unit": "g"},
            "nutrition": {
                "energy_kcal": 120.0,
                "protein_g": 4.0,
                "carbohydrate_g": 20.0,
                "fat_g": 3.0,
            },
            "nutrition_per_100g": {
                "energy_kcal": 240.0,
                "protein_g": 8.0,
                "carbohydrate_g": 40.0,
                "fat_g": 6.0,
            },
        }, {
            "serving_id": "serving-2",
            "description": "2 servings",
            "metric_mass": {"amount": 100.0, "unit": "g"},
            "nutrition": {
                "energy_kcal": 240.0,
                "protein_g": 8.0,
                "carbohydrate_g": 40.0,
                "fat_g": 6.0,
            },
            "nutrition_per_100g": {
                "energy_kcal": 240.0,
                "protein_g": 8.0,
                "carbohydrate_g": 40.0,
                "fat_g": 6.0,
            },
        }],
    }
    monkeypatch.setattr(
        mobile_food_discovery, "servings",
        lambda food_id: dict(food, food_id=food_id))
    return food


def provider_command(**changes):
    payload = {
        "kind": "provider_backed",
        "provider": "fatsecret",
        "food_id": "food-1",
        "serving_id": "serving-1",
        "quantity": 1,
        "slot": "kahvalti",
        "discovery_source": "search",
    }
    payload.update(changes)
    return payload


def manual_command(**changes):
    payload = {
        "kind": "manual",
        "description": "Ev yapımı yemek",
        "slot": "ogle",
        "nutrition": {
            "energy_kcal": 420,
            "protein_g": 25,
            "carbohydrate_g": 40,
            "fat_g": 14,
        },
    }
    payload.update(changes)
    return payload


def post(client, headers, payload):
    return client.post(LOG_PATH, headers=headers, json=payload)


@pytest.mark.parametrize("payload", [provider_command(), manual_command()])
def test_each_command_writes_a_server_fingerprint_and_opaque_identity(
        raw_client, as_mobile, mobile_user, provider, payload):
    response = post(raw_client, as_mobile(mobile_user), payload)

    assert response.status_code == 201
    row = MealLog.query.one()
    assert len(row.idempotency_fingerprint) == 64
    assert all(char in "0123456789abcdef" for char in row.idempotency_fingerprint)
    assert response.json["meal"]["id"] != str(row.id)
    assert response.json["meal"]["day"] == row.tarih


def test_provider_nutrition_is_resolved_and_scaled_server_side(
        raw_client, as_mobile, mobile_user, provider):
    response = post(
        raw_client, as_mobile(mobile_user), provider_command(quantity=2))

    assert response.status_code == 201
    assert response.json["meal"]["nutrition"] == {
        "energy_kcal": 240.0,
        "protein_g": 8.0,
        "carbohydrate_g": 40.0,
        "fat_g": 6.0,
    }


def test_provider_resolution_runs_outside_the_preflight_db_transaction(
        raw_client, as_mobile, mobile_user, provider, monkeypatch):
    transaction_states = []

    def resolve(food_id):
        transaction_states.append(db.session().in_transaction())
        return dict(provider, food_id=food_id)

    monkeypatch.setattr(mobile_food_discovery, "servings", resolve)

    response = post(
        raw_client, as_mobile(mobile_user), provider_command())

    assert response.status_code == 201
    assert transaction_states == [False]


@pytest.mark.parametrize("payload", [
    provider_command(nutrition={"energy_kcal": 1}),
    manual_command(provider="fatsecret"),
    manual_command(food_id="food-1"),
    manual_command(serving_id="serving-1"),
    manual_command(quantity=1),
    provider_command(idempotency_fingerprint="0" * 64),
])
def test_mixed_or_client_fingerprint_payload_is_deterministic_400(
        raw_client, as_mobile, mobile_user, provider, payload):
    response = post(raw_client, as_mobile(mobile_user), payload)

    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_LOG_FOOD_COMMAND"
    assert MealLog.query.count() == 0


@pytest.mark.parametrize("payload", [
    provider_command(quantity=float("nan")),
    provider_command(quantity=float("inf")),
    provider_command(quantity=-1),
    manual_command(nutrition={
        "energy_kcal": -1, "protein_g": 1,
        "carbohydrate_g": 1, "fat_g": 1,
    }),
])
def test_invalid_numbers_never_persist_a_fingerprint(
        raw_client, as_mobile, mobile_user, provider, payload):
    response = post(raw_client, as_mobile(mobile_user), payload)

    assert response.status_code == 400
    assert MealLog.query.count() == 0


@pytest.mark.parametrize("payload", [provider_command(), manual_command()])
def test_same_semantic_command_replays_same_id_without_second_row(
        raw_client, as_mobile, mobile_user, provider, payload):
    headers = as_mobile(mobile_user)
    first = post(raw_client, headers, payload)
    second = post(raw_client, headers, payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json["meal"]["id"] == first.json["meal"]["id"]
    assert MealLog.query.count() == 1


def test_quantity_one_and_one_point_zero_are_the_same_command(
        raw_client, as_mobile, mobile_user, provider):
    headers = as_mobile(mobile_user)
    first = post(raw_client, headers, provider_command(quantity=1))
    replay = post(raw_client, headers, provider_command(quantity=1.0))

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json["meal"]["id"] == first.json["meal"]["id"]


@pytest.mark.parametrize("equivalent", [
    {key: value for key, value in reversed(list(provider_command().items()))},
    {key: value for key, value in provider_command().items()
     if key not in {"quantity", "discovery_source"}},
    provider_command(
        kind="PROVIDER_BACKED", provider="FATSECRET",
        slot="KAHVALTI", discovery_source="SEARCH"),
])
def test_transport_only_representation_does_not_change_provider_identity(
        raw_client, as_mobile, mobile_user, provider, equivalent):
    headers = as_mobile(mobile_user)
    first = post(raw_client, headers, provider_command())
    replay = post(raw_client, headers, equivalent)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json["meal"]["id"] == first.json["meal"]["id"]


def test_manual_outer_whitespace_is_the_same_normalized_description(
        raw_client, as_mobile, mobile_user, provider):
    headers = as_mobile(mobile_user)
    first = post(raw_client, headers, manual_command())
    replay = post(
        raw_client, headers,
        manual_command(description="  Ev yapımı yemek  "))

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json["meal"]["id"] == first.json["meal"]["id"]


@pytest.mark.parametrize("second", [
    provider_command(food_id="food-2"),
    provider_command(serving_id="serving-2"),
    provider_command(quantity=2),
    provider_command(slot="ogle"),
    manual_command(),
])
def test_same_key_with_materially_different_command_conflicts(
        raw_client, as_mobile, mobile_user, provider, second):
    headers = as_mobile(mobile_user)
    assert post(raw_client, headers, provider_command()).status_code == 201

    conflict = post(raw_client, headers, second)

    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert MealLog.query.count() == 1


def test_legacy_null_fingerprint_is_never_silently_replayed(
        raw_client, as_mobile, mobile_user, provider):
    db.session.add(MealLog(
        user_id=mobile_user.id, ogun="Kahvaltı", yemekler="Legacy",
        kalori=1, protein=1, karb=1, yag=1, tarih="2026-08-10",
        source="manual", idempotency_key="log-food-key-0001"))
    db.session.commit()

    conflict = post(
        raw_client, as_mobile(mobile_user), provider_command())

    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert MealLog.query.count() == 1


def test_same_key_is_independent_between_users(
        raw_client, as_mobile, mobile_user, make_user, provider):
    other = make_user("mobile-log-food-other")

    first = post(raw_client, as_mobile(mobile_user), provider_command())
    second = post(raw_client, as_mobile(other), provider_command())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json["meal"]["id"] != second.json["meal"]["id"]
    assert MealLog.query.count() == 2
