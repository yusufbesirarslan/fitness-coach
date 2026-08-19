"""Canonical training-generation preference contract.

Allow-lists and typed failures live here so HTTP, the generator, and tests
share one vocabulary. Capability (supported vs unsupported vs conflicting)
is evaluated separately in ``capability.py`` after a request has parsed.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.services.training_generation.models import TrainingPreferences


CODE_INVALID = "TRAINING_PLAN_INVALID_PREFERENCE"
CODE_UNSUPPORTED = "TRAINING_PLAN_UNSUPPORTED_CONFIGURATION"
CODE_CONFLICTING = "TRAINING_PLAN_CONFLICTING_PREFERENCES"
CODE_GENERATION_UNAVAILABLE = "TRAINING_PLAN_GENERATION_UNAVAILABLE"
CODE_GENERATION_INVALID = "TRAINING_PLAN_GENERATION_INVALID"
CODE_NO_SESSION = "TRAINING_PLAN_NO_SESSION"

I18N_INVALID = "plan.contract.invalid_preference"
I18N_UNSUPPORTED = "plan.contract.unsupported_configuration"
I18N_CONFLICTING = "plan.contract.conflicting_preferences"
I18N_NO_SESSION = "plan.no_session"
I18N_GENERATION_INVALID = "plan.gen_failed"
I18N_GENERATION_UNAVAILABLE = "route.plan_failed"

# UI tokens the product actually exposes. Aliases map onto these, never onto
# a silent General fallback.
STYLE_INPUT_ALIASES = {
    "genel": "genel",
    "general": "genel",
    "general_fitness": "genel",
    "fonksiyonel": "fonksiyonel",
    "functional": "fonksiyonel",
    "bodybuilding": "bodybuilding",
    "powerlifting": "powerlifting",
    "crossfit": "crossfit",
    "calisthenics": "calisthenics",
}

STYLE_RULE_KEYS = {
    "genel": "general_fitness",
    "fonksiyonel": "functional",
    "bodybuilding": "bodybuilding",
    "powerlifting": "powerlifting",
    "crossfit": "crossfit",
    "calisthenics": "calisthenics",
}

FOCUS_VALUES = frozenset({
    "genel", "guc", "kondisyon", "kas_kutlesi", "yag_yakimi", "esneklik",
})
EQUIPMENT_VALUES = frozenset({"spor_salonu", "ev", "minimal"})
BODY_FOCUS_VALUES = frozenset({
    "tum_vucut", "ust_vucut", "sirt", "alt_vucut", "core",
})
CARDIO_TYPES = frozenset({
    "yok", "kosu", "bisiklet", "yuzme", "ip_atlama", "yuruyus", "karisik",
})
CARDIO_INTENSITIES = frozenset({"dusuk", "orta", "yuksek", "karisik"})
DAY_COUNTS = frozenset({3, 4, 5, 6})
DURATIONS = frozenset({30, 45, 60, 90})
CARDIO_DAYS = frozenset({0, 1, 2, 3, 4, 5, 6})
CARDIO_DURATIONS = frozenset({15, 20, 30, 45})

_ASSET_ROOT = Path(__file__).resolve().parents[1] / "training_assets"


class PreferenceContractError(ValueError):
    """Deterministic, non-retryable preference/capability failure."""

    def __init__(
        self,
        *,
        status: str,
        public_code: str,
        reason: str,
        i18n_key: str,
        http_status: int = 422,
    ):
        super().__init__(reason)
        self.status = status
        self.public_code = public_code
        self.reason = reason
        self.i18n_key = i18n_key
        self.http_status = http_status
        self.retryable = False

    @classmethod
    def invalid(cls, reason: str) -> "PreferenceContractError":
        return cls(
            status="invalid",
            public_code=CODE_INVALID,
            reason=reason,
            i18n_key=I18N_INVALID,
        )

    @classmethod
    def unsupported(cls, reason: str) -> "PreferenceContractError":
        return cls(
            status="unsupported",
            public_code=CODE_UNSUPPORTED,
            reason=reason,
            i18n_key=I18N_UNSUPPORTED,
        )

    @classmethod
    def conflicting(cls, reason: str) -> "PreferenceContractError":
        return cls(
            status="conflicting",
            public_code=CODE_CONFLICTING,
            reason=reason,
            i18n_key=I18N_CONFLICTING,
        )

    def to_body(self, translate) -> dict:
        return {
            "error": translate(self.i18n_key),
            "code": self.public_code,
            "retryable": False,
        }


def canonical_style(style: str) -> str:
    """Map a recognized style token to the style-rules key.

    Unknown values raise. They must never become ``general_fitness``.
    """
    key = (style or "").strip().lower()
    ui = STYLE_INPUT_ALIASES.get(key)
    if ui is None:
        raise PreferenceContractError.invalid("UNKNOWN_PROGRAM_STYLE")
    return STYLE_RULE_KEYS[ui]


@lru_cache(maxsize=1)
def _goals() -> dict:
    with (_ASSET_ROOT / "rules" / "goals.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def load_focus_directive(odak_hedef: str) -> str:
    return _goals().get(odak_hedef, "")


def _missing(data: dict, field):
    return field not in data or data.get(field) is None


def _require_token(data: dict, field: str, allowed: dict | frozenset, default: str) -> str:
    if _missing(data, field):
        return default
    raw = data.get(field)
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise PreferenceContractError.invalid(f"INVALID_{field.upper()}")
    token = raw.strip().lower()
    if isinstance(allowed, dict):
        mapped = allowed.get(token)
        if mapped is None:
            raise PreferenceContractError.invalid(f"UNKNOWN_{field.upper()}")
        return mapped
    if token not in allowed:
        raise PreferenceContractError.invalid(f"UNKNOWN_{field.upper()}")
    return token


def _require_int_choice(data: dict, field: str, allowed: frozenset[int], default: int) -> int:
    if _missing(data, field):
        return default
    raw = data.get(field)
    if isinstance(raw, bool):
        raise PreferenceContractError.invalid(f"INVALID_{field.upper()}")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise PreferenceContractError.invalid(f"INVALID_{field.upper()}") from None
    if isinstance(raw, str) and str(value) != raw.strip():
        raise PreferenceContractError.invalid(f"INVALID_{field.upper()}")
    if value not in allowed:
        raise PreferenceContractError.invalid(f"INVALID_{field.upper()}")
    return value


def parse_canonical_preferences(data: dict, stored_injuries: str = "") -> TrainingPreferences:
    """Allow-list parse. Does not evaluate capability."""
    payload = data if isinstance(data, dict) else {}
    return TrainingPreferences(
        gun_sayisi=_require_int_choice(payload, "gun_sayisi", DAY_COUNTS, 3),
        ekipman=_require_token(payload, "ekipman", EQUIPMENT_VALUES, "spor_salonu"),
        odak=_require_token(payload, "odak", BODY_FOCUS_VALUES, "tum_vucut"),
        sure=_require_int_choice(payload, "sure", DURATIONS, 45),
        kardiyo_tipi=_require_token(payload, "kardiyo_tipi", CARDIO_TYPES, "yok"),
        kardiyo_gun=_require_int_choice(payload, "kardiyo_gun", CARDIO_DAYS, 0),
        kardiyo_sure=_require_int_choice(payload, "kardiyo_sure", CARDIO_DURATIONS, 20),
        kardiyo_yogunluk=_require_token(
            payload, "kardiyo_yogunluk", CARDIO_INTENSITIES, "orta"),
        antrenman_tarzi=_require_token(
            payload, "antrenman_tarzi", STYLE_INPUT_ALIASES, "genel"),
        odak_hedef=_require_token(payload, "odak_hedef", FOCUS_VALUES, "genel"),
        injuries=_injuries(payload, stored_injuries),
    )


def _injuries(payload: dict, stored_injuries: str) -> str:
    if "injuries" not in payload or payload.get("injuries") is None:
        return (stored_injuries or "").strip()
    raw = payload.get("injuries")
    if not isinstance(raw, str):
        raise PreferenceContractError.invalid("INVALID_INJURIES")
    return raw.strip() or (stored_injuries or "").strip()
