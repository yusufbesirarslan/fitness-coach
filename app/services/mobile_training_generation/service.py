"""Idempotent generate-and-persist authority for a user's first native plan."""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.models import (
    TRAINING_PLAN_GENERATION_FAILED,
    TRAINING_PLAN_GENERATION_GENERATED,
    TRAINING_PLAN_GENERATION_IN_PROGRESS,
    TRAINING_PLAN_GENERATION_SUCCEEDED,
    TrainingPlan,
    UserSession,
)
from app.services.today_facts import get_active_plan
from app.services.training_generation.output_errors import GenerationOutputError
from app.services.training_generation.service import generate_training_plan_candidate

from .contract import NativePlanRequest
from .errors import (
    ExistingPlanRefused,
    GenerationInProgress,
    GenerationPrerequisiteMissing,
    IdempotencyConflict,
)
from .locking import try_owner_lock
from . import store


@dataclass(frozen=True)
class GenerationCommandResult:
    plan: TrainingPlan
    replayed: bool


def _required_session(user_id):
    session = (UserSession.query.filter_by(user_id=user_id)
               .order_by(UserSession.created_at.desc()).first())
    if session is None:
        raise GenerationPrerequisiteMissing()
    return session


def _inspect_durable(user_id, key, fingerprint):
    operation = store.find_by_key(user_id, key)
    if operation is None:
        return None, None
    if operation.request_fingerprint != fingerprint:
        raise IdempotencyConflict()
    if operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED:
        return operation, GenerationCommandResult(
            plan=store.replay_plan(operation, user_id), replayed=True)
    if operation.status == TRAINING_PLAN_GENERATION_FAILED:
        raise store.stored_failure(operation)
    if operation.status not in (
            TRAINING_PLAN_GENERATION_IN_PROGRESS,
            TRAINING_PLAN_GENERATION_GENERATED):
        raise store.GenerationPersistenceUnavailable()
    return operation, None


def generate_and_persist(
    user,
    request: NativePlanRequest,
    key: str,
    *,
    chat_fn,
    provider_guard,
    logger=None,
) -> GenerationCommandResult:
    """Generate and atomically persist one first plan, or replay its outcome."""
    if not isinstance(request, NativePlanRequest):
        raise TypeError("request must be NativePlanRequest")
    last_session = _required_session(user.id)

    _operation, result = _inspect_durable(user.id, key, request.fingerprint)
    if result is not None:
        return result

    with try_owner_lock(user.id) as acquired:
        if not acquired:
            raise GenerationInProgress()

        operation, result = _inspect_durable(
            user.id, key, request.fingerprint)
        if result is not None:
            return result

        if get_active_plan(user.id) is not None:
            raise ExistingPlanRefused()

        active = store.find_active_for_owner(user.id)
        if active is not None and (operation is None or active.id != operation.id):
            raise GenerationInProgress()

        if operation is not None and operation.status == TRAINING_PLAN_GENERATION_GENERATED:
            return GenerationCommandResult(
                plan=store.commit_plan(operation.id, user.id), replayed=False)

        fresh_claim = operation is None
        if fresh_claim:
            operation = store.claim(user.id, key, request.fingerprint)
        else:
            operation = store.begin_resumed_attempt(operation.id)

        try:
            with provider_guard():
                candidate = generate_training_plan_candidate(
                    user,
                    last_session,
                    request.preferences,
                    chat_fn,
                    language=getattr(user, "preferred_language", None) or "tr",
                    logger=logger,
                )
        except GenerationOutputError as error:
            raise store.record_failure(operation.id, error) from None
        except Exception:
            if fresh_claim:
                store.abandon_claim(operation.id)
            raise

        document_json = json.dumps(
            candidate.document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        store.stage_candidate(
            operation.id, document_json, candidate.overall_score)
        return GenerationCommandResult(
            plan=store.commit_plan(operation.id, user.id), replayed=False)
