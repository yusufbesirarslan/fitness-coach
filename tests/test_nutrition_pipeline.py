"""Unit tests for the deterministic nutrition pipeline (nutrition_pipeline.py).

Saf fonksiyon testleri: DB / Flask / psycopg2 / OpenAI gerektirmez, lokalde calisir.
    python -m pytest tests/test_nutrition_pipeline.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nutrition_pipeline as np  # noqa: E402
from nutrition_pipeline import (  # noqa: E402
    DISH_SERVING_DEFAULT_G,
    DISH_SERVING_MIN_G,
    MAX_KCAL_PER_100G,
    PORTION_KCAL_BANDS,
    build_evaluation,
    check_portion_band,
    check_serving,
    estimate_serving_grams,
    gate_per_serving,
    is_pure_fat_ingredient,
    parse_fatsecret_serving,
    sanitize_servings,
    score_compatibility,
)


def _macros(cal, protein, carbs, fat):
    return {"calories": cal, "protein": protein, "carbs": carbs, "fat": fat}


def test_pure_fat_ingredient_detects_oils():
    # 'Zeytin Tabağı' field bug: 'olive' matched 'Olive Oil' (pure fat) → scaled to
    # 1326 kcal / 150 g fat. Pure oils/fats are ingredients, not menu dishes.
    assert is_pure_fat_ingredient(_macros(900, 0, 0, 100)) is True      # olive oil /100g
    assert is_pure_fat_ingredient(_macros(1326, 0, 0, 150)) is True     # the exact field value
    assert is_pure_fat_ingredient(_macros(884, 0, 0, 100)) is True      # generic oil
    assert is_pure_fat_ingredient(_macros(119, 0, 0, 13.5)) is True     # 1 tbsp oil serving


def test_real_foods_are_not_pure_fat():
    # Whole olives have carbs+protein → kept (not flagged as the oil ingredient).
    assert is_pure_fat_ingredient(_macros(115, 0.8, 6.0, 11.0)) is False
    assert is_pure_fat_ingredient(_macros(160, 2.0, 9.0, 15.0)) is False   # avocado
    assert is_pure_fat_ingredient(_macros(717, 0.85, 0.06, 81.0)) is False  # butter (trace protein)
    assert is_pure_fat_ingredient(_macros(705, 82, 0, 40)) is False        # a burger
    assert is_pure_fat_ingredient(_macros(0, 0, 0, 0)) is False            # empty


def _serving(amount, cal, protein, carbs, fat, **extra):
    s = {
        "metric_serving_amount": amount,
        "metric_serving_unit": "g",
        "calories": cal,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
    }
    s.update(extra)
    return s


# ---------------------------------------------------------------------------
# MOCK FIXTURE — ham FatSecret API yanitlarini taklit eder (todos.txt §3)
# Gercek API'de degerler STRING gelir ve karbonhidrat anahtari 'carbohydrate'tir.
# ---------------------------------------------------------------------------
RAW_FATSECRET_SERVINGS = [
    {   # Dogrulanmis yuksek-protein, yagsiz et (Bonfile / dana bonfile)
        "serving_id": "1", "serving_description": "100 g",
        "metric_serving_amount": "100.0", "metric_serving_unit": "g",
        "calories": "143", "protein": "26.0", "carbohydrate": "0.0", "fat": "4.0",
    },
    {   # Yuksek-karb (pirinc pilavi)
        "serving_id": "2", "serving_description": "100 g",
        "metric_serving_amount": "100.0", "metric_serving_unit": "g",
        "calories": "130", "protein": "2.7", "carbohydrate": "28.0", "fat": "0.3",
    },
    {   # Bozuk/aykiri-deger (Pesto Soslu Makarna): 350 g'da 4696 kcal / 440 g yag
        "serving_id": "3", "serving_description": "1 serving",
        "metric_serving_amount": "350.0", "metric_serving_unit": "g",
        "calories": "4696", "protein": "100.0", "carbohydrate": "84.0", "fat": "440.0",
    },
]


class TestFatSecretMapping:
    """todos.txt §3.1 & §3.2: ham FatSecret yaniti -> dogru anahtar eslemesi +
    bozuk/aykiri girdinin son yanit dizisinden tamamen filtrelenmesi."""

    def test_keys_map_to_exact_fields(self):
        beef = parse_fatsecret_serving(RAW_FATSECRET_SERVINGS[0])
        # Yagsiz et KARB degeri ALMAMALI (protein<->carb kaymasi yok).
        assert beef["protein"] == 26.0
        assert beef["carbs"] == 0.0
        assert beef["fat"] == 4.0
        assert beef["calories"] == 143.0

        rice = parse_fatsecret_serving(RAW_FATSECRET_SERVINGS[1])
        assert rice["carbs"] == 28.0
        assert rice["protein"] == 2.7
        assert rice["fat"] == 0.3

    def test_outlier_excluded_from_payload(self):
        parsed = [parse_fatsecret_serving(r) for r in RAW_FATSECRET_SERVINGS]
        clean = sanitize_servings(parsed)
        ids = {s["serving_id"] for s in clean}
        assert "3" not in ids        # Pesto aykiri-degeri elendi
        assert ids == {"1", "2"}     # sadece gecerli iki girdi kaldi
        assert len(clean) == 2


# ---------------------------------------------------------------------------
# MODULE 1 — Sanity check boundaries
# ---------------------------------------------------------------------------

class TestCaloricDensity:
    def test_density_exactly_900_passes(self):
        # 100g @ 900 kcal == tavan; gecmeli.
        valid, _flags, reasons = check_serving(_serving(100, 900, 0, 0, 100))
        assert valid is True
        assert "caloric_density_exceeds_900" not in reasons

    def test_density_just_over_900_discarded(self):
        valid, _flags, reasons = check_serving(_serving(100, 901, 0, 0, 100))
        assert valid is False
        assert "caloric_density_exceeds_900" in reasons

    def test_3000_kcal_cup_of_tea_discarded(self):
        # 200 ml cay, 3000 kcal -> 1500 kcal/100 -> termodinamik olarak imkansiz.
        valid, _flags, reasons = check_serving(_serving(200, 3000, 0, 0, 0))
        assert valid is False
        assert "caloric_density_exceeds_900" in reasons


class TestMacroWeight:
    def test_macros_equal_weight_passes(self):
        # P+C+F == porsiyon agirligi -> sinirda gecerli.
        valid, _flags, reasons = check_serving(_serving(100, 400, 30, 40, 30))
        assert valid is True
        assert "macro_weight_exceeds_serving" not in reasons

    def test_macros_exceed_weight_discarded(self):
        # P+C+F = 102 > 100 (+1 tolerans) -> gecersiz.
        valid, _flags, reasons = check_serving(_serving(100, 400, 40, 40, 22))
        assert valid is False
        assert "macro_weight_exceeds_serving" in reasons

    def test_no_metric_amount_skips_weight_check(self):
        # Agirlik 0 -> agirlik/yogunluk kontrolleri atlanir (gecerli kabul).
        valid, _flags, reasons = check_serving(_serving(0, 400, 40, 40, 22))
        assert valid is True
        assert reasons == []


class TestAtwaterConsistency:
    def test_consistent_macros_not_flagged(self):
        # 4*30 + 4*40 + 9*4 = 316 ~ 320 beyan -> tutarli.
        _valid, flags, _reasons = check_serving(_serving(100, 320, 30, 40, 4))
        assert "macros_inconsistent" not in flags

    def test_inconsistent_macros_flagged_but_not_discarded(self):
        # Beyan 100 kcal ama 4*30+4*40+9*5 = 325 -> buyuk sapma -> isaretlenir.
        valid, flags, _reasons = check_serving(_serving(100, 100, 30, 40, 5))
        assert "macros_inconsistent" in flags
        assert valid is True  # yumusak kontrol: silmez


class TestServingPlausibility:
    """Bug 2 (macro mapping & filter leakage): imkansiz makro profilleri yanit
    dizisinden TAMAMEN silinmeli — sadece skor 0 verilip tutulmamali."""

    def test_meat_with_zero_protein_rejected(self):
        # "Bonfile" senaryosu: kayda deger kalori ama makrolar bunu aciklayamiyor
        # (kalori yoktan var olamaz) -> enerji korunumu ihlali, ELE.
        valid, _flags, reasons = check_serving(_serving(0, 200, 0, 6, 1))
        assert valid is False
        assert "calories_exceed_macro_energy" in reasons

    def test_absurd_serving_discarded_by_absolute_caps(self):
        # "Pesto Soslu Makarna": 4696 kcal / 440 g yag -> tek porsiyon icin imkansiz.
        valid, _flags, reasons = check_serving(_serving(0, 4696, 100, 84, 440))
        assert valid is False
        assert "calories_exceed_serving_max" in reasons
        assert "macro_exceeds_serving_max" in reasons

    def test_normal_large_meal_passes(self):
        # Mesru buyuk karisik tabak (~1500 kcal) elenmemeli (false positive yok).
        valid, _flags, reasons = check_serving(_serving(0, 1500, 90, 120, 60))
        assert valid is True
        assert reasons == []

    def test_sanitize_drops_impossible_servings(self):
        good = _serving(200, 520, 30, 60, 18)     # makul tek porsiyon
        bonfile = _serving(0, 200, 0, 6, 1)       # kalori-makro enerji ihlali
        pesto = _serving(0, 4696, 100, 84, 440)   # absurd porsiyon
        out = sanitize_servings([good, bonfile, pesto])
        assert len(out) == 1
        assert out[0]["calories"] == 520


class TestSanitizeServings:
    def test_drops_invalid_keeps_valid(self):
        good = _serving(100, 165, 31, 0, 3.6)          # tavuk gogsu
        bad = _serving(100, 5000, 0, 0, 0)              # imkansiz yogunluk
        out = sanitize_servings([good, bad])
        assert len(out) == 1
        assert out[0]["calories"] == 165

    def test_generic_sorted_before_brand(self):
        brand = _serving(100, 200, 10, 10, 10, food_type="Brand")
        generic = _serving(100, 150, 12, 8, 6, food_type="Generic")
        out = sanitize_servings([brand, generic])
        assert out[0]["food_type"] == "Generic"

    def test_empty_input(self):
        assert sanitize_servings([]) == []
        assert sanitize_servings(None) == []


class TestPortionMatrix:
    def test_known_descriptors_resolve(self):
        assert estimate_serving_grams("1 slice") == 30.0
        assert estimate_serving_grams("2 dilim ekmek") == 30.0
        assert estimate_serving_grams("1 simit") == 100.0
        assert estimate_serving_grams("1 cup") == 200.0

    def test_specific_before_general(self):
        # "su bardağı" genel "bardak"tan once eslesmeli.
        assert estimate_serving_grams("1 su bardağı") == 200.0
        assert estimate_serving_grams("1 yemek kaşığı") == 15.0

    def test_unknown_returns_none(self):
        assert estimate_serving_grams("xyzzy frobnicate") is None
        assert estimate_serving_grams("") is None
        assert estimate_serving_grams(None) is None


# ---------------------------------------------------------------------------
# MODULE 2 — Scoring edge cases
# ---------------------------------------------------------------------------

class TestScoringHardLimit:
    def test_exceeds_calorie_budget_scores_zero(self):
        food = {"calories": 600, "protein": 30, "carbs": 50, "fat": 20}
        remaining = {"calories": 500, "protein": 100, "carbs": 100, "fat": 50}
        result = score_compatibility(food, remaining)
        assert result["score"] == 0
        assert "Exceeds daily budget limit" in result["warnings"]

    def test_exceeds_fat_budget_scores_zero(self):
        food = {"calories": 200, "protein": 5, "carbs": 5, "fat": 30}
        remaining = {"calories": 2000, "protein": 100, "carbs": 200, "fat": 20}
        result = score_compatibility(food, remaining)
        assert result["score"] == 0
        assert "Exceeds daily budget limit" in result["warnings"]


class TestScoringEdgeBudgets:
    def test_zero_remaining_any_food_zero(self):
        food = {"calories": 100, "protein": 10, "carbs": 10, "fat": 5}
        remaining = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
        result = score_compatibility(food, remaining)
        assert result["score"] == 0
        assert "Exceeds daily budget limit" in result["warnings"]

    def test_negative_remaining_no_crash(self):
        food = {"calories": 100, "protein": 10, "carbs": 10, "fat": 5}
        remaining = {"calories": -300, "protein": -10, "carbs": -5, "fat": -2}
        result = score_compatibility(food, remaining)
        assert result["score"] == 0  # butce asilmis -> her pozitif makro asar

    def test_zero_calorie_food_zero_budget_does_not_crash(self):
        # Kalorisiz su/cay, butce 0 -> butceyi asmaz, makul yuksek skor.
        food = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
        remaining = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
        result = score_compatibility(food, remaining)
        assert 0 <= result["score"] <= 100


class TestScoringOutputShape:
    def test_score_always_int_in_range(self):
        cases = [
            ({"calories": 50, "protein": 10, "carbs": 2, "fat": 1},
             {"calories": 1500, "protein": 120, "carbs": 150, "fat": 50}),
            ({"calories": 480, "protein": 5, "carbs": 90, "fat": 10},
             {"calories": 600, "protein": 100, "carbs": 100, "fat": 40}),
        ]
        for food, remaining in cases:
            result = score_compatibility(food, remaining)
            assert isinstance(result["score"], int)
            assert 0 <= result["score"] <= 100

    def test_perfect_fit_high_score(self):
        # Yagsiz, proteinli, kalori butcesine rahat sigan -> yuksek skor.
        food = {"calories": 120, "protein": 26, "carbs": 0, "fat": 1.5}
        remaining = {"calories": 1500, "protein": 120, "carbs": 150, "fat": 50}
        result = score_compatibility(food, remaining)
        assert result["score"] >= 90
        assert "fits_calorie_budget" in result["flags"]

    def test_protein_bonus_applied(self):
        # Proteinli besin, esik altinda kalan butce -> bonus skoru tam tutmali.
        food = {"calories": 110, "protein": 25, "carbs": 1, "fat": 1}
        remaining = {"calories": 1200, "protein": 80, "carbs": 120, "fat": 40}
        result = score_compatibility(food, remaining)
        assert result["score"] == 100
        assert "high_protein" in result["flags"]

    def test_carb_overload_warns_but_not_zero(self):
        food = {"calories": 300, "protein": 2, "carbs": 70, "fat": 1}
        remaining = {"calories": 2000, "protein": 100, "carbs": 50, "fat": 60}
        result = score_compatibility(food, remaining)
        assert "High carbohydrate load" in result["warnings"]
        assert result["score"] > 0  # karbda kati 0 kurali yok


class TestGranularScoring:
    """Bug 1 (binary collapse): butceye sigan ama makro-dengesiz besinler 100
    yerine granuler (orta) puan almali; skor protein kalitesiyle olceklenmeli."""

    def test_pure_carb_when_protein_needed_scores_mid(self):
        # Saf karb, butceye rahat siger ama kullanici hala proteine ihtiyac duyuyor.
        food = {"calories": 300, "protein": 0, "carbs": 70, "fat": 1}
        remaining = {"calories": 1500, "protein": 120, "carbs": 150, "fat": 50}
        result = score_compatibility(food, remaining)
        assert 60 <= result["score"] <= 80      # artik flat 100 degil
        assert "low_protein_food" in result["flags"]

    def test_score_scales_with_protein_quality(self):
        # Protein kalitesi arttikca skor MONOTON artmali (granulerlik kaniti).
        remaining = {"calories": 1500, "protein": 120, "carbs": 150, "fat": 50}
        pure_carb = score_compatibility(
            {"calories": 300, "protein": 0, "carbs": 73, "fat": 1}, remaining)["score"]
        mixed = score_compatibility(
            {"calories": 300, "protein": 15, "carbs": 45, "fat": 5}, remaining)["score"]
        lean_protein = score_compatibility(
            {"calories": 300, "protein": 60, "carbs": 8, "fat": 4}, remaining)["score"]
        assert pure_carb < mixed < lean_protein

    def test_no_balance_penalty_when_protein_goal_met(self):
        # Kalan protein 0 -> denge cezasi yok; ayni karb besin yine yuksek alir.
        food = {"calories": 300, "protein": 0, "carbs": 70, "fat": 1}
        remaining = {"calories": 1500, "protein": 0, "carbs": 150, "fat": 50}
        result = score_compatibility(food, remaining)
        assert result["score"] >= 90
        assert "low_protein_food" not in result["flags"]


class TestScoreDistribution:
    """todos.txt §3.3: skorlama ikili (100/0) degil; cesitli/granuler puanlar
    uretmeli ve kullanicinin KALAN profiline gore degismeli."""

    def test_low_mid_high_band_spread(self):
        # Ayni 'protein gerekli' kalan profili; besinin protein kalitesi arttikca
        # skor kademeli yukselir -> ~55 / ~75 / ~90 bantlari.
        remaining = {"calories": 1500, "protein": 130, "carbs": 90, "fat": 50}
        low = score_compatibility(
            {"calories": 450, "protein": 5, "carbs": 82, "fat": 5}, remaining)["score"]
        mid = score_compatibility(
            {"calories": 450, "protein": 12, "carbs": 75, "fat": 8}, remaining)["score"]
        high = score_compatibility(
            {"calories": 450, "protein": 25, "carbs": 55, "fat": 11}, remaining)["score"]
        assert 50 <= low <= 62                          # ~55
        assert 66 <= mid <= 80                          # ~75
        assert 84 <= high <= 96                         # ~90
        assert low < mid < high                         # monoton granulerlik
        assert all(0 < s < 100 for s in (low, mid, high))  # ikili degil

    def test_same_food_varies_by_remaining_profile(self):
        # Ayni karb-agirlikli besin: protein gerekirken dusuk, protein hedefi
        # dolmusken yuksek puan almali (dinamik, kalan-ihtiyac temelli sapma).
        food = {"calories": 400, "protein": 10, "carbs": 70, "fat": 6}
        needs_protein = score_compatibility(
            food, {"calories": 1500, "protein": 140, "carbs": 80, "fat": 45})["score"]
        protein_met = score_compatibility(
            food, {"calories": 1500, "protein": 0, "carbs": 250, "fat": 60})["score"]
        assert needs_protein < protein_met


# ---------------------------------------------------------------------------
# MODULE 3 — Output contract
# ---------------------------------------------------------------------------

class TestBuildEvaluation:
    def test_exact_keys_and_kcal_mapping(self):
        macros = {"calories": 420, "protein": 16, "carbs": 66, "fat": 9}
        remaining = {"calories": 2000, "protein": 120, "carbs": 250, "fat": 70}
        out = build_evaluation("12345", "Simit", "1 Piece (100g)", macros, remaining)

        assert set(out.keys()) == {
            "food_id", "name", "standardized_serving",
            "macros", "compatibility_score", "flags", "warnings",
        }
        assert out["food_id"] == "12345"
        assert out["name"] == "Simit"
        assert out["standardized_serving"] == "1 Piece (100g)"
        assert out["macros"] == {"kcal": 420, "protein": 16, "carbs": 66, "fat": 9}
        assert isinstance(out["compatibility_score"], int)
        assert 0 <= out["compatibility_score"] <= 100

    def test_extra_flags_merged_and_deduped(self):
        macros = {"calories": 120, "protein": 26, "carbs": 0, "fat": 1.5}
        remaining = {"calories": 1500, "protein": 120, "carbs": 150, "fat": 50}
        out = build_evaluation("1", "Tavuk", "100 g", macros, remaining,
                               extra_flags=["fits_calorie_budget", "verified"])
        # Tekrar elenmis olmali, ekstra korunmali.
        assert out["flags"].count("fits_calorie_budget") == 1
        assert "verified" in out["flags"]

    def test_none_food_id_becomes_empty_string(self):
        out = build_evaluation(None, "X", "100 g",
                               {"calories": 10, "protein": 1, "carbs": 1, "fat": 0},
                               {"calories": 100, "protein": 10, "carbs": 10, "fat": 5})
        assert out["food_id"] == ""


def test_constants_sane():
    assert MAX_KCAL_PER_100G == 900.0
    assert np.ATWATER_TOLERANCE > 0


# ---------------------------------------------------------------------------
# Porsiyon makullugu (docs/menu-porsiyon-eslesme-hatasi.md): dogru KIMLIKLI ama
# yanlis MIKTARLI eslesmeler — FatSecret per-serving kucuk ABD referans miktari
# (tek kofte, 1/2 cup) tam tabak sanilinca 2-3x eksik kalori.
# ---------------------------------------------------------------------------

class TestCheckPortionBand:
    def test_field_cases_are_low(self):
        # Saha ciktisi (ai-chatbot-menu.txt): tam tabak sanilan referans miktarlar.
        assert check_portion_band(170, "burger") == "low"   # Vegan Burger
        assert check_portion_band(180, "pasta") == "low"    # Penne Arrabbiata

    def test_in_band_ok(self):
        assert check_portion_band(500, "burger") == "ok"
        assert check_portion_band(450, "pasta") == "ok"
        assert check_portion_band(300, "salad") == "ok"
        assert check_portion_band(250, "soup") == "ok"

    def test_oversized_is_high(self):
        assert check_portion_band(900, "burger") == "high"
        assert check_portion_band(1200, "pasta") == "high"

    def test_no_decision_without_band(self):
        # None = "karar yok": tur bilinmiyor, bandi yok veya kalori <= 0.
        assert check_portion_band(170, None) is None
        assert check_portion_band(170, "kebab") is None     # taksonomide yok
        assert check_portion_band(0, "burger") is None
        assert check_portion_band(-5, "burger") is None


class TestGatePerServing:
    def test_vegan_burger_field_case_converted(self):
        # 'Veggie Burger – per 1 patty = 170 kcal' (ekmeksiz kofte) tam tabak
        # sanilmamali → 100g-esdegeri olarak per-100g yoluna verilir.
        status, conv = gate_per_serving("burger", _macros(170, 5, 20, 8))
        assert status == "convert"
        assert conv == _macros(170, 5, 20, 8)  # agirlik bilinmiyor → as-is ≈100g
        # 300 g tur varsayilaniyla olceklenince banda oturur: 170 * 3 = 510 kcal.
        assert PORTION_KCAL_BANDS["burger"][0] <= conv["calories"] * 3.0 <= PORTION_KCAL_BANDS["burger"][1]

    def test_penne_arrabbiata_field_case_converted(self):
        status, conv = gate_per_serving("pasta", _macros(180, 5, 29, 4))
        assert status == "convert"
        assert conv["calories"] == 180.0

    def test_in_band_serving_accepted(self):
        status, conv = gate_per_serving("burger", _macros(500, 30, 40, 22))
        assert status == "accept"
        assert conv is None

    def test_unknown_dish_type_accepted_unchanged(self):
        # Taksonomi kapsamiyorsa karar yok → mevcut davranis korunur.
        assert gate_per_serving(None, _macros(170, 5, 20, 8)) == ("accept", None)
        assert gate_per_serving("kebab", _macros(170, 5, 20, 8)) == ("accept", None)

    def test_oversized_serving_skipped(self):
        # Band ustu (aile/toplu kayit): 100g varsaymak degeri patlatir → atla.
        status, conv = gate_per_serving("pasta", _macros(1200, 40, 150, 45))
        assert status == "skip"
        assert conv is None

    def test_known_grams_ratio_conversion(self):
        # '1 cup' corba (200 g matris degeri) 120 kcal → 60 kcal/100g yogunluk.
        status, conv = gate_per_serving("soup", _macros(120, 6, 14, 4), serving_grams=200.0)
        assert status == "convert"
        assert conv["calories"] == 60.0
        assert conv["protein"] == 3.0

    def test_invalid_density_falls_back_to_as_is(self):
        # Kaba matris agirligi ('dilim'→30g) oran donusumunde 900 kcal/100g
        # tavanini asarsa as-is 100g-esdegerine dusulur.
        status, conv = gate_per_serving("pizza", _macros(285, 12, 36, 10), serving_grams=30.0)
        assert status == "convert"
        assert conv["calories"] == 285.0  # 950/100g gecersiz → as-is

    def test_small_grams_triggers_convert_even_in_band(self):
        # Band-ici kalori ama metrik agirlik turun asgarisinden kucuk → tam tabak
        # degil (belge #2): yine donustur.
        status, conv = gate_per_serving("burger", _macros(400, 25, 30, 18), serving_grams=120.0)
        assert status == "convert"
        # Oran donusumu gecerli yogunluk verir: 400 * 100/120 ≈ 333 kcal/100g.
        assert conv["calories"] == round(400 * 100.0 / 120.0, 1)


def test_portion_tables_self_consistent():
    # Tablolar ayni tur kumesini kapsamali; degerler kendi iclerinde tutarli olmali.
    assert set(PORTION_KCAL_BANDS) == set(DISH_SERVING_DEFAULT_G) == set(DISH_SERVING_MIN_G)
    for dish, (low, high) in PORTION_KCAL_BANDS.items():
        assert 0 < low < high
        assert high <= np.MAX_SERVING_KCAL
        # LLM kelepcesi 50-600 g (ai_nutrition._estimate_serving_weights_llm).
        assert 50 <= DISH_SERVING_DEFAULT_G[dish] <= 600
        assert 0 < DISH_SERVING_MIN_G[dish] <= DISH_SERVING_DEFAULT_G[dish]
