"""Yemek günlüğü (CustomMeal/CustomMealItem) + hızlı ekle.

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.blueprints.nutrition import bp
from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog, NutritionPlan
from app.services.gamification import complete_quest_for_user
from app.services.validators import _to_float
from app.timeutil import app_today, day_key


def _sanitize_meal_macros(kalori, protein, karb, yag):
    """LLM plan makrolarını nutrition_pipeline kapısıyla (menü hattıyla aynı)
    denetle; fiziksel olarak imkânsız değerleri makul tavanlara kıs — aksi halde
    bir LLM saçmalığı (örn. 9999 kcal) doğrudan MealLog'a sızıyordu (F9)."""
    import nutrition_pipeline as _np
    serving = {"calories": kalori, "protein": protein, "carbs": karb, "fat": yag}
    is_valid, _flags, reasons = _np.check_serving(serving)
    if not is_valid:
        current_app.logger.warning("[NUTRITION] Plan makroları makul değil %s — kısılıyor", reasons)
        kalori = min(kalori, _np.MAX_SERVING_KCAL)
        protein = min(protein, _np.MAX_SERVING_MACRO_G)
        karb = min(karb, _np.MAX_SERVING_MACRO_G)
        yag = min(yag, _np.MAX_SERVING_FAT_G)
    return kalori, protein, karb, yag


@bp.route("/api/quick-add-meal", methods=["POST"])
@login_required
def quick_add_meal():
    data     = request.get_json(silent=True) or {}
    meal_key = data.get("meal_key", "")

    MEAL_LABELS = {
        "kahvalti": "Kahvaltı",
        "ogle":     "Öğle",
        "aksam":    "Akşam",
        "ara_ogun": "Ara Öğün"
    }
    if meal_key not in MEAL_LABELS:
        return jsonify({"error": "Geçersiz öğün anahtarı."}), 400

    plan_record = NutritionPlan.query.filter_by(user_id=current_user.id)\
        .order_by(NutritionPlan.created_at.desc()).first()

    if not plan_record:
        return jsonify({"error": "Aktif beslenme planı bulunamadı."}), 404

    plan = json.loads(plan_record.plan_data)
    meal = plan.get(meal_key)

    if not meal:
        return jsonify({"error": "Bu öğün planda tanımlı değil."}), 404

    yemekler = ", ".join(meal.get("yemekler", []))
    today    = day_key()

    kalori, protein, karb, yag = _sanitize_meal_macros(
        round(float(meal.get("kalori",  0)), 1),
        round(float(meal.get("protein", 0)), 1),
        round(float(meal.get("karb",    0)), 1),
        round(float(meal.get("yag",     0)), 1),
    )
    entry = MealLog(
        user_id  = current_user.id,
        ogun     = MEAL_LABELS[meal_key],
        yemekler = yemekler,
        kalori   = kalori,
        protein  = protein,
        karb     = karb,
        yag      = yag,
        tarih    = today
    )
    entry.source = "ai_plan"
    db.session.add(entry)
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
    response = {
        "message": f"{MEAL_LABELS[meal_key]} planından eklendi.",
        "nutrients": {
            "kalori":  entry.kalori,
            "protein": entry.protein,
            "karb":    entry.karb,
            "yag":     entry.yag
        }
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


@bp.route("/api/diary/meal", methods=["POST"])
@login_required
def diary_create_meal():
    data = request.get_json(silent=True) or {}
    meal_name = data.get("meal_name", "").strip()
    date_key = data.get("date_key", app_today().isoformat())

    valid_meals = ("Kahvaltı", "Öğle", "Akşam", "Ara Öğün")
    if meal_name not in valid_meals:
        return jsonify({"error": "Geçersiz öğün adı"}), 400

    existing = CustomMeal.query.filter_by(
        user_id=current_user.id, meal_name=meal_name, date_key=date_key
    ).first()
    if existing:
        return jsonify({"meal_id": existing.id, "exists": True})

    meal = CustomMeal(user_id=current_user.id, meal_name=meal_name, date_key=date_key)
    db.session.add(meal)
    db.session.commit()
    return jsonify({"meal_id": meal.id, "exists": False})


@bp.route("/api/diary/meal/<int:meal_id>/item", methods=["POST"])
@login_required
def diary_add_item(meal_id):
    meal = CustomMeal.query.get(meal_id)
    if not meal or meal.user_id != current_user.id:
        return jsonify({"error": "Öğün bulunamadı"}), 404
    if meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400

    data = request.get_json(silent=True) or {}
    food_name = data.get("food_name", "").strip()
    food_id = data.get("fatsecret_food_id", "")

    if not food_name:
        return jsonify({"error": "Besin adı gerekli"}), 400

    srv_id = data.get("serving_id")
    if srv_id:
        qty = _to_float(data.get("serving_quantity", 1), 1)
        srv_cal = _to_float(data.get("serving_calories", 0))
        srv_pro = _to_float(data.get("serving_protein", 0))
        srv_carb = _to_float(data.get("serving_carbs", 0))
        srv_fat = _to_float(data.get("serving_fat", 0))
        metric_amt = _to_float(data.get("metric_serving_amount", 0))
        grams = round(metric_amt * qty, 1) if metric_amt else 0
        p100_cal = round(srv_cal / metric_amt * 100, 2) if metric_amt else 0
        p100_pro = round(srv_pro / metric_amt * 100, 2) if metric_amt else 0
        p100_carb = round(srv_carb / metric_amt * 100, 2) if metric_amt else 0
        p100_fat = round(srv_fat / metric_amt * 100, 2) if metric_amt else 0
        item = CustomMealItem(
            custom_meal_id=meal_id,
            food_name=food_name,
            grams=grams,
            calories=round(srv_cal * qty, 1),
            protein=round(srv_pro * qty, 1),
            carbs=round(srv_carb * qty, 1),
            fat=round(srv_fat * qty, 1),
            fatsecret_food_id=food_id or None,
            per_100g_calories=p100_cal,
            per_100g_protein=p100_pro,
            per_100g_carbs=p100_carb,
            per_100g_fat=p100_fat,
            serving_id=str(srv_id),
            serving_description=data.get("serving_description", ""),
            serving_quantity=qty,
        )
    else:
        grams = _to_float(data.get("grams", 100), 100)
        per_100g = data.get("per_100g")
        if not isinstance(per_100g, dict):
            per_100g = {}
        scale = grams / 100.0
        p100_cal = _to_float(per_100g.get("calories", 0))
        p100_pro = _to_float(per_100g.get("protein", 0))
        p100_carb = _to_float(per_100g.get("carbs", 0))
        p100_fat = _to_float(per_100g.get("fat", 0))
        item = CustomMealItem(
            custom_meal_id=meal_id,
            food_name=food_name,
            grams=grams,
            calories=round(p100_cal * scale, 1),
            protein=round(p100_pro * scale, 1),
            carbs=round(p100_carb * scale, 1),
            fat=round(p100_fat * scale, 1),
            fatsecret_food_id=food_id or None,
            per_100g_calories=p100_cal,
            per_100g_protein=p100_pro,
            per_100g_carbs=p100_carb,
            per_100g_fat=p100_fat,
        )

    db.session.add(item)
    db.session.commit()
    return jsonify({
        "item_id": item.id,
        "calories": item.calories,
        "protein": item.protein,
        "carbs": item.carbs,
        "fat": item.fat
    })


@bp.route("/api/diary/item/<int:item_id>", methods=["PATCH"])
@login_required
def diary_update_item(item_id):
    item = CustomMealItem.query.get(item_id)
    if not item or item.meal.user_id != current_user.id:
        return jsonify({"error": "Besin bulunamadı"}), 404
    if item.meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400

    data = request.get_json(silent=True) or {}
    srv_id = data.get("serving_id")

    if srv_id:
        qty = _to_float(data.get("serving_quantity", 1), 1)
        srv_cal = _to_float(data.get("serving_calories", 0))
        srv_pro = _to_float(data.get("serving_protein", 0))
        srv_carb = _to_float(data.get("serving_carbs", 0))
        srv_fat = _to_float(data.get("serving_fat", 0))
        metric_amt = _to_float(data.get("metric_serving_amount", 0))
        item.serving_id = str(srv_id)
        item.serving_description = data.get("serving_description", "")
        item.serving_quantity = qty
        item.grams = round(metric_amt * qty, 1) if metric_amt else 0
        item.calories = round(srv_cal * qty, 1)
        item.protein = round(srv_pro * qty, 1)
        item.carbs = round(srv_carb * qty, 1)
        item.fat = round(srv_fat * qty, 1)
        if metric_amt:
            item.per_100g_calories = round(srv_cal / metric_amt * 100, 2)
            item.per_100g_protein = round(srv_pro / metric_amt * 100, 2)
            item.per_100g_carbs = round(srv_carb / metric_amt * 100, 2)
            item.per_100g_fat = round(srv_fat / metric_amt * 100, 2)
    elif "serving_quantity" in data and item.serving_id:
        qty = _to_float(data["serving_quantity"], item.serving_quantity or 1)
        old_qty = item.serving_quantity or 1
        factor = qty / old_qty
        item.serving_quantity = qty
        item.grams = round(item.grams * factor, 1)
        item.calories = round(item.calories * factor, 1)
        item.protein = round(item.protein * factor, 1)
        item.carbs = round(item.carbs * factor, 1)
        item.fat = round(item.fat * factor, 1)
    else:
        grams = _to_float(data.get("grams", item.grams), item.grams)
        scale = grams / 100.0
        item.grams = grams
        item.calories = round((item.per_100g_calories or 0) * scale, 1)
        item.protein = round((item.per_100g_protein or 0) * scale, 1)
        item.carbs = round((item.per_100g_carbs or 0) * scale, 1)
        item.fat = round((item.per_100g_fat or 0) * scale, 1)

    db.session.commit()
    return jsonify({
        "item_id": item.id,
        "grams": item.grams,
        "calories": item.calories,
        "protein": item.protein,
        "carbs": item.carbs,
        "fat": item.fat
    })


@bp.route("/api/diary/item/<int:item_id>", methods=["DELETE"])
@login_required
def diary_delete_item(item_id):
    item = CustomMealItem.query.get(item_id)
    if not item or item.meal.user_id != current_user.id:
        return jsonify({"error": "Besin bulunamadı"}), 404
    if item.meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400
    db.session.delete(item)
    db.session.commit()
    return jsonify({"deleted": True})


@bp.route("/api/diary/meal/<int:meal_id>/log", methods=["POST"])
@login_required
def diary_log_meal(meal_id):
    meal = CustomMeal.query.get(meal_id)
    if not meal or meal.user_id != current_user.id:
        return jsonify({"error": "Öğün bulunamadı"}), 404
    if meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400
    if not meal.items:
        return jsonify({"error": "Öğüne en az bir besin ekle"}), 400

    total_cal = sum(i.calories for i in meal.items)
    total_pro = sum(i.protein for i in meal.items)
    total_karb = sum(i.carbs for i in meal.items)
    total_fat = sum(i.fat for i in meal.items)

    def _item_label(i):
        if i.serving_description and i.serving_quantity:
            qty = int(i.serving_quantity) if i.serving_quantity == int(i.serving_quantity) else i.serving_quantity
            return f"{i.food_name} ({qty}x {i.serving_description})"
        return f"{i.food_name} ({int(i.grams)}g)"
    yemekler = ", ".join(_item_label(i) for i in meal.items)
    today = day_key()

    entry = MealLog(
        user_id=current_user.id,
        ogun=meal.meal_name,
        yemekler=yemekler,
        kalori=round(total_cal, 1),
        protein=round(total_pro, 1),
        karb=round(total_karb, 1),
        yag=round(total_fat, 1),
        tarih=today,
        source="diary"
    )
    db.session.add(entry)
    meal.is_logged = True
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
    response = {
        "message": f"{meal.meal_name} kaydedildi.",
        "nutrients": {
            "kalori": entry.kalori,
            "protein": entry.protein,
            "karb": entry.karb,
            "yag": entry.yag
        }
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


@bp.route("/api/diary/today")
@login_required
def diary_today():
    today_key = app_today().isoformat()
    meals = CustomMeal.query.filter_by(
        user_id=current_user.id, date_key=today_key
    ).order_by(CustomMeal.id).all()

    result = []
    grand_total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        items = []
        meal_total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
        for i in m.items:
            items.append({
                "id": i.id,
                "food_name": i.food_name,
                "grams": i.grams,
                "calories": i.calories,
                "protein": i.protein,
                "carbs": i.carbs,
                "fat": i.fat,
                "per_100g": {
                    "calories": i.per_100g_calories,
                    "protein": i.per_100g_protein,
                    "carbs": i.per_100g_carbs,
                    "fat": i.per_100g_fat
                },
                "serving_id": i.serving_id,
                "serving_description": i.serving_description,
                "serving_quantity": i.serving_quantity,
                "fatsecret_food_id": i.fatsecret_food_id,
            })
            meal_total["calories"] += i.calories
            meal_total["protein"] += i.protein
            meal_total["carbs"] += i.carbs
            meal_total["fat"] += i.fat

        result.append({
            "id": m.id,
            "meal_name": m.meal_name,
            "is_logged": m.is_logged,
            "items": items,
            "totals": meal_total
        })

        for k in grand_total:
            grand_total[k] += meal_total[k]

    return jsonify({"meals": result, "totals": grand_total})
