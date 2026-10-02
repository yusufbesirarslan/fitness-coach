"""Canonical Nutrition Plan GENERATION — shared by the web and native transports.

Extracted verbatim (NUTR-PR7) from ``POST /nutrition-plan`` so the browser route
and the native ``POST /api/v1/nutrition/plan/generate`` run ONE prompt
construction, ONE food catalogue, ONE rating rule and ONE parse. The transports
own only their gates (rate limit, premium quota, AI concurrency) and their
response mapping. Generation is a PROPOSAL: nothing here touches the database.

``chat_fn`` is injected by the caller (``app.services.ai._heavy_chat`` in both
transports, resolved at call time so a test can patch the route module's
binding exactly as before).
"""
import json

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


FOOD_NAMES = frozenset(
    item["isim"]
    for category in FOOD_DATABASE.values()
    for items in (category.values() if isinstance(category, dict) else (category,))
    for item in items
)


def food_rating(foods):
    """The generator's 0-10 average rating of the chosen FOODS (not adherence)."""
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


def rating_label(overall_score):
    if overall_score >= 8:
        return "İyi", "green"
    if overall_score >= 6:
        return "Orta", "orange"
    return "Kötü", "red"


def build_prompts(lang, target_calories, goal, selected_proteins, selected_carbs,
                  selected_fats, custom_foods):
    """(prompt, system_prompt) — JSON keys stay Turkish in every locale."""

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

    return prompt, system_prompt


def parse_plan_options(raw):
    """The provider's text → the parsed document. Raises on anything else."""
    raw = raw.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    return json.loads(raw)


def generate_plan_options(chat_fn, lang, target_calories, goal, selected_proteins,
                          selected_carbs, selected_fats, custom_foods):
    """One provider call → the parsed ``planlar`` list. Never persists.

    Raises ``json.JSONDecodeError`` for unparseable output and ``KeyError`` /
    ``TypeError`` when ``planlar`` is missing — callers map both to a typed
    failure; nothing is fabricated.
    """
    prompt, system_prompt = build_prompts(
        lang, target_calories, goal, selected_proteins, selected_carbs,
        selected_fats, custom_foods)
    raw = chat_fn(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        max_tokens=2000,
        temperature=0.3,
        feature="nutrition_plan",
    )
    plans = parse_plan_options(raw)
    return plans["planlar"]
