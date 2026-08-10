"""Characterisation tests for the legacy food-discovery and ledger-write paths.

Written BEFORE the Sprint 9 PR2A extraction, and kept afterwards, so that the
minimal service extraction the mobile adapters need is provably behaviour
preserving. Nothing here asserts what the code *should* do — every assertion
records what it *did* on `origin/main` at 78e9d3c.

Two of these tests deliberately pin behaviour PR2A considers wrong for a mobile
contract (an unknown serving mass and an underivable per-100 g reference both
becoming a measured `0`). They stay green because the legacy web surfaces are
not changed: the correction lives at the new `/api/v1` boundary and nowhere
else. If someone later "fixes" the legacy builder, these tests are the alarm
that a web payload changed shape.

    python -m pytest tests/test_food_discovery_characterization.py -v
"""
import pytest

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog
from app.services import fatsecret, meal_idempotency
from app.services import nutrition_pipeline


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# _food_get_servings — the provider call PR2A extracts a raw-fetch helper from.
# ---------------------------------------------------------------------------

def _servings_payload(servings, name="Chicken Breast", brand="Acme"):
    return {"food": {
        "food_name": name,
        "brand_name": brand,
        "servings": {"serving": servings},
    }}


def test_food_get_servings_maps_provider_keys_and_normalises(app, monkeypatch):
    """Provider `carbohydrate` becomes `carbs`, and a synthetic 100 g row is added."""
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    payload = _servings_payload([{
        "serving_id": "5501",
        "serving_description": "1 cup, diced",
        "metric_serving_amount": "140.0",
        "metric_serving_unit": "g",
        "calories": "231",
        "protein": "43.4",
        "carbohydrate": "0",
        "fat": "5.0",
    }])
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Resp(payload))

    with app.app_context():
        servings = fatsecret._food_get_servings("33691")

    by_id = {s["serving_id"]: s for s in servings}
    real = by_id["5501"]
    assert real["serving_description"] == "1 cup, diced"
    assert real["metric_serving_amount"] == 140.0
    assert real["carbs"] == 0.0            # provider spells it "carbohydrate"
    assert real["protein"] == 43.4
    assert real["is_bulk"] is False
    # _normalize_servings derives a 100 g base when the provider has none.
    assert "100g_calc" in by_id
    assert by_id["100g_calc"]["metric_serving_amount"] == 100.0


def test_food_get_servings_falls_through_methods_until_servings_parse(
        app, monkeypatch):
    """A `food.get.v4` response WITHOUT servings advances to the next method.

    This is the loop condition the raw-fetch extraction has to keep: it is not
    "first response without an error", it is "first response a serving list can
    be read out of".
    """
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    seen = []

    def fake_get(url, **kwargs):
        method = kwargs["params"]["method"]
        seen.append(method)
        if method == "food.get.v4":
            return _Resp({"food": {"food_name": "No servings here"}})
        return _Resp(_servings_payload([{
            "serving_id": "1",
            "serving_description": "100 g",
            "metric_serving_amount": "100",
            "metric_serving_unit": "g",
            "calories": "165", "protein": "31", "carbohydrate": "0", "fat": "3.6",
        }]))

    monkeypatch.setattr(fatsecret, "_fs_get", fake_get)
    with app.app_context():
        servings = fatsecret._food_get_servings("33691")

    assert seen == ["food.get.v4", "food.get.v2"]
    assert [s["serving_id"] for s in servings] == ["1"]


def test_food_get_servings_returns_none_when_no_method_yields_servings(
        app, monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(
        fatsecret, "_fs_get",
        lambda url, **kw: _Resp({"error": {"code": 13, "message": "nope"}}))
    with app.app_context():
        assert fatsecret._food_get_servings("33691") is None


def test_food_get_servings_returns_none_on_token_failure(app, monkeypatch):
    def boom():
        raise RuntimeError("no creds")
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", boom)
    with app.app_context():
        assert fatsecret._food_get_servings("33691") is None


def test_food_get_servings_substitutes_a_matrix_estimate_for_unknown_mass(
        app, monkeypatch):
    """LEGACY, and deliberately unchanged: a missing metric amount is GUESSED.

    `parse_fatsecret_serving` falls back to the deterministic portion-weight
    matrix, and when even that has no entry the amount lands as `0.0` — an
    unknown mass presented as a measurement. The mobile contract refuses both
    (see docs/MOBILE_NUTRITION.md, "Gap 5"); the web payload keeps them.
    """
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    payload = _servings_payload([
        {"serving_id": "a", "serving_description": "1 bardak",
         "metric_serving_amount": "", "metric_serving_unit": "",
         "calories": "80", "protein": "4", "carbohydrate": "6", "fat": "4"},
        {"serving_id": "b", "serving_description": "1 mystery portion",
         "metric_serving_amount": None, "metric_serving_unit": None,
         "calories": "0", "protein": "0", "carbohydrate": "0", "fat": "0"},
    ])
    monkeypatch.setattr(fatsecret, "_fs_get", lambda url, **kw: _Resp(payload))

    with app.app_context():
        servings = fatsecret._food_get_servings("1")

    by_id = {s["serving_id"]: s for s in servings}
    assert by_id["a"]["metric_serving_amount"] > 0     # matrix guess for "bardak"
    assert by_id["b"]["metric_serving_amount"] == 0.0  # no entry -> measured zero


def test_parse_fatsecret_serving_turns_absent_nutrients_into_zero():
    """LEGACY: a nutrient the provider never sent arrives as `0.0`."""
    parsed = nutrition_pipeline.parse_fatsecret_serving({
        "serving_id": "9", "serving_description": "1 portion",
        "metric_serving_amount": "0", "metric_serving_unit": "g",
        "calories": "120",
    })
    assert parsed["protein"] == 0.0
    assert parsed["carbs"] == 0.0
    assert parsed["fat"] == 0.0
    assert parsed["metric_serving_amount"] == 0.0


# ---------------------------------------------------------------------------
# _food_find_by_barcode — GTIN handling PR2A reuses for the mobile lookup.
# ---------------------------------------------------------------------------

def test_food_find_by_barcode_left_pads_to_gtin13_and_returns_food(
        app, monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    barcodes = []

    def fake_get(url, **kwargs):
        params = kwargs["params"]
        if params["method"] == "food.find_id_for_barcode":
            barcodes.append(params["barcode"])
            return _Resp({"food_id": {"value": "4242"}})
        return _Resp(_servings_payload([{
            "serving_id": "1", "serving_description": "1 bar",
            "metric_serving_amount": "60", "metric_serving_unit": "g",
            "calories": "250", "protein": "20", "carbohydrate": "25", "fat": "8",
        }], name="Protein Bar", brand="Acme"))

    monkeypatch.setattr(fatsecret, "_fs_get", fake_get)
    with app.app_context():
        found = fatsecret._food_find_by_barcode("5449000000996")

    assert barcodes[0] == "5449000000996"
    assert found["food_id"] == "4242"
    assert found["name"] == "Protein Bar"
    assert found["brand"] == "Acme"
    assert found["servings"]


def test_food_find_by_barcode_pads_a_short_upc(app, monkeypatch):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    barcodes = []

    def fake_get(url, **kwargs):
        params = kwargs["params"]
        if params["method"] == "food.find_id_for_barcode":
            barcodes.append(params["barcode"])
            return _Resp({"food_id": {"value": "0"}})
        return _Resp({"error": {"code": 1}})

    monkeypatch.setattr(fatsecret, "_fs_get", fake_get)
    with app.app_context():
        assert fatsecret._food_find_by_barcode("012345678905") is None

    assert barcodes == ["0012345678905"]  # left-padded to 13 digits


def test_food_find_by_barcode_rejects_non_numeric_before_any_call(
        app, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("no provider call may happen for a non-numeric code")

    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", explode)
    with app.app_context():
        assert fatsecret._food_find_by_barcode("abc") is None


# ---------------------------------------------------------------------------
# meal_idempotency — the one replay-protection service PR2A must reuse.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "abcdefgh",                       # 8 chars, the documented minimum
    "a" * 64,                         # 64 chars, the documented maximum
    "Ab.9_no-wait:this-is-fine",      # no underscore in the class? it is allowed
    "0123456789.:-_",
])
def test_idempotency_key_regex_accepts_documented_shapes(app, key):
    with app.test_request_context(headers={"Idempotency-Key": key}):
        assert meal_idempotency.read_idempotency_key() == key


@pytest.mark.parametrize("key", [
    "",
    "short7",                         # 7 chars, below the minimum
    "a" * 65,                         # 65 chars, above the maximum
    "has space here",
    "has/slash/chars",
    "unicode-çğş-key",
])
def test_idempotency_key_regex_rejects_everything_else(app, key):
    with app.test_request_context(headers={"Idempotency-Key": key}):
        assert meal_idempotency.read_idempotency_key() is None


def test_idempotency_missing_header_reads_as_absent(app):
    with app.test_request_context():
        assert meal_idempotency.read_idempotency_key() is None


def test_find_existing_is_scoped_to_the_authenticated_user(app, make_user):
    owner = make_user("idem-owner")
    other = make_user("idem-other")
    entry = MealLog(user_id=owner.id, ogun="Kahvaltı", yemekler="Yulaf",
                    kalori=100.0, protein=1.0, karb=1.0, yag=1.0,
                    tarih="2026-08-09", idempotency_key="shared-key-1")
    db.session.add(entry)
    db.session.commit()

    assert meal_idempotency.find_existing(owner.id, "shared-key-1") is not None
    assert meal_idempotency.find_existing(other.id, "shared-key-1") is None
    assert meal_idempotency.find_existing(owner.id, None) is None


def test_commit_once_resolves_a_duplicate_key_to_the_winner_row(app, make_user):
    user = make_user("idem-race")
    first = MealLog(user_id=user.id, ogun="Öğle", yemekler="Pilav",
                    kalori=300.0, protein=6.0, karb=60.0, yag=3.0,
                    tarih="2026-08-09")
    winner, created = meal_idempotency.commit_once(first, "race-key-0001")
    assert created is True

    second = MealLog(user_id=user.id, ogun="Öğle", yemekler="Pilav",
                     kalori=300.0, protein=6.0, karb=60.0, yag=3.0,
                     tarih="2026-08-09")
    resolved, created_again = meal_idempotency.commit_once(second, "race-key-0001")
    assert created_again is False
    assert resolved.id == winner.id
    assert MealLog.query.filter_by(user_id=user.id).count() == 1


def test_commit_once_without_a_key_never_deduplicates(app, make_user):
    user = make_user("idem-keyless")
    for _ in range(2):
        entry = MealLog(user_id=user.id, ogun="Akşam", yemekler="Çorba",
                        kalori=90.0, protein=3.0, karb=12.0, yag=2.0,
                        tarih="2026-08-09")
        meal_idempotency.commit_once(entry, None)
    assert MealLog.query.filter_by(user_id=user.id).count() == 2


# ---------------------------------------------------------------------------
# The diary builder's zero-for-unknown behaviour (Gap 5), pinned as legacy.
# ---------------------------------------------------------------------------

def test_diary_builder_persists_unknown_mass_and_per_100g_as_zero(
        client, auth_user):
    """LEGACY, and deliberately unchanged by PR2A.

    `/api/diary/meal/<id>/item` derives grams and the per-100 g reference from
    `metric_serving_amount`, and when the provider had none it stores `0` for
    both — "we do not know" recorded as "we measured nothing". PR2A closes this
    for the mobile contract only; changing it here would change a live web
    payload, which the PR is not allowed to do.
    """
    created = client.post("/api/diary/meal", json={"meal_name": "Kahvaltı"})
    meal_id = created.get_json()["meal_id"]

    response = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "Bilinmeyen porsiyon",
        "serving_id": "77",
        "serving_quantity": 2,
        "serving_calories": 120,
        "serving_protein": 5,
        "serving_carbs": 10,
        "serving_fat": 6,
        # No metric_serving_amount: the provider did not state a mass.
    })
    assert response.status_code == 200

    item = CustomMealItem.query.filter_by(custom_meal_id=meal_id).one()
    assert item.grams == 0                  # unknown mass -> measured zero
    assert item.per_100g_calories == 0      # underivable reference -> zero
    assert item.per_100g_protein == 0
    assert item.calories == 240.0           # the multiplier itself is applied
    assert CustomMeal.query.get(meal_id).user_id == auth_user.id
