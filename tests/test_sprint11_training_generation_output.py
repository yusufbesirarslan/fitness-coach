"""Sprint 11 PR3 — training generation output reliability.

Hermetic: no live Bedrock/OpenAI. Provider invocation is always a spy.
"""
from pathlib import Path
from types import SimpleNamespace

import json
import pytest

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import TrainingPlan, UserSession
from app.services.exercise_catalog import ExerciseAmbiguous, ExerciseContext
from app.services.training_generation.exercise_resolution import (
    canonicalize_plan_exercises,
)
from app.services.training_generation import exercise_resolution
from app.services.training_generation.extractor import (
    extract_plan_object,
    json_structure_incomplete,
)
from app.services.training_generation.models import TrainingPreferences
from app.services.training_generation.output_errors import (
    GenerationExerciseAmbiguousError,
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseIncompatibleError,
    GenerationExerciseUnresolvedError,
    ParseFailedError,
    SaveInvalidError,
    SchemaInvalidError,
    SemanticInvalidError,
    TruncatedError,
)
from app.services.training_generation.plan_schema import (
    MAX_PROVIDER_COMPLETIONS,
    PRIMARY_MAX_TOKENS,
    REPAIR_MAX_TOKENS,
    WEEKDAYS,
)
from app.services.training_generation.preference_contract import (
    CODE_GENERATION_EXERCISE_AMBIGUOUS,
    CODE_GENERATION_EXERCISE_IDENTITY_INVALID,
    CODE_GENERATION_EXERCISE_INCOMPATIBLE,
    CODE_GENERATION_EXERCISE_UNRESOLVED,
    CODE_GENERATION_PARSE_FAILED,
    CODE_GENERATION_SEMANTICALLY_INVALID,
    CODE_GENERATION_TRUNCATED,
    CODE_SAVE_INVALID,
    CODE_UNSUPPORTED,
)
from app.services.training_generation.response_validator import validate_generated_plan
from app.services.training_generation.service import (
    generate_training_plan_payload,
    validate_plan_for_save,
)
from app.services.training_generation import service as training_service
from app.services.ai import ChatCompletion


def _exercise(name="Goblet Squat", sets=3):
    return {
        "isim": name,
        "set": sets,
        "tekrar": "8-12",
        "dinlenme": "90 sn",
        "not": "kontrollü",
    }


def _week(training_days=3, cardio_days=0, exercises=None, duration=45, cardio_duration=20):
    exercises = exercises or [_exercise(), _exercise("Row"), _exercise("Push-up")]
    program = []
    training_left = training_days
    cardio_left = cardio_days
    for day in WEEKDAYS:
        if training_left:
            program.append({
                "gun": day, "tip": "antrenman", "odak": "Full Body",
                "sure_dk": duration, "tahmini_kalori": 300,
                "egzersizler": [dict(item) for item in exercises],
            })
            training_left -= 1
        elif cardio_left:
            program.append({
                "gun": day, "tip": "kardiyo", "odak": "Kondisyon",
                "sure_dk": cardio_duration, "tahmini_kalori": 200,
                "egzersizler": [_exercise("Easy Run", 1)],
            })
            cardio_left -= 1
        else:
            program.append({
                "gun": day, "tip": "dinlenme", "odak": "Aktif Toparlanma",
                "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": [],
            })
    return {
        "program": program,
        "haftalik_ozet": {
            "yogunluk_skoru": 7, "denge_skoru": 7, "uygunluk_skoru": 7,
        },
    }


def _prefs(**overrides):
    data = dict(
        gun_sayisi=3, sure=45, antrenman_tarzi="genel", ekipman="spor_salonu",
        odak="tum_vucut", odak_hedef="genel", kardiyo_tipi="yok", kardiyo_gun=0,
        kardiyo_sure=20, kardiyo_yogunluk="orta", injuries="",
    )
    data.update(overrides)
    return TrainingPreferences(**data)


def _session(user):
    db.session.add(UserSession(
        user_id=user.id, goal="kas kazanma", fitness_level="intermediate",
        current_activity="active", tdee=2600,
    ))
    db.session.commit()


def _stub_pipeline(monkeypatch, preferences=None):
    preferences = preferences or _prefs()
    classification = SimpleNamespace(
        level="Beginner", confidence=0.8, score=0.5,
        risk_flags=[], constraints_applied=[],
    )
    monkeypatch.setattr(training_service, "persist_posted_injuries", lambda *a, **k: None)
    monkeypatch.setattr(training_service, "parse_preferences", lambda *a, **k: preferences)
    monkeypatch.setattr(training_service, "require_supported", lambda prefs: prefs)
    monkeypatch.setattr(training_service, "build_features", lambda *a, **k: object())
    monkeypatch.setattr(training_service, "classify_user", lambda *a, **k: classification)
    monkeypatch.setattr(training_service, "build_program_context", lambda *a, **k: object())
    monkeypatch.setattr(training_service, "build_training_prompt", lambda *a, **k: "base prompt")
    monkeypatch.setattr(training_service, "build_system_prompt", lambda *a, **k: "system prompt")
    return preferences


def _generate(monkeypatch, chat_fn, preferences=None, logger=None):
    _stub_pipeline(monkeypatch, preferences)
    return generate_training_plan_payload(
        SimpleNamespace(user_metadata={}), None, {}, chat_fn=chat_fn, logger=logger,
    )


# ── Parser ───────────────────────────────────────────────────────────────────


def test_extracts_plain_json_object():
    assert extract_plan_object('{"program": []}') == {"program": []}


def test_extracts_single_code_fence():
    raw = "```json\n{\"program\": []}\n```"
    assert extract_plan_object(raw) == {"program": []}


def test_rejects_malformed_json():
    with pytest.raises(ParseFailedError):
        extract_plan_object("{")


def test_rejects_incomplete_json_without_calling_it_truncation():
    with pytest.raises(ParseFailedError) as exc:
        extract_plan_object('{"program": [', truncated=False)
    assert not isinstance(exc.value, TruncatedError)


def test_truncated_flag_with_incomplete_json_is_truncation():
    with pytest.raises(TruncatedError):
        extract_plan_object('{"program": [', truncated=True)


def test_rejects_multiple_json_objects():
    with pytest.raises(ParseFailedError, match="multiple"):
        extract_plan_object('{"a": 1}{"b": 2}')


def test_rejects_prose_around_json():
    with pytest.raises(ParseFailedError):
        extract_plan_object('Here you go: {"program": []} thanks')


def test_rejects_wrong_top_level_type():
    with pytest.raises(ParseFailedError, match="object"):
        extract_plan_object("[1, 2, 3]")


def test_incomplete_structure_helper_detects_open_brace():
    assert json_structure_incomplete('{"program": [') is True
    assert json_structure_incomplete('{"program": []}') is False


# ── Structural validator ─────────────────────────────────────────────────────


def test_valid_plan_passes_structure_and_semantics():
    plan, warnings = validate_generated_plan(_week(), _prefs())
    assert len(plan["program"]) == 7
    assert warnings == []
    assert plan["haftalik_ozet"]["toplam_antrenman_gun"] == 3


def test_missing_required_exercise_fields_are_schema_invalid():
    plan = _week()
    plan["program"][0]["egzersizler"][0] = {"isim": "Squat"}
    with pytest.raises(SchemaInvalidError):
        validate_generated_plan(plan, _prefs())


def test_unknown_day_key_is_schema_invalid():
    plan = _week()
    plan["program"][0]["secret"] = True
    with pytest.raises(SchemaInvalidError, match="unknown"):
        validate_generated_plan(plan, _prefs())


def test_wrong_nested_type_is_schema_invalid():
    plan = _week()
    plan["program"][0]["egzersizler"] = {"isim": "Squat"}
    with pytest.raises(SchemaInvalidError):
        validate_generated_plan(plan, _prefs())


def test_oversized_name_is_schema_invalid():
    plan = _week()
    plan["program"][0]["egzersizler"][0]["isim"] = "x" * 121
    with pytest.raises(SchemaInvalidError):
        validate_generated_plan(plan, _prefs())


def test_invalid_weekday_is_schema_invalid():
    plan = _week()
    plan["program"][0]["gun"] = "Monday"
    with pytest.raises(SchemaInvalidError, match="gun"):
        validate_generated_plan(plan, _prefs())


def test_float_sets_are_schema_invalid():
    plan = _week()
    plan["program"][0]["egzersizler"][0]["set"] = 3.0
    with pytest.raises(SchemaInvalidError, match="integer"):
        validate_generated_plan(plan, _prefs())


def test_messy_numeric_strings_are_schema_invalid():
    plan = _week()
    plan["program"][0]["sure_dk"] = "45 dk"
    with pytest.raises(SchemaInvalidError):
        validate_generated_plan(plan, _prefs())


# ── Semantic validator ───────────────────────────────────────────────────────


def test_wrong_training_day_count_is_semantic():
    with pytest.raises(SemanticInvalidError, match="antrenman günü"):
        validate_generated_plan(_week(training_days=1), _prefs(gun_sayisi=3))


def test_empty_training_day_is_schema_not_semantic():
    plan = _week()
    plan["program"][0]["egzersizler"] = []
    with pytest.raises(SchemaInvalidError, match="en az bir egzersiz"):
        validate_generated_plan(plan, _prefs())


def test_rest_day_with_exercises_is_schema_invalid():
    plan = _week()
    plan["program"][3]["egzersizler"] = [_exercise()]
    with pytest.raises(SchemaInvalidError, match="dinlenme"):
        validate_generated_plan(plan, _prefs())


def test_cardio_day_count_must_match_request():
    prefs = _prefs(kardiyo_tipi="kosu", kardiyo_gun=2, gun_sayisi=3)
    with pytest.raises(SemanticInvalidError, match="kardiyo"):
        validate_generated_plan(_week(training_days=3, cardio_days=0), prefs)
    plan, _ = validate_generated_plan(_week(training_days=3, cardio_days=2), prefs)
    assert sum(1 for day in plan["program"] if day["tip"] == "kardiyo") == 2


def test_bodybuilding_requires_hypertrophy_sized_sessions():
    prefs = _prefs(antrenman_tarzi="bodybuilding")
    small = _week(exercises=[_exercise()])
    with pytest.raises(SemanticInvalidError, match="bodybuilding"):
        validate_generated_plan(small, prefs)
    bulky = _week(exercises=[
        _exercise("Bench Press"), _exercise("Row"),
        _exercise("Squat"), _exercise("Curl"),
    ])
    validate_generated_plan(bulky, prefs)


def test_powerlifting_requires_multi_lift_sessions():
    prefs = _prefs(antrenman_tarzi="powerlifting")
    with pytest.raises(SemanticInvalidError, match="powerlifting"):
        validate_generated_plan(_week(exercises=[_exercise()]), prefs)
    validate_generated_plan(_week(exercises=[
        _exercise("Back Squat"), _exercise("Bench Press"), _exercise("Deadlift"),
    ]), prefs)


def test_invented_exercise_names_still_pass_without_catalog():
    plan = _week(exercises=[
        _exercise("Quantum Trap Bar Snatch"),
        _exercise("Invented Laser Row"),
        _exercise("Photon Curl"),
    ])
    validated, warnings = validate_generated_plan(plan, _prefs())
    assert validated["program"][0]["egzersizler"][0]["isim"] == "Quantum Trap Bar Snatch"
    assert warnings == []


def test_focus_mismatch_is_not_invented_without_catalog():
    plan = _week()
    plan["program"][0]["odak"] = "Göğüs"
    validate_generated_plan(plan, _prefs(odak="alt_vucut"))


# ── Repair budget ────────────────────────────────────────────────────────────


def test_valid_first_response_is_one_call(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_week())

    _generate(monkeypatch, fake)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == PRIMARY_MAX_TOKENS


def test_parse_failure_gets_one_repair(monkeypatch):
    calls = []
    responses = iter(("{", json.dumps(_week())))

    def fake(**kwargs):
        calls.append(kwargs)
        return next(responses)

    _generate(monkeypatch, fake)
    assert len(calls) == 2
    assert "REPAIR:" in calls[1]["messages"][0]["content"]
    assert "kısa tut" not in calls[1]["messages"][0]["content"]


def test_truncation_metadata_uses_repair_token_budget(monkeypatch):
    calls = []
    responses = iter((
        ChatCompletion(text='{"program": [', truncated=True, finish_reason="max_tokens"),
        json.dumps(_week()),
    ))

    def fake(**kwargs):
        calls.append(kwargs)
        return next(responses)

    _generate(monkeypatch, fake)
    assert [call["max_tokens"] for call in calls] == [
        PRIMARY_MAX_TOKENS, REPAIR_MAX_TOKENS,
    ]


def test_failed_repair_does_not_repair_again(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return "not json"

    with pytest.raises(ParseFailedError):
        _generate(monkeypatch, fake)
    assert len(calls) == 2


def test_semantic_failure_does_not_enter_repair(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_week(training_days=1))

    with pytest.raises(SemanticInvalidError):
        _generate(monkeypatch, fake, preferences=_prefs(gun_sayisi=4))
    assert len(calls) == 1


def test_truncated_closed_json_still_gets_one_repair(monkeypatch):
    calls = []
    short = _week(training_days=1)
    responses = iter((
        ChatCompletion(
            text=json.dumps(short), truncated=True, finish_reason="max_tokens"),
        json.dumps(_week(training_days=4)),
    ))

    def fake(**kwargs):
        calls.append(kwargs)
        return next(responses)

    _generate(monkeypatch, fake, preferences=_prefs(gun_sayisi=4))
    assert [call["max_tokens"] for call in calls] == [
        PRIMARY_MAX_TOKENS, REPAIR_MAX_TOKENS,
    ]


def test_schema_failure_does_not_enter_repair(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps({})

    with pytest.raises(SchemaInvalidError):
        _generate(monkeypatch, fake)
    assert len(calls) == 1


def test_contract_rejection_never_enters_repair(monkeypatch):
    calls = []
    monkeypatch.setattr(training_service, "persist_posted_injuries", lambda *a, **k: None)

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_week())

    from app.services.training_generation.preference_contract import PreferenceContractError
    with pytest.raises(PreferenceContractError):
        generate_training_plan_payload(
            SimpleNamespace(user_metadata={}, id=1),
            SimpleNamespace(),
            {"gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "crossfit"},
            chat_fn=fake,
        )
    assert calls == []


def test_provider_unavailable_does_not_masquerade_as_parse_repair(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("AI servisi hatası")

    from app.services.training_generation.output_errors import GenerationUnavailableError
    with pytest.raises(GenerationUnavailableError):
        _generate(monkeypatch, fake)
    assert len(calls) == 1


def test_completion_budget_never_exceeds_two(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return "{"

    with pytest.raises(ParseFailedError):
        _generate(monkeypatch, fake)
    assert len(calls) <= MAX_PROVIDER_COMPLETIONS


# ── Exercise canonicalization (Sprint 11 PR4 Task 3) ────────────────────────


def test_pr3_valid_aliases_become_canonical_ids():
    plan = _week(exercises=[_exercise("Back Squat")])
    validated, _ = validate_generated_plan(plan, _prefs())

    canonical = canonicalize_plan_exercises(
        validated, ExerciseContext(equipment_context="spor_salonu"))

    ex = canonical["program"][0]["egzersizler"][0]
    assert ex["exercise_id"] == "ex_barbell_back_squat"
    assert ex["isim"] == "Barbell Back Squat"


def test_duplicate_exercise_references_resolve_to_the_same_stable_id():
    """Different aliases of the same lift must converge on one exercise_id."""
    plan = _week(exercises=[
        _exercise("Squat"), _exercise("Back Squat"), _exercise("Barbell Squat"),
    ])
    validated, _ = validate_generated_plan(plan, _prefs())

    canonical = canonicalize_plan_exercises(
        validated, ExerciseContext(equipment_context="spor_salonu"))

    ids = {
        ex["exercise_id"]
        for day in canonical["program"] if day["tip"] == "antrenman"
        for ex in day["egzersizler"]
    }
    assert ids == {"ex_barbell_back_squat"}


def test_canonicalization_dedupes_repeated_lookups_and_loads_catalog_once(monkeypatch):
    """Perf property: N occurrences of the same name cost one resolve call."""
    plan = _week(exercises=[
        _exercise("Back Squat"), _exercise("Back Squat"), _exercise("Row"),
    ])
    validated, _ = validate_generated_plan(plan, _prefs())

    catalog_loads = []
    real_load = exercise_resolution.load_exercise_catalog

    def counting_load():
        catalog_loads.append(1)
        return real_load()

    resolve_calls = []
    real_resolve = exercise_resolution.resolve_exercise

    def counting_resolve(*, name, catalog=None):
        resolve_calls.append(name)
        return real_resolve(name=name, catalog=catalog)

    monkeypatch.setattr(exercise_resolution, "load_exercise_catalog", counting_load)
    monkeypatch.setattr(exercise_resolution, "resolve_exercise", counting_resolve)

    canonical = canonicalize_plan_exercises(
        validated, ExerciseContext(equipment_context="spor_salonu"))

    # 3 training days * 3 exercises = 9 occurrences, only 2 distinct names.
    assert len(catalog_loads) == 1
    assert sorted(resolve_calls) == sorted(["Back Squat", "Row"])
    ids = {
        ex["exercise_id"]
        for day in canonical["program"] if day["tip"] == "antrenman"
        for ex in day["egzersizler"]
    }
    assert ids == {"ex_barbell_back_squat", "ex_barbell_row"}


def test_canonicalization_preserves_prescription_fields_and_adds_only_identity():
    plan = _week(exercises=[_exercise("Back Squat", sets=5)])
    validated, _ = validate_generated_plan(plan, _prefs())

    canonical = canonicalize_plan_exercises(
        validated, ExerciseContext(equipment_context="spor_salonu"))

    ex = canonical["program"][0]["egzersizler"][0]
    assert ex["set"] == 5
    assert ex["tekrar"] == "8-12"
    assert ex["dinlenme"] == "90 sn"
    assert ex["not"] == "kontrollü"
    assert set(ex) == {"isim", "set", "tekrar", "dinlenme", "not", "exercise_id"}


def test_unresolved_provider_name_is_typed_and_not_repaired(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_week(exercises=[_exercise("Invented Laser Row")]))

    with pytest.raises(GenerationExerciseUnresolvedError):
        _generate(monkeypatch, fake)
    assert len(calls) == 1


def test_ambiguous_generated_exercise_is_typed(monkeypatch):
    def fake_resolve(*, name, catalog=None):
        raise ExerciseAmbiguous("ambiguous")

    monkeypatch.setattr(exercise_resolution, "resolve_exercise", fake_resolve)
    plan, _ = validate_generated_plan(_week(), _prefs())

    with pytest.raises(GenerationExerciseAmbiguousError):
        canonicalize_plan_exercises(
            plan, ExerciseContext(equipment_context="spor_salonu"))


def test_id_looking_generated_name_is_identity_invalid():
    plan, _ = validate_generated_plan(
        _week(exercises=[_exercise("ex_fake_exercise")]), _prefs())

    with pytest.raises(GenerationExerciseIdentityInvalidError):
        canonicalize_plan_exercises(
            plan, ExerciseContext(equipment_context="spor_salonu"))


def test_equipment_incompatible_generated_exercise_is_typed():
    """An 'ev' (home/bodyweight-only) plan containing a barbell squat fails closed."""
    plan, _ = validate_generated_plan(
        _week(exercises=[_exercise("Back Squat")]), _prefs())

    with pytest.raises(GenerationExerciseIncompatibleError):
        canonicalize_plan_exercises(plan, ExerciseContext(equipment_context="ev"))


def test_generation_pipeline_rejects_incompatible_equipment(monkeypatch):
    """Proves the real request-derived ExerciseContext (not just the unit-level
    context) is what canonicalization checks against."""
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_week(exercises=[_exercise("Back Squat")]))

    with pytest.raises(GenerationExerciseIncompatibleError):
        _generate(monkeypatch, fake, preferences=_prefs(ekipman="ev"))
    assert len(calls) == 1


def test_http_typed_exercise_unresolved(client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(_week(exercises=[_exercise("Invented Laser Row")])),
    )

    response = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})

    assert response.status_code == 500
    body = response.get_json()
    assert body["code"] == CODE_GENERATION_EXERCISE_UNRESOLVED
    assert body["retryable"] is True
    assert "Invented Laser Row" not in body["error"]


# ── Architecture guards: canonicalization boundary ───────────────────────────


def test_canonicalization_runs_after_the_full_try_except_not_inside_repair():
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    entry_repair_catch = "except (ParseFailedError, TruncatedError) as exc:"
    ozet_line = 'ozet = plan.get("haftalik_ozet", {})'
    canonicalize_call = "plan = canonicalize_plan_exercises(plan, exercise_context)"

    assert entry_repair_catch in source
    assert canonicalize_call in source
    assert ozet_line in source
    assert source.index(entry_repair_catch) < source.index(canonicalize_call)
    assert source.index(canonicalize_call) < source.index(ozet_line)

    # The repair boundary's own try/except body must not gain exercise-error
    # handling — canonicalization must never be reachable from inside it.
    repair_block = source.split(entry_repair_catch)[1].split(
        "except SchemaInvalidError:")[0]
    assert "canonicalize_plan_exercises" not in repair_block
    assert "GenerationExercise" not in repair_block


def test_repair_catches_remain_exactly_parse_and_truncation_errors():
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    assert source.count(
        "except (ParseFailedError, TruncatedError) as exc:") == 1
    assert (
        "except (ParseFailedError, TruncatedError, SchemaInvalidError, "
        "SemanticInvalidError):"
    ) in source


def test_provider_call_budget_stays_two():
    assert MAX_PROVIDER_COMPLETIONS == 2


def test_generated_exercise_id_key_is_not_yet_accepted_by_save(client, auth_user, monkeypatch):
    """Known Task 3->Task 4 gap: generate now emits exercise_id, but save-time
    structural validation does not accept it yet (Task 4 widens EXERCISE_KEYS
    using plan_schema.EXERCISE_ID_KEY). This documents the gap rather than
    hiding it; Task 4 closes it."""
    _session(auth_user)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: json.dumps(_week()))

    generated = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert generated.status_code == 200
    body = generated.get_json()
    assert body["program"][0]["egzersizler"][0]["exercise_id"].startswith("ex_")

    saved = client.post("/training-plan/save", json={
        "plan": body["program"], "score": body["overall_score"],
    })
    assert saved.status_code == 422
    assert saved.get_json()["code"] == CODE_SAVE_INVALID


# ── HTTP typed output errors ─────────────────────────────────────────────────


def test_malformed_output_is_typed_parse_failed(client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: "sorry I cannot")
    response = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert response.status_code == 500
    body = response.get_json()
    assert body["code"] == CODE_GENERATION_PARSE_FAILED
    assert body["retryable"] is True
    assert "sorry" not in body["error"].lower()


def test_day_count_mismatch_does_not_retry(client, auth_user, monkeypatch):
    _session(auth_user)
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_week(training_days=1))

    monkeypatch.setattr(training_bp, "_heavy_chat", fake)
    response = client.post("/training-plan", json={"gun_sayisi": 4, "sure": 45})
    assert response.status_code == 500
    assert response.get_json()["code"] == CODE_GENERATION_SEMANTICALLY_INVALID
    assert len(calls) == 1


def test_truncated_output_is_typed_after_failed_repair(client, auth_user, monkeypatch):
    _session(auth_user)

    def fake(**kwargs):
        return ChatCompletion(text='{"program": [', truncated=True, finish_reason="length")

    monkeypatch.setattr(training_bp, "_heavy_chat", fake)
    response = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert response.status_code == 500
    assert response.get_json()["code"] == CODE_GENERATION_TRUNCATED


# ── Save-time re-validation ──────────────────────────────────────────────────


def test_generated_valid_plan_saves(client, auth_user):
    response = client.post("/training-plan/save", json={
        "plan": _week()["program"], "score": 7.0,
    })
    assert response.status_code == 200
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 1


def test_save_persists_canonicalized_plan_in_the_original_list_shape(client, auth_user):
    seeded = client.post("/training-plan/save", json={
        "plan": _week()["program"], "score": 6.0,
    })
    assert seeded.status_code == 200

    submitted = _week()["program"]
    submitted[0]["gun"] = " Pazartesi "
    replaced = client.post("/training-plan/save", json={
        "plan": submitted, "score": 7.0,
    })

    assert replaced.status_code == 200
    row = TrainingPlan.query.filter_by(user_id=auth_user.id).one()
    stored = json.loads(row.plan_data)
    assert isinstance(stored, list)
    assert stored[0]["gun"] == "Pazartesi"


def test_client_mutated_invalid_plan_does_not_save(client, auth_user):
    client.post("/training-plan/save", json={"plan": _week()["program"], "score": 7.0})
    original = TrainingPlan.query.filter_by(user_id=auth_user.id).one().plan_data
    mutated = _week()["program"]
    mutated[0]["egzersizler"] = []
    rejected = client.post("/training-plan/save", json={"plan": mutated, "score": 1})
    assert rejected.status_code == 422
    assert rejected.get_json()["code"] == CODE_SAVE_INVALID
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).one().plan_data == original


def test_malformed_handcrafted_plan_does_not_save(client, auth_user):
    response = client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 7.0})
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_wrong_training_day_count_does_not_save(client, auth_user):
    response = client.post("/training-plan/save", json={
        "plan": _week(training_days=1)["program"], "score": 7.0,
    })
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_seven_training_days_does_not_save(client, auth_user):
    response = client.post("/training-plan/save", json={
        "plan": _week(training_days=7)["program"], "score": 7.0,
    })
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_wrong_weekday_count_does_not_save(client, auth_user):
    plan = _week()["program"][:3]
    response = client.post("/training-plan/save", json={"plan": plan, "score": 7.0})
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_save_rejection_happens_before_delete(client, auth_user):
    first = client.post("/training-plan/save", json={
        "plan": _week(exercises=[_exercise("Keep Me")])["program"], "score": 7.0,
    })
    assert first.status_code == 200
    rejected = client.post("/training-plan/save", json={"plan": {"v": 9}, "score": 1})
    assert rejected.status_code == 422
    stored = json.loads(TrainingPlan.query.filter_by(user_id=auth_user.id).one().plan_data)
    assert stored[0]["egzersizler"][0]["isim"] == "Keep Me"


def test_validate_plan_for_save_rejects_schema_errors():
    with pytest.raises(SaveInvalidError):
        validate_plan_for_save({"v": 1})


# ── Full mocked path ─────────────────────────────────────────────────────────


def test_generate_then_save_mocked_path(client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat", lambda **kwargs: json.dumps(_week()))
    generated = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert generated.status_code == 200
    body = generated.get_json()
    assert body["program"][0]["egzersizler"][0]["exercise_id"].startswith("ex_")

    # Task 3 canonicalizes generate-time output with exercise_id; save-time
    # structural validation is widened to accept it in Task 4 (known gap,
    # see test_generated_exercise_id_key_is_not_yet_accepted_by_save). A
    # client saving today still submits the name-only shape.
    name_only_program = [
        {
            **day,
            "egzersizler": [
                {k: v for k, v in ex.items() if k != "exercise_id"}
                for ex in day["egzersizler"]
            ],
        }
        for day in body["program"]
    ]
    saved = client.post("/training-plan/save", json={
        "plan": name_only_program, "score": body["overall_score"],
    })
    assert saved.status_code == 200
    row = TrainingPlan.query.filter_by(user_id=auth_user.id).one()
    stored = json.loads(row.plan_data)
    assert len(stored) == 7


# ── Architecture guards ──────────────────────────────────────────────────────


def test_all_save_paths_invoke_canonical_validation():
    source = Path(training_bp.__file__).read_text(encoding="utf-8")
    assert "validate_plan_for_save" in source
    assert source.index("validate_plan_for_save") < source.index(
        "TrainingPlan.query.filter_by(user_id=current_user.id).delete()")


def test_repair_suffix_is_not_write_less():
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    assert "Yanıtı kısa tut" not in source
    assert "REPAIR:" in source


def test_semantic_failure_cannot_loop_into_repair_in_source():
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    assert "except SemanticInvalidError:" in source
    semantic_block = source.split("except SemanticInvalidError:")[1].split("except")[0]
    assert "repair" not in semantic_block.lower() or "repair_eligible=0" in semantic_block


def test_generate_does_not_journal_adaptive_mutations():
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    assert "PlanMutationRecord" not in source
    assert "undo_last_change" not in source


def test_no_exercise_catalog_authority_introduced():
    gen_root = Path(training_service.__file__).resolve().parent
    for path in gen_root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "CREATE TABLE exercise" not in text
        assert "alias_table" not in text
        assert "fuzzy" not in text.lower()


def test_pr2_contract_still_runs_before_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(training_service, "persist_posted_injuries", lambda *a, **k: None)
    from app.services.training_generation.preference_contract import PreferenceContractError
    with pytest.raises(PreferenceContractError) as exc:
        generate_training_plan_payload(
            SimpleNamespace(user_metadata={}, id=1), SimpleNamespace(),
            {"gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "crossfit"},
            chat_fn=lambda **kwargs: calls.append(kwargs) or "{}",
        )
    assert exc.value.public_code == CODE_UNSUPPORTED
    assert calls == []


def test_representative_plan_fits_primary_token_budget():
    bulky = _week(
        training_days=6,
        exercises=[_exercise(f"Lift {n}") for n in range(8)],
    )
    encoded = json.dumps(bulky, ensure_ascii=False)
    # 4 chars/token is a conservative overestimate of serialized JSON.
    assert len(encoded) / 4 < PRIMARY_MAX_TOKENS
