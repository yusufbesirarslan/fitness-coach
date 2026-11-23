"""Independent whole-meal fallback. No discovery/cache/FatSecret dependency.

Use the existing macro estimator's single batch/provider path, with strict
success rules. Every proposed food is re-estimated; no old nutrients enter it.
"""
from decimal import Decimal
import json
import math
import re

from app.services.nutrition_pipeline import check_serving, MAX_MEAL_TOTAL_KCAL, MAX_MEAL_TOTAL_MACRO_G, MAX_MEAL_TOTAL_FAT_G
from .document import EditError

MACROS = ("calories", "protein", "carbs", "fat")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EditError("estimate_unavailable")
        result[key] = value
    return result


def strict_response(raw, items):
    """No JSON repair, numeric coercion, fuzzy matching or absent-as-zero."""
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 32768:
            raise ValueError()
        data = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(data, dict) or set(data) != set(items):
            raise ValueError()
        for item in items:
            values = data[item]
            if not isinstance(values, dict) or set(values) != set(MACROS):
                raise ValueError()
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                   for v in values.values()):
                raise ValueError()
            if values["calories"] == 0 and any(values[m] > 0 for m in MACROS[1:]):
                raise ValueError()
            grams = validate_quantity(item)
            valid, flags, _ = check_serving({**values, "metric_serving_amount": grams})
            if not valid or flags:
                raise ValueError()
        return data
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise EditError("estimate_unavailable") from None


# Deliberately bounded initial grammar, not a portion guesser. Legacy strings
# without one explicit measured quantity must be clarified before an AI call.
_QUANTITY = re.compile(
    r"(?P<food>[^\d\n\r]+?)\s+-\s+(?P<amount>[0-9]{1,4}(?:[.,][0-9]{1,2})?)\s*"
    r"(?P<unit>g|gr|gram|grams|kg|ml|l|adet|piece|pieces)\Z", re.IGNORECASE)


def validate_quantity(item):
    match = _QUANTITY.fullmatch(item.strip())
    if not match or not any(c.isalpha() for c in match["food"]):
        raise EditError("item_quantity_unavailable")
    amount = Decimal(match["amount"].replace(",", "."))
    unit = match["unit"].casefold()
    ceiling = Decimal(30 if unit in {"adet", "piece", "pieces"} else
                      5 if unit in {"kg", "l"} else 5000)
    if amount <= 0 or amount > ceiling:
        raise EditError("item_quantity_unavailable")
    return float(amount * (1000 if unit == "kg" else 1)) if unit in {"g", "gr", "gram", "grams", "kg"} else None


def estimate_meal(items):
    if (not items or len(items) > 30 or any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in items)
            or len({x.strip().casefold() for x in items}) != len(items)):
        raise EditError("estimate_unavailable")
    for item in items:
        validate_quantity(item)
    from app.services.ai_nutrition import _estimate_macros_llm_batch
    values = _estimate_macros_llm_batch(list(items), strict=True)
    # Defense in depth: even a replaced adapter cannot skip validation.
    values = strict_response(json.dumps(values, allow_nan=False), items)
    totals = [sum((Decimal(str(values[x][m])) for x in items), Decimal(0)) for m in MACROS]
    if any(v > ceiling for v, ceiling in zip(totals, (
            MAX_MEAL_TOTAL_KCAL, MAX_MEAL_TOTAL_MACRO_G, MAX_MEAL_TOTAL_MACRO_G, MAX_MEAL_TOTAL_FAT_G))):
        raise EditError("estimate_unavailable")
    return {k: float(v) for k, v in zip(("kalori", "protein", "karb", "yag"), totals)}
