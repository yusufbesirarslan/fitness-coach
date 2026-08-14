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
"""
import copy
import json

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
    REST_TIP,
    normalize_exercise_name,
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


def _find_exercise_index(exercises, exercise_name):
    """The single index matching ``exercise_name``.

    Matching is case-insensitive and whitespace-insensitive because names are
    free text typed by humans and models. Two matches is refused, never resolved
    by position (see ``AmbiguousExerciseTarget``).
    """
    wanted = normalize_exercise_name(exercise_name)
    if wanted is None:
        raise InvalidMutation("exercise name is required")
    folded = wanted.casefold()
    hits = [
        index
        for index, entry in enumerate(exercises)
        if isinstance(entry, dict)
        and str(entry.get(FIELD_NAME) or "").strip().casefold() == folded
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

    if isinstance(command, ReplaceExerciseCommand):
        changed = _apply_replace(program, command)
    elif isinstance(command, AddExerciseCommand):
        changed = _apply_add(program, command)
    elif isinstance(command, RemoveExerciseCommand):
        changed = _apply_remove(program, command)
    elif isinstance(command, UpdateExercisePrescriptionCommand):
        changed = _apply_update(program, command)
    elif isinstance(command, MoveTrainingDayCommand):
        changed = _apply_move(program, command)
    else:  # pragma: no cover - the service refuses unknown types earlier
        raise InvalidMutation("unsupported command")

    return (working, True) if changed else (document, False)


def _apply_replace(program, command):
    replacement = normalize_exercise_name(command.replacement)
    if replacement is None:
        raise InvalidMutation("replacement name is required")

    day = _find_day(program, command.day)
    exercises = _exercises_of(day)
    index = _find_exercise_index(exercises, command.exercise)

    # Mutate the existing slot in place: position, rest, notes and any field
    # this PR does not model are inherited, not rebuilt (brief §9A).
    entry = exercises[index]
    updates = {FIELD_NAME: replacement}
    if command.sets is not None:
        updates[FIELD_SETS] = validate_sets(command.sets)
    if command.reps is not None:
        updates[FIELD_REPS] = validate_reps(command.reps)

    if all(entry.get(key) == value for key, value in updates.items()):
        return False
    entry.update(updates)
    return True


def _apply_add(program, command):
    name = normalize_exercise_name(command.exercise)
    if name is None:
        raise InvalidMutation("exercise name is required")
    if command.sets is None or command.reps is None:
        raise InvalidMutation("sets and reps are required when adding")
    sets = validate_sets(command.sets)
    reps = validate_reps(command.reps)

    day = _find_day(program, command.day)
    _require_workout_day(day)
    exercises = _exercises_of(day)
    exercises.append({FIELD_NAME: name, FIELD_SETS: sets, FIELD_REPS: reps})
    return True


def _apply_remove(program, command):
    day = _find_day(program, command.day)
    exercises = _exercises_of(day)
    index = _find_exercise_index(exercises, command.exercise)
    exercises.pop(index)
    _require_day_stays_valid(day)
    return True


def _apply_update(program, command):
    if command.sets is None and command.reps is None:
        raise InvalidMutation("no prescription field supplied")

    updates = {}
    if command.sets is not None:
        updates[FIELD_SETS] = validate_sets(command.sets)
    if command.reps is not None:
        updates[FIELD_REPS] = validate_reps(command.reps)

    day = _find_day(program, command.day)
    exercises = _exercises_of(day)
    entry = exercises[_find_exercise_index(exercises, command.exercise)]

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
