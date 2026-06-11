"""Deterministic nutrition ingestion, sanitization & macro-scoring pipeline.

Bu modul, FatSecret API'sinden gelen ham besin verisini *deterministik* (LLM'siz)
kurallarla temizler, 100g bazina normalize edilmis makrolari kullanicinin gunluk
KALAN makro hedefleriyle karsilastirip 0-100 arasi bir uyum skoru uretir ve sunum
katmaninin (LLM) ASLA degistiremeyecegi temiz bir JSON sozlesmesi dondurur.

Tasarim ilkeleri (todos.txt):
1. Tum matematik/filtreleme burada, saf fonksiyonlarda yapilir. LLM kullanilmaz.
2. LLM yalnizca bu modulun urettigi sabit sayilari *sunar*.

Saf modul: Flask / SQLAlchemy / OpenAI importu YOKTUR. Bu sayede DB veya ag
baglantisi olmadan birim testleriyle dogrulanabilir. Makro sozlugu sozlesmesi
projenin geri kalaniyla ayni: ``{"calories", "protein", "carbs", "fat"}``
(kaloriler kcal, makrolar gram).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# MODULE 1 — Sabitler & saglik/biyoloji kisitlari
# ---------------------------------------------------------------------------

# Termodinamik tavan: 100g saf yag ~900 kcal. Hicbir gercek besin 100g basina
# 900 kcal'i asamaz (orn. "bir bardak cay = 3000 kcal" troll girdisi elenir).
MAX_KCAL_PER_100G = 900.0

# Tek bir insan porsiyonu (bir tabak/kase) bu mutlak sinirlari asamaz. Porsiyon
# agirligi bilinmese bile gecerli: LLM/parse hatalarini (orn. tek porsiyona 4696
# kcal veya 440 g yag) agirliktan BAGIMSIZ olarak eler. Genis tutuldu ki mesru
# buyuk karisik tabaklar (~1500-2000 kcal) gecsin, sadece imkansizlar elensin.
MAX_SERVING_KCAL = 3000.0
MAX_SERVING_MACRO_G = 300.0

# Protein+Karb+Yag gram toplami porsiyon agirligini bu kadar gram asabilir
# (kayan nokta yuvarlama paylari icin kucuk tolerans).
MACRO_WEIGHT_TOLERANCE_G = 1.0

# Beyan edilen kalori ile Atwater'dan hesaplanan kalori (4P + 4C + 9F) arasindaki
# goreli sapma bu esigi asarsa girdi "tutarsiz" olarak ISARETLENIR (silinmez).
ATWATER_TOLERANCE = 0.30

# Enerji korunumu (kati): makrolarin uretebilecegi enerji, beyan edilen kalorinin
# bu kesrinin ALTINA duserse girdi fiziksel olarak imkansizdir (kalori yoktan var
# olamaz) -> ELE. Asimetriktir: yalnizca "kalori var ama makro yok" yonunu siler;
# ters yon (etiket kaloriyi az gosterir) yumusak ISARET olarak kalir (bkz.
# ATWATER_TOLERANCE). Not: bu kontrol makro-disi enerji iceren alkollu icecekleri
# (etanol 7 kcal/g, makrolara sayilmaz) de eler; fitness gunlugu icin kabul edilir.
ATWATER_HARD_TOLERANCE = 0.50

# Skorlamada sifira bolmeyi onleyen taban; ayni zamanda kalan butce ~0 iken
# herhangi bir pozitif makronun butceyi asmasini saglar.
_EPS = 1e-9

# Asiri butce cezasinin devreye girdigi esik: bir makro kalanin %80'ini asinca.
_PENALTY_THRESHOLD = 0.80

# Makro basina ilerlemeli ceza agirliklari (esik ustu her oran puani icin).
_PENALTY_WEIGHT_CAL = 150.0
_PENALTY_WEIGHT_FAT = 150.0
_PENALTY_WEIGHT_CARB = 100.0

# Protein bonusu tavani ve olcegi.
_PROTEIN_BONUS_MAX = 15.0
_PROTEIN_BONUS_SCALE = 30.0

# Makro denge cezasi: hedef protein kalori payi SABIT degil, kullanicinin O ANKI
# KALAN ihtiyacindan turetilir (kalan proteinin kalan makro-kalorisine orani).
# Boylece ceza "besinin makro orani ile kullanicinin kalan ihtiyaci arasindaki
# orantili sapmadir" (todos.txt §1). Besin bu dinamik hedefin altinda kaldikca
# PURUZSUZ (ikili degil) ceza alir; butceye sigan ama protein-fakiri besinler 100
# yerine kademeli ~55-80 puana oturur (binary collapse fix). Tavan, neredeyse tum
# butcesi protein olan kullanicilarda asiri cezayi sinirlar (skor tabani ~60).
_PROTEIN_QUALITY_CAP = 0.40
_BALANCE_PENALTY_WEIGHT = 100.0

# Bir besinin "protein orani dusuk" olarak isaretlenecegi esik (kalori payi).
# high_protein esiginin (0.20) altinda tutulur ki iki isaret cakismasin.
_LOW_PROTEIN_FLAG_SHARE = 0.15

# Deterministik porsiyon -> gram/ml ortalama agirlik matrisi. SADECE FatSecret
# metric_serving_amount eksik/0 oldugunda yedek olarak kullanilir; LLM tahmini
# yerine gecer. Degerler ortalama yaklasimlardir. Daha spesifik (cok kelimeli)
# anahtarlar genel olanlardan ONCE gelmeli (alt-dize eslesmesi sirayla yapilir).
_PORTION_WEIGHTS = [
    ("su bardağı", 200.0),
    ("çay bardağı", 100.0),
    ("yemek kaşığı", 15.0),
    ("tatlı kaşığı", 7.0),
    ("çay kaşığı", 5.0),
    ("tablespoon", 15.0),
    ("teaspoon", 5.0),
    ("simit", 100.0),
    ("dilim", 30.0),
    ("slice", 30.0),
    ("bardak", 200.0),
    ("glass", 200.0),
    ("kâse", 250.0),
    ("kase", 250.0),
    ("bowl", 250.0),
    ("cup", 200.0),
    ("çay", 200.0),
    ("tea", 200.0),
    ("medium", 120.0),
    ("orta", 120.0),
    ("adet", 100.0),
    ("piece", 100.0),
]


def _num(value, default=0.0):
    """Guvenli float donusumu: None / hatali deger -> default."""
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def estimate_serving_grams(description):
    """Porsiyon aciklamasini deterministik agirlik matrisinden gram/ml'ye cevir.

    SADECE FatSecret metric_serving_amount eksik/0 oldugunda yedek olarak
    kullanilmali. Bilinmeyen aciklama icin ``None`` doner (cagiran taraf karar verir).
    LLM KULLANMAZ.
    """
    if not description:
        return None
    d = str(description).lower()
    for key, grams in _PORTION_WEIGHTS:
        if key in d:
            return float(grams)
    return None


def parse_fatsecret_serving(raw):
    """Ham FatSecret ``serving`` JSON nesnesini standart makro sozlesmemize esle.

    FatSecret alanlari -> ic sema:
      ``calories`` -> calories, ``protein`` -> protein, ``carbohydrate`` -> carbs,
      ``fat`` -> fat (DIKKAT: FatSecret 'carbohydrate' kullanir; 'carbs' degil).
      Ayrica serving_id / serving_description / metric_serving_amount(+unit) tasinir.

    ``metric_serving_amount`` eksik/0 ise ``estimate_serving_grams`` ile porsiyon
    matrisinden deterministik (LLM'siz) tahmin edilir. Tum sayisal alanlar ``_num``
    ile guvenle cevrilir (FatSecret degerleri string gelebilir). Anahtar eslemesini
    TEK noktada toplar ki protein/karb/yag kaymasi tekrar olusamasin (todos.txt §2).
    """
    raw = raw or {}
    amount = _num(raw.get("metric_serving_amount"))
    if amount == 0:
        est = estimate_serving_grams(raw.get("serving_description", ""))
        if est:
            amount = est
    return {
        "serving_id": str(raw.get("serving_id", "")),
        "serving_description": raw.get("serving_description", ""),
        "metric_serving_amount": amount,
        "metric_serving_unit": raw.get("metric_serving_unit", "g"),
        "calories": _num(raw.get("calories")),
        "protein": _num(raw.get("protein")),
        "carbs": _num(raw.get("carbohydrate")),
        "fat": _num(raw.get("fat")),
    }


def check_serving(serving):
    """Tek bir porsiyon sozlugunu fizik/biyoloji kisitlarina gore denetle.

    Beklenen anahtarlar: ``metric_serving_amount`` (gram/ml), ``calories``,
    ``protein``, ``carbs``, ``fat``.

    Donus: ``(is_valid, flags, reasons)``
      - ``is_valid``: kati saglik kontrollerinden gecti mi (False -> at).
      - ``flags``: bilgilendirici isaretler (orn. ``"macros_inconsistent"``).
      - ``reasons``: gecersizlik nedenleri (orn. ``"caloric_density_exceeds_900"``).

    Kati kontroller (ihlal -> gecersiz):
      * Makro-agirlik: protein + karb + yag (g) <= porsiyon agirligi (+ tolerans).
      * Kalorik yogunluk: 100g basina kalori <= 900 kcal (termodinamik tavan).
      * Mutlak porsiyon tavanlari: kalori <= MAX_SERVING_KCAL ve her makro <=
        MAX_SERVING_MACRO_G (agirlik bilinmese de imkansiz porsiyonlari eler).
      * Enerji korunumu: makrolarin Atwater enerjisi beyan kalorinin cok altinda
        olamaz (ATWATER_HARD_TOLERANCE) -> "kalori var ama makro yok" parse hatasi.
    Yumusak kontrol (ihlal -> sadece isaret):
      * Atwater tutarliligi: beyan kalori ile 4P+4C+9F arasindaki (ters yon) sapma.

    Not: "yesil sebzede >20g protein" gibi kategoriye ozgu aykiri-deger kurallari
    bir besin taksonomisi gerektirir (uygulamada yok); bunun yerine kategoriden
    bagimsiz Atwater tutarlilik kontrolu genel bir aykiri-deger tespiti saglar.
    """
    flags = []
    reasons = []
    is_valid = True

    amount = _num(serving.get("metric_serving_amount"))
    cal = _num(serving.get("calories"))
    protein = _num(serving.get("protein"))
    carbs = _num(serving.get("carbs"))
    fat = _num(serving.get("fat"))

    if amount > 0:
        # Makro-agirlik kontrolu: makro gram toplami porsiyon agirligini asamaz.
        if (protein + carbs + fat) > amount + MACRO_WEIGHT_TOLERANCE_G:
            is_valid = False
            reasons.append("macro_weight_exceeds_serving")

        # Kalorik yogunluk kontrolu: 100g basina kalori 900'u asamaz.
        density_per_100g = cal / amount * 100.0
        if density_per_100g > MAX_KCAL_PER_100G + _EPS:
            is_valid = False
            reasons.append("caloric_density_exceeds_900")

    # Tek porsiyon mutlak tavanlari (agirliktan bagimsiz): bir tabak yemek bu
    # sinirlari asamaz. Porsiyon agirligi bilinmese bile imkansiz LLM/parse
    # ciktilarini eler (orn. "Pesto Soslu Makarna" 4696 kcal / 440 g yag).
    if cal > MAX_SERVING_KCAL:
        is_valid = False
        reasons.append("calories_exceed_serving_max")
    if max(protein, carbs, fat) > MAX_SERVING_MACRO_G:
        is_valid = False
        reasons.append("macro_exceeds_serving_max")

    # Atwater / enerji korunumu.
    expected_cal = 4.0 * protein + 4.0 * carbs + 9.0 * fat
    if cal > 0:
        if expected_cal < cal * (1.0 - ATWATER_HARD_TOLERANCE):
            # Kati: beyan edilen kaloriler makrolarin uretebileceginin cok ustunde
            # -> makrolar eksik/yanlis (orn. et ama 0 g protein). Parse/eslesme
            # hatasi kabul edilir ve girdi ELENIR.
            is_valid = False
            reasons.append("calories_exceed_macro_energy")
        elif expected_cal > 0 and abs(cal - expected_cal) / cal > ATWATER_TOLERANCE:
            # Yumusak: beyan ile Atwater farkli ama ret bandinin altinda
            # (orn. etiket kaloriyi az gosteriyor) -> sadece ISARETLE, silme.
            flags.append("macros_inconsistent")

    return is_valid, flags, reasons


# Saf yag/sivi-yag bilesenini ayirt etmek icin: kalorinin bu kesrinden fazlasi
# yagdan geliyorsa ve protein+karb yok denecek kadar azsa, girdi bir YEMEK degil
# bir BILESEN (zeytinyagi, tereyagi, ayciçek yagi...) profilindedir.
_PURE_FAT_CAL_SHARE = 0.95
# protein/karb "yok denecek kadar az" esigi (gram). Saf yaglarda 0; zeytin TANESI
# gibi gercek yiyeceklerde protein VEYA karb bunun ustundedir (~0.8g / ~6g).
_TRACE_MACRO_G = 0.5


def is_pure_fat_ingredient(macros):
    """Makro profili saf yag/sivi-yag BILESENI mi? (zeytinyagi, tereyagi, vb.)

    Bunlar menude TEK BASINA siparis edilen bir yemek DEGIL, bir bilesendir.
    FatSecret 'zeytin/olive' aramasina cogu zaman 'Olive Oil' (100g = 900 kcal,
    saf yag) dondurur; 'Zeytin Tabagi' (bir tabak zeytin) buna eslesince porsiyon
    agirligiyla olceklenip 1300+ kcal / 150 g yag gibi imkansiz bir 'tabak' uretir.

    Profil: protein ~0 VE karb ~0 VE kalorinin >= %95'i yagdan. Gercek yemeklerde
    -zeytin TANESI dahil (~115 kcal, 6 g karb, 0.8 g protein)- protein VEYA karb
    iz miktarin uzerindedir; bu yuzden kural yalnizca saf yaglari yakalar, gercek
    yemekleri (hatta zeytinin kendisini) elemez.

    NOT: Bu yalnizca MENU TARAMA hattinda kullanilmali. Kullanici kocta acikca
    'zeytinyagi' loglamak isteyebilir; orada bu eleme uygulanmaz."""
    cal = _num(macros.get("calories"))
    fat = _num(macros.get("fat"))
    protein = _num(macros.get("protein"))
    carbs = _num(macros.get("carbs"))
    if cal <= 0 or fat <= 0:
        return False
    if protein >= _TRACE_MACRO_G or carbs >= _TRACE_MACRO_G:
        return False
    return (9.0 * fat) >= _PURE_FAT_CAL_SHARE * cal


# ---------------------------------------------------------------------------
# Porsiyon makullugu (yemek-turune gore alt/ust kalori bandi) — bkz.
# docs/menu-porsiyon-eslesme-hatasi.md. Kimlik kapilari DOGRU yemegi secse de
# FatSecret per-serving kaydi kucuk bir ABD referans miktari olabilir (tek
# kofte, 1/2 cup) → butun tabak 2-3x eksik sayilir. Bu kurallar MIKTARI denetler.
# dish_type anahtarlari ai_nutrition._DISH_TYPE_KEYWORDS siniflaridir; cagiran
# taraf turu cozer (bu modul saf kalir, taksonomi importu yapmaz).

# Yemek-turune gore makul tek-porsiyon kalori bandi (kcal). Pizza belgede
# ornek listede yok ama taksonomide var; butun restoran pizzasi ~800-1200 kcal.
PORTION_KCAL_BANDS = {
    "burger": (350.0, 800.0),
    "pasta": (350.0, 700.0),
    "salad": (150.0, 600.0),
    "soup": (150.0, 400.0),
    "pizza": (400.0, 1300.0),
}

# Tur-bazli varsayilan servis agirligi (g) — duz 150 g yedegin yerine. Degerler
# _estimate_serving_weights_llm prompt kurallariyla hizali (makarna 300-400,
# burger 250-350, salata 250-350, corba 250-300) ve LLM kelepcesi (50-600) icinde.
DISH_SERVING_DEFAULT_G = {
    "burger": 300.0,
    "pasta": 350.0,
    "salad": 300.0,
    "soup": 275.0,
    "pizza": 400.0,
}

# Per-serving kaydinin metrik agirligi turun asgarisinden kucukse bu "tam tabak"
# degil bir referans miktaridir (tek kofte, 1 cup) → 100g-esdegeri muamelesi
# yapilir. Soup=150: "cup"→200g matris degeriyle sinir cakismasini onler
# (1 cup corba mesru bir porsiyon olabilir; gercekten kucukler bandi zaten asar).
DISH_SERVING_MIN_G = {
    "burger": 200.0,
    "pasta": 200.0,
    "salad": 150.0,
    "soup": 150.0,
    "pizza": 200.0,
}


def check_portion_band(calories, dish_type):
    """Toplam kaloriyi yemek-turunun makul porsiyon bandiyla karsilastir.

    Donus: ``"low"`` / ``"high"`` / ``"ok"`` — ya da band uygulanamiyorsa
    ``None`` (tur bilinmiyor, bandi yok veya kalori <= 0). ``None`` "gecti"
    DEGIL "karar yok" demektir; cagiran mevcut davranisini surdurmelidir."""
    band = PORTION_KCAL_BANDS.get(dish_type) if dish_type else None
    cal = _num(calories)
    if not band or cal <= 0:
        return None
    low, high = band
    if cal < low:
        return "low"
    if cal > high:
        return "high"
    return "ok"


def gate_per_serving(dish_type, macros, serving_grams=None):
    """FatSecret per-serving kaydina TAM PORSIYON olarak guvenilir mi karari.

    Donus: ``(status, baseline_100g)``
      * ``"accept"`` → kayit bant icinde (veya tur bilinmiyor) → mevcut davranis:
        degeri oldugu gibi tam tabak say. ``baseline_100g`` None.
      * ``"skip"``   → bant USTU (aile/toplu kayit). 100g varsaymak degeri daha da
        patlatacagindan aday tamamen atlanir; sonraki aday veya LLM kazanir.
      * ``"convert"``→ bant ALTI ya da metrik agirlik turun asgarisinden kucuk →
        kayit tam tabak DEGIL. ``baseline_100g`` per-100g esdegeri olarak doner;
        cagiran bunu per-100g yoluna (servis agirligiyla olcekleme) verir.

    Donusum: ``serving_grams`` biliniyorsa makrolar 100/serving_grams ile oranlanir
    ve sonuc ``check_serving(amount=100)`` ile dogrulanir (kaba agirlik matrisi
    "dilim"→30g gibi degerlerle 900 kcal/100g tavanini asabilir); gecersizse veya
    agirlik bilinmiyorsa kucuk referans porsiyonu ~100g esdegeri kabul edilir
    (FatSecret referans miktarlari 100-150g civarinda kumelenir → hafif yukari
    yanlilik, bant icine oturur). LLM KULLANMAZ."""
    band = check_portion_band(macros.get("calories"), dish_type)
    if band is None:
        return "accept", None
    if band == "high":
        return "skip", None
    grams = _num(serving_grams)
    too_small = grams > 0 and grams < DISH_SERVING_MIN_G.get(dish_type, 0.0)
    if band == "low" or too_small:
        if grams > 0:
            scale = 100.0 / grams
            candidate = {
                "calories": round(_num(macros.get("calories")) * scale, 1),
                "protein": round(_num(macros.get("protein")) * scale, 1),
                "carbs": round(_num(macros.get("carbs")) * scale, 1),
                "fat": round(_num(macros.get("fat")) * scale, 1),
            }
            valid, _flags, _reasons = check_serving(
                {"metric_serving_amount": 100.0, **candidate})
            if valid:
                return "convert", candidate
        return "convert", {
            "calories": _num(macros.get("calories")),
            "protein": _num(macros.get("protein")),
            "carbs": _num(macros.get("carbs")),
            "fat": _num(macros.get("fat")),
        }
    return "accept", None


def sanitize_servings(servings, food_type=None):
    """Bir porsiyon listesini denetle: gecersizleri at, isaretleri ekle, dogrulanmis
    (Generic) girdileri one al (kararli siralama).

    ``food_type`` listedeki tum girdiler icin ortak bir tur ipucu olarak gecilebilir;
    her porsiyon sozlugunde ayrica ``food_type`` anahtari varsa o onceliklenir.
    Veritabanina/skora gecmeden ONCE cagrilmak uzere tasarlanmistir.
    """
    sanitized = []
    for serving in servings or []:
        is_valid, flags, _reasons = check_serving(serving)
        if not is_valid:
            continue
        item = dict(serving)
        if flags:
            item["flags"] = list(item.get("flags", [])) + flags
        sanitized.append(item)

    # Dogrulanmis/Generic girdileri one al (kararli siralama digerlerini korur).
    def _priority(item):
        ft = item.get("food_type", food_type)
        return 0 if (ft is None or str(ft).strip().lower() == "generic") else 1

    sanitized.sort(key=_priority)
    return sanitized


# ---------------------------------------------------------------------------
# MODULE 2 — Deterministik uyum skoru (0-100)
# ---------------------------------------------------------------------------

def _safe_ratio(food_value, remaining_value):
    """food/remaining orani; kalan <= 0 iken pozitif makro -> sonsuz (butce asimi)."""
    if remaining_value <= 0:
        return float("inf") if food_value > 0 else 0.0
    return food_value / remaining_value


def score_compatibility(food_macros, remaining):
    """Bir besinin kullanicinin KALAN gunluk makrolariyla uyumunu 0-100 puanla.

    Girdi:
      - ``food_macros``: ``{"calories", "protein", "carbs", "fat"}`` (porsiyona olceklenmis).
      - ``remaining``: ayni anahtarlarla kullanicinin gun icin KALAN butcesi.

    Mantik (todos.txt Module 2 ceza matrisi):
      * Taban skor 100.
      * Kalori/Yag/Karb asiri-butce cezasi: bir makro kalanin %80'ini astikca
        ilerlemeli ceza uygulanir.
      * Makro denge cezasi: besin butceye SIGSA bile, besinin protein kalori payi
        kullanicinin KALAN ihtiyacindaki protein payinin (dinamik hedef) altinda
        kaldikca orantili-puruzsuz ceza alir (orn. saf karb, protein gerekirken)
        -> ~55-80 puan (binary collapse fix); ceza kalan profile gore degisir.
      * Kati kural: besin kalanin %100'unu (kalori VEYA yag) asarsa skor ANINDA 0
        olur ve ``"Exceeds daily budget limit"`` uyarisi tetiklenir.
      * Protein bonusu: kalan protein hedefi varsa ve besin kalori butcesine
        sigiyorsa proteinli besinler odullendirilir.
      * ``Score = max(0, min(100, round(100 - (P_cal+P_fat+P_carb+P_balance) + Bonus_Protein)))``.

    Donus: ``{"score": int(0..100), "flags": [...], "warnings": [...]}``.
    """
    cal = _num(food_macros.get("calories"))
    protein = _num(food_macros.get("protein"))
    carbs = _num(food_macros.get("carbs"))
    fat = _num(food_macros.get("fat"))

    # Negatif kalanlari 0'a kenetle (asilmis butce = butce yok).
    rem_cal = max(_num(remaining.get("calories")), 0.0)
    rem_pro = max(_num(remaining.get("protein")), 0.0)
    rem_carb = max(_num(remaining.get("carbs")), 0.0)
    rem_fat = max(_num(remaining.get("fat")), 0.0)

    cal_ratio = _safe_ratio(cal, rem_cal)
    fat_ratio = _safe_ratio(fat, rem_fat)
    carb_ratio = _safe_ratio(carbs, rem_carb)
    pro_ratio = _safe_ratio(protein, rem_pro)

    flags = []
    warnings = []

    # Kati kural: kalori veya yag butcesi %100'u astiysa skor 0.
    if cal_ratio > 1.0 or fat_ratio > 1.0:
        return {
            "score": 0,
            "flags": [],
            "warnings": ["Exceeds daily budget limit"],
        }

    # Ilerlemeli asiri-butce cezalari (yalnizca esik ustunde).
    penalty_cal = max(0.0, cal_ratio - _PENALTY_THRESHOLD) * _PENALTY_WEIGHT_CAL
    penalty_fat = max(0.0, fat_ratio - _PENALTY_THRESHOLD) * _PENALTY_WEIGHT_FAT
    penalty_carb = max(0.0, carb_ratio - _PENALTY_THRESHOLD) * _PENALTY_WEIGHT_CARB

    # Makro denge cezasi (granulerlik): besinin kalorisinin ne kadari proteinden
    # geliyor (protein_cal_share) vs kullanicinin KALAN ihtiyacinda protein kalori
    # payi ne kadar (target_share)? Besin bu DINAMIK hedefin altina dustukce
    # orantili ve PURUZSUZ ceza uygula -> "makro orani ile kalan ihtiyac arasindaki
    # sapma" (todos.txt §1). Boylece ayni besin, kullanicinin kalan profiline gore
    # farkli (granuler) puan alir; saf karb/yag besinler 100 yerine kademeli iner.
    protein_cal_share = (4.0 * protein / cal) if cal > 0 else 0.0
    rem_macro_cal = 4.0 * rem_pro + 4.0 * rem_carb + 9.0 * rem_fat
    penalty_balance = 0.0
    if rem_pro > 0 and rem_macro_cal > 0:
        target_share = min(_PROTEIN_QUALITY_CAP, (4.0 * rem_pro) / rem_macro_cal)
        deficit = max(0.0, target_share - protein_cal_share)
        penalty_balance = deficit * _BALANCE_PENALTY_WEIGHT

    # Protein bonusu: kalan protein hedefi varken proteinli besinleri odullendir.
    bonus_protein = 0.0
    if rem_pro > 0 and protein > 0:
        bonus_protein = min(_PROTEIN_BONUS_MAX, pro_ratio * _PROTEIN_BONUS_SCALE)

    raw = 100.0 - (penalty_cal + penalty_fat + penalty_carb + penalty_balance) + bonus_protein
    score = int(round(max(0.0, min(100.0, raw))))

    # Deterministik isaretler.
    if rem_fat > 0 and fat_ratio < 0.30:
        flags.append("low_fat")
    if protein_cal_share >= 0.20:
        flags.append("high_protein")
    elif rem_pro > 0 and protein_cal_share < _LOW_PROTEIN_FLAG_SHARE:
        flags.append("low_protein_food")
    if cal_ratio <= _PENALTY_THRESHOLD:
        flags.append("fits_calorie_budget")

    # Deterministik uyarilar (kati 0 disindaki esik asimlari).
    if _PENALTY_THRESHOLD < cal_ratio <= 1.0:
        warnings.append("Approaching calorie budget")
    if _PENALTY_THRESHOLD < fat_ratio <= 1.0:
        warnings.append("Approaching fat budget")
    if carb_ratio > 1.0:
        warnings.append("High carbohydrate load")

    return {"score": score, "flags": flags, "warnings": warnings}


# ---------------------------------------------------------------------------
# MODULE 3 — Cikis mimarisi (LLM'in degistiremeyecegi JSON sozlesmesi)
# ---------------------------------------------------------------------------

def build_evaluation(food_id, name, standardized_serving, macros, remaining,
                     extra_flags=None):
    """Sunum katmani (LLM) icin temiz, yapilandirilmis degerlendirme JSON'u uret.

    Ciktidaki tum sayilar kesindir; LLM bunlari ASLA degistirmemelidir.

    Ornek sozlesme::

        {
          "food_id": "12345",
          "name": "Simit",
          "standardized_serving": "1 Piece (100g)",
          "macros": {"kcal": 420, "protein": 16, "carbs": 66, "fat": 9},
          "compatibility_score": 100,
          "flags": ["low_fat", "fits_calorie_budget"],
          "warnings": []
        }
    """
    scored = score_compatibility(macros, remaining)

    # Sanitize isaretleri + skor isaretleri (sira korunur, tekrar elenir).
    merged_flags = list(dict.fromkeys(list(extra_flags or []) + scored["flags"]))

    return {
        "food_id": "" if food_id is None else str(food_id),
        "name": name,
        "standardized_serving": standardized_serving,
        "macros": {
            "kcal": int(round(_num(macros.get("calories")))),
            "protein": int(round(_num(macros.get("protein")))),
            "carbs": int(round(_num(macros.get("carbs")))),
            "fat": int(round(_num(macros.get("fat")))),
        },
        "compatibility_score": scored["score"],
        "flags": merged_flags,
        "warnings": scored["warnings"],
    }
