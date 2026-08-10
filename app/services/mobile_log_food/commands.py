"""Typed, transport-independent LogFood semantic commands."""
from dataclasses import dataclass
from decimal import Decimal


_SLOTS = frozenset({"kahvalti", "ogle", "aksam", "ara_ogun"})


def _validated_amount(name, value, maximum, *, positive=False):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value == 0) or value > maximum:
        raise ValueError(f"{name} is outside the allowed range")


@dataclass(frozen=True)
class ManualNutritionSnapshot:
    energy_kcal: Decimal
    protein_g: Decimal
    carbohydrate_g: Decimal
    fat_g: Decimal

    def __post_init__(self):
        _validated_amount("energy_kcal", self.energy_kcal, Decimal("100000"))
        _validated_amount("protein_g", self.protein_g, Decimal("50000"))
        _validated_amount(
            "carbohydrate_g", self.carbohydrate_g, Decimal("50000"))
        _validated_amount("fat_g", self.fat_g, Decimal("50000"))


@dataclass(frozen=True)
class ProviderBackedLogFoodCommand:
    provider: str
    food_id: str
    serving_id: str
    quantity: Decimal
    slot: str
    discovery_source: str

    def __post_init__(self):
        if self.provider != "fatsecret":
            raise ValueError("unsupported provider")
        if not self.food_id or not self.serving_id:
            raise ValueError("provider identities are required")
        _validated_amount(
            "quantity", self.quantity, Decimal("1000"), positive=True)
        if self.slot not in _SLOTS:
            raise ValueError("invalid slot")
        if self.discovery_source not in {"search", "barcode"}:
            raise ValueError("invalid discovery source")


@dataclass(frozen=True)
class ManualLogFoodCommand:
    description: str
    slot: str
    nutrition: ManualNutritionSnapshot

    def __post_init__(self):
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("description is required")
        if self.description != self.description.strip():
            raise ValueError("description must already be normalized")
        if self.slot not in _SLOTS:
            raise ValueError("invalid slot")
        if not isinstance(self.nutrition, ManualNutritionSnapshot):
            raise ValueError("validated nutrition is required")
