"""Idempotent generate-and-persist authority for a user's first native plan."""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from sqlalchemy import inspect

from app.extensions import db
from app.services.ai_spend_guard import subject_scope
from app.services.ai_consent import ConsentDenied
from app.services.training_generation.feature_extractor import build_features

from app.models import (
    TRAINING_PLAN_GENERATION_FAILED,
    TRAINING_PLAN_GENERATION_GENERATED,
    TRAINING_PLAN_GENERATION_IN_PROGRESS,
    TRAINING_PLAN_GENERATION_SUCCEEDED,
    TrainingPlan,
)
from app.services.account_profile import onboarding_state
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
from . import allowance, store


@dataclass(frozen=True)
class GenerationCommandResult:
    plan: TrainingPlan
    replayed: bool


def _required_session(user):
    """The canonical onboarding session, or the typed prerequisite failure.

    Readiness is `account_profile.onboarding_state` — the same rule behind
    `account/me.profile_complete` — so this command can never accept an
    account the account surface calls incomplete, or refuse one it calls
    complete.
    """
    state = onboarding_state(user)
    if not state.complete:
        raise GenerationPrerequisiteMissing()
    return state.session


def _inspect_durable(user_id, key, fingerprint):
    operation = store.find_by_key(user_id, key)
    if operation is None:
        return None, None
    if operation.request_fingerprint != fingerprint:
        raise IdempotencyConflict()
    if operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED:
        result = GenerationCommandResult(
            plan=store.replay_plan(operation, user_id), replayed=True)
        allowance.settle(operation.id)  # a lost settlement is finished, never repeated
        return operation, result
    if operation.status == TRAINING_PLAN_GENERATION_FAILED:
        failure = store.stored_failure(operation)
        allowance.settle(operation.id)
        raise failure
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
    last_session = _required_session(user)
    # Read before the claim commit expires `user`: no extra row reload.
    language = user.language or "tr"
    user_id = user.id

    _operation, result = _inspect_durable(user.id, key, request.fingerprint)
    if result is not None:
        return result

    with try_owner_lock(user_id) as acquired:
        if not acquired:
            raise GenerationInProgress()

        store.retire_abandoned(user_id)
        allowance.settle_pending(user_id)
        operation, result = _inspect_durable(
            user_id, key, request.fingerprint)
        if result is not None:
            return result

        if get_active_plan(user_id) is not None:
            raise ExistingPlanRefused()

        active = store.find_active_for_owner(user_id)
        if active is not None and (operation is None or active.id != operation.id):
            raise GenerationInProgress()

        if operation is not None and operation.status == TRAINING_PLAN_GENERATION_GENERATED:
            operation_id = operation.id
            plan = store.commit_plan(operation_id, user_id)
            allowance.settle(operation_id)
            return GenerationCommandResult(plan=plan, replayed=False)

        # Freeze inputs before claim/dispatch commits expire ORM objects.
        features = build_features(user, last_session, request.preferences)
        if operation is None:
            operation = store.claim(user_id, key, request.fingerprint)
        elif operation.provider_dispatch_possible is not False:
            raise GenerationInProgress()
        operation_id = inspect(operation).identity[0]
        # End any read-only recovery transaction before entering controls.
        db.session.rollback()

    # No advisory connection or operation/owner transaction spans provider I/O.
    authorized = False
    try:
        if allowance.enforcement_enabled():
            # D1 refusal before any hold, permit or SDK call (not authority).
            allowance.preflight_consent(user_id)
        with provider_guard():
            metered = store.authorize_dispatch(operation_id)
            authorized = True
            with subject_scope(user_id), store.attempt_evidence(operation_id, metered):
                candidate = generate_training_plan_candidate(
                    user, last_session, request.preferences, chat_fn,
                    language=language, logger=logger, frozen_features=features)
    except ConsentDenied as error:
        if authorized:
            # Preserve the D1 code while using the existing terminal/refund
            # authority. Attempt evidence still decides metered settlement.
            store.record_failure(operation_id, SimpleNamespace(
                public_code=error.code, http_status=error.status,
                retryable=error.status >= 500))
            allowance.settle(operation_id)
        else:
            store.abandon_claim(operation_id)
        raise
    except GenerationOutputError as error:
        failure = store.record_failure(operation_id, error)
        allowance.settle(operation_id)
        raise failure from None
    except Exception:
        if not authorized:
            store.abandon_claim(operation_id)
        raise

    document_json = json.dumps(
        candidate.document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    store.stage_candidate(operation_id, document_json, candidate.overall_score)
    plan = store.commit_plan(operation_id, user_id)
    # Only after the plan and SUCCEEDED receipt committed: never settle success
    # for a plan that does not exist.
    allowance.settle(operation_id)
    return GenerationCommandResult(plan=plan, replayed=False)
