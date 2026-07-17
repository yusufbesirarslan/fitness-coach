"""MealLog defteri: foto/AI makro tahmini, bugün/geçmiş, değerlendirme.

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
import s3_helper
from flask import Response, current_app, jsonify, request
from flask_login import current_user
from app.auth_middleware import require_auth

from app.blueprints.nutrition import bp
from app.config import AI_RATELIMIT
from app.extensions import _user_or_ip_key, db, limiter
from app.i18n import current_locale, t
from app.models import MealLog, UserSession
from app.prompts import nutrition as nutrition_prompts
from app.services.ai import _openai_chat
from app.services.nutrition_pipeline import sanitize_meal_total_macros
from app.services.gamification import complete_quest_for_user
from app.services import meal_idempotency
from app.services.validators import _meal_photo_url, _to_float, validate_meal_photo
from app.timeutil import day_key, display_ddmm


@bp.route("/meal-log", methods=["POST"])
@require_auth
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
def log_meal():
    data = request.get_json(silent=True) or {}
    ogun     = data.get("ogun", "")
    yemekler = data.get("yemekler", "")

    if not ogun or not yemekler:
        return jsonify({"error": t("route.meal_foods_required")}), 400

    idempotency_key = meal_idempotency.read_idempotency_key()
    existing = meal_idempotency.find_existing(current_user.id, idempotency_key)
    if existing:
        response = {
            "message": t("route.x_logged", name=existing.ogun),
            "nutrients": {
                "kalori": existing.kalori,
                "protein": existing.protein,
                "karb": existing.karb,
                "yag": existing.yag,
            },
        }
        if existing.photo_key:
            response["photo_url"] = _meal_photo_url(existing)
        return jsonify(response)

    # Opsiyonel öğün fotoğrafı: doğrula ve (varsa) S3'e yükle. S3 hatası öğün
    # kaydını bloklamaz (fail-open) — yalnızca foto atlanır.
    photo_bytes, photo_mime, photo_err = validate_meal_photo(data.get("image"))
    if photo_err:
        return jsonify({"error": photo_err}), 400
    meal_photo_key = None
    if photo_bytes:
        try:
            if s3_helper.is_enabled():
                meal_photo_key = s3_helper.upload_image(
                    photo_bytes, content_type=photo_mime,
                    prefix="meals", user_id=current_user.id,
                )
        except Exception as e:
            current_app.logger.info(f"[S3] Öğün fotoğrafı yüklemesi başarısız: {type(e).__name__}: {e}")

    _FITNESS_DICT = {
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*(?:whey|protein\s*tozu|protein\s*powder)':
            lambda m: (f"{m.group(1)} ölçek whey protein tozu ({int(m.group(1))*30}g)", None),
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*kreatin':
            lambda m: (f"{m.group(1)} ölçek kreatin ({int(m.group(1))*5}g)", None),
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*(?:kazein|casein)':
            lambda m: (f"{m.group(1)} ölçek kazein protein ({int(m.group(1))*33}g)", None),
        r'(?i)\b(\d+)\s*(?:adet\s+)?pirinç\s*patlağı':
            lambda m: (f"{m.group(1)} adet pirinç patlağı ({int(m.group(1))*8}g)", None),
        r'(?i)\bprotein\s*bar[ıi]?\b':
            lambda m: ("1 protein bar (60g)", None),
        r'(?i)\b(\d+)\s*(?:kaşık|tbsp)\s*fıstık\s*ezmesi':
            lambda m: (f"{m.group(1)} yemek kaşığı fıstık ezmesi ({int(m.group(1))*15}g)", None),
        r'(?i)\b(\d+)\s*(?:kaşık|tbsp)\s*(?:bal|honey)':
            lambda m: (f"{m.group(1)} yemek kaşığı bal ({int(m.group(1))*21}g)", None),
        r'(?i)\bbcaa\b':
            lambda m: ("1 ölçek BCAA (7g)", None),
    }
    import re as _re
    normalized_yemekler = yemekler
    for pattern, handler in _FITNESS_DICT.items():
        match = _re.search(pattern, normalized_yemekler)
        if match:
            replacement, _ = handler(match)
            normalized_yemekler = _re.sub(pattern, replacement, normalized_yemekler, count=1)
    if normalized_yemekler != yemekler:
        current_app.logger.info("[MEAL] Fitness shorthand normalized")
    yemekler_for_prompt = normalized_yemekler

    override = data.get("override_macros")
    if override and isinstance(override, dict):
        # Kullanıcı override'ı doğrudan kanonik MealLog'a YAZMADAN ÖNCE fiziksel-
        # sağlık kapısından geçir (menü/koç/diary hatlarıyla aynı tek kaynak) —
        # aksi halde request-kontrollü çöp (örn. 9999 kcal) deftere sızıp günlük
        # toplamları, protein nudge'ını ve haftalık raporları bozardı (C1).
        from app.services import nutrition_pipeline as _np
        kalori, protein, karb, yag = _np.clamp_serving_macros(
            round(_to_float(override.get("kalori", 0)), 1),
            round(_to_float(override.get("protein", 0)), 1),
            round(_to_float(override.get("karb", 0)), 1),
            round(_to_float(override.get("yag", 0)), 1),
        )
        nutrients = {"kalori": kalori, "protein": protein, "karb": karb, "yag": yag}
        today = day_key()
        entry = MealLog(
            user_id=current_user.id, ogun=ogun, yemekler=yemekler,
            kalori=nutrients["kalori"], protein=nutrients["protein"],
            karb=nutrients["karb"], yag=nutrients["yag"], tarih=today,
            photo_key=meal_photo_key
        )
        entry, created = meal_idempotency.commit_once(entry, idempotency_key)
        # C5: override yolu da AI-hesaplı yolla aynı 'meal_logged' görevini
        # vermeli — kullanıcı makroyu elle girdi diye günlük görev/XP atlanmasın.
        quest_result = complete_quest_for_user(current_user.id, "meal_logged") if created else None
        resp = {"message": t("route.x_logged", name=ogun), "nutrients": nutrients}
        if meal_photo_key:
            resp["photo_url"] = _meal_photo_url(entry)
        if quest_result:
            resp["quest_awarded"] = quest_result
        return jsonify(resp)

    # İstem şablonu app/prompts/nutrition.py'de (WS4).
    prompt = nutrition_prompts.build_meal_totals_prompt(yemekler_for_prompt)

    nutrients = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    raw = ""
    parsed_ok = False

    try:
        raw = _openai_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=nutrition_prompts.MEAL_TOTALS_SYSTEM,
            max_tokens=150,
            temperature=0.0,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        # Bazen AI fazladan metin ekliyor, sadece { } arasını al
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        nutrients = json.loads(raw)
        kalori, protein, karb, yag = sanitize_meal_total_macros(
            nutrients.get("kalori"),
            nutrients.get("protein"),
            nutrients.get("karb"),
            nutrients.get("yag"),
        )
        nutrients = {
            "kalori": kalori,
            "protein": protein,
            "karb": karb,
            "yag": yag,
        }
        parsed_ok = True
    except Exception as e:
        current_app.logger.info("MEAL LOG ERROR: %s", type(e).__name__)

    # AI çağrısı/JSON parse başarısızsa nutrients tümü 0 kalır; bunu kanonik
    # MealLog defterine YAZMA — kalıcı sıfır-makro satırı günlük toplamları,
    # protein nudge'ını ve haftalık raporları bozar (sessizce). Hatayı kullanıcıya
    # döndür ki tekrar denesin; commit etme.
    if not parsed_ok:
        return jsonify({
            "error": t("route.meal_calc_failed")
        }), 502

    today = day_key()

    entry = MealLog(
        user_id  = current_user.id,
        ogun     = ogun,
        yemekler = yemekler,
        kalori   = nutrients.get("kalori", 0),
        protein  = nutrients.get("protein", 0),
        karb     = nutrients.get("karb", 0),
        yag      = nutrients.get("yag", 0),
        tarih    = today,
        photo_key = meal_photo_key
    )
    entry, created = meal_idempotency.commit_once(entry, idempotency_key)

    quest_result = complete_quest_for_user(current_user.id, "meal_logged") if created else None
    response = {
        "message": t("route.x_logged", name=ogun),
        "nutrients": nutrients,
    }
    if meal_photo_key:
        response["photo_url"] = _meal_photo_url(entry)
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


@bp.route("/meal-log/today")
@require_auth
def today_meals():
    """KANONİK 'bugün yenenler' defteri ve toplamları (MealLog — tek doğru kaynak).

    Ana sayfa/beslenme halkaları (templates/index.html, static/nutrition.js
    loadTodayData) günlük kalori/makro toplamı için YALNIZCA bunu kullanır.
    DİKKAT: /api/diary/today toplamlarıyla TOPLANMAZ — diary'de 'kaydedilmiş'
    (is_logged) öğünler zaten buraya MealLog satırı olarak yazılır; iki yüzeyi
    toplamak çift sayım olur (bkz. diary_today docstring)."""
    today = day_key()
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today)\
        .order_by(MealLog.created_at.asc()).all()

    result = []
    totals = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    for m in meals:
        result.append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag,
            "source": getattr(m, "source", "manual") or "manual",
            "photo_url": _meal_photo_url(m),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
        totals["kalori"]  += m.kalori or 0
        totals["protein"] += m.protein or 0
        totals["karb"]    += m.karb or 0
        totals["yag"]     += m.yag or 0

    return jsonify({"meals": result, "totals": totals, "tarih": display_ddmm(today)})


@bp.route("/meal-log/history")
@require_auth
def meal_history():
    # Önce en yeni N GÜN'ün anahtarlarını al, sonra YALNIZCA o günlerin TÜM
    # satırlarını çek. Eski "ilk 50 satır çek → sonra güne göre grupla" yaklaşımı,
    # bir gün 50-satır sınırına denk geldiğinde o günün toplamını yalnızca sınır
    # içindeki satırlardan hesaplayıp kalori/makroyu sessizce DÜŞÜK gösteriyordu
    # (M2). Gün-sayısıyla sınırlamak her gösterilen günün tam toplamını garanti eder.
    HISTORY_DAYS = 14
    recent_days = [r[0] for r in db.session.query(MealLog.tarih)
        .filter(MealLog.user_id == current_user.id)
        .group_by(MealLog.tarih)
        .order_by(MealLog.tarih.desc())
        .limit(HISTORY_DAYS).all()]

    meals = (MealLog.query.filter(
        MealLog.user_id == current_user.id,
        MealLog.tarih.in_(recent_days),
    ).order_by(MealLog.created_at.desc()).all() if recent_days else [])

    days = {}
    for m in meals:
        if m.tarih not in days:
            days[m.tarih] = {"meals": [], "totals": {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}}
        days[m.tarih]["meals"].append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag,
            "photo_url": _meal_photo_url(m)
        })
        days[m.tarih]["totals"]["kalori"]  += m.kalori or 0
        days[m.tarih]["totals"]["protein"] += m.protein or 0
        days[m.tarih]["totals"]["karb"]    += m.karb or 0
        days[m.tarih]["totals"]["yag"]     += m.yag or 0

    result = [{"tarih": display_ddmm(k), **v} for k, v in days.items()]
    return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")


@bp.route("/meal-log/review", methods=["POST"])
@require_auth
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
def review_meals():
    today = day_key()
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today).all()

    if not meals:
        return jsonify({"error": t("route.no_meals_today")}), 400

    last_session = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()

    target = last_session.target_calories if last_session else 2000
    goal   = last_session.goal if last_session else "genel sağlık"

    meals_text = ""
    total_cal = 0
    for m in meals:
        meals_text += f"- {m.ogun}: {m.yemekler} ({m.kalori} kcal, P:{m.protein}g K:{m.karb}g Y:{m.yag}g)\n"
        total_cal += m.kalori or 0

    # Değerlendirme metni kullanıcıya gösterilir → dile göre üret. meals_text içindeki
    # öğün/yemek adları (kanonik) girdi bağlamıdır; AI hedef dilde özetler.
    if current_locale() == "en":
        prompt = (
            "You are a nutrition coach. Write in English. Address the user as 'you'.\n\n"
            f"User's goal: {goal}\n"
            f"Daily calorie target: {round(target)} kcal\n"
            f"Total today: {round(total_cal)} kcal\n\n"
            f"What they ate today:\n{meals_text}\n"
            "Evaluate today:\n"
            "- Did they reach the calorie target?\n"
            "- Is the macro distribution balanced?\n"
            "- How is it in terms of bioavailability?\n"
            "- Is the gluten content high?\n"
            "- Is there anything that should change?\n"
            "Be short and specific, 4-5 sentences is enough."
        )
        system_prompt = "You are a nutrition coach. Speak briefly, specifically, in English."
    else:
        prompt = (
            f"Sen bir beslenme koçusun. Türkçe yaz, İngilizce kullanma.\n"
            f"Kullanıcıya 'sen' diye hitap et.\n\n"
            f"Kullanıcının hedefi: {goal}\n"
            f"Günlük kalori hedefi: {round(target)} kcal\n"
            f"Bugün toplam: {round(total_cal)} kcal\n\n"
            f"Bugün yedikleri:\n{meals_text}\n"
            f"Bu günü değerlendir:\n"
            f"- Kalori hedefine ulaştı mı?\n"
            f"- Makro dağılımı dengeli mi?\n"
            f"- Biyoyararlanım açısından nasıl?\n"
            f"- Gluten içeriği yüksek mi?\n"
            f"- Değiştirilmesi gereken bir şey var mı?\n"
            f"Kısa ve spesifik ol, 4-5 cümle yeterli."
        )
        system_prompt = "Sen bir beslenme koçusun. Kısa, spesifik, Türkçe konuş."

    try:
        review = _openai_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            max_tokens=400,
            temperature=0.7,
        )
    except Exception:
        current_app.logger.exception("Öğün değerlendirmesi üretilemedi")
        review = t("route.eval_failed")

    return jsonify({"review": review, "total_calories": round(total_cal), "target": round(target)})
