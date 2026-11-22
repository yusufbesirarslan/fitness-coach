"""Exercise Catalog V3: an additive discovery layer over the canonical catalog.

The canonical catalog (``exercise_catalog``) stays the identity, compatibility
and generation authority. This module adds what the picker and the Coach read
need and the legacy schema cannot say:

* thirteen user-facing target regions (``primary_region`` keeps its legacy
  meaning for generation and validation; ``target_region`` is separate);
* exercise families that group functionally similar variants for discovery;
* the variant equipment that tells one family member from another.

A family never becomes an identity. Every variant keeps its own canonical
``exercise_id``, ``(family_id, equipment)`` resolves to exactly one of them,
and a variant is never substituted for another.

``equipment`` on a variant is the distinguishing item (``dumbbell``,
``smith_machine``...). It is always one of the exercise's required items, or
``bodyweight`` when nothing loads the movement. ``required_equipment`` is the
full set the exercise needs at once (barbell + rack + bench) and is what the
plan's equipment context is checked against.

Pure: one bundled data file, validated against the live catalog once and
cached. No database, Flask or provider import.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from app.services.exercise_catalog import (
    CARDIO_MOVEMENT,
    EQUIPMENT_VOCABULARY,
    CatalogConfigurationError,
    ExerciseCatalog,
    ExerciseContext,
    ExerciseDefinition,
    is_exercise_compatible,
    load_exercise_catalog,
    search_exercises,
)


TAXONOMY_PATH = Path(__file__).with_name("training_assets") / "exercise_taxonomy_v3.json"
FAMILY_ID_PATTERN = re.compile(r"^fam_[a-z0-9_]+$")
#: Reserved for the owner-private workstream; never a curated identity.
PRIVATE_ID_PREFIX = "ex_private_"

#: User-facing V3 target regions, in picker order.
TARGET_REGIONS = (
    "chest", "back", "shoulders", "biceps", "triceps", "quads", "hamstrings",
    "glutes", "calves", "core", "full_body", "cardio", "mobility",
)
#: Items that load or assist a movement. A ``bodyweight`` variant carries none.
LOADING_EQUIPMENT = frozenset({
    "barbell", "cable", "dumbbell", "ez_bar", "kettlebell", "machine",
    "resistance_band", "smith_machine",
})
#: The one legacy ``primary_region`` each V3 region implies. Used for NEW
#: entries only; reviewed legacy entries may differ (e.g. deadlift).
LEGACY_REGION_FOR = MappingProxyType({
    "chest": "chest", "back": "back", "shoulders": "shoulders",
    "biceps": "arms", "triceps": "arms",
    "quads": "lower_body", "hamstrings": "lower_body", "glutes": "lower_body",
    "calves": "calves", "core": "core", "full_body": "full_body",
    "cardio": "cardio", "mobility": "mobility",
})

AVAILABLE = "available"
EQUIPMENT_UNAVAILABLE = "equipment_unavailable"
CARDIO_UNAVAILABLE = "cardio_unavailable"
SOURCE_CURATED = "curated"


class FamilyVariantUnknown(ValueError):
    """The family or the equipment variant inside it does not exist."""


@dataclass(frozen=True)
class Label:
    key: str
    label_tr: str
    label_en: str


@dataclass(frozen=True)
class Variant:
    exercise_id: str
    family_id: str
    equipment: str
    generation: bool


@dataclass(frozen=True)
class Family:
    family_id: str
    display_name_tr: str
    display_name_en: str
    target_region: str
    variants: tuple[Variant, ...]


@dataclass(frozen=True)
class Taxonomy:
    taxonomy_version: int
    catalog_version: int
    regions: tuple[Label, ...]
    equipment_labels: Mapping[str, Label]
    families: tuple[Family, ...]
    family_by_id: Mapping[str, Family]
    variant_by_exercise: Mapping[str, Variant]


def _exact(value, keys, label, optional=frozenset()):
    if not isinstance(value, dict) or not keys <= set(value) <= keys | optional:
        raise CatalogConfigurationError(f"invalid V3 {label} keys")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise CatalogConfigurationError(f"invalid V3 {label}")
    return value


def _labels(raw, label):
    if not isinstance(raw, list):
        raise CatalogConfigurationError(f"invalid V3 {label}")
    out = []
    for item in raw:
        item = _exact(item, {"key", "label_tr", "label_en"}, label)
        out.append(Label(_text(item["key"], label), _text(item["label_tr"], label),
                         _text(item["label_en"], label)))
    if len({item.key for item in out}) != len(out):
        raise CatalogConfigurationError(f"duplicate V3 {label}")
    return tuple(out)


def _variant_equipment_is_valid(equipment: str, exercise: ExerciseDefinition) -> bool:
    if equipment == "bodyweight":
        return not (exercise.equipment & LOADING_EQUIPMENT)
    return equipment in exercise.equipment


def build_taxonomy(raw: object, catalog: ExerciseCatalog) -> Taxonomy:
    """Validate an overlay against ``catalog``; fail closed on any drift."""
    root = _exact(raw, {"taxonomy_version", "catalog_version", "regions",
                        "equipment_labels", "families"}, "taxonomy")
    version = root["taxonomy_version"]
    if type(version) is not int or version < 1:
        raise CatalogConfigurationError("invalid V3 taxonomy version")
    if root["catalog_version"] != catalog.version:
        raise CatalogConfigurationError("V3 taxonomy describes another catalog version")
    regions = _labels(root["regions"], "region")
    if tuple(region.key for region in regions) != TARGET_REGIONS:
        raise CatalogConfigurationError("V3 regions must be the reviewed thirteen")
    if any("baldır" in region.label_tr.casefold() for region in regions):
        raise CatalogConfigurationError("founder naming: no 'Baldır' label")
    equipment_labels = {item.key: item for item in _labels(root["equipment_labels"], "equipment")}
    if set(equipment_labels) != EQUIPMENT_VOCABULARY:
        raise CatalogConfigurationError("V3 equipment labels must cover the vocabulary")

    if not isinstance(root["families"], list) or not root["families"]:
        raise CatalogConfigurationError("V3 taxonomy must contain families")
    families: list[Family] = []
    family_by_id: dict[str, Family] = {}
    variant_by_exercise: dict[str, Variant] = {}
    for raw_family in root["families"]:
        entry = _exact(raw_family, {"family_id", "display_name_tr", "display_name_en",
                                    "target_region", "variants"}, "family")
        family_id = entry["family_id"]
        if not isinstance(family_id, str) or not FAMILY_ID_PATTERN.fullmatch(family_id):
            raise CatalogConfigurationError("invalid V3 family ID")
        if family_id in family_by_id:
            raise CatalogConfigurationError("duplicate V3 family ID")
        region = entry["target_region"]
        if region not in TARGET_REGIONS:
            raise CatalogConfigurationError("invalid V3 target region")
        if not isinstance(entry["variants"], list) or not entry["variants"]:
            raise CatalogConfigurationError("V3 family needs a variant")
        variants = []
        for raw_variant in entry["variants"]:
            item = _exact(raw_variant, {"equipment", "exercise_id"}, "variant",
                          optional=frozenset({"generation"}))
            exercise_id = item["exercise_id"]
            exercise = catalog.by_id.get(exercise_id) if isinstance(exercise_id, str) else None
            if exercise is None or not exercise.active:
                raise CatalogConfigurationError("V3 variant names no active catalog ID")
            if exercise_id.startswith(PRIVATE_ID_PREFIX):
                raise CatalogConfigurationError("reserved private exercise namespace")
            if exercise_id in variant_by_exercise:
                raise CatalogConfigurationError("exercise belongs to two V3 families")
            equipment = item["equipment"]
            if equipment not in EQUIPMENT_VOCABULARY or not _variant_equipment_is_valid(
                    equipment, exercise):
                raise CatalogConfigurationError("V3 variant equipment does not fit the exercise")
            generation = item.get("generation", True)
            if type(generation) is not bool:
                raise CatalogConfigurationError("V3 generation flag must be boolean")
            is_cardio = exercise.movement == CARDIO_MOVEMENT
            if is_cardio != (region == "cardio"):
                raise CatalogConfigurationError("cardio identities belong to the cardio region")
            variant = Variant(exercise_id, family_id, equipment, generation)
            variants.append(variant)
            variant_by_exercise[exercise_id] = variant
        if len({variant.equipment for variant in variants}) != len(variants):
            raise CatalogConfigurationError("V3 family equipment must be unique")
        family = Family(family_id, _text(entry["display_name_tr"], "family name"),
                        _text(entry["display_name_en"], "family name"), region, tuple(variants))
        families.append(family)
        family_by_id[family_id] = family

    active = {exercise.exercise_id for exercise in catalog.exercises if exercise.active}
    if set(variant_by_exercise) != active:
        raise CatalogConfigurationError("every active catalog ID needs exactly one V3 family")
    return Taxonomy(
        taxonomy_version=version,
        catalog_version=catalog.version,
        regions=regions,
        equipment_labels=MappingProxyType(equipment_labels),
        families=tuple(families),
        family_by_id=MappingProxyType(family_by_id),
        variant_by_exercise=MappingProxyType(variant_by_exercise),
    )


@lru_cache(maxsize=1)
def load_taxonomy() -> Taxonomy:
    try:
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogConfigurationError("unable to load V3 taxonomy") from exc
    return build_taxonomy(raw, load_exercise_catalog())


def resolve_variant(family_id: str, equipment: str, taxonomy: Taxonomy | None = None) -> str:
    """The one canonical ID for a family + equipment choice, or fail closed."""
    taxonomy = taxonomy or load_taxonomy()
    family = taxonomy.family_by_id.get(family_id) if isinstance(family_id, str) else None
    if family is None:
        raise FamilyVariantUnknown("unknown family")
    for variant in family.variants:
        if variant.equipment == equipment:
            return variant.exercise_id
    raise FamilyVariantUnknown("equipment is not a variant of this family")


@lru_cache(maxsize=1)
def generation_excluded_ids() -> frozenset[str]:
    """IDs the AI generator must not offer yet (V3 additions start excluded).

    Read straight from the overlay so the generator depends only on this one
    reviewed list, not on the full taxonomy validation (which ``load_taxonomy``
    and the test suite own). Unreadable overlay → fail closed (no generation).
    """
    try:
        raw = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        excluded = {
            variant["exercise_id"]
            for family in raw["families"] for variant in family["variants"]
            if variant.get("generation", True) is False
        }
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise CatalogConfigurationError("unable to load V3 generation exclusions") from exc
    if not all(isinstance(item, str) for item in excluded):
        raise CatalogConfigurationError("invalid V3 generation exclusions")
    return frozenset(excluded)


def generation_eligible(exercise_id: str) -> bool:
    """Whether the AI generator may offer this ID."""
    return exercise_id not in generation_excluded_ids()


def availability(exercise: ExerciseDefinition, context: ExerciseContext | None) -> str | None:
    """Advisory picker state under a plan's saved context; the edit still decides."""
    if context is None:
        return None
    if is_exercise_compatible(exercise, context):
        return AVAILABLE
    return CARDIO_UNAVAILABLE if exercise.movement == CARDIO_MOVEMENT else EQUIPMENT_UNAVAILABLE


def describe_exercise(exercise_id: str, taxonomy: Taxonomy | None = None) -> dict | None:
    """Closed Coach/plan metadata for one curated ID; ``None`` when not curated.

    Built from server-owned data only. It names a training category, never a
    muscle-activation profile.
    """
    taxonomy = taxonomy or load_taxonomy()
    variant = taxonomy.variant_by_exercise.get(exercise_id) if isinstance(exercise_id, str) else None
    if variant is None:
        return None
    exercise = load_exercise_catalog().by_id[exercise_id]
    family = taxonomy.family_by_id[variant.family_id]
    return {
        "exercise_id": exercise_id,
        "source": SOURCE_CURATED,
        "family_id": family.family_id,
        "family_name": family.display_name_en,
        "target_region": family.target_region,
        "equipment": variant.equipment,
        "required_equipment": sorted(exercise.equipment),
        "name": exercise.canonical_name,
    }


def search_families(query: str, region: str | None = None,
                    taxonomy: Taxonomy | None = None) -> tuple[tuple[Family, str], ...]:
    """Rank families by their best-matching variant; return ``(family, default_id)``.

    Reuses the catalog's accent-insensitive TR/EN search so V1 and V3 agree on
    what a query matches. An empty query browses in region, then name order.
    """
    taxonomy = taxonomy or load_taxonomy()
    catalog = load_exercise_catalog()
    order = {key: index for index, key in enumerate(TARGET_REGIONS)}
    if not query.strip():
        families = sorted(
            (family for family in taxonomy.families
             if region is None or family.target_region == region),
            key=lambda family: (order[family.target_region], family.display_name_en))
        return tuple((family, family.variants[0].exercise_id) for family in families)
    seen: dict[str, str] = {}
    for exercise in search_exercises(catalog, query):
        variant = taxonomy.variant_by_exercise.get(exercise.exercise_id)
        if variant is None or variant.family_id in seen:
            continue
        if region is not None and taxonomy.family_by_id[variant.family_id].target_region != region:
            continue
        seen[variant.family_id] = exercise.exercise_id
    return tuple((taxonomy.family_by_id[family_id], default) for family_id, default in seen.items())
