"""Sprint 11 PR3 — training generation output reliability.

Hermetic: no live Bedrock/OpenAI. Provider invocation is always a spy.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import json
import pytest
from sqlalchemy import event

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import TrainingPlan, UserSession
from app.services import exercise_catalog
from app.services.exercise_catalog import (
    ExerciseAmbiguous,
    ExerciseContext,
    load_exercise_catalog,
)
from app.services.training_generation.exercise_context_token import (
    sign_exercise_context,
)
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
    SaveContextInvalidError,
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
    CODE_SAVE_CONTEXT_INVALID,
    CODE_SAVE_EXERCISE_INVALID,
    CODE_SAVE_INVALID,
    CODE_UNSUPPORTED,
)
from app.services.training_generation.response_validator import validate_generated_plan
from app.services.training_generation.service import (
    generate_training_plan_payload,
    resolve_save_exercise_context,
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


def _week(training_days=3, cardio_days=0, exercises=None, duration=45,
          cardio_duration=20, cardio_exercises=None):
    exercises = exercises or [_exercise(), _exercise("Row"), _exercise("Push-up")]
    cardio_exercises = cardio_exercises or [_exercise("Easy Run", 1)]
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
                "egzersizler": [dict(item) for item in cardio_exercises],
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


@pytest.fixture
def save_token(app, auth_user):
    """Mint the signed exercise context the save boundary demands.

    Tests must go through the real signer with the real app secret: a
    hand-rolled token here would test a fixture, not the boundary.
    """
    def _make(equipment="spor_salonu", cardio_type="yok", style="genel",
              user_id=None):
        return sign_exercise_context(
            ExerciseContext(equipment_context=equipment, cardio_type=cardio_type,
                            style=style),
            app.config["SECRET_KEY"],
            auth_user.id if user_id is None else user_id,
        )
    return _make


def _post_save(client, plan, token, score=7.0):
    return client.post("/training-plan/save", json={
        "plan": plan, "score": score, "exercise_context_token": token,
    })


def _stored_document(user_id):
    return json.loads(
        TrainingPlan.query.filter_by(user_id=user_id).one().plan_data)


@contextmanager
def delete_spy():
    """Record the DELETEs the database really executes against training_plan.

    Spying on the statement rather than on a patched query object keeps this
    honest: it observes the destructive act itself, so no refactor of the
    route can make the ordering guarantee silently untested.
    """
    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        text = statement.lstrip().upper()
        if text.startswith("DELETE") and "TRAINING_PLAN" in text:
            statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


@pytest.fixture
def fixture_catalog(tmp_path, monkeypatch):
    """Swap in a tiny code-owned catalog that actually has a retired entry.

    Nothing has been retired from the shipped catalog yet, so "inactive" is
    otherwise untestable at the save boundary — and it is precisely the case
    that must fail closed the first time a product decision retires a lift.
    """
    def _entry(exercise_id, name, active=True):
        return {
            "exercise_id": exercise_id, "canonical_name": name, "aliases": [],
            "equipment": ["bodyweight"], "movement": "squat",
            "primary_region": "lower_body", "active": active,
        }

    path = tmp_path / "exercises.json"
    path.write_text(json.dumps({"version": 1, "exercises": [
        _entry("ex_fixture_active", "Fixture Active"),
        _entry("ex_fixture_retired", "Fixture Retired", active=False),
    ]}), encoding="utf-8")
    monkeypatch.setattr(exercise_catalog, "CATALOG_PATH", path)
    load_exercise_catalog.cache_clear()
    yield
    load_exercise_catalog.cache_clear()


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


def test_schema_failure_enters_exactly_one_bounded_repair(monkeypatch):
    """Retargeted: a missed SHAPE is a provider formatting failure and now gets
    the same single repair turn parse/truncation always had (the plainest
    supported request was otherwise unusable whenever the provider slipped).
    Still bounded by MAX_PROVIDER_COMPLETIONS, still raises if it does not
    validate, and the full canonical validation re-runs after the repair."""
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps({})

    with pytest.raises(SchemaInvalidError):
        _generate(monkeypatch, fake)
    assert len(calls) == MAX_PROVIDER_COMPLETIONS == 2


def test_semantic_failure_does_not_enter_repair(monkeypatch):
    """A candidate that contradicts the ACCEPTED command is a different answer,
    not a formatting slip — it stays terminal on the first completion."""
    from app.services.training_generation.output_errors import SemanticInvalidError

    week = _week()
    for day in week["program"]:
        day["tip"] = "dinlenme"
        day["egzersizler"] = []
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(week)

    with pytest.raises(SemanticInvalidError):
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
    entry_repair_catch = (
        "except (ParseFailedError, TruncatedError, SchemaInvalidError) as exc:")
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
        "except SemanticInvalidError:")[0]
    assert "canonicalize_plan_exercises" not in repair_block
    assert "GenerationExercise" not in repair_block


def test_repair_catches_remain_exactly_the_formatting_errors():
    """Exactly ONE repair entry point, covering exactly the three provider
    FORMATTING failures. SemanticInvalidError must never be added to it."""
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    entry = "except (ParseFailedError, TruncatedError, SchemaInvalidError) as exc:"
    assert source.count(entry) == 1
    assert (
        "except (ParseFailedError, TruncatedError, SchemaInvalidError, "
        "SemanticInvalidError) as exc:"
    ) not in source
    # The inner catch still re-raises every typed failure of the repair turn.
    assert (
        "except (ParseFailedError, TruncatedError, SchemaInvalidError, "
        "SemanticInvalidError):"
    ) in source


def test_provider_call_budget_stays_two():
    assert MAX_PROVIDER_COMPLETIONS == 2


def test_generated_exercise_id_is_accepted_by_save(client, auth_user, monkeypatch):
    """Task 4 closes the Task 3 gap: a generated plan carries exercise_id and
    a signed exercise context, and both survive the round trip into save. This
    test replaces the transient-gap test that asserted the opposite."""
    _session(auth_user)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: json.dumps(_week()))

    generated = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert generated.status_code == 200
    body = generated.get_json()
    assert body["program"][0]["egzersizler"][0]["exercise_id"].startswith("ex_")

    saved = client.post("/training-plan/save", json={
        "plan": body["program"], "score": body["overall_score"],
        "exercise_context_token": body["exercise_context_token"],
    })
    assert saved.status_code == 200
    stored = _stored_document(auth_user.id)
    assert stored["program"][0]["egzersizler"][0]["exercise_id"].startswith("ex_")


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


def test_generated_valid_plan_saves(client, auth_user, save_token):
    response = _post_save(client, _week()["program"], save_token())
    assert response.status_code == 200
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 1


def test_save_persists_the_canonicalized_plan_as_a_canonical_document(
        client, auth_user, save_token):
    """Sprint 11 PR4 Task 4 changes the persisted SHAPE: the row is now the
    canonical document (program + the server-created exercise_context), not
    the bare list the client posted. Equipment truth has to be stored with
    the plan it applies to, and a client-shaped row cannot carry it. Legacy
    bare-list rows stay readable — every reader branches on
    isinstance(data, list) — so there is no backfill and no migration.

    The original assertion is preserved verbatim in meaning: a submitted
    " Pazartesi " is persisted normalized to "Pazartesi"."""
    seeded = _post_save(client, _week()["program"], save_token(), score=6.0)
    assert seeded.status_code == 200

    submitted = _week()["program"]
    submitted[0]["gun"] = " Pazartesi "
    replaced = _post_save(client, submitted, save_token(), score=7.0)

    assert replaced.status_code == 200
    stored = _stored_document(auth_user.id)
    assert isinstance(stored, dict)
    assert stored["program"][0]["gun"] == "Pazartesi"
    assert stored["exercise_context"]["equipment_context"] == "spor_salonu"


def test_client_mutated_invalid_plan_does_not_save(client, auth_user, save_token):
    _post_save(client, _week()["program"], save_token())
    original = TrainingPlan.query.filter_by(user_id=auth_user.id).one().plan_data
    mutated = _week()["program"]
    mutated[0]["egzersizler"] = []
    rejected = _post_save(client, mutated, save_token(), score=1)
    assert rejected.status_code == 422
    assert rejected.get_json()["code"] == CODE_SAVE_INVALID
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).one().plan_data == original


def test_malformed_handcrafted_plan_does_not_save(client, auth_user, save_token):
    response = _post_save(client, {"v": 1}, save_token())
    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_INVALID
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_wrong_training_day_count_does_not_save(client, auth_user, save_token):
    response = _post_save(client, _week(training_days=1)["program"], save_token())
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_seven_training_days_does_not_save(client, auth_user, save_token):
    response = _post_save(client, _week(training_days=7)["program"], save_token())
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_wrong_weekday_count_does_not_save(client, auth_user, save_token):
    response = _post_save(client, _week()["program"][:3], save_token())
    assert response.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_save_rejection_happens_before_delete(client, auth_user, save_token):
    # "Keep Me" is no longer expressible: exercise identity is the catalog's.
    # A declared alias plays the same role — it is recognisable in the stored
    # row, and it proves the surviving plan is the canonicalized one.
    first = _post_save(client, _week(exercises=[_exercise("Bench")])["program"],
                       save_token())
    assert first.status_code == 200
    rejected = _post_save(client, {"v": 9}, save_token(), score=1)
    assert rejected.status_code == 422
    stored = _stored_document(auth_user.id)
    assert stored["program"][0]["egzersizler"][0]["isim"] == "Barbell Bench Press"


def test_validate_plan_for_save_rejects_schema_errors():
    with pytest.raises(SaveInvalidError):
        validate_plan_for_save({"v": 1}, ExerciseContext("spor_salonu"))


# ── Full mocked path ─────────────────────────────────────────────────────────


def test_generate_then_save_mocked_path(client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat", lambda **kwargs: json.dumps(_week()))
    generated = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert generated.status_code == 200
    body = generated.get_json()
    assert body["program"][0]["egzersizler"][0]["exercise_id"].startswith("ex_")

    # Both shapes reach save through the same boundary: the ID-bearing document
    # generation produced, and the name-only shape a client may still submit.
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
    for program in (body["program"], name_only_program):
        saved = client.post("/training-plan/save", json={
            "plan": program, "score": body["overall_score"],
            "exercise_context_token": body["exercise_context_token"],
        })
        assert saved.status_code == 200
        stored = _stored_document(auth_user.id)
        assert len(stored["program"]) == 7
        assert stored["exercise_context"]["catalog_version"] == \
            load_exercise_catalog().version


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


# ── Save-time exercise authority (Task 4) ────────────────────────────────────
#
# /training-plan/save is the only destructive TrainingPlan path in the app. It
# now proves catalog exercise authority — against the equipment context the
# SERVER accepted at generation time, carried in a signed token — before it
# deletes anything. Every test below is either "the authoritative thing is
# what got stored" or "the unauthoritative thing changed nothing".


def test_valid_id_with_tampered_name_persists_catalog_name(
        client, auth_user, save_token):
    token = save_token(equipment="spor_salonu")
    plan = _week()["program"]
    plan[0]["egzersizler"][0].update(
        exercise_id="ex_barbell_bench_press", isim="Magic Chest Exercise")

    assert _post_save(client, plan, token, score=8).status_code == 200

    persisted = _stored_document(auth_user.id)["program"][0]["egzersizler"][0]
    assert persisted["isim"] == "Barbell Bench Press"
    assert persisted["exercise_id"] == "ex_barbell_bench_press"


def test_invalid_exercise_save_does_not_delete_existing_plan(
        client, auth_user, save_token):
    assert _post_save(client, _week()["program"], save_token()).status_code == 200
    existing = TrainingPlan.query.filter_by(user_id=auth_user.id).one()
    before = existing.plan_data

    broken = _week()["program"]
    broken[0]["egzersizler"][0]["exercise_id"] = "ex_fake"
    response = _post_save(client, broken, save_token(), score=9)

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert db.session.get(TrainingPlan, existing.id).plan_data == before


# ── Context token at the route boundary ─────────────────────────────────────


@pytest.mark.parametrize("token", [
    None, "", "not-a-token", "1.AAAA.AAAA", "2.AAAA.AAAA", 42, {"v": 1},
])
def test_save_without_a_verifiable_context_token_is_refused(
        client, auth_user, token):
    body = {"plan": _week()["program"], "score": 7.0}
    if token is not None:
        body["exercise_context_token"] = token

    response = client.post("/training-plan/save", json=body)

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_CONTEXT_INVALID
    assert response.get_json()["retryable"] is False
    assert TrainingPlan.query.count() == 0


def test_save_refuses_a_context_token_minted_for_another_user(
        client, auth_user, save_token):
    response = _post_save(
        client, _week()["program"], save_token(user_id=auth_user.id + 1))

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_CONTEXT_INVALID
    assert TrainingPlan.query.count() == 0


def test_save_refuses_a_context_token_signed_with_another_key(
        client, auth_user):
    forged = sign_exercise_context(
        ExerciseContext("spor_salonu"), "not-the-app-secret", auth_user.id)

    response = _post_save(client, _week()["program"], forged)

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_CONTEXT_INVALID
    assert TrainingPlan.query.count() == 0


@pytest.mark.parametrize("token", [
    "1.ab\u00fccd.AAAA",       # Latin-1 accented, payload segment
    "1.\U0001f600AAA.AAAA",    # emoji, payload segment
    "1.\ud800AAA.AAAA",        # lone surrogate, payload segment
    "1.AAAA.ab\u00fccd",       # Latin-1 accented, signature segment
    "\u0661.AAAA.AAAA",        # Arabic-Indic digit one, version segment
])
def test_save_with_a_non_ascii_context_token_is_refused_not_a_server_error(
        client, auth_user, save_token, token):
    """A non-ASCII segment is a rejected token, never an unhandled 500.

    Regression: the signature used to be computed over the raw payload
    segment before anything charset-checked it, so a non-ASCII character
    raised UnicodeEncodeError. That is a ValueError but not an
    ExerciseContextInvalid, so it escaped the typed contract, returned 500,
    and handed the error store a frame whose locals are the whole token and
    the signing key.
    """
    assert _post_save(client, _week()["program"], save_token()).status_code == 200
    before = _stored_document(auth_user.id)

    response = _post_save(
        client, _week(exercises=[_exercise("Barbell Deadlift")])["program"],
        token, score=9)

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_CONTEXT_INVALID
    assert response.get_json()["retryable"] is False
    assert _stored_document(auth_user.id) == before


def test_rejected_context_token_never_echoes_any_part_of_itself(
        client, auth_user, save_token):
    token = save_token(user_id=auth_user.id + 1)
    body = _post_save(client, _week()["program"], token).get_json()

    assert set(body) == {"error", "code", "retryable"}
    for segment in token.split("."):
        assert segment not in body["error"]
    assert token not in json.dumps(body)


# ── Exercise identity at the route boundary ─────────────────────────────────


@pytest.mark.parametrize("exercise_id", [
    "ex_fake", "ex_not_a_real_lift", "EX_BARBELL_BENCH_PRESS",
    "barbell_bench_press", "ex_barbell_bench_press ",
])
def test_save_refuses_an_exercise_id_the_catalog_does_not_own(
        client, auth_user, save_token, exercise_id):
    plan = _week()["program"]
    plan[0]["egzersizler"][0]["exercise_id"] = exercise_id

    response = _post_save(client, plan, save_token())

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert TrainingPlan.query.count() == 0


def test_save_refuses_a_retired_exercise_by_name_and_by_id(
        client, auth_user, save_token, fixture_catalog):
    token = save_token(equipment="ev")
    assert _post_save(
        client, _week(exercises=[_exercise("Fixture Active")])["program"],
        token).status_code == 200
    survivor = _stored_document(auth_user.id)

    by_name = _week(exercises=[_exercise("Fixture Retired")])["program"]
    by_id = _week(exercises=[_exercise("Fixture Active")])["program"]
    by_id[0]["egzersizler"][0]["exercise_id"] = "ex_fixture_retired"

    for plan in (by_name, by_id):
        response = _post_save(client, plan, token, score=9)
        assert response.status_code == 422
        assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert _stored_document(auth_user.id) == survivor


def test_save_refuses_a_name_only_exercise_the_catalog_cannot_resolve(
        client, auth_user, save_token):
    plan = _week(exercises=[_exercise("Invented Laser Row")])["program"]

    response = _post_save(client, plan, save_token())

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert "Invented Laser Row" not in response.get_json()["error"]
    assert TrainingPlan.query.count() == 0


def test_save_accepts_a_declared_alias_and_stores_the_canonical_name(
        client, auth_user, save_token):
    plan = _week(exercises=[_exercise("Bench-Press")])["program"]

    assert _post_save(client, plan, save_token()).status_code == 200

    persisted = _stored_document(auth_user.id)["program"][0]["egzersizler"][0]
    assert persisted["isim"] == "Barbell Bench Press"
    assert persisted["exercise_id"] == "ex_barbell_bench_press"
    # Prescription fields are carried through untouched — the catalog owns
    # identity, not the dose.
    assert persisted["set"] == 3
    assert persisted["tekrar"] == "8-12"
    assert persisted["dinlenme"] == "90 sn"


# ── Equipment truth at the route boundary ───────────────────────────────────


def test_home_context_refuses_a_barbell_exercise_the_gym_context_allows(
        client, auth_user, save_token):
    bodyweight = _week(exercises=[_exercise("Push-Up")])["program"]
    barbell = _week(exercises=[_exercise("Barbell Back Squat")])["program"]

    assert _post_save(
        client, bodyweight, save_token(equipment="ev")).status_code == 200

    rejected = _post_save(client, barbell, save_token(equipment="ev"), score=9)
    assert rejected.status_code == 422
    assert rejected.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert _stored_document(auth_user.id)["program"][0]["egzersizler"][0]["isim"] \
        == "Push-Up"

    # Same plan, honest context — the refusal was about equipment, not the lift.
    assert _post_save(
        client, barbell, save_token(equipment="spor_salonu"), score=9,
    ).status_code == 200


def test_minimal_context_allows_dumbbell_and_band_but_not_machines(
        client, auth_user, save_token):
    token = save_token(equipment="minimal")
    allowed = _week(exercises=[_exercise("Goblet Squat"),
                               _exercise("Band Row")])["program"]
    machine = _week(exercises=[_exercise("Leg Press")])["program"]

    assert _post_save(client, allowed, token).status_code == 200
    refused = _post_save(client, machine, token, score=9)
    assert refused.status_code == 422
    assert refused.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID


# ── Cardio placement: the equipment gate must not be bypassable by placement ─


def test_a_cardio_exercise_cannot_be_saved_onto_a_training_day(
        client, auth_user, monkeypatch):
    """The full exploit, through the real routes and with nothing forged.

    ``is_exercise_compatible`` gates a cardio movement on the declared
    ``cardio_type`` and NOT on ``equipment_context`` — deliberately, because a
    home user who runs outdoors is a real product case. That carve-out is only
    sound while a cardio entry can only land on a ``kardiyo`` day. Without the
    placement rule, generating with ekipman="ev"/kardiyo_tipi="karisik"/
    kardiyo_gun=0 and then saving Swimming and Stationary Cycling into an
    "antrenman" day stored a server-blessed plan that prescribes a pool and a
    stationary bike under ``equipment_context: "ev"``.
    """
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(_week(exercises=[_exercise("Push-Up")])))

    generated = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "ekipman": "ev",
        "kardiyo_tipi": "karisik", "kardiyo_gun": 0,
    })
    assert generated.status_code == 200, generated.get_json()
    token = generated.get_json()["exercise_context_token"]

    smuggled = _week(exercises=[_exercise("Swimming"),
                                _exercise("Stationary Cycling")])["program"]
    assert smuggled[0]["tip"] == "antrenman"

    response = _post_save(client, smuggled, token)

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert TrainingPlan.query.count() == 0


def test_a_cardio_exercise_still_saves_on_a_genuine_cardio_day(
        client, auth_user, save_token):
    plan = _week(training_days=3, cardio_days=1,
                 exercises=[_exercise("Push-Up")],
                 cardio_exercises=[_exercise("Swimming", 1)])["program"]
    assert plan[3]["tip"] == "kardiyo"

    response = _post_save(
        client, plan, save_token(equipment="ev", cardio_type="karisik"))

    assert response.status_code == 200, response.get_json()
    stored = _stored_document(auth_user.id)["program"][3]["egzersizler"][0]
    assert stored["isim"] == "Swimming"
    assert stored["exercise_id"] == "ex_swimming"


def test_cardio_type_yok_still_refuses_a_cardio_exercise_on_a_cardio_day(
        client, auth_user, save_token):
    """The placement rule is additive: the cardio_type gate is still the gate."""
    plan = _week(training_days=3, cardio_days=1,
                 exercises=[_exercise("Push-Up")],
                 cardio_exercises=[_exercise("Swimming", 1)])["program"]

    response = _post_save(
        client, plan, save_token(equipment="ev", cardio_type="yok"))

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert TrainingPlan.query.count() == 0


def test_generation_fails_closed_when_the_provider_puts_cardio_on_a_training_day(
        client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(
            _week(exercises=[_exercise("Push-Up"), _exercise("Brisk Walk")])))

    response = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "ekipman": "ev",
        "kardiyo_tipi": "yuruyus", "kardiyo_gun": 0,
    })

    assert response.status_code == 500
    assert response.get_json()["code"] == CODE_GENERATION_EXERCISE_INCOMPATIBLE
    assert TrainingPlan.query.count() == 0


def test_canonicalization_refuses_a_cardio_movement_outside_a_cardio_day():
    plan = _week(exercises=[_exercise("Jump Rope")])
    context = ExerciseContext(equipment_context="ev", cardio_type="ip_atlama")

    with pytest.raises(GenerationExerciseIncompatibleError):
        canonicalize_plan_exercises(plan, context)


def test_canonicalization_allows_a_non_cardio_exercise_on_a_cardio_day():
    """One-directional on purpose.

    Forbidding a strength lift on a kardiyo day is a plan-quality opinion,
    not an authority question, so this boundary does not answer it.
    """
    plan = _week(training_days=3, cardio_days=1,
                 exercises=[_exercise("Push-Up")],
                 cardio_exercises=[_exercise("Push-Up", 1)])
    context = ExerciseContext(equipment_context="ev", cardio_type="karisik")

    canonical = canonicalize_plan_exercises(plan, context)

    assert canonical["program"][3]["egzersizler"][0]["isim"] == "Push-Up"


def test_persisted_exercise_context_comes_only_from_the_verified_token(
        client, auth_user, save_token):
    plan = _week(exercises=[_exercise("Push-Up")])["program"]
    token = save_token(equipment="ev", cardio_type="kosu", style="calisthenics")

    assert _post_save(client, plan, token).status_code == 200

    assert _stored_document(auth_user.id)["exercise_context"] == {
        "equipment_context": "ev",
        "cardio_type": "kosu",
        "style": "calisthenics",
        "catalog_version": load_exercise_catalog().version,
    }


# ── Client-authored authority keys are refused, never absorbed ──────────────


@pytest.mark.parametrize("authority_key", [
    "exercise_context", "equipment", "ekipman", "catalog_version",
    "exercise_context_token",
])
def test_save_refuses_client_authored_authority_keys_on_the_document(
        client, auth_user, save_token, authority_key):
    document = {
        "program": _week()["program"],
        authority_key: {"equipment_context": "spor_salonu"},
    }

    response = _post_save(client, document, save_token())

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_INVALID
    assert TrainingPlan.query.count() == 0


@pytest.mark.parametrize("authority_key", [
    "equipment", "movement", "primary_region", "active", "canonical_name",
])
def test_save_refuses_client_authored_catalog_metadata_on_an_exercise(
        client, auth_user, save_token, authority_key):
    plan = _week()["program"]
    plan[0]["egzersizler"][0][authority_key] = "bodyweight"

    response = _post_save(client, plan, save_token())

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_INVALID
    assert TrainingPlan.query.count() == 0


def test_a_client_declared_context_cannot_widen_the_signed_one(
        client, auth_user, save_token):
    """The forbidden move, spelled out: post a home token but ask for a gym."""
    plan = _week(exercises=[_exercise("Barbell Back Squat")])["program"]

    response = client.post("/training-plan/save", json={
        "plan": plan, "score": 7.0,
        "exercise_context_token": save_token(equipment="ev"),
        "exercise_context": {"equipment_context": "spor_salonu"},
        "ekipman": "spor_salonu",
    })

    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_SAVE_EXERCISE_INVALID
    assert TrainingPlan.query.count() == 0


# ── The weekly summary is preserved, never fabricated ───────────────────────


def test_save_preserves_a_weekly_summary_the_client_supplied(
        client, auth_user, save_token):
    # Deliberately NOT 7/7/7. That is byte-identical to the summary the
    # server synthesises when none is supplied, so a 7/7/7 fixture would pass
    # even with preservation removed and the default fabricated instead.
    plan = _week()
    plan["haftalik_ozet"] = {
        "yogunluk_skoru": 3, "denge_skoru": 4, "uygunluk_skoru": 5,
    }

    assert _post_save(client, plan, save_token()).status_code == 200

    ozet = _stored_document(auth_user.id)["haftalik_ozet"]
    assert ozet["yogunluk_skoru"] == 3
    assert ozet["denge_skoru"] == 4
    assert ozet["uygunluk_skoru"] == 5


def test_save_omits_the_weekly_summary_the_client_did_not_send(
        client, auth_user, save_token):
    assert _post_save(client, _week()["program"], save_token()).status_code == 200

    stored = _stored_document(auth_user.id)
    assert "haftalik_ozet" not in stored
    assert set(stored) == {"program", "exercise_context"}


# ── Ordering: nothing is destroyed until everything has passed ──────────────


def test_every_check_runs_before_the_destructive_delete(
        client, auth_user, save_token):
    """Delete spy over the whole rejection surface.

    Token, structure, semantics, exercise resolution and equipment
    compatibility each get a submission that fails only on them; none may
    reach the DELETE. The final valid save proves the spy can see a delete
    at all, so an empty list is evidence and not a broken probe.
    """
    assert _post_save(client, _week()["program"], save_token()).status_code == 200
    seeded = _stored_document(auth_user.id)

    rejections = {
        "token": {"plan": _week()["program"], "score": 1},
        "structure": {"plan": {"v": 9}, "score": 1,
                      "exercise_context_token": save_token()},
        "semantics": {"plan": _week(training_days=1)["program"], "score": 1,
                      "exercise_context_token": save_token()},
        "resolution": {
            "plan": _week(exercises=[_exercise("Invented Laser Row")])["program"],
            "score": 1, "exercise_context_token": save_token()},
        "equipment": {
            "plan": _week(exercises=[_exercise("Barbell Back Squat")])["program"],
            "score": 1, "exercise_context_token": save_token(equipment="ev")},
    }

    with delete_spy() as deletes:
        for label, body in rejections.items():
            response = client.post("/training-plan/save", json=body)
            assert response.status_code == 422, label
            assert deletes == [], label
        assert _post_save(
            client, _week()["program"], save_token(), score=9).status_code == 200
        assert len(deletes) == 1

    assert _stored_document(auth_user.id)["program"] == seeded["program"]


def test_save_route_verifies_context_before_it_validates_or_deletes():
    source = Path(training_bp.__file__).read_text(encoding="utf-8")
    verify = source.index("resolve_save_exercise_context(")
    validate = source.index("validate_plan_for_save(")
    delete = source.index("TrainingPlan.query.filter_by(user_id=current_user.id).delete()")

    assert verify < validate < delete


# ── Generation emits the token; unit callers may opt out ────────────────────


def test_generate_route_always_returns_a_context_token(
        client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: json.dumps(_week()))

    body = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45}).get_json()

    token = body["exercise_context_token"]
    assert isinstance(token, str) and token
    # The payload never restates the context in the clear: the token is the
    # only carrier, so a client cannot read (or edit) what it is asserting.
    assert "exercise_context" not in body
    assert "ekipman" not in json.dumps(body)


def test_generate_route_binds_the_token_factory_to_the_signed_in_user(
        client, auth_user, app, monkeypatch):
    """Behavioural, not a source grep: a factory closed over a hard-coded id
    would satisfy any "the right strings appear in the route" assertion."""
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat", lambda **kwargs: json.dumps(_week()))

    token = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45,
    }).get_json()["exercise_context_token"]

    minted_for = resolve_save_exercise_context(
        token, app.config["SECRET_KEY"], auth_user.id)
    assert minted_for.equipment_context == "spor_salonu"

    with pytest.raises(SaveContextInvalidError):
        resolve_save_exercise_context(
            token, app.config["SECRET_KEY"], auth_user.id + 1)


def test_generation_payload_omits_the_token_without_a_factory(monkeypatch):
    payload = _generate(monkeypatch, lambda **kwargs: json.dumps(_week()))
    assert "exercise_context_token" not in payload


def test_generated_token_matches_the_accepted_equipment_context(
        client, auth_user, app, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(_week(exercises=[_exercise("Push-Up")])))

    body = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "ekipman": "ev",
    }).get_json()

    context = resolve_save_exercise_context(
        body["exercise_context_token"], app.config["SECRET_KEY"], auth_user.id)
    assert context.equipment_context == "ev"


# ── Structural validation opts in to exercise_id only where save asks ───────


def test_generation_structural_validation_still_rejects_provider_authored_ids():
    plan = _week()
    plan["program"][0]["egzersizler"][0]["exercise_id"] = "ex_barbell_bench_press"
    with pytest.raises(SchemaInvalidError):
        validate_generated_plan(plan, _prefs())


def test_only_one_resolution_path_exists_for_plan_exercises():
    """Save must reuse the generation canonicalizer, not fork a second one."""
    gen_root = Path(training_service.__file__).resolve().parent
    resolvers = [
        path.name for path in gen_root.glob("*.py")
        if "resolve_exercise(" in path.read_text(encoding="utf-8")
    ]
    assert resolvers == ["exercise_resolution.py"]
