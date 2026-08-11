"""Strict, transport-independent mobile diary mutation commands."""
from dataclasses import dataclass


SLOT_LABELS = {
    "kahvalti": "KahvaltÄ±",
    "ogle": "Ã–ÄŸle",
    "aksam": "AkÅŸam",
    "ara_ogun": "Ara Ã–ÄŸÃ¼n",
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
