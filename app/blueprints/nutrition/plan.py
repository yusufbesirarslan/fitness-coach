"""Beslenme planı: AI üretim + kaydet/aktif plan (freemium kotalı).

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
from flask import current_app, jsonify, render_template, request
from flask_login import current_user
from app.auth_middleware import require_auth

from app.blueprints.nutrition import bp
from app.config import AI_RATELIMIT, BEDROCK_RATELIMIT
from app.extensions import _user_or_ip_key, limiter
from app.i18n import current_locale, t
from app.models import NutritionPlan, UserSession
from app.services.ai import _heavy_chat
from app.services.ai_gate import ai_concurrency_gate
from app.services import nutrition_plan_generation as generation
from app.services.nutrition_plan_store import (
    UNCONDITIONAL,
    replace_nutrition_plan,
)
from app.services.nutrition_plan_schema import (
    NutritionPlanInvalid,
    validate_nutrition_plan_for_save,
)
from app.services.plan_score import PlanScoreInvalid, parse_plan_score
from app.services.premium import premium_ai_plan_gate
from app.timeutil import display_dt


@bp.route("/nutrition-plan/save", methods=["POST"])
@require_auth
def save_nutrition_plan():
    data = request.get_json(silent=True) or {}
    plan = data.get("plan")
    score = data.get("score")

    if not plan:
        return jsonify({"error": t("route.plan_data_missing")}), 400

    # SIRA GARANTİDİR: her iki doğrulama da TEK BİR satır silinmeden ÖNCE
    # çalışır, yani geçersiz bir istek kullanıcının mevcut planını yok edemez.
    # F3 — `score` bir db.Float kolonuna gider; "abc" eskiden route'tan geçip
    # flush'ta patlıyor ve yalnızca JSON okuyan çağırana HTML 500 döndürüyordu.
    try:
        score = parse_plan_score(score)
    except PlanScoreInvalid as exc:
        current_app.logger.info(
            "[NUTRITION] save_score_rejected reason=%s", exc.reason)
        return jsonify(exc.to_body(t)), exc.http_status

    # F2: persist the CANONICAL document, never the client's. Anything the
    # schema does not name is refused rather than dropped, so what /active
    # hands back to innerHTML is a closed, bounded field set.
    try:
        plan = validate_nutrition_plan_for_save(plan)
    except NutritionPlanInvalid as exc:
        current_app.logger.info(
            "[NUTRITION] save_schema_rejected reason=%s", exc.reason)
        return jsonify(exc.to_body(t)), exc.http_status

    # Eski planı sil, yenisini kaydet — the ONE replacement boundary, shared
    # with the native transport (NUTR-PR7). Browser semantics are unchanged:
    # unconditional replacement (the accepted PR5 cross-tab residual).
    replace_nutrition_plan(current_user.id, plan, score, UNCONDITIONAL)

    return jsonify({"message": t("route.plan_saved")})


@bp.route("/nutrition-plan/active")
@require_auth
def get_active_nutrition_plan():
    plan = NutritionPlan.query.filter_by(user_id=current_user.id)\
        .order_by(NutritionPlan.created_at.desc())\
        .first()

    if not plan:
        return jsonify({"exists": False})

    return jsonify({
        "exists"    : True,
        "plan"      : json.loads(plan.plan_data),
        "score"     : plan.score,
        "created_at": display_dt(plan.created_at, "%d.%m.%Y")
    })


@bp.route("/nutrition")
@require_auth
def nutrition():
    return render_template("nutrition.html", username=current_user.username, profile_picture=current_user.avatar_src)


@bp.route("/nutrition-plan", methods=["POST"])
@require_auth
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
@limiter.limit(BEDROCK_RATELIMIT, key_func=_user_or_ip_key)  # Sonnet üretimi: daha sıkı tavan
@premium_ai_plan_gate("nutrition")  # non-premium: haftada 1 üretim
@ai_concurrency_gate  # A1: bloklayıcı AI çağrıları tüm thread'leri doldurmasın
def nutrition_plan_generate():
    data = request.get_json(silent=True) or {}
    # Kullanıcının son oturumundan kalori hedefini al
    last = UserSession.query.filter_by(user_id=current_user.id)        .order_by(UserSession.created_at.desc())        .first()

    if not last:
        return jsonify({"error": t("plan.no_session")}), 400

    target_calories = last.target_calories
    goal            = last.goal
    if target_calories is None:
        return jsonify({"error": t("plan.no_session")}), 400

    # Seçilen gıdalar
    selected_proteins = data.get("proteins", [])
    selected_carbs    = data.get("carbs", [])
    selected_fats     = data.get("fats", [])
    custom_foods      = data.get("custom_foods", [])

    if not selected_proteins or not selected_carbs or not selected_fats:
        return jsonify({"error": t("nutrition.plan.pick_one")}), 400

    # Catalogue, rating, prompt and parse are the ONE shared generation
    # authority (app/services/nutrition_plan_generation.py, NUTR-PR7) — the
    # native transport runs exactly the same code.
    overall_score = generation.food_rating(
        selected_proteins + selected_carbs + selected_fats)
    score_label, score_color = generation.rating_label(overall_score)

    # AI'a gönder — dile göre prompt. JSON ANAHTARLARI HER DİLDE TÜRKÇE KALIR.
    lang = current_locale()

    try:
        planlar = generation.generate_plan_options(
            _heavy_chat, lang, target_calories, goal, selected_proteins,
            selected_carbs, selected_fats, custom_foods)

        return jsonify({
            "planlar"      : planlar,
            "overall_score": overall_score,
            "score_label"  : score_label,
            "score_color"  : score_color,
            "target_calories": round(target_calories)
        })

    except json.JSONDecodeError:
        return jsonify({"error": t("plan.gen_failed")}), 500
    except Exception:
        current_app.logger.exception("Plan oluşturma hatası")
        return jsonify({"error": t("route.plan_failed")}), 500
