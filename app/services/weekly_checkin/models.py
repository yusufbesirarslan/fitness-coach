"""Framework-free value objects of the weekly check-in authority (LP16-A).

No ORM query, no Flask, no provider: transports parse their own wire format
into these and hand them to ``service``; ``service`` answers with these.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Optional


NOT_APPLICABLE = "not_applicable"
TERNARY_TOKENS = frozenset(("yes", "partly", "no", NOT_APPLICABLE))


@dataclass(frozen=True)
class RecordedNutritionAdherence:
    """Closed read vocabulary shared by native history and later consumers."""

    scale: str | None
    value: int | str | None


def recorded_nutrition_adherence(row):
    """Read the scale and exact value of a persisted check-in, if available."""
    answer = row.nutrition_adherence_v2
    if answer is not None:
        return RecordedNutritionAdherence("ternary_v2", answer)
    score = row.beslenme_uyumu
    if type(score) is int and 1 <= score <= 5:
        return RecordedNutritionAdherence("numeric_1_5", score)
    return RecordedNutritionAdherence(None, None)


@dataclass(frozen=True)
class FullCheckIn:
    """Already-parsed values of one full weekly check-in.

    The service never parses or defaults these: the web transport keeps its
    legacy parser (missing metrics become 3, unknown overload becomes
    ``kismen``) in ``tracking.py``; a stricter native parser belongs to LP16-B.
    ``intensity`` is required — a row with ``yogunluk`` set is what every
    reader calls a full check-in. ``note`` is stored as given.
    """

    weight: float
    intensity: int
    fatigue: int
    progressive_overload: str
    sleep_quality: int
    nutrition_adherence: int | None
    note: Any = None
    nutrition_adherence_v2: str | None = None


@dataclass(frozen=True)
class CheckInContext:
    """What a full check-in is persisted against, read once by the service.

    ``previous`` is the owner's latest FULL check-in (sparse weight-only rows
    excluded); ``session`` is the canonical ``UserSession`` whose derived
    targets the check-in weight recalculates. Callers may read both (the web
    feedback prompt does) but never pass a different session to the service.
    """

    owner_id: int
    previous: Any = None
    session: Any = None


@dataclass(frozen=True)
class BodyWeightUpdate:
    """Result of the one shared weight primitive.

    ``profile_ready`` false means the derived targets were left untouched and
    the values are the session's stored ones (or ``None`` without a session).
    """

    bmr: Optional[float]
    tdee: Optional[float]
    target_calories: Optional[float]
    profile_ready: bool


class SubmissionOutcome(str, enum.Enum):
    FRESH = "fresh"          # no row holds this key yet: go on and persist
    COMMITTED = "committed"  # this call's row is durable
    REPLAYED = "replayed"    # the key's committed result, same intent
    CONFLICT = "conflict"    # the key belongs to a different intent


@dataclass(frozen=True)
class SubmissionResult:
    """Outcome of an idempotent step; ``response`` is the stored replay body."""

    outcome: SubmissionOutcome
    response: Any = None
