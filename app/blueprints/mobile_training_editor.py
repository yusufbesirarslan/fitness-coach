"""Native owner edits backed by the canonical Training mutation service."""

import re
import unicodedata

from flask import current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from app.blueprints.mobile_api import bp, mobile_error
from app.mobile_auth_middleware import require_mobile_auth
from app.services import exercise_picker, mobile_training
from app.services.exercise_catalog import (
    ID_PATTERN,
    MAX_EXERCISE_ID_LENGTH,
    CatalogConfigurationError,
    load_exercise_catalog,
    search_exercises,
)
from app.services.exercise_catalog_v3 import TARGET_REGIONS
from app.services.plan_mutation import (
    ACTOR_USER,
    ActiveWorkoutConflict,
    AddExerciseCommand,
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseAlreadyPresent,
    ExerciseNotFound,
    IdempotencyConflict,
    InvalidMutation,
    InvalidPrescription,
    MutationContext,
    PlanEditRevision,
    PlanMutationError,
    PlanNotFound,
    PlanNotMutable,
    PlanStateConflict,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UndoConflict,
    UndoUnavailable,
    UpdateExercisePrescriptionCommand,
    apply_plan_mutation,
    undo_last_change,
)
from app.services.plan_mutation.validation import validate_reps, validate_sets
from app.services.training_generation.plan_schema import (
    NATIVE_MAX_EXERCISES_PER_DAY,
    REPS_MAX,
    WEEKDAYS,
)


_CONTRACT_VERSION = 1
_MAX_QUERY_CHARS = 80
_MUTATION_ID_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_SNAPSHOT_RE = re.compile(r"[a-f0-9]{64}")
_REVISION_FIELDS = {"plan_lineage", "mutation_version", "snapshot"}
_TARGET_FIELDS = {"exercise_id", "position"}
_PRESCRIPTION_FIELDS = {"sets", "reps"}
# G4: optional list selector. Absent (every pre-G4 client) is the day's own
# strength list, exactly as before; "cardio" is the strength day's separate
# cardio session, which only an ``include_sessions=1`` reader can see.
_SESSION_FIELD = "session"
_SESSIONS = frozenset({"strength", "cardio"})
_OPERATIONS = {
    "add": (AddExerciseCommand, {"exercise_id", "sets", "reps"}, {_SESSION_FIELD}),
    "remove": (RemoveExerciseCommand, _TARGET_FIELDS, {_SESSION_FIELD}),
    "replace": (ReplaceExerciseCommand, _TARGET_FIELDS | {"replacement_id"},
                _PRESCRIPTION_FIELDS | {_SESSION_FIELD}),
    "prescription": (UpdateExercisePrescriptionCommand, _TARGET_FIELDS,
                     _PRESCRIPTION_FIELDS | {_SESSION_FIELD}),
}
_ERRORS = {
    ActiveWorkoutConflict: ("TRAINING_EDITOR_ACTIVE_WORKOUT", 409),
    AmbiguousExerciseTarget: ("TRAINING_EDITOR_AMBIGUOUS_EXERCISE", 409),
    DayNotFound: ("TRAINING_EDITOR_TARGET_NOT_FOUND", 404),
    ExerciseAlreadyPresent: ("TRAINING_EDITOR_EXERCISE_ALREADY_PRESENT", 409),
    ExerciseNotFound: ("TRAINING_EDITOR_TARGET_NOT_FOUND", 404),
    IdempotencyConflict: ("TRAINING_EDITOR_IDEMPOTENCY_CONFLICT", 409),
    InvalidMutation: ("TRAINING_EDITOR_INVALID_REQUEST", 400),
    InvalidPrescription: ("TRAINING_EDITOR_INVALID_PRESCRIPTION", 400),
    PlanNotFound: ("TRAINING_EDITOR_PLAN_NOT_FOUND", 404),
    PlanNotMutable: ("TRAINING_EDITOR_PLAN_UNAVAILABLE", 409),
    PlanStateConflict: ("TRAINING_EDITOR_STALE_PLAN", 409),
    UndoConflict: ("TRAINING_EDITOR_UNDO_UNAVAILABLE", 409),
    UndoUnavailable: ("TRAINING_EDITOR_UNDO_UNAVAILABLE", 409),
}


def _is_safe_text(value, limit):
    return (isinstance(value, str) and len(value) <= limit
            and all(unicodedata.category(char) not in {"Cc", "Cs"} for char in value))


def _exercise_id(value):
    if (not isinstance(value, str) or len(value) > MAX_EXERCISE_ID_LENGTH
            or not ID_PATTERN.fullmatch(value)):
        raise InvalidMutation("invalid exercise ID")
    return value


def _revision(body):
    lineage = body["plan_lineage"]
    version = body["mutation_version"]
    snapshot = body["snapshot"]
    if (not isinstance(lineage, str) or not 1 <= len(lineage) <= 64
            or any(not 33 <= ord(char) <= 126 for char in lineage)
            or type(version) is not int or not 0 <= version <= 2_147_483_647
            or not isinstance(snapshot, str) or not _SNAPSHOT_RE.fullmatch(snapshot)):
        raise InvalidMutation("invalid plan revision")
    return PlanEditRevision(lineage, version, snapshot)


def _parse_mutation(operation, body):
    command_type, required, optional = _OPERATIONS[operation]
    required = required | _REVISION_FIELDS | {"day_slot"}
    if not isinstance(body, dict) or not required <= body.keys() <= required | optional:
        raise InvalidMutation("invalid request fields")
    slot = body["day_slot"]
    if type(slot) is not int or not 0 <= slot < len(WEEKDAYS):
        raise InvalidMutation("invalid workout day")

    fields = {}
    if "position" in body:
        position = body["position"]
        if type(position) is not int or not 0 <= position < NATIVE_MAX_EXERCISES_PER_DAY:
            raise InvalidMutation("invalid exercise position")
        fields["position"] = position
    if "replacement_id" in body:
        fields["replacement"] = _exercise_id(body["replacement_id"])
    if "sets" in body:
        fields["sets"] = validate_sets(body["sets"])
    if "reps" in body:
        if not _is_safe_text(body["reps"], REPS_MAX):
            raise InvalidPrescription("invalid reps text")
        fields["reps"] = validate_reps(body["reps"])
    if _SESSION_FIELD in body:
        if body[_SESSION_FIELD] not in _SESSIONS:
            raise InvalidMutation("invalid session")
        fields["session"] = body[_SESSION_FIELD]
    if operation == "prescription" and not _PRESCRIPTION_FIELDS & fields.keys():
        raise InvalidMutation("a prescription field is required")

    command = command_type(day=WEEKDAYS[slot], exercise=_exercise_id(body["exercise_id"]), **fields)
    return _revision(body), command


@bp.get("/training/editor")
@require_mobile_auth
def training_editor_plan():
    try:
        include_sessions = mobile_training.sessions_requested(request.args)
    except mobile_training.InvalidSessionsExpansion:
        return mobile_error("TRAINING_EDITOR_INVALID_REQUEST",
                            "Invalid Training editor request.", 400, False)
    try:
        payload = mobile_training.build_current_plan(
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            sessions_enabled=bool(current_app.config.get("FITX_WORKOUT_SESSIONS_ENABLED")),
            include_editor_revision=True,
            include_sessions=include_sessions,
        )
    except mobile_training.PlanUnprojectable:
        return mobile_error("TRAINING_EDITOR_PLAN_UNAVAILABLE",
                            "The current Training plan cannot be edited safely.", 409, False)
    except mobile_training.TrainingReadUnavailable:
        return mobile_error("TRAINING_EDITOR_UNAVAILABLE",
                            "Training is temporarily unavailable.", 503, True)
    return jsonify(contract_version=_CONTRACT_VERSION, **payload)


@bp.get("/training/exercise-catalog")
@require_mobile_auth
def training_exercise_catalog():
    query = request.args.get("q", "")
    if (set(request.args) - {"q"} or len(request.args.getlist("q")) > 1
            or not _is_safe_text(query, _MAX_QUERY_CHARS)):
        return mobile_error("TRAINING_EDITOR_INVALID_REQUEST",
                            "Invalid exercise search.", 400, False)
    try:
        catalog = load_exercise_catalog()
        exercises = search_exercises(catalog, query)
    except CatalogConfigurationError:
        return mobile_error("TRAINING_EDITOR_UNAVAILABLE",
                            "The exercise catalog is temporarily unavailable.", 503, True)
    return jsonify(
        contract_version=_CONTRACT_VERSION,
        catalog_version=catalog.version,
        exercises=[{
            "exercise_id": item.exercise_id,
            "display_name": item.canonical_name,
            "equipment": sorted(item.equipment),
            "movement": item.movement,
            "primary_region": item.primary_region,
        } for item in exercises],
    )


@bp.get("/training/exercise-families")
@require_mobile_auth
def training_exercise_families():
    """Catalog V3 picker: families, equipment variants and advisory availability.

    A separate versioned resource, so the strict V1 catalog decoder never sees
    a new field. The saved identity is always one variant's ``exercise_id``.
    """
    query = request.args.get("q", "")
    region = request.args.get("region")
    if (set(request.args) - {"q", "region"}
            or any(len(request.args.getlist(key)) > 1 for key in request.args)
            or not _is_safe_text(query, _MAX_QUERY_CHARS)
            or (region is not None and region not in TARGET_REGIONS)):
        return mobile_error("TRAINING_EDITOR_INVALID_REQUEST",
                            "Invalid exercise search.", 400, False)
    try:
        context = exercise_picker.plan_exercise_context(g.mobile_user.id)
        payload = exercise_picker.build_picker(query, region, context)
    except (SQLAlchemyError, CatalogConfigurationError):
        return mobile_error("TRAINING_EDITOR_UNAVAILABLE",
                            "The exercise catalog is temporarily unavailable.", 503, True)
    return jsonify(payload)


@bp.post("/training/editor/<any(add,remove,replace,prescription,undo):operation>")
@require_mobile_auth
def mutate_training_editor_plan(operation):
    try:
        request.max_content_length = 4096
        owner = g.mobile_user.id
        context = MutationContext(request.headers.get("Idempotency-Key"), actor=ACTOR_USER)
        try:
            body = request.get_json(silent=True)
        except RecursionError:
            raise InvalidMutation("invalid request nesting") from None
        if operation == "undo":
            if not isinstance(body, dict) or set(body) != _REVISION_FIELDS | {"mutation_id"}:
                raise InvalidMutation("invalid undo fields")
            target = body["mutation_id"]
            if not isinstance(target, str) or not _MUTATION_ID_RE.fullmatch(target):
                raise InvalidMutation("invalid mutation ID")
            result = undo_last_change(
                owner, context, editor_revision=_revision(body), target_mutation_id=target)
        else:
            revision, command = _parse_mutation(operation, body)
            result = apply_plan_mutation(owner, command, context, editor_revision=revision)
    except RequestEntityTooLarge:
        return mobile_error("TRAINING_EDITOR_INVALID_REQUEST",
                            "Training edit request is too large.", 413, False)
    except PlanMutationError as error:
        code, status = _ERRORS[type(error)]
        return mobile_error(code, "The Training edit could not be applied.", status, False)
    except (SQLAlchemyError, CatalogConfigurationError):
        return mobile_error("TRAINING_EDITOR_UNAVAILABLE",
                            "Training is temporarily unavailable.", 503, True)

    response = jsonify(
        contract_version=_CONTRACT_VERSION,
        outcome=result.outcome,
        mutation_id=result.mutation_id,
        mutation_version=result.plan_version,
        reread_required=True,
    )
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return response
