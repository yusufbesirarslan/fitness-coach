"""MINERVA-DEMO-R4: generated day titles read as titles, not programming notes.

The device showed "Full Body B – Tek Bacak, Dikey İtme/Çekme, Glute Odak" as a
session's name: the general-fitness few-shot's notes, translated word for
word. The prompt now names the title format, and a Turkish title still in the
note shape is rewritten before the plan is persisted. Nothing but ``odak``
changes.

    python -m pytest tests/test_minerva_r4_plan_naming.py -v
"""
import copy
import json

import pytest

from app.services.exercise_catalog import ExerciseContext
from app.services.training_generation.exercise_choices import (
    compatible_exercise_choices,
)
from app.services.training_generation.focus_title import (
    member_focus_title,
    normalize_generated_titles,
)
from app.services.training_generation.models import (
    ClassificationResult,
    ProgramContext,
    TrainingPreferences,
    UserTrainingFeatures,
)
from app.services.training_generation.plan_schema import WEEKDAYS
from app.services.training_generation.prompt_builder import build_training_prompt
from app.services.training_generation.semantic_validator import (
    validate_plan_semantics,
)
from app.services.training_generation.service import (
    generate_training_plan_candidate,
)

# The demo plan's titles, exactly as the device showed them.
DEVICE_A = "Full Body A – Squat, Yatay İtme/Çekme, Kalça Menteşesi"
DEVICE_B = "Full Body B – Tek Bacak, Dikey İtme/Çekme, Glute Odak"
RECOVERY = (
    "Aktif toparlanma: 20-30 dk hafif yürüyüş, kalça ve torasik mobilite "
    "hareketleri"
)


# ── The title rule ───────────────────────────────────────────────────────────


def test_full_body_a_reads_as_the_split_and_its_regions():
    assert member_focus_title(DEVICE_A) == (
        "Tüm Vücut A — Squat, göğüs, sırt ve kalça")


def test_unilateral_lower_body_says_tek_taraflı_never_a_bare_tek_bacak():
    title = member_focus_title(DEVICE_B)
    assert title == "Tüm Vücut B — Tek taraflı bacak, omuz, sırt ve kalça"
    assert "Tek bacak" not in title and "Tek Bacak" not in title


@pytest.mark.parametrize("raw,expected", [
    ("Tüm Vücut A: yatay itme, yatay çekme", "Tüm Vücut A — Göğüs ve sırt"),
    ("Full Body C - Dikey İtme + Dikey Çekme", "Tüm Vücut C — Omuz ve sırt"),
])
def test_push_and_pull_are_named_by_plane(raw, expected):
    assert member_focus_title(raw) == expected


@pytest.mark.parametrize(
    "hip", ["Kalça Menteşesi", "kalça dominant", "Glute Odak", "Kalça Odaklı"])
def test_hinge_and_glute_emphasis_read_as_the_hips(hip):
    assert member_focus_title(f"Full Body A – Squat, {hip}") == (
        "Tüm Vücut A — Squat ve kalça")


@pytest.mark.parametrize("unchanged", [
    # Already in the house style (what the prompt now asks for).
    "Tüm Vücut A — Squat, göğüs ve sırt",
    "Tüm Vücut B — Tek taraflı kuvvet ve üst vücut",
    # Not a full-body note: other splits and free text keep their words.
    "Full Body",
    "Üst vücut itiş: göğüs, omuz ve triceps",
    "Bacak Günü – Squat, Leg Press, Romanian Deadlift",
    RECOVERY,
    # A pattern outside the closed list leaves the whole title alone.
    "Full Body A – Squat, Yatay İtme/Çekme, Farmer Walk",
    # English titles are never turned into Turkish.
    "Full Body A – Squat, Horizontal Push/Pull, Hip Hinge",
    "Full Body A – Squat, Core",
])
def test_anything_not_fully_understood_is_left_as_written(unchanged):
    assert member_focus_title(unchanged) == unchanged


def test_a_long_generated_title_stays_within_the_schema_bound():
    title = member_focus_title(
        "Full Body B – Tek Bacak, Dikey İtme/Çekme, Glute Odak, "
        "İsteğe Bağlı Kondisyon, Core Stabilitesi")
    assert title == (
        "Tüm Vücut B — Tek taraflı bacak, omuz, sırt, kalça, kondisyon ve core")
    from app.services.training_generation.plan_schema import FOCUS_MAX
    assert len(title) <= FOCUS_MAX


# ── The plan rule: only odak, only training days, only Turkish ─────────────


def _plan():
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": DEVICE_A,
             "sure_dk": 45, "tahmini_kalori": 340, "egzersizler": [
                 {"exercise_id": "ex_barbell_front_squat", "isim": "Front Squat",
                  "set": 3, "tekrar": "6-10", "dinlenme": "120 sn",
                  "not": "Kalçadan eğil, sırt nötr"}]},
            {"gun": "Salı", "tip": "dinlenme", "odak": RECOVERY,
             "sure_dk": 30, "tahmini_kalori": 120, "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": DEVICE_B,
             "sure_dk": 45, "tahmini_kalori": 340, "egzersizler": [
                 {"exercise_id": "ex_bulgarian_split_squat",
                  "isim": "Bulgarian Split Squat", "set": 3, "tekrar": "8-10",
                  "dinlenme": "90 sn", "not": "Tek bacak, kontrollü"}]},
        ],
        "haftalik_ozet": {"yogunluk_skoru": 7, "denge_skoru": 8,
                          "uygunluk_skoru": 8},
    }


def test_a_turkish_plan_changes_only_training_day_titles():
    plan = _plan()
    before = copy.deepcopy(plan)
    out = normalize_generated_titles(plan, "tr")

    assert plan == before, "the input plan is not mutated"
    assert [d["odak"] for d in out["program"]] == [
        "Tüm Vücut A — Squat, göğüs, sırt ve kalça",
        RECOVERY,
        "Tüm Vücut B — Tek taraflı bacak, omuz, sırt ve kalça",
    ]
    # Everything else — prescription, notes (even one that says "Tek bacak"),
    # durations, the summary — is exactly what the model wrote.
    for got, was in zip(out["program"], before["program"]):
        assert {k: v for k, v in got.items() if k != "odak"} == {
            k: v for k, v in was.items() if k != "odak"}
    assert out["haftalik_ozet"] == before["haftalik_ozet"]


def test_an_english_plan_is_returned_untouched():
    plan = _plan()
    assert normalize_generated_titles(plan, "en") is plan


# ── The prompt names the title format; the rest of the contract is intact ──


def _prompt(language):
    prefs = TrainingPreferences()
    feats = UserTrainingFeatures(
        age=25, weight=80.0, height=180.0, goal="kas_kutlesi",
        self_reported_level="beginner", current_activity="orta",
        training_history_months=6, weekly_frequency=3, movement_competency={},
        strength_proxy=1.0, consistency_score=0.5, injuries="")
    cls = ClassificationResult(level="Beginner", confidence=0.9, score=30,
                               score_breakdown={}, constraints_applied=[],
                               risk_flags=[], reasoning_summary="")
    ctx = ProgramContext(classified_level="Beginner", weekly_training_days=3,
                         recovery_capacity_factor=1.0, volume_guideline="v",
                         intensity_guideline="i", progression_guideline="p",
                         deload_guideline="d", movement_coverage=["squat"],
                         style_directive="s", risk_flags=[],
                         constraints_applied=[])
    choices = compatible_exercise_choices(ExerciseContext(equipment_context="ev"))
    return build_training_prompt(
        feats, prefs, cls, ctx, language=language, exercise_choices=choices)


def test_turkish_prompt_names_the_title_format_and_shows_a_turkish_title():
    prompt = _prompt("tr")
    rules = prompt.split("PROGRAM RULES", 1)[1].split("JSON FORMAT", 1)[0]
    assert '13. "odak" kullanıcının plan kartında okuduğu BAŞLIKTIR' in rules
    assert "Tüm Vücut A — Squat, göğüs ve sırt" in rules
    for banned in ("kalça menteşesi", "glute odak", '"tek bacak"'):
        assert banned in rules, "the rule names what not to write"
    assert '"Full Body"' in rules, "Full Body is named as not to be written"
    assert '15. tip="dinlenme" günü "odak"' in rules
    # The semantic validator refuses a rest day over 45 minutes and that
    # refusal is never repaired, so the rule states the same ceiling.
    assert "(en fazla 45)" in rules
    # The only JSON example now shows a Turkish title, not "Full Body".
    example = prompt.split("JSON FORMAT", 1)[1]
    assert '"odak":"Tüm Vücut A — Squat, göğüs ve sırt"' in example
    assert '"odak":"Full Body"' not in example


def test_english_prompt_gets_the_english_rules_and_example():
    prompt = _prompt("en")
    assert '13. "odak" is the title a member reads on the plan card' in prompt
    assert '"odak":"Full Body A — Squat, chest and back"' in prompt
    assert "(at most 45)" in prompt
    assert "BAŞLIKTIR" not in prompt


@pytest.mark.parametrize("language", ["tr", "en"])
def test_the_programming_rules_and_choice_set_are_unchanged(language):
    prompt = _prompt(language)
    for rule in (
        "1. Haftanın tam 7 günü için plan yap",
        "4. Aynı ağır pattern iki gün üst üste gelmesin",
        "9. Egzersiz nesnesi tam olarak exercise_id, set, tekrar, dinlenme, not içersin",
        '12. tip="dinlenme" günü HİÇBİR egzersiz içermez',
        "EXERCISE CHOICES (kapalı liste",
        "MOVEMENT COVERAGE REQUIRED",
        "STYLE FEW-SHOT REFERENCE",
    ):
        assert rule in prompt, rule


# ── End to end: what is persisted ───────────────────────────────────────────


def _provider_document():
    def ex(exercise_id):
        return {"exercise_id": exercise_id, "set": 3, "tekrar": "8-12",
                "dinlenme": "90 sn", "not": "Kontrollü tempo"}
    program = []
    for index, weekday in enumerate(WEEKDAYS):
        training = index in (0, 2, 4)
        program.append({
            "gun": weekday,
            "tip": "antrenman" if training else "dinlenme",
            "odak": (DEVICE_A if index != 2 else DEVICE_B) if training else RECOVERY,
            "sure_dk": 45 if training else 30,
            "tahmini_kalori": 300 if training else 100,
            "egzersizler": (
                [ex("ex_goblet_squat"), ex("ex_barbell_row"), ex("ex_push_up")]
                if training else []),
        })
    return {"program": program, "haftalik_ozet": {
        "yogunluk_skoru": 7, "denge_skoru": 8, "uygunluk_skoru": 9}}


def _preferences():
    return TrainingPreferences(
        gun_sayisi=3, ekipman="spor_salonu", odak="tum_vucut", sure=45,
        kardiyo_tipi="yok", kardiyo_gun=0, kardiyo_sure=20,
        kardiyo_yogunluk="orta", antrenman_tarzi="genel", odak_hedef="genel",
        injuries="")


@pytest.mark.parametrize("language,expected_a", [
    ("tr", "Tüm Vücut A — Squat, göğüs, sırt ve kalça"),
    ("en", DEVICE_A),
])
def test_the_persisted_document_carries_the_title_and_the_same_prescription(
        make_user, language, expected_a):
    from app.extensions import db
    from app.models import UserSession
    user = make_user(f"r4-naming-{language}")
    session = UserSession(user_id=user.id, goal="kas kazanma",
                          fitness_level="intermediate",
                          current_activity="active", tdee=2600)
    db.session.add(session)
    db.session.commit()

    raw = _provider_document()
    candidate = generate_training_plan_candidate(
        user, session, _preferences(), language=language,
        chat_fn=lambda **_: json.dumps(raw, ensure_ascii=False))

    days = candidate.document["program"]
    assert days[0]["odak"] == expected_a
    assert days[1]["odak"] == RECOVERY
    assert days[1]["sure_dk"] == 30
    for got, sent in zip(days, raw["program"]):
        assert [(e["exercise_id"], e["set"], e["tekrar"], e["dinlenme"],
                 e["not"]) for e in got["egzersizler"]] == [
            (e["exercise_id"], e["set"], e["tekrar"], e["dinlenme"], e["not"])
            for e in sent["egzersizler"]]
        assert (got["gun"], got["tip"], got["sure_dk"],
                got["tahmini_kalori"]) == (
            sent["gun"], sent["tip"], sent["sure_dk"], sent["tahmini_kalori"])


def test_the_rest_day_ceiling_in_the_prompt_is_the_validator_s():
    """Rule 15's "(en fazla 45)" is exactly the semantic validator's bound."""
    from app.services.training_generation.output_errors import (
        SemanticInvalidError,
    )
    plan = _provider_document()
    rest = plan["program"][1]
    rest["sure_dk"] = 45
    validate_plan_semantics(copy.deepcopy(plan), None)
    rest["sure_dk"] = 46
    with pytest.raises(SemanticInvalidError):
        validate_plan_semantics(copy.deepcopy(plan), None)
