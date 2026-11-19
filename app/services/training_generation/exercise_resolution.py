"""Canonicalization of a validated plan's exercise references.

PR3 (response_validator.py) proves a plan is the correct *shape* and matches
the accepted request. It does not, and must not, know about exercise
identity — that authority is the server-owned catalog
(app/services/exercise_catalog.py). This module is the one place a validated
plan's ``egzersizler`` entries become catalog-owned identity. Two entry
points, one per boundary:

- ``canonicalize_generated_exercises`` (generation, PR B): the provider chose
  an ``exercise_id`` from the request's closed ``CompatibleExerciseChoices``.
  Validated by ID only — shape, existence, active, membership in that exact
  set, equipment, cardio placement — and the display name is written from
  the catalog. There is no name lookup on this path, so a translated,
  misspelled or invented name cannot decide (or break) identity.
- ``canonicalize_plan_exercises`` (save): a client plan carries the IDs
  generation gave it, or is a legacy name-only plan resolved by exact
  normalized name/alias.

Neither repairs, substitutes, or infers: every reference resolves to exactly
one active, compatible catalog entry, or the whole attempt fails closed with
a typed ``GenerationOutputError`` subclass.

Generation runs this exactly once, after an accepted candidate exists —
service.py calls it outside the parse/truncation repair boundary, so an
exercise-authority failure can never be caught by, or looped back into, the
repair path.
"""
from __future__ import annotations

from app.services.exercise_catalog import (
    CARDIO_MOVEMENT,
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
from app.services.training_generation.exercise_choices import (
    CompatibleExerciseChoices,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseAmbiguousError,
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseIncompatibleError,
    GenerationExerciseUnresolvedError,
)
from app.services.training_generation.plan_schema import CARDIO_TIP, EXERCISE_ID_KEY


def _resolve_generated_name(
    name: str,
    catalog,
    cache: dict[str, ExerciseDefinition],
) -> ExerciseDefinition:
    """Resolve one name-only (legacy client) exercise name against the catalog.

    Save path only since PR B; generation never resolves by name.

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
            "generated exercise name does not match any active catalog entry",
            resolution_category=(
                "inactive_match" if isinstance(exc, ExerciseInactive)
                else "unresolved_name"
            ),
        ) from exc
    except ExerciseResolutionError as exc:  # pragma: no cover - defensive fail-closed
        # Any future domain resolution failure this module does not yet know
        # about must still fail closed as a typed generation error, never as
        # a raw domain ValueError leaking past this boundary.
        raise GenerationExerciseUnresolvedError(
            "generated exercise reference could not be resolved",
            resolution_category="resolution_error",
        ) from exc

    cache[normalized] = exercise
    return exercise


def _resolve_declared_id(exercise_id: str, catalog) -> ExerciseDefinition:
    """Resolve a client-declared ``exercise_id`` — untrusted until it resolves.

    An ID arriving on a save is only ever a *claim*: it looks like identity,
    which is exactly why it gets no benefit of the doubt. It is re-resolved
    against the same catalog on every submission, so a retired or renamed
    entry stops being savable the moment the product retires it.
    """
    try:
        return resolve_exercise(exercise_id=exercise_id, catalog=catalog)
    except ExerciseIdentityInvalid as exc:
        raise GenerationExerciseIdentityInvalidError(
            "declared exercise ID is not valid catalog identity") from exc
    except ExerciseInactive as exc:
        raise GenerationExerciseUnresolvedError(
            "declared exercise ID is not an active catalog entry") from exc
    except ExerciseResolutionError as exc:  # pragma: no cover - defensive
        raise GenerationExerciseUnresolvedError(
            "declared exercise ID could not be resolved") from exc


def _resolve_plan_exercise(
    exercise: dict,
    catalog,
    cache: dict[str, ExerciseDefinition],
) -> ExerciseDefinition:
    """Resolve one plan entry, ID-bearing or name-only, through one path.

    A saved plan may carry the ID canonicalization already wrote, or be a
    legacy name-only plan. Both land here rather than in a second save-side
    resolver, because two resolution paths are two places for exercise
    authority to drift.
    """
    declared_id = exercise.get(EXERCISE_ID_KEY)
    if declared_id is not None:
        return _resolve_declared_id(declared_id, catalog)
    return _resolve_generated_name(exercise["isim"], catalog, cache)


def check_placement(exercise: ExerciseDefinition, day: dict) -> None:
    """Bind a cardio-movement entry to a cardio day, fail-closed otherwise.

    ``is_exercise_compatible`` deliberately gates cardio by the declared
    ``cardio_type`` and not by ``equipment_context`` — a home user who runs
    outdoors is a real product case. That carve-out is only sound while a
    cardio entry can only land on a ``kardiyo`` day: without this rule a
    ``ekipman="ev"`` plan could prescribe swimming inside a strength day and
    still persist under ``equipment_context: "ev"``, i.e. the equipment gate
    would be bypassable purely by placement.

    Deliberately one-directional. Forbidding a non-cardio exercise on a
    cardio day is a plan-quality opinion, not an authority question, and this
    boundary only answers authority questions.
    """
    if exercise.movement == CARDIO_MOVEMENT and day.get("tip") != CARDIO_TIP:
        raise GenerationExerciseIncompatibleError(
            "cardio exercise is placed on a day that is not a cardio day",
            resolution_category="cardio_placement")


def _resolve_chosen_id(
    exercise_id,
    catalog,
    choices: CompatibleExerciseChoices,
) -> ExerciseDefinition:
    """Validate one provider-chosen ID against the catalog and the closed set.

    Order is fixed so the typed outcome is deterministic: shape → exists →
    active → member of THIS request's choice set. Each failure carries a
    bounded category for diagnostics; the ID itself is never echoed.
    """
    if not isinstance(exercise_id, str) or not ID_PATTERN.fullmatch(exercise_id):
        raise GenerationExerciseIdentityInvalidError(
            "generated exercise_id is not a well-formed catalog ID",
            resolution_category="malformed_id")
    try:
        exercise = resolve_exercise(exercise_id=exercise_id, catalog=catalog)
    except ExerciseIdentityInvalid as exc:
        raise GenerationExerciseIdentityInvalidError(
            "generated exercise_id names no catalog entry",
            resolution_category="unknown_id") from exc
    except ExerciseInactive as exc:
        raise GenerationExerciseUnresolvedError(
            "generated exercise_id is not an active catalog entry",
            resolution_category="inactive_id") from exc
    except ExerciseResolutionError as exc:  # defensive: any future failure fails closed
        raise GenerationExerciseUnresolvedError(
            "generated exercise_id could not be resolved",
            resolution_category="resolution_error") from exc
    if exercise.exercise_id not in choices:
        raise GenerationExerciseIncompatibleError(
            "generated exercise_id is outside the request's compatible choices",
            resolution_category="outside_choice_set")
    return exercise


def canonicalize_generated_exercises(
    plan: dict,
    choices: CompatibleExerciseChoices,
) -> dict:
    """Turn a provider plan's chosen IDs into catalog-owned exercise entries.

    Must run after ``validate_generated_plan`` accepted ``plan`` (so every
    entry is exactly ``exercise_id``/``set``/``tekrar``/``dinlenme``/``not``)
    and with the SAME ``choices`` object the prompt was built from. For each
    entry: the ID is validated (``_resolve_chosen_id``), equipment
    compatibility is re-checked against ``choices.context`` (defence in depth
    — membership already implies it), cardio placement is enforced, and the
    persisted entry is rebuilt field by field: ``isim`` and ``exercise_id``
    from the catalog, prescription fields from the provider. Nothing else the
    provider wrote is carried over. Same output shape as
    ``canonicalize_plan_exercises``.
    """
    catalog = load_exercise_catalog()
    context = choices.context
    canonical_program = []
    for day in plan["program"]:
        canonical_exercises = []
        for exercise in day["egzersizler"]:
            resolved = _resolve_chosen_id(
                exercise.get(EXERCISE_ID_KEY), catalog, choices)
            if not is_exercise_compatible(resolved, context):
                raise GenerationExerciseIncompatibleError(
                    "generated exercise is not compatible with the accepted "
                    "equipment context",
                    resolution_category="equipment")
            check_placement(resolved, day)
            canonical_exercises.append({
                "isim": resolved.canonical_name,
                "set": exercise["set"],
                "tekrar": exercise["tekrar"],
                "dinlenme": exercise["dinlenme"],
                "not": exercise["not"],
                EXERCISE_ID_KEY: resolved.exercise_id,
            })
        canonical_program.append({**day, "egzersizler": canonical_exercises})
    return {**plan, "program": canonical_program}


def canonicalize_plan_exercises(plan: dict, context: ExerciseContext) -> dict:
    """Resolve every CLIENT plan exercise reference to catalog-owned identity.

    The save boundary's resolver (``validate_plan_for_save``). Generation
    uses ``canonicalize_generated_exercises`` instead: since PR B a provider
    plan carries no name to resolve.

    Must run after PR3 structural + semantic validation has already accepted
    ``plan``. For each exercise entry this writes only ``exercise_id``
    (the catalog's stable identity) and replaces ``isim`` with the catalog's
    canonical display name — a supplied display name is never preserved, so a
    valid ID plus a fabricated name still persists the catalog's name; every
    prescription field (``set``/``tekrar``/``dinlenme``/``not``) is carried
    over unchanged. Never adds catalog metadata (equipment/movement/region)
    to the plan — those stay server-side.

    Fails closed (raises a ``GenerationOutputError`` subclass) on the first
    exercise reference that does not resolve to exactly one active,
    ``context``-compatible catalog entry, or that is a cardio-movement entry
    placed on a day whose ``tip`` is not ``kardiyo`` (see
    ``check_placement``). No automatic substitution.
    """
    catalog = load_exercise_catalog()
    cache: dict[str, ExerciseDefinition] = {}
    canonical_program = []
    for day in plan["program"]:
        canonical_exercises = []
        for exercise in day["egzersizler"]:
            resolved = _resolve_plan_exercise(exercise, catalog, cache)
            if not is_exercise_compatible(resolved, context):
                raise GenerationExerciseIncompatibleError(
                    "generated exercise is not compatible with the accepted "
                    "equipment context",
                    resolution_category="equipment")
            check_placement(resolved, day)
            canonical_exercises.append({
                **exercise,
                "isim": resolved.canonical_name,
                "exercise_id": resolved.exercise_id,
            })
        canonical_program.append({**day, "egzersizler": canonical_exercises})
    return {**plan, "program": canonical_program}
