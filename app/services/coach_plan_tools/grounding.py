"""Server-owned grounding of one Coach plan-mutation command.

The model may name a day, an exercise and a prescription. This module is
the only place those values are allowed to become a typed command that
can persist. It never writes; it returns either a replacement command or
a bounded non-applying result.
"""
from dataclasses import dataclass, replace
import re

from app.services.coach_plan_policy import CONFIRM, parse_confirmation_intent
from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)

from . import results
from .exercise_grounding import (
    KIND_SUGGEST as EX_SUGGEST,
    KIND_UNKNOWN as EX_UNKNOWN,
    resolve_destination,
)
from .prescriptions import (
    Prescription,
    merge_prescription,
    parse_prescription,
    parse_proposed_prescription,
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


def _last_user_and_assistant(history):
    previous_user = ""
    previous_assistant = ""
    if not history:
        return previous_user, previous_assistant
    # History may already exclude the current user turn.
    for item in reversed(list(history)):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content") or ""
        if role == "assistant" and not previous_assistant:
            previous_assistant = content
        elif role == "user" and not previous_user:
            previous_user = content
        if previous_user and previous_assistant:
            break
    return previous_user, previous_assistant


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


def user_owned_intent(message=None, history=None):
    """Exercise / day / prescription the user actually named this turn."""
    message = current_user_message() if message is None else message
    history = current_turn_history() if history is None else history
    previous_user, previous_assistant = _last_user_and_assistant(history)
    current_rx = parse_prescription(message)
    previous_rx = parse_prescription(previous_user)
    proposed = parse_proposed_prescription(previous_assistant)
    accepted = parse_confirmation_intent(message) == CONFIRM
    rx = current_rx
    if rx.sets is None and rx.reps is None:
        if accepted and (proposed.sets is not None and proposed.reps is not None):
            rx = proposed
        elif previous_rx.sets is not None or previous_rx.reps is not None:
            # Current turn is the missing half ("12" / "3x12") of a prior add.
            if current_rx.sets is None and current_rx.reps is None:
                rx = previous_rx
            else:
                rx = Prescription(
                    sets=current_rx.sets or previous_rx.sets,
                    reps=current_rx.reps or previous_rx.reps,
                )
        elif current_rx.sets is not None or current_rx.reps is not None:
            rx = current_rx
    current_name = _exercise_from_text(message)
    if parse_confirmation_intent(message) == CONFIRM:
        current_name = ""
    name = current_name or _exercise_from_text(previous_user)
    source = message or ""
    if not find_explicit_weekday(source) and not semantic_regions(source):
        source = previous_user or source
    return {
        "message": message,
        "source": source,
        "exercise": name,
        "prescription": rx,
        "accepted_proposal": accepted and proposed.sets is not None,
        "has_user_text": bool((message or "").strip()),
    }


def ground_command(user_id, command):
    """Return a ``Grounding``: ready command, or a non-applying result."""
    intent = user_owned_intent()
    command = _canonicalize_command_days(command)

    if isinstance(command, (AddExerciseCommand, RemoveExerciseCommand,
                            ReplaceExerciseCommand,
                            UpdateExercisePrescriptionCommand)):
        target = resolve_workout_target(
            user_id, intent["source"] or intent["message"], command.day)
        if target.kind == KIND_AMBIGUOUS:
            return Grounding(result=results.needs_input_result(
                results.REASON_AMBIGUOUS_WORKOUT, command,
                candidates=target.candidates))
        if target.kind == KIND_NOT_FOUND:
            return Grounding(result=results.needs_input_result(
                results.REASON_WORKOUT_NOT_FOUND, command))
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
            return Grounding(result=results.needs_input_result(
                results.REASON_EXERCISE_UNKNOWN,
                replace(command, exercise=name)))
        if destination.kind == EX_SUGGEST:
            return Grounding(result=results.needs_input_result(
                results.REASON_EXERCISE_SUGGEST,
                replace(command, exercise=name),
                suggestion=destination.suggestion))
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
            return Grounding(result=results.needs_input_result(
                results.REASON_MISSING_PRESCRIPTION, command,
                label=_label_for(user_id, command.day, intent["source"])))
        if rx.sets is None and intent["has_user_text"]:
            return Grounding(result=results.needs_input_result(
                results.REASON_MISSING_SETS, replace(command, reps=rx.reps)))
        if rx.reps is None and intent["has_user_text"]:
            return Grounding(result=results.needs_input_result(
                results.REASON_MISSING_REPS, replace(command, sets=rx.sets)))
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
            return Grounding(result=results.needs_input_result(
                results.REASON_EXERCISE_UNKNOWN, command))
        if destination.kind == EX_SUGGEST:
            return Grounding(result=results.needs_input_result(
                results.REASON_EXERCISE_SUGGEST, command,
                suggestion=destination.suggestion))
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
                return Grounding(result=results.needs_input_result(
                    results.REASON_MISSING_PRESCRIPTION, command))
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


_CLARIFICATION_MARKERS = (
    "how many sets and reps",
    "kaç set",
    "set ve tekrar",
    "would you like",
    "did you mean",
    "bunu mu demek",
    "i found your",
    "antrenmanını",
    "i couldn't find",
    "bulamadım",
)


def _looks_like_our_clarification(assistant_text):
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        return False
    folded = assistant_text.casefold()
    return any(marker in folded for marker in _CLARIFICATION_MARKERS)


def followup_add_arguments():
    """Arguments for a server-owned ADD completing a prior clarification.

    Returns ``None`` unless the current turn supplies the missing piece of
    an add the server already grounded (prescription text, or yes to a
    proposal the server itself wrote). The dummy weekday is replaced by
    workout-target resolution against the previous user turn.
    """
    intent = user_owned_intent()
    if not intent["has_user_text"] or not intent["exercise"]:
        return None
    rx = intent["prescription"]
    if rx.sets is None or rx.reps is None:
        return None
    _previous_user, previous_assistant = _last_user_and_assistant(
        current_turn_history())
    if not _looks_like_our_clarification(previous_assistant):
        return None
    from app.services.plan_mutation.validation import WEEKDAYS
    return {
        "day": WEEKDAYS[0],
        "exercise": intent["exercise"],
        "sets": rx.sets,
        "reps": str(rx.reps),
    }
