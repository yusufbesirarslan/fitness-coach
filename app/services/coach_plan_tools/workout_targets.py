"""Resolve a Coach semantic workout target against the ACTIVE persisted plan.

The model may name a weekday or a nickname ("leg workout", "push day").
Only the persisted plan is allowed to decide which slot that is. Zero or
several matches are clarification, never ``candidates[0]``.
"""
from dataclasses import dataclass
import json

from app.services.exercise_catalog import resolve_exercise, ExerciseResolutionError
from app.services.plan_mutation.validation import WORKOUT_TIPS
from app.services.today_facts import get_active_plan

from .weekdays import canonicalize_weekday, find_explicit_weekday


KIND_RESOLVED = "resolved"
KIND_AMBIGUOUS = "ambiguous"
KIND_NOT_FOUND = "not_found"
KIND_NONE = "none"

# Closed token sets. This is not a classifier: a user turn either contains
# one of these tokens or it does not. Keys are catalog ``primary_region``
# values plus a few focus nicknames that plans actually store in ``odak``.
_REGION_TOKENS = {
    "lower_body": (
        "leg", "legs", "lower", "quad", "quads", "glute", "glutes",
        "hamstring", "hamstrings",
        "bacak", "bacaklar", "alt",
    ),
    "chest": (
        "chest", "push", "bench",
        "gogus", "göğüs", "itis", "itiş",
    ),
    "back": (
        "back", "pull",
        "sirt", "sırt", "cekis", "çekiş",
    ),
    "arms": (
        "arm", "arms", "bicep", "biceps", "tricep", "triceps",
        "kol", "kollar", "biseps", "triseps",
    ),
    "shoulders": (
        "shoulder", "shoulders", "delt", "delts",
        "omuz", "omuzlar",
    ),
    "core": (
        "core", "abs", "ab",
        "karin", "karın", "merkez", "core",
    ),
}

_FOCUS_HINTS = {
    "lower_body": ("leg", "bacak", "alt vucut", "alt vücut", "lower body"),
    "chest": ("chest", "gogus", "göğüs", "itis", "itiş", "push"),
    "back": ("back", "sirt", "sırt", "cekis", "çekiş", "pull"),
    "arms": ("arm", "kol", "bicep", "tricep"),
    "shoulders": ("shoulder", "omuz"),
    "core": ("core", "merkez", "karin", "karın", "abs"),
}


@dataclass(frozen=True)
class WorkoutTarget:
    kind: str
    day: str = ""
    label: str = ""
    candidates: tuple = ()


def _fold(value):
    if not isinstance(value, str):
        return ""
    table = str.maketrans({
        "ı": "i", "İ": "i", "â": "a", "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g", "ö": "o", "Ö": "o", "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
    })
    return " ".join(value.translate(table).casefold().split())


def _tokens(message):
    folded = _fold(message)
    out = []
    current = []
    for char in folded:
        if char.isalnum():
            current.append(char)
        else:
            if current:
                out.append("".join(current))
                current = []
    if current:
        out.append("".join(current))
    return tuple(out)


def semantic_regions(message):
    """Closed-vocab regions named in the user turn. Empty if none."""
    tokens = set(_tokens(message))
    if not tokens:
        return ()
    # Ignore generic words that would otherwise light up "alt"/"lower".
    generic = {"workout", "antrenman", "day", "gun", "gün", "my", "the",
               "a", "to", "on", "for", "with", "and", "ve", "bir"}
    tokens -= generic
    found = []
    for region, names in _REGION_TOKENS.items():
        if tokens & set(names):
            found.append(region)
    return tuple(found)


def _program_days(user_id):
    plan = get_active_plan(user_id)
    if plan is None or not plan.plan_data:
        return ()
    try:
        document = json.loads(plan.plan_data)
    except (TypeError, ValueError):
        return None
    if isinstance(document, list):
        program = document
    elif isinstance(document, dict):
        program = document.get("program")
    else:
        return None
    if not isinstance(program, list):
        return None
    return [day for day in program if isinstance(day, dict)]


def _day_regions(day):
    """Best-effort regions for one persisted day, from focus + catalog."""
    regions = set()
    focus = _fold(day.get("odak") or "")
    for region, hints in _FOCUS_HINTS.items():
        if any(hint in focus for hint in hints):
            regions.add(region)
    exercises = day.get("egzersizler") or []
    if not isinstance(exercises, list):
        exercises = []
    for entry in exercises:
        if not isinstance(entry, dict):
            continue
        name = entry.get("isim")
        try:
            definition = resolve_exercise(name=name) if name else None
        except ExerciseResolutionError:
            definition = None
        if definition is not None:
            regions.add(definition.primary_region)
            continue
        folded_name = _fold(name or "")
        for region, hints in _FOCUS_HINTS.items():
            if any(hint in folded_name for hint in hints):
                regions.add(region)
    return frozenset(regions)


def _is_workout_day(day):
    return day.get("tip") in WORKOUT_TIPS


def resolve_workout_target(user_id, message, model_day=None):
    """Map user text (and an optional model day) onto one plan slot.

    Priority:
    1. An explicit weekday in the user turn.
    2. A semantic target matched against the active plan.
    3. A canonical weekday the model supplied, only when the user named
       neither a weekday nor a semantic target — and only if that slot
       exists. The model still cannot invent a nickname.
    """
    explicit = find_explicit_weekday(message) if message else None
    if explicit is not None:
        return WorkoutTarget(KIND_RESOLVED, day=explicit, label=explicit)

    regions = semantic_regions(message) if message else ()
    days = _program_days(user_id)
    if not days:
        # No readable plan: let the mutation domain refuse PLAN_NOT_FOUND
        # / PLAN_NOT_MUTABLE instead of inventing a workout-target miss.
        return WorkoutTarget(KIND_NONE)

    if regions:
        matches = []
        for day in days:
            if not _is_workout_day(day):
                continue
            gun = day.get("gun")
            if not isinstance(gun, str) or not gun:
                continue
            day_regions = _day_regions(day)
            if day_regions & set(regions):
                matches.append(gun)
        # Preserve plan order, unique.
        unique = []
        for gun in matches:
            if gun not in unique:
                unique.append(gun)
        if len(unique) == 1:
            label = regions[0].replace("_", " ")
            return WorkoutTarget(
                KIND_RESOLVED, day=unique[0], label=label)
        if len(unique) > 1:
            return WorkoutTarget(KIND_AMBIGUOUS, candidates=tuple(unique))
        return WorkoutTarget(KIND_NOT_FOUND)

    canonical = canonicalize_weekday(model_day) if model_day else None
    if canonical is not None:
        guns = [day.get("gun") for day in days]
        if canonical in guns:
            return WorkoutTarget(KIND_RESOLVED, day=canonical, label=canonical)
        return WorkoutTarget(KIND_NOT_FOUND)

    if message and str(message).strip():
        # The user asked to change the plan but named no day and no
        # nickname. Do not let the model pick one.
        return WorkoutTarget(KIND_NOT_FOUND)
    return WorkoutTarget(KIND_NONE)


def find_exercise_slots(user_id, name):
    """Canonical days in the active plan that hold ``name``.

    Matching is catalog-aware when the name (or the stored slot name)
    resolves, otherwise casefold equality. Used to resume update/replace
    when the user named the exercise but not the day. Several hits stay
    ambiguous — never ``candidates[0]``.
    """
    if not isinstance(name, str) or not name.strip():
        return ()
    days = _program_days(user_id)
    if not days:
        return ()
    wanted = _identity(name)
    if wanted is None:
        return ()
    found = []
    for day in days:
        if not _is_workout_day(day):
            continue
        gun = day.get("gun")
        if not isinstance(gun, str) or not gun:
            continue
        exercises = day.get("egzersizler") or []
        if not isinstance(exercises, list):
            continue
        for entry in exercises:
            if not isinstance(entry, dict):
                continue
            slot = _identity(entry.get("isim"))
            if slot is None:
                continue
            if slot == wanted:
                if gun not in found:
                    found.append(gun)
                break
    return tuple(found)


def _identity(name):
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        definition = resolve_exercise(name=name)
    except ExerciseResolutionError:
        definition = None
    if definition is not None:
        return ("id", definition.exercise_id)
    return ("name", _fold(name))
