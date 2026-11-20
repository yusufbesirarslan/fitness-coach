"""The persisted nutrition-plan document — one closed schema, enforced on write.

``POST /nutrition-plan/save`` used to accept whatever JSON a client sent,
``GET /nutrition-plan/active`` returned it verbatim, and ``static/nutrition.js``
interpolated several of those fields straight into ``innerHTML``. The CSP stops
script execution, so this was never active XSS — but ``style-src-attr
'unsafe-inline'`` is still on (dynamic width/colour attributes need it), which
makes a persisted ``<div style="position:fixed;inset:0">`` a working UI-redress
primitive. Stored markup was the real exposure, and an unbounded document was
the second one.

The vocabulary below is not invented: it is exactly what the application emits.
``/nutrition-plan`` prompts the provider for a fixed Turkish key set (the keys
are the canonical contract in every locale — only the values are translated),
and ``selectPlan()`` in ``static/nutrition.js`` forwards ONE element of
``planlar`` verbatim as ``plan``. Everything here is that element:

    {"isim": "Plan A",
     "kahvalti"|"ogle"|"aksam"|"ara_ogun": {
         "yemekler": ["Yumurta - 3 adet", ...],
         "kalori": 420, "protein": 28, "karb": 35, "yag": 18},
     "toplam_kalori": 1477, "toplam_protein": 136,
     "toplam_karb": 123, "toplam_yag": 44}

Two deliberate non-goals:

* **Names are not sanitised here.** ``isim`` and the ``yemekler`` entries are
  free text and legitimately contain ``&`` or an apostrophe. Escaping is the
  render-side control (``esc()``); a second, weaker filter at this boundary
  would buy nothing but false confidence. What this schema does guarantee is
  that free text can only ever land in those two slots — every field the page
  interpolates WITHOUT escaping is numeric here, so it can only render digits.
* **Reads are untouched.** Rows written before this boundary existed are still
  in the database and must keep loading, so validation happens on write only
  and no migration is required.
"""
from __future__ import annotations

import json
import math
import re

MEAL_KEYS = ("kahvalti", "ogle", "aksam", "ara_ogun")
MEAL_MACRO_KEYS = ("kalori", "protein", "karb", "yag")
TOTAL_KEYS = ("toplam_kalori", "toplam_protein", "toplam_karb", "toplam_yag")
NAME_KEY = "isim"
ITEMS_KEY = "yemekler"

TOP_LEVEL_KEYS = frozenset((NAME_KEY,) + MEAL_KEYS + TOTAL_KEYS)
MEAL_OBJECT_KEYS = frozenset((ITEMS_KEY,) + MEAL_MACRO_KEYS)

# Bounds. They are not physiology, they are document bounds: large enough that
# no plan the app can generate touches them, small enough that the row, the
# JSON response and the rendered page all stay bounded by construction.
NAME_MAX = 120
ITEM_MAX = 200
ITEMS_MAX = 30
VALUE_MIN = 0
VALUE_MAX = 100_000
DOCUMENT_MAX_BYTES = 16 * 1024

# Control characters have no place in a food name and every place in a log
# injection or a mangled render. C0 plus DEL; newline and tab included, because
# a meal item is a single line by construction.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Plain decimal only. No exponent, no leading "+", no whitespace, no "400 kcal".
_DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")

CODE_NUTRITION_PLAN_INVALID = "nutrition_plan_invalid"
I18N_NUTRITION_PLAN_INVALID = "route.nutrition_plan_invalid"


class NutritionPlanInvalid(ValueError):
    """The submitted document is not a nutrition plan. Deterministic 400 JSON.

    One public code for every way to fail. The ``reason`` is for the server log
    only: returning which field and which rule it broke would turn the save
    endpoint into a schema oracle, and the caller already has the payload.
    """

    public_code = CODE_NUTRITION_PLAN_INVALID
    i18n_key = I18N_NUTRITION_PLAN_INVALID
    http_status = 400
    retryable = False

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def to_body(self, translate) -> dict:
        return {
            "error": translate(self.i18n_key),
            "code": self.public_code,
            "retryable": self.retryable,
        }


def _text(value, limit, what):
    if not isinstance(value, str):
        raise NutritionPlanInvalid("%s is not a string" % what)
    if not value or len(value) > limit:
        raise NutritionPlanInvalid("%s length out of range" % what)
    if _CONTROL.search(value):
        raise NutritionPlanInvalid("%s has control characters" % what)
    return value


def _number(value, what):
    """A finite, in-range number.

    ``int``/``float`` pass through with their type intact — ``1477`` must not
    come back as ``1477.0``, because the page prints the raw JSON value. A
    plain decimal *string* is coerced, and only that: the provider is an LLM
    and ``"420"`` is a benign shape it really emits, while coercion is also
    what disarms it — the persisted value becomes a number, so the unescaped
    macro slots in the UI can only ever render digits.
    """
    # bool is an int subclass, and True is not a macro.
    if isinstance(value, bool):
        raise NutritionPlanInvalid("%s is a boolean" % what)
    if isinstance(value, str):
        if not _DECIMAL.match(value):
            raise NutritionPlanInvalid("%s is not a number" % what)
        value = float(value)
        if value.is_integer():
            value = int(value)
    elif not isinstance(value, (int, float)):
        raise NutritionPlanInvalid("%s is not a number" % what)
    if not math.isfinite(value):
        raise NutritionPlanInvalid("%s is not finite" % what)
    if not VALUE_MIN <= value <= VALUE_MAX:
        raise NutritionPlanInvalid("%s out of range" % what)
    return value


def _meal(raw, key):
    if not isinstance(raw, dict):
        raise NutritionPlanInvalid("%s is not an object" % key)
    unknown = set(raw) - MEAL_OBJECT_KEYS
    if unknown:
        raise NutritionPlanInvalid("%s has unknown keys" % key)
    if not raw:
        # An empty object is not a meal. Accepting it would persist a slot the
        # UI renders as a heading with nothing under it, and that quick-add
        # already has to refuse at read time.
        raise NutritionPlanInvalid("%s is empty" % key)

    meal = {}
    if ITEMS_KEY in raw:
        items = raw[ITEMS_KEY]
        if not isinstance(items, list):
            raise NutritionPlanInvalid("%s.yemekler is not a list" % key)
        if len(items) > ITEMS_MAX:
            raise NutritionPlanInvalid("%s.yemekler too long" % key)
        meal[ITEMS_KEY] = [
            _text(item, ITEM_MAX, "%s.yemekler item" % key) for item in items]
    for macro in MEAL_MACRO_KEYS:
        if macro in raw:
            meal[macro] = _number(raw[macro], "%s.%s" % (key, macro))
    return meal


def validate_nutrition_plan_for_save(plan):
    """The canonical document to persist, or ``NutritionPlanInvalid``.

    The return value is REBUILT from validated fields rather than filtered in
    place, so "only known keys are persisted" is a property of the code path
    and not of a check somebody could later move.
    """
    if not isinstance(plan, dict) or isinstance(plan, bool):
        raise NutritionPlanInvalid("document is not an object")
    unknown = set(plan) - TOP_LEVEL_KEYS
    if unknown:
        raise NutritionPlanInvalid("document has unknown keys")

    document = {}
    if NAME_KEY in plan:
        document[NAME_KEY] = _text(plan[NAME_KEY], NAME_MAX, "isim")
    meals = 0
    for key in MEAL_KEYS:
        if key in plan:
            document[key] = _meal(plan[key], key)
            meals += 1
    if not meals:
        raise NutritionPlanInvalid("document has no meal")
    for key in TOTAL_KEYS:
        if key in plan:
            document[key] = _number(plan[key], key)

    encoded = json.dumps(document, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > DOCUMENT_MAX_BYTES:
        raise NutritionPlanInvalid("document too large")
    return document
