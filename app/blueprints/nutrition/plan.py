"""Beslenme planı: AI üretim + kaydet/aktif plan (freemium kotalı).

app/blueprints/nutrition.py (god-module) eş-anlamlı parçalara bölündü; rotalar
ve davranış AYNI (aynı `nutrition` blueprint'i, aynı endpoint adları). Ortak
`bp` paketten gelir.
"""
import json
from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.blueprints.nutrition import bp
from app.config import AI_RATELIMIT, BEDROCK_RATELIMIT
from app.extensions import _user_or_ip_key, db, limiter
from app.i18n import current_locale, t
from app.models import NutritionPlan, UserSession
from app.services.ai import _heavy_chat
from app.services.premium import premium_ai_plan_gate


@bp.route("/nutrition-plan/save", methods=["POST"])
@login_required
def save_nutrition_plan():
    data = request.get_json(silent=True) or {}
    plan = data.get("plan")
    score = data.get("score")

    if not plan:
        return jsonify({"error": t("route.plan_data_missing")}), 400

    # Eski planı sil, yenisini kaydet
    NutritionPlan.query.filter_by(user_id=current_user.id).delete()

    new_plan = NutritionPlan(
        user_id   = current_user.id,
        plan_data = json.dumps(plan, ensure_ascii=False),
        score     = score
    )
    db.session.add(new_plan)
    db.session.commit()

    return jsonify({"message": t("route.plan_saved")})


@bp.route("/nutrition-plan/active")
@login_required
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
        "created_at": plan.created_at.strftime("%d.%m.%Y")
    })


@bp.route("/nutrition")
@login_required
def nutrition():
    return render_template("nutrition.html", username=current_user.username, profile_picture=current_user.avatar_src)


@bp.route("/nutrition-plan", methods=["POST"])
@login_required
@limiter.limit(AI_RATELIMIT, key_func=_user_or_ip_key)
@limiter.limit(BEDROCK_RATELIMIT, key_func=_user_or_ip_key)  # Sonnet üretimi: daha sıkı tavan
@premium_ai_plan_gate("nutrition")  # non-premium: haftada 1 üretim
def nutrition_plan_generate():
    data = request.get_json(silent=True) or {}
    FOOD_DATABASE = {
    "protein": {
        "hayvansal": [
            {"isim": "Tavuk Göğsü", "kalori": 165, "protein": 31, "karb": 0, "yag": 3.6, "mikro_skor": 8, "biyoyararlanim": 9, "gluten": 10},
            {"isim": "Yumurta", "kalori": 155, "protein": 13, "karb": 1.1, "yag": 11, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
            {"isim": "Ton Balığı", "kalori": 132, "protein": 28, "karb": 0, "yag": 1.3, "mikro_skor": 8, "biyoyararlanim": 9, "gluten": 10},
            {"isim": "Kırmızı Et", "kalori": 250, "protein": 26, "karb": 0, "yag": 17, "mikro_skor": 8, "biyoyararlanim": 8, "gluten": 10},
            {"isim": "Yoğurt", "kalori": 61, "protein": 10, "karb": 3.6, "yag": 0.4, "mikro_skor": 7, "biyoyararlanim": 8, "gluten": 10},
            {"isim": "Somon", "kalori": 208, "protein": 20, "karb": 0, "yag": 13, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
        ],
        "bitkisel": [
            {"isim": "Mercimek", "kalori": 116, "protein": 9, "karb": 20, "yag": 0.4, "mikro_skor": 8, "biyoyararlanim": 6, "gluten": 10},
            {"isim": "Nohut", "kalori": 164, "protein": 9, "karb": 27, "yag": 2.6, "mikro_skor": 7, "biyoyararlanim": 6, "gluten": 10},
            {"isim": "Tofu", "kalori": 76, "protein": 8, "karb": 1.9, "yag": 4.2, "mikro_skor": 7, "biyoyararlanim": 7, "gluten": 10},
            {"isim": "Kinoa", "kalori": 120, "protein": 4.4, "karb": 21, "yag": 1.9, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
            {"isim": "Edamame", "kalori": 121, "protein": 11, "karb": 8.9, "yag": 5.2, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
        ]
    },
    "karbonhidrat": [
        {"isim": "Yulaf Ezmesi", "kalori": 389, "protein": 17, "karb": 66, "yag": 7, "mikro_skor": 9, "biyoyararlanim": 8, "gluten": 6},
        {"isim": "Pirinç", "kalori": 130, "protein": 2.7, "karb": 28, "yag": 0.3, "mikro_skor": 6, "biyoyararlanim": 8, "gluten": 10},
        {"isim": "Bulgur", "kalori": 83, "protein": 3, "karb": 18, "yag": 0.2, "mikro_skor": 7, "biyoyararlanim": 7, "gluten": 3},
        {"isim": "Tatlı Patates", "kalori": 86, "protein": 1.6, "karb": 20, "yag": 0.1, "mikro_skor": 9, "biyoyararlanim": 8, "gluten": 10},
        {"isim": "Tam Buğday Ekmeği", "kalori": 247, "protein": 13, "karb": 41, "yag": 3.4, "mikro_skor": 6, "biyoyararlanim": 6, "gluten": 2},
        {"isim": "Muz", "kalori": 89, "protein": 1.1, "karb": 23, "yag": 0.3, "mikro_skor": 7, "biyoyararlanim": 9, "gluten": 10},
        {"isim": "Elma", "kalori": 52, "protein": 0.3, "karb": 14, "yag": 0.2, "mikro_skor": 7, "biyoyararlanim": 8, "gluten": 10},
    ],
    "yag": [
        {"isim": "Zeytinyağı", "kalori": 884, "protein": 0, "karb": 0, "yag": 100, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
        {"isim": "Avokado", "kalori": 160, "protein": 2, "karb": 9, "yag": 15, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
        {"isim": "Badem", "kalori": 579, "protein": 21, "karb": 22, "yag": 50, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
        {"isim": "Ceviz", "kalori": 654, "protein": 15, "karb": 14, "yag": 65, "mikro_skor": 9, "biyoyararlanim": 7, "gluten": 10},
        {"isim": "Fındık", "kalori": 628, "protein": 15, "karb": 17, "yag": 61, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
    ]
}
    # Kullanıcının son oturumundan kalori hedefini al
    last = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc())\
        .first()

    if not last:
        return jsonify({"error": t("plan.no_session")}), 400

    target_calories = last.target_calories
    goal            = last.goal

    # Seçilen gıdalar
    selected_proteins = data.get("proteins", [])
    selected_carbs    = data.get("carbs", [])
    selected_fats     = data.get("fats", [])
    custom_foods      = data.get("custom_foods", [])

    if not selected_proteins or not selected_carbs or not selected_fats:
        return jsonify({"error": t("nutrition.plan.pick_one")}), 400

    # Seçilen gıdaların ortalama skorunu hesapla
    def avg_score(foods):
        if not foods:
            return 0
        all_foods = []
        for category in FOOD_DATABASE.values():
            if isinstance(category, dict):
                for items in category.values():
                    all_foods.extend(items)
            else:
                all_foods.extend(category)
        selected = [f for f in all_foods if f["isim"] in foods]
        if not selected:
            return 0
        mikro  = sum(f["mikro_skor"] for f in selected) / len(selected)
        biyo   = sum(f["biyoyararlanim"] for f in selected) / len(selected)
        gluten = sum(f["gluten"] for f in selected) / len(selected)
        return round((mikro + biyo + gluten) / 3, 1)

    overall_score = avg_score(selected_proteins + selected_carbs + selected_fats)

    if overall_score >= 8:
        score_label = "İyi"
        score_color = "green"
    elif overall_score >= 6:
        score_label = "Orta"
        score_color = "orange"
    else:
        score_label = "Kötü"
        score_color = "red"

    # AI'a gönder — dile göre prompt. JSON ANAHTARLARI (planlar/kahvalti/ogle/aksam/
    # ara_ogun/yemekler/isim/toplam_*) HER DİLDE TÜRKÇE KALIR: frontend (nutrition.js)
    # bu anahtarları parse eder. Yalnızca DEĞERLER (yemek adları) dile göre yazılır.
    lang = current_locale()

    # Paylaşılan JSON şablon örneği — anahtarlar TR (kanonik kontrat).
    json_example = """{
  "planlar": [
    {
      "isim": "Plan A",
      "kahvalti": {"yemekler": ["Yumurta - 3 adet", "Tam buğday ekmeği - 2 dilim"], "kalori": 420, "protein": 28, "karb": 35, "yag": 18},
      "ogle": {"yemekler": ["Tavuk göğsü - 150g", "Pirinç - 100g"], "kalori": 380, "protein": 48, "karb": 28, "yag": 5},
      "aksam": {"yemekler": ["Kırmızı et - 120g", "Tatlı patates - 150g"], "kalori": 450, "protein": 38, "karb": 30, "yag": 20},
      "ara_ogun": {"yemekler": ["Yoğurt - 200g", "Muz - 1 adet"], "kalori": 227, "protein": 22, "karb": 30, "yag": 1},
      "toplam_kalori": 1477,
      "toplam_protein": 136,
      "toplam_karb": 123,
      "toplam_yag": 44
    },
    {
      "isim": "Plan B",
      "kahvalti": {"yemekler": ["yemek - miktar"], "kalori": 400, "protein": 25, "karb": 40, "yag": 15},
      "ogle": {"yemekler": ["yemek - miktar"], "kalori": 450, "protein": 40, "karb": 35, "yag": 10},
      "aksam": {"yemekler": ["yemek - miktar"], "kalori": 500, "protein": 42, "karb": 38, "yag": 18},
      "ara_ogun": {"yemekler": ["yemek - miktar"], "kalori": 200, "protein": 15, "karb": 20, "yag": 6},
      "toplam_kalori": 1550,
      "toplam_protein": 122,
      "toplam_karb": 133,
      "toplam_yag": 49
    },
    {
      "isim": "Plan C",
      "kahvalti": {"yemekler": ["yemek - miktar"], "kalori": 380, "protein": 22, "karb": 42, "yag": 12},
      "ogle": {"yemekler": ["yemek - miktar"], "kalori": 430, "protein": 38, "karb": 40, "yag": 12},
      "aksam": {"yemekler": ["yemek - miktar"], "kalori": 520, "protein": 44, "karb": 35, "yag": 22},
      "ara_ogun": {"yemekler": ["yemek - miktar"], "kalori": 210, "protein": 18, "karb": 22, "yag": 5},
      "toplam_kalori": 1540,
      "toplam_protein": 122,
      "toplam_karb": 139,
      "toplam_yag": 51
    }
  ]
}"""

    if lang == "en":
        custom_text = (f"\nUser's custom foods: {', '.join(custom_foods)}"
                       if custom_foods else "")
        prompt = (
            "You are a nutrition expert. Write ALL meal/food text values in ENGLISH "
            "(translate the Turkish source food names). KEEP the JSON keys EXACTLY as "
            "shown below — they are in Turkish (planlar, kahvalti, ogle, aksam, ara_ogun, "
            "yemekler, isim, kalori, protein, karb, yag, toplam_*); translate ONLY the values.\n\n"
            "User info:\n"
            f"- Daily target calories: {round(target_calories)} kcal\n"
            f"- Goal: {goal}\n\n"
            "User's preferred foods:\n"
            f"- Protein sources: {', '.join(selected_proteins)}\n"
            f"- Carb sources: {', '.join(selected_carbs)}\n"
            f"- Fat sources: {', '.join(selected_fats)}\n"
            f"{custom_text}\n\n"
            "Using ONLY these foods, create 3 DIFFERENT daily meal plans (Plan A, Plan B, Plan C).\n"
            f"Each plan must be around {round(target_calories)} kcal (±100 kcal tolerance).\n"
            "Each plan must include breakfast, lunch, dinner and a snack.\n"
            "Specify the amount in grams or pieces for each item.\n"
            "Calculate and write all calorie and macro values as real numbers.\n\n"
            "Respond ONLY in the JSON format below, write nothing else. Keep the keys exactly "
            "as shown; put the food text in English.\n"
            "Example format (values are examples, compute the real ones):\n"
            + json_example
        )
        system_prompt = ("You are a nutrition expert. Return ONLY valid JSON, nothing else. "
                         "Keep the JSON keys exactly as given (Turkish); translate values to English.")
    else:
        custom_text = (f"\nKullanıcının eklediği özel gıdalar: {', '.join(custom_foods)}"
                       if custom_foods else "")
        prompt = (
            "Sen bir beslenme uzmanısın. Türkçe yaz, İngilizce kelime kullanma.\n\n"
            "Kullanıcı bilgileri:\n"
            f"- Günlük hedef kalori: {round(target_calories)} kcal\n"
            f"- Hedef: {goal}\n\n"
            "Kullanıcının tercih ettiği gıdalar:\n"
            f"- Protein kaynakları: {', '.join(selected_proteins)}\n"
            f"- Karbonhidrat kaynakları: {', '.join(selected_carbs)}\n"
            f"- Yağ kaynakları: {', '.join(selected_fats)}\n"
            f"{custom_text}\n\n"
            "SADECE bu gıdaları kullanarak 3 FARKLI günlük beslenme planı oluştur (Plan A, Plan B, Plan C).\n"
            f"Her plan tam olarak {round(target_calories)} kcal civarında olsun (±100 kcal tolerans).\n"
            "Her plan kahvaltı, öğle, akşam ve ara öğün içersin.\n"
            "Her öğünde gram veya adet olarak miktar belirt.\n"
            "Tüm kalori ve makro değerlerini gerçek sayı olarak hesapla ve yaz.\n\n"
            "Yanıtını SADECE şu JSON formatında ver, başka hiçbir şey yazma.\n"
            "Örnek format (değerler örnek, gerçek değerleri hesapla):\n"
            + json_example
        )
        system_prompt = "Sen bir beslenme uzmanısın. SADECE geçerli JSON döndür, başka hiçbir şey yazma."

    try:
        raw = _heavy_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            max_tokens=2000,
            temperature=0.3,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        plans = json.loads(raw)

        return jsonify({
            "planlar"      : plans["planlar"],
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
