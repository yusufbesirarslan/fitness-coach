"""Deterministic, provider-independent generation capability matrix.

A parsed preference object is:

- supported — the 7-day schema can faithfully represent the request
- unsupported — recognized preferences AxisAI cannot currently combine
- conflicting — internally contradictory (e.g. more allocated days than 7)

Invalid input is rejected by the preference contract before this module runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.training_generation.models import TrainingPreferences
from app.services.training_generation.preference_contract import (
    PreferenceContractError,
    canonical_style,
)


STATUS_SUPPORTED = "supported"
STATUS_UNSUPPORTED = "unsupported"
STATUS_CONFLICTING = "conflicting"

# Styles the current 7-day {gun, tip, egzersizler} schema can actually express.
# CrossFit WODs / mixed-modality structure are not first-class fields.
SUPPORTED_EQUIPMENT_BY_STYLE = {
    "general_fitness": frozenset({"spor_salonu", "ev", "minimal"}),
    "bodybuilding": frozenset({"spor_salonu", "ev", "minimal"}),
    "powerlifting": frozenset({"spor_salonu"}),
    "calisthenics": frozenset({"spor_salonu", "ev", "minimal"}),
    "functional": frozenset({"spor_salonu", "ev", "minimal"}),
    "crossfit": frozenset(),
}


@dataclass(frozen=True)
class CapabilityResult:
    status: str
    reason: str

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUPPORTED


def evaluate_capability(preferences: TrainingPreferences) -> CapabilityResult:
    style = canonical_style(preferences.antrenman_tarzi)
    allowed_equipment = SUPPORTED_EQUIPMENT_BY_STYLE.get(style, frozenset())
    if style == "crossfit" or not allowed_equipment:
        return CapabilityResult(STATUS_UNSUPPORTED, "CROSSFIT_SCHEMA_UNSUPPORTED")
    if preferences.ekipman not in allowed_equipment:
        if style == "powerlifting":
            return CapabilityResult(
                STATUS_UNSUPPORTED, "POWERLIFTING_REQUIRES_GYM_EQUIPMENT")
        return CapabilityResult(
            STATUS_UNSUPPORTED, "STYLE_EQUIPMENT_UNSUPPORTED")

    cardio_days = preferences.kardiyo_gun if preferences.kardiyo_tipi != "yok" else 0
    if preferences.kardiyo_tipi == "yok" and preferences.kardiyo_gun > 0:
        return CapabilityResult(STATUS_CONFLICTING, "CARDIO_DAYS_WITHOUT_TYPE")
    if preferences.gun_sayisi + cardio_days > 7:
        return CapabilityResult(
            STATUS_CONFLICTING, "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS")
    return CapabilityResult(STATUS_SUPPORTED, "SUPPORTED")


def require_supported(preferences: TrainingPreferences) -> TrainingPreferences:
    result = evaluate_capability(preferences)
    if result.status == STATUS_UNSUPPORTED:
        raise PreferenceContractError.unsupported(result.reason)
    if result.status == STATUS_CONFLICTING:
        raise PreferenceContractError.conflicting(result.reason)
    return preferences
