"""Server-owned grounding of one Coach plan-mutation command.

The model may name a day, an exercise and a prescription. This module is
the only place those values are allowed to become a typed command that
can persist. It never writes; it returns either a replacement command or
a bounded non-applying result.
"""
from dataclasses import dataclass, replace
import re

from app.services.coach_plan_policy import CANCEL, CONFIRM, parse_confirmation_intent
from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)

from . import clarifications, results
from .exercise_grounding import (
    KIND_SUGGEST as EX_SUGGEST,
    KIND_UNKNOWN as EX_UNKNOWN,
    resolve_destination,
)
from .prescriptions import (
    Prescription,
    merge_prescription,
    parse_prescription,
)
from .weekdays import canonicalize_weekday, find_explicit_weekday
from .workout_targets import (
    KIND_AMBIGUOUS,
    KIND_NONE,
    KIND_NOT_FOUND,
    KIND_RESOLVED,
    _REGION_TOKENS,
    resolve_workout_target,
    semantic_regions,
)


# Presentation-only. Shown in clarification copy so the user can accept
# it; never written unless the next turn explicitly accepts it.
PROPOSED_SETS = 3
PROPOSED_REPS = "8-12"

_CUE_WORDS = frozenset({
    "add", "adding", "please", "can", "you", "to", "my", "the", "a", "an",
    "on", "for", "with", "of", "and", "me",
    "ekle", "ekler", "lutfen", "lütfen", "benim", "bir", "ye", "ya",
    "workout", "workouts", "antrenman", "antrenmanima", "antrenmanıma",
    "antrenmanina", "antrenmanına", "day", "gun", "gün", "gunu", "günü",
    "gunune", "gününe", "gunune",
    "sets", "set", "reps", "rep", "tekrar", "tekrari", "tekrarı",
    "yes", "yeah", "yep", "evet", "ok", "okay", "olur",
})


@dataclass(frozen=True)
class Grounding:
    command: object = None
    result: dict = None

    @property
    def ready(self):
        return self.result is None and self.command is not None


def current_user_message():
    from flask import g
    try:
        return getattr(g, "_coach_plan_user_message", "") or ""
    except RuntimeError:
        return ""


def current_turn_history():
    from flask import g
    try:
        return list(getattr(g, "_coach_plan_history", None) or ())
    except RuntimeError:
        return []


_REGION_WORDS = frozenset(
    token for names in _REGION_TOKENS.values() for token in names)


def _exercise_from_text(message):
    """Leftover tokens after stripping cues, weekdays, rx and nicknames."""
    if not isinstance(message, str) or not message.strip():
        return ""
    text = message
    text = re.sub(
        r"\d+\s*[x×]\s*\d+(?:\s*[-–—]\s*\d+)?", " ", text, flags=re.I)
    text = re.sub(
        r"\d+\s*(?:sets?|set|reps?|tekrar(?:lar)?)\b", " ", text, flags=re.I)
    text = re.sub(r"\bwith\b", " ", text, flags=re.I)
    tokens = re.findall(r"[A-Za-z0-9çÇğĞıİöÖşŞüÜ+-]+", text)
    kept = []
    for token in tokens:
        folded = token.casefold()
        if folded in _CUE_WORDS:
            continue
        if canonicalize_weekday(token) is not None:
            continue
        if folded in _REGION_WORDS:
            continue
        kept.append(token)
    return " ".join(kept).strip()


def user_owned_intent(message=None, history=None, user_id=None):
    """Exercise / day / prescription grounded for this turn.

    Current-turn user text is always authority for newly typed values.
    Completing a prior clarification reads the server-owned session
    record, never assistant prose and never client-supplied history.
    """
    message = current_user_message() if message is None else message
    accepted = parse_confirmation_intent(message) == CONFIRM
    current_rx = parse_prescription(message)
    current_name = _exercise_from_text(message)
    if accepted:
        current_name = ""
    stored = (
        clarifications.load(user_id) if user_id is not None
        else clarifications.load_current())
    rx = current_rx
    name = current_name
    source = message or ""
    accepted_proposal = False
    if stored:
        rx, name, source, accepted_proposal = _overlay_stored_clarification(
            stored, message, current_rx, current_name, accepted)
    return {
        "message": message,
        "source": source,
        "exercise": name,
        "prescription": rx,
        "accepted_proposal": accepted_proposal,
        "has_user_text": bool((message or "").strip()),
    }


def refresh_clarification_for_turn(user_message):
    """Drop a leftover clarification when this turn names a new exercise."""
    intent = parse_confirmation_intent(user_message)
    if intent == CONFIRM:
        return
    if intent == CANCEL:
        clarifications.clear()
        return
    if _exercise_from_text(user_message):
        clarifications.clear()


def _overlay_stored_clarification(stored, message, current_rx, current_name,
                                  accepted):
    """Fill missing fields from the server-owned clarification record."""
    rx = _prescription_from_store(stored, message, current_rx, accepted)
    if accepted and stored.get("suggestion"):
        name = stored.get("suggestion") or ""
    else:
        name = current_name or stored.get("exercise") or ""
    source = message or ""
    if not find_explicit_weekday(source) and not semantic_regions(source):
        source = stored.get("day") or source
    accepted_proposal = bool(
        accepted and (
            stored.get("proposed_sets") is not None
            or stored.get("suggestion")))
    return rx, name, source, accepted_proposal


def _prescription_from_store(stored, message, current_rx, accepted):
    if current_rx.sets is None and current_rx.reps is None:
        current_rx = _bare_number_prescription(stored, message) or current_rx
    if current_rx.sets is not None or current_rx.reps is not None:
        return Prescription(
            sets=(current_rx.sets if current_rx.sets is not None
                  else stored.get("sets")),
            reps=(current_rx.reps if current_rx.reps is not None
                  else stored.get("reps")),
        )
    if accepted:
        if (stored.get("proposed_sets") is not None
                and stored.get("proposed_reps") is not None):
            return Prescription(
                sets=stored.get("proposed_sets"),
                reps=stored.get("proposed_reps"),
            )
        if stored.get("sets") is not None and stored.get("reps") is not None:
            return Prescription(
                sets=stored.get("sets"), reps=stored.get("reps"))
    return Prescription(sets=stored.get("sets"), reps=stored.get("reps"))


def _bare_number_prescription(stored, message):
    """A bare '12' / '8-12' completes the half the server asked for."""
    if not isinstance(message, str):
        return None
    match = re.fullmatch(
        r"\s*(\d+(?:\s*[-–—]\s*\d+)?)\s*", message.strip())
    if not match:
        return None
    reason = stored.get("reason")
    token = match.group(1)
    if reason == results.REASON_MISSING_REPS:
        return Prescription(reps=_normalize_bare_reps(token))
    if reason == results.REASON_MISSING_SETS:
        try:
            return Prescription(sets=int(token))
        except ValueError:
            return None
    return None


def _normalize_bare_reps(token):
    from .prescriptions import _normalize_reps
    return _normalize_reps(token)


def ground_command(user_id, command):
    """Return a ``Grounding``: ready command, or a non-applying result."""
    intent = user_owned_intent(user_id=user_id)
    command = _canonicalize_command_days(command)

    if isinstance(command, (AddExerciseCommand, RemoveExerciseCommand,
                            ReplaceExerciseCommand,
                            UpdateExercisePrescriptionCommand)):
        target = resolve_workout_target(
            user_id, intent["source"] or intent["message"], command.day)
        if target.kind == KIND_AMBIGUOUS:
            return _needs_input(
                user_id, results.REASON_AMBIGUOUS_WORKOUT, command,
                candidates=target.candidates)
        if target.kind == KIND_NOT_FOUND:
            return _needs_input(
                user_id, results.REASON_WORKOUT_NOT_FOUND, command)
        if target.kind == KIND_RESOLVED:
            command = replace(command, day=target.day)
        elif target.kind == KIND_NONE:
            canonical = canonicalize_weekday(command.day)
            if canonical is not None:
                command = replace(command, day=canonical)

    if isinstance(command, AddExerciseCommand):
        name = intent["exercise"] or command.exercise
        destination = resolve_destination(name)
        if destination.kind == EX_UNKNOWN:
            return _needs_input(
                user_id, results.REASON_EXERCISE_UNKNOWN,
                replace(command, exercise=name))
        if destination.kind == EX_SUGGEST:
            return _needs_input(
                user_id, results.REASON_EXERCISE_SUGGEST,
                replace(command, exercise=name),
                suggestion=destination.suggestion,
                user_rx=intent["prescription"])
        command = replace(command, exercise=destination.canonical_name)
        rx = merge_prescription(
            intent["prescription"],
            tool_sets=command.sets,
            tool_reps=command.reps,
            user_message_present=intent["has_user_text"],
        )
        if intent["has_user_text"] and (
                intent["prescription"].sets is not None
                or intent["prescription"].reps is not None
                or intent["accepted_proposal"]):
            rx = intent["prescription"]
        if rx.sets is None and rx.reps is None and intent["has_user_text"]:
            return _needs_input(
                user_id, results.REASON_MISSING_PRESCRIPTION, command,
                label=_label_for(user_id, command.day, intent["source"]),
                user_rx=rx)
        if rx.sets is None and intent["has_user_text"]:
            return _needs_input(
                user_id, results.REASON_MISSING_SETS,
                replace(command, reps=rx.reps), user_rx=rx)
        if rx.reps is None and intent["has_user_text"]:
            return _needs_input(
                user_id, results.REASON_MISSING_REPS,
                replace(command, sets=rx.sets), user_rx=rx)
        if rx.sets is None or rx.reps is None:
            return Grounding(command=replace(
                command, sets=rx.sets, reps=rx.reps))
        return Grounding(command=replace(
            command, sets=rx.sets, reps=str(rx.reps)))

    if isinstance(command, ReplaceExerciseCommand):
        same_name = (
            str(command.exercise).strip().casefold()
            == str(command.replacement).strip().casefold())
        if same_name:
            return Grounding(command=command)
        destination = resolve_destination(command.replacement)
        if destination.kind == EX_UNKNOWN:
            return _needs_input(
                user_id, results.REASON_EXERCISE_UNKNOWN, command)
        if destination.kind == EX_SUGGEST:
            return _needs_input(
                user_id, results.REASON_EXERCISE_SUGGEST, command,
                suggestion=destination.suggestion)
        command = replace(command, replacement=destination.canonical_name)
        if intent["has_user_text"]:
            rx = intent["prescription"]
            if rx.sets is not None or rx.reps is not None:
                command = replace(
                    command,
                    sets=rx.sets if rx.sets is not None else command.sets,
                    reps=(str(rx.reps) if rx.reps is not None
                          else command.reps),
                )
        return Grounding(command=command)

    if isinstance(command, UpdateExercisePrescriptionCommand):
        if intent["has_user_text"]:
            rx = intent["prescription"]
            if rx.sets is None and rx.reps is None:
                return _needs_input(
                    user_id, results.REASON_MISSING_PRESCRIPTION, command)
            command = replace(
                command,
                sets=rx.sets if rx.sets is not None else command.sets,
                reps=(str(rx.reps) if rx.reps is not None else command.reps),
            )
        return Grounding(command=command)

    if isinstance(command, MoveTrainingDayCommand):
        day = canonicalize_weekday(command.day) or command.day
        target = canonicalize_weekday(command.target_day) or command.target_day
        return Grounding(command=replace(
            command, day=day, target_day=target))

    if isinstance(command, RemoveExerciseCommand):
        return Grounding(command=command)

    return Grounding(command=command)


def _canonicalize_command_days(command):
    if hasattr(command, "day"):
        canonical = canonicalize_weekday(command.day)
        if canonical is not None:
            command = replace(command, day=canonical)
    if hasattr(command, "target_day"):
        canonical = canonicalize_weekday(command.target_day)
        if canonical is not None:
            command = replace(command, target_day=canonical)
    return command


def _label_for(user_id, day, message):
    target = resolve_workout_target(user_id, message, day)
    return target.label or day


def _needs_input(user_id, reason, command, user_rx=None, **kwargs):
    """Non-applying result, remembering accept-able clarifications."""
    if reason in (
            results.REASON_MISSING_PRESCRIPTION,
            results.REASON_MISSING_SETS,
            results.REASON_MISSING_REPS,
            results.REASON_EXERCISE_SUGGEST):
        rx = user_rx or Prescription()
        clarifications.remember(user_id, {
            "day": getattr(command, "day", "") or "",
            "exercise": getattr(command, "exercise", "") or "",
            "suggestion": kwargs.get("suggestion") or "",
            "sets": rx.sets,
            "reps": rx.reps,
            "proposed_sets": (
                PROPOSED_SETS
                if reason == results.REASON_MISSING_PRESCRIPTION else None),
            "proposed_reps": (
                PROPOSED_REPS
                if reason == results.REASON_MISSING_PRESCRIPTION else None),
            "reason": reason,
        })
    else:
        clarifications.clear(user_id)
    return Grounding(result=results.needs_input_result(
        reason, command, **kwargs))


def followup_add_arguments(user_id=None):
    """Arguments for a server-owned ADD completing a prior clarification.

    Returns ``None`` unless a clarification the server itself stored is
    being completed this turn (prescription text, or yes to the stored
    proposal). Assistant chat text is never read.
    """
    stored = (
        clarifications.load(user_id) if user_id is not None
        else clarifications.load_current())
    if not stored or not stored.get("day"):
        return None
    intent = user_owned_intent(user_id=user_id)
    if not intent["has_user_text"] or not intent["exercise"]:
        return None
    rx = intent["prescription"]
    if rx.sets is None or rx.reps is None:
        return None
    return {
        "day": stored["day"],
        "exercise": intent["exercise"],
        "sets": rx.sets,
        "reps": str(rx.reps),
    }
