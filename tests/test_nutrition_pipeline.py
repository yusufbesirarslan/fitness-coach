"""Unit tests for the deterministic nutrition pipeline (nutrition_pipeline.py).

Saf fonksiyon testleri: DB / Flask / psycopg2 / OpenAI gerektirmez, lokalde calisir.
    python -m pytest tests/test_nutrition_pipeline.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nutrition_pipeline as np  # noqa: E402
from nutrition_pipeline import (  # noqa: E402
    MAX_KCAL_PER_100G,
    build_evaluation,
    check_serving,
    estimate_serving_grams,
    sanitize_servings,
    score_compatibility,
)


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
