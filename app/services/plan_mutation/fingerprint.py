"""Versioned semantic fingerprints for typed plan-mutation commands.

Pure: a command in, a hex digest out. No ORM, no Flask, no clock, no randomness —
the same command must fingerprint identically in two processes, on two machines,
years apart, or replay silently stops working.

What is hashed is an *explicitly constructed* semantic payload, never
``repr()``, never a raw request body, never prompt or display text. Two calls
that mean the same thing must collapse to one digest; two calls that mean
different things must not.

Normalization (brief §18) mirrors how ``document.py`` actually resolves a target,
so the fingerprint agrees with the mutation it protects:

* ``day`` / ``target_day`` — stripped. ``_find_day`` compares the stripped label
  exactly, so case is meaning here.
* the *target* exercise of replace/remove/update — stripped and casefolded,
  because ``_find_exercise_index`` matches that way; "bench press" and
  "Bench  Press " address the same slot and must replay, not conflict.
* a *stored* name (``replacement``, and ``exercise`` on an add) — stripped only.
  That value is written into the plan verbatim, so "Machine Press" and
  "machine press" are genuinely different intents.
* ``reps`` — stripped (``validate_reps`` stores the stripped form); ``sets`` —
  the integer, with ``None`` preserved as "field not supplied", which differs
  from supplying the value the plan already holds.

Nothing here authorizes anything. A digest proves two requests mean the same
thing; ownership is still the owner-scoped plan query.
"""
import hashlib
import json

from .commands import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)


#: Domain separation + representation version. A change to which fields
#: participate, or to the normalization above, requires a NEW version rather
#: than silently redefining what an existing stored digest meant.
FINGERPRINT_DOMAIN = "axisai/training-plan-mutation/v1"

#: Stable command identities. Persisted, so they are part of the audit contract
#: and never derived from a class name that a refactor could rename.
COMMAND_TYPES = {
    ReplaceExerciseCommand: "replace_exercise",
    AddExerciseCommand: "add_exercise",
    RemoveExerciseCommand: "remove_exercise",
    UpdateExercisePrescriptionCommand: "update_exercise_prescription",
    MoveTrainingDayCommand: "move_training_day",
}

#: The compensating operation is fingerprinted too, so that reusing a mutation's
#: key for an undo is a deterministic conflict rather than an accidental replay.
UNDO_COMMAND_TYPE = "undo_last_change"


def command_type(command) -> str:
    """The stable persisted identity of a typed command."""
    try:
        return COMMAND_TYPES[type(command)]
    except KeyError:
        raise TypeError("unsupported mutation command") from None


def _label(value):
    """A stripped label, or ``None`` when the value is not usable text.

    Deliberately total. The fingerprint is computed before the document layer
    validates, so a malformed command must still hash deterministically instead
    of raising here — it is rejected a moment later, and by then it has claimed
    no operation identity.
    """
    return value.strip() if isinstance(value, str) else None


def _target(value):
    """A stripped, casefolded exercise *target* — how the document matches."""
    label = _label(value)
    return None if label is None else label.casefold()


def _sets(value):
    """The integer set count, or ``None``. ``bool`` is not an int here."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _semantic_payload(command):
    kind = command_type(command)
    if isinstance(command, ReplaceExerciseCommand):
        body = {
            "day": _label(command.day),
            "exercise": _target(command.exercise),
            "replacement": _label(command.replacement),
            "sets": _sets(command.sets),
            "reps": _label(command.reps),
        }
    elif isinstance(command, AddExerciseCommand):
        body = {
            "day": _label(command.day),
            "exercise": _label(command.exercise),
            "sets": _sets(command.sets),
            "reps": _label(command.reps),
        }
    elif isinstance(command, RemoveExerciseCommand):
        body = {
            "day": _label(command.day),
            "exercise": _target(command.exercise),
        }
    elif isinstance(command, UpdateExercisePrescriptionCommand):
        body = {
            "day": _label(command.day),
            "exercise": _target(command.exercise),
            "sets": _sets(command.sets),
            "reps": _label(command.reps),
        }
    elif isinstance(command, MoveTrainingDayCommand):
        body = {
            "day": _label(command.day),
            "target_day": _label(command.target_day),
        }
    else:  # pragma: no cover - command_type already refused it
        raise TypeError("unsupported mutation command")
    return {"domain": FINGERPRINT_DOMAIN, "kind": kind, **body}


def _digest(payload):
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def semantic_fingerprint(command) -> str:
    """The versioned semantic digest of one typed mutation command."""
    return _digest(_semantic_payload(command))


def undo_fingerprint() -> str:
    """The digest every ``undo_last_change`` request shares.

    Undo takes no arguments: its meaning is entirely "reverse the latest
    reversible mutation of my current plan". One constant digest is therefore
    correct, and it is what makes a same-key undo retry replay the original undo
    instead of reversing a second, earlier mutation (brief §31).
    """
    return _digest({"domain": FINGERPRINT_DOMAIN, "kind": UNDO_COMMAND_TYPE})


def snapshot_fingerprint(plan_text) -> str:
    """Digest of an exact persisted ``plan_data`` text.

    Integrity and comparison only. Undo compares this against the state a
    mutation is recorded as having produced; a mismatch means something wrote the
    plan outside this boundary and the undo must fail closed rather than guess.
    """
    return hashlib.sha256(plan_text.encode("utf-8")).hexdigest()
