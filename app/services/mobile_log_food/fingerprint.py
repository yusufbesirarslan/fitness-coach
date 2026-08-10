"""Versioned semantic fingerprints for validated mobile LogFood commands.

The v1 representation is a compatibility contract. Future changes to which
semantic fields participate, or to their canonicalization, require a new domain
version rather than silently changing the meaning of an existing fingerprint.
"""
from decimal import Decimal
import hashlib
import json

from .commands import ManualLogFoodCommand, ProviderBackedLogFoodCommand


_DOMAIN = "axisai/mobile-log-food/v1"


def _canonical_number(value: Decimal) -> str:
    """Return a platform-independent decimal spelling for a validated value."""
    if not value.is_finite():
        raise ValueError("fingerprint numbers must be finite")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _semantic_payload(command):
    if isinstance(command, ProviderBackedLogFoodCommand):
        return {
            "domain": _DOMAIN,
            "kind": "provider_backed",
            "provider": command.provider,
            "food_id": command.food_id,
            "serving_id": command.serving_id,
            "quantity": _canonical_number(command.quantity),
            "slot": command.slot,
            "discovery_source": command.discovery_source,
        }
    if isinstance(command, ManualLogFoodCommand):
        return {
            "domain": _DOMAIN,
            "kind": "manual",
            "description": command.description,
            "slot": command.slot,
            "nutrition": {
                "energy_kcal": _canonical_number(
                    command.nutrition.energy_kcal),
                "protein_g": _canonical_number(command.nutrition.protein_g),
                "carbohydrate_g": _canonical_number(
                    command.nutrition.carbohydrate_g),
                "fat_g": _canonical_number(command.nutrition.fat_g),
            },
        }
    raise TypeError("semantic_fingerprint requires a validated LogFood command")


def semantic_fingerprint(command) -> str:
    """Hash an explicitly constructed semantic representation, never raw JSON."""
    canonical = json.dumps(
        _semantic_payload(command),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
