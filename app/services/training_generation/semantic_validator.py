"""Semantic validation of a structurally valid generated plan.

Answers: even though this object is a 7-day program, does it satisfy the
accepted PR2 request? Exercise identity and equipment truth are PR4.
"""
from __future__ import annotations

from app.services.training_generation.output_errors import SemanticInvalidError
from app.services.training_generation.plan_schema import MIN_TRAINING_EXERCISES
from app.services.training_generation.preference_contract import (
    DAY_COUNTS,
    canonical_style,
)


def _cardio_days(preferences) -> int:
    if preferences is None:
        return 0
    if preferences.kardiyo_tipi == "yok":
        return 0
    return int(preferences.kardiyo_gun or 0)


def _style_key(preferences) -> str:
    if preferences is None:
        return "general_fitness"
    return canonical_style(preferences.antrenman_tarzi)


def validate_plan_semantics(plan: dict, preferences=None) -> dict:
    program = plan["program"]
    training = [day for day in program if day["tip"] == "antrenman"]
    cardio = [day for day in program if day["tip"] == "kardiyo"]
    rest = [day for day in program if day["tip"] == "dinlenme"]

    if preferences is None:
        if len(training) not in DAY_COUNTS:
            raise SemanticInvalidError(
                f"toplam antrenman günü {sorted(DAY_COUNTS)} olmalı")
        if len(training) + len(cardio) > 7:
            raise SemanticInvalidError("haftalık gün tahsisi 7'yi aşamaz")
    else:
        expected_training = preferences.gun_sayisi
        expected_cardio = _cardio_days(preferences)
        if len(training) != expected_training:
            raise SemanticInvalidError(
                f"toplam antrenman günü {expected_training} olmalı")
        if len(cardio) != expected_cardio:
            raise SemanticInvalidError(
                f"toplam kardiyo günü {expected_cardio} olmalı")
        if len(rest) != 7 - expected_training - expected_cardio:
            raise SemanticInvalidError("dinlenme günü sayısı kanonik tahsise uymalı")

        requested = preferences.sure
        for day in training:
            if day["sure_dk"] < max(15, requested // 2) or day["sure_dk"] > requested * 2:
                raise SemanticInvalidError("antrenman süresi istenen süreyle uyumsuz")
        if expected_cardio:
            cardio_dur = preferences.kardiyo_sure
            for day in cardio:
                if day["sure_dk"] < max(10, cardio_dur // 2) or day["sure_dk"] > cardio_dur * 2:
                    raise SemanticInvalidError("kardiyo süresi istenen süreyle uyumsuz")

        # Style session-size only. Exercise names, equipment, and SBD/%1RM
        # identity cannot be proven without PR4 catalog authority.
        style = _style_key(preferences)
        minimum = MIN_TRAINING_EXERCISES.get(style, 1)
        for day in training:
            if len(day["egzersizler"]) < minimum:
                raise SemanticInvalidError(
                    f"{style} antrenman günü en az {minimum} egzersiz içermeli")

    for day in rest:
        if day["sure_dk"] > 45:
            raise SemanticInvalidError("dinlenme günü antrenman süresi taşıyamaz")

    return plan
