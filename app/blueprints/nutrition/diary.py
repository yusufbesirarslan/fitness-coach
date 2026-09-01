"""Yemek günlüğü (CustomMeal/CustomMealItem) + hızlı ekle.

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
from flask import current_app, jsonify, request
from flask_login import current_user
from app.auth_middleware import require_auth
from sqlalchemy.exc import IntegrityError

from app.blueprints.nutrition import bp
from app.extensions import auth_write_limit, db
from app.i18n import t
from app.models import CustomMeal, CustomMealItem, MealLog, NutritionPlan
from app.services.gamification import complete_quest_for_user
from app.services import meal_idempotency
from app.services.mobile_log_food.parsing import (
    InvalidLogFoodCommand,
    parse_provider_identity,
    parse_provider_quantity,
)
from app.services.provider_food_snapshot import (
    ProviderFoodNotFound,
    resolve_provider_food,
)
from app.services.validators import _to_float
from app.timeutil import app_today, day_key


def _sanitize_meal_macros(kalori, protein, karb, yag):
    """LLM plan makrolarını nutrition_pipeline kapısıyla (menü/koç hattıyla aynı)
    denetle; fiziksel olarak imkânsız değerleri makul tavanlara kıs — aksi halde
    bir LLM saçmalığı (örn. 9999 kcal) doğrudan MealLog'a sızıyordu (F9).

    Oransal kırpma mantığı tek kaynakta (nutrition_pipeline.clamp_serving_macros);
    burada yalnızca kısılma olduysa loglarız."""
    from app.services import nutrition_pipeline as _np
    clamped = _np.clamp_serving_macros(kalori, protein, karb, yag)
    if clamped != (kalori, protein, karb, yag):
        current_app.logger.warning("[NUTRITION] Plan makroları makul değil — kısılıyor")
    return clamped


def _clamp_item_macros(item):
    """Bir CustomMealItem'in makrolarını nutrition_pipeline kapısından geçir
    (menü/koç/quick-add/override hatlarıyla AYNI tek kaynak: clamp_serving_macros).

    Diary hattı eskiden yalnızca negatifleri 0'a çekiyordu; üst fiziksel-tavan
    uygulanmadığından istemci `serving_calories: 90000` gibi bir değeri doğrudan
    CustomMealItem'a ve oradan kanonik MealLog'a sızdırabiliyordu — günlük
    toplamları, protein nudge'ını ve haftalık raporları bozardı (H1). Per-item
    kıyma, çok-öğeli meşru bir öğün toplamını bozmadan yalnızca aykırı öğeyi düzeltir.
    """
    from app.services import nutrition_pipeline as _np
    # clamp_serving_macros yalnızca POZİTİF üst-tavan taşmalarını oransal kırpar;
    # NEGATİF değerleri olduğu gibi geçirir. Bir istemci negatif serving_calories/
    # grams/serving_quantity göndererek MealLog toplamlarını (protein nudge, haftalık
    # rapor) aşağı çekebiliyordu (B6) — bu yüzden önce tüm makro/miktarları 0'a taban
    # yap, sonra üst tavanı uygula. Böylece tüm diary dalları tek kapıdan korunur.
    orig = (item.calories, item.protein, item.carbs, item.fat)
    item.calories = max(item.calories or 0, 0)
    item.protein = max(item.protein or 0, 0)
    item.carbs = max(item.carbs or 0, 0)
    item.fat = max(item.fat or 0, 0)
    if item.grams is not None:
        item.grams = max(item.grams, 0)
    if item.serving_quantity is not None:
        item.serving_quantity = max(item.serving_quantity, 0)
    cal, pro, carb, fat = _np.clamp_serving_macros(
        item.calories, item.protein, item.carbs, item.fat)
    if (cal, pro, carb, fat) != orig:
        current_app.logger.warning(
            "[DIARY] Öğe makroları makul değil — kısılıyor (food=%s)", item.food_name)
    item.calories, item.protein, item.carbs, item.fat = cal, pro, carb, fat


def _claim_diary_meal(meal_id, user_id):
    """Mark an unlogged meal as logged without committing the transaction."""
    return CustomMeal.query.filter_by(
        id=meal_id,
        user_id=user_id,
        is_logged=False,
    ).update({"is_logged": True}, synchronize_session=False)


# The diary PERSISTS the identities it validates, and its columns are narrower
# than the canonical 128-character transport bound. Deriving the effective
# bound from the model is what keeps the two from drifting apart: an identity
# the provider would accept but the column cannot hold must fail here, at the
# transport, rather than after the network call at the INSERT (PR3B, P2-01).
_IDENTITY_MAX_LENGTH = min(
    CustomMealItem.__table__.c.fatsecret_food_id.type.length,
    CustomMealItem.__table__.c.serving_id.type.length,
)


def _provider_identity(value):
    """One provider identity, on the canonical policy plus the storage bound."""
    return parse_provider_identity(value, _IDENTITY_MAX_LENGTH)


def _stored_quantity(value):
    """The default a PATCH/promotion re-resolves at when the caller omits one.

    A STORED quantity is not transport input, so a legacy row that never had
    one keeps the documented single serving; ``None`` arriving from the REQUEST
    is malformed and never reaches here (see `_transport_quantity`).
    """
    return 1 if value is None else value


def _transport_quantity(data, field, default):
    """Read one quantity from the request under the canonical typed policy.

    ``data.get(field, default)`` is the whole point: an OMITTED field keeps
    ``default``, while a field present as JSON ``null`` arrives as ``None`` and
    is rejected instead of silently becoming a plausible serving (P1-02).
    """
    return parse_provider_quantity(data.get(field, default))


def _provider_error(exc):
    db.session.rollback()
    if isinstance(exc, (ProviderFoodNotFound, ValueError)):
        return jsonify({"error": "invalid_serving"}), 400
    current_app.logger.exception("[DIARY] provider resolution failed")
    return jsonify({
        "error": "food_provider_unavailable",
        "retryable": True,
    }), 503


def _apply_provider_snapshot(item, snapshot):
    nutrition = snapshot.nutrition
    per_100g = snapshot.nutrition_per_100g
    item.food_name = snapshot.food_name
    item.fatsecret_food_id = snapshot.food_id
    item.serving_id = snapshot.serving_id
    item.serving_description = snapshot.serving_description
    item.serving_quantity = float(snapshot.quantity)
    item.grams = round(float(snapshot.grams), 1)
    item.calories = round(float(nutrition.energy_kcal), 1)
    item.protein = round(float(nutrition.protein_g), 1)
    item.carbs = round(float(nutrition.carbohydrate_g), 1)
    item.fat = round(float(nutrition.fat_g), 1)
    if per_100g:
        item.per_100g_calories = round(float(per_100g.energy_kcal), 2)
        item.per_100g_protein = round(float(per_100g.protein_g), 2)
        item.per_100g_carbs = round(float(per_100g.carbohydrate_g), 2)
        item.per_100g_fat = round(float(per_100g.fat_g), 2)
    else:
        item.per_100g_calories = None
        item.per_100g_protein = None
        item.per_100g_carbs = None
        item.per_100g_fat = None
    _clamp_item_macros(item)


def _item_state(item):
    return (
        item.id, item.custom_meal_id, item.food_name, item.grams,
        item.calories, item.protein, item.carbs, item.fat,
        item.fatsecret_food_id, item.per_100g_calories,
        item.per_100g_protein, item.per_100g_carbs, item.per_100g_fat,
        item.serving_id, item.serving_description, item.serving_quantity,
    )


@bp.route("/api/quick-add-meal", methods=["POST"])
@require_auth
@auth_write_limit
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
        return jsonify({"error": t("route.invalid_meal_key")}), 400

    idempotency_key = meal_idempotency.read_idempotency_key()
    existing = meal_idempotency.find_existing(current_user.id, idempotency_key)
    if existing:
        return jsonify({
            "message": t("route.added_from_plan", meal=existing.ogun),
            "nutrients": {
                "kalori": existing.kalori,
                "protein": existing.protein,
                "karb": existing.karb,
                "yag": existing.yag,
            },
        })

    plan_record = NutritionPlan.query.filter_by(user_id=current_user.id)\
        .order_by(NutritionPlan.created_at.desc()).first()

    if not plan_record:
        return jsonify({"error": t("route.no_active_nutrition_plan")}), 404

    # plan_data LLM üretimi ve şema doğrulaması yapılmadan kaydediliyor — bozuk
    # JSON / beklenmedik tipler 500 yerine temiz hata döndürmeli (A4).
    try:
        plan = json.loads(plan_record.plan_data)
    except (json.JSONDecodeError, TypeError):
        plan = {}
    meal = plan.get(meal_key) if isinstance(plan, dict) else None

    # Boş/None/dict-olmayan meal reddedilir (eski `if not meal` davranışı korunur) —
    # aksi halde boş öğün kanonik deftere 0-makro satır yazardı.
    if not isinstance(meal, dict) or not meal:
        return jsonify({"error": t("route.meal_not_in_plan")}), 404

    # yemekler liste olmayabilir / öğeleri str olmayabilir → güvenle str listesine indir.
    raw_yemekler = meal.get("yemekler", [])
    if isinstance(raw_yemekler, str):
        raw_yemekler = [raw_yemekler]
    elif not isinstance(raw_yemekler, list):
        raw_yemekler = []
    yemekler = ", ".join(str(y) for y in raw_yemekler if y is not None)
    today    = day_key()

    # Makrolar da LLM'den; bare float() yerine _to_float (örn. "400 kcal" → 0) ki
    # sayısal-olmayan değer 500 atmasın — diğer makro girişleriyle tutarlı.
    kalori, protein, karb, yag = _sanitize_meal_macros(
        round(_to_float(meal.get("kalori",  0)), 1),
        round(_to_float(meal.get("protein", 0)), 1),
        round(_to_float(meal.get("karb",    0)), 1),
        round(_to_float(meal.get("yag",     0)), 1),
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
    entry, created = meal_idempotency.commit_once(entry, idempotency_key)

    quest_result = complete_quest_for_user(current_user.id, "meal_logged") if created else None
    response = {
        "message": t("route.added_from_plan", meal=MEAL_LABELS[meal_key]),
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
@require_auth
@auth_write_limit
def diary_create_meal():
    data = request.get_json(silent=True) or {}
    meal_name = data.get("meal_name", "").strip()
    date_key = day_key()

    valid_meals = ("Kahvaltı", "Öğle", "Akşam", "Ara Öğün")
    if meal_name not in valid_meals:
        return jsonify({"error": t("route.invalid_meal_name")}), 400

    existing = CustomMeal.query.filter_by(
        user_id=current_user.id, meal_name=meal_name, date_key=date_key
    ).first()
    if existing:
        return jsonify({"meal_id": existing.id, "exists": True})

    meal = CustomMeal(user_id=current_user.id, meal_name=meal_name, date_key=date_key)
    db.session.add(meal)
    try:
        db.session.commit()
    except IntegrityError:
        # Yarış: eşzamanlı iki POST da existence-check'i aştı; uq_custom_meal_day
        # ikinci INSERT'i reddetti. 500 yerine rollback + re-query → mevcut satırı
        # döndür (set_water / log_daily_activity ile aynı desen).
        db.session.rollback()
        existing = CustomMeal.query.filter_by(
            user_id=current_user.id, meal_name=meal_name, date_key=date_key
        ).first()
        if existing:
            return jsonify({"meal_id": existing.id, "exists": True})
        raise
    return jsonify({"meal_id": meal.id, "exists": False})


@bp.route("/api/diary/meal/<int:meal_id>/item", methods=["POST"])
@require_auth
@auth_write_limit
def diary_add_item(meal_id):
    meal = db.session.get(CustomMeal, meal_id)
    if not meal or meal.user_id != current_user.id:
        return jsonify({"error": t("route.meal_not_found")}), 404
    if meal.is_logged:
        return jsonify({"error": t("route.meal_already_logged")}), 400

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_serving"}), 400
    food_name = data.get("food_name", "").strip()
    # PRESENCE of either identity field is the provider claim, exactly as the
    # canonical parser treats it: a field present as `null` is a malformed
    # identity, not an absent one, so it cannot silently select manual mode.
    provider_backed = bool({"fatsecret_food_id", "serving_id"} & set(data))

    if not food_name:
        return jsonify({"error": t("route.food_name_required")}), 400

    if provider_backed:
        # The complete provider command is validated - typed, bounded, and
        # fail-closed - BEFORE any network I/O, on the canonical LogFood
        # policy. An incomplete half never falls back to caller-owned manual
        # nutrition, and a malformed value never becomes a default.
        try:
            food_id = _provider_identity(data.get("fatsecret_food_id"))
            srv_id = _provider_identity(data.get("serving_id"))
            quantity = _transport_quantity(data, "serving_quantity", 1)
        except InvalidLogFoodCommand as exc:
            return _provider_error(exc)

        # The ownership check above opened a read transaction. Provider I/O must
        # not hold it open; after resolution we lock and re-check the meal.
        db.session.rollback()
        try:
            snapshot = resolve_provider_food(
                "fatsecret", food_id, srv_id, quantity)
        except Exception as exc:
            return _provider_error(exc)

        meal = CustomMeal.query.filter_by(
            id=meal_id, user_id=current_user.id).with_for_update().first()
        if not meal:
            db.session.rollback()
            return jsonify({"error": t("route.meal_not_found")}), 404
        if meal.is_logged:
            db.session.rollback()
            return jsonify({"error": t("route.meal_already_logged")}), 400
        item = CustomMealItem(
            custom_meal_id=meal_id,
            food_name=snapshot.food_name, grams=0,
            calories=0, protein=0, carbs=0, fat=0,
        )
        _apply_provider_snapshot(item, snapshot)
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
            fatsecret_food_id=None,
            per_100g_calories=p100_cal,
            per_100g_protein=p100_pro,
            per_100g_carbs=p100_carb,
            per_100g_fat=p100_fat,
        )

    # H1: kanonik deftere (MealLog) toplanmadan ÖNCE öğeyi fiziksel-sağlık
    # kapısından geçir — diğer tüm ingest hatlarıyla aynı tek kaynak.
    _clamp_item_macros(item)
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
@require_auth
@auth_write_limit
def diary_update_item(item_id):
    item = db.session.get(CustomMealItem, item_id)
    if not item or item.meal.user_id != current_user.id:
        return jsonify({"error": t("route.food_not_found")}), 404
    if item.meal.is_logged:
        return jsonify({"error": t("route.meal_already_logged")}), 400

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_serving"}), 400
    before = _item_state(item)
    food_id = str(item.fatsecret_food_id or "").strip()
    stored_serving_id = str(item.serving_id or "").strip()

    if bool(food_id) != bool(stored_serving_id):
        return jsonify({"error": "invalid_serving"}), 400

    # Provider-identity transport fields are validated BEFORE the manual or
    # provider branch is chosen, so a request cannot claim provider identity
    # and be answered as a caller-authoritative manual update (P1-01). The food
    # identity is server-stored: serving and quantity are the only selections
    # this transport owns, so a request-supplied food id - matching the stored
    # one or not - is an attempted identity conversion and fails closed.
    if "fatsecret_food_id" in data:
        return jsonify({"error": "invalid_serving"}), 400

    if food_id:
        if "grams" in data or "per_100g" in data:
            return jsonify({"error": "invalid_serving"}), 400
        try:
            serving_id = (_provider_identity(data["serving_id"])
                          if "serving_id" in data else stored_serving_id)
            quantity = _transport_quantity(
                data, "serving_quantity", _stored_quantity(item.serving_quantity))
        except InvalidLogFoodCommand as exc:
            return _provider_error(exc)

        db.session.rollback()
        try:
            snapshot = resolve_provider_food(
                "fatsecret", food_id, serving_id, quantity)
        except Exception as exc:
            return _provider_error(exc)

        meal = CustomMeal.query.filter_by(
            id=before[1], user_id=current_user.id).with_for_update().first()
        item = CustomMealItem.query.filter_by(id=item_id).with_for_update().first()
        if not meal or not item or item.custom_meal_id != meal.id:
            db.session.rollback()
            return jsonify({"error": t("route.food_not_found")}), 404
        if meal.is_logged:
            db.session.rollback()
            return jsonify({"error": t("route.meal_already_logged")}), 400
        if _item_state(item) != before:
            db.session.rollback()
            return jsonify({"error": "diary_item_changed"}), 409
        _apply_provider_snapshot(item, snapshot)
    else:
        if "serving_id" in data or "serving_quantity" in data:
            return jsonify({"error": "invalid_serving"}), 400
        db.session.rollback()
        meal = CustomMeal.query.filter_by(
            id=before[1], user_id=current_user.id).with_for_update().first()
        item = CustomMealItem.query.filter_by(id=item_id).with_for_update().first()
        if not meal or not item or item.custom_meal_id != meal.id:
            db.session.rollback()
            return jsonify({"error": t("route.food_not_found")}), 404
        if meal.is_logged:
            db.session.rollback()
            return jsonify({"error": t("route.meal_already_logged")}), 400
        if _item_state(item) != before:
            db.session.rollback()
            return jsonify({"error": "diary_item_changed"}), 409
        grams = _to_float(data.get("grams", item.grams), item.grams)
        scale = grams / 100.0
        item.grams = grams
        item.calories = round((item.per_100g_calories or 0) * scale, 1)
        item.protein = round((item.per_100g_protein or 0) * scale, 1)
        item.carbs = round((item.per_100g_carbs or 0) * scale, 1)
        item.fat = round((item.per_100g_fat or 0) * scale, 1)

    # H1: her güncelleme dalından sonra (yeni serving, miktar/gram yeniden
    # ölçekleme) öğeyi tekrar fiziksel-sağlık kapısından geçir.
    _clamp_item_macros(item)
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
@require_auth
@auth_write_limit
def diary_delete_item(item_id):
    item = db.session.get(CustomMealItem, item_id)
    if not item or item.meal.user_id != current_user.id:
        return jsonify({"error": t("route.food_not_found")}), 404
    meal_id = item.custom_meal_id
    if item.meal.is_logged:
        return jsonify({"error": t("route.meal_already_logged")}), 400
    db.session.rollback()
    meal = CustomMeal.query.filter_by(
        id=meal_id, user_id=current_user.id).with_for_update().first()
    item = CustomMealItem.query.filter_by(id=item_id).with_for_update().first()
    if not meal or not item or item.custom_meal_id != meal.id:
        db.session.rollback()
        return jsonify({"error": t("route.food_not_found")}), 404
    if meal.is_logged:
        db.session.rollback()
        return jsonify({"error": t("route.meal_already_logged")}), 400
    db.session.delete(item)
    db.session.commit()
    return jsonify({"deleted": True})


@bp.route("/api/diary/meal/<int:meal_id>/log", methods=["POST"])
@require_auth
@auth_write_limit
def diary_log_meal(meal_id):
    meal = db.session.get(CustomMeal, meal_id)
    if not meal or meal.user_id != current_user.id:
        return jsonify({"error": t("route.meal_not_found")}), 404
    if meal.is_logged:
        return jsonify({"error": t("route.meal_already_logged")}), 400
    if not meal.items:
        return jsonify({"error": t("route.add_one_food")}), 400

    meal_name = meal.meal_name
    staging_items = sorted(meal.items, key=lambda item: item.id)
    original_states = [_item_state(item) for item in staging_items]
    provider_selections = []
    for item in staging_items:
        food_id = str(item.fatsecret_food_id or "").strip()
        serving_id = str(item.serving_id or "").strip()
        if bool(food_id) != bool(serving_id):
            return jsonify({"error": "invalid_serving"}), 400
        if food_id:
            try:
                quantity = parse_provider_quantity(
                    _stored_quantity(item.serving_quantity))
            except InvalidLogFoodCommand as exc:
                return _provider_error(exc)
            provider_selections.append(
                (item.id, food_id, serving_id, quantity))

    # Close the staging read transaction before any blocking provider call.
    db.session.rollback()
    resolved = {}
    try:
        for item_id, food_id, serving_id, quantity in provider_selections:
            resolved[item_id] = resolve_provider_food(
                "fatsecret", food_id, serving_id, quantity)
    except Exception as exc:
        return _provider_error(exc)

    meal = CustomMeal.query.filter_by(
        id=meal_id, user_id=current_user.id).with_for_update().first()
    if not meal:
        db.session.rollback()
        return jsonify({"error": t("route.meal_not_found")}), 404
    if meal.is_logged:
        db.session.rollback()
        return jsonify({"error": t("route.meal_already_logged")}), 400
    items = CustomMealItem.query.filter_by(custom_meal_id=meal_id)\
        .order_by(CustomMealItem.id).with_for_update().all()
    if [_item_state(item) for item in items] != original_states:
        db.session.rollback()
        return jsonify({"error": "diary_meal_changed"}), 409

    for item in items:
        if item.id in resolved:
            _apply_provider_snapshot(item, resolved[item.id])

    total_cal = sum(i.calories for i in items)
    total_pro = sum(i.protein for i in items)
    total_karb = sum(i.carbs for i in items)
    total_fat = sum(i.fat for i in items)

    def _item_label(i):
        if i.serving_description and i.serving_quantity:
            qty = int(i.serving_quantity) if i.serving_quantity == int(i.serving_quantity) else i.serving_quantity
            return f"{i.food_name} ({qty}x {i.serving_description})"
        return f"{i.food_name} ({int(i.grams)}g)"
    yemekler = ", ".join(_item_label(i) for i in items)
    today = day_key()

    if not _claim_diary_meal(meal_id, current_user.id):
        db.session.rollback()
        return jsonify({"error": t("route.meal_already_logged")}), 400

    entry = MealLog(
        user_id=current_user.id,
        ogun=meal_name,
        yemekler=yemekler,
        kalori=round(total_cal, 1),
        protein=round(total_pro, 1),
        karb=round(total_karb, 1),
        yag=round(total_fat, 1),
        tarih=today,
        source="diary"
    )
    db.session.add(entry)
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
    response = {
        "message": t("route.x_logged", name=meal_name),
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
@require_auth
def diary_today():
    """Diary-builder görünümü: bugünün CustomMeal'leri + diary'ye özel grand_total.

    Bu, beslenme defterinin (diary sekmesi) KENDİ toplamıdır; kullanıcının gün
    içinde diary'de oluşturduğu tüm öğünleri (kaydedilmiş + devam eden) gösterir.
    KANONİK 'bugün yenenler' kaynağı DEĞİLDİR — o /meal-log/today (MealLog).
    'Kaydedilmiş' (is_logged) bir öğün ayrıca MealLog'a da yazıldığından, bu
    grand_total /meal-log/today toplamıyla ASLA TOPLANMAMALIDIR (çift sayım)."""
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
