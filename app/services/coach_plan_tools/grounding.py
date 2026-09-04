"""Server-owned grounding of one Coach plan-mutation command.

The model may name a day, an exercise and a prescription. This module is
the only place those values are allowed to become a typed command that
can persist. It never writes; it returns either a replacement command or
a bounded non-applying result.
"""
from dataclasses import dataclass, replace
import hashlib
import re

from app.services.coach_plan_policy import CANCEL, CONFIRM, parse_confirmation_intent
from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)
from app.services.plan_mutation.fingerprint import command_type

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
from .schemas import (
    ADD_EXERCISE_TOOL,
    MOVE_DAY_TOOL,
    REMOVE_EXERCISE_TOOL,
    REPLACE_EXERCISE_TOOL,
    UPDATE_PRESCRIPTION_TOOL,
)
from .weekdays import canonicalize_weekday, find_explicit_weekday
from .workout_targets import (
    KIND_AMBIGUOUS,
    KIND_NONE,
    KIND_NOT_FOUND,
    KIND_RESOLVED,
    WorkoutTarget,
    _REGION_TOKENS,
    find_exercise_slots,
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
    "gunune", "gününe", "gunune", "body", "upper", "lower",
    "sets", "set", "reps", "rep", "tekrar", "tekrari", "tekrarı",
    "yes", "yeah", "yep", "evet", "ok", "okay", "olur",
    "it", "that", "this", "would", "be", "good", "great", "fine", "sure",
    "thanks", "thank", "please", "works", "perfect", "just", "like",
    "sounds", "nice", "really", "very", "too", "also", "then", "so",
    "well", "maybe", "kind", "kinda", "cool", "alright", "right",
    "gotcha", "want", "idea", "tamam", "harika", "super", "süper",
    "bunu", "onu", "oyle", "öyle", "boyle", "böyle", "yap", "yapalim",
    "yapalım", "istiyorum", "tabii", "peki",
    "change", "replace", "swap", "instead", "degistir", "değiştir",
    "yerine", "guncelle", "güncelle",
})

_ACCEPT_PREFIXES = frozenset({
    "yes", "yeah", "yep", "evet", "olur", "ok", "okay", "sure", "tabii",
    "confirm", "tamam",
})

_OPERATION_TOOLS = {
    "add_exercise": ADD_EXERCISE_TOOL,
    "replace_exercise": REPLACE_EXERCISE_TOOL,
    "update_exercise_prescription": UPDATE_PRESCRIPTION_TOOL,
}

#: Every write tool's operation, including the two that have no continuation
#: path. Supersession needs them all: a ``remove`` the user just asked for is
#: still a new intention that a pending ``replace`` must not outlive.
_TOOL_OPERATIONS = {
    ADD_EXERCISE_TOOL: "add_exercise",
    REPLACE_EXERCISE_TOOL: "replace_exercise",
    UPDATE_PRESCRIPTION_TOOL: "update_exercise_prescription",
    REMOVE_EXERCISE_TOOL: "remove_exercise",
    MOVE_DAY_TOOL: "move_training_day",
}

#: Domain separation for the clarification request id. Bumping this string
#: invalidates every stored record rather than silently redefining what an
#: existing one meant — the same discipline ``plan_mutation.fingerprint`` uses.
_REQUEST_DOMAIN = "axisai/coach-plan-clarification/v1"

#: Field separator for the hashed payload. An exercise name cannot contain it,
#: so ("ab", "c") and ("a", "bc") cannot collide onto one identity.
_SEPARATOR = chr(31)


def _fold_name(value):
    """Case- and space-insensitive identity for one exercise name."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).casefold()


def request_id(operation, exercise, replacement=""):
    """Bounded identity of ONE mutation request.

    A clarification record is only ever executable by the request that minted
    it, so the record has to carry an identity strong enough to answer "is the
    thing in front of me the same intention?" without reading chat prose and
    without a second model call. Operation plus the two names the user actually
    supplied is exactly that: the day, the sets and the reps are the fields a
    continuation is *allowed* to change, so none of them participate.
    """
    raw = _SEPARATOR.join((
        _REQUEST_DOMAIN, operation or "",
        _fold_name(exercise), _fold_name(replacement)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _record_names(stored, field):
    """The names a stored record answers to for ``field``.

    A suggestion counts as the same request: "did you mean Dumbbell Biceps
    Curl?" → "yes" re-issues the command under the canonical name, and that
    is the same intention, not a new one.
    """
    names = {_fold_name(stored.get(field)), _fold_name(stored.get("suggestion"))}
    names.discard("")
    return names


def request_matches_record(stored, operation, exercise, replacement=""):
    """Whether ``operation``/``exercise``/``replacement`` continue ``stored``.

    Used in two places and deliberately the same predicate in both: deciding
    whether an incoming request supersedes a pending clarification, and
    deciding whether a newly minted clarification may inherit already-grounded
    fields from the old one. Two answers that could disagree is precisely the
    defect being closed.
    """
    if not stored:
        return False
    if stored.get("operation") != operation:
        return False
    target = _fold_name(exercise)
    known = _record_names(stored, "exercise")
    if target and known and target not in known:
        return False
    wanted = _fold_name(replacement)
    stored_replacements = _record_names(stored, "replacement")
    if wanted and stored_replacements and wanted not in stored_replacements:
        return False
    return True


def supersede_stale_clarification(user_id, tool_name, arguments):
    """A new explicit mutation request invalidates an incompatible pending one.

    Runs at the REQUEST boundary rather than the turn boundary, and before the
    parser, because both of those are where the incident came from: the model
    raised a second, different mutation inside one turn, that second request
    was refused for a missing day, and the *first* request's pending
    clarification was still sitting there when the user typed "Monday" — so
    "Monday" executed a replace nobody was still asking for.

    Only the record's own identity decides. Nothing here reads assistant prose,
    and a request that continues the pending record (including the server's own
    re-issue after "yes"/"15") leaves it untouched.
    """
    operation = _TOOL_OPERATIONS.get(tool_name)
    if operation is None:
        return
    try:
        stored = (
            clarifications.load(user_id) if user_id is not None
            else clarifications.load_current())
    except clarifications.ClarificationAuthorityUnavailable:
        # Fail closed: the continuation path already refuses to execute from a
        # store it cannot read, so a stale record cannot fire either.
        return
    if not stored:
        return
    if not isinstance(arguments, dict):
        arguments = {}
    exercise = arguments.get("exercise")
    replacement = arguments.get("replacement")
    if request_matches_record(
            stored, operation,
            exercise if isinstance(exercise, str) else "",
            replacement if isinstance(replacement, str) else ""):
        return
    try:
        clarifications.clear(user_id)
    except clarifications.ClarificationAuthorityUnavailable:
        return


def continuation_matches_record(record, tool_name, arguments):
    """Whether ``record`` is the record a planned continuation came from.

    ``load`` then ``consume`` is two reads of a shared store; between them the
    record can be replaced by another worker handling the same user. The
    arguments were built from the FIRST read, so they are only allowed to
    execute if the record actually taken is the same request.
    """
    if not isinstance(record, dict):
        return False
    operation = _TOOL_OPERATIONS.get(tool_name)
    if operation is None or record.get("operation") != operation:
        return False
    if not isinstance(arguments, dict):
        return False
    return request_matches_record(
        record, operation,
        arguments.get("exercise") or "", arguments.get("replacement") or "")


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
    text = re.sub(r"\b\d+(?:\s*[-–—]\s*\d+)?\b", " ", text)
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


def _is_clarification_acceptance(message):
    if parse_confirmation_intent(message) == CONFIRM:
        return True
    if not isinstance(message, str) or not message.strip():
        return False
    tokens = re.findall(r"[A-Za-z0-9çÇğĞıİöÖşŞüÜ+-]+", message)
    if not tokens:
        return False
    if tokens[0].casefold() not in _ACCEPT_PREFIXES:
        return False
    if parse_confirmation_intent(message) == CANCEL:
        return False
    remainder = " ".join(tokens[1:])
    return not _exercise_from_text(remainder)


def user_owned_intent(message=None, history=None, user_id=None):
    """Exercise / day / prescription grounded for this turn.

    Current-turn user text is always authority for newly typed values.
    Completing a prior clarification reads the server-owned session
    record, never assistant prose and never client-supplied history.
    """
    message = current_user_message() if message is None else message
    try:
        stored = (
            clarifications.load(user_id) if user_id is not None
            else clarifications.load_current())
    except clarifications.ClarificationAuthorityUnavailable:
        stored = None
    accepted = _is_clarification_acceptance(message) if stored else (
        parse_confirmation_intent(message) == CONFIRM)
    current_rx = parse_prescription(message)
    current_name = _exercise_from_text(message)
    if accepted:
        current_name = ""
    rx = current_rx
    name = current_name
    source = message or ""
    accepted_proposal = False
    if stored and _is_continuation_reply(message, stored):
        rx, name, source, accepted_proposal = _overlay_stored_clarification(
            stored, message, current_rx, current_name, accepted)
    return {
        "message": message,
        "source": source,
        "exercise": name,
        # The name THIS turn's text names, before any overlay. "yes" and "15"
        # name nothing; a stored record still does, and the two must not be
        # confused when deciding whether the user is re-targeting.
        "typed_exercise": current_name,
        "prescription": rx,
        "accepted_proposal": accepted_proposal,
        "has_user_text": bool((message or "").strip()),
        "stored": stored,
    }


def refresh_clarification_for_turn(user_message, user_id=None):
    """Drop a leftover clarification when this turn is a new mutation."""
    try:
        stored = (
            clarifications.load(user_id) if user_id is not None
            else clarifications.load_current())
    except clarifications.ClarificationAuthorityUnavailable:
        return
    intent = parse_confirmation_intent(user_message)
    if intent == CONFIRM or _is_clarification_acceptance(user_message):
        return
    if intent == CANCEL:
        try:
            clarifications.clear(user_id)
        except clarifications.ClarificationAuthorityUnavailable:
            return
        return
    if stored and _is_continuation_reply(user_message, stored):
        return
    if _exercise_from_text(user_message):
        clarifications.clear(user_id)
        return
    if stored and user_message and not _is_continuation_reply(
            user_message, stored):
        clarifications.clear(user_id)


def is_continuation_attempt(message):
    """Whether this turn looks like a clarification continuation, not a new mutation."""
    if not isinstance(message, str) or not message.strip():
        return False
    if parse_confirmation_intent(message) in (CONFIRM, CANCEL):
        return True
    if _is_clarification_acceptance(message):
        return True
    if _exercise_from_text(message):
        return False
    if find_explicit_weekday(message) is not None:
        return True
    rx = parse_prescription(message)
    if rx.sets is not None or rx.reps is not None:
        return True
    return bool(re.fullmatch(
        r"\s*\d+(?:\s*[-–—]\s*\d+)?\s*", message.strip()))


def _is_continuation_reply(message, stored):
    if not stored or not isinstance(message, str):
        return False
    if parse_confirmation_intent(message) in (CONFIRM, CANCEL):
        return True
    if _is_clarification_acceptance(message):
        return True
    leftover = _exercise_from_text(message)
    if leftover:
        return False
    if _bare_number_prescription(stored, message) is not None:
        return True
    rx = parse_prescription(message)
    if rx.sets is not None or rx.reps is not None:
        return True
    explicit = find_explicit_weekday(message)
    if explicit is None:
        return False
    candidates = tuple(stored.get("candidate_days") or ())
    if candidates:
        return True
    stored_day = stored.get("day") or ""
    return not stored_day or explicit == stored_day


def _overlay_stored_clarification(stored, message, current_rx, current_name,
                                  accepted):
    """Fill missing fields from the server-owned clarification record."""
    rx = _prescription_from_store(stored, message, current_rx, accepted)
    if accepted and stored.get("suggestion"):
        name = stored.get("suggestion") or ""
    else:
        name = current_name or stored.get("exercise") or ""
    source = message or ""
    explicit = find_explicit_weekday(source)
    candidates = tuple(stored.get("candidate_days") or ())
    if explicit and candidates and explicit not in candidates:
        source = source
    elif not explicit and not semantic_regions(source):
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
    if reason in (
            results.REASON_MISSING_PRESCRIPTION,
            results.REASON_AMBIGUOUS_WORKOUT,
            results.REASON_EXERCISE_SUGGEST):
        if stored.get("sets") is None and stored.get("reps") is None:
            return None
        if stored.get("sets") is not None and stored.get("reps") is None:
            return Prescription(reps=_normalize_bare_reps(token))
        if stored.get("reps") is not None and stored.get("sets") is None:
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
    stored = intent.get("stored")

    if stored and stored.get("candidate_days"):
        explicit = find_explicit_weekday(intent["message"])
        candidates = tuple(stored.get("candidate_days") or ())
        if explicit and explicit not in candidates:
            return _needs_input(
                user_id, results.REASON_AMBIGUOUS_WORKOUT, command,
                candidates=candidates,
                user_rx=intent["prescription"])

    if isinstance(command, (AddExerciseCommand, RemoveExerciseCommand,
                            ReplaceExerciseCommand,
                            UpdateExercisePrescriptionCommand)):
        target = _resolve_command_workout(user_id, command, intent)
        if target.kind == KIND_AMBIGUOUS:
            grounded = _prepare_partial(user_id, command, intent)
            return _needs_input(
                user_id, results.REASON_AMBIGUOUS_WORKOUT, grounded,
                candidates=target.candidates,
                user_rx=intent["prescription"])
        if target.kind == KIND_NOT_FOUND:
            grounded = _prepare_partial(user_id, command, intent)
            return _needs_input(
                user_id, results.REASON_WORKOUT_NOT_FOUND, grounded)
        if target.kind == KIND_RESOLVED:
            command = replace(command, day=target.day)
        elif target.kind == KIND_NONE:
            canonical = canonicalize_weekday(command.day)
            if canonical is not None:
                command = replace(command, day=canonical)
        if not (command.day or "").strip() and not isinstance(
                command, RemoveExerciseCommand):
            # ``day`` is groundable, so it can legitimately arrive absent — but
            # nothing above resolved it. Asking stores what IS grounded; letting
            # it through would reach the domain as a bare "day is required".
            grounded = _prepare_partial(user_id, command, intent)
            return _needs_input(
                user_id, results.REASON_WORKOUT_NOT_FOUND, grounded)

    if isinstance(command, AddExerciseCommand):
        return _ground_add(user_id, command, intent)

    if isinstance(command, ReplaceExerciseCommand):
        return _ground_replace(user_id, command, intent)

    if isinstance(command, UpdateExercisePrescriptionCommand):
        return _ground_update(user_id, command, intent)

    if isinstance(command, MoveTrainingDayCommand):
        day = canonicalize_weekday(command.day) or command.day
        target = canonicalize_weekday(command.target_day) or command.target_day
        return Grounding(command=replace(
            command, day=day, target_day=target))

    if isinstance(command, RemoveExerciseCommand):
        return Grounding(command=command)

    return Grounding(command=command)


def _resolve_command_workout(user_id, command, intent):
    message = intent["source"] or intent["message"]
    named_day = find_explicit_weekday(intent["message"])
    named_region = semantic_regions(intent["message"])
    target = resolve_workout_target(user_id, message, command.day)
    slot_commands = (
        UpdateExercisePrescriptionCommand, ReplaceExerciseCommand,
        RemoveExerciseCommand)
    # Only when the user NAMED the exercise in this turn's own words. On a
    # continuation ("yes", "15", "Monday") the exercise comes from the stored
    # record, and re-deriving the day from it would throw away the day that
    # record already settled — turning an answered question back into the
    # question.
    if (isinstance(command, slot_commands) and intent.get("typed_exercise")
            and not named_day and not named_region):
        search = command.exercise
        destination = resolve_destination(search)
        if destination.canonical_name:
            search = destination.canonical_name
        elif destination.suggestion:
            search = destination.suggestion
        slots = find_exercise_slots(user_id, search)
        if len(slots) == 1:
            return WorkoutTarget(KIND_RESOLVED, day=slots[0], label=slots[0])
        if len(slots) > 1:
            return WorkoutTarget(KIND_AMBIGUOUS, candidates=slots)
        return WorkoutTarget(KIND_NOT_FOUND)
    return target


def _prepare_partial(user_id, command, intent):
    if isinstance(command, AddExerciseCommand):
        name = intent["exercise"] or command.exercise
        destination = resolve_destination(name)
        if destination.kind != EX_UNKNOWN and destination.canonical_name:
            command = replace(command, exercise=destination.canonical_name)
        elif destination.kind == EX_SUGGEST:
            command = replace(command, exercise=name)
        rx = intent["prescription"]
        if rx.sets is not None or rx.reps is not None:
            command = replace(
                command,
                sets=rx.sets if rx.sets is not None else command.sets,
                reps=(str(rx.reps) if rx.reps is not None else command.reps),
            )
    return command


def _ground_add(user_id, command, intent):
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
    if rx.sets is None and rx.reps is None:
        # No user text to ground from and the model supplied neither half.
        return _needs_input(
            user_id, results.REASON_MISSING_PRESCRIPTION, command,
            label=_label_for(user_id, command.day, intent["source"]),
            user_rx=rx)
    if rx.sets is None or rx.reps is None:
        return Grounding(command=replace(
            command, sets=rx.sets, reps=rx.reps))
    return Grounding(command=replace(
        command, sets=rx.sets, reps=str(rx.reps)))


def _ground_replace(user_id, command, intent):
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
            suggestion=destination.suggestion,
            user_rx=intent["prescription"])
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


def _ground_update(user_id, command, intent):
    if intent["has_user_text"]:
        rx = intent["prescription"]
        if rx.sets is None and rx.reps is None and (
                command.sets is None and command.reps is None):
            return _needs_input(
                user_id, results.REASON_MISSING_PRESCRIPTION, command,
                user_rx=rx)
        command = replace(
            command,
            sets=rx.sets if rx.sets is not None else command.sets,
            reps=(str(rx.reps) if rx.reps is not None else command.reps),
        )
    if command.sets is None and command.reps is None:
        # The parser lets a groundable field arrive absent; the domain would
        # answer this with a bare INVALID_MUTATION, which tells the user
        # nothing and stores nothing to continue from.
        return _needs_input(
            user_id, results.REASON_MISSING_PRESCRIPTION, command)
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
    """Non-applying result, remembering accept-able clarifications.

    Merging is MONOTONIC and scoped to one request lineage. Two rules, and the
    incident came from having neither:

    * a field already grounded from user intent is never replaced or dropped by
      a later clarification — "with 4 sets" then "15" is ``4x15``, not ``3x15``;
    * inheritance only happens WITHIN one request. A record minted for a
      different intention contributes nothing, so a pending replace of
      "Barbell Curl" cannot donate its exercise, its suggestion or its
      candidate days to a brand-new add.
    """
    if reason in (
            results.REASON_MISSING_PRESCRIPTION,
            results.REASON_MISSING_SETS,
            results.REASON_MISSING_REPS,
            results.REASON_EXERCISE_SUGGEST,
            results.REASON_AMBIGUOUS_WORKOUT):
        candidates = kwargs.get("candidates") or ()
        intent = user_owned_intent(user_id=user_id)
        operation = (
            command_type(command) if command is not None else "add_exercise")
        stored = intent.get("stored") or {}
        if not request_matches_record(
                stored, operation,
                getattr(command, "exercise", "") or "",
                getattr(command, "replacement", "") or ""):
            stored = {}
        rx = user_rx or Prescription()
        exercise = (
            getattr(command, "exercise", "")
            or stored.get("exercise")
            or "")
        replacement = (
            getattr(command, "replacement", "")
            or stored.get("replacement")
            or "")
        suggestion = kwargs.get("suggestion") or stored.get("suggestion") or ""
        day = getattr(command, "day", "") or stored.get("day") or ""
        if reason == results.REASON_AMBIGUOUS_WORKOUT:
            # The day is exactly what is still unknown; keeping the model's
            # guess would let a later "yes" execute against it.
            day = ""
        clarifications.remember(user_id, {
            "operation": operation,
            "request_id": stored.get("request_id") or request_id(
                operation, exercise, replacement),
            "day": day,
            "exercise": exercise,
            "replacement": replacement,
            "suggestion": suggestion,
            "sets": rx.sets if rx.sets is not None else stored.get("sets"),
            "reps": rx.reps if rx.reps is not None else stored.get("reps"),
            "proposed_sets": (
                PROPOSED_SETS
                if reason == results.REASON_MISSING_PRESCRIPTION else None),
            "proposed_reps": (
                PROPOSED_REPS
                if reason == results.REASON_MISSING_PRESCRIPTION else None),
            "candidate_days": candidates or stored.get("candidate_days") or (),
            "reason": reason,
        })
    else:
        clarifications.clear(user_id)
    return Grounding(result=results.needs_input_result(
        reason, command, **kwargs))


def followup_add_arguments(user_id=None):
    """Arguments for a server-owned ADD completing a prior clarification."""
    mutation = followup_mutation(user_id)
    if not mutation:
        return None
    tool, arguments = mutation
    if tool != ADD_EXERCISE_TOOL:
        return None
    return arguments


def followup_mutation(user_id=None):
    """``(tool_name, arguments)`` completing a server-owned clarification.

    Returns ``None`` unless a clarification the server itself stored is
    being completed this turn. Assistant chat text is never read.
    """
    stored = (
        clarifications.load(user_id) if user_id is not None
        else clarifications.load_current())
    if not stored:
        return None
    message = current_user_message()
    if parse_confirmation_intent(message) == CANCEL:
        return None
    if not _is_continuation_reply(message, stored):
        return None
    explicit = find_explicit_weekday(message)
    candidates = tuple(stored.get("candidate_days") or ())
    if explicit and candidates and explicit not in candidates:
        return None
    intent = user_owned_intent(user_id=user_id)
    if not intent["has_user_text"]:
        return None
    day = stored.get("day") or ""
    if explicit and (not candidates or explicit in candidates):
        day = explicit
    if not day:
        return None
    exercise = intent["exercise"] or stored.get("exercise") or ""
    if stored.get("suggestion") and _is_clarification_acceptance(message):
        exercise = stored.get("suggestion") or exercise
    if not exercise:
        return None
    operation = stored.get("operation") or "add_exercise"
    tool = _OPERATION_TOOLS.get(operation)
    if tool is None:
        return None
    if operation == "replace_exercise":
        replacement = (
            stored.get("suggestion") or stored.get("replacement") or "")
        if not replacement:
            return None
        arguments = {
            "day": day,
            "exercise": stored.get("exercise") or exercise,
            "replacement": replacement,
        }
        rx = intent["prescription"]
        if rx.sets is not None:
            arguments["sets"] = rx.sets
        if rx.reps is not None:
            arguments["reps"] = str(rx.reps)
        return tool, arguments
    if operation == "update_exercise_prescription":
        rx = intent["prescription"]
        if rx.sets is None and rx.reps is None:
            return None
        arguments = {
            "day": day,
            "exercise": exercise,
        }
        if rx.sets is not None:
            arguments["sets"] = rx.sets
        if rx.reps is not None:
            arguments["reps"] = str(rx.reps)
        return tool, arguments
    rx = intent["prescription"]
    if rx.sets is None and rx.reps is None:
        return None
    arguments = {"day": day, "exercise": exercise}
    if rx.sets is not None:
        arguments["sets"] = rx.sets
    if rx.reps is not None:
        arguments["reps"] = str(rx.reps)
    # A HALF-answered add is still this request. Re-issuing it with what is
    # known lets grounding ask for the other half and store the merge; going
    # silent here would hand the turn back to the model, which is how the
    # already-grounded "4 sets" was lost in the first place.
    return tool, arguments


def invalid_candidate_result(user_id=None):
    """Re-ask when the user named a day that is not a stored candidate."""
    stored = (
        clarifications.load(user_id) if user_id is not None
        else clarifications.load_current())
    if not stored:
        return None
    message = current_user_message()
    explicit = find_explicit_weekday(message)
    candidates = tuple(stored.get("candidate_days") or ())
    if not explicit or not candidates or explicit in candidates:
        return None
    if _exercise_from_text(message):
        return None
    command = AddExerciseCommand(
        day=stored.get("day") or "",
        exercise=stored.get("exercise") or "",
        sets=stored.get("sets"),
        reps=stored.get("reps"),
    )
    return results.needs_input_result(
        results.REASON_AMBIGUOUS_WORKOUT, command, candidates=candidates)
