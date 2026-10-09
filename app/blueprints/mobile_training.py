"""Bearer-authenticated native Training routes."""
from contextlib import contextmanager
from functools import partial

from flask import current_app, g, jsonify, request
from flask_limiter.errors import RateLimitExceeded

from app.blueprints.mobile_api import bp, mobile_error
from app.config import AI_RATELIMIT, BEDROCK_RATELIMIT
from app.extensions import limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.services import mobile_training
from app.services.ai import _heavy_complete as _heavy_chat
from app.services.ai_gate import BlockingConcurrencyLimit, blocking_concurrency_slot
from app.services.mobile_training_generation import (
    GenerationInProgress,
    PlanGenerationCommandError,
    generate_and_persist,
    parse_idempotency_key,
    parse_native_request,
)
from app.services.training_generation.preference_contract import PreferenceContractError


def _sessions_enabled():
    return bool(current_app.config.get("FITX_WORKOUT_SESSIONS_ENABLED", False))


@contextmanager
def _native_generation_provider_guard():
    with limiter.limit(AI_RATELIMIT, key_func=lambda: str(g.mobile_user.id)):
        with limiter.limit(
                BEDROCK_RATELIMIT, key_func=lambda: str(g.mobile_user.id)):
            with blocking_concurrency_slot():
                yield


def _generation_error(error, *, retry_after=None):
    return mobile_error(
        error.public_code,
        "The Training plan request could not be completed.",
        error.http_status,
        error.retryable,
        retry_after=retry_after,
    )


@bp.post("/training/plans")
@require_mobile_auth
def create_training_plan():
    try:
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
        native_request = parse_native_request(request.get_json(silent=True))
        result = generate_and_persist(
            g.mobile_user,
            native_request,
            key,
            chat_fn=partial(_heavy_chat, feature="training_plan"),
            provider_guard=_native_generation_provider_guard,
            logger=current_app.logger,
        )
        payload = mobile_training.project_current_plan(
            result.plan,
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            sessions_enabled=_sessions_enabled(),
        )
    except PreferenceContractError as error:
        return mobile_error(
            error.public_code,
            "The Training preferences are not supported.",
            error.http_status,
            error.retryable,
        )
    except PlanGenerationCommandError as error:
        retry_after = 15 if isinstance(error, GenerationInProgress) else None
        return _generation_error(error, retry_after=retry_after)
    except RateLimitExceeded as error:
        retry_after = error.limit.limit.get_expiry()
        return mobile_error(
            "TRAINING_PLAN_RATE_LIMITED",
            "Too many Training plan generation requests.",
            429,
            True,
            retry_after=retry_after,
        )
    except BlockingConcurrencyLimit:
        return mobile_error(
            "TRAINING_PLAN_GENERATION_BUSY",
            "Training plan generation is temporarily busy.",
            503,
            True,
            retry_after=15,
        )
    except (mobile_training.PlanUnprojectable,
            mobile_training.TrainingReadUnavailable):
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )

    response = jsonify(payload)
    response.status_code = 201
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return response


@bp.get("/training/preferences")
@require_mobile_auth
def training_preferences():
    return jsonify(mobile_training.preference_contract())


@bp.get("/training/plans/current")
@require_mobile_auth
def current_training_plan():
    try:
        payload = mobile_training.build_current_plan(
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            sessions_enabled=_sessions_enabled(),
        )
    except mobile_training.PlanUnprojectable:
        return mobile_error(
            "TRAINING_PLAN_UNPROJECTABLE",
            "The current Training plan cannot be displayed safely.",
            409,
            False,
        )
    except mobile_training.TrainingReadUnavailable:
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    return jsonify(payload)


@bp.get("/training/workouts/<workout_reference>")
@require_mobile_auth
def training_workout(workout_reference):
    try:
        payload = mobile_training.build_workout(
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            workout_reference,
        )
    except mobile_training.WorkoutNotFound:
        return mobile_error(
            "TRAINING_WORKOUT_NOT_FOUND",
            "The Training workout was not found.",
            404,
            False,
        )
    except mobile_training.WorkoutStale:
        return mobile_error(
            "TRAINING_WORKOUT_STALE",
            "The Training workout reference is stale.",
            409,
            False,
        )
    except mobile_training.PlanUnprojectable:
        return mobile_error(
            "TRAINING_PLAN_UNPROJECTABLE",
            "The current Training plan cannot be displayed safely.",
            409,
            False,
        )
    except mobile_training.TrainingReadUnavailable:
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    return jsonify(payload)


@bp.post("/training/plans/replacement-proposals")
@require_mobile_auth
def create_replacement_proposal():
    from app.extensions import db
    from app.services.training_plan_replacement.generation import generate_proposal
    from sqlalchemy.exc import SQLAlchemyError
    try:
        key = parse_idempotency_key(request.headers.get('Idempotency-Key'))
        command = parse_native_request(request.get_json(silent=True))
        result = generate_proposal(g.mobile_user, command, key,
            chat_fn=partial(_heavy_chat, feature='training_plan'),
            provider_guard=_native_generation_provider_guard)
    except PreferenceContractError as error:
        return mobile_error(error.public_code, 'The Training preferences are not supported.', error.http_status, error.retryable)
    except PlanGenerationCommandError as error:
        return _generation_error(error, retry_after=15 if isinstance(error, GenerationInProgress) else getattr(error, 'retry_after', None))
    except RateLimitExceeded as error:
        return mobile_error('TRAINING_PLAN_RATE_LIMITED', 'Too many Training plan generation requests.', 429, False,
                            retry_after=error.limit.limit.get_expiry())
    except BlockingConcurrencyLimit:
        return mobile_error('TRAINING_PLAN_GENERATION_BUSY', 'Training plan generation is temporarily busy.', 503, False)
    except (SQLAlchemyError, ValueError, mobile_training.PlanUnprojectable):
        db.session.rollback()
        return mobile_error('TRAINING_PLAN_PERSISTENCE_UNAVAILABLE', 'Training is temporarily unavailable.', 503, True)
    response = jsonify({'contract_version': 1, 'proposal_token': result.proposal_token,
                        'expires_at': mobile_training._utc_iso(result.expires_at), 'candidate': result.review})
    response.status_code = 201
    response.headers['Idempotency-Replayed'] = str(result.replayed).lower()
    return response


@bp.post("/training/plans/replacement/confirm")
@require_mobile_auth
def confirm_replacement_proposal():
    import re
    from app.extensions import db
    from app.services.training_plan_replacement import service as authority
    from app.services.mobile_training_generation.errors import InvalidPlanRequest
    from sqlalchemy.exc import SQLAlchemyError
    try:
        key = parse_idempotency_key(request.headers.get('Idempotency-Key'))
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {'proposal_token', 'confirmed'}
                or body['confirmed'] is not True or not isinstance(body['proposal_token'], str)
                or re.fullmatch(r'[A-Za-z0-9_-]{43}', body['proposal_token']) is None):
            raise InvalidPlanRequest()
        result = authority.confirm_replacement(g.mobile_user.id, body['proposal_token'], key)
    except PlanGenerationCommandError as error:
        return _generation_error(error)
    except authority.ConfirmationConflict:
        return mobile_error('TRAINING_PLAN_IDEMPOTENCY_CONFLICT', 'The confirmation key has a different intent.', 409, False)
    except authority.ProposalUnavailable:
        return mobile_error('TRAINING_PLAN_PROPOSAL_UNAVAILABLE', 'The Training proposal is unavailable.', 404, False)
    except authority.ReplacementRefusal as error:
        codes = {'STALE': 'TRAINING_PLAN_STALE_CURRENT_PLAN',
                 'ACTIVE_REFUSED': 'TRAINING_PLAN_ACTIVE_SESSION_REFUSED',
                 'COACH_PENDING_REFUSED': 'TRAINING_PLAN_COACH_PENDING_REFUSED',
                 'EXPIRED': 'TRAINING_PLAN_PROPOSAL_EXPIRED', 'CONSUMED': 'TRAINING_PLAN_PROPOSAL_CONSUMED'}
        response = mobile_error(codes[error.reason], 'The Training replacement could not be applied.', 409, False)
        response.headers['Idempotency-Replayed'] = str(error.result.replayed).lower()
        return response
    except SQLAlchemyError:
        db.session.rollback()
        return mobile_error('TRAINING_PLAN_PERSISTENCE_UNAVAILABLE', 'Training is temporarily unavailable.', 503, True)
    response = jsonify({'contract_version': 1, 'outcome': 'applied', 'reread_required': True})
    response.headers['Idempotency-Replayed'] = str(result.replayed).lower()
    return response
