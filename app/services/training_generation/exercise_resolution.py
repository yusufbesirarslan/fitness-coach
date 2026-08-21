"""Generation-time canonicalization of a validated plan's exercise references.

PR3 (response_validator.py) proves a generated plan is the correct *shape*
and matches the accepted request. It does not, and must not, know about
exercise identity — that authority is the server-owned catalog
(app/services/exercise_catalog.py). This module is the one place a validated
candidate plan's ``egzersizler`` entries become catalog-owned identity.

It never repairs, substitutes, or infers: every generated exercise reference
either resolves to exactly one active, equipment-compatible catalog entry, or
the whole generation attempt fails closed with a typed
``GenerationOutputError`` subclass. Provider-generated strings and
ID-looking strings are never exercise authority.

Runs exactly once, after generation has already produced an accepted
candidate plan — service.py calls this outside the parse/truncation repair
boundary (see the call site in ``generate_training_plan_payload``), so an
exercise-authority failure can never be caught by, or looped back into, the
repair path.
"""
from __future__ import annotations

from app.services.exercise_catalog import (
    ExerciseAmbiguous,
    ExerciseContext,
    ExerciseDefinition,
    ExerciseIdentityInvalid,
    ExerciseInactive,
    ExerciseResolutionError,
    ExerciseUnresolved,
    ID_PATTERN,
    is_exercise_compatible,
    load_exercise_catalog,
    normalize_exercise_lookup,
    resolve_exercise,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseAmbiguousError,
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseIncompatibleError,
    GenerationExerciseUnresolvedError,
)


def _resolve_generated_name(
    name: str,
    catalog,
    cache: dict[str, ExerciseDefinition],
) -> ExerciseDefinition:
    """Resolve one provider-supplied exercise name against the catalog.

    ``cache`` is scoped to a single ``canonicalize_plan_exercises`` call, so a
    7-day plan that references the same exercise many times (same or
    differently-worded alias) pays for at most one catalog lookup per
    distinct normalized name — never one per occurrence, and never a second
    catalog load (``load_exercise_catalog`` is called once by the caller and
    threaded through here).
    """
    normalized = normalize_exercise_lookup(name)
    cached = cache.get(normalized)
    if cached is not None:
        return cached

    # A name shaped like a catalog ID cannot become authoritative by merely
    # failing to match a canonical name or alias — reject it as an identity
    # violation rather than letting it fall through as an unrelated "unknown
    # exercise" outcome.
    if isinstance(name, str) and ID_PATTERN.fullmatch(name.strip()):
        raise GenerationExerciseIdentityInvalidError(
            "generated exercise name is an ID-shaped string, not a display name")

    try:
        exercise = resolve_exercise(name=name, catalog=catalog)
    except ExerciseAmbiguous as exc:
        raise GenerationExerciseAmbiguousError(
            "generated exercise name matches more than one catalog entry") from exc
    except ExerciseIdentityInvalid as exc:
        raise GenerationExerciseIdentityInvalidError(
            "generated exercise reference is not valid catalog identity") from exc
    except (ExerciseUnresolved, ExerciseInactive) as exc:
        # An inactive catalog entry is not usable in a new plan either way;
        # from generation's point of view this is the same "not currently
        # resolvable" outcome as no match at all.
        raise GenerationExerciseUnresolvedError(
            "generated exercise name does not match any active catalog entry") from exc
    except ExerciseResolutionError as exc:  # pragma: no cover - defensive fail-closed
        # Any future domain resolution failure this module does not yet know
        # about must still fail closed as a typed generation error, never as
        # a raw domain ValueError leaking past this boundary.
        raise GenerationExerciseUnresolvedError(
            "generated exercise reference could not be resolved") from exc

    cache[normalized] = exercise
    return exercise


def canonicalize_plan_exercises(plan: dict, context: ExerciseContext) -> dict:
    """Resolve every generated exercise reference to catalog-owned identity.

    Must run after PR3 structural + semantic validation has already accepted
    ``plan`` (``validate_generated_plan``). For each exercise entry this
    writes only ``exercise_id`` (the catalog's stable identity) and replaces
    ``isim`` with the catalog's canonical display name; every prescription
    field (``set``/``tekrar``/``dinlenme``/``not``) is carried over
    unchanged. Never adds catalog metadata (equipment/movement/region) to the
    plan — those stay server-side.

    Fails closed (raises a ``GenerationOutputError`` subclass) on the first
    exercise reference that does not resolve to exactly one active,
    ``context``-compatible catalog entry. No automatic substitution.
    """
    catalog = load_exercise_catalog()
    cache: dict[str, ExerciseDefinition] = {}
    canonical_program = []
    for day in plan["program"]:
        canonical_exercises = []
        for exercise in day["egzersizler"]:
            resolved = _resolve_generated_name(exercise["isim"], catalog, cache)
            if not is_exercise_compatible(resolved, context):
                raise GenerationExerciseIncompatibleError(
                    "generated exercise is not compatible with the accepted "
                    "equipment context")
            canonical_exercises.append({
                **exercise,
                "isim": resolved.canonical_name,
                "exercise_id": resolved.exercise_id,
            })
        canonical_program.append({**day, "egzersizler": canonical_exercises})
    return {**plan, "program": canonical_program}
