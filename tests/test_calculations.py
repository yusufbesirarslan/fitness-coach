"""Unit tests for the core domain math (app/services/calculations.py).

BMR/TDEE/hedef kalori her planı besler. TDEE'deki bilinmeyen-anahtar →
sedentary fallback'i geçmişte gerçek bir bug'dı ("aktif" vs "active");
bu testler o davranışı sabitler. Saf fonksiyonlar: DB / ağ yok.

    python -m pytest tests/test_calculations.py -v
"""
import pytest

from app.services.calculations import (
    calculate_activity_calories,
    calculate_bmr,
    calculate_target,
    calculate_tdee,
    generate_nutrition_plan,
    generate_training_plan,
)


# ---------------------------------------------------------------------------
# BMR (Mifflin-St Jeor)
# ---------------------------------------------------------------------------

def test_bmr_male():
    # 10*80 + 6.25*180 - 5*30 + 5
    assert calculate_bmr(80, 180, 30, "male") == 1780


def test_bmr_female():
    # 10*80 + 6.25*180 - 5*30 - 161
    assert calculate_bmr(80, 180, 30, "female") == 1614


def test_bmr_gender_case_insensitive():
    assert calculate_bmr(80, 180, 30, "Male") == 1780
    # 'male' dışındaki her değer kadın formülüne düşer (mevcut sözleşme).
    assert calculate_bmr(80, 180, 30, "FEMALE") == 1614


# ---------------------------------------------------------------------------
# TDEE — aktivite çarpanları ve bilinmeyen anahtar fallback'i
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("activity,multiplier", [
    ("sedentary", 1.2),
    ("active", 1.55),
    ("very_active", 1.75),
    ("ACTIVE", 1.55),       # büyük/küçük harf duyarsız
])
def test_tdee_multipliers(activity, multiplier):
    assert calculate_tdee(1000, activity) == pytest.approx(1000 * multiplier)


def test_tdee_unknown_activity_falls_back_to_sedentary():
    # Geçmiş bug: frontend "aktif" gönderiyordu, sözlükte yoktu → sessizce 1.2.
    # Fallback davranışı bilinçli; bu test onu görünür kılar.
    assert calculate_tdee(1000, "aktif") == pytest.approx(1200)
    assert calculate_tdee(1000, "") == pytest.approx(1200)


# ---------------------------------------------------------------------------
# Hedef kalori
# ---------------------------------------------------------------------------

def test_target_calorie_adjustments():
    assert calculate_target(2000, "kilo verme") == 1600
    assert calculate_target(2000, "kas kazanma") == 2300
    assert calculate_target(2000, "korumak") == 2000
    assert calculate_target(2000, "KILO VERME") == 1600


# ---------------------------------------------------------------------------
# Adım → kalori (MET modeli)
# ---------------------------------------------------------------------------

def test_activity_calories_moderate_walk():
    calories, distance_km, duration_min = calculate_activity_calories(
        steps=10_000, intensity="moderate", weight_kg=70, height_cm=170)
    # stride 70.38 cm → 7.038 km; 7.038/4.5 saat; 3.5 MET * 70 kg * süre
    assert distance_km == 7.04
    assert duration_min == 93.8
    assert calories == 383.2


def test_activity_calories_zero_steps():
    assert calculate_activity_calories(0, "brisk", 70, 170) == (0.0, 0.0, 0.0)


def test_activity_calories_rejects_nonpositive_profile():
    # Eksik profil (boy/kilo 0) sessizce 0 döndürmemeli — geçersiz girdi maskelenmesin (F1.3).
    with pytest.raises(ValueError):
        calculate_activity_calories(10_000, "moderate", weight_kg=0, height_cm=170)
    with pytest.raises(ValueError):
        calculate_activity_calories(10_000, "moderate", weight_kg=70, height_cm=0)


def test_activity_calories_unknown_intensity_uses_moderate():
    assert calculate_activity_calories(10_000, "turbo", 70, 170) == \
        calculate_activity_calories(10_000, "moderate", 70, 170)


def test_activity_calories_scale_with_intensity():
    # Aynı adım sayısında hızlı tempo daha kısa sürer ama MET'i yüksektir.
    cal_light, _, dur_light = calculate_activity_calories(10_000, "light", 70, 170)
    cal_fast, _, dur_fast = calculate_activity_calories(10_000, "fast", 70, 170)
    assert dur_fast < dur_light
    assert cal_fast != cal_light


# ---------------------------------------------------------------------------
# Plan üreticileri
# ---------------------------------------------------------------------------

def test_training_plan_known_combos_and_fallback():
    assert "yürüyüş" in generate_training_plan("kilo verme", "beginner")
    assert "split" in generate_training_plan("kas kazanma", "advanced")
    assert generate_training_plan("Kilo Verme", "Beginner") == \
        generate_training_plan("kilo verme", "beginner")  # case-insensitive
    assert generate_training_plan("bilinmeyen", "beginner") == "Standart plan"


def test_nutrition_plan_embeds_target_calories():
    assert "1600" in generate_nutrition_plan("kilo verme", 1600.4)
    assert "2300" in generate_nutrition_plan("kas kazanma", 2300.0)
    assert generate_nutrition_plan("korumak", 2000) == "Standart beslenme planı"
