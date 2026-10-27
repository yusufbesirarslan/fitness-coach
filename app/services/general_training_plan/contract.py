"""Strict request identity for the non-AI general training-plan command.

The body is a closed key set naming a versioned template and its schedule
parameters: two for ``general_full_body_v1``, plus the four canonical cardio
preferences for ``general_full_body_cardio_v1``. Nothing about the person is
accepted: no injuries, goal, body metric, free text, history or
client-authored plan. The parameters are the canonical Training vocabularies
(``gun_sayisi``/``ekipman``/``kardiyo_*``) restricted to what each template
supports.

The fingerprint domain differs from the AI command's, so one Idempotency-Key
can never mean both commands: reusing it across them is a conflict, never a
replay of the other command's result.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services import general_plan_identity as identity
from app.services.mobile_training_generation.errors import InvalidPlanRequest


REQUEST_FIELDS = identity.V1_FIELDS
CARDIO_REQUEST_FIELDS = identity.CARDIO_FIELDS
_FINGERPRINT_DOMAIN = identity.FINGERPRINT_DOMAIN


@dataclass(frozen=True)
class GeneralCardio:
    """The cardio half of a ``general_full_body_cardio_v1`` request."""

    cardio_type: str
    days: int
    minutes: int
    intensity: str


@dataclass(frozen=True)
class GeneralPlanRequest:
    template: str
    days: int
    equipment: str
    fingerprint: str
    cardio: GeneralCardio | None = None


def _token(raw: dict, field: str, allowed) -> str:
    value = raw[field]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidPlanRequest(f"{field} is not supported")
    return value


def _integer(raw: dict, field: str, allowed) -> int:
    value = raw[field]
    if type(value) is not int or value not in allowed:
        raise InvalidPlanRequest(f"{field} is not supported")
    return value


def parse_general_request(raw: object) -> GeneralPlanRequest:
    """Parse one exact request; unsupported or extra input is refused, never coerced.

    The template names its own closed key set. A v1 body is exactly what it
    was before the cardio template existed; a cardio body must carry all four
    cardio preferences, with cardio actually chosen.
    """
    if not isinstance(raw, dict):
        raise InvalidPlanRequest("request body must be an object")
    template = raw.get("template")
    if not isinstance(template, str) or template not in identity.TEMPLATE_IDS:
        raise InvalidPlanRequest("template is not supported")
    expected = (CARDIO_REQUEST_FIELDS if template == identity.TEMPLATE_CARDIO_V1
                else REQUEST_FIELDS)
    if set(raw) != expected:
        raise InvalidPlanRequest("request fields do not match the contract")
    days = raw["gun_sayisi"]
    if type(days) is not int or days not in identity.STRENGTH_DAYS:
        raise InvalidPlanRequest("day count is not supported")
    equipment = raw["ekipman"]
    if not isinstance(equipment, str) or equipment not in identity.EQUIPMENT:
        raise InvalidPlanRequest("equipment context is not supported")
    normalized = {"template": template, "gun_sayisi": days, "ekipman": equipment}
    cardio = None
    if template == identity.TEMPLATE_CARDIO_V1:
        cardio = GeneralCardio(
            cardio_type=_token(raw, "kardiyo_tipi", identity.CARDIO_TYPES),
            days=_integer(raw, "kardiyo_gun", identity.cardio_day_choices(days)),
            minutes=_integer(raw, "kardiyo_sure", identity.CARDIO_DURATIONS),
            intensity=_token(raw, "kardiyo_yogunluk", identity.CARDIO_INTENSITIES),
        )
        normalized.update({
            "kardiyo_tipi": cardio.cardio_type,
            "kardiyo_gun": cardio.days,
            "kardiyo_sure": cardio.minutes,
            "kardiyo_yogunluk": cardio.intensity,
        })
    return GeneralPlanRequest(
        template=template,
        days=days,
        equipment=equipment,
        fingerprint=identity.fingerprint(normalized),
        cardio=cardio,
    )
