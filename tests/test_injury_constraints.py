"""Unit tests for the deterministic injury contraindication engine
(app/services/injury_constraints.py).

Saf fonksiyonlar — Flask/DB gerektirmez. Eşleştirme (TR + EN, aksan), katı direktif
üretimi, post-filtre tespiti ve null/boş güvenliği sınanır.

    python -m pytest tests/test_injury_constraints.py -v
"""
import pytest

from app.services import injury_constraints as ic


# ---------------------------------------------------------------------------
# Null / boş / "yok" güvenliği — sakatlık yoksa ASLA kısıt üretme, asla patlama
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    None, "", "   ", "Hiçbiri", "hiçbiri", "HİÇBİRİ", "yok", "none", "No", "-", "n/a",
])
def test_none_like_values_produce_no_constraints(value):
    assert ic.has_constraints(value) is False
    assert ic.build_injury_directive(value) == ""
    assert ic.banned_exercise_names(value) == set()
    assert ic.find_contraindicated("Back Squat", value) is None


def test_find_contraindicated_handles_empty_exercise_name():
    assert ic.find_contraindicated("", "menisküs") is None
    assert ic.find_contraindicated(None, "menisküs") is None


# ---------------------------------------------------------------------------
# Eşleştirme — TR + EN takma adlar, aksan-bağımsız
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["menisküs", "Meniscus tear", "sağ menisküs yırtığı"])
def test_meniscus_matches_tr_and_en(text):
    assert ic.has_constraints(text)
    assert ic.find_contraindicated("Barbell Back Squat", text) == "back squat"
    assert ic.find_contraindicated("Box Jump", text) == "box jump"


def test_meniscus_safe_alternative_not_flagged():
    # Güvenli alternatifler kontrendike sayılmamalı (yanlış pozitif yok).
    assert ic.find_contraindicated("Leg Curl", "menisküs") is None
    assert ic.find_contraindicated("Leg Press", "menisküs") is None


def test_kyphosis_english_alias_and_behind_neck():
    assert ic.find_contraindicated("Behind Neck Press", "kyphosis") == "behind neck"


def test_lumbar_herniation_turkish_possessive_catches_deadlift():
    # 'bel fıtığı' → normalize 'bel fitigi'; 'fitik' alt-dize değil → 'fitig' eki şart.
    assert ic.find_contraindicated("Conventional Deadlift", "bel fıtığı") == "deadlift"
    assert ic.find_contraindicated("Russian Twist", "lumbar herniation") == "russian twist"


def test_scoliosis_axial_load_flagged():
    assert ic.find_contraindicated("Heavy Back Squat", "skolyoz") == "heavy back squat"


def test_shoulder_upright_row_flagged():
    assert ic.find_contraindicated("Upright Row", "omuz sıkışması") == "upright row"


def test_unrelated_condition_does_not_match_known_set():
    # Tanınmayan ama dolu girdi: post-filtre tetiklemez ama direktif yine de uyarır.
    assert ic.banned_exercise_names("uçuk garip bir durum") == set()
    assert ic.find_contraindicated("Deadlift", "uçuk garip bir durum") is None
    directive = ic.build_injury_directive("uçuk garip bir durum")
    assert directive != ""                       # sakatlık verisi sessizce yutulmaz
    assert "güvenli" in directive.lower()


# ---------------------------------------------------------------------------
# Direktif üretimi — katı, yapısal blok
# ---------------------------------------------------------------------------

def test_directive_contains_banned_safe_and_focus():
    d = ic.build_injury_directive("menisküs")
    assert "KONTRENDİKASYON" in d
    assert "YASAK" in d
    assert "GÜVENLİ ALTERNATİF" in d
    assert "Leg Press" in d                       # bir güvenli alternatif


def test_directive_multi_condition_lists_each():
    d = ic.build_injury_directive("Menisküs ve Kifoz")
    assert "Menisküs" in d
    assert "Kifoz" in d
    # İki ayrı durum bloğu (▸ işaretçisi) bulunmalı.
    assert d.count("▸") >= 2


def test_banned_exercise_names_returns_normalized_set():
    names = ic.banned_exercise_names("menisküs")
    assert "deep squat" in names
    assert "box jump" in names
    assert all(n == n.lower() for n in names)     # hepsi normalize/küçük harf
