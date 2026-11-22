"""Read-only Coach view of the owner's canonical training plan.

Adaptive Coach Foundation V1, Training slice B1. Before this module the
Coach could *write* to the plan (six narrow tools, PR3/PR4) but could not
*see* it: the only plan-shaped context block was the AdaptivePlan guidance
projection, which carries a weekly focus and no day or exercise. The model
therefore had to name days and exercises from conversation memory, and the
server's grounding layer caught the misses after the fact. This module
closes that gap the narrow way:

* **One canonical read.** ``get_active_plan`` (the selector every reader
  uses) + ``parse_plan_document`` (the parser the mutation boundary uses).
  No second plan authority, no cache, no write, no journal access.
* **Ownership is structural.** The only input is the server-resolved
  ``user_id``; the tool schema takes no arguments, so a model cannot name a
  user, plan, lineage or version.
* **Closed projection.** Only weekday, day kind, catalog-resolved exercise
  name, set count and a bounded rep prescription leave this module. Free
  text (``odak``, ``not``, ``dinlenme``), the plan lineage/version and row ids
  never do. Exercise names are published only when the catalog vouches for
  them (the stored name if it is a declared name/alias, so Coach and Plan
  screen agree). An exercise the catalog cannot resolve is published as
  ``name: null`` so the model knows a slot exists that it cannot name — it
  must ask the user instead of guessing (PA06: stored prose fails closed).

Catalog V3 adds, for every slot, its ``position`` in the stored day (the
editor's row index) and — only when the stored ``exercise_id`` is a curated
catalog identity — ``exercise_id``, ``source``, ``family_id``,
``target_region`` and the variant ``equipment``. They come from the server's
own taxonomy for that ID, never from stored plan text, so an equipment change
made in the editor (Dumbbell → Machine) is what the next read reports. No
muscle-activation profile is published. Owner-private IDs are out of scope here.

``coach_privacy.tool_result`` re-projects this payload before it reaches a
provider; that is the transfer boundary. This module is the domain read.
"""
from app.services.exercise_catalog import (
    CatalogConfigurationError,
    ExerciseResolutionError,
    load_exercise_catalog,
    resolve_exercise,
)
from app.services.exercise_catalog_v3 import describe_exercise
from app.services.plan_mutation.document import (
    FIELD_DAY,
    FIELD_EXERCISE_ID,
    FIELD_EXERCISES,
    FIELD_KIND,
    FIELD_NAME,
    FIELD_REPS,
    FIELD_SETS,
    parse_plan_document,
)
from app.services.plan_mutation.errors import PlanNotMutable
from app.services.plan_mutation.validation import (
    MAX_SETS,
    MIN_SETS,
    VALID_TIPS,
    WEEKDAYS,
)
from app.services.today_facts import get_active_plan

READ_PLAN_TOOL = "get_current_training_plan"

STATUS_OK = "ok"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"
ERROR_PLAN_UNREADABLE = "PLAN_UNREADABLE"

#: Projection ceilings. A generated day holds well under this; the cap keeps
#: the tool result (billed provider input) bounded by construction.
MAX_PROJECTED_EXERCISES_PER_DAY = 20

READ_PLAN_TOOL_DEF = {
    "name": READ_PLAN_TOOL,
    "description": (
        "Read the user's CURRENT saved training plan (weekday, day type, "
        "exercises with sets and reps). Call this before you describe the "
        "plan, name a day or exercise, or propose or make any plan change. "
        "Never describe plan contents from memory. Takes no arguments; it "
        "only reads and never changes anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def _reps(value):
    """A bounded rep prescription string, or None (never invented)."""
    if not isinstance(value, str):
        return None
    reps = value.strip()
    if not reps or len(reps) > 40:
        return None
    return reps


def _sets(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_SETS <= value <= MAX_SETS else None


def _exercise_name(exercise, catalog):
    """The name the user's Plan screen shows, when the catalog vouches for it.

    The stored ``isim`` is published only if it resolves in the exercise
    catalog (a declared name or alias), so the Coach and the Plan surface say
    the same words. A slot whose stored name is not catalog-known falls back
    to the canonical name of its stable ``exercise_id``; otherwise ``None``.
    """
    name = exercise.get(FIELD_NAME)
    if isinstance(name, str) and len(name) <= 120:
        try:
            resolve_exercise(name=name, catalog=catalog)
            return name
        except ExerciseResolutionError:
            pass
    exercise_id = exercise.get(FIELD_EXERCISE_ID)
    if isinstance(exercise_id, str):
        try:
            return resolve_exercise(
                exercise_id=exercise_id, catalog=catalog).canonical_name
        except ExerciseResolutionError:
            pass
    return None


def _curated_identity(exercise):
    """V3 identity fields for a curated stored ID, else nothing.

    The V3 layer is additive: an unloadable overlay drops these fields (the
    pre-V3 read) instead of making the whole plan unreadable.
    """
    try:
        descriptor = describe_exercise(exercise.get(FIELD_EXERCISE_ID))
    except CatalogConfigurationError:
        return {}
    if descriptor is None:
        return {}
    return {key: descriptor[key] for key in (
        "exercise_id", "source", "family_id", "target_region", "equipment")}


def project_program(program, catalog=None):
    """Pure projection of a parsed program into the closed read shape."""
    catalog = catalog or load_exercise_catalog()
    by_day = {}
    for day in program:
        name = day.get(FIELD_DAY)
        if name not in WEEKDAYS or name in by_day:
            raise PlanNotMutable("plan has an unrecognised or duplicate day")
        kind = day.get(FIELD_KIND)
        raw = day.get(FIELD_EXERCISES) or []
        if not isinstance(raw, list):
            raise PlanNotMutable("day has a malformed exercise list")
        exercises = []
        for position, exercise in enumerate(raw[:MAX_PROJECTED_EXERCISES_PER_DAY]):
            if not isinstance(exercise, dict):
                raise PlanNotMutable("day has a malformed exercise")
            projected = {
                "name": _exercise_name(exercise, catalog),
                "sets": _sets(exercise.get(FIELD_SETS)),
                "reps": _reps(exercise.get(FIELD_REPS)),
                "position": position,
            }
            projected.update(_curated_identity(exercise))
            exercises.append(projected)
        entry = {
            "day": name,
            "kind": kind if kind in VALID_TIPS else None,
            "exercises": exercises,
        }
        if len(raw) > MAX_PROJECTED_EXERCISES_PER_DAY:
            entry["truncated"] = True
        by_day[name] = entry
    # Calendar order, independent of storage order.
    return [by_day[name] for name in WEEKDAYS if name in by_day]


def read_current_training_plan(user_id):
    """The owner's current plan as a closed, bounded projection.

    Never raises for an unreadable stored plan: the Coach is told the plan
    cannot be read (and must not describe it) rather than receiving a guess.
    """
    plan = get_active_plan(user_id)
    if plan is None:
        return {"status": STATUS_NOT_FOUND}
    try:
        _document, program = parse_plan_document(plan.plan_data)
        days = project_program(program)
    except PlanNotMutable:
        return {"status": STATUS_ERROR, "error": ERROR_PLAN_UNREADABLE}
    return {"status": STATUS_OK, "plan": {"days": days}}
