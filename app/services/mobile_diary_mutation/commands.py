"""Strict, transport-independent mobile diary mutation commands."""
from dataclasses import dataclass

from app.services.mobile_nutrition.serialization import SLOT_BY_MEAL_LABEL


SLOT_LABELS = {
    slot: meal_label for meal_label, slot in SLOT_BY_MEAL_LABEL.items()
}


class InvalidDiaryMutation(ValueError):
    pass


@dataclass(frozen=True)
class SetSlotCommand:
    slot: str


def parse_mutation_command(data):
    if not isinstance(data, dict) or set(data) != {"operation", "slot"}:
        raise InvalidDiaryMutation
    if data.get("operation") != "set_slot":
        raise InvalidDiaryMutation
    slot = data.get("slot")
    if not isinstance(slot, str) or slot not in SLOT_LABELS:
        raise InvalidDiaryMutation
    return SetSlotCommand(slot=slot)
