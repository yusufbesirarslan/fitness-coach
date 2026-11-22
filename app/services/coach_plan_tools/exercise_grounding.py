"""Catalog identity for Coach ADD / replacement destinations.

Exact canonical names and declared aliases are mutation-eligible.
A simple English plural of a unique *canonical* name is also eligible
(Walking Lunges → Walking Lunge) because the catalog entry is unambiguous.

Everything else is presentation-only: at most one high-confidence
suggestion, never silent substitution. Fuzzy scores never become the
identity that is written.

Only reviewed catalog IDs are destinations (``coach_grounding_eligible``):
the Catalog V3 additions are not offered until a reviewed overlay change
admits them, so a new name can never steal or split a V2 phrase, and an
exact V3 name is ``unknown`` rather than re-read as another exercise.
``resolve_slot_name`` (finding an exercise already in the plan) also lets an
exact V3 name name itself, because the editor may have placed it.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.exercise_catalog import (
    ExerciseResolutionError,
    load_exercise_catalog,
    normalize_exercise_lookup,
    resolve_exercise,
)
from app.services.exercise_catalog_v3 import coach_grounding_eligible


KIND_RESOLVED = "resolved"
KIND_SUGGEST = "suggest"
KIND_UNKNOWN = "unknown"

# Suggestion is presentation-only. These bounds keep "blabla" from
# matching a random catalog row and keep "dambıl curl" suggesting one
# biceps-curl entry rather than every curl.
_SUGGEST_MIN = 0.64
_SUGGEST_GAP = 0.08


@dataclass(frozen=True)
class ExerciseTarget:
    kind: str
    canonical_name: str = ""
    suggestion: str = ""


def _fold(value):
    text = normalize_exercise_lookup(value)
    return text.replace("ı", "i").replace("â", "a")


def _exact(name):
    try:
        return resolve_exercise(name=name)
    except ExerciseResolutionError:
        return None


def resolve_destination(name):
    """Resolve an ADD/replacement destination against the live catalog.

    An exact name of a not-yet-reviewed ID is ``unknown``: it is never
    re-read as a different (reviewed) exercise by the inexact paths.
    """
    if not isinstance(name, str) or not name.strip():
        return ExerciseTarget(KIND_UNKNOWN)
    definition = _exact(name)
    if definition is not None:
        if not coach_grounding_eligible(definition.exercise_id):
            return ExerciseTarget(KIND_UNKNOWN)
        return ExerciseTarget(
            KIND_RESOLVED, canonical_name=definition.canonical_name)
    return _inexact(name)


def resolve_slot_name(name):
    """Name an exercise already in the plan (update/replace/remove lookup).

    The editor may have placed any active catalog ID, so an exact name
    resolves to itself whether or not it is reviewed for Coach destinations.
    Everything else follows the destination rules (reviewed IDs only).
    """
    if not isinstance(name, str) or not name.strip():
        return ExerciseTarget(KIND_UNKNOWN)
    definition = _exact(name)
    if definition is not None:
        return ExerciseTarget(
            KIND_RESOLVED, canonical_name=definition.canonical_name)
    return _inexact(name)


def _inexact(name):
    subset = _unique_token_subset(name)
    if subset is not None:
        return ExerciseTarget(
            KIND_RESOLVED, canonical_name=subset.canonical_name)

    plural = _unique_canonical_plural(name)
    if plural is not None:
        return ExerciseTarget(KIND_RESOLVED, canonical_name=plural.canonical_name)

    alias_plural = _unique_alias_plural(name)
    if alias_plural is not None:
        return ExerciseTarget(
            KIND_SUGGEST, suggestion=alias_plural.canonical_name)

    suggestion = _high_confidence_suggestion(name)
    if suggestion is not None:
        return ExerciseTarget(
            KIND_SUGGEST, suggestion=suggestion.canonical_name)
    return ExerciseTarget(KIND_UNKNOWN)


def _candidates():
    return tuple(
        exercise for exercise in load_exercise_catalog().exercises
        if exercise.active and coach_grounding_eligible(exercise.exercise_id))


def _unique_canonical_plural(name):
    """``Walking Lunges`` → the one catalog entry whose canonical name is
    ``Walking Lunge``. Aliases are not eligible: ``squats`` matching the
    alias ``Squat`` of Barbell Back Squat must stay a suggestion.
    """
    needle = _fold(name)
    if not needle:
        return None
    hits = []
    for exercise in _candidates():
        canon = _fold(exercise.canonical_name)
        if needle in (canon + "s", canon + "es") or (
                canon.endswith("s") and needle == canon[:-1]):
            hits.append(exercise)
    if len(hits) == 1:
        return hits[0]
    return None


def _unique_alias_plural(name):
    """``squats`` → the one entry whose declared alias is ``Squat``.

    Not mutation-eligible: several squat variants exist, so this stays a
    suggestion even when the alias uniquely names one catalog row.
    """
    needle = _fold(name)
    if not needle:
        return None
    hits = []
    for exercise in _candidates():
        for alias in exercise.aliases:
            folded = _fold(alias)
            if needle in (folded + "s", folded + "es"):
                hits.append(exercise)
                break
    if len(hits) == 1:
        return hits[0]
    return None


def _unique_token_subset(name):
    """``Dumbbell Curl`` → ``Dumbbell Biceps Curl`` when no other entry
    contains every query token.
    """
    tokens = tuple(part for part in _fold(name).split() if part)
    if len(tokens) < 2:
        return None
    hits = []
    for exercise in _candidates():
        labels = (exercise.canonical_name,) + tuple(exercise.aliases)
        for label in labels:
            label_tokens = set(_fold(label).split())
            if set(tokens) <= label_tokens:
                hits.append(exercise)
                break
    if len(hits) == 1:
        return hits[0]
    return None


def _high_confidence_suggestion(name):
    needle = _fold(name)
    if not needle:
        return None
    scored = []
    for exercise in _candidates():
        labels = (exercise.canonical_name,) + tuple(exercise.aliases)
        score = max(_similarity(needle, _fold(label)) for label in labels)
        scored.append((score, exercise))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None
    best_score, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= _SUGGEST_MIN and (best_score - second) >= _SUGGEST_GAP:
        return best
    return None


def _similarity(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    whole = SequenceMatcher(None, left, right).ratio()
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return whole
    token_hits = []
    for token in left_tokens:
        token_hits.append(max(
            SequenceMatcher(None, token, other).ratio()
            for other in right_tokens))
    token_score = sum(token_hits) / len(token_hits)
    return max(whole, token_score)
