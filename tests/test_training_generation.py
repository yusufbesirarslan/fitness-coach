import json
from types import SimpleNamespace

import pytest

from app.services.training_generation.classifier_service import classify_user
from app.services.training_generation.models import (
    PerformanceHistory,
    TrainingPreferences,
    UserTrainingFeatures,
)
from app.services.training_generation.program_generator import build_program_context
from app.services.training_generation.recovery_model import recovery_capacity_factor
from app.services.training_generation.response_validator import (
    PlanValidationError,
    WEEKDAYS,
    validate_generated_plan,
)
from app.services.training_generation import service as training_service


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
        injuries="Hiçbiri",
        performance_history=PerformanceHistory(
            weekly_training_sessions=[4, 4, 3, 4],
            volume_trend=[5000, 5200, 5400, 5600],
            adherence_score=0.75,
            fatigue_trend=2.0,
            sleep_quality=4.0,
            stable_score_weeks=3,
        ),
    )
    data.update(overrides)
    return UserTrainingFeatures(**data)


def test_missing_movement_data_caps_advanced_self_report():
    result = classify_user(_features(
        self_reported_level="advanced",
        training_history_months=60,
        weekly_frequency=6,
        movement_competency={},
        strength_proxy=1.2,
        consistency_score=0.9,
    ))

    assert result.level == "Intermediate"
    assert "missing_movement_data" in result.risk_flags
    assert "advanced_requires_observed_competency" in result.constraints_applied
    assert result.confidence < 0.75


def test_injury_flag_downgrades_and_reduces_confidence():
    result = classify_user(_features(
        self_reported_level="intermediate",
        injuries="bel fıtığı",
        movement_competency={"squat": 2, "hinge": 2, "horizontal_push": 2, "core": 2},
    ))

    assert result.level == "Beginner"
    assert "active_injury_or_limitation" in result.risk_flags
    assert "injury_downgrade" in result.constraints_applied
    assert result.confidence <= 0.7


def test_beginner_default_can_upgrade_with_stable_window():
    # B2 regresyonu: saklı fitness_level'ı olmayıp "beginner" varsayılan olan bir
    # kullanıcı, gözlenen performans yüksek VE stabilite penceresi (>=3 hafta)
    # sağlandığında Beginner'ın üstüne çıkabilmeli.
    result = classify_user(_features(
        self_reported_level="beginner",
        training_history_months=48,
        weekly_frequency=5,
        strength_proxy=1.2,
        consistency_score=0.9,
        performance_history=PerformanceHistory(
            weekly_training_sessions=[5, 5, 4, 5],
            volume_trend=[6000, 6200, 6400, 6600],
            adherence_score=0.9,
            fatigue_trend=2.0,
            sleep_quality=4.0,
            stable_score_weeks=4,
        ),
    ))
    assert result.level != "Beginner"
    assert "upgrade_requires_stability_window" not in result.constraints_applied


def test_beginner_default_upgrade_blocked_without_stable_window():
    # Aynı güçlü sinyaller ama stabilite penceresi < 3 hafta → yükseltme
    # hysteresis ile Beginner'a geri çekilmeli.
    result = classify_user(_features(
        self_reported_level="beginner",
        training_history_months=48,
        weekly_frequency=5,
        strength_proxy=1.2,
        consistency_score=0.9,
        performance_history=PerformanceHistory(
            weekly_training_sessions=[5, 5, 4, 5],
            volume_trend=[6000, 6200, 6400, 6600],
            adherence_score=0.9,
            fatigue_trend=2.0,
            sleep_quality=4.0,
            stable_score_weeks=1,
        ),
    ))
    assert result.level == "Beginner"
    assert "upgrade_requires_stability_window" in result.constraints_applied


def test_recovery_factor_scales_down_for_age_frequency_fatigue_and_sleep():
    factor = recovery_capacity_factor(_features(
        age=54,
        weekly_frequency=6,
        performance_history=PerformanceHistory(
            weekly_training_sessions=[6, 6, 5, 6],
            adherence_score=0.8,
            fatigue_trend=4.5,
            sleep_quality=2.0,
            stable_score_weeks=3,
        ),
    ))

    assert 0.5 <= factor <= 1.2
    assert factor < 0.8


@pytest.mark.parametrize("style,expected", [
    ("bodybuilding", "hipertrofi"),
    ("powerlifting", "ana kaldırış"),
    ("crossfit", "energy system"),
    ("calisthenics", "progression tree"),
    ("fonksiyonel", "movement pattern"),
    ("genel", "full body"),
])
def test_program_context_uses_style_specific_rules(style, expected):
    prefs = TrainingPreferences(gun_sayisi=4, antrenman_tarzi=style, sure=45)
    classification = classify_user(_features())

    context = build_program_context(_features(), prefs, classification)

    assert expected in context.style_directive.lower()
    assert context.weekly_training_days == 4
    assert context.recovery_capacity_factor > 0
    assert "squat" in context.movement_coverage
    assert "horizontal_pull" in context.movement_coverage


def test_response_validator_rejects_invalid_tip_and_wrong_training_day_count():
    prefs = TrainingPreferences(gun_sayisi=3, sure=45)
    invalid_tip = {
        "program": [
            {"gun": "Pazartesi", "tip": "workout", "odak": "Full", "sure_dk": 45,
             "tahmini_kalori": 300, "egzersizler": []},
            {"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Çarşamba", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cuma", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cumartesi", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
        ],
        "haftalik_ozet": {"yogunluk_skoru": 7, "denge_skoru": 7, "uygunluk_skoru": 7},
    }

    with pytest.raises(PlanValidationError, match="tip"):
        validate_generated_plan(invalid_tip, prefs, injuries="")

    wrong_count = {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Full", "sure_dk": 45,
             "tahmini_kalori": 300, "egzersizler": [{"isim": "Squat"}]},
            {"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Çarşamba", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cuma", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cumartesi", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []},
        ],
        "haftalik_ozet": {"yogunluk_skoru": 7, "denge_skoru": 7, "uygunluk_skoru": 7},
    }

    with pytest.raises(PlanValidationError, match="antrenman günü"):
        validate_generated_plan(wrong_count, prefs, injuries="")


def _rest_day(gun):
    return {"gun": gun, "tip": "dinlenme", "odak": "Aktif Toparlanma",
            "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []}


def test_response_validator_rejects_training_day_without_exercises():
    # B7: "antrenman" günü egzersiz listesi boş olamaz.
    prefs = TrainingPreferences(gun_sayisi=1, sure=45)
    plan = {"program": [
        {"gun": "Pazartesi", "tip": "antrenman", "odak": "Full", "sure_dk": 45,
         "tahmini_kalori": 300, "egzersizler": []},
    ] + [_rest_day(g) for g in ["Salı", "Çarşamba", "Perşembe", "Cuma",
                                 "Cumartesi", "Pazar"]],
        "haftalik_ozet": {}}
    with pytest.raises(PlanValidationError, match="en az bir egzersiz"):
        validate_generated_plan(plan, prefs, injuries="")


def test_response_validator_rejects_duplicate_days():
    # B8: gün adları benzersiz olmalı (7× "Pazartesi" reddedilir).
    prefs = TrainingPreferences(gun_sayisi=0, sure=45)
    plan = {"program": [_rest_day("Pazartesi") for _ in range(7)],
            "haftalik_ozet": {}}
    with pytest.raises(PlanValidationError, match="benzersiz"):
        validate_generated_plan(plan, prefs, injuries="")


def test_response_validator_parses_messy_numeric_fields():
    # B9: "3-4" / "45 dk" / "~300" gibi değerler bare ValueError yerine
    # savunmacı ayrıştırılmalı (generic 500'e düşmeden).
    prefs = TrainingPreferences(gun_sayisi=1, sure=45)
    plan = {"program": [
        {"gun": "Pazartesi", "tip": "antrenman", "odak": "Full",
         "sure_dk": "45 dk", "tahmini_kalori": "~300",
         "egzersizler": [{"isim": "Squat", "set": "3-4", "tekrar": "8-12"}]},
    ] + [_rest_day(g) for g in ["Salı", "Çarşamba", "Perşembe", "Cuma",
                                 "Cumartesi", "Pazar"]],
        "haftalik_ozet": {"yogunluk_skoru": "~8"}}
    result, _warnings = validate_generated_plan(plan, prefs, injuries="")
    day0 = result["program"][0]
    assert day0["sure_dk"] == 45
    assert day0["tahmini_kalori"] == 300
    assert day0["egzersizler"][0]["set"] == 3
    assert result["haftalik_ozet"]["yogunluk_skoru"] == 8


def _valid_generated_plan():
    return {
        "program": [
            {
                "gun": day,
                "tip": "antrenman" if index == 0 else "dinlenme",
                "odak": "Full Body" if index == 0 else "Aktif Toparlanma",
                "sure_dk": 45 if index == 0 else 0,
                "tahmini_kalori": 300 if index == 0 else 0,
                "egzersizler": [{"isim": "Squat"}] if index == 0 else [],
            }
            for index, day in enumerate(WEEKDAYS)
        ],
        "haftalik_ozet": {
            "yogunluk_skoru": 7,
            "denge_skoru": 7,
            "uygunluk_skoru": 7,
        },
    }


def _stub_generation_pipeline(monkeypatch):
    preferences = TrainingPreferences(gun_sayisi=1, sure=45)
    classification = SimpleNamespace(
        level="Beginner",
        confidence=0.8,
        score=0.5,
        risk_flags=[],
        constraints_applied=[],
    )
    monkeypatch.setattr(training_service, "persist_posted_injuries", lambda *args, **kwargs: None)
    monkeypatch.setattr(training_service, "parse_preferences", lambda *args, **kwargs: preferences)
    monkeypatch.setattr(training_service, "build_features", lambda *args, **kwargs: object())
    monkeypatch.setattr(training_service, "classify_user", lambda *args, **kwargs: classification)
    monkeypatch.setattr(training_service, "build_program_context", lambda *args, **kwargs: object())
    monkeypatch.setattr(training_service, "build_training_prompt", lambda *args, **kwargs: "base prompt")
    monkeypatch.setattr(training_service, "build_system_prompt", lambda *args, **kwargs: "system prompt")


def _generate_with_chat(monkeypatch, chat_fn, logger=None):
    _stub_generation_pipeline(monkeypatch)
    user = SimpleNamespace(user_metadata={})
    return training_service.generate_training_plan_payload(
        user,
        None,
        {},
        chat_fn=chat_fn,
        logger=logger,
    )


def test_generation_retries_truncated_response_with_larger_compact_request(monkeypatch):
    calls = []
    warnings = []
    responses = iter(("{\"program\": [", json.dumps(_valid_generated_plan())))
    logger = SimpleNamespace(warning=lambda *args, **kwargs: warnings.append((args, kwargs)))

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return next(responses)

    result = _generate_with_chat(monkeypatch, fake_chat, logger=logger)

    assert result["program"][0]["tip"] == "antrenman"
    assert [call["max_tokens"] for call in calls] == [4000, 7000]
    assert calls[0]["messages"][0]["content"] == "base prompt"
    assert calls[1]["messages"][0]["content"].endswith(
        "Yanıtı kısa tut ve yalnızca eksiksiz JSON döndür."
    )
    assert len(warnings) == 1
    assert "{\"program\": [" not in repr(warnings)


def test_generation_retries_validation_error_once(monkeypatch):
    calls = []
    responses = iter((json.dumps({}), json.dumps(_valid_generated_plan())))

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return next(responses)

    _generate_with_chat(monkeypatch, fake_chat)

    assert [call["max_tokens"] for call in calls] == [4000, 7000]


def test_generation_does_not_retry_valid_first_response(monkeypatch):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return json.dumps(_valid_generated_plan())

    _generate_with_chat(monkeypatch, fake_chat)

    assert [call["max_tokens"] for call in calls] == [4000]


def test_generation_raises_parse_error_after_single_retry(monkeypatch):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return "not json"

    with pytest.raises(json.JSONDecodeError):
        _generate_with_chat(monkeypatch, fake_chat)

    assert [call["max_tokens"] for call in calls] == [4000, 7000]
