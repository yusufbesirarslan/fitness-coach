"""Barcode product workflow.

This module keeps barcode business logic out of the UI and route layer:
FatSecret lookup -> normalized food model -> cache -> nutrition analysis ->
deterministic recommendation -> optional MealLog write.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from app.services import nutrition_pipeline
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    BarcodeFoodCache,
    MealLog,
    UserSession,
)
from app.services.ai_gate import blocking_concurrency_slot
from app.services.fatsecret import _food_find_by_barcode
from app.services.workout_state import resolve_workout_state
from app.timeutil import day_key


SUPPORTED_BARCODE_LENGTHS = {6, 8, 12, 13}

BARCODE_NUTRITION_RULES = {
    "high_protein_density": 0.08,  # grams protein per kcal
    "high_protein_g": 20.0,
    "low_calorie_kcal": 180.0,
    "high_fiber_g": 5.0,
    "moderate_fiber_g": 3.0,
    "high_sugar_g": 10.0,
    "high_sodium_mg": 400.0,
    "energy_dense_kcal_per_100g": 250.0,
    "max_recommended_servings": 3.0,
    "min_recommended_servings": 0.25,
}


def normalize_barcode(code: str) -> str:
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(digits) not in SUPPORTED_BARCODE_LENGTHS:
        return ""
    return digits


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round1(value):
    return round(float(value or 0), 1)


def _serving_id(serving, index):
    return str(serving.get("serving_id") or serving.get("id") or f"serving-{index + 1}")


def normalize_food_model(barcode: str, fatsecret_payload: dict) -> dict:
    """Convert the FatSecret helper payload into AxisAI's internal food model."""
    normalized_barcode = normalize_barcode(barcode) or "".join(ch for ch in (barcode or "") if ch.isdigit())
    servings = []
    for idx, serving in enumerate((fatsecret_payload or {}).get("servings") or []):
        macros = {
            "calories": _round1(serving.get("calories")),
            "protein": _round1(serving.get("protein")),
            "carbs": _round1(serving.get("carbs")),
            "fat": _round1(serving.get("fat")),
        }
        if "fiber" in serving:
            macros["fiber"] = _round1(serving.get("fiber"))
        if "sugar" in serving:
            macros["sugar"] = _round1(serving.get("sugar"))
        if "sodium" in serving:
            macros["sodium"] = _round1(serving.get("sodium"))
        servings.append({
            "id": _serving_id(serving, idx),
            "description": serving.get("serving_description") or serving.get("description") or "Serving",
            "metric_serving_amount": _round1(serving.get("metric_serving_amount")),
            "metric_serving_unit": serving.get("metric_serving_unit") or "g",
            "is_bulk": bool(serving.get("is_bulk", False)),
            "macros": macros,
        })

    default = _choose_default_serving(servings)
    return {
        "barcode": normalized_barcode,
        "food_id": str((fatsecret_payload or {}).get("food_id") or ""),
        "name": (fatsecret_payload or {}).get("name") or "Unknown food",
        "brand": (fatsecret_payload or {}).get("brand") or "",
        "servings": servings,
        "default_serving_id": default["id"] if default else "",
    }


def _choose_default_serving(servings):
    sane = [s for s in servings if not s.get("is_bulk") and s.get("macros", {}).get("calories", 0) > 0]
    return (sane or servings or [None])[0]


def get_serving(food: dict, serving_id: str | None = None) -> dict | None:
    servings = (food or {}).get("servings") or []
    if serving_id:
        for serving in servings:
            if str(serving.get("id")) == str(serving_id):
                return serving
    return _choose_default_serving(servings)


def _macro_distribution(macros):
    calories = max(_to_float(macros.get("calories")), 0)
    if calories <= 0:
        return {"protein_pct": 0, "carb_pct": 0, "fat_pct": 0}
    return {
        "protein_pct": round((_to_float(macros.get("protein")) * 4 / calories) * 100),
        "carb_pct": round((_to_float(macros.get("carbs")) * 4 / calories) * 100),
        "fat_pct": round((_to_float(macros.get("fat")) * 9 / calories) * 100),
    }


def analyze_food(food: dict, serving_id: str | None = None) -> dict:
    serving = get_serving(food, serving_id)
    macros = (serving or {}).get("macros") or {}
    calories = _to_float(macros.get("calories"))
    protein = _to_float(macros.get("protein"))
    carbs = _to_float(macros.get("carbs"))
    fat = _to_float(macros.get("fat"))
    fiber = _to_float(macros.get("fiber"))
    sugar = _to_float(macros.get("sugar"))
    sodium = _to_float(macros.get("sodium"))
    grams = _to_float((serving or {}).get("metric_serving_amount"))

    protein_density = round(protein / calories, 3) if calories > 0 else 0
    distribution = _macro_distribution(macros)

    if fiber >= BARCODE_NUTRITION_RULES["high_fiber_g"]:
        fiber_rating = "high"
    elif fiber >= BARCODE_NUTRITION_RULES["moderate_fiber_g"]:
        fiber_rating = "moderate"
    elif "fiber" in macros:
        fiber_rating = "low"
    else:
        fiber_rating = "unknown"

    sugar_warning = sugar >= BARCODE_NUTRITION_RULES["high_sugar_g"]
    sodium_warning = sodium >= BARCODE_NUTRITION_RULES["high_sodium_mg"]
    high_protein = (
        protein >= BARCODE_NUTRITION_RULES["high_protein_g"]
        or protein_density >= BARCODE_NUTRITION_RULES["high_protein_density"]
    )
    low_calorie = calories > 0 and calories <= BARCODE_NUTRITION_RULES["low_calorie_kcal"]
    kcal_per_100g = calories / grams * 100 if grams > 0 else 0
    energy_dense = kcal_per_100g >= BARCODE_NUTRITION_RULES["energy_dense_kcal_per_100g"]

    badges = []
    if high_protein:
        badges.append("High Protein")
    if low_calorie:
        badges.append("Low Calorie")
    if fiber_rating == "high":
        badges.append("High Fiber")
    if fat <= 3 and calories > 0:
        badges.append("Low Fat")
    if sugar_warning:
        badges.append("High Sugar")
    if sodium_warning:
        badges.append("High Sodium")
    if energy_dense:
        badges.append("Energy Dense")

    score = 55
    score += min(protein_density / BARCODE_NUTRITION_RULES["high_protein_density"], 1.5) * 14
    score += 8 if fiber_rating == "high" else 4 if fiber_rating == "moderate" else 0
    score += 6 if low_calorie else 0
    score -= 12 if sugar_warning else 0
    score -= 8 if sodium_warning else 0
    score -= 7 if energy_dense and not high_protein else 0
    score -= 8 if fat >= 18 else 0
    axisai_score = int(max(0, min(100, round(score))))

    if high_protein and protein >= 20:
        protein_quality_badge = "excellent"
    elif high_protein:
        protein_quality_badge = "good"
    else:
        protein_quality_badge = "low"

    return {
        "serving_id": (serving or {}).get("id", ""),
        "protein_density": protein_density,
        "macro_distribution": distribution,
        "fiber_rating": fiber_rating,
        "sugar_warning": sugar_warning,
        "sodium_warning": sodium_warning,
        "protein_quality_badge": protein_quality_badge,
        "high_protein": high_protein,
        "low_calorie": low_calorie,
        "meal_quality_score": axisai_score,
        "axisai_food_score": axisai_score,
        "badges": badges,
    }


def _goal_key(goal):
    value = (goal or "").strip().lower()
    if any(token in value for token in ("bulk", "kas", "kazan")):
        return "bulk"
    if any(token in value for token in ("cut", "kilo", "verme", "yag", "yağ")):
        return "cut"
    return "maintain"


def _target_macros(session):
    calories = _to_float(getattr(session, "target_calories", None), 2000) or 2000
    goal = _goal_key(getattr(session, "goal", ""))
    protein_ratio = 0.30 if goal == "bulk" else 0.25
    return {
        "calories": calories,
        "protein": round(calories * protein_ratio / 4, 1),
        "carbs": round(calories * 0.45 / 4, 1),
        "fat": round(calories * 0.25 / 9, 1),
    }


def _today_totals(user_id):
    today = day_key()
    meals = MealLog.query.filter_by(user_id=user_id, tarih=today).all()
    return {
        "calories": round(sum(m.kalori or 0 for m in meals), 1),
        "protein": round(sum(m.protein or 0 for m in meals), 1),
        "carbs": round(sum(m.karb or 0 for m in meals), 1),
        "fat": round(sum(m.yag or 0 for m in meals), 1),
    }


def get_user_barcode_context(user_id, meal_time=""):
    session = UserSession.query.filter_by(user_id=user_id).order_by(UserSession.created_at.desc()).first()
    targets = _target_macros(session)
    consumed = _today_totals(user_id)
    remaining = {key: round(max(targets[key] - consumed[key], 0), 1) for key in targets}
    return {
        "goal": _goal_key(getattr(session, "goal", "")),
        "targets": targets,
        "consumed": consumed,
        "remaining": remaining,
        "workout_completed_today": resolve_workout_state(
            user_id).completed_today,
        "meal_time": meal_time or "",
        "daily_progress": {
            key: round((consumed[key] / targets[key]) * 100, 1) if targets[key] else 0
            for key in targets
        },
    }


def recommend_portion(food, context, serving_id=None):
    serving = get_serving(food, serving_id)
    macros = (serving or {}).get("macros") or {}
    remaining = (context or {}).get("remaining") or {}
    candidates = []
    for key, macro_key in (("calories", "calories"), ("protein", "protein"), ("carbs", "carbs"), ("fat", "fat")):
        macro_value = _to_float(macros.get(macro_key))
        remaining_value = _to_float(remaining.get(key))
        if macro_value > 0 and remaining_value > 0:
            candidates.append((remaining_value / macro_value, key))
    if not candidates:
        return {"servings": 1.0, "basis": "default", "label": "1 serving"}

    servings, basis = min(candidates, key=lambda item: item[0])
    servings = max(
        BARCODE_NUTRITION_RULES["min_recommended_servings"],
        min(BARCODE_NUTRITION_RULES["max_recommended_servings"], servings),
    )
    servings = round(servings, 1)
    return {
        "servings": servings,
        "basis": basis,
        "label": f"{servings:g} serving" if servings == 1 else f"{servings:g} servings",
    }


def recommend_for_food(food, context, serving_id=None):
    analysis = analyze_food(food, serving_id)
    serving = get_serving(food, serving_id)
    macros = (serving or {}).get("macros") or {}
    portion = recommend_portion(food, context, serving_id)
    goal = (context or {}).get("goal", "maintain")
    messages = []

    if analysis["sugar_warning"]:
        messages.append("This food is high in sugar.")
    if analysis["sodium_warning"]:
        messages.append("This food is high in sodium.")
    if analysis["high_protein"] and (context or {}).get("workout_completed_today"):
        messages.append("This food is an excellent choice after today's workout.")
    if portion["servings"] < 1:
        messages.append("Consider eating a smaller serving.")
    if goal == "cut":
        if analysis["low_calorie"] or analysis["axisai_food_score"] >= 70:
            messages.append("This fits your cutting goal.")
        elif _to_float(macros.get("calories")) >= 240:
            messages.append("This food is not ideal for cutting.")
    elif goal == "bulk":
        if analysis["high_protein"] or _to_float(macros.get("calories")) >= 240:
            messages.append("This food is better suited for bulking.")
    else:
        if analysis["axisai_food_score"] >= 70:
            messages.append("This is a balanced choice for maintenance.")

    remaining_protein = _to_float(((context or {}).get("remaining") or {}).get("protein"))
    if remaining_protein and _to_float(macros.get("protein")) >= remaining_protein * 0.75:
        messages.append("Your protein target is nearly complete.")

    return {"messages": messages, "portion": portion}


def _cache_payload(cache_row):
    payload = deepcopy(cache_row.payload or {})
    return {"source": "cache", "food": payload}


def get_barcode_product(code, lookup_func=None):
    barcode = normalize_barcode(code)
    if not barcode:
        return None

    cached = BarcodeFoodCache.query.filter_by(barcode=barcode).first()
    if cached:
        return _cache_payload(cached)

    # Cache MISS → FatSecret'e senkron, bloklayıcı bir tur (timeout=10). Uygulama
    # tek gunicorn worker + 8 thread çalışır; bu tur rezerv-sayılan slotun DIŞINDA
    # kalırsa, FatSecret gecikme atağında kullanıcılar-arası bir barkod yığını
    # thread'lerin hepsini park edip /health'i kuyruğa sokabilir (triage
    # 2026-08-07 #2; PR #199'un food_search için kapattığı sınıfın aynısı).
    # Slot YALNIZCA ağ turunu sarar: yukarıdaki cache HIT'i saf DB'dir ve slot
    # tüketmez, böylece ucuz tarama akışı gereksiz 503 yemez. Kapasite dolu ise
    # BlockingConcurrencyLimit ÇAĞIRANA yükselir — "besin bulunamadı"ya
    # ÇEVRİLMEZ (kapasite reddi bir arama sonucu değildir).
    lookup = lookup_func or _food_find_by_barcode
    with blocking_concurrency_slot():
        raw = lookup(barcode)
    if not raw:
        return None

    food = normalize_food_model(barcode, raw)
    row = BarcodeFoodCache(
        barcode=barcode,
        food_id=food["food_id"],
        food_name=food["name"],
        brand=food["brand"],
        payload=food,
    )
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = BarcodeFoodCache.query.filter_by(barcode=barcode).first()
        if existing:
            return _cache_payload(existing)
        raise
    return {"source": "fatsecret", "food": food}


def legacy_servings(food):
    result = []
    for serving in (food or {}).get("servings") or []:
        macros = serving.get("macros") or {}
        result.append({
            "serving_id": serving.get("id"),
            "serving_description": serving.get("description"),
            "metric_serving_amount": serving.get("metric_serving_amount"),
            "metric_serving_unit": serving.get("metric_serving_unit"),
            "calories": macros.get("calories", 0),
            "protein": macros.get("protein", 0),
            "carbs": macros.get("carbs", 0),
            "fat": macros.get("fat", 0),
            "is_bulk": serving.get("is_bulk", False),
        })
    return result


def build_lookup_response(product, context):
    food = product["food"]
    default_serving = food.get("default_serving_id")
    recommendations = recommend_for_food(food, context, default_serving)
    return {
        "barcode": food.get("barcode", ""),
        "source": product.get("source", ""),
        "food": food,
        "analysis": analyze_food(food, default_serving),
        "recommendations": recommendations["messages"],
        "portion_recommendation": recommendations["portion"],
        "daily_context": context,
        "analytics_events": ["Food Found"],
        # Backwards-compatible keys for existing nutrition.js callers.
        "food_id": food.get("food_id", ""),
        "name": food.get("name", ""),
        "brand": food.get("brand", ""),
        "servings": legacy_servings(food),
    }


def not_found_response():
    return {
        "error": "not_found",
        "actions": ["manual_search", "scan_nutrition_label"],
        "analytics_events": ["Food Not Found"],
    }


def scale_serving_macros(food, serving_id, quantity):
    serving = get_serving(food, serving_id)
    if not serving:
        return None, None
    qty = max(_to_float(quantity, 1.0), 0)
    macros = serving.get("macros") or {}
    cal, protein, carbs, fat = nutrition_pipeline.clamp_serving_macros(
        _round1(_to_float(macros.get("calories")) * qty),
        _round1(_to_float(macros.get("protein")) * qty),
        _round1(_to_float(macros.get("carbs")) * qty),
        _round1(_to_float(macros.get("fat")) * qty),
    )
    return serving, {
        "calories": _round1(cal),
        "protein": _round1(protein),
        "carbs": _round1(carbs),
        "fat": _round1(fat),
        "quantity": qty,
    }


def goal_impact_for_add(user_id, added_macros):
    before = _today_totals(user_id)
    after = {
        "calories": _round1(before["calories"] + added_macros["calories"]),
        "protein": _round1(before["protein"] + added_macros["protein"]),
        "carbs": _round1(before["carbs"] + added_macros["carbs"]),
        "fat": _round1(before["fat"] + added_macros["fat"]),
    }
    session = UserSession.query.filter_by(user_id=user_id).order_by(UserSession.created_at.desc()).first()
    targets = _target_macros(session)
    return {"before": before, "after": after, "targets": targets}


def add_food_to_diary(user_id, food, meal, serving_id=None, quantity=1,
                      idempotency_key=None):
    serving, macros = scale_serving_macros(food, serving_id, quantity)
    if not serving or not macros or macros["calories"] <= 0:
        return None
    qty = int(macros["quantity"]) if macros["quantity"] == int(macros["quantity"]) else macros["quantity"]
    label = f"{food.get('name', 'Food')} ({qty}x {serving.get('description', 'serving')})"
    entry = MealLog(
        user_id=user_id,
        ogun=meal,
        yemekler=label,
        kalori=macros["calories"],
        protein=macros["protein"],
        karb=macros["carbs"],
        yag=macros["fat"],
        tarih=day_key(),
        source="barcode",
        created_at=datetime.utcnow(),
    )
    from app.services.meal_idempotency import commit_once
    return commit_once(entry, idempotency_key)
