"""Transport-independent resolution of provider food serving truth."""
from dataclasses import dataclass
from decimal import Decimal

from app.services import mobile_food_discovery


class ProviderFoodNotFound(Exception):
    """The provider food or serving cannot be resolved authoritatively."""


@dataclass(frozen=True)
class ProviderNutrition:
    energy_kcal: Decimal
    protein_g: Decimal
    carbohydrate_g: Decimal
    fat_g: Decimal

    def __post_init__(self):
        limits = (
            (self.energy_kcal, Decimal("100000")),
            (self.protein_g, Decimal("50000")),
            (self.carbohydrate_g, Decimal("50000")),
            (self.fat_g, Decimal("50000")),
        )
        if any(not value.is_finite() or value < 0 or value > maximum
               for value, maximum in limits):
            raise ValueError("provider nutrition is outside the allowed range")


@dataclass(frozen=True)
class ProviderFoodSnapshot:
    provider: str
    food_id: str
    serving_id: str
    quantity: Decimal
    food_name: str
    serving_description: str
    nutrition: ProviderNutrition
    grams: Decimal
    nutrition_per_100g: ProviderNutrition | None

    @property
    def description(self):
        quantity = format(self.quantity.normalize(), "f")
        return f"{self.food_name} ({quantity}x {self.serving_description})"


def _nutrition(values, multiplier=Decimal("1")):
    try:
        return ProviderNutrition(
            energy_kcal=Decimal(str(values["energy_kcal"])) * multiplier,
            protein_g=Decimal(str(values["protein_g"])) * multiplier,
            carbohydrate_g=(
                Decimal(str(values["carbohydrate_g"])) * multiplier),
            fat_g=Decimal(str(values["fat_g"])) * multiplier,
        )
    except (KeyError, TypeError, ValueError):
        raise ProviderFoodNotFound from None


def resolve_provider_food(provider, food_id, serving_id, quantity):
    """Resolve and scale one semantic serving selection from provider truth."""
    if provider != "fatsecret" or not food_id or not serving_id:
        raise ProviderFoodNotFound
    if (not isinstance(quantity, Decimal) or not quantity.is_finite()
            or quantity <= 0 or quantity > Decimal("1000")):
        raise ValueError("quantity is outside the allowed range")

    food = mobile_food_discovery.servings(str(food_id))
    if not food:
        raise ProviderFoodNotFound
    serving = next((candidate for candidate in food.get("servings", [])
                    if candidate.get("serving_id") == str(serving_id)), None)
    if not serving:
        raise ProviderFoodNotFound

    nutrition = _nutrition(serving.get("nutrition") or {}, quantity)
    per_100g_values = serving.get("nutrition_per_100g")
    per_100g = _nutrition(per_100g_values) if per_100g_values else None
    metric = serving.get("metric_mass") or {}
    grams = Decimal("0")
    if str(metric.get("unit") or "").lower() == "g":
        try:
            grams = Decimal(str(metric["amount"])) * quantity
        except (KeyError, TypeError, ValueError):
            raise ProviderFoodNotFound from None
        if not grams.is_finite() or grams < 0:
            raise ProviderFoodNotFound

    return ProviderFoodSnapshot(
        provider=provider,
        food_id=str(food_id),
        serving_id=str(serving_id),
        quantity=quantity,
        food_name=str(food.get("name") or "Food"),
        serving_description=str(serving.get("description") or "serving"),
        nutrition=nutrition,
        grams=grams,
        nutrition_per_100g=per_100g,
    )
