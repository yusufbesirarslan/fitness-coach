"""Canonical, product-owned exercise identity and compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata


CATALOG_PATH = Path(__file__).with_name("training_assets") / "exercises.json"
ID_PATTERN = re.compile(r"^ex_[a-z0-9_]+$")
HYPHENS = "‐‑‒–—―−"

EQUIPMENT_VOCABULARY = frozenset({
    "barbell", "bench", "bodyweight", "cable", "cardio_machine",
    "dumbbell", "kettlebell", "machine", "outdoor_running", "outdoor_walking",
    "pool", "pull_up_bar", "rack", "resistance_band", "rope",
    "stationary_bicycle",
})
MOVEMENT_VOCABULARY = frozenset({
    "anti_extension", "anti_rotation", "calf_raise", "carry", "cardio",
    "core_dynamic", "curl", "dip", "hinge", "horizontal_pull",
    "horizontal_push", "lunge", "mobility", "squat", "vertical_pull",
    "vertical_push",
})
# The one movement value that is a cardio *modality* rather than a resistance
# pattern. Named here, in the vocabulary's own module, so callers that need to
# reason about cardio placement do not grow a second movement vocabulary.
CARDIO_MOVEMENT = "cardio"

REGION_VOCABULARY = frozenset({
    "arms", "back", "calves", "cardio", "chest", "core", "full_body",
    "lower_body", "mobility", "shoulders",
})

ALL_CATALOG_EQUIPMENT = EQUIPMENT_VOCABULARY
CONTEXT_EQUIPMENT = {
    "ev": frozenset({"bodyweight"}),
    "minimal": frozenset({"bodyweight", "dumbbell", "resistance_band"}),
    "spor_salonu": ALL_CATALOG_EQUIPMENT,
}
CARDIO_EQUIPMENT = {
    "kosu": frozenset({"outdoor_running"}),
    "yuruyus": frozenset({"outdoor_walking"}),
    "ip_atlama": frozenset({"rope"}),
    "bisiklet": frozenset({"stationary_bicycle"}),
    "yuzme": frozenset({"pool"}),
    "karisik": frozenset({
        "outdoor_running", "outdoor_walking", "rope", "stationary_bicycle", "pool",
    }),
}


class CatalogConfigurationError(ValueError):
    """The code-owned catalog asset is malformed or internally inconsistent."""


class ExerciseResolutionError(ValueError):
    """Base type for deterministic, fail-closed exercise resolution failures."""


class ExerciseUnresolved(ExerciseResolutionError):
    pass


class ExerciseAmbiguous(ExerciseResolutionError):
    pass


class ExerciseIdentityInvalid(ExerciseResolutionError):
    pass


class ExerciseInactive(ExerciseResolutionError):
    pass


class ExerciseIncompatible(ExerciseResolutionError):
    pass


@dataclass(frozen=True)
class ExerciseDefinition:
    exercise_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    equipment: frozenset[str]
    movement: str
    primary_region: str
    active: bool


@dataclass(frozen=True)
class ExerciseContext:
    equipment_context: str
    cardio_type: str = "yok"
    style: str = "general_fitness"
    catalog_version: int = 1


@dataclass(frozen=True)
class ExerciseCatalog:
    version: int
    exercises: tuple[ExerciseDefinition, ...]
    by_id: Mapping[str, ExerciseDefinition]
    by_lookup: Mapping[str, tuple[ExerciseDefinition, ...]]


def normalize_exercise_lookup(value: str) -> str:
    """Normalize safe spelling variants only; never infer or fuzzy-match intent."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(str.maketrans({char: "-" for char in HYPHENS}))
    return " ".join(normalized.casefold().split())


def _require_exact_keys(value: object, expected: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise CatalogConfigurationError(f"invalid {label} keys")
    return value


def _load_entry(raw: object) -> ExerciseDefinition:
    entry = _require_exact_keys(raw, frozenset({
        "exercise_id", "canonical_name", "aliases", "equipment", "movement",
        "primary_region", "active",
    }), "exercise")
    exercise_id = entry["exercise_id"]
    canonical_name = entry["canonical_name"]
    aliases = entry["aliases"]
    equipment = entry["equipment"]
    movement = entry["movement"]
    primary_region = entry["primary_region"]
    active = entry["active"]
    if not isinstance(exercise_id, str) or not ID_PATTERN.fullmatch(exercise_id):
        raise CatalogConfigurationError("invalid exercise ID")
    if not isinstance(canonical_name, str) or not normalize_exercise_lookup(canonical_name):
        raise CatalogConfigurationError("invalid canonical name")
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and normalize_exercise_lookup(alias) for alias in aliases
    ):
        raise CatalogConfigurationError("invalid aliases")
    if len(set(aliases)) != len(aliases):
        raise CatalogConfigurationError("duplicate aliases")
    if not isinstance(equipment, list) or not equipment or not all(
        isinstance(item, str) and item in EQUIPMENT_VOCABULARY for item in equipment
    ):
        raise CatalogConfigurationError("invalid equipment")
    if len(set(equipment)) != len(equipment):
        raise CatalogConfigurationError("duplicate equipment")
    if (
        not isinstance(movement, str)
        or movement not in MOVEMENT_VOCABULARY
        or not isinstance(primary_region, str)
        or primary_region not in REGION_VOCABULARY
    ):
        raise CatalogConfigurationError("invalid movement metadata")
    if type(active) is not bool:
        raise CatalogConfigurationError("active must be a boolean")
    return ExerciseDefinition(
        exercise_id=exercise_id,
        canonical_name=canonical_name,
        aliases=tuple(aliases),
        equipment=frozenset(equipment),
        movement=movement,
        primary_region=primary_region,
        active=active,
    )


@lru_cache(maxsize=1)
def load_exercise_catalog() -> ExerciseCatalog:
    """Load and validate the one reviewed, version-controlled catalog asset."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogConfigurationError("unable to load exercise catalog") from exc
    root = _require_exact_keys(raw, frozenset({"version", "exercises"}), "catalog")
    if type(root["version"]) is not int or root["version"] < 1:
        raise CatalogConfigurationError("invalid catalog version")
    if not isinstance(root["exercises"], list) or not root["exercises"]:
        raise CatalogConfigurationError("catalog must contain exercises")

    exercises = tuple(_load_entry(entry) for entry in root["exercises"])
    by_id: dict[str, ExerciseDefinition] = {}
    by_lookup: dict[str, tuple[ExerciseDefinition, ...]] = {}
    for exercise in exercises:
        if exercise.exercise_id in by_id:
            raise CatalogConfigurationError("duplicate exercise ID")
        by_id[exercise.exercise_id] = exercise
        for name in (exercise.canonical_name, *exercise.aliases):
            lookup = normalize_exercise_lookup(name)
            if lookup in by_lookup:
                raise CatalogConfigurationError("normalized name or alias collision")
            by_lookup[lookup] = (exercise,)
    return ExerciseCatalog(
        version=root["version"],
        exercises=exercises,
        by_id=MappingProxyType(by_id),
        by_lookup=MappingProxyType(by_lookup),
    )


def resolve_exercise(
    exercise_id: str | None = None,
    name: str | None = None,
    catalog: ExerciseCatalog | None = None,
) -> ExerciseDefinition:
    """Resolve only a supplied canonical ID or exact normalized declared name."""
    catalog = catalog or load_exercise_catalog()
    if exercise_id is not None:
        if not isinstance(exercise_id, str) or not ID_PATTERN.fullmatch(exercise_id):
            raise ExerciseIdentityInvalid("invalid exercise ID")
        exercise = catalog.by_id.get(exercise_id)
        if exercise is None:
            raise ExerciseIdentityInvalid("unknown exercise ID")
        if not exercise.active:
            raise ExerciseInactive("inactive exercise ID")
        return exercise
    if not isinstance(name, str) or not normalize_exercise_lookup(name):
        raise ExerciseUnresolved("exercise name is required")
    matches = catalog.by_lookup.get(normalize_exercise_lookup(name), ())
    if not matches:
        raise ExerciseUnresolved("unknown exercise name")
    if len(matches) != 1:
        raise ExerciseAmbiguous("ambiguous exercise name")
    exercise = matches[0]
    if not exercise.active:
        raise ExerciseInactive("inactive exercise name")
    return exercise


def is_exercise_compatible(exercise: ExerciseDefinition, context: ExerciseContext) -> bool:
    """Check closed product equipment and cardio rules without inspecting names."""
    if context.equipment_context not in CONTEXT_EQUIPMENT:
        return False
    if exercise.movement == "cardio":
        return bool(
            context.cardio_type in CARDIO_EQUIPMENT
            and exercise.equipment <= CARDIO_EQUIPMENT[context.cardio_type]
        )
    return exercise.equipment <= CONTEXT_EQUIPMENT[context.equipment_context]


def compatible_exercises(context: ExerciseContext) -> tuple[ExerciseDefinition, ...]:
    """Return active exercises compatible with a server-owned accepted context."""
    return tuple(
        exercise for exercise in load_exercise_catalog().exercises
        if exercise.active and is_exercise_compatible(exercise, context)
    )
