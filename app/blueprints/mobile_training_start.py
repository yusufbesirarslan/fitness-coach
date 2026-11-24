"""Bearer-authenticated PD2 onboarding training start. Transport only.

    GET  /api/v1/training/start                   start state + canonical calories
    PUT  /api/v1/training/start                   {"decision": "skip"}
    POST /api/v1/training/plans/preview/accept    activate the AI preview (If-Match)
    POST /api/v1/training/plans/preview/discard   cancel / choose another method

The AI preview itself is ``POST /api/v1/training/plans?preview=1`` (the D1-
closed generation endpoint) and the General/Manual preview is
``POST /api/v1/training/plans/general/preview``. Accept and Discard never
reach a provider: Accept commits the already-staged candidate through the
existing ``store.commit_plan`` writer; Discard ends it. Neither is a D1-closed
endpoint, exactly like the existing replacement confirmation.

Every route here answers 404 BEFORE authentication while
``FITX_TRAINING_START_PREVIEW_ENABLED`` is OFF (default). See
docs/PD2_TRAINING_START.md.
"""
from functools import wraps

from flask import abort, current_app, g, jsonify, request

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.services import mobile_training, training_start
from app.services.ai_consent import ConsentDenied
from app.blueprints.ai_consent import consent_error_response
from app.services.mobile_training_generation import (
    GenerationInProgress,
    InvalidPlanRequest,
    PlanGenerationCommandError,
    PreviewPreconditionRequired,
    accept_preview,
    discard_preview,
    parse_idempotency_key,
)
from app.services.training_plan_origin import ai_origin


def _enabled():
    return bool(current_app.config.get("FITX_TRAINING_START_PREVIEW_ENABLED", False))


def rollout_gated(view):
    """Flag OFF: 404 before auth; the rule stays registered (see Today Guidance)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _enabled():
            abort(404)
        return view(*args, **kwargs)
    return wrapper


def _start_error(error):
    return mobile_error(
        error.public_code,
        "The Training start request could not be completed.",
        error.http_status,
        error.retryable,
    )


def _command_error(error):
    return mobile_error(
        error.public_code,
        "The Training plan request could not be completed.",
        error.http_status,
        error.retryable,
        retry_after=15 if isinstance(error, GenerationInProgress) else None,
    )


def _unavailable():
    db.session.rollback()
    return mobile_error(
        "TRAINING_READ_UNAVAILABLE",
        "Training is temporarily unavailable.",
        503,
        True,
    )


@bp.get("/training/start")
@rollout_gated
@require_mobile_auth
def training_start_state():
    try:
        return jsonify(training_start.read_start(g.mobile_user.id))
    except training_start.TrainingStartError as error:
        return _start_error(error)


@bp.put("/training/start")
@rollout_gated
@require_mobile_auth
def record_training_start():
    try:
        training_start.parse_decision(request.get_json(silent=True))
        return jsonify(training_start.record_skip(g.mobile_user))
    except training_start.TrainingStartError as error:
        return _start_error(error)


def _sessions_requested():
    try:
        return mobile_training.sessions_requested(request.args)
    except mobile_training.InvalidSessionsExpansion:
        raise InvalidPlanRequest("include_sessions must be exactly 1") from None


@bp.post("/training/plans/preview/accept")
@rollout_gated
@require_mobile_auth
def accept_training_plan_preview():
    try:
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
        include_sessions = _sessions_requested()
        expected = training_start.parse_if_match(request.headers.get("If-Match"))
        if not expected:
            raise PreviewPreconditionRequired()
        result = accept_preview(g.mobile_user, key, expected)
        payload = mobile_training.project_current_plan(
            result.plan,
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            sessions_enabled=bool(
                current_app.config.get("FITX_WORKOUT_SESSIONS_ENABLED", False)),
            include_sessions=include_sessions,
        )
    except PlanGenerationCommandError as error:
        return _command_error(error)
    except ConsentDenied as error:
        # The staged candidate's consent authority was withdrawn: no plan.
        return consent_error_response(error)
    except (mobile_training.PlanUnprojectable,
            mobile_training.TrainingReadUnavailable):
        return _unavailable()

    response = jsonify({
        "contract_version": training_start.CONTRACT_VERSION,
        "origin": ai_origin(),
        **payload,
    })
    response.status_code = 201
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return response


@bp.post("/training/plans/preview/discard")
@rollout_gated
@require_mobile_auth
def discard_training_plan_preview():
    try:
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
        discard_preview(g.mobile_user, key)
    except PlanGenerationCommandError as error:
        return _command_error(error)
    return "", 204
