"""Bearer-authenticated non-AI general Training plan (LP21). Transport only.

    POST /api/v1/training/plans/general           first plan from the reviewed template
    POST /api/v1/training/plans/general/preview   PD2: that plan, computed, NOT written

Attached to the existing ``mobile_api`` blueprint (one ``/api/v1`` surface,
``no-store``, error envelope, approved-route gate). Deliberately NOT a branch
of ``POST /training/plans``: that endpoint is AI generation and stays closed
by D1 (``ai_consent._CLOSED_ENDPOINTS``); this one never reaches a provider,
consent, allowance, quota, AI rate limit or AI concurrency slot, so it needs
none of them and takes none of them. Its own ceiling is an ordinary per-owner
write limit. See docs/LP21_NONAI_GENERAL_PLAN.md.
"""
from functools import wraps

from flask import abort, current_app, g, jsonify, request
from flask_limiter.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.services import mobile_training
from app.services import training_start
from app.services.general_training_plan import (
    create_general_plan,
    parse_general_request,
    preview_general_plan,
)
from app.services.mobile_training_generation import (
    GenerationInProgress,
    InvalidPlanRequest,
    PlanGenerationCommandError,
    PreviewPreconditionRequired,
    parse_idempotency_key,
)
from app.services.training_plan_origin import general_origin


GENERAL_PLAN_WRITE_RATELIMIT = "10 per minute; 60 per hour"
GENERAL_PLAN_PREVIEW_RATELIMIT = "20 per minute; 120 per hour"


def _owner_key():
    return str(g.mobile_user.id)


def _preview_key():
    return f"{g.mobile_user.id}:general-preview"


def _accepted_preview():
    """PD2 Accept: optional ``If-Match`` naming the previewed candidate.

    Absent keeps the LP21 contract byte-identical. Present only while the PD2
    rollout flag is ON (otherwise refused, never ignored), and then it must
    name the exact document the preview showed.
    """
    raw = request.headers.get("If-Match")
    if raw is None:
        return None
    if not bool(current_app.config.get("FITX_TRAINING_START_PREVIEW_ENABLED", False)):
        raise PreviewPreconditionRequired()
    expected = training_start.parse_if_match(raw)
    if not expected:
        raise PreviewPreconditionRequired()
    return expected


@bp.post("/training/plans/general")
@require_mobile_auth
def create_general_training_plan():
    try:
        # In the view, so a rejection is a typed Training 429, never the
        # blueprint's auth-shaped AUTH_RATE_LIMITED.
        with limiter.limit(GENERAL_PLAN_WRITE_RATELIMIT, key_func=_owner_key):
            pass
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
        try:
            include_sessions = mobile_training.sessions_requested(request.args)
        except mobile_training.InvalidSessionsExpansion:
            raise InvalidPlanRequest("include_sessions must be exactly 1") from None
        general_request = parse_general_request(
            request.get_json(silent=True), sessions=include_sessions)
        expected = _accepted_preview()
        result = create_general_plan(
            g.mobile_user, general_request, key,
            expected_candidate_fingerprint=expected)
        payload = mobile_training.project_current_plan(
            result.plan,
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            sessions_enabled=bool(
                current_app.config.get("FITX_WORKOUT_SESSIONS_ENABLED", False)),
            include_sessions=include_sessions,
        )
    except RateLimitExceeded as error:
        return mobile_error(
            "TRAINING_PLAN_RATE_LIMITED",
            "Too many Training plan requests.",
            429,
            True,
            retry_after=error.limit.limit.get_expiry(),
        )
    except PlanGenerationCommandError as error:
        return mobile_error(
            error.public_code,
            "The Training plan request could not be completed.",
            error.http_status,
            error.retryable,
            retry_after=15 if isinstance(error, GenerationInProgress) else None,
        )
    except (mobile_training.PlanUnprojectable,
            mobile_training.TrainingReadUnavailable):
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    except SQLAlchemyError:
        db.session.rollback()
        return mobile_error(
            "TRAINING_PLAN_PERSISTENCE_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )

    response = jsonify({
        "contract_version": 1,
        "origin": general_origin(general_request.template),
        **payload,
    })
    response.status_code = 201
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return response


def _preview_gated(view):
    """Flag OFF: 404 before auth; the rule stays registered."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not bool(current_app.config.get("FITX_TRAINING_START_PREVIEW_ENABLED", False)):
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.post("/training/plans/general/preview")
@_preview_gated
@require_mobile_auth
def preview_general_training_plan():
    """PD2: the deterministic plan Accept would create, plus the canonical
    calorie target. Pure: no ledger row, no plan row, no AI seam. Accept is
    ``POST /training/plans/general`` with ``If-Match: "<candidate_fingerprint>"``.
    """
    try:
        # Its own bucket: previewing never spends the Accept write budget.
        with limiter.limit(GENERAL_PLAN_PREVIEW_RATELIMIT, key_func=_preview_key):
            pass
        try:
            include_sessions = mobile_training.sessions_requested(request.args)
        except mobile_training.InvalidSessionsExpansion:
            raise InvalidPlanRequest("include_sessions must be exactly 1") from None
        general_request = parse_general_request(
            request.get_json(silent=True), sessions=include_sessions)
        preview = preview_general_plan(g.mobile_user, general_request)
        body = training_start.preview_payload(
            g.mobile_user.id,
            method=training_start.METHOD_GENERAL,
            document_json=preview.document_json,
            candidate_fingerprint=preview.candidate_fingerprint,
            include_sessions=include_sessions,
        )
        body["preview"]["origin"] = general_origin(general_request.template)
    except RateLimitExceeded as error:
        return mobile_error(
            "TRAINING_PLAN_RATE_LIMITED",
            "Too many Training plan requests.",
            429,
            True,
            retry_after=error.limit.limit.get_expiry(),
        )
    except PlanGenerationCommandError as error:
        return mobile_error(
            error.public_code,
            "The Training plan request could not be completed.",
            error.http_status,
            error.retryable,
        )
    except (mobile_training.PlanUnprojectable,
            mobile_training.TrainingReadUnavailable):
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    except SQLAlchemyError:
        db.session.rollback()
        return mobile_error(
            "TRAINING_PLAN_PERSISTENCE_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    return jsonify(body)
