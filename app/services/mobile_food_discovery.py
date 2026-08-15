"""Read-only FatSecret discovery projection for the mobile nutrition API."""
import math

from app.models import BarcodeFoodCache
from app.services import fatsecret
from app.services.ai_gate import blocking_concurrency_slot
from app.services.barcode import normalize_barcode


def _number(value):
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
    return {
        key: round(value * 100 / grams, 4)
        for key, value in nutrition.items()
    }


def project_food(food_id, raw_food):
    """Project raw provider truth without legacy mass estimates or zero fill."""
    servings_raw = ((raw_food or {}).get("servings") or {}).get("serving") or []
    if isinstance(servings_raw, dict):
        servings_raw = [servings_raw]
    projected = []
    for raw in servings_raw:
        nutrition = _nutrition(raw)
        grams = _number(raw.get("metric_serving_amount"))
        unit = raw.get("metric_serving_unit")
        metric_mass = None
        if grams is not None and unit:
            metric_mass = {"amount": grams, "unit": str(unit)}
        projected.append({
            "serving_id": str(raw.get("serving_id") or ""),
            "description": str(raw.get("serving_description") or ""),
            "nutrition": nutrition,
            "metric_mass": metric_mass,
            "nutrition_per_100g": _per_100g(
                nutrition, grams if str(unit or "").lower() == "g" else None),
        })
    return {
        "provider": "fatsecret",
        "food_id": str(food_id),
        "name": str((raw_food or {}).get("food_name") or ""),
        "brand": str((raw_food or {}).get("brand_name") or ""),
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
        foods = fatsecret._food_search_raw(query) or []
    return [{
        "provider": "fatsecret",
        "food_id": str(food.get("food_id") or ""),
        "name": str(food.get("food_name") or ""),
        "brand": str(food.get("brand_name") or ""),
    } for food in foods if food.get("food_id")]


def servings(food_id):
    with blocking_concurrency_slot():
        raw = fatsecret._food_get_raw(food_id)
    return project_food(food_id, raw) if raw else None


def _cached_food(row):
    payload = row.payload or {}
    raw_servings = []
    for serving in payload.get("servings") or []:
        macros = serving.get("macros") or {}
        amount = serving.get("metric_serving_amount")
        raw_servings.append({
            "serving_id": serving.get("id"),
            "serving_description": serving.get("description"),
            # A cached zero may be legacy fabrication, so it remains unknown.
            "metric_serving_amount": amount if _number(amount) not in (None, 0) else None,
            "metric_serving_unit": serving.get("metric_serving_unit"),
            "calories": macros.get("calories"),
            "protein": macros.get("protein"),
            "carbohydrate": macros.get("carbs"),
            "fat": macros.get("fat"),
        })
    return project_food(row.food_id, {
        "food_name": row.food_name,
        "brand_name": row.brand,
        "servings": {"serving": raw_servings},
    })


def barcode_lookup(code):
    normalized = normalize_barcode(code)
    if not normalized:
        return None
    cached = BarcodeFoodCache.query.filter_by(barcode=normalized).first()
    if cached:
        return _cached_food(cached)
    # Slot yalnızca ağ turunu sarar — yukarıdaki cache HIT'i saf DB'dir.
    with blocking_concurrency_slot():
        food_id = fatsecret._food_find_id_by_barcode_raw(normalized)
    return servings(food_id) if food_id else None
