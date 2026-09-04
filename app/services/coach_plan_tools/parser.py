"""LLM arguments → typed PR1 command, or a refusal. Nothing in between.

This is the only place a model-supplied value crosses into the domain, and it
is written on the assumption that the provider will not honour the JSON schema
(brief §37). A schema is a hint to the model; a parser is a boundary.

What it refuses, and why each one matters:

* **an unknown property** — silently dropping ``{"day": "Cuma", "sets": 4}`` on
  a remove call would execute a *different* request than the model expressed,
  and neither the user nor the model would learn that (brief §38);
* **a missing required property** — no defaulting. The generator fills gaps in
  LLM output because a slightly-wrong plan beats no plan; a mutation that
  invents "3 sets" the user never asked for is a fabricated instruction;
* **a wrong type** — including ``bool``, which is an ``int`` subclass and would
  otherwise arrive as "1 set";
* **an empty/blank string** — a target nobody named.

With one bounded exception, ``GROUNDABLE``. A few properties are not the
model's to know: which weekday "my leg workout" is, and whether the user
already said "4 sets". The server resolves those against the persisted plan
and the user's own words in ``grounding.py``, which is *also* the only layer
that can store what it already grounded and ask for the rest. Refusing the
call here instead throws that partial state away before it can be stored,
and the next turn then has nothing but the model's memory to run on — which
is exactly how "add Walking Lunges with 4 sets" came back as ``3x15``.

So an absent-or-blank value for a groundable property is recorded as *not
supplied* and handed to grounding, which fills it or asks for it. This is
not defaulting: nothing is invented here, and a value that is present but
of the wrong type is still refused, because "sets: '3'" is a model defect
rather than a gap the user can close.

What it deliberately does NOT do is re-implement the domain's bounds. Set and
rep *values* are range-checked by ``plan_mutation.validation`` when the command
is applied; duplicating those numbers here would create a second authority that
could disagree with the one that actually rejects. This layer settles shape and
presence; the domain settles legality.

Refusals raise ``ToolArgumentError``, which the executor turns into a bounded
``INVALID_ARGUMENTS`` result. No mutation has been attempted at that point and
no operation identity has been claimed.
"""
from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)

from .schemas import (
    ADD_EXERCISE_TOOL,
    MOVE_DAY_TOOL,
    REMOVE_EXERCISE_TOOL,
    REPLACE_EXERCISE_TOOL,
    UPDATE_PRESCRIPTION_TOOL,
)


class ToolArgumentError(ValueError):
    """The model's arguments cannot become a typed command.

    Carries a short, server-authored reason for the model. It never echoes a
    stack trace, a domain exception string, or anything about storage.
    """


#: ``{tool name: (required, optional)}``. One table, so the accepted property
#: set and the schema published to the provider cannot drift apart —
#: a test compares this against ``PLAN_MUTATION_TOOL_DEFS`` directly.
TOOL_ARGUMENTS = {
    REPLACE_EXERCISE_TOOL: (("day", "exercise", "replacement"),
                            ("sets", "reps")),
    ADD_EXERCISE_TOOL: (("day", "exercise", "sets", "reps"), ()),
    REMOVE_EXERCISE_TOOL: (("day", "exercise"), ()),
    UPDATE_PRESCRIPTION_TOOL: (("day", "exercise"), ("sets", "reps")),
    MOVE_DAY_TOOL: (("day", "target_day"), ()),
}

#: ``{tool name: properties the SERVER can resolve}``. Absent or blank here is
#: "not supplied", not a refusal — see the module docstring. Kept deliberately
#: narrow:
#:
#: * ``day`` on add/replace/update, because ``workout_targets`` resolves a
#:   weekday, a nickname ("my leg workout") or a unique exercise slot against
#:   the persisted plan, and ``grounding`` asks the user when it cannot;
#: * ``sets``/``reps`` on add, because grounding stores the half it has and
#:   asks for the other half.
#:
#: ``remove`` and ``move`` are NOT groundable: neither has a continuation path
#: (``grounding._OPERATION_TOOLS`` cannot re-issue them), so a stored
#: clarification for one could never be completed. ``exercise``,
#: ``replacement`` and ``target_day`` are never groundable — a target nobody
#: named is the one thing this boundary exists to refuse.
GROUNDABLE = {
    REPLACE_EXERCISE_TOOL: frozenset({"day"}),
    ADD_EXERCISE_TOOL: frozenset({"day", "sets", "reps"}),
    UPDATE_PRESCRIPTION_TOOL: frozenset({"day"}),
    REMOVE_EXERCISE_TOOL: frozenset(),
    MOVE_DAY_TOOL: frozenset(),
}

#: Which properties are text and which are whole numbers. Used for the shape
#: check only — ranges belong to the domain.
_TEXT_FIELDS = frozenset(
    {"day", "target_day", "exercise", "replacement", "reps"})
_INT_FIELDS = frozenset({"sets"})


def _absent(value):
    """Whether the model supplied nothing usable for this property.

    ``None`` and a blank string only. ``0`` and ``False`` are values the model
    *chose*, and collapsing them to "not supplied" would silently execute a
    different request than the one it expressed — ``sets: 0`` is refused by the
    domain's range check, which is the honest answer.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _text(name, value):
    if not isinstance(value, str):
        raise ToolArgumentError(f"'{name}' metin olmalı")
    text = value.strip()
    if not text:
        raise ToolArgumentError(f"'{name}' boş olamaz")
    return text


def _whole_number(name, value):
    # bool first: True would otherwise pass isinstance(..., int) as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError(f"'{name}' tam sayı olmalı")
    return value


def _coerce(name, value):
    if name in _TEXT_FIELDS:
        return _text(name, value)
    if name in _INT_FIELDS:
        return _whole_number(name, value)
    # Unreachable while TOOL_ARGUMENTS and the field sets agree; a test pins
    # that they do, and reaching here would mean a property with no declared
    # type — refuse rather than pass an unchecked value to the domain.
    raise ToolArgumentError(f"'{name}' desteklenmiyor")


def parse_tool_arguments(name, arguments):
    """Return ``{field: value}`` for ``name``, or raise ``ToolArgumentError``.

    ``arguments`` is whatever the provider produced — already decoded to a
    Python object by the Coach's dispatcher. A non-object is refused rather
    than coerced: ``null`` and ``"day=Cuma"`` are both "the model did not send
    arguments", not "the model sent empty arguments".
    """
    try:
        required, optional = TOOL_ARGUMENTS[name]
    except KeyError:
        raise ToolArgumentError("bilinmeyen plan aracı") from None

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolArgumentError("araç argümanları bir nesne olmalı")

    accepted = set(required) | set(optional)
    unexpected = sorted(set(arguments) - accepted)
    if unexpected:
        # Named, not silently dropped: the model must be able to correct
        # itself, and a dropped field can change what the call means.
        raise ToolArgumentError(
            "beklenmeyen alan: " + ", ".join(unexpected))

    groundable = GROUNDABLE.get(name, frozenset())
    # A blank NON-groundable required field is still "a target nobody named",
    # not "not supplied": it reaches ``_coerce`` and earns the precise
    # "boş olamaz" the model can act on.
    missing = [field for field in required
               if arguments.get(field) is None and field not in groundable]
    if missing:
        raise ToolArgumentError("eksik alan: " + ", ".join(missing))

    parsed = {}
    for field in required:
        if field in groundable and _absent(arguments.get(field)):
            # The server grounds this one. Leaving it out of ``parsed`` is how
            # "not supplied" is spelled to ``build_command``.
            continue
        parsed[field] = _coerce(field, arguments[field])
    for field in optional:
        # An explicit null — or a blank string, which providers emit for the
        # same thing — means "not supplied", which is how the typed command
        # already spells an absent override.
        if _absent(arguments.get(field)):
            continue
        parsed[field] = _coerce(field, arguments[field])
    return parsed


def build_command(name, arguments):
    """Return the typed PR1 command for ``name``, or raise.

    Every branch constructs one known frozen dataclass by keyword. There is no
    dynamic dispatch on a model-supplied string, no ``**arguments`` splat into
    a constructor, and no path that can produce a command type this function
    does not name — that is what makes "the model cannot reach an arbitrary
    mutation" structural rather than a review promise.
    """
    fields = parse_tool_arguments(name, arguments)

    if name == REPLACE_EXERCISE_TOOL:
        return ReplaceExerciseCommand(
            day=fields.get("day", ""),
            exercise=fields["exercise"],
            replacement=fields["replacement"],
            sets=fields.get("sets"),
            reps=fields.get("reps"),
        )
    if name == ADD_EXERCISE_TOOL:
        return AddExerciseCommand(
            day=fields.get("day", ""),
            exercise=fields["exercise"],
            sets=fields.get("sets"),
            reps=fields.get("reps"),
        )
    if name == REMOVE_EXERCISE_TOOL:
        return RemoveExerciseCommand(
            day=fields["day"], exercise=fields["exercise"])
    if name == UPDATE_PRESCRIPTION_TOOL:
        if "sets" not in fields and "reps" not in fields:
            # The domain refuses this too. Refusing here as well means the
            # model gets a usable correction instead of a generic rejection,
            # and no operation identity is spent on an empty command.
            raise ToolArgumentError("set veya tekrar'dan en az biri gerekli")
        return UpdateExercisePrescriptionCommand(
            day=fields.get("day", ""),
            exercise=fields["exercise"],
            sets=fields.get("sets"),
            reps=fields.get("reps"),
        )
    if name == MOVE_DAY_TOOL:
        return MoveTrainingDayCommand(
            day=fields["day"], target_day=fields["target_day"])

    raise ToolArgumentError("bilinmeyen plan aracı")
