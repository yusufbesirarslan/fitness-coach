"""TR 13-17 AI Training first plan: AI-service vs purchase eligibility and the
youth boundary. The native journey through the real D1 fence is PostgreSQL-only:
tests/test_tr_minor_training_ai_pg.py. Shipping pins stay None (asserted).
"""
from datetime import datetime, timezone

import pytest

from app.extensions import db
from app.services import account_eligibility as policy, ai_launch
from app.services.training_generation import youth_safety
from app.services.training_generation.output_errors import SemanticInvalidError
from app.services.training_generation.preference_contract import (
    CODE_UNSUPPORTED, PreferenceContractError)
from tests.test_mobile_training_generation_api import CANONICAL, _provider_document

NOTICE = policy.TR_MINOR_NOTICE_VERSION
TEEN_AI = policy.TR_MINOR_TRAINING_AI_VERSION
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


@pytest.fixture(autouse=True)
def enable(app):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = True


@pytest.fixture
def pins(monkeypatch):
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", NOTICE)
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_TRAINING_AI_VERSION", TEEN_AI)
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", policy.VERSION)


def _ticket(age, country="TR"):
    return policy.preview({"age": age, "country": country})["eligibility"]["registration_token"]


def _user(make_user, age, name):
    user = make_user(name)
    notice = NOTICE if age < 18 else None
    policy.attach(user, policy.registration_record(_ticket(age), notice))
    db.session.commit()
    return user


# ---- shipping state ---------------------------------------------------------

def test_teen_ai_pin_ships_closed():
    assert policy.APPROVED_TR_MINOR_TRAINING_AI_VERSION is None
    assert policy.tr_minor_ai_open() is False
    assert policy.TR_MINOR_AI_FEATURES == frozenset({"training_plan"})


def test_closed_pin_refuses_a_registered_teen_even_with_the_adult_cohort_open(
        app, make_user, monkeypatch):
    monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", NOTICE)
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", policy.VERSION)
    teen = _user(make_user, 15, "teenclosed")
    with pytest.raises(ai_launch.consent.ConsentDenied) as refused:
        ai_launch.require_cohort(teen, "training_plan")
    assert refused.value.code == "AI_SERVICE_ELIGIBILITY_REQUIRED"


# ---- AI service eligibility is not purchase eligibility ---------------------

@pytest.mark.parametrize("age", [13, 15, 17])
def test_teen_is_ai_eligible_for_training_only_and_never_purchase_eligible(
        app, make_user, pins, age):
    teen = _user(make_user, age, f"teenai{age}")
    assert policy.purchase_eligible(teen, NOW()) is False
    assert policy.ai_service_eligible(teen, "training_plan", NOW()) is True
    for feature in ("coach", "nutrition_plan", "vision", "menu_extract", None, "other"):
        assert policy.ai_service_eligible(teen, feature, NOW()) is False
        with pytest.raises(ai_launch.consent.ConsentDenied):
            ai_launch.require_cohort(teen, feature)
    ai_launch.require_cohort(teen, "training_plan")


def test_adult_ai_eligibility_is_unchanged(app, make_user, pins):
    adult = _user(make_user, 30, "adultai")
    assert policy.purchase_eligible(adult, NOW()) is True
    for feature in ("training_plan", "coach", "nutrition_plan", "vision", "menu_extract", None):
        assert policy.ai_service_eligible(adult, feature, NOW()) is True


@pytest.mark.parametrize("change", ["notice_pin", "profile_age_18", "profile_age_none",
                                    "foreign_teen", "future_declaration"])
def test_teen_ai_fails_closed(app, make_user, pins, monkeypatch, change):
    if change == "foreign_teen":
        teen = make_user("teengb")
        # GB 13-17 is screen_passed but never a TR minor record.
        policy.attach(teen, policy.registration_record(_ticket(16, "GB")))
        db.session.commit()
    else:
        teen = _user(make_user, 16, "teenfail")
    if change == "notice_pin":
        monkeypatch.setattr(policy, "APPROVED_TR_MINOR_NOTICE_VERSION", None)
    elif change == "profile_age_18":
        teen.age = 18
    elif change == "profile_age_none":
        teen.age = None
    now = NOW()
    if change == "future_declaration":
        now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert policy.ai_service_eligible(teen, "training_plan", now) is False


def test_allocation_maps_only_the_training_ledger_family(app, make_user, pins, monkeypatch):
    from app.services.ai_allowances import launch_policy
    monkeypatch.setattr(launch_policy, "installed_policy", lambda: object())
    teen = _user(make_user, 14, "teenalloc")
    ai_launch.check_allocation(teen, "training_generation")
    for family in ("coach", "nutrition_generation", "pump_check", "qr_menu", None):
        with pytest.raises(ai_launch.consent.ConsentDenied):
            ai_launch.check_allocation(teen, family)


def test_purchase_path_still_refuses_the_teen(app, make_user, pins):
    from app.services import subscriptions
    teen = _user(make_user, 16, "teenbuy")
    assert subscriptions._purchase_eligible(teen, NOW()) is False


# ---- youth boundary ---------------------------------------------------------

def _prefs(**changes):
    from app.services.training_generation.preference_contract import parse_canonical_preferences
    return parse_canonical_preferences({**CANONICAL, **changes})


@pytest.mark.parametrize("changes", [{"odak_hedef": "yag_yakimi"},
                                     {"antrenman_tarzi": "powerlifting"}])
def test_minor_refuses_adult_body_composition_or_maximal_preferences(changes):
    prefs = _prefs(**changes)
    with pytest.raises(PreferenceContractError) as refused:
        youth_safety.require_supported(15, prefs)
    assert refused.value.public_code == CODE_UNSUPPORTED
    assert youth_safety.require_supported(30, prefs) is prefs


def test_effective_age_is_the_lowest_known_age():
    assert youth_safety.effective_age(18, 15) == 15
    assert youth_safety.effective_age(None, 16) == 16
    assert youth_safety.effective_age(None, "17") is None


def test_directive_is_minor_only_and_localized():
    assert youth_safety.directive(30, "tr") == youth_safety.directive(None, "en") == ""
    assert "GENÇ KULLANICI" in youth_safety.directive(13, "tr")
    assert "YOUTH SAFETY" in youth_safety.directive(17, "en")


def _plan(**exercise):
    plan = _provider_document("ex_barbell_back_squat")
    for day in plan["program"]:
        for item in day["egzersizler"]:
            item.update(exercise)
    return plan


@pytest.mark.parametrize("exercise", [
    {"set": 5}, {"tekrar": "3-5"}, {"tekrar": "1"}, {"not": "RPE 9, heavy"},
    {"not": "Work up to a 1RM"}, {"not": "Tükenişe kadar devam et"},
    {"not": "Kalori açığı için hızlı yap"}, {"not": "Max effort single"},
    {"not": "RPE 7-8"}, {"not": "RPE: 9"}, {"not": "RPE ~8"}, {"not": "RIR 0"},
    {"not": "Tükenmeye yakın bitir"}, {"tekrar": "AMRAP"}, {"tekrar": "Tükenişe kadar"},
    {"tekrar": "2x4"}, {"not": "Tükenişe kadar çalışma"},
    {"not": "Not: son seti tükenişe kadar yap"}, {"not": "Go to failure with no rest"},
    {"not": "Max reps on the last set"}, {"not": "Maksimum tekrar yap"}, {"tekrar": "Max"},
    {"not": "RIR 2"}, {"not": "RPE 8/10"},
])
def test_minor_output_outside_the_boundary_is_semantically_invalid(exercise, caplog):
    plan = _plan(**exercise)
    with pytest.raises(SemanticInvalidError):
        youth_safety.validate_plan(15, plan)
    line, = [r.getMessage() for r in caplog.records if "YOUTH_BOUNDARY" in r.getMessage()]
    assert line.startswith("[TRAINING][YOUTH_BOUNDARY] outcome=rejected rule=")
    assert "15" not in line and "squat" not in line.lower()
    assert youth_safety.validate_plan(30, plan) is plan  # adults unchanged


@pytest.mark.parametrize("exercise", [
    {}, {"tekrar": "30 sn"}, {"not": "RPE 7, kontrollü"}, {"tekrar": "6-8"}, {"set": 4},
    {"tekrar": "3x10"}, {"tekrar": "12 (her bacak)"}, {"not": "Do not go to failure"},
    {"not": "Tükenişe kadar gitmeyin"}, {"not": "1RM testi yok"}, {"not": "Diyetisyene danış"},
    {"not": "Maksimum tekrar 12"}, {"not": "RIR 3, RPE 6-7"}, {"tekrar": "45 saniye"},
    {"not": "RPE 7/10 hissettir"}, {"not": "RPE 6/10"}, {"not": "Not: sırtını düz tut"},
    {"not": "Never train to failure"}, {"not": "max rep range 12"},
])
def test_minor_output_inside_the_boundary_passes(exercise):
    plan = _plan(**exercise)
    assert youth_safety.validate_plan(16, plan) is plan


def test_preference_contract_omits_refused_choices_for_a_minor_only():
    from app.services.mobile_training import preference_contract
    adult, minor = preference_contract()["fields"], preference_contract(minor=True)["fields"]
    assert "powerlifting" in adult["antrenman_tarzi"]["choices"]
    assert "yag_yakimi" in adult["odak_hedef"]["choices"]
    assert "powerlifting" not in minor["antrenman_tarzi"]["choices"]
    assert "yag_yakimi" not in minor["odak_hedef"]["choices"]
    assert {k: v for k, v in adult.items() if k not in {"antrenman_tarzi", "odak_hedef"}} == {
        k: v for k, v in minor.items() if k not in {"antrenman_tarzi", "odak_hedef"}}
