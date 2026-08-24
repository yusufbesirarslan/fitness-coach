"""Canonical prescription bounds, reused rather than redefined (brief §12).

``plan_schema`` is the repository's authority on what a legal exercise looks
like. The generator's ``response_validator`` cannot be called directly here —
it validates a *whole generated plan* against ``TrainingPreferences`` (7 days,
a required training-day count, injury screening), none of which a targeted
mutation should re-run. This module reuses the schema bounds, and
tests/test_plan_mutation.py pins the two together so they cannot drift.

Both generate and mutation **reject** out-of-range values. Generate no longer
clamps LLM output; a caller asking for 999 sets has made an error, and silently
storing 100 would tell them their request succeeded when it did not.

Sprint 11 PR4 adds one more reused authority: ``validate_exercise_context``
reads the verified exercise context the save boundary persisted INSIDE the plan
document, using the same closed equipment/cardio/style vocabularies the signed
context token is checked against. It decides only whether a stored plan is
canonical; what an exercise IS stays the catalog's answer.
"""
from app.services.exercise_catalog import ExerciseContext
from app.services.training_generation.exercise_context_token import (
    CARDIO_TYPES,
    EQUIPMENT_CONTEXTS,
    STYLES,
)
from app.services.training_generation.plan_schema import (
    NAME_MAX,
    REPS_MAX,
    SET_MAX,
    SET_MIN,
    VALID_TIPS,
    WEEKDAYS,
)


#: Sets bounds — identical to ``plan_schema.SET_MIN`` / ``SET_MAX``.
MIN_SETS = SET_MIN
MAX_SETS = SET_MAX

#: Column-backed length ceilings, from the same schema.
MAX_EXERCISE_NAME_CHARS = NAME_MAX
MAX_REPS_CHARS = REPS_MAX

#: The canonical rest marker. A rest day holding exercises is not a valid plan.
REST_TIP = "dinlenme"

#: Non-rest day kinds, derived from the generator's vocabulary — never a second
#: hardcoded copy.
WORKOUT_TIPS = frozenset(VALID_TIPS) - {REST_TIP}

#: The key the Task 4 save boundary writes the VERIFIED exercise context under,
#: inside the plan document itself. Its presence is what makes a stored plan
#: canonical; its absence is what makes one legacy.
FIELD_EXERCISE_CONTEXT = "exercise_context"

#: Exactly the keys that boundary writes. Checked as a set, not as a subset: a
#: block carrying an unexpected key was not written by the save path, and a
#: mutation is not the place to start guessing which half of it to believe.
EXERCISE_CONTEXT_KEYS = frozenset({
    "equipment_context", "cardio_type", "style", "catalog_version"})

__all__ = [
    "MAX_EXERCISE_NAME_CHARS",
    "MAX_REPS_CHARS",
    "MAX_SETS",
    "MIN_SETS",
    "REST_TIP",
    "VALID_TIPS",
    "WEEKDAYS",
    "EXERCISE_CONTEXT_KEYS",
    "FIELD_EXERCISE_CONTEXT",
    "WORKOUT_TIPS",
    "normalize_exercise_name",
    "validate_exercise_context",
    "validate_reps",
    "validate_sets",
]


def validate_exercise_context(raw):
    """Turn a stored ``exercise_context`` block into a usable ``ExerciseContext``.

    Fails closed. A canonical plan whose context cannot be read is NOT allowed
    to degrade into legacy name-only matching: silent degradation is a
    downgrade attack — write one unreadable byte into the context block and the
    catalog stops being the authority over that plan.

    The three vocabularies are imported from the signed-token module rather
    than restated: equipment and cardio ultimately come from the catalog and
    style from the preference contract, and a second copy here would be a
    second, drifting opinion about what a legal context is.

    ``catalog_version`` is required to be a real positive integer but is NOT
    required to equal the loaded catalog's version. Provenance is what it
    records; authority is settled by re-resolving every reference against the
    LIVE catalog on every mutation, which is what actually stops a retired
    entry from being written. Demanding equality would instead make every
    already-stored plan unmutable the moment the catalog version is bumped.
    """
    from .errors import InvalidMutation

    if not isinstance(raw, dict) or set(raw) != EXERCISE_CONTEXT_KEYS:
        raise InvalidMutation("stored exercise context is not canonical")
    equipment_context = raw["equipment_context"]
    cardio_type = raw["cardio_type"]
    style = raw["style"]
    catalog_version = raw["catalog_version"]
    if not (isinstance(equipment_context, str)
            and isinstance(cardio_type, str)
            and isinstance(style, str)):
        raise InvalidMutation("stored exercise context is not canonical")
    # ``type(...) is not int`` rather than isinstance: bool is an int subclass
    # and ``True`` must never pass as catalog version 1.
    if type(catalog_version) is not int or catalog_version < 1:
        raise InvalidMutation("stored exercise context is not canonical")
    if (equipment_context not in EQUIPMENT_CONTEXTS
            or cardio_type not in CARDIO_TYPES
            or style not in STYLES):
        raise InvalidMutation("stored exercise context is not canonical")
    return ExerciseContext(
        equipment_context=equipment_context,
        cardio_type=cardio_type,
        style=style,
        catalog_version=catalog_version,
    )


def normalize_exercise_name(value):
    """Return a trimmed exercise name, or ``None`` when it is not usable.

    Mirrors the generator's ``str(...).strip()[:120]`` normalization, except that
    an over-long name is refused rather than truncated — a silently shortened
    name is a different exercise.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > MAX_EXERCISE_NAME_CHARS:
        return None
    return name


def validate_sets(value):
    """Return ``value`` as a legal set count, or raise ``InvalidPrescription``."""
    from .errors import InvalidPrescription

    # bool is an int subclass; True would otherwise pass as 1 set.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidPrescription("sets must be an integer")
    if not MIN_SETS <= value <= MAX_SETS:
        raise InvalidPrescription("sets outside canonical bounds")
    return value


def validate_reps(value):
    """Return ``value`` as a legal rep prescription, or raise.

    Reps stay a *string* because the canonical plan stores ranges ("8-12"),
    time-based work ("30 dk") and fixed counts ("5") in one field. PR1 does not
    impose a numeric model on a field the rest of the system reads as free text.
    """
    from .errors import InvalidPrescription

    if not isinstance(value, str):
        raise InvalidPrescription("reps must be a string")
    reps = value.strip()
    if not reps or len(reps) > MAX_REPS_CHARS:
        raise InvalidPrescription("reps outside canonical bounds")
    return reps
