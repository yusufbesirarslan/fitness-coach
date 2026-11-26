"""Idempotent generate-and-persist authority for a user's first native plan."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from sqlalchemy import inspect

from app.extensions import db
from app.services import ai, ai_consent
from app.services.ai_spend_guard import subject_scope
from app.services.ai_consent import ConsentDenied
from app.services.training_generation import youth_safety
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
    PreviewAlreadyAccepted,
    PreviewDiscarded,
    PreviewExpired,
    PreviewNotFound,
    PreviewPending,
    PreviewStale,
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


def _training_start_enabled() -> bool:
    from flask import current_app
    return bool(current_app.config.get("FITX_TRAINING_START_PREVIEW_ENABLED", False))


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



def _dispatch_and_generate(user, last_session, request, operation_id, features, *,
                           chat_fn, provider_guard, logger, language, user_id):
    """Provider controls -> dispatch authorization -> one candidate, or the
    recorded terminal failure. Shared verbatim by generate-and-persist and the
    PD2 preview so the two cannot drift. Returns ``(candidate, authority)``."""
    # No advisory connection or operation/owner transaction spans provider I/O.
    authorized = False
    try:
        if allowance.enforcement_enabled():
            # D1 refusal before any hold, permit or SDK call (not authority).
            allowance.preflight_consent(user_id)
        with provider_guard():
            metered = store.authorize_dispatch(operation_id)
            authorized = True
            # ONE immutable authority for every admission of this operation
            # (retry, fallback, repair), its staging and its canonical write.
            recipient, model = ai.heavy_primary_route()
            authority = ai_consent.begin_operation(user_id, "training_plan", recipient, model)
            with (subject_scope(user_id), store.attempt_evidence(operation_id, metered),
                  ai_consent.operation_scope(authority)):
                candidate = generate_training_plan_candidate(
                    user, last_session, request.preferences, chat_fn,
                    language=language, logger=logger, frozen_features=features,
                    sessions=request.sessions)
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
    return candidate, authority


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
    allowance.require_all_feature_ready()
    if not isinstance(request, NativePlanRequest):
        raise TypeError("request must be NativePlanRequest")
    last_session = _required_session(user)
    # Read before the claim commit expires `user`: no extra row reload.
    language = user.language or "tr"
    user_id = user.id

    _operation, result = _inspect_durable(user.id, key, request.fingerprint)
    if result is not None:
        return result
    # Youth boundary before any claim, hold or provider work (adults: no-op).
    youth_safety.require_supported(
        youth_safety.effective_age(getattr(last_session, "age", None),
                                   getattr(user, "age", None)),
        request.preferences)

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
        if (active is not None and active.status == TRAINING_PLAN_GENERATION_GENERATED
                and is_preview_operation(active)):
            # PD2 rollback safety: generate-and-persist can never activate a
            # preview (its fingerprint domain differs). While PD2 is ON an
            # unexpired preview is still the owner's to accept or discard, so
            # it is never ended before THIS request's admission is known;
            # with PD2 OFF (or once expired) it is stranded and is ended.
            if _training_start_enabled() and not _expired(active):
                raise PreviewPending()
            for ended in store.discard_generated(user_id, operation_id=active.id):
                allowance.settle(ended)
            active = store.find_active_for_owner(user_id)
        if (active is not None and active.status == TRAINING_PLAN_GENERATION_GENERATED
                and (operation is None or active.id != operation.id)):
            # Withdrawn GENERATED work can never commit: end it instead of
            # letting it block this owner forever (LP21 no stranded operation).
            refused = store.refuse_stale_generated(user_id)
            if refused is not None:
                allowance.settle(refused)
                active = None
        if active is not None and (operation is None or active.id != operation.id):
            raise GenerationInProgress()

        if operation is not None and operation.status == TRAINING_PLAN_GENERATION_GENERATED:
            # Recovery commits only under the candidate's ORIGINAL authority.
            operation_id = operation.id
            try:
                plan = store.commit_plan(operation_id, user_id)
            finally:
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

    candidate, authority = _dispatch_and_generate(
        user, last_session, request, operation_id, features,
        chat_fn=chat_fn, provider_guard=provider_guard, logger=logger,
        language=language, user_id=user_id)

    document_json = json.dumps(
        candidate.document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    try:
        store.stage_candidate(operation_id, document_json, candidate.overall_score, authority)
        plan = store.commit_plan(operation_id, user_id)
    except ConsentDenied:
        # Withdrawn before the write: no plan, terminal refusal recorded by the
        # store; settlement reads the true attempt evidence.
        allowance.settle(operation_id)
        raise
    # Only after the plan and SUCCEEDED receipt committed: never settle success
    # for a plan that does not exist.
    allowance.settle(operation_id)
    return GenerationCommandResult(plan=plan, replayed=False)


# --- PD2 training start: AI draft -> preview -> accept ----------------------
#
# The pending draft IS the existing ledger's GENERATED operation: the staged
# candidate bound to its consent authority, never a ``training_plan`` row, so
# Today, active-plan reads, rewards and analytics cannot see it. Accept is the
# existing ``store.commit_plan`` writer (the same one legacy recovery uses),
# pinned to the exact previewed text. No new plan store, no new plan writer.

PREVIEW_TTL = timedelta(hours=24)
_PREVIEW_FP_PREFIX = store.PREVIEW_FINGERPRINT_PREFIX
_PREVIEW_FP_DOMAIN = b"axisai:training-start-preview:v1\0"


def preview_fingerprint(request: NativePlanRequest) -> str:
    """Domain-separated preview identity (64 chars, the column width).

    Never equal to a legacy fingerprint, so a legacy generate-and-persist
    retry under a preview key is an IdempotencyConflict: a preview can only
    ever be activated through ``accept_preview``.
    """
    digest = hashlib.sha256(
        _PREVIEW_FP_DOMAIN + request.fingerprint.encode("ascii")).hexdigest()
    return _PREVIEW_FP_PREFIX + digest[:60]


is_preview_operation = store.is_preview_operation


@dataclass(frozen=True)
class PreviewResult:
    document_json: str
    candidate_fingerprint: str
    expires_at: datetime
    replayed: bool


def _expired(operation, now=None) -> bool:
    staged_at = operation.updated_at or operation.created_at
    return staged_at is None or staged_at + PREVIEW_TTL <= (now or datetime.utcnow())


def _end_expired(user_id, operation_id):
    for ended in store.discard_generated(user_id, operation_id=operation_id):
        allowance.settle(ended)
    raise PreviewExpired()


def _replay_preview(operation, user_id) -> PreviewResult:
    operation_id = operation.id
    if _expired(operation):
        _end_expired(user_id, operation_id)
    try:
        document_json, _score, staged_at = store.read_candidate(operation_id, user_id)
    except ConsentDenied:
        allowance.settle(operation_id)  # a definitive refusal just ended it
        raise
    return PreviewResult(
        document_json=document_json,
        candidate_fingerprint=store.candidate_sha(document_json),
        expires_at=staged_at + PREVIEW_TTL,
        replayed=True)


def _inspect_preview(user_id, key, fingerprint):
    operation = store.find_by_key(user_id, key)
    if operation is None:
        return None, None
    if operation.request_fingerprint != fingerprint:
        raise IdempotencyConflict()
    if operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED:
        allowance.settle(operation.id)
        raise PreviewAlreadyAccepted()
    if operation.status == TRAINING_PLAN_GENERATION_FAILED:
        failure = store.stored_failure(operation)
        allowance.settle(operation.id)
        raise failure
    if operation.status == TRAINING_PLAN_GENERATION_GENERATED:
        # Same key, same request: show the SAME staged candidate again. No
        # claim, no hold, no provider call (no regeneration on rebuild/restart).
        return operation, _replay_preview(operation, user_id)
    if operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS:
        raise store.GenerationPersistenceUnavailable()
    return operation, None


def generate_preview(
    user,
    request: NativePlanRequest,
    key: str,
    *,
    chat_fn,
    provider_guard,
    logger=None,
) -> PreviewResult:
    """Generate ONE AI candidate and stage it as the owner's pending draft.

    Identical admission to ``generate_and_persist`` (onboarding, youth
    boundary, owner arbitration, allowance/quota claim, D1 dispatch
    authorization, consent operation authority) -- it only stops after
    ``stage_candidate``: nothing is committed, no plan row exists. An
    unexpired pending candidate must be discarded before a new preview.
    """
    allowance.require_all_feature_ready()
    if not isinstance(request, NativePlanRequest):
        raise TypeError("request must be NativePlanRequest")
    last_session = _required_session(user)
    language = user.language or "tr"
    user_id = user.id
    fingerprint = preview_fingerprint(request)

    _operation, result = _inspect_preview(user_id, key, fingerprint)
    if result is not None:
        return result
    youth_safety.require_supported(
        youth_safety.effective_age(getattr(last_session, "age", None),
                                   getattr(user, "age", None)),
        request.preferences)

    with try_owner_lock(user_id) as acquired:
        if not acquired:
            raise GenerationInProgress()

        store.retire_abandoned(user_id)
        allowance.settle_pending(user_id)
        operation, result = _inspect_preview(user_id, key, fingerprint)
        if result is not None:
            return result

        if get_active_plan(user_id) is not None:
            raise ExistingPlanRefused()

        active = store.find_active_for_owner(user_id)
        if (active is not None and active.status == TRAINING_PLAN_GENERATION_GENERATED
                and is_preview_operation(active)
                and (operation is None or active.id != operation.id)):
            if not _expired(active):
                # A delivered draft is never destroyed before THIS request's
                # admission is known: the owner discards it explicitly
                # (Choose another / Edit with changed preferences).
                raise PreviewPending()
            # An expired draft has no value left: end it (consumed).
            for ended in store.discard_generated(user_id, operation_id=active.id):
                allowance.settle(ended)
            active = store.find_active_for_owner(user_id)
        if active is not None and (operation is None or active.id != operation.id):
            raise GenerationInProgress()

        features = build_features(user, last_session, request.preferences)
        if operation is None:
            operation = store.claim(user_id, key, fingerprint)
        elif operation.provider_dispatch_possible is not False:
            raise GenerationInProgress()
        operation_id = inspect(operation).identity[0]
        db.session.rollback()

    candidate, authority = _dispatch_and_generate(
        user, last_session, request, operation_id, features,
        chat_fn=chat_fn, provider_guard=provider_guard, logger=logger,
        language=language, user_id=user_id)

    document_json = json.dumps(
        candidate.document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    try:
        store.stage_candidate(operation_id, document_json, candidate.overall_score, authority)
    except ConsentDenied:
        allowance.settle(operation_id)
        raise
    staged = store.find_by_key(user_id, key)
    staged_at = staged.updated_at if staged is not None else datetime.utcnow()
    db.session.rollback()
    return PreviewResult(
        document_json=document_json,
        candidate_fingerprint=store.candidate_sha(document_json),
        expires_at=staged_at + PREVIEW_TTL,
        replayed=False)


def _preview_operation(user_id, key):
    operation = store.find_by_key(user_id, key)
    if not is_preview_operation(operation):
        raise PreviewNotFound()
    return operation


def accept_preview(user, key: str, candidate_fingerprint: str) -> GenerationCommandResult:
    """Activate exactly the previewed candidate, once; replay thereafter.

    Writer = ``store.commit_plan`` (operation row -> owner row -> plan
    absence check -> insert), pinned by ``expected_candidate_sha``. A
    repeated Accept replays the committed plan: no second plan, no second
    charge (the allowance hold settles once per operation).
    """
    allowance.require_all_feature_ready()
    user_id = user.id
    with try_owner_lock(user_id) as acquired:
        if not acquired:
            raise GenerationInProgress()
        operation = _preview_operation(user_id, key)
        operation_id = operation.id
        if operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED:
            plan = store.replay_plan(operation, user_id)
            allowance.settle(operation_id)
            return GenerationCommandResult(plan=plan, replayed=True)
        if operation.status == TRAINING_PLAN_GENERATION_FAILED:
            allowance.settle(operation_id)
            if operation.error_code == store.CANDIDATE_DISCARDED_CODE:
                raise PreviewDiscarded()
            raise store.stored_failure(operation)
        if operation.status != TRAINING_PLAN_GENERATION_GENERATED:
            raise GenerationInProgress()
        if _expired(operation):
            _end_expired(user_id, operation_id)
        db.session.rollback()
        try:
            plan = store.commit_plan(
                operation_id, user_id, expected_candidate_sha=candidate_fingerprint)
        except store.CandidateMismatch:
            raise PreviewStale() from None
        finally:
            allowance.settle(operation_id)
        return GenerationCommandResult(plan=plan, replayed=False)


def discard_preview(user, key: str) -> None:
    """Cancel / choose another method: end the pending candidate; idempotent.

    Never touches an active plan. A preview that was already accepted is
    refused (409) rather than silently ignored.
    """
    user_id = user.id
    with try_owner_lock(user_id) as acquired:
        if not acquired:
            raise GenerationInProgress()
        operation = _preview_operation(user_id, key)
        operation_id = operation.id
        if operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED:
            raise PreviewAlreadyAccepted()
        if operation.status == TRAINING_PLAN_GENERATION_FAILED:
            allowance.settle(operation_id)
            return
        if operation.status != TRAINING_PLAN_GENERATION_GENERATED:
            raise GenerationInProgress()
        db.session.rollback()
        for ended in store.discard_generated(user_id, operation_id=operation_id):
            allowance.settle(ended)
