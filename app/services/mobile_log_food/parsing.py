"""Strict transport parsing into validated semantic LogFood commands."""
from decimal import Decimal, InvalidOperation

from .commands import (
    ManualLogFoodCommand,
    ManualNutritionSnapshot,
    ProviderBackedLogFoodCommand,
)


class InvalidLogFoodCommand(ValueError):
    pass


def _enum(value):
    return value.strip().lower() if isinstance(value, str) else ""


def _identity(value):
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 128:
        raise InvalidLogFoodCommand("invalid provider identity")
    return normalized


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidLogFoodCommand("invalid number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidLogFoodCommand("invalid number") from None
    if not parsed.is_finite():
        raise InvalidLogFoodCommand("invalid number")
    return parsed


def _provider(data):
    allowed = {
        "kind", "provider", "food_id", "serving_id", "quantity", "slot",
        "discovery_source",
    }
    if set(data) - allowed:
        raise InvalidLogFoodCommand("mixed or unknown provider fields")
    try:
        return ProviderBackedLogFoodCommand(
            provider=_enum(data.get("provider")),
            food_id=_identity(data.get("food_id")),
            serving_id=_identity(data.get("serving_id")),
            quantity=_decimal(data.get("quantity", 1)),
            slot=_enum(data.get("slot")),
            discovery_source=_enum(data.get("discovery_source", "search")),
        )
    except ValueError as error:
        raise InvalidLogFoodCommand(str(error)) from None


def _manual(data):
    if set(data) != {"kind", "description", "slot", "nutrition"}:
        raise InvalidLogFoodCommand("mixed, unknown, or missing manual fields")
    description = data.get("description")
    if not isinstance(description, str):
        raise InvalidLogFoodCommand("invalid description")
    description = description.strip()
    if not 1 <= len(description) <= 500:
        raise InvalidLogFoodCommand("invalid description")
    nutrition = data.get("nutrition")
    expected = {"energy_kcal", "protein_g", "carbohydrate_g", "fat_g"}
    if not isinstance(nutrition, dict) or set(nutrition) != expected:
        raise InvalidLogFoodCommand("invalid nutrition snapshot")
    try:
        snapshot = ManualNutritionSnapshot(
            energy_kcal=_decimal(nutrition["energy_kcal"]),
            protein_g=_decimal(nutrition["protein_g"]),
            carbohydrate_g=_decimal(nutrition["carbohydrate_g"]),
            fat_g=_decimal(nutrition["fat_g"]),
        )
        return ManualLogFoodCommand(
            description=description,
            slot=_enum(data.get("slot")),
            nutrition=snapshot,
        )
    except ValueError as error:
        raise InvalidLogFoodCommand(str(error)) from None


def parse_command(data):
    if not isinstance(data, dict):
        raise InvalidLogFoodCommand("command must be an object")
    kind = _enum(data.get("kind"))
    if kind == "provider_backed":
        return _provider(data)
    if kind == "manual":
        return _manual(data)
    raise InvalidLogFoodCommand("invalid command kind")
