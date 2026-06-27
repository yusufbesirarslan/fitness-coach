"""MealLog defteri: foto/AI makro tahmini, bugün/geçmiş, değerlendirme.

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
import s3_helper
from flask import Response, current_app, jsonify, request
from flask_login import current_user, login_required

from app.blueprints.nutrition import bp
from app.config import AI_RATELIMIT
from app.extensions import _user_or_ip_key, db, limiter
from app.i18n import current_locale, t
from app.models import MealLog, UserSession
from app.services.ai import PORTION_SANITY_RULE, _openai_chat
from app.services.gamification import complete_quest_for_user
from app.services.validators import _meal_photo_url, _to_float, validate_meal_photo
from app.timeutil import day_key, display_ddmm


@bp.route("/meal-log", methods=["POST"])
@login_required
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
def log_meal():
    data = request.get_json(silent=True) or {}
    ogun     = data.get("ogun", "")
    yemekler = data.get("yemekler", "")

    if not ogun or not yemekler:
        return jsonify({"error": t("route.meal_foods_required")}), 400

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
        current_app.logger.info(f"[MEAL] Normalized: '{yemekler}' → '{normalized_yemekler}'")
    yemekler_for_prompt = normalized_yemekler

    override = data.get("override_macros")
    if override and isinstance(override, dict):
        # Kullanıcı override'ı doğrudan kanonik MealLog'a YAZMADAN ÖNCE fiziksel-
        # sağlık kapısından geçir (menü/koç/diary hatlarıyla aynı tek kaynak) —
        # aksi halde request-kontrollü çöp (örn. 9999 kcal) deftere sızıp günlük
        # toplamları, protein nudge'ını ve haftalık raporları bozardı (C1).
        import nutrition_pipeline as _np
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
        db.session.add(entry)
        db.session.commit()
        resp = {"message": t("route.x_logged", name=ogun), "nutrients": nutrients}
        if meal_photo_key:
            resp["photo_url"] = _meal_photo_url(entry)
        return jsonify(resp)

    prompt = (
        f"Sen bir beslenme uzmanısın. Aşağıdaki yemeklerin GERÇEK toplam besin değerlerini hesapla.\n"
        f"Miktar belirtilmişse kullan, belirtilmemişse standart porsiyon kabul et.\n\n"
        f"Referans değerler:\n"
        f"- Tavuk göğsü (100g): 165 kcal, 31g protein, 0g karb, 3.6g yağ\n"
        f"- Yumurta (1 adet, 60g): 90 kcal, 7g protein, 0.6g karb, 6.3g yağ\n"
        f"- Pirinç pişmiş (100g): 130 kcal, 2.7g protein, 28g karb, 0.3g yağ\n"
        f"- Yulaf ezmesi (100g): 389 kcal, 17g protein, 66g karb, 7g yağ\n"
        f"- Beyaz peynir (100g): 264 kcal, 17g protein, 0.5g karb, 21g yağ\n"
        f"- Tam buğday ekmeği (1 dilim, 30g): 74 kcal, 4g protein, 12g karb, 1g yağ\n"
        f"- Ekmek (1 dilim, 30g): 80 kcal, 2.5g protein, 15g karb, 1g yağ\n"
        f"- Zeytinyağı (1 yemek kaşığı, 14ml): 119 kcal, 0g protein, 0g karb, 14g yağ\n"
        f"- Muz (1 orta, 120g): 105 kcal, 1.3g protein, 27g karb, 0.4g yağ\n"
        f"- Fıstık ezmesi (15g): 94 kcal, 4g protein, 3g karb, 8g yağ\n"
        f"- Makarna pişmiş (100g): 158 kcal, 5.8g protein, 31g karb, 0.9g yağ\n"
        f"- Patates pişmiş (100g): 87 kcal, 1.9g protein, 20g karb, 0.1g yağ\n"
        f"- Kırmızı et (100g): 250 kcal, 26g protein, 0g karb, 17g yağ\n"
        f"- Ton balığı (100g): 132 kcal, 28g protein, 0g karb, 1.3g yağ\n"
        f"- Süt (1 bardak, 240ml): 150 kcal, 8g protein, 12g karb, 8g yağ\n"
        f"- Yoğurt (100g): 61 kcal, 10g protein, 3.6g karb, 0.4g yağ\n"
        f"- Peynir (kaşar, 100g): 350 kcal, 25g protein, 1.5g karb, 27g yağ\n"
        f"- Salatalık (100g): 16 kcal, 0.7g protein, 3.6g karb, 0.1g yağ\n"
        f"- Domates (100g): 18 kcal, 0.9g protein, 3.9g karb, 0.2g yağ\n\n"
        f"Spor takviyeleri ve fitness besinleri:\n"
        f"- Whey protein tozu (1 ölçek, 30g): 120 kcal, 24g protein, 3g karb, 1.5g yağ\n"
        f"- Kazein protein (1 ölçek, 33g): 120 kcal, 24g protein, 3g karb, 1g yağ\n"
        f"- Kreatin monohidrat (1 ölçek, 5g): 0 kcal, 0g protein, 0g karb, 0g yağ\n"
        f"- BCAA (1 ölçek, 7g): 0 kcal, 0g protein, 0g karb, 0g yağ\n"
        f"- Protein bar (1 adet, 60g): 220 kcal, 20g protein, 22g karb, 8g yağ\n"
        f"- Pirinç patlağı (1 adet, 8g): 28 kcal, 0.7g protein, 6g karb, 0.2g yağ\n"
        f"- Fıstık ezmesi (1 yemek kaşığı, 15g): 94 kcal, 4g protein, 3g karb, 8g yağ\n"
        f"- Bal (1 yemek kaşığı, 21g): 64 kcal, 0g protein, 17g karb, 0g yağ\n\n"
        f"Kullanıcının yediği: {yemekler_for_prompt}\n\n"
        f"Her besini ayrı hesapla ve topla. Sonucu SADECE aşağıdaki JSON formatında döndür.\n"
        f"Değerler gerçek sayı olmalı (0 değil), ondalık olabilir:\n"
        f'{{"kalori": 520, "protein": 38, "karb": 45, "yag": 14}}'
    )

    nutrients = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    raw = ""
    parsed_ok = False

    try:
        raw = _openai_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="SADECE JSON döndür. Açıklama yapma, markdown kullanma, sadece düz JSON objesi. Tüm değerler sayı olmalı." + PORTION_SANITY_RULE,
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
        for key in ("kalori", "protein", "karb", "yag"):
            try:
                nutrients[key] = round(float(nutrients.get(key, 0)), 1)
            except (TypeError, ValueError):
                nutrients[key] = 0
        parsed_ok = True
    except Exception as e:
        current_app.logger.info(f"MEAL LOG ERROR: {e}")
        current_app.logger.info(f"RAW: {raw}")

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
    db.session.add(entry)
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
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
@login_required
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
            "photo_url": _meal_photo_url(m)
        })
        totals["kalori"]  += m.kalori or 0
        totals["protein"] += m.protein or 0
        totals["karb"]    += m.karb or 0
        totals["yag"]     += m.yag or 0

    return jsonify({"meals": result, "totals": totals, "tarih": display_ddmm(today)})


@bp.route("/meal-log/history")
@login_required
def meal_history():
    meals = MealLog.query.filter_by(user_id=current_user.id)\
        .order_by(MealLog.created_at.desc()).limit(50).all()

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
@login_required
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
