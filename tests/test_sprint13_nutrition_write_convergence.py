"""Sprint 13 PR3 - web nutrition write-path convergence.

PR1 (``docs/superpowers/specs/2026-08-30-sprint13-pr1-nutrition-closure-discovery.md``)
found one canonical consumed-food ledger with two trust models over it: the
mobile LogFood path re-fetches provider truth and rescales it server-side, while
the web posted browser-computed macros for the same provider food. PR3 closes
that gap on the DIRECT web write paths - F4, F5, F6, F7, F8 and F12 - and
satisfies N8 at the first-party scope stated in the report's section 12.

PR3B closes the independently discovered F15 diary gap and, together with these
direct-writer invariants, satisfies N2. Its successor tests live in
`tests/test_sprint13_nutrition_closure_discovery.py` and the diary route suite.

These are *invariant* tests, not characterization tests: each one fails if the
convergence regresses.

Provider truth is stubbed at the real network boundary
(``fatsecret._food_get_raw``), never at ``_provider_snapshot``: a test that
mocked the recompute away would prove nothing about the trust boundary it exists
to defend.

    python -m pytest tests/test_sprint13_nutrition_write_convergence.py -v
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    BarcodeFoodCache,
    CustomMeal,
    CustomMealItem,
    DailyQuest,
    MealLog,
    Message,
    UserQuestProgress,
)
from app.services import barcode as barcode_svc
from app.services import fatsecret, mobile_auth
from app.services.mobile_nutrition import serialization


REPO_ROOT = Path(__file__).resolve().parent.parent
NUTRITION_JS = (REPO_ROOT / "static" / "nutrition.js").read_text(encoding="utf-8")

FOOD_ID = "77777"
BAR_SERVING = "bar-1"
HUNDRED_G_SERVING = "bar-100g"
BARCODE = "5000159407236"

CANONICAL_SLOTS = ("Kahvaltı", "Öğle", "Akşam", "Ara Öğün")


def _raw_provider_food():
    """The shape ``fatsecret._food_get_raw`` returns, verbatim."""
    return {
        "food_id": FOOD_ID,
        "food_name": "Acme Protein Bar",
        "brand_name": "Acme",
        "servings": {"serving": [
            {
                "serving_id": BAR_SERVING,
                "serving_description": "1 bar",
                "metric_serving_amount": "60",
                "metric_serving_unit": "g",
                "calories": "210",
                "protein": "21",
                "carbohydrate": "23",
                "fat": "6",
            },
            {
                "serving_id": HUNDRED_G_SERVING,
                "serving_description": "100 g",
                "metric_serving_amount": "100",
                "metric_serving_unit": "g",
                "calories": "350",
                "protein": "35",
                "carbohydrate": "38.5",
                "fat": "10",
            },
        ]},
    }


@pytest.fixture
def provider(monkeypatch):
    """Stub the provider *network* boundary and record every lookup."""
    calls = []

    def fake_food_get_raw(food_id):
        calls.append(str(food_id))
        return _raw_provider_food() if str(food_id) == FOOD_ID else None

    monkeypatch.setattr(fatsecret, "_food_get_raw", fake_food_get_raw)
    return calls


@pytest.fixture
def no_ai(monkeypatch):
    """The AI estimation branch must never run on a provider or manual command."""
    from app.blueprints.nutrition import meallog as nutrition_meallog

    def boom(**kwargs):
        raise AssertionError("the LLM estimation branch must not be reached")

    monkeypatch.setattr(nutrition_meallog, "_openai_chat", boom)


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user):
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        return {"Authorization": "Bearer opaque-access-credential"}
    return _headers


def _key(suffix):
    return f"pr3-{suffix}-0000000000"


def _web_provider_log(client, *, slot="Öğle", food_id=FOOD_ID,
                      serving_id=BAR_SERVING, quantity=1,
                      discovery_source="search", key="provider", **extra):
    body = {
        "ogun": slot,
        "provider_food": {
            "provider": "fatsecret",
            "food_id": food_id,
            "serving_id": serving_id,
            "quantity": quantity,
            "discovery_source": discovery_source,
        },
    }
    body.update(extra)
    return client.post("/meal-log", json=body,
                       headers={"Idempotency-Key": _key(key)})


# ---------------------------------------------------------------------------
# F5 / N2 - the web provider path IS the mobile provider path
# ---------------------------------------------------------------------------

def test_web_and_mobile_provider_logs_produce_equivalent_ledger_rows(
        client, auth_user, make_user, as_mobile, provider, no_ai):
    """Section 41: same semantic command, same canonical row."""
    mobile_owner = make_user("pr3-parity-mobile")
    headers = as_mobile(mobile_owner)

    assert _web_provider_log(client, quantity=2, key="parity").status_code == 200
    mobile = client.post("/api/v1/nutrition/logs", headers={
        **headers, "Idempotency-Key": _key("parity-mobile")}, json={
            "kind": "provider_backed",
            "provider": "fatsecret",
            "food_id": FOOD_ID,
            "serving_id": BAR_SERVING,
            "quantity": 2,
            "slot": "ogle",
            "discovery_source": "search",
        })
    assert mobile.status_code == 201

    web_row = MealLog.query.filter_by(user_id=auth_user.id).one()
    mobile_row = MealLog.query.filter_by(user_id=mobile_owner.id).one()

    def domain(row):
        return (row.ogun, row.yemekler, row.kalori, row.protein, row.karb,
                row.yag, row.tarih, row.source)

    assert domain(web_row) == domain(mobile_row)
    assert domain(web_row) == (
        "Öğle", "Acme Protein Bar (2x 1 bar)", 420.0, 42.0, 46.0, 12.0,
        web_row.tarih, "search")


def test_provider_nutrition_is_the_provider_serving_times_quantity(
        client, auth_user, provider, no_ai):
    """Section 43: persisted macros are provider truth x quantity, nothing else."""
    for index, quantity in enumerate((1, "0.5", 3)):
        assert _web_provider_log(
            client, quantity=quantity, key=f"qty{index}").status_code == 200

    rows = MealLog.query.filter_by(user_id=auth_user.id).order_by(
        MealLog.id).all()
    assert [(r.kalori, r.protein, r.karb, r.yag) for r in rows] == [
        (210.0, 21.0, 23.0, 6.0),
        (105.0, 10.5, 11.5, 3.0),
        (630.0, 63.0, 69.0, 18.0),
    ]
    assert provider == [FOOD_ID, FOOD_ID, FOOD_ID], (
        "Every provider-backed write must re-fetch provider truth.")


def test_the_caller_cannot_supply_macros_for_a_provider_backed_food(
        client, auth_user, provider, no_ai):
    """Section 42: a mixed provider/manual command is refused, not merged."""
    response = _web_provider_log(client, key="mixed", override_macros={
        "kalori": 99999, "protein": 0, "karb": 0, "yag": 50000})

    assert response.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    assert provider == [], "A refused command must not reach the provider."


@pytest.mark.parametrize("extra", [
    {"yemekler": "tampered description"},
    {"image": "data:image/png;base64,AAAA"},
])
def test_a_provider_command_may_not_smuggle_a_photo_or_a_description(
        client, auth_user, provider, no_ai, extra):
    """The provider command carries identities only (section 7)."""
    response = _web_provider_log(client, key="smuggle", **extra)

    assert response.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    assert provider == []


def test_an_unknown_provider_food_or_serving_is_a_deterministic_4xx(
        client, auth_user, provider, no_ai):
    """Section 43: no row, no invented nutrition."""
    assert _web_provider_log(
        client, food_id="00000", key="nofood").status_code == 404
    assert _web_provider_log(
        client, serving_id="not-a-serving", key="noserving").status_code == 404
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_a_provider_write_requires_a_valid_idempotency_key(
        client, auth_user, provider, no_ai):
    """Section 8: provider-backed writes inherit the shared writer contract."""
    response = client.post("/meal-log", json={
        "ogun": "Öğle",
        "provider_food": {
            "provider": "fatsecret", "food_id": FOOD_ID,
            "serving_id": BAR_SERVING, "quantity": 1,
            "discovery_source": "search"},
    })

    assert response.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    assert provider == []


def test_provider_replay_returns_the_same_row_and_awards_one_quest(
        client, auth_user, provider, no_ai):
    """Section 10: same key + same command replays; XP is awarded once."""
    db.session.add(DailyQuest(title="Öğün Kaydet", description="Bugün bir öğün",
                              points_reward=20, quest_type="meal_logged"))
    db.session.commit()

    first = _web_provider_log(client, key="replay")
    second = _web_provider_log(client, key="replay")

    assert first.status_code == second.status_code == 200
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert first.get_json().get("quest_awarded")
    assert second.get_json().get("quest_awarded") is None
    assert UserQuestProgress.query.filter_by(user_id=auth_user.id).count() == 1


def test_a_reused_key_with_a_different_command_conflicts(
        client, auth_user, provider, no_ai):
    """Section 10: never silently return the wrong meal."""
    assert _web_provider_log(client, key="conflict").status_code == 200
    conflict = _web_provider_log(client, key="conflict", quantity=2)

    assert conflict.status_code == 409
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


def test_the_web_provider_response_publishes_no_raw_database_identifier(
        client, auth_user, provider, no_ai):
    """Section 9 / 24: the web transport keeps its shape and leaks no row id."""
    body = _web_provider_log(client, key="shape").get_json()

    assert set(body) >= {"message", "nutrients"}
    assert body["nutrients"] == {
        "kalori": 210.0, "protein": 21.0, "karb": 23.0, "yag": 6.0}
    assert "meal_log_id" not in body
    assert "id" not in body
    assert "revision" not in body


# ---------------------------------------------------------------------------
# F5 / section 11 - manual nutrition stays manual, within the shared bounds
# ---------------------------------------------------------------------------

def test_manual_macros_persist_unrecomputed_and_never_call_the_provider(
        client, auth_user, provider, no_ai):
    """Manual entry is user-authoritative by design (C3)."""
    response = client.post("/meal-log", json={
        "ogun": "Akşam", "yemekler": "ev yapımı mercimek çorbası",
        "override_macros": {
            "kalori": 642.0, "protein": 44.0, "karb": 61.0, "yag": 21.0},
    })

    assert response.status_code == 200
    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert (row.kalori, row.protein, row.karb, row.yag) == (
        642.0, 44.0, 61.0, 21.0)
    assert row.source == "manual"
    assert provider == [], "Manual entry must not be forced through FatSecret."


@pytest.mark.parametrize("macros", [
    {"kalori": float("nan"), "protein": 1, "karb": 1, "yag": 1},
    {"kalori": "Infinity", "protein": 1, "karb": 1, "yag": 1},
    {"kalori": -10, "protein": -2, "karb": -3, "yag": -4},
    {"kalori": 100001, "protein": 1, "karb": 1, "yag": 1},
    {"kalori": "not-a-number", "protein": 1, "karb": 1, "yag": 1},
    {"kalori": None, "protein": 1, "karb": 1, "yag": 1},
    {"kalori": 100, "protein": 1, "karb": 1},
    {"kalori": True, "protein": 1, "karb": 1, "yag": 1},
])
def test_malformed_manual_nutrition_is_rejected_not_coerced_to_zero(
        client, auth_user, provider, no_ai, macros):
    """Section 11: the typed manual bounds are the only manual policy."""
    response = client.post("/meal-log", json={
        "ogun": "Akşam", "yemekler": "hatalı giriş",
        "override_macros": macros,
    })

    assert response.status_code == 400, macros
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    assert provider == []


def test_manual_and_provider_commands_cannot_be_mixed_in_either_direction(
        client, auth_user, provider, no_ai):
    """Section 33: the transport states the command; it is never inferred."""
    response = client.post("/meal-log", json={
        "ogun": "Öğle", "yemekler": "tavuk",
        "override_macros": {"kalori": 1, "protein": 1, "karb": 1, "yag": 1},
        "provider_food": {
            "provider": "fatsecret", "food_id": FOOD_ID,
            "serving_id": BAR_SERVING, "quantity": 1,
            "discovery_source": "search"},
    }, headers={"Idempotency-Key": _key("both")})

    assert response.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_the_manual_bounds_are_the_shared_typed_snapshot():
    """Section 11: one validation path, not a second looser numeric policy."""
    from app.services import mobile_log_food

    assert callable(getattr(mobile_log_food, "parse_manual_nutrition", None)), (
        "The web manual branch must reuse the mobile snapshot parser, not "
        "reimplement number validation.")
    parsed = mobile_log_food.parse_manual_nutrition({
        "energy_kcal": "1", "protein_g": 1, "carbohydrate_g": 1, "fat_g": 1})
    assert isinstance(parsed, mobile_log_food.ManualNutritionSnapshot)


# ---------------------------------------------------------------------------
# F7 / C12 - the canonical web meal slot vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slot", CANONICAL_SLOTS)
def test_every_canonical_web_slot_is_accepted(
        client, auth_user, provider, no_ai, slot):
    assert _web_provider_log(client, slot=slot, key="slot").status_code == 200
    assert MealLog.query.filter_by(user_id=auth_user.id, ogun=slot).count() == 1


@pytest.mark.parametrize("slot", [
    "brunch, sort of", "", "Ogle", "breakfast", "x" * 120, "AI Koç",
])
def test_an_invalid_slot_is_rejected_before_any_side_effect(
        client, auth_user, provider, no_ai, monkeypatch, slot):
    """Section 13/45: no provider call, no upload, no LLM, no row, no quest."""
    import s3_helper
    from app.blueprints.nutrition import meallog as nutrition_meallog

    monkeypatch.setattr(s3_helper, "upload_image", lambda *a, **kw: (
        _ for _ in ()).throw(
            AssertionError("photo upload ran before slot validation")))
    monkeypatch.setattr(
        nutrition_meallog, "complete_quest_for_user", lambda *a, **kw: (
            _ for _ in ()).throw(
                AssertionError("quest mutated before slot validation")))

    response = client.post("/meal-log", json={
        "ogun": slot, "yemekler": "menemen",
        "override_macros": {
            "kalori": 400.0, "protein": 20.0, "karb": 12.0, "yag": 28.0},
    })

    assert response.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0
    assert provider == []


def test_the_slot_guard_does_not_touch_the_intentional_non_slot_writers():
    """C12: 'AI Koç' and '<sender>...alınan öneri' remain legitimate labels."""
    assert serialization.slot_token("AI Koç") == serialization.SLOT_UNKNOWN
    assert set(serialization.SLOT_BY_MEAL_LABEL) == set(CANONICAL_SLOTS)


# ---------------------------------------------------------------------------
# F4 - the multi-food quick log no longer means "100 g of everything"
# ---------------------------------------------------------------------------

def _js_function_body(name):
    """Return the balanced body of a named function in nutrition.js."""
    start = NUTRITION_JS.index(f"function {name}(")
    open_brace = NUTRITION_JS.index("{", start)
    depth, index, quote = 0, open_brace, None
    while index < len(NUTRITION_JS):
        char = NUTRITION_JS[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return NUTRITION_JS[open_brace:index + 1]
        index += 1
    raise AssertionError(f"unbalanced body for {name}")


def test_diary_provider_writes_send_semantics_not_canonical_nutrition():
    """F15: diary macros may preview in the browser, never authorize writes."""
    forbidden = (
        "serving_calories", "serving_protein", "serving_carbs",
        "serving_fat", "metric_serving_amount",
    )
    for name in (
            "confirmServingModal", "updateDiaryServing",
            "updateDiaryServingQty", "updateDiaryServingQtyOnly"):
        body = _js_function_body(name)
        assert not any(field in body for field in forbidden), name
    add_body = _js_function_body("confirmServingModal")
    assert "fatsecret_food_id" in add_body
    assert "serving_id" in add_body and "serving_quantity" in add_body


def test_the_multi_food_quick_log_computes_no_macros_of_its_own():
    """F4: the per-100 g aggregate is gone from the persistence path."""
    body = _js_function_body("logMeal")

    assert "per_100g" not in body, (
        "F4 regression: a browser per-100 g figure is back in the write path.")
    assert "override_macros" not in body, (
        "F5 regression: the multi-food branch is persisting client macros.")
    assert "reduce(" not in body, (
        "F4 regression: a client-side macro aggregate is back in logMeal.")
    assert "postSelectedFood" in body, (
        "Each selected food must be written through the shared command helper.")


def test_each_selected_food_is_written_as_an_explicit_semantic_command():
    """Sections 18/19: provider identity + serving + quantity, or manual."""
    body = _js_function_body("postSelectedFood")

    assert "provider_food" in body
    assert "serving_id" in body and "quantity" in body
    assert "per_100g" not in body, (
        "A per-100 g figure must never reach the persistence command.")


def test_a_selected_food_carries_a_chosen_serving_and_quantity():
    """Section 18: serving identity is chosen, never inferred."""
    body = _js_function_body("addSelectedFood")

    assert "serving_id" in body and "quantity" in body
    for guessed in ("serving_id: 100", "servings[0]", "serving_id: '1'"):
        assert guessed not in body, (
            "Serving identity must come from the user's choice, not a default.")


def test_the_serving_modal_meallog_mode_sends_no_client_macros():
    """Sections 15/16: the browser preview is not the persistence authority."""
    body = _js_function_body("logProviderFoodToLedger")

    assert "provider_food" in body
    assert "override_macros" not in body
    assert "_smCurrentMacros" not in body, (
        "The preview calculation is being posted as persistence authority.")


def test_the_barcode_flow_still_stamps_the_barcode_discovery_source(
        client, auth_user, provider, no_ai):
    """Section 16: a barcode meal is never stamped manual or search."""
    assert _web_provider_log(
        client, discovery_source="barcode", key="barcode").status_code == 200

    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert row.source == "barcode"
    assert row.kalori == 210.0, "Server truth, not the browser preview."


# ---------------------------------------------------------------------------
# F6 / C13 - the legacy compatibility route is safe and deprecated
# ---------------------------------------------------------------------------

def _seed_barcode_cache():
    food = barcode_svc.normalize_food_model(BARCODE, {
        "food_id": FOOD_ID,
        "name": "Acme Protein Bar",
        "brand": "Acme",
        "servings": [{
            "serving_id": BAR_SERVING,
            "serving_description": "1 bar",
            "metric_serving_amount": 60,
            "metric_serving_unit": "g",
            "calories": 210, "protein": 21, "carbs": 23, "fat": 6,
        }],
    })
    db.session.add(BarcodeFoodCache(
        barcode=BARCODE, food_id=food["food_id"],
        food_name=food["name"], brand=food["brand"], payload=food))
    db.session.commit()
    return food


def test_legacy_barcode_add_ignores_caller_supplied_nutrition(
        client, auth_user):
    """F6: only server-resolved provider truth may be persisted."""
    _seed_barcode_cache()

    response = client.post("/api/food/barcode/add", json={
        "barcode": BARCODE,
        "meal": "Ara Öğün",
        "serving_id": BAR_SERVING,
        "serving_quantity": 1,
        "food": {
            "barcode": BARCODE, "food_id": FOOD_ID,
            "name": "Tampered", "brand": "Acme",
            "default_serving_id": BAR_SERVING,
            "servings": [{
                "id": BAR_SERVING, "description": "1 bar",
                "metric_serving_amount": 60, "metric_serving_unit": "g",
                "is_bulk": False,
                "macros": {"calories": 5.0, "protein": 0.0,
                           "carbs": 0.0, "fat": 0.0},
            }],
        },
    })

    assert response.status_code == 200
    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert (row.kalori, row.protein, row.karb, row.yag) == (
        210.0, 21.0, 23.0, 6.0), "The caller's food object was trusted."
    assert row.yemekler == "Acme Protein Bar (1x 1 bar)"


def test_legacy_barcode_add_publishes_no_raw_meal_log_id(client, auth_user):
    """Section 24: neither the first success nor the replay exposes the row id."""
    _seed_barcode_cache()
    headers = {"Idempotency-Key": _key("legacy")}
    payload = {
        "barcode": BARCODE, "meal": "Ara Öğün",
        "serving_id": BAR_SERVING, "serving_quantity": 1,
    }

    first = client.post("/api/food/barcode/add", json=payload, headers=headers)
    second = client.post("/api/food/barcode/add", json=payload, headers=headers)

    assert first.status_code == second.status_code == 200
    assert "meal_log_id" not in first.get_json()
    assert "meal_log_id" not in second.get_json()
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1


def test_legacy_barcode_add_is_deprecated_but_still_responds(client, auth_user):
    """C13: deprecation is observable; removal is PR5's decision, with evidence."""
    _seed_barcode_cache()

    response = client.post("/api/food/barcode/add", json={
        "barcode": BARCODE, "meal": "Ara Öğün",
        "serving_id": BAR_SERVING, "serving_quantity": 1,
    })

    assert response.status_code == 200
    assert response.headers.get("Deprecation") == "true"
    assert "/meal-log" in response.headers.get("Link", "")
    assert "sunset" not in {k.lower() for k in response.headers.keys()}, (
        "No sunset date may be invented; PR5 removes this route only with "
        "evidence (C13).")


def test_legacy_barcode_add_fails_closed_without_provider_identity(
        client, auth_user):
    """Section 23: an unresolvable legacy payload persists nothing."""
    response = client.post("/api/food/barcode/add", json={
        "meal": "Ara Öğün",
        "food": {"name": "Ghost", "servings": [{
            "id": "s1", "description": "1 bar",
            "macros": {"calories": 900.0, "protein": 90.0,
                       "carbs": 0.0, "fat": 0.0}}]},
        "serving_id": "s1", "serving_quantity": 1,
    })

    assert response.status_code in (400, 404)
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_the_legacy_barcode_route_still_exists(app):
    """C13: PR3 made it safe. PR5 investigated removal and KEPT it deprecated."""
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/food/barcode/add" in rules


# ---------------------------------------------------------------------------
# F8 - shared meal suggestion provenance and replay
# ---------------------------------------------------------------------------

@pytest.fixture
def accepted_suggestion(client, auth_user, make_user, monkeypatch):
    """A deterministic meal suggestion addressed to the authenticated user."""
    from app.blueprints import social as social_bp

    sender = make_user("pr3-suggestion-sender")
    message = Message(sender_id=sender.id, receiver_id=auth_user.id,
                      body="tavuk + pilav yemelisin",
                      message_type="suggestion_meal")
    db.session.add(message)
    db.session.commit()

    monkeypatch.setattr(social_bp, "_calculate_meal_suggestion", lambda snap: {
        "items": ("tavuk", "pilav"),
        "kalori": 590.0, "protein": 64.7, "karb": 56.0, "yag": 7.6,
    })
    return message.id


def test_an_accepted_suggestion_records_its_own_provenance(
        client, auth_user, accepted_suggestion):
    """F8: the suggestion writer no longer reads back as a hand-typed meal."""
    response = client.post(f"/suggest/respond/{accepted_suggestion}",
                           json={"action": "accept"})

    assert response.status_code == 200
    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert row.source == "suggestion"
    assert row.idempotency_key, "The suggestion writer must carry a replay key."
    assert serialization.source_token(row.source) == "suggestion"
    assert "suggestion" in serialization.KNOWN_SOURCES


def test_the_suggestion_replay_key_is_derived_from_the_message_identity(
        client, auth_user, accepted_suggestion):
    """Section 27: stable identity, never randomness or mutable macro text."""
    from app.services.meal_idempotency import _KEY_RE

    client.post(f"/suggest/respond/{accepted_suggestion}",
                json={"action": "accept"})

    row = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert str(accepted_suggestion) in row.idempotency_key
    assert _KEY_RE.fullmatch(row.idempotency_key)


def test_replaying_a_suggestion_acceptance_writes_no_second_row(
        client, auth_user, accepted_suggestion):
    """Sections 29/50: one row, one quest, deterministic already-handled reply."""
    db.session.add(DailyQuest(title="Öğün Kaydet", description="Bugün bir öğün",
                              points_reward=20, quest_type="meal_logged"))
    db.session.commit()

    first = client.post(f"/suggest/respond/{accepted_suggestion}",
                        json={"action": "accept"})
    second = client.post(f"/suggest/respond/{accepted_suggestion}",
                         json={"action": "accept"})

    assert first.status_code == 200
    assert second.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert UserQuestProgress.query.filter_by(user_id=auth_user.id).count() == 1


def test_the_suggestion_write_stays_inside_the_atomic_social_transaction():
    """Section 28: reuse must not split the message-claim transaction."""
    source = (REPO_ROOT / "app" / "blueprints" / "social.py"
              ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    persist = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_persist_meal_suggestion")
    body = ast.unparse(persist)

    assert "commit_once" not in body and "db.session.commit" not in body, (
        "Committing inside the suggestion writer would prematurely commit the "
        "message state transition and the reply message with it.")
    assert "idempotency_key" in body


# ---------------------------------------------------------------------------
# F12 - the diary commit's replay authority, made explicit
# ---------------------------------------------------------------------------

def test_a_replayed_diary_commit_writes_no_second_ledger_row(
        client, auth_user):
    """F12: `_claim_diary_meal`'s atomic is_logged flip IS the replay authority."""
    from app.timeutil import app_today

    meal = CustomMeal(user_id=auth_user.id, meal_name="Öğle",
                      date_key=app_today().isoformat())
    db.session.add(meal)
    db.session.flush()
    db.session.add(CustomMealItem(
        custom_meal_id=meal.id, food_name="Tavuk", grams=150.0,
        calories=248.0, protein=46.5, carbs=0.0, fat=5.4,
        per_100g_calories=165.0, per_100g_protein=31.0,
        per_100g_carbs=0.0, per_100g_fat=3.6))
    db.session.commit()

    first = client.post(f"/api/diary/meal/{meal.id}/log")
    second = client.post(f"/api/diary/meal/{meal.id}/log")

    assert first.status_code == 200
    assert second.status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id,
                                   source="diary").count() == 1


def test_the_diary_commit_relies_on_the_claim_and_not_a_redundant_key():
    """F12 / section 30: the authority is declared, not duplicated."""
    source = (REPO_ROOT / "app" / "blueprints" / "nutrition" / "diary.py"
              ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    commit = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "diary_log_meal")
    calls = {ast.unparse(node.func) for node in ast.walk(commit)
             if isinstance(node, ast.Call)}

    assert "_claim_diary_meal" in calls, (
        "The atomic claim is the diary commit's replay authority (F12).")
    assert not {c for c in calls if "commit_once" in c or "idempotency" in c}, (
        "A second replay mechanism was attached without evidence that the "
        "claim is insufficient (PR1 F12).")


# ---------------------------------------------------------------------------
# N2 architecture guards
# ---------------------------------------------------------------------------

def test_the_web_provider_branch_delegates_to_the_canonical_log_food_service():
    """Section 52: no second recompute, no second scaler, no second fingerprint."""
    source = (REPO_ROOT / "app" / "blueprints" / "nutrition" / "meallog.py"
              ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {ast.unparse(node.func) for node in ast.walk(tree)
             if isinstance(node, ast.Call)}

    assert any(call.endswith("log_food") for call in calls), (
        "The web provider path must call the canonical LogFood service.")
    assert not any("_provider_snapshot" in call or call.endswith("servings")
                   for call in calls), (
        "The blueprint is recomputing provider nutrition itself.")
    assert "semantic_fingerprint" not in source, (
        "The fingerprint authority belongs to mobile_log_food alone.")


def test_no_second_log_food_service_was_forked():
    """Section 4: the mobile_ name is historical; the authority is single."""
    services = REPO_ROOT / "app" / "services"
    forks = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in services.rglob("*.py")
        if ("log_food" in path.name or "food_persistence" in path.name)
        and "mobile_log_food" not in path.as_posix())

    assert forks == []


def test_no_direct_web_write_path_persists_caller_supplied_provider_nutrition():
    """Structurally: on THESE routes, the request-macro path is manual-only.

    Scope, corrected after the independent PR3 review: this guard covers
    `/meal-log` and `POST /api/food/barcode/add`. It does NOT cover the
    diary-builder staging writer, which still carries caller-supplied nutrition
    for a provider-identified food into `MealLog` (F15, open, owned by PR3B).
    It is therefore evidence for those two routes, not for N2 repository-wide.
    """
    meallog = (REPO_ROOT / "app" / "blueprints" / "nutrition" / "meallog.py"
               ).read_text(encoding="utf-8")
    food = (REPO_ROOT / "app" / "blueprints" / "food.py"
            ).read_text(encoding="utf-8")

    assert "override_macros" in meallog, (
        "Manual entry is still user-authoritative (C3) - do not delete it.")

    tree = ast.parse(food)
    barcode_add = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "barcode_add_to_diary")
    body = ast.unparse(barcode_add)
    assert "data.get('food')" not in body, (
        "F6 regression: the legacy route reads a caller-supplied food object "
        "again.")
    assert "meal_log_id" not in body, "F6 regression: raw row id is published."
    assert "get_barcode_product" in body, (
        "The legacy route must resolve provider truth server-side.")
