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
"""
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

__all__ = [
    "MAX_EXERCISE_NAME_CHARS",
    "MAX_REPS_CHARS",
    "MAX_SETS",
    "MIN_SETS",
    "REST_TIP",
    "VALID_TIPS",
    "WEEKDAYS",
    "WORKOUT_TIPS",
    "normalize_exercise_name",
    "validate_reps",
    "validate_sets",
]


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
