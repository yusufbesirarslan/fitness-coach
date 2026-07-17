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

import math
import re

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
MAX_MEAL_TOTAL_KCAL = 10000.0
MAX_MEAL_TOTAL_MACRO_G = 2000.0
MAX_MEAL_TOTAL_FAT_G = 1000.0


# Tek bir insan porsiyonundaki YAĞ icin daha siki, ayri bir tavan. Mesru cok yagli
# tabaklar (buyuk antipasti/karisik izgara ~100-120 g yag) gecsin ama olcekleme
# patlamalari elensin: ornegin 'olive' aramasi saf zeytinyagina eslesip porsiyon
# agirligiyla carpilinca uretilen 202 g yag / 1848 kcal'lik "salata" -> imkansiz.
# Atwater tutarli oldugu (yag-kalorisi ~ beyan kalori) icin enerji kontrolune
# takilmiyordu; bu mutlak yag tavani agirliktan bagimsiz olarak yakalar.
MAX_SERVING_FAT_G = 150.0

# Tek bir insan porsiyonundaki KARBONHIDRAT icin ayri tavan (yag tavaniyla ayni
# mantik). Genel makro tavani (300 g) cok gevsek: bir tabak en buyuk pilav/makarna
# bile ~120-150 g karbi gecmez. Bunun ustu (saha vakasi: 'Asya Usulu Acili Tavuk'
# 244 g karb / 1480 kcal) sisirilmis/halusinasyon tahminidir. Atwater tutarli
# oldugu icin enerji kontrolune takilmiyordu; bu mutlak karb tavani yakalar.
MAX_SERVING_CARB_G = 200.0

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

# "high_protein" etiketi icin kalori payinin (>=0.20) YANI SIRA gereken MUTLAK
# protein (gram). Yalniz oran bakmak, 45 kcal'lik 3 g proteinli bir salatayi
# (3 g -> 12 kcal -> kalorinin %27'si) "yuksek protein" gosteriyordu; mutlak esik
# bunu eler — gercekten proteinli bir ogun en az bu kadar gram protein icermeli.
_HIGH_PROTEIN_MIN_G = 15.0

# "low_fat" etiketi artik besinin KENDI yag-enerji payina bakar (yag_kcal/toplam),
# kullanicinin kalan yag butcesine DEGIL. Bu esigin altindaki yag payi "dusuk yag"
# sayilir. Boylece kalori-yogun yagli bir tabak (orn. 20 g yag / 255 kcal = %67),
# kullanicinin kalan butcesi bol olsa bile yanlislikla "dusuk yag" etiketi almaz.
_LOW_FAT_CAL_SHARE = 0.30

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
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _finite_nonnegative(value):
    value = _num(value)
    return value if math.isfinite(value) and value > 0 else 0


def sanitize_meal_total_macros(calories, protein, carbs, fat):
    values = [
        _finite_nonnegative(value)
        for value in (calories, protein, carbs, fat)
    ]
    calories, protein, carbs, fat = values
    ratios = [1.0]
    if calories:
        ratios.append(MAX_MEAL_TOTAL_KCAL / calories)
    if protein:
        ratios.append(MAX_MEAL_TOTAL_MACRO_G / protein)
    if carbs:
        ratios.append(MAX_MEAL_TOTAL_MACRO_G / carbs)
    if fat:
        ratios.append(MAX_MEAL_TOTAL_FAT_G / fat)
    scale = min(1.0, *ratios)
    calories, protein, carbs, fat = [
        round(value * scale, 1) for value in values
    ]
    supported = 4.0 * protein + 4.0 * carbs + 9.0 * fat
    if calories and supported < calories * (1.0 - ATWATER_HARD_TOLERANCE):
        calories = round(supported, 1)
    return calories, protein, carbs, fat


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


# Yemek adinda ACIKCA belirtilen porsiyon gramaji: '(220 GR)', '(200 Gr)',
# '(110gr)', '(80 g)'. Restoran menulerinde neredeyse her zaman protein ana
# yemegin porsiyonunu belirtir. '(400 GR. 2 Kisilik)' gibi cok-kisilik notunda
# gramaj kisi sayisina bolunur (tek porsiyon).
_STATED_GRAMS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:gr|gram|g)\b", re.IGNORECASE)
_STATED_PERSONS_RE = re.compile(r"(\d+)\s*ki[sş]i", re.IGNORECASE)


def parse_stated_grams(name):
    """Yemek adindaki ACIK porsiyon gramajini (gram) cikar; yoksa ``None``.

    Saf fonksiyon (LLM/ag yok). '(220 GR)' -> 220.0. Cok-kisilik notu varsa
    ('400 GR. 2 Kisilik') gramaj kisi sayisina bolunur (tek porsiyon). Birden
    cok gramaj gecerse ILKI alinir (porsiyon ad'in basinda belirtilir)."""
    if not name:
        return None
    m = _STATED_GRAMS_RE.search(str(name))
    if not m:
        return None
    try:
        grams = float(m.group(1).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if grams <= 0:
        return None
    persons = _STATED_PERSONS_RE.search(str(name))
    if persons:
        n = _num(persons.group(1))
        if n > 1:
            grams = grams / n
    return grams


# Beyan edilen gramajli bir tabak icin imkansiz-DUSUK kalori yogunlugu esigi
# (kcal/g). Pismis tuzlu restoran ana yemekleri tipik olarak >1.5 kcal/g; bunun
# altindaki degerler (220g tavuklu fajita ~0.57, 300g levrek ~0.61, 200g soslu
# schnitzel ~1.3) neredeyse her zaman eksik/hatali bir tahmindir. Gramaji
# belirtilen ogeler protein ana yemekleridir → bu esik onlar icin guvenlidir.
MIN_KCAL_PER_G_STATED = 1.5


def is_low_for_stated_grams(macros, grams, min_kcal_per_g=MIN_KCAL_PER_G_STATED):
    """Beyan gramajli ogenin kalori yogunlugu imkansiz-dusuk mu? (yeniden-tahmin sinyali)

    ``calories / grams < esik`` -> ``True``. Yalnizca POZITIF kalorili, gramaji
    bilinen ogeler icin anlamlidir; aksi halde ``False`` (karar yok). MIKTARI
    denetler, kimligi degil; saf fonksiyon — LLM KULLANMAZ."""
    cal = _num(macros.get("calories")) if macros else 0.0
    g = _num(grams)
    if g <= 0 or cal <= 0:
        return False
    return (cal / g) < min_kcal_per_g


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
    # Yag, genel makro tavanindan daha siki bir esige tabi (olcekleme patlamalarini
    # erken yakalar; tek tabakta >150 g yag fiziksel olarak imkansiz).
    if fat > MAX_SERVING_FAT_G:
        is_valid = False
        reasons.append("fat_exceeds_serving_max")
    # Karb, genel makro tavanindan daha siki bir esige tabi: tek tabakta >200 g
    # karb fiziksel olarak imkansiz (sisirilmis/halusinasyon tahminini yakalar).
    if carbs > MAX_SERVING_CARB_G:
        is_valid = False
        reasons.append("carbs_exceed_serving_max")

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


def clamp_serving_macros(calories, protein, carbs, fat):
    """Tek bir porsiyonun makrolarini fiziksel tavanlara ORANSAL kis.

    ``check_serving`` porsiyonu gecersiz bulursa (orn. 9999 kcal saçma deger),
    en kotu ihlal eden boyutun katsayisiyla TUM makrolari TEK oranla olcekler —
    boylece her tavana uyulur ve Atwater oranlari korunur (her boyutu bagimsiz
    kirpmak protein/karb/yag toplamini kaloriyle tutarsiz birakirdi).

    Donus: ``(calories, protein, carbs, fat)`` — gecersizse kirpilmis ve 1
    ondaliga yuvarli; gecerliyse girdi aynen (yuvarlamadan).

    UI/diyari/menu/koc yollarinin HEPSI bunu kanonik MealLog'a yazmadan once
    cagirmali — aksi halde bir LLM/FatSecret sacmaligi deftere sizar (DB CHECK
    yalnizca >100000 kcal gibi kaba tasmayi yakalar, "3000 kcal" cöpünü degil).
    """
    calories = max(_num(calories), 0)
    protein = max(_num(protein), 0)
    carbs = max(_num(carbs), 0)
    fat = max(_num(fat), 0)
    serving = {"calories": calories, "protein": protein, "carbs": carbs, "fat": fat}
    is_valid, _flags, _reasons = check_serving(serving)
    if is_valid:
        return calories, protein, carbs, fat

    ratios = []
    if calories and calories > MAX_SERVING_KCAL:
        ratios.append(MAX_SERVING_KCAL / calories)
    if protein and protein > MAX_SERVING_MACRO_G:
        ratios.append(MAX_SERVING_MACRO_G / protein)
    if carbs and carbs > MAX_SERVING_CARB_G:
        ratios.append(MAX_SERVING_CARB_G / carbs)
    if fat and fat > MAX_SERVING_FAT_G:
        ratios.append(MAX_SERVING_FAT_G / fat)
    scale = min(ratios) if ratios else 1.0
    if scale < 1.0:
        calories = round(calories * scale, 1)
        protein = round(protein * scale, 1)
        carbs = round(carbs * scale, 1)
        fat = round(fat * scale, 1)

    _valid, _flags, reasons = check_serving({
        "calories": calories, "protein": protein, "carbs": carbs, "fat": fat,
    })
    # Mutlak tavan ölçeklemesinden sonra da enerji korunumu ihlali kalabilir.
    # Kalan her sert Atwater ihlalini makroların desteklediği enerjiye indir;
    # makroların tamamı sıfırsa güvenli sonuç da sıfır kaloridir.
    if "calories_exceed_macro_energy" in reasons:
        calories = round(4.0 * protein + 4.0 * carbs + 9.0 * fat, 1)
    return calories, protein, carbs, fat


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


# Menu-tarama hattina ozgu alt taban: bir YEMEK icin bu kcal'in altindaki makro
# (orn. 'Sicak Kahvalti' -> 5 kcal) neredeyse her zaman basarisiz/bos bir FatSecret
# eslesmesidir; gercek bir tabak degil. SADECE menu hattinda uygulanir -- koc
# hattinda cay/su gibi dusuk kalorili ogeleri loglamak serbest.
MENU_MIN_DISH_KCAL = 20.0


def is_implausibly_low_menu_kcal(macros, min_kcal=MENU_MIN_DISH_KCAL):
    """Menu-tarama: makro absurd-dusuk kalorili mi (~basarisiz FatSecret eslesmesi)?

    ``0 < calories < min_kcal`` -> True. ``calories <= 0`` ayri bir durumdur
    ("veri yok") ve cagiran tarafindan ele alinir; bu yuzden burada True donmez.
    is_pure_fat_ingredient ile ayni menu-sanitize damarinda yer alir."""
    cal = _num(macros.get("calories"))
    return 0.0 < cal < min_kcal


# Ekmek/hamur bazli yemekler (burger, pizza, makarna) icin asgari karb (gram):
# bu turlerin tabaninda ekmek/hamur vardir → karb pratikte hicbir zaman ~0 olmaz.
# 0 karbli bir 'burger/pizza/makarna' kaydi ekmeksiz koftedir (FatSecret patty-only)
# ya da yanlis eslesmedir → reddedilip LLM'e (ekmek karbini ekleyen) birakilmali.
BREADBASED_MIN_CARB_G = 8.0
_BREADBASED_DISH_TYPES = frozenset({"burger", "pizza", "pasta"})


def is_breadbased_zero_carb(macros, dish_type):
    """Ekmek/hamur bazli yemek (burger/pizza/makarna) sifir-karbli mi? (kimlik hatasi)

    Saha vakasi (ai-chatbot-menu.txt): 'Chicken Burger' → 82 g protein, 0 g karb
    (ekmeksiz koftenin FatSecret kaydi). Burger/pizza/makarna her zaman ekmek/hamur
    icerir → karb 0 olamaz. ``dish_type`` bread-bazli sinifta VE ``calories > 0`` VE
    ``carbs < BREADBASED_MIN_CARB_G`` ise True (kayit yanlis → cagiran reddetmeli).
    Saf fonksiyon (LLM/ag yok); ``dish_type`` cagiran tarafindan cozulur."""
    if dish_type not in _BREADBASED_DISH_TYPES:
        return False
    cal = _num(macros.get("calories")) if macros else 0.0
    if cal <= 0:
        return False
    return _num(macros.get("carbs")) < BREADBASED_MIN_CARB_G


# Adinda acik protein kaynagi gecen bir yemek bu gramin altinda protein
# icermemeli; altindaysa eslesme yemegin kendisine degil bir sosa/garniture
# carpmistir (saha vakasi: 'Sweet Chili Soslu Tavuk' → 4 g protein, sos kaydi).
PROTEIN_DISH_MIN_G = 8.0
# Turkce karakterleri ASCII'ye katlar (saf modul: app/ai_nutrition importu YOK).
# Anahtar eslestirmesi aksandan bagimsiz olsun ('Köfte'→'kofte', 'Balık'→'balik').
_TR_FOLD_LOWER = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})
# Foldlandiktan sonra ASCII alt-dize olarak aranir (TR + EN). Corba/salata turleri
# haric tutulur (tavuk corbasi/salatasi mesru dusuk protein yogunlugu icerebilir).
# NOT: 'hindi' (hindi eti) bilincli olarak yok — 'Hindistan' (hindistan cevizi =
# coconut) icinde alt-dize olarak gecip dusuk-proteinli hindistan cevizli tatlilari
# yanlis reddederdi; hindi-eti ana yemegi nadir, collision riski daha buyuk.
_PROTEIN_SOURCE_KEYWORDS = (
    "tavuk", "chicken", "biftek", "steak", "kofte", "balik", "somon", "salmon",
    "karides", "shrimp", "dana", "kuzu", "bonfile", "schnitzel", "snitzel",
)
_PROTEIN_DISH_EXCLUDE_TYPES = frozenset({"soup", "salad"})


def is_protein_dish_low_protein(name, macros, dish_type=None):
    """Adinda protein kaynagi gecen yemek imkansiz-dusuk proteinli mi? (kimlik hatasi)

    Ad bir protein kaynagi (tavuk/et/balik...) iceriyorsa VE tur corba/salata DEGILSE
    VE ``0 < protein < PROTEIN_DISH_MIN_G`` ise True → eslesme yemege degil bir
    sosa/garniture carpmistir; cagiran reddedip LLM'e birakmali. Esik bilincli
    dusuk (yalniz 4 g gibi net hatalari yakalar, mesru hafif yemekleri bozmaz).
    Saf fonksiyon (LLM/ag yok)."""
    if not name:
        return False
    if dish_type in _PROTEIN_DISH_EXCLUDE_TYPES:
        return False
    folded = str(name).translate(_TR_FOLD_LOWER).lower()
    if not any(kw in folded for kw in _PROTEIN_SOURCE_KEYWORDS):
        return False
    protein = _num(macros.get("protein")) if macros else 0.0
    return 0.0 < protein < PROTEIN_DISH_MIN_G


# ---------------------------------------------------------------------------
# Porsiyon makullugu (yemek-turune gore alt/ust kalori bandi) — bkz.
# docs/menu-porsiyon-eslesme-hatasi.md. Kimlik kapilari DOGRU yemegi secse de
# FatSecret per-serving kaydi kucuk bir ABD referans miktari olabilir (tek
# kofte, 1/2 cup) → butun tabak 2-3x eksik sayilir. Bu kurallar MIKTARI denetler.
# dish_type anahtarlari ai_nutrition._DISH_TYPE_KEYWORDS siniflaridir; cagiran
# taraf turu cozer (bu modul saf kalir, taksonomi importu yapmaz).

# Yemek-turune gore makul tek-porsiyon kalori bandi (kcal). Pizza bandi TEK
# KISILIK tam pizza (~30 cm) icindir: margarita ~850, dort peynirli ~1050;
# 1100 ustu (saha vakasi: 1320 kcal Margarita) sisirilmis tahmindir. Tatli
# bandi tek dilim/kase porsiyonu denetler (800 kcal'lik sufle elenir/kirpilir).
PORTION_KCAL_BANDS = {
    "burger": (350.0, 800.0),
    "pasta": (350.0, 700.0),
    "salad": (150.0, 600.0),
    "soup": (150.0, 400.0),
    "pizza": (400.0, 1100.0),
    "dessert": (150.0, 700.0),
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
    "dessert": 150.0,
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
    "dessert": 80.0,
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


def clamp_to_band(macros, dish_type):
    """Bant-USTU makrolari turun ust sinirina oransal olarak kirp.

    Donus: ``(macros, changed)``. Yalnizca ``check_portion_band == "high"``
    durumunda tum makrolar ayni katsayiyla olceklenir (kalori = bant ustu;
    oransal olcekleme Atwater tutarliligini korur). "low"/"ok"/None oldugu
    gibi birakilir — dusuk taraf mesru olabilir (cocuk porsiyonu, kucuk kase)
    ve kaynak-tarafi kapilar (gate_per_serving) zaten duzeltiyor.

    Tek bogum noktasi olarak menu hattinda skor oncesi cagrilir: cache,
    FatSecret accept, per-100g olcekleme ve LLM yedegi dahil HER kaynaktan
    sizan sisirilmis degeri (saha vakasi: Margarita pizza 1320 kcal) yakalar.
    LLM KULLANMAZ."""
    band = check_portion_band(macros.get("calories"), dish_type)
    if band != "high":
        return macros, False
    cal = _num(macros.get("calories"))
    _low, high = PORTION_KCAL_BANDS[dish_type]
    scale = high / cal
    clamped = {
        "calories": round(cal * scale, 1),
        "protein": round(_num(macros.get("protein")) * scale, 1),
        "carbs": round(_num(macros.get("carbs")) * scale, 1),
        "fat": round(_num(macros.get("fat")) * scale, 1),
    }
    return clamped, True


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
        olur ve ``"Exceeds daily budget limit"`` uyarisi tetiklenir. KARBONHIDRAT
        BILEREK haric: karb tasmasi hard-zero degil, ilerlemeli ceza + "High
        carbohydrate load" uyarisi alir (kalori zaten karbi izledigi icin kalori
        kapisi en kotu durumu yakalar) — bu asimetri tasarim geregidir (1.5).
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
    # low_fat: besinin KENDI yag-enerji payina gore (kalan butceye gore degil) —
    # yagli yemekler buyuk butce kaldiginda yanlis "dusuk yag" damgasi almasin.
    fat_cal_share = (9.0 * fat / cal) if cal > 0 else 0.0
    if cal > 0 and fat_cal_share < _LOW_FAT_CAL_SHARE:
        flags.append("low_fat")
    # high_protein: hem kalori payi (>=0.20) HEM DE mutlak gram (>=_HIGH_PROTEIN_MIN_G)
    # esigini gecmeli — kucuk porsiyonlu az-protein (orn. 3 g'lik salata) elensin.
    if protein_cal_share >= 0.20 and protein >= _HIGH_PROTEIN_MIN_G:
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
