"""Pure public projections for workout-state consumers."""
from datetime import date

from app.services.training_generation.response_validator import VALID_TIPS, WEEKDAYS


MAX_TODAY_EXERCISES = 50


class PlanSerializationError(ValueError):
    """The plan cannot be represented by the bounded public contract."""


def workout_state_payload(snapshot):
    """Compose decision facts with the canonical execution view, without reads.

    The query layer already loaded this SessionView in the coherent snapshot.
    Keep state classification independent of checkpoint/identity serialization.
    """
    state = snapshot.to_dict()
    if state.get("session") is not None and snapshot.execution_view is not None:
        state["session"] = {**snapshot.execution_view.to_dict(), **state["session"]}
    return {
        "completed": snapshot.completed_today,
        "state": state,
    }


def serialize_plan(plan_data):
    """Project a persisted plan onto the closed, bounded Training schema."""
    wrapped = isinstance(plan_data, dict)
    program = plan_data if isinstance(plan_data, list) else (
        plan_data.get("program") if wrapped else None)
    if not isinstance(program, list) or len(program) != 7:
        return {"program": []} if wrapped else []
    if any(not isinstance(row, dict) or row.get("gun") not in WEEKDAYS or
           row.get("tip") not in VALID_TIPS for row in program):
        raise PlanSerializationError("invalid seven-day schedule")
    if {row["gun"] for row in program} != set(WEEKDAYS):
        raise PlanSerializationError("incomplete seven-day schedule")
    projected = [_serialize_day(row) for row in program]
    return {"program": projected} if wrapped else projected


def serialize_today_plan(plan_data, today: date):
    """Project today's plan content onto the closed, bounded public schema.

    A malformed schedule is a normal unavailable-domain state and returns None.
    A selected row whose content violates the public bounds raises so the
    bootstrap can fail closed instead of sending partial or misleading content.
    """
    program = plan_data if isinstance(plan_data, list) else (
        plan_data.get("program") if isinstance(plan_data, dict) else None)
    if not isinstance(program, list) or len(program) != 7:
        return None
    weekday = WEEKDAYS[today.weekday()]
    selected = next(
        (row for row in program
         if isinstance(row, dict) and row.get("gun") == weekday),
        None,
    )
    if selected is None or selected.get("tip") not in VALID_TIPS:
        return None
    return _serialize_day(selected)


def _serialize_day(selected):
    exercises = selected.get("egzersizler")
    if not isinstance(exercises, list) or len(exercises) > MAX_TODAY_EXERCISES:
        raise PlanSerializationError("invalid exercise collection")

    projected = {
        "gun": _bounded_text(selected.get("gun"), 20, required=True),
        "tip": selected["tip"],
        "odak": _bounded_text(selected.get("odak"), 120),
        "sure_dk": _bounded_int(selected.get("sure_dk"), 0, 1440),
        "tahmini_kalori": _bounded_int(
            selected.get("tahmini_kalori"), 0, 900),
        "egzersizler": [],
    }
    for exercise in exercises:
        if not isinstance(exercise, dict):
            raise PlanSerializationError("invalid exercise")
        projected["egzersizler"].append({
            "isim": _bounded_text(exercise.get("isim"), 120, required=True),
            "set": _bounded_int(exercise.get("set"), 1, 100),
            "tekrar": _bounded_text(exercise.get("tekrar"), 40),
            "dinlenme": _optional_bounded_text(exercise, "dinlenme", 40),
            "not": _optional_bounded_text(exercise, "not", 240),
        })
    return projected


def _optional_bounded_text(exercise, key, maximum):
    """Read an exercise's optional display text, absent or not.

    ``dinlenme`` and ``not`` carry no meaning to any consumer beyond being
    shown, and a canonical plan can legitimately be missing them: the writer
    that appends an exercise to an existing day persists ``isim``/``set``/
    ``tekrar`` (plus the resolved catalog id) only, and the save validator
    already treats ``not`` as optional. Demanding a ``str`` here
    made one such entry raise, which ``/training/bootstrap`` turns into a
    fail-closed 500 — so a plan the user still owns rendered as
    "Workout state unavailable".

    This is the whole compatibility boundary, and deliberately the narrowest
    one: ABSENT (or null) becomes "". A value that is PRESENT is still held to
    the exact same type and length bounds as before, and every other field —
    ``isim``, ``set``, ``tekrar``, ``gun``, ``tip``, ``odak``, the numeric
    bounds and the seven-day/weekday/tip authority — is untouched. Genuinely
    unrepresentable content still fails the snapshot closed rather than
    leaking a partial plan.
    """
    value = exercise.get(key)
    if value is None:
        return ""
    return _bounded_text(value, maximum)


def _bounded_text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise PlanSerializationError("invalid text field")
    if required and not value.strip():
        raise PlanSerializationError("required text field is empty")
    return value


def _bounded_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanSerializationError("invalid integer field")
    if value < minimum or value > maximum:
        raise PlanSerializationError("integer field outside public bounds")
    return value
