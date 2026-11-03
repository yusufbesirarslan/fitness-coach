"""LP20-BACKEND-TR-WORKOUT-INSTRUCTIONS-04 — plain Turkish exercise guidance.

Prompt-level qualification only. These tests prove what the generation
prompt ASKS for (plain, everyday Turkish; RPE kept; safety cues kept; no
invented instructions) and that nothing outside the Turkish wording moved:
the English prompt, exercise IDs, the injury directive and the response
contract. They do not and cannot prove that every provider answer is
jargon-free — that needs real output sampling under the D1/provider
qualification process.
"""
import json
import re

import pytest

from app.services import injury_constraints
from app.services.exercise_catalog import ExerciseContext
from app.services.training_generation.classifier_service import classify_user
from app.services.training_generation.exercise_choices import (
    compatible_exercise_choices,
)
from app.services.training_generation.exercise_resolution import (
    canonicalize_generated_exercises,
)
from app.services.training_generation.models import (
    PerformanceHistory,
    TrainingPreferences,
    UserTrainingFeatures,
)
from app.services.training_generation.plan_schema import NOTE_MAX
from app.services.training_generation.program_generator import build_program_context
from app.services.training_generation.prompt_builder import (
    build_system_prompt,
    build_training_prompt,
)
from app.services.training_generation.response_validator import (
    WEEKDAYS,
    annotate_injuries,
    validate_generated_plan,
)

# Representative synthetic exercises named by the task.
SYNTHETIC_IDS = (
    "ex_thoracic_mobility",
    "ex_scapular_pull_up",
    "ex_hip_hinge_drill",
    "ex_lat_pulldown",
    "ex_push_up",
)
SAFETY_INJURIES = "kifoz, bel fıtığı"
LEGACY_NOTE = "RPE 7, kontrollü tempo"


def _features(injuries="Hiçbiri"):
    return UserTrainingFeatures(
        age=30, weight=82.0, height=180.0, goal="kas kazanma",
        self_reported_level="intermediate", current_activity="active",
        training_history_months=18, weekly_frequency=4,
        movement_competency={k: 2 for k in (
            "squat", "hinge", "horizontal_push", "vertical_push",
            "horizontal_pull", "vertical_pull", "core")},
        strength_proxy=1.0, consistency_score=0.75, injuries=injuries,
        performance_history=PerformanceHistory(
            weekly_training_sessions=[4, 4, 3, 4],
            volume_trend=[5000, 5200, 5400, 5600], adherence_score=0.75,
            fatigue_trend=2.0, sleep_quality=4.0, stable_score_weeks=3),
    )


def _prompt(language, injuries="Hiçbiri", ekipman="spor_salonu"):
    features = _features(injuries)
    prefs = TrainingPreferences(gun_sayisi=3, sure=45, injuries=injuries,
                                ekipman=ekipman)
    classification = classify_user(features)
    context = build_program_context(features, prefs, classification)
    choices = compatible_exercise_choices(ExerciseContext(equipment_context=ekipman))
    return build_training_prompt(features, prefs, classification, context,
                                 language, exercise_choices=choices), choices


def _lang_rule(prompt):
    return next(line for line in prompt.splitlines() if "İçerik dili:" in line)


def _json_example(prompt):
    return json.loads(prompt.split("JSON FORMAT", 1)[1].strip())


def _example_note(prompt):
    return _json_example(prompt)["program"][0]["egzersizler"][0]["not"]


# ── Turkish style request ────────────────────────────────────────────────

@pytest.mark.parametrize("ekipman", ["spor_salonu", "ev", "minimal"])
@pytest.mark.parametrize("injuries", ["Hiçbiri", SAFETY_INJURIES])
def test_turkish_prompt_asks_for_plain_everyday_gym_language(ekipman, injuries):
    prompt, _ = _prompt("tr", injuries, ekipman)
    rule = _lang_rule(prompt)
    # Plain-language coach voice, about what the user actually does.
    assert "sade, günlük Türkçe" in rule
    assert "ne yapacağını söyle" in rule
    # Jargon named, with familiar replacements and a movement-specific example.
    for term in ("torasik", "skapular", "lomber", "fleksiyon"):
        assert term in rule
    assert "üst sırt" in rule and "kürek kemikleri" in rule
    assert "Üst sırtını yavaşça döndür, belini sabit tut" in rule
    assert "harekete uygunsa" in rule  # an example, not a universal cue
    # Established gym names are kept, not force-translated.
    assert "yerleşik adları çevirme" in rule
    # Intensity semantics: the RPE value itself must not change.
    assert "RPE değerini değiştirme" in rule
    assert "RPE 7 = setin sonunda yaklaşık 3 tekrar daha yapabilecek kadar" in rule
    # Concise, but never at the cost of technique/safety, and nothing invented.
    assert "iki kısa cümle" in rule
    assert "güvenlik/sakatlık ipucunu atlama" in rule
    assert "harekette olmayan talimat uydurma" in rule
    # The pre-existing ID carve-out is intact in the same rule line.
    assert "exercise_id" in rule and "ASLA çevrilmez" in rule


def test_style_rule_appears_once_and_only_in_the_user_prompt():
    prompt, _ = _prompt("tr", SAFETY_INJURIES)
    assert prompt.count("Üslup:") == 1
    assert "Üslup:" not in build_system_prompt("tr")


def test_turkish_example_note_is_plain_and_keeps_rpe_7():
    prompt, _ = _prompt("tr")
    note = _example_note(prompt)
    assert note != LEGACY_NOTE
    assert LEGACY_NOTE not in prompt
    assert note.startswith("RPE 7:")          # same prescribed intensity
    assert "3 tekrar daha" in note            # RPE 7 ≈ 3 reps in reserve
    assert len(note) <= NOTE_MAX
    # Exercise-agnostic: the example ID may be any catalog choice, so the
    # example must not carry a movement-specific cue such as squat depth.
    for specific in ("diz", "squat", "iniş", "kalça"):
        assert specific not in note.lower()


# ── English unchanged ────────────────────────────────────────────────────

@pytest.mark.parametrize("injuries", ["Hiçbiri", SAFETY_INJURIES])
def test_english_prompt_keeps_its_rule_and_example_note(injuries):
    prompt, _ = _prompt("en", injuries)
    assert _lang_rule(prompt) == (
        "6. İçerik dili: ENGLISH; ama gun ve tip kanonik Türkçe kalacak; "
        "exercise_id opak kimliktir, çevrilmez.")
    assert _example_note(prompt) == LEGACY_NOTE
    assert "Üslup:" not in prompt


def test_turkish_and_english_prompts_differ_only_in_rule_6_and_example_note():
    tr, _ = _prompt("tr", SAFETY_INJURIES)
    en, _ = _prompt("en", SAFETY_INJURIES)
    tr_lines, en_lines = tr.splitlines(), en.splitlines()
    assert len(tr_lines) == len(en_lines)
    differing = [i for i, (a, b) in enumerate(zip(tr_lines, en_lines)) if a != b]
    json_line = tr_lines.index("JSON FORMAT") + 1
    assert [tr_lines[i].split(".", 1)[0] for i in differing[:-1]] == ["6"]
    assert differing[-1] == json_line
    assert tr_lines[json_line].replace(_example_note(tr), LEGACY_NOTE) == en_lines[json_line]


# ── Exercise identity and response contract ──────────────────────────────

def test_exercise_ids_and_choice_list_are_untouched():
    prompt, choices = _prompt("tr")
    lines = [l for l in prompt.splitlines() if l.startswith("- ex_")]
    assert lines == [f"- {c.exercise_id} | {c.canonical_name}" for c in choices.choices]
    assert set(SYNTHETIC_IDS) <= choices.exercise_ids
    # The style rule names no ID, so it cannot steer or rename one.
    assert not re.search(r"\bex_[a-z0-9_]+", _lang_rule(prompt))
    example = _json_example(prompt)["program"][0]["egzersizler"][0]
    assert set(example) == {"exercise_id", "set", "tekrar", "dinlenme", "not"}
    assert example["exercise_id"] in choices.exercise_ids


def test_json_example_shape_is_identical_across_languages():
    def shape(value):
        if isinstance(value, dict):
            return {k: shape(v) for k, v in value.items()}
        if isinstance(value, list):
            return [shape(v) for v in value]
        return type(value).__name__
    assert shape(_json_example(_prompt("tr")[0])) == shape(_json_example(_prompt("en")[0]))


# Plain-Turkish notes of the kind the prompt now asks for, one per synthetic
# exercise, plus a safety-relevant one on a lift the injury overlay flags.
PLAIN_NOTES = {
    "ex_thoracic_mobility": "Üst sırtını yavaşça döndür, belini sabit tut.",
    "ex_scapular_pull_up": "Kollarını bükmeden kürek kemiklerini aşağı çek, sonra yavaşça bırak.",
    "ex_hip_hinge_drill": "Kalçanı geriye it, sırtın düz kalsın; dizlerini hafif bük.",
    "ex_lat_pulldown": "Barı göğsüne doğru çek, gövdeni geriye savurma.",
    "ex_push_up": "RPE 7: setin sonunda yaklaşık 3 tekrar daha yapabilecek kadar zor; vücudun düz kalsın.",
    "ex_barbell_row": "Belin ağrırsa göğüs destekli row ile değiştir; sırtını yuvarlama.",
}


def _plan_with_notes():
    exercises = [
        {"exercise_id": exercise_id, "set": 3, "tekrar": "8-12",
         "dinlenme": "90 sn", "not": note}
        for exercise_id, note in PLAIN_NOTES.items()
    ]
    return {
        "program": [
            {"gun": day,
             "tip": "antrenman" if i == 0 else "dinlenme",
             "odak": "Tüm Vücut" if i == 0 else "Hafif yürüyüş ve esneme",
             "sure_dk": 45 if i == 0 else 0,
             "tahmini_kalori": 300 if i == 0 else 0,
             "egzersizler": exercises if i == 0 else []}
            for i, day in enumerate(WEEKDAYS)
        ],
        "haftalik_ozet": {"yogunluk_skoru": 7, "denge_skoru": 7, "uygunluk_skoru": 7},
    }


def test_plain_turkish_notes_pass_the_unchanged_response_contract():
    prefs = TrainingPreferences(gun_sayisi=1, sure=45)
    validated, _ = validate_generated_plan(_plan_with_notes(), prefs)
    canonical = canonicalize_generated_exercises(
        validated, compatible_exercise_choices(ExerciseContext(equipment_context="spor_salonu")))
    day = canonical["program"][0]["egzersizler"]
    assert [ex["exercise_id"] for ex in day] == list(PLAIN_NOTES)
    assert [ex["not"] for ex in day] == list(PLAIN_NOTES.values())


# ── Injury safety ────────────────────────────────────────────────────────

@pytest.mark.parametrize("language", ["tr", "en"])
def test_injury_directive_is_embedded_verbatim(language):
    prompt, _ = _prompt(language, SAFETY_INJURIES)
    directive = injury_constraints.build_injury_directive(SAFETY_INJURIES)
    assert directive and directive.strip() in prompt
    # Banned movements and the "explain the adaptation in not" instruction
    # still reach the model unchanged.
    assert "YASAK: deadlift" in prompt
    assert "'not' alanına neden güvenli olduğunu/uyarlamayı yaz" in prompt


def test_injury_overlay_still_flags_a_modified_exercise_with_a_plain_note():
    prefs = TrainingPreferences(gun_sayisi=1, sure=45, injuries=SAFETY_INJURIES)
    validated, _ = validate_generated_plan(_plan_with_notes(), prefs)
    canonical = canonicalize_generated_exercises(
        validated, compatible_exercise_choices(ExerciseContext(equipment_context="spor_salonu")))
    warnings = annotate_injuries(canonical, SAFETY_INJURIES)
    flagged = {w["egzersiz"] for w in warnings}
    assert "Barbell Row" in flagged
    row = next(ex for ex in canonical["program"][0]["egzersizler"]
               if ex["exercise_id"] == "ex_barbell_row")
    assert row["not"].startswith("⚠️ SAKATLIK RİSKİ")
    assert row["not"].endswith(PLAIN_NOTES["ex_barbell_row"])
    assert len(row["not"]) <= NOTE_MAX
