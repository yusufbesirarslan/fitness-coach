"""Pure C1 targeting and whole-meal arithmetic; never writes a saved plan.

F-3 (founder, 2026-11-18): absent reliable item contributions, re-estimate the
entire affected meal and require confirmation. Never subtract a guessed old
item from stored totals. Missing meal facts are unknown, never zero.
"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json

from app.services.nutrition_plan_schema import (
    DOCUMENT_MAX_BYTES, ITEMS_KEY, ITEM_MAX, ITEMS_MAX, MEAL_KEYS,
    MEAL_MACRO_KEYS, TOTAL_KEYS, NutritionPlanInvalid,
    validate_nutrition_plan_for_save,
)


class EditError(ValueError):
    """Internal bounded outcome; no raw document or target in the exception."""


@dataclass(frozen=True)
class Snapshot:
    # Exact bytes are preserved for the future byte-precondition undo journal.
    raw: str

    @property
    def digest(self):
        return hashlib.sha256(self.raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Edit:
    kind: str
    meal: str
    item: str
    replacement: str | None = None


@dataclass(frozen=True)
class MealTarget:
    snapshot: Snapshot
    edit: Edit
    items: tuple[str, ...]
    changed: bool


@dataclass(frozen=True)
class PreparedEdit:
    before: Snapshot
    after: Snapshot
    changed: bool
    requires_confirmation: bool
    # Metadata belongs in the future journal, outside the closed plan schema.
    nutrient_source: str | None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EditError("plan_unreadable")
        result[key] = value
    return result


def _document(snapshot):
    if not isinstance(snapshot, Snapshot) or not isinstance(snapshot.raw, str):
        raise EditError("plan_unreadable")
    if len(snapshot.raw.encode("utf-8")) > DOCUMENT_MAX_BYTES:
        raise EditError("plan_unreadable")
    try:
        document = json.loads(snapshot.raw, object_pairs_hook=_object)
        return validate_nutrition_plan_for_save(document)
    except (TypeError, ValueError, NutritionPlanInvalid, RecursionError, OverflowError):
        raise EditError("plan_unreadable") from None


def parse_snapshot(raw):
    snapshot = Snapshot(raw)
    _document(snapshot)
    return snapshot


def _item(value):
    if (not isinstance(value, str) or not value.strip()
            or len(value) > ITEM_MAX
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise EditError("invalid_item")
    return value


def _identity(value):
    # No fuzzy matching, aliases, substring guessing or index supplied by a model.
    return value.strip().casefold()


def target_edit(snapshot, edit):
    """Resolve exactly one item before spending an estimation call.

    An add of an existing item is refused; a replacement with identical text
    is a no-op and needs no estimate. Removal cannot silently empty a meal.
    The returned items are immutable, detached from the stored document.
    """
    if (not isinstance(edit, Edit) or not isinstance(edit.kind, str)
            or edit.kind not in {"replace", "add", "remove"}):
        raise EditError("invalid_edit")
    if edit.meal not in MEAL_KEYS:
        raise EditError("invalid_meal")
    _item(edit.item)
    if edit.kind == "replace":
        _item(edit.replacement)
    elif edit.replacement is not None:
        raise EditError("invalid_edit")
    document = _document(snapshot)
    meal = document.get(edit.meal)
    if meal is None:
        raise EditError("meal_not_found")
    items = meal.get(ITEMS_KEY)
    if not items:
        raise EditError("meal_unreadable")
    for item in items:
        _item(item)
    matches = [i for i, item in enumerate(items)
               if _identity(item) == _identity(edit.item)]
    if len(matches) > 1:
        raise EditError("ambiguous_item")
    updated = list(items)
    if edit.kind == "add":
        if matches:
            raise EditError("item_already_present")
        if len(items) >= ITEMS_MAX:
            raise EditError("too_many_items")
        updated.append(edit.item)
    else:
        if not matches:
            raise EditError("item_not_found")
        index = matches[0]
        if edit.kind == "remove":
            if len(items) == 1:
                raise EditError("empty_meal")
            updated.pop(index)
        else:
            if any(i != index and _identity(item) == _identity(edit.replacement)
                   for i, item in enumerate(items)):
                raise EditError("item_already_present")
            updated[index] = edit.replacement
    return MealTarget(snapshot, edit, tuple(updated), updated != items)


def finish_edit(target, estimated_meal):
    """Prepare the complete new document from a trusted server meal estimate.

    ``estimated_meal`` is exactly {kalori, protein, karb, yag} for ALL target
    items together. It must come from a future consent-gated independent
    estimator adapter, never directly from request/tool arguments. This pure
    function establishes neither provenance nor authorization to persist.
    """
    if not isinstance(target, MealTarget):
        raise EditError("invalid_target")
    # Re-derive rather than trusting caller-constructed items/changed fields.
    canonical = target_edit(target.snapshot, target.edit)
    if target != canonical:
        raise EditError("invalid_target")
    if not target.changed:
        return PreparedEdit(target.snapshot, target.snapshot, False, False, None)
    if (not isinstance(estimated_meal, dict)
            or set(estimated_meal) != set(MEAL_MACRO_KEYS)):
        raise EditError("estimate_unavailable")
    for value in estimated_meal.values():
        # The legacy estimator coalesces missing/invalid inputs to zero. This
        # adapter boundary must receive complete numeric facts, not that shape.
        if type(value) not in (int, float):
            raise EditError("estimate_unavailable")
    document = _document(target.snapshot)
    updated = deepcopy(document)
    updated[target.edit.meal] = {
        ITEMS_KEY: list(target.items), **estimated_meal,
    }
    try:
        updated = validate_nutrition_plan_for_save(updated)
        for macro, total in zip(MEAL_MACRO_KEYS, TOTAL_KEYS):
            values = [updated[slot].get(macro) for slot in MEAL_KEYS
                      if slot in updated]
            if any(value is None for value in values):
                raise EditError("meal_totals_unavailable")
            # Decimal avoids accumulating binary float drift across meals.
            value = sum((Decimal(str(v)) for v in values), Decimal(0))
            updated[total] = int(value) if value == value.to_integral() else float(value)
        updated = validate_nutrition_plan_for_save(updated)
    except (NutritionPlanInvalid, OverflowError):
        raise EditError("estimate_unavailable") from None
    # No existing total is used in the calculation, consistent or otherwise.
    raw = json.dumps(updated, ensure_ascii=False, allow_nan=False)
    return PreparedEdit(target.snapshot, parse_snapshot(raw), True, True,
                        "independent_whole_meal_estimate")
