"""Typed meal-item edits and their pure application to a plan document.

Three narrow commands, no generic "set field". A meal item is free text
(``"Yumurta - 3 adet"``) with no identity of its own, so a target matches by
normalized text (case-folded, whitespace collapsed) within ONE meal slot; two
matches are ambiguous and refused, never resolved by position.

Pure: no database, no Flask, no provider. Values are validated with the
persisted schema's own bounds (``nutrition_plan_schema``), so an edit can only
produce text the save path would also accept.
"""
import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass

from app.services.nutrition_plan_schema import (
    ITEM_MAX,
    ITEMS_KEY,
    ITEMS_MAX,
    MEAL_KEYS,
)

from .errors import (
    AmbiguousMealItem,
    InvalidNutritionEdit,
    MealItemAlreadyPresent,
    MealItemNotFound,
    MealNotFound,
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
FINGERPRINT_VERSION = "nutrition-edit/v1"


@dataclass(frozen=True)
class ReplaceMealItemCommand:
    meal: str
    item: str
    new_item: str


@dataclass(frozen=True)
class RemoveMealItemCommand:
    meal: str
    item: str


@dataclass(frozen=True)
class AddMealItemCommand:
    meal: str
    new_item: str


NUTRITION_EDIT_COMMANDS = (
    ReplaceMealItemCommand, RemoveMealItemCommand, AddMealItemCommand)
_TYPES = {
    ReplaceMealItemCommand: "replace_meal_item",
    RemoveMealItemCommand: "remove_meal_item",
    AddMealItemCommand: "add_meal_item",
}


COMMAND_TYPES = frozenset(_TYPES.values())


def command_type(command):
    return _TYPES[type(command)]


def normalized(text):
    return _SPACE.sub(" ", text).strip().casefold()


def _item_text(value, what):
    if not isinstance(value, str):
        raise InvalidNutritionEdit("%s is not text" % what)
    if _CONTROL.search(value):
        raise InvalidNutritionEdit("%s has control characters" % what)
    value = _SPACE.sub(" ", value).strip()
    if not value or len(value) > ITEM_MAX:
        raise InvalidNutritionEdit("%s is out of bounds" % what)
    return value


def validate_command(command):
    """A normalized copy of ``command``, or ``InvalidNutritionEdit``."""
    if type(command) not in _TYPES:
        raise InvalidNutritionEdit("unsupported edit")
    if command.meal not in MEAL_KEYS:
        raise InvalidNutritionEdit("unknown meal slot")
    fields = asdict(command)
    for name in ("item", "new_item"):
        if name in fields:
            fields[name] = _item_text(fields[name], name)
    return type(command)(**fields)


def semantic_fingerprint(command):
    """Stable digest of the edit's meaning (normalized text, not raw bytes)."""
    fields = {k: (normalized(v) if k != "meal" else v)
              for k, v in sorted(asdict(command).items())}
    body = json.dumps([FINGERPRINT_VERSION, command_type(command), fields],
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _index_of(items, target):
    wanted = normalized(target)
    hits = [i for i, item in enumerate(items) if normalized(item) == wanted]
    if not hits:
        raise MealItemNotFound("no such item in this meal")
    if len(hits) > 1:
        raise AmbiguousMealItem("more than one item matches")
    return hits[0]


def apply_item_edit(document, command):
    """``(new_document, changed)``; ``document`` itself is never modified.

    Only the edited meal's item list changes here. Its nutrient values are
    recomputed by the caller from a whole-meal estimate (F-3), never inferred
    from the old values.
    """
    meal = document.get(command.meal)
    if not isinstance(meal, dict):
        raise MealNotFound("this plan has no such meal")
    items = meal.get(ITEMS_KEY)
    if not isinstance(items, list):
        raise MealNotFound("this meal has no item list")

    new_items = list(items)
    if isinstance(command, ReplaceMealItemCommand):
        index = _index_of(new_items, command.item)
        if normalized(command.new_item) == normalized(new_items[index]):
            return document, False
        if any(normalized(i) == normalized(command.new_item) for i in new_items):
            raise MealItemAlreadyPresent("the new item is already in this meal")
        new_items[index] = command.new_item
    elif isinstance(command, RemoveMealItemCommand):
        index = _index_of(new_items, command.item)
        if len(new_items) == 1:
            # A meal with no items has no estimate to recompute from; removing
            # the last item is a whole-meal decision, not an item edit.
            raise InvalidNutritionEdit("a meal keeps at least one item")
        del new_items[index]
    else:
        if any(normalized(i) == normalized(command.new_item) for i in new_items):
            raise MealItemAlreadyPresent("the item is already in this meal")
        if len(new_items) >= ITEMS_MAX:
            raise InvalidNutritionEdit("this meal is full")
        new_items.append(command.new_item)

    edited = copy.deepcopy(document)
    edited[command.meal][ITEMS_KEY] = new_items
    return edited, True
