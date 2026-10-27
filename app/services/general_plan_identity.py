"""Closed request identities of the non-AI general Training templates (LP21).

Neutral and standard-library only, so both the general command
(``general_training_plan.contract``) and the read-side origin projection
(``training_plan_origin``) can share one definition of "which request is
this" without the origin projection reaching the general-plan package, the
AI ledger store or a provider module.

Two versioned templates exist. Neither changes the other's meaning:

* ``general_full_body_v1`` — strength only: ``template``, ``gun_sayisi``,
  ``ekipman``. Its request body, fingerprint and content are unchanged.
* ``general_full_body_cardio_v1`` — the same strength week plus dedicated
  cardio days, adding the four canonical cardio preferences. Cardio is never
  "none" here: a strength-only week is the v1 template, not this one.

A receipt stores only the request fingerprint, never the template. Both
request spaces are small and closed, so the template is recovered by exact
fingerprint lookup — durable server evidence, never plan content. A general
receipt whose fingerprint names no request in either space claims nothing.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from itertools import product


TEMPLATE_V1 = "general_full_body_v1"
TEMPLATE_CARDIO_V1 = "general_full_body_cardio_v1"
TEMPLATE_IDS = (TEMPLATE_V1, TEMPLATE_CARDIO_V1)

STRENGTH_DAYS = frozenset({3, 4})
EQUIPMENT = frozenset({"ev", "minimal", "spor_salonu"})

# The canonical cardio vocabularies (``preference_contract``) minus "yok";
# parity is pinned by tests rather than imported, to keep this module neutral.
CARDIO_TYPES = frozenset({
    "kosu", "bisiklet", "yuzme", "ip_atlama", "yuruyus", "karisik",
})
CARDIO_DURATIONS = frozenset({15, 20, 30, 45})
CARDIO_INTENSITIES = frozenset({"dusuk", "orta", "yuksek", "karisik"})

# A non-personalized template always keeps at least one full rest day, so
# strength + cardio never fills the week (the AI matrix's ceiling is seven).
MIN_REST_DAYS = 1

V1_FIELDS = frozenset({"template", "gun_sayisi", "ekipman"})
CARDIO_FIELDS = V1_FIELDS | frozenset({
    "kardiyo_tipi", "kardiyo_gun", "kardiyo_sure", "kardiyo_yogunluk",
})

# One domain for both templates: the template id is inside the fingerprinted
# body, so the two request spaces cannot collide, and v1 fingerprints stay
# exactly what they were before the cardio template existed.
FINGERPRINT_DOMAIN = b"axisai:training-plan-general:v1\0"


def cardio_day_choices(strength_days: int) -> tuple[int, ...]:
    """The cardio day counts a strength week of this size can hold."""
    return tuple(range(1, 7 - MIN_REST_DAYS - strength_days + 1))


def fingerprint(normalized: dict) -> str:
    """The request identity of one already-validated, normalized body."""
    encoded = json.dumps(
        normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(FINGERPRINT_DOMAIN + encoded).hexdigest()


def request_space():
    """Every normalized request either template accepts, deterministically."""
    for days, equipment in product(sorted(STRENGTH_DAYS), sorted(EQUIPMENT)):
        yield {"template": TEMPLATE_V1, "gun_sayisi": days, "ekipman": equipment}
    for days, equipment in product(sorted(STRENGTH_DAYS), sorted(EQUIPMENT)):
        for cardio_type, cardio_days, minutes, intensity in product(
                sorted(CARDIO_TYPES), cardio_day_choices(days),
                sorted(CARDIO_DURATIONS), sorted(CARDIO_INTENSITIES)):
            yield {
                "template": TEMPLATE_CARDIO_V1,
                "gun_sayisi": days,
                "ekipman": equipment,
                "kardiyo_tipi": cardio_type,
                "kardiyo_gun": cardio_days,
                "kardiyo_sure": minutes,
                "kardiyo_yogunluk": intensity,
            }


@lru_cache(maxsize=1)
def _templates_by_fingerprint() -> dict[str, str]:
    return {fingerprint(body): body["template"] for body in request_space()}


def template_for_fingerprint(value) -> str | None:
    """The template a general receipt's fingerprint names, or None."""
    if not isinstance(value, str):
        return None
    return _templates_by_fingerprint().get(value)
