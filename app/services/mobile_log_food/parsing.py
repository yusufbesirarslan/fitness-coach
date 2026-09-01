"""Strict transport parsing into validated semantic LogFood commands."""
from decimal import Decimal, InvalidOperation

from .commands import (
    ManualLogFoodCommand,
    ManualNutritionSnapshot,
    ProviderBackedLogFoodCommand,
    _validated_amount,
)

PROVIDER_IDENTITY_MAX_LENGTH = 128
PROVIDER_QUANTITY_MAX = Decimal("1000")


class InvalidLogFoodCommand(ValueError):
    pass


def _enum(value):
    return value.strip().lower() if isinstance(value, str) else ""


def parse_provider_identity(value, maximum=PROVIDER_IDENTITY_MAX_LENGTH):
    """Normalize and bound one provider identity: the ONLY identity policy.

    Public because the diary blueprint adapts into exactly this validation
    rather than keeping a second, unbounded policy of its own (Sprint 13 PR3B,
    P2-01) - the same reason `parse_manual_nutrition` below is public. A caller
    that PERSISTS the identity passes the narrower bound of the column that
    will store it: 128 characters the provider would accept are still a failed
    INSERT, and failing at the transport is the honest place to fail.
    """
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > maximum:
        raise InvalidLogFoodCommand("invalid provider identity")
    return normalized


def parse_provider_quantity(value):
    """Validate one provider serving quantity: the ONLY quantity bounds policy.

    Callers must read the field as ``data.get(name, <default>)`` so that an
    OMITTED field keeps its documented default while a field present as JSON
    ``null`` arrives here as ``None`` and is rejected. Rehabilitating ``None``
    into a default is what let a malformed diary command become durable
    one-serving provider staging (Sprint 13 PR3B, P1-02).
    """
    quantity = _decimal(value)
    try:
        _validated_amount("quantity", quantity, PROVIDER_QUANTITY_MAX,
                          positive=True)
    except ValueError as error:
        raise InvalidLogFoodCommand(str(error)) from None
    return quantity


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
            food_id=parse_provider_identity(data.get("food_id")),
            serving_id=parse_provider_identity(data.get("serving_id")),
            quantity=_decimal(data.get("quantity", 1)),
            slot=_enum(data.get("slot")),
            discovery_source=_enum(data.get("discovery_source", "search")),
        )
    except ValueError as error:
        raise InvalidLogFoodCommand(str(error)) from None


_MANUAL_NUTRITION_FIELDS = frozenset(
    {"energy_kcal", "protein_g", "carbohydrate_g", "fat_g"})


def parse_manual_nutrition(nutrition):
    """Validate a manual nutrition snapshot: the ONLY manual bounds policy.

    Public because the web `/meal-log` manual branch adapts into exactly this
    validation rather than keeping a second, looser numeric policy of its own
    (Sprint 13 PR3, C3). The mobile parser below is the other caller, so the
    two clients cannot drift apart.
    """
    if not isinstance(nutrition, dict) or set(nutrition) != _MANUAL_NUTRITION_FIELDS:
        raise InvalidLogFoodCommand("invalid nutrition snapshot")
    try:
        return ManualNutritionSnapshot(
            energy_kcal=_decimal(nutrition["energy_kcal"]),
            protein_g=_decimal(nutrition["protein_g"]),
            carbohydrate_g=_decimal(nutrition["carbohydrate_g"]),
            fat_g=_decimal(nutrition["fat_g"]),
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
    snapshot = parse_manual_nutrition(data.get("nutrition"))
    try:
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
