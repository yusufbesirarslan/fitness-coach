"""The reviewed, deterministic general training templates.

``general_full_body_v1`` (rules version 1) is the strength-only week.
``general_full_body_cardio_v1`` is that SAME strength week, unchanged, with
dedicated cardio days placed on fixed rest slots; it never alters a strength
day and always leaves Sunday as a full rest day.

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

from app.services import general_plan_identity as identity
from app.services.exercise_catalog import load_exercise_catalog
from app.services.training_generation.plan_schema import CARDIO_TIP, WEEKDAYS


TEMPLATE_ID = identity.TEMPLATE_V1
CARDIO_TEMPLATE_ID = identity.TEMPLATE_CARDIO_V1
SUPPORTED_DAYS = identity.STRENGTH_DAYS
SUPPORTED_EQUIPMENT = identity.EQUIPMENT
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


# Cardio days fill rest slots in this order: Saturday first, then the
# mid-week gaps. Sunday is never listed, so every week keeps one full rest
# day; the slot lists are long enough for ``cardio_day_choices`` exactly.
_CARDIO_SLOTS = {
    3: (5, 1, 3),
    4: (5, 2),
}

# One catalog cardio entry per type. Mixed rotates through activities that
# need no pool, so it never prescribes swimming to someone without one.
_CARDIO_EXERCISES = {
    "kosu": ("ex_outdoor_run",),
    "bisiklet": ("ex_stationary_cycling",),
    "yuzme": ("ex_swimming",),
    "ip_atlama": ("ex_jump_rope",),
    "yuruyus": ("ex_brisk_walk",),
    "karisik": ("ex_outdoor_run", "ex_stationary_cycling", "ex_brisk_walk"),
}

# Day labels only: the activity and the intensity the user chose, echoed.
_CARDIO_LABELS = {
    "tr": {
        "cardio": "Kardiyo",
        "ex_outdoor_run": "Koşu",
        "ex_stationary_cycling": "Bisiklet",
        "ex_swimming": "Yüzme",
        "ex_jump_rope": "İp atlama",
        "ex_brisk_walk": "Tempolu yürüyüş",
        "dusuk": "Düşük yoğunluk",
        "orta": "Orta yoğunluk",
        "yuksek": "Yüksek yoğunluk",
        "karisik": "Karışık yoğunluk",
    },
    "en": {
        "cardio": "Cardio",
        "ex_outdoor_run": "Running",
        "ex_stationary_cycling": "Cycling",
        "ex_swimming": "Swimming",
        "ex_jump_rope": "Jump rope",
        "ex_brisk_walk": "Brisk walking",
        "dusuk": "Low intensity",
        "orta": "Moderate intensity",
        "yuksek": "High intensity",
        "karisik": "Mixed intensity",
    },
}


def build_cardio_program(
    days: int,
    equipment: str,
    cardio_type: str,
    cardio_days: int,
    cardio_minutes: int,
    intensity: str,
    language,
) -> list[dict]:
    """The v1 strength week plus ``cardio_days`` dedicated cardio days.

    Every strength day is exactly ``build_program``'s; only rest slots become
    cardio days. Each cardio day holds one catalog cardio entry for the
    requested minutes: one round, no rest, no estimated calories, no notes.
    """
    if (cardio_type not in identity.CARDIO_TYPES
            or cardio_days not in identity.cardio_day_choices(days)
            or cardio_minutes not in identity.CARDIO_DURATIONS
            or intensity not in identity.CARDIO_INTENSITIES):
        raise ValueError("unsupported general cardio parameters")
    program = build_program(days, equipment, language)
    labels = _CARDIO_LABELS[label_language(language)]
    names = load_exercise_catalog().by_id
    rotation = _CARDIO_EXERCISES[cardio_type]
    for index, slot in enumerate(sorted(_CARDIO_SLOTS[days][:cardio_days])):
        exercise_id = rotation[index % len(rotation)]
        program[slot] = {
            "gun": WEEKDAYS[slot],
            "tip": CARDIO_TIP,
            "odak": " · ".join((
                labels["cardio"], labels[exercise_id], labels[intensity])),
            "sure_dk": cardio_minutes,
            "tahmini_kalori": 0,
            "egzersizler": [{
                "exercise_id": exercise_id,
                "isim": names[exercise_id].canonical_name,
                "set": 1,
                "tekrar": f"{cardio_minutes} dk",
                "dinlenme": "0 sn",
                "not": "",
            }],
        }
    return program
