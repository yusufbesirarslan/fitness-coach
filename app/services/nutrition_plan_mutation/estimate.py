"""F-3 nutrient recalculation: whole-meal re-estimation, server side.

The plan stores macros per MEAL, never per item, so after an item edit the old
item's share is unknown. The approved policy (F-3) therefore re-estimates the
WHOLE edited meal through the existing independent server estimator and never
computes "old meal - old item + new item". Daily totals are then the sum of
the canonical meals. No FatSecret data is involved: the estimator is the
server's own text estimator (``ai_nutrition._estimate_macros_llm``).

Zero writes on a failed or ambiguous estimate: every item must come back with
finite, in-bound values whose energy is consistent with their macros, and
every meal must carry all four values for the totals to be recomputed.
Anything else raises ``EstimateUnavailable`` before the caller writes.

The estimator is injected (``estimate_items(names) -> {name: macros}``), so
this module stays provider-free; the caller decides under which D1 operation
and allowance it runs.
"""
import math

from app.services import ai_consent

from app.services.nutrition_plan_schema import (
    ESTIMATED_KEY,
    ITEMS_KEY,
    MEAL_KEYS,
    MEAL_MACRO_KEYS,
    TOTAL_KEYS,
)

from .commands import normalized
from .errors import EstimateUnavailable

ESTIMATOR_ID = "server_text_estimator/v1"

_WIRE = (("calories", "kalori"), ("protein", "protein"),
         ("carbs", "karb"), ("fat", "yag"))
# Per-item plausibility bounds. Not physiology: a guard against a wrong unit
# or a hallucinated number becoming a saved plan value.
_ITEM_MAX = {"calories": 3000.0, "protein": 300.0, "carbs": 500.0, "fat": 300.0}
# Atwater consistency: 4/4/9 kcal per gram, with generous tolerance.
_ENERGY_TOLERANCE_KCAL = 60.0
_ENERGY_TOLERANCE_RATIO = 0.25


#: The D1 purpose the estimate runs under: the same purpose the edited plan
#: was generated under, so ``apply_prepared_edit``'s ``nutrition_plan`` fence
#: and the provider gate agree on ONE operation.
ESTIMATE_FEATURE = "nutrition_plan"


def server_estimator(names):
    """The production estimator. The caller runs it inside the owner's
    ``nutrition_plan`` D1 operation (``ai_consent.operation_scope``); a D1
    refusal propagates as ``ConsentDenied``, never as "no estimate"."""
    from app.services.ai_nutrition import _estimate_macros_llm
    return _estimate_macros_llm(list(names), feature=ESTIMATE_FEATURE)


def _value(raw, key):
    value = raw.get(key) if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EstimateUnavailable("estimate is missing a value")
    value = float(value)
    if not math.isfinite(value) or value < 0 or value > _ITEM_MAX[key]:
        raise EstimateUnavailable("estimate value out of bounds")
    return value


def _checked(raw):
    macros = {key: _value(raw, key) for key, _ in _WIRE}
    if macros["calories"] <= 0:
        raise EstimateUnavailable("estimate has no energy")
    energy = 4 * macros["protein"] + 4 * macros["carbs"] + 9 * macros["fat"]
    tolerance = max(_ENERGY_TOLERANCE_KCAL,
                    _ENERGY_TOLERANCE_RATIO * macros["calories"])
    if abs(energy - macros["calories"]) > tolerance:
        raise EstimateUnavailable("estimate energy is inconsistent")
    return macros


def estimate_meal(items, estimate_items):
    """Per-item estimates for every item of one meal, in item order.

    Each distinct item (by normalized text) is estimated once; every item must
    resolve, or nothing is returned.
    """
    distinct = {}
    for item in items:
        distinct.setdefault(normalized(item), item)
    try:
        answer = estimate_items(list(distinct.values()))
    except ai_consent.ConsentDenied:
        raise  # a consent decision, typed by D1 — not an estimate failure
    except Exception as error:  # noqa: BLE001 — any estimator fault = no write
        raise EstimateUnavailable("estimator failed") from error
    if not isinstance(answer, dict):
        raise EstimateUnavailable("estimator returned no estimate")
    by_key = {normalized(k): v for k, v in answer.items() if isinstance(k, str)}
    resolved = []
    for item in items:
        raw = by_key.get(normalized(item))
        if raw is None:
            raise EstimateUnavailable("an item could not be estimated")
        resolved.append((item, _checked(raw)))
    return resolved


def recompute(document, meal_key, estimates):
    """Edited meal values from the estimate, daily totals from the meals.

    Returns ``(document, provenance)``. Rounded to whole numbers, the form the
    generator writes. A meal without all four values makes the totals unknown,
    which is ambiguous: refused rather than written as a partial sum.
    """
    meal = document[meal_key]
    for wire, key in _WIRE:
        meal[key] = int(round(sum(m[wire] for _, m in estimates)))
    meal[ESTIMATED_KEY] = True

    totals = dict.fromkeys(MEAL_MACRO_KEYS, 0)
    for slot in MEAL_KEYS:
        if slot not in document:
            continue
        other = document[slot]
        for key in MEAL_MACRO_KEYS:
            value = other.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EstimateUnavailable("another meal has no values to total")
            totals[key] += value
    for key, total_key in zip(MEAL_MACRO_KEYS, TOTAL_KEYS):
        document[total_key] = int(round(totals[key]))

    provenance = {
        "source": ESTIMATOR_ID,
        "meal": meal_key,
        "method": "whole_meal_reestimation",
        "items": [{"item": item, **{w: round(m[w], 1) for w, _ in _WIRE}}
                  for item, m in estimates],
        "item_count": len(meal[ITEMS_KEY]),
    }
    return document, provenance
