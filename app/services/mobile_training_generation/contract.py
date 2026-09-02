"""Strict request identity for the native Training plan-generation command."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from app.services.training_generation.capability import require_supported
from app.services.training_generation.models import TrainingPreferences
from app.services.training_generation.preference_contract import (
    BODY_FOCUS_VALUES,
    CARDIO_INTENSITIES,
    CARDIO_TYPES,
    EQUIPMENT_VALUES,
    FOCUS_VALUES,
    STYLE_RULE_KEYS,
    PreferenceContractError,
    parse_canonical_preferences,
)

from .errors import InvalidIdempotencyKey, InvalidPlanRequest


REQUEST_FIELDS = frozenset({
    "gun_sayisi",
    "ekipman",
    "odak",
    "sure",
    "kardiyo_tipi",
    "kardiyo_gun",
    "kardiyo_sure",
    "kardiyo_yogunluk",
    "antrenman_tarzi",
    "odak_hedef",
    "injuries",
})
_INTEGER_FIELDS = frozenset({
    "gun_sayisi", "sure", "kardiyo_gun", "kardiyo_sure",
})
_TOKEN_FIELDS = {
    "ekipman": EQUIPMENT_VALUES,
    "odak": BODY_FOCUS_VALUES,
    "kardiyo_tipi": CARDIO_TYPES,
    "kardiyo_yogunluk": CARDIO_INTENSITIES,
    "antrenman_tarzi": frozenset(STYLE_RULE_KEYS),
    "odak_hedef": FOCUS_VALUES,
}
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_FINGERPRINT_DOMAIN = b"axisai:training-plan-generation:v1\0"
_INJURIES_MAX = 2_000


@dataclass(frozen=True)
class NativePlanRequest:
    preferences: TrainingPreferences
    normalized: dict[str, object]
    fingerprint: str


def parse_idempotency_key(raw: object) -> str:
    """Validate an opaque key without normalizing or exposing its contents."""
    if not isinstance(raw, str) or _KEY_RE.fullmatch(raw) is None:
        raise InvalidIdempotencyKey("idempotency key is malformed")
    return raw


def _validate_native_types(payload: dict) -> None:
    for field in _INTEGER_FIELDS:
        if type(payload[field]) is not int:
            raise InvalidPlanRequest("integer preference has the wrong type")
    for field, allowed in _TOKEN_FIELDS.items():
        raw = payload[field]
        if not isinstance(raw, str) or raw.strip().lower() not in allowed:
            raise InvalidPlanRequest("token preference is not canonical")
    injuries = payload["injuries"]
    if not isinstance(injuries, str):
        raise InvalidPlanRequest("injuries must be a string")
    cleaned = injuries.strip()
    if len(cleaned) > _INJURIES_MAX:
        raise InvalidPlanRequest("injuries exceeds its bound")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in cleaned):
        raise InvalidPlanRequest("injuries contains forbidden characters")


def _semantic_fingerprint(normalized: dict[str, object]) -> str:
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(_FINGERPRINT_DOMAIN + encoded).hexdigest()


def parse_native_request(raw: object) -> NativePlanRequest:
    """Parse one exact native request through canonical Training authorities."""
    if not isinstance(raw, dict) or isinstance(raw, bool):
        raise InvalidPlanRequest("request body must be an object")
    if set(raw) != REQUEST_FIELDS:
        raise InvalidPlanRequest("request fields do not match the contract")
    _validate_native_types(raw)
    try:
        preferences = parse_canonical_preferences(raw, stored_injuries="")
    except PreferenceContractError as error:
        if error.status != "invalid":
            raise
        raise InvalidPlanRequest("preference value is invalid") from None
    require_supported(preferences)
    normalized = asdict(preferences)
    return NativePlanRequest(
        preferences=preferences,
        normalized=normalized,
        fingerprint=_semantic_fingerprint(normalized),
    )
