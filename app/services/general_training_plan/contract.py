"""Strict request identity for the non-AI general training-plan command.

The body is a closed key set naming a versioned template and its two
schedule parameters. Nothing about the person is accepted: no injuries, goal,
body metric, free text, history or client-authored plan. The parameters are
the canonical Training vocabularies (``gun_sayisi``/``ekipman``) restricted to
what the template supports.

The fingerprint domain differs from the AI command's, so one Idempotency-Key
can never mean both commands: reusing it across them is a conflict, never a
replay of the other command's result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.services.mobile_training_generation.errors import InvalidPlanRequest

from .template import SUPPORTED_DAYS, SUPPORTED_EQUIPMENT, TEMPLATE_ID


REQUEST_FIELDS = frozenset({"template", "gun_sayisi", "ekipman"})
_FINGERPRINT_DOMAIN = b"axisai:training-plan-general:v1\0"


@dataclass(frozen=True)
class GeneralPlanRequest:
    template: str
    days: int
    equipment: str
    fingerprint: str


def parse_general_request(raw: object) -> GeneralPlanRequest:
    """Parse one exact request; unsupported or extra input is refused, never coerced."""
    if not isinstance(raw, dict):
        raise InvalidPlanRequest("request body must be an object")
    if set(raw) != REQUEST_FIELDS:
        raise InvalidPlanRequest("request fields do not match the contract")
    template, days, equipment = raw["template"], raw["gun_sayisi"], raw["ekipman"]
    if not isinstance(template, str) or template != TEMPLATE_ID:
        raise InvalidPlanRequest("template is not supported")
    if type(days) is not int or days not in SUPPORTED_DAYS:
        raise InvalidPlanRequest("day count is not supported")
    if not isinstance(equipment, str) or equipment not in SUPPORTED_EQUIPMENT:
        raise InvalidPlanRequest("equipment context is not supported")
    normalized = {"template": template, "gun_sayisi": days, "ekipman": equipment}
    encoded = json.dumps(
        normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return GeneralPlanRequest(
        template=template,
        days=days,
        equipment=equipment,
        fingerprint=hashlib.sha256(_FINGERPRINT_DOMAIN + encoded).hexdigest(),
    )
