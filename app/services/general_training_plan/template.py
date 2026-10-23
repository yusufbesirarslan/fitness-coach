"""The reviewed, deterministic general training template (rules version 1).

Pure data and one pure builder: no database, Flask, clock, randomness,
profile, history or provider. The same ``(days, equipment, language)`` always
yields the same document, byte for byte once serialized with sorted keys.

What this template is: a conservative, non-personalized full-body A/B week
built only from active catalog identities that the requested equipment
context allows. What it is not: adaptive, injury-aware, goal-tuned or derived
from anything the user has done. Nothing here reads a user, so it cannot
claim to be about one.

Deliberate omissions, each a truthfulness choice rather than a gap:

* no ``haftalik_ozet`` — those are AI quality scores; a template has none,
  and the persisted ``TrainingPlan.score`` stays NULL;
* ``tahmini_kalori`` is 0 on every day — an energy estimate needs a body,
  and this path never reads one (0 is the schema's "not estimated" floor);
* empty exercise notes — coaching or safety copy needs approved wording.
"""
from __future__ import annotations

from app.services.exercise_catalog import load_exercise_catalog
from app.services.training_generation.plan_schema import WEEKDAYS


TEMPLATE_ID = "general_full_body_v1"
SUPPORTED_DAYS = frozenset({3, 4})
SUPPORTED_EQUIPMENT = frozenset({"ev", "minimal", "spor_salonu"})
SESSION_MINUTES = 45

# Weekday slot (index into WEEKDAYS) -> session variant. Non-adjacent days
# for three sessions; two A/B pairs with a mid-week rest for four.
_SCHEDULE = {
    3: {0: "A", 2: "B", 4: "A"},
    4: {0: "A", 1: "B", 3: "A", 4: "B"},
}

_COMPOUND_REST = "90 sn"
_CORE_REST = "60 sn"

# (exercise_id, sets, reps, rest). Every ID is an active catalog entry whose
# equipment is inside the context's allowance (proven per context by tests
# and re-validated by the canonical save boundary on every request).
_SESSIONS = {
    "ev": {
        "A": (
            ("ex_bodyweight_squat", 3, "12-15", _COMPOUND_REST),
            ("ex_push_up", 3, "8-12", _COMPOUND_REST),
            ("ex_glute_bridge", 3, "12-15", _COMPOUND_REST),
            ("ex_inverted_row", 3, "8-12", _COMPOUND_REST),
            ("ex_plank", 3, "30 sn", _CORE_REST),
        ),
        "B": (
            ("ex_walking_lunge", 3, "10-12", _COMPOUND_REST),
            ("ex_pike_push_up", 3, "6-10", _COMPOUND_REST),
            ("ex_glute_bridge", 3, "12-15", _COMPOUND_REST),
            ("ex_inverted_row", 3, "8-12", _COMPOUND_REST),
            ("ex_dead_bug", 3, "10-12", _CORE_REST),
        ),
    },
    "minimal": {
        "A": (
            ("ex_goblet_squat", 3, "10-12", _COMPOUND_REST),
            ("ex_dumbbell_floor_press", 3, "8-12", _COMPOUND_REST),
            ("ex_dumbbell_row", 3, "10-12", _COMPOUND_REST),
            ("ex_dumbbell_romanian_deadlift", 3, "10-12", _COMPOUND_REST),
            ("ex_plank", 3, "30 sn", _CORE_REST),
        ),
        "B": (
            ("ex_dumbbell_split_squat", 3, "10-12", _COMPOUND_REST),
            ("ex_dumbbell_shoulder_press", 3, "8-12", _COMPOUND_REST),
            ("ex_band_pulldown", 3, "10-12", _COMPOUND_REST),
            ("ex_glute_bridge", 3, "12-15", _COMPOUND_REST),
            ("ex_dead_bug", 3, "10-12", _CORE_REST),
        ),
    },
    "spor_salonu": {
        "A": (
            ("ex_leg_press", 3, "10-12", _COMPOUND_REST),
            ("ex_incline_dumbbell_press", 3, "8-12", _COMPOUND_REST),
            ("ex_seated_cable_row", 3, "10-12", _COMPOUND_REST),
            ("ex_dumbbell_romanian_deadlift", 3, "10-12", _COMPOUND_REST),
            ("ex_plank", 3, "30 sn", _CORE_REST),
        ),
        "B": (
            ("ex_goblet_squat", 3, "10-12", _COMPOUND_REST),
            ("ex_dumbbell_shoulder_press", 3, "8-12", _COMPOUND_REST),
            ("ex_lat_pulldown", 3, "10-12", _COMPOUND_REST),
            ("ex_lying_leg_curl", 3, "10-12", _COMPOUND_REST),
            ("ex_dead_bug", 3, "10-12", _CORE_REST),
        ),
    },
}

# Day labels only; the catalog names every exercise.
_LABELS = {
    "tr": {"A": "Genel Tüm Vücut A", "B": "Genel Tüm Vücut B", "rest": "Dinlenme"},
    "en": {"A": "General Full Body A", "B": "General Full Body B", "rest": "Rest"},
}


def label_language(language) -> str:
    """The label set for an account language; Turkish is the product default."""
    return language if language in _LABELS else "tr"


def build_program(days: int, equipment: str, language) -> list[dict]:
    """The canonical 7-day program for one supported parameter pair.

    Each exercise is the save boundary's ID/name pair: the ``isim`` is the
    catalog's own canonical name, and ``validate_plan_for_save`` re-resolves
    the ID (active, equipment-compatible) before anything is persisted.
    """
    if days not in SUPPORTED_DAYS or equipment not in SUPPORTED_EQUIPMENT:
        raise ValueError("unsupported general template parameters")
    labels = _LABELS[label_language(language)]
    names = load_exercise_catalog().by_id
    schedule = _SCHEDULE[days]
    program = []
    for slot, weekday in enumerate(WEEKDAYS):
        variant = schedule.get(slot)
        if variant is None:
            program.append({
                "gun": weekday, "tip": "dinlenme", "odak": labels["rest"],
                "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": [],
            })
            continue
        program.append({
            "gun": weekday,
            "tip": "antrenman",
            "odak": labels[variant],
            "sure_dk": SESSION_MINUTES,
            "tahmini_kalori": 0,
            "egzersizler": [
                {"exercise_id": exercise_id,
                 "isim": names[exercise_id].canonical_name,
                 "set": sets, "tekrar": reps, "dinlenme": rest, "not": ""}
                for exercise_id, sets, reps, rest in _SESSIONS[equipment][variant]
            ],
        })
    return program
