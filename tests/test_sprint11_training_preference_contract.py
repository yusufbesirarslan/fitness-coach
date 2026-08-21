"""Sprint 11 PR2 — Canonical training preference contract & capability matrix.

Hermetic: no live Bedrock/OpenAI. Provider invocation is always a spy.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import PlanMutationRecord, TrainingPlan, UserSession
from app.services.training_generation.capability import (
    STATUS_CONFLICTING,
    STATUS_SUPPORTED,
    STATUS_UNSUPPORTED,
    evaluate_capability,
)
from app.services.training_generation.classifier_service import classify_user
from app.services.training_generation.feature_extractor import parse_preferences
from app.services.training_generation.models import (
    PerformanceHistory,
    TrainingPreferences,
    UserTrainingFeatures,
)
from app.services.training_generation.preference_contract import (
    CODE_CONFLICTING,
    CODE_GENERATION_PARSE_FAILED,
    CODE_GENERATION_SEMANTICALLY_INVALID,
    CODE_INVALID,
    CODE_NO_SESSION,
    CODE_UNSUPPORTED,
    PreferenceContractError,
)
from app.services.training_generation.program_generator import (
    STYLE_ALIASES,
    build_program_context,
    canonical_style,
)
from app.services.training_generation.prompt_builder import build_training_prompt
from app.services.training_generation.response_validator import (
    WEEKDAYS,
    validate_generated_plan,
)
from app.services.training_generation import service as training_service


ASSET_ROOT = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "training_assets"
)

OFFICIAL_UI_STYLES = (
    "genel",
    "crossfit",
    "calisthenics",
    "powerlifting",
    "bodybuilding",
    "fonksiyonel",
)
OFFICIAL_UI_GOALS = (
    "genel",
    "guc",
    "kondisyon",
    "kas_kutlesi",
    "yag_yakimi",
    "esneklik",
)
OFFICIAL_UI_EQUIPMENT = ("spor_salonu", "ev", "minimal")
OFFICIAL_UI_DAYS = (3, 4, 5, 6)
OFFICIAL_UI_DURATIONS = (30, 45, 60, 90)


def _features(**overrides):
    data = dict(
        age=30,
        weight=82.0,
        height=180.0,
        goal="kas kazanma",
        self_reported_level="intermediate",
        current_activity="active",
        training_history_months=18,
        weekly_frequency=4,
        movement_competency={
            "squat": 2,
            "hinge": 2,
            "horizontal_push": 2,
            "vertical_push": 1,
            "horizontal_pull": 2,
            "vertical_pull": 1,
            "core": 2,
        },
        strength_proxy=1.0,
        consistency_score=0.75,
        injuries="",
        performance_history=PerformanceHistory(
            weekly_training_sessions=[4, 4, 3, 4],
            volume_trend=[5000.0, 5200.0, 5400.0, 5600.0],
            adherence_score=0.75,
            fatigue_trend=2.0,
            sleep_quality=4.0,
            stable_score_weeks=3,
        ),
    )
    data.update(overrides)
    return UserTrainingFeatures(**data)


def _seven_day_plan(training_days=3, exercise_name="Invented Laser Squat 9000"):
    program = []
    for index, day in enumerate(WEEKDAYS):
        if index < training_days:
            program.append({
                "gun": day,
                "tip": "antrenman",
                "odak": "Full Body",
                "sure_dk": 45,
                "tahmini_kalori": 300,
                "egzersizler": [
                    {
                        "isim": exercise_name if n == 0 else f"{exercise_name} {n}",
                        "set": 3,
                        "tekrar": "8-12",
                        "dinlenme": "90 sn",
                        "not": "",
                    }
                    for n in range(4)
                ],
            })
        else:
            program.append({
                "gun": day,
                "tip": "dinlenme",
                "odak": "Aktif Toparlanma",
                "sure_dk": 0,
                "tahmini_kalori": 0,
                "egzersizler": [],
            })
    return {
        "program": program,
        "haftalik_ozet": {
            "yogunluk_skoru": 7,
            "denge_skoru": 7,
            "uygunluk_skoru": 7,
        },
    }


def _prompt_for(style="genel", **pref_overrides):
    prefs = parse_preferences({
        "gun_sayisi": pref_overrides.get("gun_sayisi", 4),
        "ekipman": pref_overrides.get("ekipman", "spor_salonu"),
        "odak": pref_overrides.get("odak", "tum_vucut"),
        "sure": pref_overrides.get("sure", 45),
        "kardiyo_tipi": pref_overrides.get("kardiyo_tipi", "yok"),
        "kardiyo_gun": pref_overrides.get("kardiyo_gun", 0),
        "antrenman_tarzi": style,
        "odak_hedef": pref_overrides.get("odak_hedef", "kas_kutlesi"),
        "injuries": pref_overrides.get("injuries", ""),
    })
    features = _features()
    classification = classify_user(features)
    context = build_program_context(features, prefs, classification)
    return build_training_prompt(features, prefs, classification, context), prefs, context


def _session(auth_user, **kwargs):
    row = UserSession(
        user_id=auth_user.id,
        goal=kwargs.get("goal", "kas kazanma"),
        fitness_level=kwargs.get("fitness_level", "intermediate"),
        current_activity=kwargs.get("current_activity", "active"),
        tdee=kwargs.get("tdee", 2700),
    )
    db.session.add(row)
    db.session.commit()
    return row


# ── Preference normalization ─────────────────────────────────────────────────


@pytest.mark.parametrize("style", OFFICIAL_UI_STYLES)
def test_parse_accepts_every_official_style(style):
    prefs = parse_preferences({"antrenman_tarzi": style, "gun_sayisi": 4, "sure": 45})
    assert prefs.antrenman_tarzi == style
    assert canonical_style(style) in STYLE_ALIASES.values()


@pytest.mark.parametrize("alias,canonical_ui", [
    ("general", "genel"),
    ("general_fitness", "genel"),
    ("functional", "fonksiyonel"),
])
def test_parse_normalizes_declared_style_aliases(alias, canonical_ui):
    prefs = parse_preferences({"antrenman_tarzi": alias, "gun_sayisi": 4, "sure": 45})
    assert prefs.antrenman_tarzi == canonical_ui
    assert canonical_style(prefs.antrenman_tarzi) == STYLE_ALIASES[canonical_ui]


def test_unknown_style_is_invalid_not_general():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"antrenman_tarzi": "banana_program", "gun_sayisi": 3, "sure": 45})
    assert exc.value.public_code == CODE_INVALID
    assert exc.value.retryable is False
    with pytest.raises(PreferenceContractError):
        canonical_style("banana_program")
    with pytest.raises(PreferenceContractError):
        canonical_style("olympic_weightlifting")


@pytest.mark.parametrize("payload", [[], "oops", 3, 0, False])
def test_non_object_payload_is_invalid_not_default_general(payload):
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences(payload)
    assert exc.value.public_code == CODE_INVALID
    assert exc.value.reason == "INVALID_PAYLOAD"
    assert exc.value.retryable is False


def test_present_float_day_count_is_invalid_not_truncated():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"gun_sayisi": 6.9, "sure": 45})
    assert exc.value.public_code == CODE_INVALID
    with pytest.raises(PreferenceContractError):
        parse_preferences({"gun_sayisi": 3.0, "sure": 45})


def test_present_float_duration_is_invalid_not_truncated():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"gun_sayisi": 3, "sure": 45.9})
    assert exc.value.public_code == CODE_INVALID


def test_empty_style_is_invalid_not_general():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"antrenman_tarzi": "", "gun_sayisi": 3, "sure": 45})
    assert exc.value.public_code == CODE_INVALID
    with pytest.raises(PreferenceContractError):
        canonical_style("")


@pytest.mark.parametrize("goal", OFFICIAL_UI_GOALS)
def test_parse_accepts_every_official_focus(goal):
    prefs = parse_preferences({"odak_hedef": goal, "gun_sayisi": 3, "sure": 45})
    assert prefs.odak_hedef == goal


def test_unknown_focus_is_invalid():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"odak_hedef": "shredded_peak", "gun_sayisi": 3, "sure": 45})
    assert exc.value.public_code == CODE_INVALID


@pytest.mark.parametrize("days", OFFICIAL_UI_DAYS)
def test_parse_accepts_official_day_counts(days):
    prefs = parse_preferences({"gun_sayisi": days, "sure": 45})
    assert prefs.gun_sayisi == days


def test_out_of_range_days_are_invalid_not_clamped():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"gun_sayisi": 7, "sure": 45})
    assert exc.value.public_code == CODE_INVALID
    with pytest.raises(PreferenceContractError):
        parse_preferences({"gun_sayisi": 1, "sure": 45})


@pytest.mark.parametrize("duration", OFFICIAL_UI_DURATIONS)
def test_parse_accepts_official_durations(duration):
    prefs = parse_preferences({"sure": duration, "gun_sayisi": 3})
    assert prefs.sure == duration


def test_unknown_duration_is_invalid_not_clamped():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"sure": 75, "gun_sayisi": 3})
    assert exc.value.public_code == CODE_INVALID


@pytest.mark.parametrize("equipment", OFFICIAL_UI_EQUIPMENT)
def test_parse_accepts_official_equipment(equipment):
    prefs = parse_preferences({"ekipman": equipment, "gun_sayisi": 3, "sure": 45})
    assert prefs.ekipman == equipment


def test_unknown_equipment_is_invalid():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({"ekipman": "hotel_gym", "gun_sayisi": 3, "sure": 45})
    assert exc.value.public_code == CODE_INVALID


def test_cardio_fields_accept_official_values():
    prefs = parse_preferences({
        "gun_sayisi": 3,
        "sure": 45,
        "kardiyo_tipi": "kosu",
        "kardiyo_gun": 2,
        "kardiyo_sure": 20,
        "kardiyo_yogunluk": "orta",
    })
    assert prefs.kardiyo_tipi == "kosu"
    assert prefs.kardiyo_gun == 2
    assert prefs.kardiyo_sure == 20
    assert prefs.kardiyo_yogunluk == "orta"


def test_unknown_cardio_type_is_invalid():
    with pytest.raises(PreferenceContractError) as exc:
        parse_preferences({
            "gun_sayisi": 3, "sure": 45, "kardiyo_tipi": "rowing_erg",
        })
    assert exc.value.public_code == CODE_INVALID


# ── Capability matrix ────────────────────────────────────────────────────────


def test_general_gym_midweek_is_supported():
    prefs = parse_preferences({
        "antrenman_tarzi": "genel",
        "ekipman": "spor_salonu",
        "gun_sayisi": 4,
        "sure": 45,
        "odak_hedef": "genel",
        "kardiyo_tipi": "yok",
    })
    result = evaluate_capability(prefs)
    assert result.status == STATUS_SUPPORTED


def test_bodybuilding_gym_is_supported():
    prefs = parse_preferences({
        "antrenman_tarzi": "bodybuilding",
        "ekipman": "spor_salonu",
        "gun_sayisi": 4,
        "sure": 60,
    })
    assert evaluate_capability(prefs).status == STATUS_SUPPORTED


def test_powerlifting_gym_three_day_is_supported():
    prefs = parse_preferences({
        "antrenman_tarzi": "powerlifting",
        "ekipman": "spor_salonu",
        "gun_sayisi": 3,
        "sure": 60,
    })
    assert evaluate_capability(prefs).status == STATUS_SUPPORTED


def test_calisthenics_home_is_supported():
    prefs = parse_preferences({
        "antrenman_tarzi": "calisthenics",
        "ekipman": "ev",
        "gun_sayisi": 3,
        "sure": 45,
    })
    assert evaluate_capability(prefs).status == STATUS_SUPPORTED


def test_functional_minimal_is_supported():
    prefs = parse_preferences({
        "antrenman_tarzi": "fonksiyonel",
        "ekipman": "minimal",
        "gun_sayisi": 3,
        "sure": 45,
    })
    assert evaluate_capability(prefs).status == STATUS_SUPPORTED


def test_crossfit_is_unsupported_for_every_equipment():
    for equipment in OFFICIAL_UI_EQUIPMENT:
        prefs = parse_preferences({
            "antrenman_tarzi": "crossfit",
            "ekipman": equipment,
            "gun_sayisi": 3,
            "sure": 45,
        })
        result = evaluate_capability(prefs)
        assert result.status == STATUS_UNSUPPORTED
        assert result.reason == "CROSSFIT_SCHEMA_UNSUPPORTED"


def test_powerlifting_home_is_unsupported():
    prefs = parse_preferences({
        "antrenman_tarzi": "powerlifting",
        "ekipman": "ev",
        "gun_sayisi": 3,
        "sure": 45,
    })
    result = evaluate_capability(prefs)
    assert result.status == STATUS_UNSUPPORTED
    assert result.reason == "POWERLIFTING_REQUIRES_GYM_EQUIPMENT"


def test_powerlifting_minimal_is_unsupported():
    prefs = parse_preferences({
        "antrenman_tarzi": "powerlifting",
        "ekipman": "minimal",
        "gun_sayisi": 3,
        "sure": 45,
    })
    assert evaluate_capability(prefs).status == STATUS_UNSUPPORTED


def test_six_training_plus_five_cardio_is_conflicting():
    prefs = parse_preferences({
        "antrenman_tarzi": "genel",
        "ekipman": "spor_salonu",
        "gun_sayisi": 6,
        "sure": 45,
        "kardiyo_tipi": "kosu",
        "kardiyo_gun": 5,
    })
    result = evaluate_capability(prefs)
    assert result.status == STATUS_CONFLICTING
    assert result.reason == "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS"
    assert prefs.gun_sayisi + prefs.kardiyo_gun > 7


def test_cardio_days_without_type_is_conflicting():
    prefs = parse_preferences({
        "antrenman_tarzi": "genel",
        "ekipman": "spor_salonu",
        "gun_sayisi": 3,
        "sure": 45,
        "kardiyo_tipi": "yok",
        "kardiyo_gun": 3,
    })
    assert prefs.kardiyo_tipi == "yok"
    assert prefs.kardiyo_gun == 3
    result = evaluate_capability(prefs)
    assert result.status == STATUS_CONFLICTING
    assert result.reason == "CARDIO_DAYS_WITHOUT_TYPE"


def test_feasible_cardio_allocation_is_supported():
    prefs = parse_preferences({
        "antrenman_tarzi": "genel",
        "ekipman": "spor_salonu",
        "gun_sayisi": 3,
        "sure": 45,
        "kardiyo_tipi": "kosu",
        "kardiyo_gun": 2,
    })
    assert evaluate_capability(prefs).status == STATUS_SUPPORTED


# ── Provider-call boundary ───────────────────────────────────────────────────


def test_unknown_style_invokes_zero_provider_calls(client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "banana_program",
    })
    assert response.status_code == 422
    body = response.get_json()
    assert body["code"] == CODE_INVALID
    assert body["retryable"] is False
    assert called == []


def test_crossfit_invokes_zero_provider_calls(client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "crossfit",
        "ekipman": "spor_salonu",
    })
    assert response.status_code == 422
    body = response.get_json()
    assert body["code"] == CODE_UNSUPPORTED
    assert body["retryable"] is False
    assert called == []


def test_home_powerlifting_invokes_zero_provider_calls(client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45,
        "antrenman_tarzi": "powerlifting",
        "ekipman": "ev",
        "odak_hedef": "guc",
    })
    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_UNSUPPORTED
    assert called == []


def test_overfull_week_invokes_zero_provider_calls(client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post("/training-plan", json={
        "gun_sayisi": 6, "sure": 45,
        "antrenman_tarzi": "genel",
        "kardiyo_tipi": "kosu",
        "kardiyo_gun": 5,
    })
    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_CONFLICTING
    assert called == []


def test_non_object_json_body_invokes_zero_provider_calls(
        client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post(
        "/training-plan",
        data="[]",
        content_type="application/json",
    )
    assert response.status_code == 422
    body = response.get_json()
    assert body["code"] == CODE_INVALID
    assert body["retryable"] is False
    assert called == []


def test_cardio_days_without_type_invokes_zero_provider_calls(
        client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45,
        "antrenman_tarzi": "genel",
        "ekipman": "spor_salonu",
        "kardiyo_tipi": "yok",
        "kardiyo_gun": 3,
    })
    assert response.status_code == 422
    assert response.get_json()["code"] == CODE_CONFLICTING
    assert called == []


def test_unsupported_request_does_not_retry(client, auth_user, monkeypatch):
    _session(auth_user)
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "crossfit",
    })
    assert called == []


# ── Supported request preserves canonical semantics ──────────────────────────


def test_supported_request_sends_canonical_style_and_focus_to_prompt(
        client, auth_user, monkeypatch):
    _session(auth_user, goal="kilo verme")
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return __import__("json").dumps(_seven_day_plan(3, "Goblet Squat"))

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)
    response = client.post("/training-plan", json={
        "gun_sayisi": 3,
        "sure": 45,
        "ekipman": "spor_salonu",
        "antrenman_tarzi": "bodybuilding",
        "odak_hedef": "kas_kutlesi",
        "odak": "tum_vucut",
    })
    assert response.status_code == 200, response.get_json()
    prompt = captured["prompt"]
    assert "bodybuilding" in prompt.lower() or "hipertrofi" in prompt.lower()
    assert "kas_kutlesi" in prompt
    assert "Hypertrophy" in prompt
    assert "odak_hedef" in prompt
    assert "kilo verme" in prompt
    assert "3" in prompt
    assert "45" in prompt
    assert "spor_salonu" in prompt


def test_supported_powerlifting_reaches_generation(client, auth_user, monkeypatch):
    _session(auth_user)
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return __import__("json").dumps(_seven_day_plan(3, "Back Squat"))

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)
    body = client.post("/training-plan", json={
        "gun_sayisi": 3,
        "sure": 60,
        "antrenman_tarzi": "powerlifting",
        "ekipman": "spor_salonu",
        "odak_hedef": "guc",
    }).get_json()
    assert "error" not in body
    assert "ana kaldırış" in captured["prompt"]
    assert "odak_hedef" in captured["prompt"]
    assert "guc" in captured["prompt"]


def test_days_duration_equipment_survive_normalization():
    prompt, prefs, _context = _prompt_for(
        "genel", gun_sayisi=5, sure=90, ekipman="minimal",
        odak_hedef="yag_yakimi",
    )
    assert prefs.gun_sayisi == 5
    assert prefs.sure == 90
    assert prefs.ekipman == "minimal"
    assert "90" in prompt
    assert "minimal" in prompt
    assert "yag_yakimi" in prompt


# ── Equipment-filtered prompt vocabulary (PR4 Task 2) ────────────────────────


def _captured_supported_prompt(client, auth_user, monkeypatch, **overrides):
    """POST a guaranteed-supported request and capture the provider prompt."""
    _session(auth_user)
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return __import__("json").dumps(_seven_day_plan(overrides.get("gun_sayisi", 3)))

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)
    payload = {
        "gun_sayisi": 3,
        "sure": 45,
        "ekipman": "spor_salonu",
        "antrenman_tarzi": "genel",
        "odak_hedef": "genel",
        "odak": "tum_vucut",
    }
    payload.update(overrides)
    response = client.post("/training-plan", json=payload)
    assert response.status_code == 200, response.get_json()
    return captured["prompt"]


def test_home_prompt_lists_bodyweight_but_not_barbell_exercises(
        client, auth_user, monkeypatch):
    prompt = _captured_supported_prompt(client, auth_user, monkeypatch, ekipman="ev")
    assert "Push-Up" in prompt
    assert "Barbell Back Squat" not in prompt


def test_minimal_prompt_lists_dumbbell_and_band_but_not_machine(
        client, auth_user, monkeypatch):
    # NOTE: "Goblet Squat" is deliberately avoided here — it also appears in
    # prompt_builder's static JSON FORMAT example, which would make the
    # assertion pass even without the vocabulary block.
    prompt = _captured_supported_prompt(client, auth_user, monkeypatch, ekipman="minimal")
    assert "Dumbbell Row" in prompt
    assert "Lat Pulldown" not in prompt


def test_gym_prompt_lists_every_equipment_tier(client, auth_user, monkeypatch):
    prompt = _captured_supported_prompt(client, auth_user, monkeypatch, ekipman="spor_salonu")
    assert "Push-Up" in prompt
    assert "Goblet Squat" in prompt
    assert "Barbell Back Squat" in prompt
    assert "Lat Pulldown" in prompt


def test_direct_prompt_builder_call_without_vocabulary_omits_exercise_section():
    """Callers that don't pass exercise_vocabulary (app/prompts/workout.py,
    tests/test_i18n.py, ...) must see byte-identical prompts to before Task 2."""
    prompt, _prefs, _context = _prompt_for("genel", ekipman="ev")
    assert "EXERCISE VOCABULARY" not in prompt


# ── odak_hedef cannot be accepted and ignored ────────────────────────────────


def test_odak_hedef_reaches_generation_command():
    prompt, prefs, _context = _prompt_for("bodybuilding", odak_hedef="kas_kutlesi")
    assert prefs.odak_hedef == "kas_kutlesi"
    assert "kas_kutlesi" in prompt
    assert "odak_hedef" in prompt
    assert "Hypertrophy" in prompt
    goals_asset = (ASSET_ROOT / "rules" / "goals.json").read_text(encoding="utf-8")
    assert "Hypertrophy" in goals_asset


def test_goals_json_is_consumed_by_prompt_builder():
    gen_root = Path(__file__).resolve().parents[1] / "app" / "services" / "training_generation"
    prompt_src = (gen_root / "prompt_builder.py").read_text(encoding="utf-8")
    assert "odak_hedef" in prompt_src
    assert "goals.json" in prompt_src or "load_focus_directive" in prompt_src


# ── Typed API shape ──────────────────────────────────────────────────────────


def test_missing_session_is_typed_and_skips_provider(client, auth_user, monkeypatch):
    called = []
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: called.append(kwargs) or "{}")
    response = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == CODE_NO_SESSION
    assert body["retryable"] is False
    assert "error" in body
    assert called == []


def test_malformed_provider_output_is_typed_generation_invalid(
        client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: "sorry I cannot")
    response = client.post("/training-plan", json={"gun_sayisi": 3, "sure": 45})
    assert response.status_code == 500
    body = response.get_json()
    assert body["code"] == CODE_GENERATION_PARSE_FAILED
    assert "error" in body


def test_day_count_mismatch_does_not_retry_and_is_semantic(
        client, auth_user, monkeypatch):
    _session(auth_user)
    calls = []
    wrong = _seven_day_plan(training_days=1)

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return __import__("json").dumps(wrong)

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)
    response = client.post("/training-plan", json={"gun_sayisi": 4, "sure": 45})
    assert response.status_code == 500
    assert response.get_json()["code"] == CODE_GENERATION_SEMANTICALLY_INVALID
    assert len(calls) == 1


# ── Persistence / Adaptive Coaching unchanged ────────────────────────────────


def test_save_replaces_plan_without_journal_or_typed_generation_command(
        client, auth_user):
    first = client.post("/training-plan/save", json={
        "plan": _seven_day_plan(3, "Squat")["program"],
        "score": 7.0,
    })
    assert first.status_code == 200
    first_row = TrainingPlan.query.filter_by(user_id=auth_user.id).one()
    first_lineage = first_row.lineage_id
    assert first_row.mutation_version == 0

    second = client.post("/training-plan/save", json={
        "plan": _seven_day_plan(3, "Deadlift")["program"],
        "score": 8.0,
    })
    assert second.status_code == 200
    rows = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
    assert len(rows) == 1
    assert rows[0].lineage_id != first_lineage
    assert rows[0].mutation_version == 0
    assert PlanMutationRecord.query.filter_by(user_id=auth_user.id).count() == 0
    assert "Deadlift" in rows[0].plan_data


def test_rejected_generate_cannot_be_saved_as_success(client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: "{}")
    generate = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "crossfit",
    })
    assert generate.status_code == 422
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


# ── Schema pins left for later PRs ───────────────────────────────────────────


def test_invented_exercise_names_still_pass_shape_validator():
    prefs = TrainingPreferences(gun_sayisi=3, ekipman="ev")
    plan = _seven_day_plan(training_days=3, exercise_name="Quantum Trap Bar Snatch")
    validated, warnings = validate_generated_plan(plan, prefs)
    assert validated["program"][0]["egzersizler"][0]["isim"] == "Quantum Trap Bar Snatch"
    assert warnings == []


# ── Architecture guards ──────────────────────────────────────────────────────


def test_canonical_style_has_no_unknown_to_general_fallback():
    with pytest.raises(PreferenceContractError):
        canonical_style("unknown-style-xyz")
    assert canonical_style("genel") == "general_fitness"
    assert canonical_style("general_fitness") == "general_fitness"


def test_program_context_does_not_fallback_unknown_style_to_general():
    prefs = TrainingPreferences(gun_sayisi=3, antrenman_tarzi="banana_program", sure=45)
    with pytest.raises(PreferenceContractError):
        build_program_context(_features(), prefs, classify_user(_features()))


def test_program_generator_source_has_no_style_rules_general_fallback():
    source = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "training_generation" / "program_generator.py"
    ).read_text(encoding="utf-8")
    assert "_style_rules()[style]" in source
    for snippet in (
        "_style_rules().get(style",
        ".get(style, _style_rules()",
        'get(style, "general_fitness")',
        "get(style, 'general_fitness')",
        '.get(style, "general_fitness")',
    ):
        assert snippet not in source, snippet


def test_canonical_style_source_has_no_general_default():
    source = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "training_generation" / "preference_contract.py"
    ).read_text(encoding="utf-8")
    assert "STYLE_INPUT_ALIASES.get(key)" in source
    assert "STYLE_INPUT_ALIASES.get(key," not in source
    assert '.get(key, "general_fitness")' not in source


def test_plan_v2_does_not_zero_cardio_days_when_type_is_yok():
    source = (
        Path(__file__).resolve().parents[1] / "static" / "plan_create.js"
    ).read_text(encoding="utf-8")
    assert "selections.kardiyo_gun = 0" not in source


def test_service_evaluates_capability_before_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(training_service, "persist_posted_injuries", lambda *a, **k: None)
    user = SimpleNamespace(user_metadata={}, id=1)

    def boom(**kwargs):
        calls.append(kwargs)
        raise AssertionError("provider must not be called")

    with pytest.raises(PreferenceContractError) as exc:
        training_service.generate_training_plan_payload(
            user, SimpleNamespace(), {
                "gun_sayisi": 3, "sure": 45, "antrenman_tarzi": "crossfit",
            },
            chat_fn=boom,
        )
    assert exc.value.public_code == CODE_UNSUPPORTED
    assert calls == []


def test_plan_v2_client_does_not_map_typed_400_to_setup():
    source = (
        Path(__file__).resolve().parents[1] / "static" / "plan_create.js"
    ).read_text(encoding="utf-8")
    assert "TRAINING_PLAN_NO_SESSION" in source
    assert "data.code === \"TRAINING_PLAN_NO_SESSION\"" in source or \
        "data.code === 'TRAINING_PLAN_NO_SESSION'" in source


def test_legacy_client_surfaces_backend_error_text():
    source = (
        Path(__file__).resolve().parents[1] / "static" / "training.js"
    ).read_text(encoding="utf-8")
    assert "if (data.error)" in source
    assert "plan.contract.conflicting_preferences" in source


def test_contract_failure_never_enters_compact_retry(monkeypatch):
    calls = []
    user = SimpleNamespace(user_metadata={}, id=1)
    monkeypatch.setattr(training_service, "persist_posted_injuries", lambda *a, **k: None)

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return "{}"

    with pytest.raises(PreferenceContractError):
        training_service.generate_training_plan_payload(
            user, SimpleNamespace(), {
                "gun_sayisi": 6, "sure": 45,
                "antrenman_tarzi": "genel",
                "kardiyo_tipi": "kosu",
                "kardiyo_gun": 5,
            },
            chat_fn=fake_chat,
        )
    assert calls == []
