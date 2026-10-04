"""Training generation reliability PR B — server-validated catalog identity.

The provider no longer authors exercise identity as free text. The server
computes ONE closed compatible-choice set per request
(``exercise_choices.compatible_exercise_choices``), the prompt offers exactly
those opaque ``exercise_id`` values, the provider returns a chosen ID plus
prescription (no ``isim``), and the server validates that ID against the SAME
set and writes the catalog's canonical name before anything persists.

Hermetic: the provider is always a spy or a stub.
"""
import json
import logging
import re

import pytest

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import TrainingPlan, TrainingPlanGenerationOperation, User
from app.services import ai as ai_module
from app.services import exercise_catalog, premium
from app.services.exercise_catalog import (
    ID_PATTERN,
    ExerciseContext,
    load_exercise_catalog,
)
from app.services.training_generation import exercise_resolution
from app.services.training_generation import service as training_service
from app.services.training_generation.exercise_choices import (
    CompatibleExerciseChoices,
    compatible_exercise_choices,
)
from app.services.training_generation.exercise_resolution import (
    canonicalize_generated_exercises,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseIncompatibleError,
    GenerationExerciseUnresolvedError,
    SchemaInvalidError,
)
from app.services.training_generation.plan_schema import (
    PRIMARY_MAX_TOKENS,
    PROVIDER_EXERCISE_KEYS,
)
from app.services.training_generation.response_validator import validate_generated_plan
from tests.test_mobile_training_generation_api import (  # noqa: F401 - fixtures
    CANONICAL,
    CURRENT_PATH,
    POST_PATH,
    as_mobile,
    mobile_user,
)
from tests.test_sprint11_training_generation_output import (
    _prefs,
    _provider_exercise,
    _provider_week,
    _session,
    _week,
    _exercise,
)


CODE_IDENTITY_INVALID = "TRAINING_PLAN_GENERATION_EXERCISE_IDENTITY_INVALID"
CODE_INCOMPATIBLE = "TRAINING_PLAN_GENERATION_EXERCISE_INCOMPATIBLE"
CODE_UNRESOLVED = "TRAINING_PLAN_GENERATION_EXERCISE_UNRESOLVED"
CODE_SCHEMA_INVALID = "TRAINING_PLAN_GENERATION_SCHEMA_INVALID"

GYM = ExerciseContext(equipment_context="spor_salonu")
HOME = ExerciseContext(equipment_context="ev")


def _turkish_week(exercise_id="ex_barbell_back_squat"):
    """A provider week whose every visible text field is Turkish."""
    week = _provider_week(exercises=[
        _provider_exercise(exercise_id),
        _provider_exercise("ex_barbell_row"),
        _provider_exercise("ex_push_up"),
    ])
    for day in week["program"]:
        day["odak"] = "Tüm Vücut" if day["tip"] == "antrenman" else "Aktif Toparlanma"
        for exercise in day["egzersizler"]:
            exercise["not"] = "Kontrollü tempo, çömelmede dizler dışa"
            exercise["dinlenme"] = "90 saniye"
    return week


def _stored_exercises(user_id):
    plan = TrainingPlan.query.filter_by(user_id=user_id).one()
    document = json.loads(plan.plan_data)
    return [ex for day in document["program"] for ex in day["egzersizler"]]


def _native_provider(monkeypatch, *responses):
    """Install a counting provider stub on the native route; return its calls."""
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        response = responses[min(len(calls), len(responses)) - 1]
        return response if isinstance(response, str) else json.dumps(
            response, ensure_ascii=False)

    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", complete)
    return calls


def _quota_left(user_id):
    return premium.remaining_ai_plans(db.session.get(User, user_id), "training")


def _assert_durable_terminal_failure(user_id, code):
    db.session.expire_all()
    operation = TrainingPlanGenerationOperation.query.one()
    assert operation.status == "FAILED"
    assert operation.error_code == code
    assert operation.error_http_status == 422
    assert operation.error_retryable is False
    assert operation.candidate_plan_data is None  # no staged candidate
    assert operation.candidate_score is None
    assert operation.training_plan_id is None
    assert operation.quota_reserved is False
    assert TrainingPlan.query.filter_by(user_id=user_id).count() == 0


@pytest.fixture
def quota_on(app):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    yield
    app.config["AI_PLAN_QUOTA_ENABLED"] = False


@pytest.fixture
def retired_catalog(tmp_path, monkeypatch):
    """The real catalog plus one retired bodyweight entry.

    Nothing in the shipped catalog is retired yet, so this is the only way to
    reach the inactive-ID branch through the real pipeline.
    """
    raw = json.loads(exercise_catalog.CATALOG_PATH.read_text(encoding="utf-8"))
    raw["exercises"].append({
        "exercise_id": "ex_retired_bodyweight_lift",
        "canonical_name": "Retired Bodyweight Lift",
        "aliases": [],
        "equipment": ["bodyweight"],
        "movement": "squat",
        "primary_region": "lower_body",
        "active": False,
    })
    path = tmp_path / "exercises.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(exercise_catalog, "CATALOG_PATH", path)
    load_exercise_catalog.cache_clear()
    yield
    load_exercise_catalog.cache_clear()


# ── 1. Valid canonical ID ────────────────────────────────────────────────────


def test_valid_id_persists_catalog_identity_and_name_in_one_completion(
        client, mobile_user, as_mobile, monkeypatch, quota_on):
    calls = _native_provider(monkeypatch, _turkish_week())

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "valid-id"))

    assert created.status_code == 201, created.json
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == PRIMARY_MAX_TOKENS
    stored = _stored_exercises(mobile_user.id)
    first = stored[0]
    assert first["exercise_id"] == "ex_barbell_back_squat"
    assert first["isim"] == "Barbell Back Squat"
    assert first["set"] == 3 and first["tekrar"] == "8-12"
    assert first["dinlenme"] == "90 saniye"
    assert first["not"].startswith("Kontrollü tempo")
    catalog = load_exercise_catalog()
    for exercise in stored:
        assert exercise["isim"] == catalog.by_id[exercise["exercise_id"]].canonical_name
        assert set(exercise) == {
            "isim", "set", "tekrar", "dinlenme", "not", "exercise_id"}
    assert _quota_left(mobile_user.id) == 0


# ── 2. Translated display text: the original failure class is gone ──────────


def test_turkish_visible_text_beside_valid_ids_succeeds_with_catalog_names(
        client, mobile_user, as_mobile, monkeypatch):
    """The request language is Turkish and every visible provider field is
    Turkish; identity is the ID, so nothing has to be translated back."""
    mobile_user.preferred_language = "tr"
    db.session.commit()
    _native_provider(monkeypatch, _turkish_week())

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "turkish-text"))

    assert created.status_code == 201, created.json
    stored = _stored_exercises(mobile_user.id)
    assert [ex["isim"] for ex in stored[:3]] == [
        "Barbell Back Squat", "Barbell Row", "Push-Up"]


def test_translated_text_beside_a_valid_id_cannot_cause_unresolved(monkeypatch):
    """Generation never performs a NAME lookup: a resolver that would raise
    ExerciseUnresolved for any name is never consulted for a provider plan."""
    def name_lookup_forbidden(exercise_id=None, name=None, catalog=None):
        assert name is None, "generation must not resolve exercise identity by name"
        return exercise_catalog.resolve_exercise(exercise_id=exercise_id, catalog=catalog)

    monkeypatch.setattr(exercise_resolution, "resolve_exercise", name_lookup_forbidden)
    validated, _ = validate_generated_plan(_turkish_week(), _prefs())

    canonical = canonicalize_generated_exercises(
        validated, compatible_exercise_choices(GYM))

    assert canonical["program"][0]["egzersizler"][0]["isim"] == "Barbell Back Squat"


def test_the_pr_a_translated_name_payload_no_longer_reaches_name_resolution(
        client, mobile_user, as_mobile, monkeypatch):
    """The exact PR A failure shape — a translated ``isim`` the catalog cannot
    resolve — is now a provider FORMATTING miss (no ``exercise_id``), so it
    takes the existing single bounded repair turn instead of terminating as
    EXERCISE_UNRESOLVED. A repaired ID-based answer persists normally."""
    legacy = _week(exercises=[
        _exercise("Halterle Çömelme"), _exercise("Row"), _exercise("Push-up")])
    calls = _native_provider(monkeypatch, legacy, _turkish_week())

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "pr-a-shape"))

    assert created.status_code == 201, created.json
    assert len(calls) == 2
    assert "REPAIR:" in calls[1]["messages"][0]["content"]
    assert "exercise_id" in calls[1]["messages"][0]["content"]
    assert _stored_exercises(mobile_user.id)[0]["isim"] == "Barbell Back Squat"


# ── 3. Fabricated display name beside a valid ID ─────────────────────────────


@pytest.mark.parametrize("fabricated", [
    "Magic Chest Exercise", "Halterle Çömelme", "Barbell Back Squat"])
def test_any_provider_display_name_is_outside_the_schema(fabricated):
    """The provider shape has no name slot at all — not even a correct one —
    so no provider text can be carried into persistence as identity."""
    assert "isim" not in PROVIDER_EXERCISE_KEYS
    week = _provider_week()
    week["program"][0]["egzersizler"][0]["isim"] = fabricated
    with pytest.raises(SchemaInvalidError, match="unknown"):
        validate_generated_plan(week, _prefs())


def test_fabricated_name_never_persists_even_when_repair_fixes_the_shape(
        client, mobile_user, as_mobile, monkeypatch):
    with_name = _provider_week(exercises=[_provider_exercise("ex_barbell_bench_press")])
    with_name["program"][0]["egzersizler"][0]["isim"] = "Magic Chest Exercise"
    _native_provider(monkeypatch, with_name, _provider_week(
        exercises=[_provider_exercise("ex_barbell_bench_press")]))

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "fabricated"))

    assert created.status_code == 201
    raw = TrainingPlan.query.filter_by(user_id=mobile_user.id).one().plan_data
    assert "Magic Chest Exercise" not in raw
    assert _stored_exercises(mobile_user.id)[0]["isim"] == "Barbell Bench Press"


def test_fabricated_name_that_survives_repair_fails_closed_without_a_plan(
        client, mobile_user, as_mobile, monkeypatch, quota_on):
    with_name = _provider_week()
    with_name["program"][0]["egzersizler"][0]["isim"] = "Magic Chest Exercise"
    calls = _native_provider(monkeypatch, with_name)

    response = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "fabricated-2"))

    assert response.status_code == 422
    assert response.json["error"]["code"] == CODE_SCHEMA_INVALID
    assert len(calls) == 2
    _assert_durable_terminal_failure(mobile_user.id, CODE_SCHEMA_INVALID)
    assert _quota_left(mobile_user.id) == 1


# ── 4. Unknown ID ────────────────────────────────────────────────────────────


def test_unknown_id_fails_closed_with_refund_and_no_candidate(
        client, mobile_user, as_mobile, monkeypatch, quota_on):
    calls = _native_provider(monkeypatch, _provider_week(
        exercises=[_provider_exercise("ex_quantum_laser_row")]))

    response = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "unknown-id"))

    assert response.status_code == 422
    assert response.json["error"]["code"] == CODE_IDENTITY_INVALID
    assert response.json["error"]["retryable"] is False
    assert "ex_quantum_laser_row" not in json.dumps(response.json)
    assert len(calls) == 1  # identity failures never enter the repair turn
    _assert_durable_terminal_failure(mobile_user.id, CODE_IDENTITY_INVALID)
    assert _quota_left(mobile_user.id) == 1
    assert client.get(CURRENT_PATH, headers=as_mobile(mobile_user)).json == {"plan": None}


# ── 5. Malformed ID ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("malformed", [
    "Goblet Squat",          # a display name in the ID slot
    "EX_GOBLET_SQUAT",       # case-changed
    " ex_goblet_squat",      # whitespace-edited (never stripped)
    "ex_goblet_squat\n",
    "ex-goblet-squat",       # re-punctuated
    "kadeh_squat",           # translated, prefix lost
    "ex_",
])
def test_malformed_id_is_identity_invalid_deterministically(malformed):
    week = _provider_week(exercises=[_provider_exercise(malformed)])
    validated, _ = validate_generated_plan(week, _prefs())
    for _ in range(2):  # same input, same typed outcome
        with pytest.raises(GenerationExerciseIdentityInvalidError) as caught:
            canonicalize_generated_exercises(validated, compatible_exercise_choices(GYM))
        assert caught.value.resolution_category == "malformed_id"


@pytest.mark.parametrize("not_a_string", [None, 7, ["ex_goblet_squat"], ""])
def test_non_string_or_empty_id_is_a_schema_miss(not_a_string):
    week = _provider_week()
    week["program"][0]["egzersizler"][0]["exercise_id"] = not_a_string
    with pytest.raises(SchemaInvalidError, match="exercise_id"):
        validate_generated_plan(week, _prefs())


# ── 6. Inactive ID ───────────────────────────────────────────────────────────


def test_inactive_id_is_unresolved_and_never_offered(retired_catalog):
    choices = compatible_exercise_choices(HOME)
    assert "ex_retired_bodyweight_lift" not in choices
    week = _provider_week(exercises=[_provider_exercise("ex_retired_bodyweight_lift")])
    validated, _ = validate_generated_plan(week, _prefs())

    with pytest.raises(GenerationExerciseUnresolvedError) as caught:
        canonicalize_generated_exercises(validated, choices)
    assert caught.value.resolution_category == "inactive_id"


def test_inactive_id_fails_closed_natively(
        client, mobile_user, as_mobile, monkeypatch, retired_catalog):
    _native_provider(monkeypatch, _provider_week(
        exercises=[_provider_exercise("ex_retired_bodyweight_lift")]))

    response = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "inactive-id"))

    assert response.status_code == 422
    assert response.json["error"]["code"] == CODE_UNRESOLVED
    _assert_durable_terminal_failure(mobile_user.id, CODE_UNRESOLVED)


# ── 7. Incompatible ID ───────────────────────────────────────────────────────


def test_real_active_id_outside_the_request_set_fails_closed_without_substitution(
        client, mobile_user, as_mobile, monkeypatch, quota_on):
    calls = _native_provider(monkeypatch, _provider_week(
        exercises=[_provider_exercise("ex_barbell_back_squat")]))

    response = client.post(
        POST_PATH, json={**CANONICAL, "ekipman": "ev"},
        headers=as_mobile(mobile_user, "incompatible"))

    assert response.status_code == 422
    assert response.json["error"]["code"] == CODE_INCOMPATIBLE
    assert len(calls) == 1
    _assert_durable_terminal_failure(mobile_user.id, CODE_INCOMPATIBLE)
    assert _quota_left(mobile_user.id) == 1


def test_membership_is_checked_against_the_exact_supplied_set():
    """An ID the catalog would call compatible is still refused when THIS
    request's closed set did not offer it — the set is the authority, not a
    recomputation of compatibility."""
    full = compatible_exercise_choices(GYM)
    narrowed = CompatibleExerciseChoices(
        context=GYM, catalog_version=full.catalog_version,
        choices=tuple(c for c in full.choices if c.exercise_id != "ex_barbell_row"))
    week = _provider_week(exercises=[_provider_exercise("ex_barbell_row")])
    validated, _ = validate_generated_plan(week, _prefs())

    canonicalize_generated_exercises(validated, full)  # sanity: really compatible
    with pytest.raises(GenerationExerciseIncompatibleError) as caught:
        canonicalize_generated_exercises(validated, narrowed)
    assert caught.value.resolution_category == "outside_choice_set"


def test_equipment_is_rechecked_even_for_a_member_id():
    """Defence in depth: a set that (wrongly) contains a barbell lift for a
    home context still cannot authorize it."""
    poisoned = CompatibleExerciseChoices(
        context=HOME, catalog_version=1,
        choices=compatible_exercise_choices(GYM).choices)
    week = _provider_week(exercises=[_provider_exercise("ex_barbell_back_squat")])
    validated, _ = validate_generated_plan(week, _prefs())

    with pytest.raises(GenerationExerciseIncompatibleError) as caught:
        canonicalize_generated_exercises(validated, poisoned)
    assert caught.value.resolution_category == "equipment"


# ── 8. Cardio placement ──────────────────────────────────────────────────────


def test_cardio_id_on_a_training_day_is_refused_by_placement():
    context = ExerciseContext(equipment_context="ev", cardio_type="yuruyus")
    week = _provider_week(exercises=[
        _provider_exercise("ex_push_up"), _provider_exercise("ex_brisk_walk")])
    validated, _ = validate_generated_plan(week, _prefs())

    with pytest.raises(GenerationExerciseIncompatibleError) as caught:
        canonicalize_generated_exercises(validated, compatible_exercise_choices(context))
    assert caught.value.resolution_category == "cardio_placement"


def test_cardio_id_on_a_cardio_day_is_accepted_and_named_by_the_catalog():
    context = ExerciseContext(equipment_context="ev", cardio_type="yuruyus")
    prefs = _prefs(ekipman="ev", kardiyo_tipi="yuruyus", kardiyo_gun=1)
    week = _provider_week(
        training_days=3, cardio_days=1,
        exercises=[_provider_exercise("ex_push_up")],
        cardio_exercises=[_provider_exercise("ex_brisk_walk", 1)])
    validated, _ = validate_generated_plan(week, prefs)

    canonical = canonicalize_generated_exercises(
        validated, compatible_exercise_choices(context))
    assert canonical["program"][3]["egzersizler"][0]["isim"] == "Brisk Walk"


def test_cardio_id_outside_the_declared_cardio_type_is_not_offered():
    context = ExerciseContext(equipment_context="ev", cardio_type="yok")
    week = _provider_week(
        training_days=3, cardio_days=1,
        exercises=[_provider_exercise("ex_push_up")],
        cardio_exercises=[_provider_exercise("ex_swimming", 1)])
    validated = validate_generated_plan(
        week, _prefs(ekipman="ev", kardiyo_tipi="kosu", kardiyo_gun=1))[0]

    with pytest.raises(GenerationExerciseIncompatibleError) as caught:
        canonicalize_generated_exercises(validated, compatible_exercise_choices(context))
    assert caught.value.resolution_category == "outside_choice_set"


# ── 9/10. Same-key replay and new-key recovery ───────────────────────────────


def test_incompatible_failure_replays_without_inference_and_new_key_recovers(
        client, mobile_user, as_mobile, monkeypatch, quota_on):
    calls = _native_provider(
        monkeypatch,
        _provider_week(exercises=[_provider_exercise("ex_lat_pulldown")]),
        _provider_week(exercises=[_provider_exercise("ex_push_up")]))
    body = {**CANONICAL, "ekipman": "ev"}
    failed_headers = as_mobile(mobile_user, "replay-key")

    first = client.post(POST_PATH, json=body, headers=failed_headers)
    replay = client.post(POST_PATH, json=body, headers=failed_headers)

    assert first.status_code == replay.status_code == 422
    assert replay.json["error"]["code"] == first.json["error"]["code"] == CODE_INCOMPATIBLE
    assert len(calls) == 1
    assert TrainingPlan.query.count() == 0

    fresh = client.post(
        POST_PATH, json=body, headers=as_mobile(mobile_user, "replay-key-2"))

    assert fresh.status_code == 201
    assert len(calls) == 2
    assert TrainingPlan.query.filter_by(user_id=mobile_user.id).count() == 1
    assert _stored_exercises(mobile_user.id)[0]["exercise_id"] == "ex_push_up"
    assert _quota_left(mobile_user.id) == 0


# ── 11. Browser safety ───────────────────────────────────────────────────────


@pytest.mark.parametrize("exercise_id,code", [
    ("ex_quantum_laser_row", CODE_IDENTITY_INVALID),
    ("Goblet Squat", CODE_IDENTITY_INVALID),
    ("ex_barbell_back_squat", CODE_INCOMPATIBLE),
])
def test_browser_identity_failure_keeps_the_existing_plan_untouched(
        client, auth_user, monkeypatch, exercise_id, code):
    _session(auth_user)
    existing = TrainingPlan(user_id=auth_user.id, plan_data=json.dumps({"keep": 1}))
    db.session.add(existing)
    db.session.commit()
    existing_id, existing_data = existing.id, existing.plan_data
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(_provider_week(
            exercises=[_provider_exercise(exercise_id)])))

    response = client.post(
        "/training-plan", json={"gun_sayisi": 3, "sure": 45, "ekipman": "ev"})

    assert response.status_code == 500  # browser contract unchanged (PR A)
    body = response.get_json()
    assert body["code"] == code
    assert "exercise_context_token" not in body and "program" not in body
    rows = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
    assert [(row.id, row.plan_data) for row in rows] == [(existing_id, existing_data)]


def test_browser_payload_carries_only_catalog_owned_identity(
        client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(_turkish_week(), ensure_ascii=False))

    body = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45}).get_json()

    catalog = load_exercise_catalog()
    for day in body["program"]:
        for exercise in day["egzersizler"]:
            assert exercise["isim"] == catalog.by_id[exercise["exercise_id"]].canonical_name
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


# ── 12. Provider last-good cache boundary ────────────────────────────────────


@pytest.fixture
def providers_down_with_cache(monkeypatch):
    """Real ``_heavy_complete``, both providers failing, a stubbed last-good
    store. Whatever the cache returns is what generation receives."""
    served = {"payload": None, "recalls": []}

    def openai_down(*args, **kwargs):
        raise RuntimeError("provider down")

    def recall(key):
        served["recalls"].append(key)
        return served["payload"]

    monkeypatch.setattr(ai_module, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_module, "_openai_chat", openai_down)
    monkeypatch.setattr(ai_module.ai_recovery, "recall_last_good", recall)
    monkeypatch.setattr(ai_module.ai_recovery, "remember_last_good", lambda *a: None)
    return served


def test_cached_new_schema_output_is_fully_revalidated_and_can_succeed(
        client, mobile_user, as_mobile, providers_down_with_cache):
    providers_down_with_cache["payload"] = json.dumps(_turkish_week())

    created = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "cache-valid"))

    assert created.status_code == 201, created.json
    assert len(providers_down_with_cache["recalls"]) == 1
    assert _stored_exercises(mobile_user.id)[0]["isim"] == "Barbell Back Squat"


def test_cached_legacy_name_only_output_cannot_become_authoritative(
        client, mobile_user, as_mobile, providers_down_with_cache, quota_on):
    providers_down_with_cache["payload"] = json.dumps(_week(exercises=[
        _exercise("Barbell Back Squat"), _exercise("Row"), _exercise("Push-up")]))

    response = client.post(
        POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, "cache-legacy"))

    assert response.status_code == 422
    assert response.json["error"]["code"] == CODE_SCHEMA_INVALID
    # Primary and repair both fell back to the cache; neither bypassed validation.
    assert len(providers_down_with_cache["recalls"]) == 2
    _assert_durable_terminal_failure(mobile_user.id, CODE_SCHEMA_INVALID)
    assert _quota_left(mobile_user.id) == 1


@pytest.mark.parametrize("exercise_id,code", [
    ("ex_invented_cached_lift", CODE_IDENTITY_INVALID),
    ("ex_barbell_back_squat", CODE_INCOMPATIBLE),  # valid for gym, cached for ev
])
def test_cached_invalid_identity_is_refused_like_a_live_answer(
        client, mobile_user, as_mobile, providers_down_with_cache, exercise_id, code):
    providers_down_with_cache["payload"] = json.dumps(_provider_week(
        exercises=[_provider_exercise(exercise_id)]))

    response = client.post(
        POST_PATH, json={**CANONICAL, "ekipman": "ev"},
        headers=as_mobile(mobile_user, f"cache-{exercise_id}"))

    assert response.status_code == 422
    assert response.json["error"]["code"] == code
    _assert_durable_terminal_failure(mobile_user.id, code)


def test_cache_key_is_bound_to_the_id_based_prompt(monkeypatch):
    """The last-good key hashes the full prompt, which now carries the closed
    ID set — so a pre-PR B (name-vocabulary) prompt can never address the
    same entry, and two different choice sets never share one."""
    keys = []
    monkeypatch.setattr(ai_module.ai_recovery, "lastgood_key",
                        lambda *parts: keys.append(parts) or "k")
    monkeypatch.setattr(ai_module, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_module, "_openai_chat", lambda *a, **k: "{}")
    monkeypatch.setattr(ai_module.ai_recovery, "remember_last_good", lambda *a: None)

    def generate(ekipman):
        try:
            training_service.generate_training_plan_candidate(
                type("U", (), {"user_metadata": {}, "id": 1})(), None,
                _prefs(ekipman=ekipman), ai_module._heavy_complete)
        except SchemaInvalidError:
            pass

    monkeypatch.setattr(training_service, "build_features", lambda *a, **k: None)
    monkeypatch.setattr(training_service, "classify_user", lambda *a, **k: type(
        "C", (), {"level": "Beginner", "confidence": 1, "score": 1,
                  "risk_flags": [], "constraints_applied": []})())
    captured = []
    monkeypatch.setattr(training_service, "build_program_context", lambda *a, **k: None)
    monkeypatch.setattr(
        training_service, "build_training_prompt",
        lambda *a, exercise_choices, **k: captured.append(exercise_choices) or (
            "CHOICES:" + ",".join(c.exercise_id for c in exercise_choices.choices)))
    generate("ev")
    generate("spor_salonu")
    prompts = [parts[3][0]["content"] for parts in keys]
    assert prompts[0].startswith("CHOICES:ex_") and prompts[0] != prompts[2]
    assert set(prompts[0][len("CHOICES:"):].split(",")) == captured[0].exercise_ids


# ── 13. Prompt contract ──────────────────────────────────────────────────────


def _captured_prompt(client, auth_user, monkeypatch, **body):
    _session(auth_user)
    captured = {}

    cardio_days = body.get("kardiyo_gun", 0)

    def fake(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        captured["system"] = kwargs["system_prompt"]
        return json.dumps(_provider_week(
            cardio_days=cardio_days,
            exercises=[_provider_exercise("ex_push_up")],
            cardio_exercises=[_provider_exercise("ex_brisk_walk", 1)]))

    monkeypatch.setattr(training_bp, "_heavy_chat", fake)
    response = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45, **body})
    assert response.status_code == 200, response.get_json()
    return captured


def _choice_lines(prompt):
    block = prompt.split("EXERCISE CHOICES", 1)[1].split("\n\n", 1)[0]
    return [line for line in block.splitlines() if line.startswith("- ex_")]


@pytest.mark.parametrize("ekipman,cardio", [
    ("ev", "yok"), ("minimal", "yok"), ("spor_salonu", "yok"), ("ev", "karisik")])
def test_prompt_offers_exactly_the_closed_set_as_stable_id_name_pairs(
        client, auth_user, monkeypatch, ekipman, cardio):
    body = {"ekipman": ekipman, "kardiyo_tipi": cardio,
            "kardiyo_gun": 1 if cardio != "yok" else 0}
    prompt = _captured_prompt(client, auth_user, monkeypatch, **body)["prompt"]
    context = ExerciseContext(equipment_context=ekipman, cardio_type=cardio)
    expected = compatible_exercise_choices(context)

    lines = _choice_lines(prompt)
    assert lines == [f"- {c.exercise_id} | {c.canonical_name}" for c in expected.choices]
    assert all(ID_PATTERN.fullmatch(line[2:].split(" | ")[0]) for line in lines)
    # No ID outside the closed set appears ANYWHERE — the JSON example included.
    mentioned = set(re.findall(r"\bex_[a-z0-9_]+", prompt))
    assert mentioned <= expected.exercise_ids
    example = re.search(r'"exercise_id":"(ex_[a-z0-9_]+)"', prompt)
    assert example and example.group(1) in expected


def test_prompt_never_asks_the_provider_for_a_name_or_to_translate_ids(
        client, auth_user, monkeypatch):
    captured = _captured_prompt(client, auth_user, monkeypatch)
    prompt, system = captured["prompt"], captured["system"]
    assert '"isim"' not in prompt.split("JSON FORMAT", 1)[1]
    assert "exercise_id" in prompt.split("JSON FORMAT", 1)[1]
    # The language directive (system + user) explicitly carves IDs out.
    assert "exercise_id" in system and "çevirme" in system
    lang_rule = next(line for line in prompt.splitlines() if "İçerik dili:" in line)
    assert "exercise_id" in lang_rule and "çevrilmez" in lang_rule


def test_one_choice_set_feeds_both_the_prompt_and_validation(monkeypatch):
    """The set shown to the provider and the set enforced are one object."""
    seen = {}
    real_choices = training_service.compatible_exercise_choices
    real_canonicalize = training_service.canonicalize_generated_exercises

    def spy_choices(context):
        seen.setdefault("built", []).append(real_choices(context))
        return seen["built"][-1]

    def spy_build(*args, exercise_choices, **kwargs):
        seen["prompt"] = exercise_choices
        return "base prompt"

    def spy_canonicalize(plan, choices):
        seen["validated"] = choices
        return real_canonicalize(plan, choices)

    monkeypatch.setattr(training_service, "compatible_exercise_choices", spy_choices)
    monkeypatch.setattr(training_service, "build_training_prompt", spy_build)
    monkeypatch.setattr(
        training_service, "canonicalize_generated_exercises", spy_canonicalize)
    monkeypatch.setattr(training_service, "build_features", lambda *a, **k: None)
    monkeypatch.setattr(training_service, "build_program_context", lambda *a, **k: None)
    monkeypatch.setattr(training_service, "classify_user", lambda *a, **k: type(
        "C", (), {"level": "Beginner", "confidence": 1, "score": 1,
                  "risk_flags": [], "constraints_applied": []})())

    training_service.generate_training_plan_candidate(
        type("U", (), {"user_metadata": {}, "id": 1})(), None, _prefs(),
        lambda **kwargs: json.dumps(_provider_week()))

    assert len(seen["built"]) == 1
    assert seen["prompt"] is seen["validated"] is seen["built"][0]


# ── Observability ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("exercise_id,body,category", [
    ("ex_barbell_back_squat", {"ekipman": "ev"}, "outside_choice_set"),
    ("Kadeh Squat", {}, "malformed_id"),
])
def test_identity_diagnostic_is_bounded_and_never_logs_the_provider_value(
        client, mobile_user, as_mobile, monkeypatch, caplog, exercise_id, body, category):
    _native_provider(monkeypatch, _provider_week(
        exercises=[_provider_exercise(exercise_id)]))

    with caplog.at_level(logging.INFO, logger="app"):
        response = client.post(
            POST_PATH, json={**CANONICAL, **body},
            headers=as_mobile(mobile_user, f"diag-{category}"))

    assert response.status_code == 422
    events = [r.getMessage() for r in caplog.records
              if "[TRAINING] exercise_resolution_failed " in r.getMessage()]
    assert len(events) == 1
    fields = dict(part.split("=", 1) for part in events[0].split()[2:])
    assert set(fields) == {
        "code", "category", "request_id", "catalog_version",
        "equipment", "cardio_type", "completion_count"}
    assert fields["category"] == category
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert exercise_id not in logged
