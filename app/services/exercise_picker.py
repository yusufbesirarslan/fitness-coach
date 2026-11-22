"""Read model for the Catalog V3 exercise picker (``GET /training/exercise-families``).

Families are discovery; the saved identity is always one variant's canonical
``exercise_id``. Per-variant ``availability`` is ADVISORY: it tells the picker
whether the owner's current plan context (its saved equipment and cardio
type) admits the variant, so the UI can explain instead of failing late. The
edit itself still re-checks everything under the plan lock.

One owner-scoped plan read at most; no write, no provider call. A plan the
editor could not mutate yields ``plan_equipment_context: null`` and
``availability: null`` everywhere, never a guess.
"""

from __future__ import annotations

from app.services.exercise_catalog import ExerciseContext, load_exercise_catalog
from app.services.exercise_catalog_v3 import (
    Taxonomy,
    availability,
    load_taxonomy,
    search_families,
)
from app.services.plan_mutation.document import FIELD_EXERCISE_CONTEXT, parse_plan_document
from app.services.plan_mutation.errors import PlanMutationError
from app.services.plan_mutation.validation import validate_exercise_context
from app.services.today_facts import get_active_plan

CONTRACT_VERSION = 1


def plan_exercise_context(user_id: int) -> ExerciseContext | None:
    """The owner's current plan's verified exercise context, or ``None``."""
    plan = get_active_plan(user_id)
    if plan is None:
        return None
    try:
        document, _program = parse_plan_document(plan.plan_data)
        if not isinstance(document, dict) or FIELD_EXERCISE_CONTEXT not in document:
            return None
        return validate_exercise_context(document[FIELD_EXERCISE_CONTEXT])
    except PlanMutationError:
        return None


def _labels(items):
    return [{"key": item.key, "label_tr": item.label_tr, "label_en": item.label_en}
            for item in items]


def build_picker(query: str, region: str | None, context: ExerciseContext | None,
                 taxonomy: Taxonomy | None = None) -> dict:
    taxonomy = taxonomy or load_taxonomy()
    catalog = load_exercise_catalog()
    families = []
    for family, default_id in search_families(query, region, taxonomy):
        variants = []
        for variant in family.variants:
            exercise = catalog.by_id[variant.exercise_id]
            variants.append({
                "exercise_id": exercise.exercise_id,
                "equipment": variant.equipment,
                "display_name_tr": exercise.canonical_name,
                "display_name_en": exercise.canonical_name,
                "required_equipment": sorted(exercise.equipment),
                "availability": availability(exercise, context),
            })
        families.append({
            "family_id": family.family_id,
            "display_name_tr": family.display_name_tr,
            "display_name_en": family.display_name_en,
            "target_region": family.target_region,
            "default_exercise_id": default_id,
            "variants": variants,
        })
    return {
        "contract_version": CONTRACT_VERSION,
        "catalog_version": catalog.version,
        "taxonomy_version": taxonomy.taxonomy_version,
        "plan_equipment_context": context.equipment_context if context else None,
        "regions": _labels(taxonomy.regions),
        "equipment_labels": _labels(sorted(taxonomy.equipment_labels.values(),
                                           key=lambda item: item.key)),
        "families": families,
    }
