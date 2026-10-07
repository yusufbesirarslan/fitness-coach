"""Read-only FatSecret discovery projection for the mobile nutrition API."""
import math

from app.models import BarcodeFoodCache
from app.services import fatsecret
from app.services.ai_gate import blocking_concurrency_slot
from app.services.barcode import normalize_barcode

SEARCH_RESULT_LIMIT = 8


def _identity(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise fatsecret.FoodProviderUnavailable
    identity = str(value)
    if not identity or identity != identity.strip() or len(identity) > 128:
        raise fatsecret.FoodProviderUnavailable
    return identity


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise fatsecret.FoodProviderUnavailable
    return value


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _nutrition(raw):
    return {
        "energy_kcal": _number(raw.get("calories")),
        "protein_g": _number(raw.get("protein")),
        "carbohydrate_g": _number(raw.get("carbohydrate", raw.get("carbs"))),
        "fat_g": _number(raw.get("fat")),
    }


def _per_100g(nutrition, grams):
    if grams is None or grams <= 0 or any(
            value is None for value in nutrition.values()):
        return None
    values = {
        key: round(value * 100 / grams, 4)
        for key, value in nutrition.items()
    }
    if any(not math.isfinite(value) for value in values.values()):
        raise fatsecret.FoodProviderUnavailable
    return values


def project_food(food_id, raw_food):
    """Project raw provider truth without legacy mass estimates or zero fill."""
    servings_raw = ((raw_food or {}).get("servings") or {}).get("serving") or []
    if isinstance(servings_raw, dict):
        servings_raw = [servings_raw]
    if not isinstance(servings_raw, list):
        raise fatsecret.FoodProviderUnavailable
    projected = []
    identities = set()
    for raw in servings_raw:
        if not isinstance(raw, dict):
            raise fatsecret.FoodProviderUnavailable
        serving_id = _identity(raw.get("serving_id"))
        if serving_id in identities:
            raise fatsecret.FoodProviderUnavailable
        identities.add(serving_id)
        nutrition = _nutrition(raw)
        grams = _number(raw.get("metric_serving_amount"))
        unit = raw.get("metric_serving_unit")
        metric_mass = None
        if grams is not None and grams > 0 and str(unit or "").lower() == "g":
            metric_mass = {"amount": grams, "unit": "g"}
        projected.append({
            "serving_id": serving_id,
            "description": _text(raw.get("serving_description")),
            "nutrition": nutrition,
            "metric_mass": metric_mass,
            "nutrition_per_100g": _per_100g(
                nutrition, grams if str(unit or "").lower() == "g" else None),
        })
    return {
        "provider": "fatsecret",
        "food_id": _identity(food_id),
        "name": _text((raw_food or {}).get("food_name")),
        "brand": _text((raw_food or {}).get("brand_name")),
        "servings": projected,
    }


def search(query):
    # FatSecret turları senkron ve bloklayıcıdır; rezerv-sayılan slot olmadan
    # sağlayıcı gecikmesinde 8 web thread'inin hepsi park edebilir (triage
    # 2026-08-07 #2 — web food route'larıyla AYNI sınıf; bu mobil yüzey denetimden
    # sonra eklendi). Kapasite dolduğunda BlockingConcurrencyLimit route'un
    # mevcut `except Exception` yoluna düşer → FOOD_PROVIDER_UNAVAILABLE 503
    # retryable=True, yani istemci için doğru semantik.
    with blocking_concurrency_slot():
        foods = fatsecret._food_search_raw(query, strict=True)
    return [{
        "provider": "fatsecret",
        "food_id": _identity(food.get("food_id")),
        "name": _text(food.get("food_name")),
        "brand": _text(food.get("brand_name")),
    } for food in foods[:SEARCH_RESULT_LIMIT]]


def servings(food_id):
    with blocking_concurrency_slot():
        raw = fatsecret._food_get_raw(food_id, strict=True)
    return project_food(food_id, raw) if raw else None


def barcode_lookup(code):
    if not isinstance(code, str) or not code.isascii() or not code.isdigit():
        return None
    normalized = normalize_barcode(code)
    if not normalized:
        return None
    cached = BarcodeFoodCache.query.filter_by(barcode=normalized).first()
    if cached and cached.food_id:
        # Legacy cache payloads may contain synthetic serving identities and
        # estimated mass. Only the provider food ID crosses the native boundary.
        return servings(_identity(cached.food_id))
    # Resolve the food identity first, then use the same serving pipeline.
    with blocking_concurrency_slot():
        food_id = fatsecret._food_find_id_by_barcode_raw(normalized, strict=True)
    return servings(food_id) if food_id else None
