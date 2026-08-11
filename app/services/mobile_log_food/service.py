"""Canonical MealLog persistence and semantic replay for mobile LogFood."""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from app.extensions import db
from app.models import MealLog
from app.services import meal_idempotency, mobile_food_discovery
from app.services.mobile_nutrition.identity import diary_entry_id
from app.services.mobile_nutrition.revision import (
    diary_entry_revision,
    revision_state_from_entry,
)
from app.services.mobile_nutrition.serialization import logged_meal
from app.timeutil import day_key

from .commands import (
    ManualLogFoodCommand,
    ManualNutritionSnapshot,
    ProviderBackedLogFoodCommand,
)
from .fingerprint import semantic_fingerprint


class IdempotencyConflict(Exception):
    pass


class ProviderFoodNotFound(Exception):
    pass


_MEAL_LABELS = {
    "kahvalti": "Kahvaltı",
    "ogle": "Öğle",
    "aksam": "Akşam",
    "ara_ogun": "Ara Öğün",
}


def _existing_or_conflict(user_id, key, fingerprint):
    existing = meal_idempotency.find_existing(user_id, key)
    if existing is None:
        return None
    if existing.idempotency_fingerprint != fingerprint:
        raise IdempotencyConflict
    return existing


def _provider_snapshot(command):
    food = mobile_food_discovery.servings(command.food_id)
    if not food:
        raise ProviderFoodNotFound
    serving = next((item for item in food.get("servings", [])
                    if item.get("serving_id") == command.serving_id), None)
    if not serving:
        raise ProviderFoodNotFound
    nutrition = serving.get("nutrition") or {}
    try:
        scaled = ManualNutritionSnapshot(
            energy_kcal=Decimal(str(nutrition["energy_kcal"])) * command.quantity,
            protein_g=Decimal(str(nutrition["protein_g"])) * command.quantity,
            carbohydrate_g=(
                Decimal(str(nutrition["carbohydrate_g"])) * command.quantity),
            fat_g=Decimal(str(nutrition["fat_g"])) * command.quantity,
        )
    except (KeyError, TypeError, ValueError):
        raise ProviderFoodNotFound from None
    quantity = format(command.quantity.normalize(), "f")
    description = (
        f"{food.get('name') or 'Food'} "
        f"({quantity}x {serving.get('description') or 'serving'})"
    )
    return description, scaled


def log_food(user_id, key, command):
    fingerprint = semantic_fingerprint(command)
    existing = _existing_or_conflict(user_id, key, fingerprint)
    if existing:
        return existing, False

    # The preflight SELECT starts an implicit SQLAlchemy transaction. Close that
    # read transaction before any provider network I/O; persistence below opens
    # the short transaction whose race arbiter is the existing unique key.
    db.session.rollback()

    if isinstance(command, ProviderBackedLogFoodCommand):
        description, nutrition = _provider_snapshot(command)
        source = command.discovery_source
    elif isinstance(command, ManualLogFoodCommand):
        description, nutrition = command.description, command.nutrition
        source = "manual"
    else:  # pragma: no cover - typed parser is the only caller
        raise TypeError("unsupported command")

    entry = MealLog(
        user_id=user_id,
        ogun=_MEAL_LABELS[command.slot],
        yemekler=description,
        kalori=float(nutrition.energy_kcal),
        protein=float(nutrition.protein_g),
        karb=float(nutrition.carbohydrate_g),
        yag=float(nutrition.fat_g),
        tarih=day_key(),
        source=source,
        idempotency_fingerprint=fingerprint,
        created_at=datetime.utcnow(),
    )
    winner, created = meal_idempotency.commit_once(entry, key)
    if winner.idempotency_fingerprint != fingerprint:
        raise IdempotencyConflict
    return winner, created


def response_meal(entry, secret, user_id):
    projected = SimpleNamespace(
        user_id=entry.user_id,
        entry_id=entry.id,
        meal_label=entry.ogun,
        description=entry.yemekler,
        source=entry.source,
        created_at=entry.created_at,
        energy_kcal=entry.kalori,
        protein_g=entry.protein,
        carbohydrate_g=entry.karb,
        fat_g=entry.yag,
        diary_date=entry.tarih,
        idempotency_key=entry.idempotency_key,
        idempotency_fingerprint=entry.idempotency_fingerprint,
        photo_key=entry.photo_key,
    )
    payload = logged_meal(
        projected,
        lambda entry_id: diary_entry_id(secret, user_id, entry_id),
        lambda item: diary_entry_revision(
            secret, revision_state_from_entry(item)),
    )
    payload["day"] = entry.tarih
    return payload
