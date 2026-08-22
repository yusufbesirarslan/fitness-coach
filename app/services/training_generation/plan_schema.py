"""Canonical generated-plan shape shared by extract, validate, generate, and save.

This is the AxisAI plan schema after PR2. Extra keys are rejected. Derived
weekly totals may be filled by AxisAI after a plan has otherwise validated.
"""

WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]
VALID_TIPS = frozenset({"antrenman", "dinlenme", "kardiyo"})

PLAN_KEYS = frozenset({"program", "haftalik_ozet"})
DAY_KEYS = frozenset({
    "gun", "tip", "odak", "sure_dk", "tahmini_kalori", "egzersizler",
})
EXERCISE_KEYS = frozenset({"isim", "set", "tekrar", "dinlenme", "not"})
# Sprint 11 PR4 Task 3: the canonical exercise-identity key generation-time
# canonicalization (exercise_resolution.py) writes onto an accepted plan.
# Deliberately NOT in EXERCISE_KEYS: generation must keep rejecting a
# provider-authored ID. Task 4 lets save opt in (allow_exercise_id=True) —
# there the key is optional INPUT that catalog resolution re-checks, never
# an identity the caller gets to assert.
EXERCISE_ID_KEY = "exercise_id"
EXERCISE_KEYS_WITH_ID = EXERCISE_KEYS | frozenset({EXERCISE_ID_KEY})
# Bound only. The real gate is ID_PATTERN + catalog membership; structure
# just refuses to carry an unbounded string that far.
EXERCISE_ID_MAX = 64
OZET_REQUIRED_KEYS = frozenset({
    "yogunluk_skoru", "denge_skoru", "uygunluk_skoru",
})
OZET_OPTIONAL_KEYS = frozenset({
    "toplam_antrenman_gun", "toplam_tahmini_kalori",
})
OZET_KEYS = OZET_REQUIRED_KEYS | OZET_OPTIONAL_KEYS

NAME_MAX = 120
REPS_MAX = 40
REST_MAX = 40
NOTE_MAX = 240
FOCUS_MAX = 120
SET_MIN, SET_MAX = 1, 100
DURATION_MIN, DURATION_MAX = 0, 1440
CALORIE_MIN, CALORIE_MAX = 0, 900
SCORE_MIN, SCORE_MAX = 1, 10

# Style-specific minimum usable training-session size. Enforced without a
# catalog: a one-lift "bodybuilding" week is a generic mislabeled plan.
MIN_TRAINING_EXERCISES = {
    "general_fitness": 1,
    "bodybuilding": 4,
    "powerlifting": 3,
    "calisthenics": 3,
    "functional": 3,
}

MAX_PROVIDER_COMPLETIONS = 2
PRIMARY_MAX_TOKENS = 4000
REPAIR_MAX_TOKENS = 7000
