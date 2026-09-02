"""Explicit durable transitions for native training-plan generation."""
from __future__ import annotations

import json
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    TRAINING_PLAN_GENERATION_FAILED,
    TRAINING_PLAN_GENERATION_GENERATED,
    TRAINING_PLAN_GENERATION_IN_PROGRESS,
    TRAINING_PLAN_GENERATION_SUCCEEDED,
    TrainingPlan,
    TrainingPlanGenerationOperation,
    User,
)
from app.services import premium
from app.services.today_facts import get_active_plan
from app.services.training_generation.preference_contract import (
    CODE_GENERATION_EXERCISE_AMBIGUOUS,
    CODE_GENERATION_EXERCISE_IDENTITY_INVALID,
    CODE_GENERATION_EXERCISE_INCOMPATIBLE,
    CODE_GENERATION_EXERCISE_UNRESOLVED,
    CODE_GENERATION_SCHEMA_INVALID,
    CODE_GENERATION_SEMANTICALLY_INVALID,
    CODE_GENERATION_UNAVAILABLE,
)

from .errors import (
    ExistingPlanRefused,
    GenerationInProgress,
    GenerationPersistenceUnavailable,
    GenerationQuotaExceeded,
    StoredGenerationFailure,
)


MAX_CANDIDATE_BYTES = 256 * 1024
_INVALID_CANDIDATE_CODES = frozenset({
    CODE_GENERATION_SCHEMA_INVALID,
    CODE_GENERATION_SEMANTICALLY_INVALID,
    CODE_GENERATION_EXERCISE_UNRESOLVED,
    CODE_GENERATION_EXERCISE_AMBIGUOUS,
    CODE_GENERATION_EXERCISE_IDENTITY_INVALID,
    CODE_GENERATION_EXERCISE_INCOMPATIBLE,
})


def find_by_key(user_id, key):
    return TrainingPlanGenerationOperation.query.filter_by(
        user_id=user_id, idempotency_key=key).one_or_none()


def find_active_for_owner(user_id):
    return (TrainingPlanGenerationOperation.query.filter(
        TrainingPlanGenerationOperation.user_id == user_id,
        TrainingPlanGenerationOperation.status.in_((
            TRAINING_PLAN_GENERATION_IN_PROGRESS,
            TRAINING_PLAN_GENERATION_GENERATED,
        )),
    ).order_by(TrainingPlanGenerationOperation.id.desc()).first())


def stored_failure(operation):
    return StoredGenerationFailure(
        operation.error_code or "TRAINING_PLAN_GENERATION_COMMAND_FAILED",
        operation.error_http_status or 500,
        bool(operation.error_retryable),
    )


def replay_plan(operation, user_id):
    if not operation.training_plan_id:
        raise GenerationPersistenceUnavailable()
    plan = TrainingPlan.query.filter_by(
        id=operation.training_plan_id, user_id=user_id).one_or_none()
    if plan is None or plan.lineage_id != operation.plan_lineage_id:
        raise GenerationPersistenceUnavailable()
    return plan


def claim(user_id, key, fingerprint):
    operation = TrainingPlanGenerationOperation(
        user_id=user_id,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        status=TRAINING_PLAN_GENERATION_IN_PROGRESS,
        attempt_count=1,
    )
    db.session.add(operation)
    try:
        if current_app.config.get("AI_PLAN_QUOTA_ENABLED", True):
            week = premium.reserve_ai_quota_in_transaction(
                user_id, "training", premium.FREE_WEEKLY_AI_PLANS)
            owner = db.session.get(User, user_id)
            if owner is None:
                raise GenerationPersistenceUnavailable()
            if not owner.is_premium and week is None:
                raise GenerationQuotaExceeded()
            operation.quota_reserved = week is not None
            operation.quota_week = week
        db.session.commit()
        return operation
    except (GenerationQuotaExceeded, GenerationPersistenceUnavailable):
        db.session.rollback()
        raise
    except IntegrityError as error:
        db.session.rollback()
        raise GenerationInProgress() from error
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def abandon_claim(operation_id):
    """Remove a pre-provider claim when entering provider controls failed."""
    try:
        operation = db.session.get(TrainingPlanGenerationOperation, operation_id)
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS:
            db.session.rollback()
            return
        if operation.quota_reserved:
            premium.refund_ai_quota_in_transaction(
                operation.user_id, "training", operation.quota_week)
        db.session.delete(operation)
        db.session.commit()
    except Exception:
        db.session.rollback()


def begin_resumed_attempt(operation_id):
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS:
            raise GenerationPersistenceUnavailable()
        operation.attempt_count += 1
        operation.updated_at = datetime.utcnow()
        db.session.commit()
        return operation
    except GenerationPersistenceUnavailable:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def stage_candidate(operation_id, document_json, score):
    if not isinstance(document_json, str) or len(document_json.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise GenerationPersistenceUnavailable()
    try:
        # Fail closed if a caller somehow supplies non-JSON recovery material.
        parsed = json.loads(document_json)
        if not isinstance(parsed, dict):
            raise ValueError("candidate must be an object")
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS:
            raise GenerationPersistenceUnavailable()
        operation.candidate_plan_data = document_json
        operation.candidate_score = score
        operation.status = TRAINING_PLAN_GENERATION_GENERATED
        operation.updated_at = datetime.utcnow()
        db.session.commit()
    except GenerationPersistenceUnavailable:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def record_failure(operation_id, error):
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).with_for_update().one_or_none())
        if operation is None:
            raise GenerationPersistenceUnavailable()
        if operation.quota_reserved:
            premium.refund_ai_quota_in_transaction(
                operation.user_id, "training", operation.quota_week)
        operation.status = TRAINING_PLAN_GENERATION_FAILED
        operation.candidate_plan_data = None
        operation.candidate_score = None
        operation.quota_reserved = False
        public_code = str(getattr(
            error, "public_code", "TRAINING_PLAN_GENERATION_COMMAND_FAILED"))[:64]
        http_status = int(getattr(error, "http_status", 500))
        retryable = bool(getattr(error, "retryable", False))
        if public_code == CODE_GENERATION_UNAVAILABLE:
            http_status, retryable = 503, True
        elif public_code in _INVALID_CANDIDATE_CODES:
            http_status, retryable = 422, False
        operation.error_code = public_code
        operation.error_http_status = http_status
        operation.error_retryable = retryable
        operation.updated_at = datetime.utcnow()
        operation.completed_at = datetime.utcnow()
        db.session.commit()
        return stored_failure(operation)
    except GenerationPersistenceUnavailable:
        db.session.rollback()
        raise
    except Exception as persistence_error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from persistence_error


def _insert_plan(**kwargs):
    plan = TrainingPlan(**kwargs)
    db.session.add(plan)
    return plan


def commit_plan(operation_id, user_id):
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id, user_id=user_id)
                     .with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_GENERATED:
            raise GenerationPersistenceUnavailable()
        if get_active_plan(user_id) is not None:
            raise ExistingPlanRefused()
        plan = _insert_plan(
            user_id=user_id,
            plan_data=operation.candidate_plan_data,
            score=operation.candidate_score,
        )
        db.session.flush()
        operation.status = TRAINING_PLAN_GENERATION_SUCCEEDED
        operation.training_plan_id = plan.id
        operation.plan_lineage_id = plan.lineage_id
        operation.candidate_plan_data = None
        operation.candidate_score = None
        operation.error_code = None
        operation.error_http_status = None
        operation.error_retryable = None
        operation.updated_at = datetime.utcnow()
        operation.completed_at = datetime.utcnow()
        db.session.commit()
        return plan
    except ExistingPlanRefused:
        db.session.rollback()
        raise
    except GenerationPersistenceUnavailable:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error
