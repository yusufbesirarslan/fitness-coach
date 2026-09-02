"""Catalog identity for Coach ADD / replacement destinations.

Exact canonical names and declared aliases are mutation-eligible.
A simple English plural of a unique *canonical* name is also eligible
(Walking Lunges → Walking Lunge) because the catalog entry is unambiguous.

Everything else is presentation-only: at most one high-confidence
suggestion, never silent substitution. Fuzzy scores never become the
identity that is written.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.services.exercise_catalog import (
    ExerciseResolutionError,
    load_exercise_catalog,
    normalize_exercise_lookup,
    resolve_exercise,
)


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


def resolve_destination(name):
    """Resolve an ADD/replacement destination against the live catalog."""
    if not isinstance(name, str) or not name.strip():
        return ExerciseTarget(KIND_UNKNOWN)
    try:
        definition = resolve_exercise(name=name)
    except ExerciseResolutionError:
        definition = None
    if definition is not None:
        return ExerciseTarget(
            KIND_RESOLVED, canonical_name=definition.canonical_name)

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


def _unique_canonical_plural(name):
    """``Walking Lunges`` → the one catalog entry whose canonical name is
    ``Walking Lunge``. Aliases are not eligible: ``squats`` matching the
    alias ``Squat`` of Barbell Back Squat must stay a suggestion.
    """
    needle = _fold(name)
    if not needle:
        return None
    hits = []
    for exercise in load_exercise_catalog().exercises:
        if not exercise.active:
            continue
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
    for exercise in load_exercise_catalog().exercises:
        if not exercise.active:
            continue
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
    for exercise in load_exercise_catalog().exercises:
        if not exercise.active:
            continue
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
    for exercise in load_exercise_catalog().exercises:
        if not exercise.active:
            continue
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
