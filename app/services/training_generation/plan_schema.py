"""Canonical generated-plan shape shared by extract, validate, generate, and save.

This is the AxisAI plan schema after PR2. Extra keys are rejected. Derived
weekly totals may be filled by AxisAI after a plan has otherwise validated.
"""

WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]
VALID_TIPS = frozenset({"antrenman", "dinlenme", "kardiyo"})
# The only day type a cardio-movement catalog entry may be placed on.
CARDIO_TIP = "kardiyo"

PLAN_KEYS = frozenset({"program", "haftalik_ozet"})
DAY_KEYS = frozenset({
    "gun", "tip", "odak", "sure_dk", "tahmini_kalori", "egzersizler",
})
EXERCISE_KEYS = frozenset({"isim", "set", "tekrar", "dinlenme", "not"})
# Sprint 11 PR4 Task 3: the canonical exercise-identity key canonicalization
# (exercise_resolution.py) writes onto an accepted plan. Not in EXERCISE_KEYS:
# a name-only client plan carries none. Task 4 lets save opt in
# (allow_exercise_id=True) — there the key is optional INPUT that catalog
# resolution re-checks, never an identity the caller gets to assert.
EXERCISE_ID_KEY = "exercise_id"
EXERCISE_KEYS_WITH_ID = EXERCISE_KEYS | frozenset({EXERCISE_ID_KEY})
# PR B: the PROVIDER's exercise shape. The provider chooses identity from the
# request's closed compatible-choice set and prescribes the rest; it does not
# author a display name. ``isim`` is therefore an unknown key here, not an
# ignored one — the catalog writes it after the ID has been validated
# (exercise_resolution.canonicalize_generated_exercises). EXERCISE_KEYS and
# EXERCISE_KEYS_WITH_ID above remain the CLIENT save shapes.
PROVIDER_EXERCISE_KEYS = frozenset({
    EXERCISE_ID_KEY, "set", "tekrar", "dinlenme", "not",
})
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

# Shared bound for native workout projection and manual insertion.
NATIVE_MAX_EXERCISES_PER_DAY = 32

# G4 (#530): an independent cardio SESSION inside a strength day. Optional,
# admitted only where a caller opted in (``allow_sessions``), and only on a
# ``tip="antrenman"`` day. It holds cardio movements only, placed by the same
# rule as a dedicated ``kardiyo`` day (``CARDIO_SESSION_PLACEMENT``), and it is
# a separate list: the strength ``egzersizler`` never holds cardio (P3A).
# Its duration is derived, never stored: every entry is one round of
# ``"<minutes> dk"``. Pre-G4 code rejects the key (DAY_KEYS is closed), so a
# release that persists it is forward-only.
CARDIO_SESSION_KEY = "kardiyo_seansi"
CARDIO_SESSION_KEYS = frozenset({"egzersizler"})
CARDIO_SESSION_PLACEMENT = {"tip": CARDIO_TIP}
CARDIO_SESSION_MINUTES_MAX = 600
SESSION_TIP = "antrenman"


def cardio_session_of(day):
    """The day's cardio session block, or ``None`` (pure, shape-tolerant)."""
    if not isinstance(day, dict):
        return None
    session = day.get(CARDIO_SESSION_KEY)
    return session if isinstance(session, dict) else None


def day_exercise_lists(day):
    """Every exercise list on ``day``: the day's own list, then its session's.

    The one walker for code that must see EVERY exercise a plan holds
    (identity ownership, injury and youth screens). Shape-tolerant: a
    malformed list contributes nothing here; validators refuse it elsewhere.
    """
    lists = []
    if isinstance(day, dict):
        exercises = day.get("egzersizler")
        if isinstance(exercises, list):
            lists.append(exercises)
        session = cardio_session_of(day)
        if session is not None and isinstance(session.get("egzersizler"), list):
            lists.append(session["egzersizler"])
    return lists


def session_minutes(exercises) -> int:
    """Total minutes of a validated cardio session's ``"<n> dk"`` entries."""
    return sum(int(entry["tekrar"].split(" ", 1)[0]) for entry in exercises)
