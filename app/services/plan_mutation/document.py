"""The PURE targeted-mutation engine (brief §10).

No ORM, no Flask, no database, no I/O — a plan document in, a plan document out.
That split is the same one ``workout_state/resolver.py`` and
``workout_session/models.py`` use, and it is what lets the whole mutation matrix
be exercised without a database.

How "targeted" is actually guaranteed
-------------------------------------
The canonical plan is one JSON text column, so *persisting* any change rewrites
the whole column. The invariant therefore has to hold one level up, in the
document: every operation here deep-copies the parsed document, reaches exactly
one node, and mutates that node in place. Untouched days and untouched exercises
are the *same objects* that were parsed, re-serialized in the same order with the
same keys — including keys this PR knows nothing about. Nothing is rebuilt from a
projection, which is what would silently drop an unknown field.

The plan is never regenerated, no field is recomputed, and derived plan-level
values (``haftalik_ozet`` and friends) are left exactly as the generator wrote
them: recomputing a weekly summary from a single exercise swap would be this
boundary inventing planning authority it does not have.

Two documents, one engine (Sprint 11 PR4)
-----------------------------------------
A plan saved through the Task 4 boundary carries the VERIFIED
``exercise_context`` the server accepted for it. When that block is present
this engine stops treating ``isim`` as identity: the command's target name is
resolved to a catalog entry and matched against the one slot holding that
stable ``exercise_id``, and an added or replacing exercise is written as
catalog identity (``exercise_id`` plus the canonical ``isim``) or not written
at all. Resolution is exact — canonical name or declared alias after
normalization — with no fuzzy matching and no automatic substitution, ever.

When the block is absent the document is a legacy, name-only plan and every
line below behaves exactly as it did before: casefold name matching, free-form
names written through, no ``exercise_id`` ever introduced. A legacy plan is
never silently upgraded, and — the other direction, which is the dangerous one
— a canonical plan whose context block is unreadable is never silently
downgraded: it is refused.
"""
import copy
from dataclasses import dataclass
import json

from app.services.exercise_catalog import (
    ExerciseCatalog,
    ExerciseContext,
    ExerciseDefinition,
    ExerciseResolutionError,
    is_exercise_compatible,
    load_exercise_catalog,
    resolve_exercise,
)
from app.services.training_generation.exercise_resolution import (
    check_placement,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseIncompatibleError,
)

from .commands import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)
from .errors import (
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseNotFound,
    InvalidMutation,
    PlanNotMutable,
)
from .validation import (
    FIELD_EXERCISE_CONTEXT,
    REST_TIP,
    normalize_exercise_name,
    validate_exercise_context,
    validate_reps,
    validate_sets,
)


#: Canonical field names inside ``plan_data``. One definition, so a rename is a
#: single edit rather than a hunt through string literals.
FIELD_DAY = "gun"
FIELD_KIND = "tip"
FIELD_EXERCISES = "egzersizler"
FIELD_NAME = "isim"
FIELD_SETS = "set"
FIELD_REPS = "tekrar"
#: Catalog-owned stable identity, written by Task 3/4 and — from here on — by
#: this engine. Same literal as ``plan_schema.EXERCISE_ID_KEY``; named here
#: alongside the other document fields this module reads and writes.
FIELD_EXERCISE_ID = "exercise_id"


@dataclass(frozen=True)
class _ExerciseAuthority:
    """The catalog and the verified context, resolved once per command.

    Built at most once inside ``apply_command`` and threaded down, so a
    mutation performs exactly one catalog load no matter how many exercises
    it touches, and every resolution inside one command is answered by the
    same catalog object.
    """

    catalog: ExerciseCatalog
    context: ExerciseContext


def parse_plan_document(plan_text):
    """Parse stored ``plan_data`` into ``(document, program)``.

    Accepts both canonical shapes — a bare list of days, or an object with a
    ``program`` list — exactly as every existing reader does. Anything else
    raises ``PlanNotMutable``: PR1 refuses to guess at, repair, or regenerate a
    plan it cannot recognise.
    """
    try:
        document = json.loads(plan_text)
    except (TypeError, ValueError):
        raise PlanNotMutable("plan_data is not valid JSON")

    if isinstance(document, list):
        program = document
    elif isinstance(document, dict):
        program = document.get("program")
    else:
        raise PlanNotMutable("plan_data is not a plan document")

    if not isinstance(program, list) or not program:
        raise PlanNotMutable("plan_data has no program")
    if not all(isinstance(day, dict) for day in program):
        raise PlanNotMutable("plan_data has a malformed day")
    return document, program


def serialize_plan_document(document):
    """Serialize a mutated document with the repository's existing encoding.

    ``ensure_ascii=False`` matches the only pre-existing writer
    (``POST /training-plan/save``), so Turkish weekday and exercise names stay
    readable in storage instead of becoming escape sequences.
    """
    return json.dumps(document, ensure_ascii=False)


def _program_of(document):
    return document if isinstance(document, list) else document.get("program")


def _find_day(program, day_name):
    """The one day entry whose ``gun`` matches, or ``DayNotFound``."""
    if not isinstance(day_name, str) or not day_name.strip():
        raise InvalidMutation("day is required")
    wanted = day_name.strip()
    matches = [day for day in program if day.get(FIELD_DAY) == wanted]
    if not matches:
        raise DayNotFound("target day is not in the plan")
    if len(matches) > 1:
        # The generator guarantees unique weekdays; a duplicate means the stored
        # plan is not a plan this boundary can safely target.
        raise PlanNotMutable("plan has duplicate day identities")
    return matches[0]


def _exercises_of(day):
    """The day's exercise list, materializing an absent one.

    Materializing writes into ``day`` — which is safe because ``day`` always
    belongs to the deep copy, and because no path can both materialize the list
    and report "unchanged": the only operation that succeeds on an empty list is
    ``add`` (which then reports changed), while every other operation raises
    ``ExerciseNotFound`` and discards the copy.
    """
    exercises = day.get(FIELD_EXERCISES)
    if exercises is None:
        exercises = []
        day[FIELD_EXERCISES] = exercises
    if not isinstance(exercises, list):
        raise PlanNotMutable("day has a malformed exercise list")
    return exercises


def _document_carries_exercise_identity(document):
    """A pure structural walk: does any exercise anywhere carry an ``exercise_id``?

    No parsing assumptions beyond ``dict``/``list`` — a bare list, a dict
    without ``program``, a day that is not a dict, or a day without
    ``egzersizler`` must not raise here, they must simply contribute no IDs.
    This is a structural check only: it never loads the catalog and never
    resolves a name, so it costs nothing beyond the walk itself.
    """
    if isinstance(document, list):
        program = document
    elif isinstance(document, dict):
        program = document.get("program")
    else:
        return False
    if not isinstance(program, list):
        return False
    for day in program:
        if not isinstance(day, dict):
            continue
        exercises = day.get(FIELD_EXERCISES)
        if not isinstance(exercises, list):
            continue
        for entry in exercises:
            if isinstance(entry, dict) and FIELD_EXERCISE_ID in entry:
                return True
    return False


def _exercise_authority(document):
    """The catalog authority for this document, or ``None`` for a legacy plan.

    Present  — a dict document carrying an ``exercise_context`` block.
    Absent   — a bare-list legacy document, or a dict without that key —
               PROVIDED no exercise anywhere in it carries an ``exercise_id``.
               A document that bears catalog identity but no context to prove
               who authorized it is not legacy, it is unusable: see Unusable.
    Unusable — either the key is there but the block is not one the save
               boundary could have written, or the key is missing while some
               exercise still carries an ``exercise_id``. Both raise
               ``InvalidMutation``, never a quiet fall back to legacy name
               matching. Degrading silently would mean one corrupt byte in
               the context block — or one write that dropped the block
               entirely — is enough to take a canonical plan out from under
               the catalog's authority, reopening the exact defect (P1-4)
               this task closed: a stray ``exercise_id`` surviving a
               legacy-mode name rewrite it no longer matches.

    This is the only place ``load_exercise_catalog`` is called, and it runs
    once per command. The loader is ``lru_cache``d over a bundled data file,
    so it is not I/O in the sense this pure layer forbids — but calling it
    per exercise would still be wrong, and
    tests/test_plan_mutation_architecture.py pins the single call site.
    """
    if not isinstance(document, dict) or FIELD_EXERCISE_CONTEXT not in document:
        if _document_carries_exercise_identity(document):
            raise InvalidMutation("stored exercise context is not canonical")
        return None
    context = validate_exercise_context(document[FIELD_EXERCISE_CONTEXT])
    return _ExerciseAuthority(catalog=load_exercise_catalog(), context=context)


def _resolve_exercise_name(authority, name) -> ExerciseDefinition:
    """Resolve one name to catalog identity, or refuse the whole mutation.

    Exact resolution only: an active entry's canonical name or one of its
    declared aliases, after normalization. No stemming, no token deletion, no
    nearest match — a name the catalog does not declare is simply not an
    exercise, and guessing which one was meant is exactly the substitution
    this PR forbids.

    Every ``ExerciseResolutionError`` becomes ``InvalidMutation`` here.
    ``errors.py`` is a published internal contract and gains no new class; a
    raw domain ``ValueError`` escaping this pure layer would reach the service
    with no handler and surface to the user as a 500.
    """
    try:
        return resolve_exercise(name=name, catalog=authority.catalog)
    except ExerciseResolutionError as exc:
        raise InvalidMutation(
            "exercise name is not canonical catalog identity") from exc


def _resolve_placeable_exercise(authority, name, day) -> ExerciseDefinition:
    """Resolve a name that is about to be WRITTEN into ``day``.

    Two gates, both the catalog's own and neither re-implemented here:

    * ``is_exercise_compatible`` against the plan's verified context, so the
      Coach cannot put a barbell into a bodyweight plan;
    * ``check_placement``, the generation path's cardio-vs-day rule, reused
      rather than copied. ``is_exercise_compatible`` deliberately gates cardio
      by ``cardio_type`` instead of by equipment (a home user who runs
      outdoors is a real case), which is only sound while a cardio movement
      can land on a ``kardiyo`` day and nowhere else. Task 4 closed that hole
      on the save door; leaving it open here would just move the same exploit
      to the Adaptive Coaching door and make PR4's central claim false.

    Importing the generation module's private helper is deliberate: the rule
    has to have exactly one definition, and Tasks 1-4 are accepted and not to
    be edited. A second copy of the predicate here is the failure mode worth
    avoiding — two doors drifting apart is how the first hole was opened.
    """
    exercise = _resolve_exercise_name(authority, name)
    if not is_exercise_compatible(exercise, authority.context):
        raise InvalidMutation(
            "exercise is not compatible with the plan's equipment context")
    try:
        check_placement(exercise, day)
    except GenerationExerciseIncompatibleError as exc:
        raise InvalidMutation(
            "cardio exercise cannot be placed on a non-cardio day") from exc
    return exercise


def _find_exercise_index(exercises, exercise_name, authority):
    """The single index matching ``exercise_name``.

    On a LEGACY document, matching is case-insensitive and
    whitespace-insensitive because names are free text typed by humans and
    models, and the name is all the identity there is.

    On a CANONICAL document the name is resolved to a catalog entry first and
    the slot is found by that entry's stable ``exercise_id``. Two entries
    worded differently that resolve to the same catalog entry are therefore
    the same exercise twice, and are refused as ambiguous — casefold matching
    would have seen two unrelated names and edited one of them. There is no
    fall back to name matching when the ID does not appear: on a plan the
    catalog owns, an entry it does not own is not a target.

    Two matches is refused either way, never resolved by position (see
    ``AmbiguousExerciseTarget``).

    ``authority`` has no default here or on any ``_apply_*`` helper: a
    forgotten argument would silently pick LEGACY name matching on a
    canonical plan, which is the same downgrade ``_exercise_authority``
    refuses to perform on a broken context block.
    """
    wanted = normalize_exercise_name(exercise_name)
    if wanted is None:
        raise InvalidMutation("exercise name is required")
    if authority is None:
        folded = wanted.casefold()
        hits = [
            index
            for index, entry in enumerate(exercises)
            if isinstance(entry, dict)
            and str(entry.get(FIELD_NAME) or "").strip().casefold() == folded
        ]
    else:
        target = _resolve_exercise_name(authority, wanted)
        hits = [
            index
            for index, entry in enumerate(exercises)
            if isinstance(entry, dict)
            and entry.get(FIELD_EXERCISE_ID) == target.exercise_id
        ]
    if not hits:
        raise ExerciseNotFound("target exercise is not in the day")
    if len(hits) > 1:
        raise AmbiguousExerciseTarget("target exercise matches more than once")
    return hits[0]


def _require_workout_day(day):
    """Refuse to put exercises on an explicit rest day."""
    if day.get(FIELD_KIND) == REST_TIP:
        raise InvalidMutation("cannot add an exercise to a rest day")


def _require_day_stays_valid(day):
    """A non-rest day must keep at least one exercise (canonical rule)."""
    if day.get(FIELD_KIND) == REST_TIP:
        return
    if not _exercises_of(day):
        raise InvalidMutation("a training day must keep at least one exercise")


def apply_command(document, command):
    """Apply ``command`` to a copy of ``document``.

    Returns ``(new_document, changed)``. ``changed`` is False when the requested
    desired state already held — a deterministic no-op, with the *original*
    document returned so the caller can skip the write entirely (brief §14).

    The copy is taken up-front and every validation happens against it, so a
    command that fails half-way leaves the caller's document untouched and there
    is no partially-mutated state to roll back.
    """
    working = copy.deepcopy(document)
    program = _program_of(working)
    # Resolved once, before any branch: the context decides how EVERY command
    # below reads identity, and a command that touches no exercise at all
    # (a day move) must still be refused on a plan whose context is broken.
    authority = _exercise_authority(working)

    if isinstance(command, ReplaceExerciseCommand):
        changed = _apply_replace(program, command, authority)
    elif isinstance(command, AddExerciseCommand):
        changed = _apply_add(program, command, authority)
    elif isinstance(command, RemoveExerciseCommand):
        changed = _apply_remove(program, command, authority)
    elif isinstance(command, UpdateExercisePrescriptionCommand):
        changed = _apply_update(program, command, authority)
    elif isinstance(command, MoveTrainingDayCommand):
        changed = _apply_move(program, command)
    else:  # pragma: no cover - the service refuses unknown types earlier
        raise InvalidMutation("unsupported command")

    return (working, True) if changed else (document, False)


def _apply_replace(program, command, authority):
    replacement = normalize_exercise_name(command.replacement)
    if replacement is None:
        raise InvalidMutation("replacement name is required")

    day = _find_day(program, command.day)
    exercises = _exercises_of(day)
    index = _find_exercise_index(exercises, command.exercise, authority)

    # Mutate the existing slot in place: position, rest, notes and any field
    # this PR does not model are inherited, not rebuilt (brief §9A).
    entry = exercises[index]
    updates = {FIELD_NAME: replacement}
    if authority is not None:
        # Name and identity are written as ONE update, which is what closes
        # P1-4: the pre-PR4 code rewrote ``isim`` alone and left
        # ``exercise_id`` pointing at the exercise that used to be here.
        # Because both land in ``updates``, the no-op short-circuit below
        # also still holds — replacing an exercise with an alias of itself
        # resolves back to the same pair and stays a deterministic no-op.
        resolved = _resolve_placeable_exercise(authority, replacement, day)
        updates[FIELD_NAME] = resolved.canonical_name
        updates[FIELD_EXERCISE_ID] = resolved.exercise_id
    if command.sets is not None:
        updates[FIELD_SETS] = validate_sets(command.sets)
    if command.reps is not None:
        updates[FIELD_REPS] = validate_reps(command.reps)

    if all(entry.get(key) == value for key, value in updates.items()):
        return False
    entry.update(updates)
    return True


def _apply_add(program, command, authority):
    name = normalize_exercise_name(command.exercise)
    if name is None:
        raise InvalidMutation("exercise name is required")
    if command.sets is None or command.reps is None:
        raise InvalidMutation("sets and reps are required when adding")
    sets = validate_sets(command.sets)
    reps = validate_reps(command.reps)

    day = _find_day(program, command.day)
    _require_workout_day(day)
    entry = {FIELD_NAME: name, FIELD_SETS: sets, FIELD_REPS: reps}
    if authority is not None:
        resolved = _resolve_placeable_exercise(authority, name, day)
        entry[FIELD_NAME] = resolved.canonical_name
        # Appended last, matching the key order canonicalization produces on
        # the generate/save path, so a mutated canonical plan and a freshly
        # saved one serialize the same way.
        entry[FIELD_EXERCISE_ID] = resolved.exercise_id
    exercises = _exercises_of(day)
    exercises.append(entry)
    return True


def _apply_remove(program, command, authority):
    day = _find_day(program, command.day)
    exercises = _exercises_of(day)
    index = _find_exercise_index(exercises, command.exercise, authority)
    exercises.pop(index)
    _require_day_stays_valid(day)
    return True


def _apply_update(program, command, authority):
    if command.sets is None and command.reps is None:
        raise InvalidMutation("no prescription field supplied")

    updates = {}
    if command.sets is not None:
        updates[FIELD_SETS] = validate_sets(command.sets)
    if command.reps is not None:
        updates[FIELD_REPS] = validate_reps(command.reps)

    day = _find_day(program, command.day)
    exercises = _exercises_of(day)
    # Identity is only READ here. A prescription change is not an identity
    # change, so ``exercise_id`` and ``isim`` are both left exactly alone.
    entry = exercises[
        _find_exercise_index(exercises, command.exercise, authority)]

    if all(entry.get(key) == value for key, value in updates.items()):
        return False
    entry.update(updates)
    return True


def _apply_move(program, command):
    source = _find_day(program, command.day)
    target = _find_day(program, command.target_day)
    if source is target:
        return False

    # Exchange content, not identity: each entry keeps its own ``gun`` so the
    # weekday calendar is never renamed and never reordered.
    source_label = source.get(FIELD_DAY)
    target_label = target.get(FIELD_DAY)
    source_content = {k: v for k, v in source.items() if k != FIELD_DAY}
    target_content = {k: v for k, v in target.items() if k != FIELD_DAY}

    source.clear()
    source[FIELD_DAY] = source_label
    source.update(target_content)

    target.clear()
    target[FIELD_DAY] = target_label
    target.update(source_content)
    return True
