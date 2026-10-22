"""Explicit durable transitions for native training-plan generation."""
from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import sqlalchemy as sa
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
from app.services import ai_consent, ai_provider_call, premium
from app.services.plan_owner_lock import lock_plan_owner
from app.services.today_facts import get_active_plan
from app.services.training_generation.preference_contract import (
    CODE_GENERATION_UNAVAILABLE,
)

from . import allowance
from .errors import (
    INVALID_CANDIDATE_CODES as _INVALID_CANDIDATE_CODES,
    ExistingPlanRefused,
    GenerationInProgress,
    GenerationPersistenceUnavailable,
    GenerationQuotaExceeded,
    StoredGenerationFailure,
    TrainingAllowanceRefused,
)


ABANDON_AFTER = timedelta(minutes=15)

MAX_CANDIDATE_BYTES = 256 * 1024


def find_by_key(user_id, key):
    return TrainingPlanGenerationOperation.query.filter_by(
        user_id=user_id, idempotency_key=key).populate_existing().one_or_none()


def find_active_for_owner(user_id):
    return (TrainingPlanGenerationOperation.query.filter(
        TrainingPlanGenerationOperation.user_id == user_id,
        TrainingPlanGenerationOperation.status.in_((
            TRAINING_PLAN_GENERATION_IN_PROGRESS,
            TRAINING_PLAN_GENERATION_GENERATED,
        )),
    ).order_by(TrainingPlanGenerationOperation.id.desc()).first())


def stored_failure(operation):
    # `error_retryable` is the recorded cause classification (transient, so a
    # new key may succeed). It is deliberately not projected: the public flag
    # answers "will the same key make progress?", and for FAILED it never will.
    return StoredGenerationFailure(
        operation.error_code or "TRAINING_PLAN_GENERATION_COMMAND_FAILED",
        operation.error_http_status or 500,
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
        provider_dispatch_possible=False,
    )
    db.session.add(operation)
    try:
        if allowance.enforcement_enabled():
            # Metered: the AI allowance ledger is the one quota authority; the
            # hold is taken at dispatch authorization, never here.
            operation.allowance_metered = True
        elif current_app.config.get("AI_PLAN_QUOTA_ENABLED", True):
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
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).populate_existing()
                     .with_for_update().one_or_none())
        if (operation is None
                or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS
                or operation.provider_dispatch_possible is not False):
            db.session.rollback()
            return
        if operation.quota_reserved:
            premium.refund_ai_quota_in_transaction(
                operation.user_id, "training", operation.quota_week)
        db.session.delete(operation)
        db.session.commit()
    except Exception:
        db.session.rollback()


def authorize_dispatch(operation_id):
    """Only the transaction changing False -> True may invoke generation.

    Commit uncertainty BEFORE the network door. A lost commit acknowledgement
    cannot authorize a call: the caller must receive a successful return.
    A metered operation's AI allowance hold is created in this same commit,
    stamped with the same instant as the marker (see ``allowance``), with
    ``provider_attempt_possible`` False. Returns whether it is metered.
    """
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).populate_existing()
                     .with_for_update().one_or_none())
        if (operation is None or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS
                or operation.provider_dispatch_possible is not False):
            raise GenerationInProgress()
        now = datetime.now(timezone.utc)
        allowance.bind_for_dispatch(operation, now)
        metered = operation.allowance_metered is True
        operation.provider_dispatch_possible = True
        operation.updated_at = now.replace(tzinfo=None)
        db.session.commit()
        return metered
    except (GenerationInProgress, TrainingAllowanceRefused):
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def mark_attempt_possible(operation_id):
    """Commit "a physical provider attempt may start" BEFORE that SDK call.

    Runs after the final consent check passed and before every physical
    invocation (``ai_provider_call.attempt_observer``). Only an IN_PROGRESS,
    dispatched, tracked operation can record it; a retired, deleted or
    unreachable one refuses the attempt rather than send it unrecorded. The
    marker only moves False -> True and leaves ``updated_at`` (retirement
    timing) untouched. Short own transaction; no ledger lock.
    """
    operation = TrainingPlanGenerationOperation
    try:
        if db.engine.dialect.name == "postgresql":
            db.session.execute(sa.text("SET LOCAL lock_timeout = '2000ms'"))
        marked = db.session.execute(
            sa.update(operation)
            .where(operation.id == operation_id,
                   operation.status == TRAINING_PLAN_GENERATION_IN_PROGRESS,
                   operation.provider_dispatch_possible.is_(True),
                   operation.provider_attempt_possible.is_not(None))
            .values(provider_attempt_possible=True, updated_at=operation.updated_at)
            .execution_options(synchronize_session=False)).rowcount
        if marked != 1:
            raise GenerationPersistenceUnavailable()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise ai_provider_call.AttemptEvidenceUnavailable() from None


def attempt_evidence(operation_id, tracked):
    """Scope generation so every physical attempt is recorded first (metered only)."""
    if not tracked:
        return nullcontext()
    return ai_provider_call.attempt_observer(lambda: mark_attempt_possible(operation_id))


def retire_abandoned(user_id):
    """Bound active occupancy; retain dispatch uncertainty and legacy quota.

    Called under the short owner arbitration lock. A late worker is fenced by
    the terminal status at staging/failure persistence. Metered retirement
    also orders against D1's existing account fence, which spans each SDK call.
    Contention refuses retirement for now; it never waits across provider I/O.
    This is not remote cancellation or proof that a timed-out request stopped.
    """
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(user_id=user_id, status=TRAINING_PLAN_GENERATION_IN_PROGRESS)
                     .populate_existing().with_for_update().one_or_none())
        if operation is not None and operation.updated_at <= datetime.utcnow() - ABANDON_AFTER:
            if operation.allowance_metered is True:
                # D1 owns user -> operation during attempt admission. Never
                # WAIT for its user fence while holding operation: NOWAIT
                # aborts and releases operation if D1/revoke/deletion owns user.
                # These locks span only retirement, never provider I/O.
                owner = db.session.execute(
                    sa.select(User.id).where(User.id == user_id)
                    .with_for_update(nowait=True)).scalar_one_or_none()
                if owner is None:
                    raise GenerationPersistenceUnavailable()
            operation.status = TRAINING_PLAN_GENERATION_FAILED
            operation.error_code = "TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN"
            operation.error_http_status = 503
            operation.error_retryable = False
            operation.completed_at = datetime.utcnow()
            # Never refund an ambiguous dispatch or erase its evidence.
            if operation.provider_dispatch_possible is False:
                operation.error_code = "TRAINING_PLAN_GENERATION_NOT_DISPATCHED"
                if operation.quota_reserved:
                    premium.refund_ai_quota_in_transaction(
                        user_id, "training", operation.quota_week)
                    operation.quota_reserved = False
            db.session.commit()
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def consent_failure(error):
    """A D1 refusal as the #450 terminal failure record (code + status kept)."""
    return SimpleNamespace(public_code=error.code, http_status=error.status,
                           retryable=error.status >= 500)


def stage_candidate(operation_id, document_json, score, authority):
    """Stage provider output under its operation authority, or withhold it.

    Lock order operation -> owner (``commit_plan``'s). A refused fence never
    stores the output: the operation ends FAILED with the D1 code, attempt
    evidence untouched, and the caller settles.
    """
    if not isinstance(document_json, str) or len(document_json.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise GenerationPersistenceUnavailable()
    try:
        # Fail closed if a caller somehow supplies non-JSON recovery material.
        parsed = json.loads(document_json)
        if not isinstance(parsed, dict):
            raise ValueError("candidate must be an object")
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).populate_existing().with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS:
            raise GenerationPersistenceUnavailable()
        if getattr(authority, "owner", None) != operation.user_id:
            raise ai_consent.ConsentDenied()
        ai_consent.fence(authority)
        operation.candidate_plan_data = ai_consent.bind_candidate(authority, document_json)
        operation.candidate_score = score
        operation.status = TRAINING_PLAN_GENERATION_GENERATED
        operation.updated_at = datetime.utcnow()
        db.session.commit()
    except ai_consent.ConsentDenied as denied:
        db.session.rollback()
        try:
            record_failure(operation_id, consent_failure(denied))
        except GenerationPersistenceUnavailable:
            pass  # still IN_PROGRESS: retirement terminalizes it; output is withheld either way
        raise
    except GenerationPersistenceUnavailable:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def record_failure(operation_id, error):
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id).populate_existing().with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_IN_PROGRESS:
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
    """Turn this owner's staged candidate into their plan, or refuse.

    TWO locks, in this order, and the order is the repository-wide one
    (``app/services/plan_owner_lock``): the idempotency-operation row first, so
    this command's own replay/attempt semantics are decided exactly as they
    always were, then the OWNER row.

    The owner lock is not redundant with the operation lock. The operation row
    serializes this command against another native attempt; it says nothing
    about the browser's ``POST /training-plan/save``, which creates a plan
    through ``plan_replacement`` and locks the owner row. Without this line the
    two writers lock different objects, so a browser create and a native create
    could both observe an absent plan and both insert, and the owner would hold
    two plans.

    It must be taken BEFORE the ``get_active_plan`` check below, not merely
    before the insert: PostgreSQL already blocks the insert on its own (the
    ``training_plan.user_id`` foreign key takes a ``FOR KEY SHARE`` lock on the
    owner row, which conflicts with the browser's ``FOR UPDATE``), but blocking
    the insert after the decision has been made only delays a write already
    committed to. The check has to be the thing that waits.

    D1 (LP21 operation fencing): under that SAME owner lock, the staged
    candidate's own operation authority is fenced before anything is written.
    ``GENERATED`` is not permission — recovery after process loss commits only
    at the original generation. A definitive refusal terminalizes the operation
    (FAILED, candidate discarded); an authority outage keeps it recoverable.
    """
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id, user_id=user_id)
                     .populate_existing().with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_GENERATED:
            raise GenerationPersistenceUnavailable()
        lock_plan_owner(user_id)
        authority, document_json = ai_consent.unbind_candidate(
            user_id, operation.candidate_plan_data)
        ai_consent.fence(authority)
        if get_active_plan(user_id) is not None:
            raise ExistingPlanRefused()
        plan = _insert_plan(
            user_id=user_id,
            plan_data=document_json,
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
    except ai_consent.ConsentDenied as denied:
        db.session.rollback()
        if denied.status < 500:
            _terminalize_generated(operation_id, user_id, denied)
        raise
    except ExistingPlanRefused:
        db.session.rollback()
        raise
    except GenerationPersistenceUnavailable:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


def _terminalize_generated(operation_id, user_id, denied):
    """GENERATED -> FAILED with the D1 code; the AI candidate is discarded.

    Same fields/refund rule as ``record_failure``. Attempt evidence is never
    touched, so metered settlement still reads the true attempt markers.
    Returns whether this call terminalized it.
    """
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(id=operation_id, user_id=user_id)
                     .populate_existing().with_for_update().one_or_none())
        if operation is None or operation.status != TRAINING_PLAN_GENERATION_GENERATED:
            db.session.rollback()
            return False
        if operation.quota_reserved:
            premium.refund_ai_quota_in_transaction(
                operation.user_id, "training", operation.quota_week)
        operation.status = TRAINING_PLAN_GENERATION_FAILED
        operation.candidate_plan_data = None
        operation.candidate_score = None
        operation.quota_reserved = False
        operation.error_code = str(denied.code)[:64]
        operation.error_http_status = denied.status
        operation.error_retryable = False
        operation.updated_at = operation.completed_at = datetime.utcnow()
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def refuse_stale_generated(user_id):
    """End this owner's GENERATED work that can never be committed.

    Called under the owner arbitration lock before the active-operation check,
    so withdrawn work does not strand the owner. Returns the refused operation
    id (the caller settles it) or ``None``. Still-authorized work and authority
    outages are left alone: ``commit_plan`` fences again at the write.
    """
    operation_id = None
    try:
        operation = (db.session.query(TrainingPlanGenerationOperation)
                     .filter_by(user_id=user_id, status=TRAINING_PLAN_GENERATION_GENERATED)
                     .populate_existing().with_for_update().one_or_none())
        if operation is None:
            db.session.rollback()
            return None
        operation_id = operation.id
        lock_plan_owner(user_id)
        authority, _document = ai_consent.unbind_candidate(
            user_id, operation.candidate_plan_data)
        ai_consent.fence(authority)
        db.session.rollback()
        return None
    except ai_consent.ConsentDenied as denied:
        db.session.rollback()
        if denied.status >= 500 or operation_id is None:
            return None
        return operation_id if _terminalize_generated(operation_id, user_id, denied) else None
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error


class GeneralKeyRaced(RuntimeError):
    """The general receipt INSERT lost to another row; re-read the key."""


def _generation_live(user_id):
    """An AI first-plan operation that may be running right now.

    Older IN_PROGRESS/GENERATED rows are abandoned work: ``retire_abandoned``
    would retire the first kind, and the AI route that could commit the second
    is closed by D1 on main. Neither may strand the owner without a plan.
    """
    active = find_active_for_owner(user_id)
    return (active is not None
            and active.updated_at > datetime.utcnow() - ABANDON_AFTER)


def commit_general_plan(user_id, key, fingerprint, document_json):
    """Persist one deterministic first plan and its receipt atomically, or refuse.

    The non-AI general plan's receipt is an ordinary row of this ledger, born
    SUCCEEDED in the SAME transaction as the plan. Its markers are the truth
    about that work: ``provider_dispatch_possible`` False (there is no
    provider), ``allowance_metered``/``provider_attempt_possible`` NULL
    (unmetered, untracked), no quota reserved. No AI generation can end
    SUCCEEDED with dispatch False — ``authorize_dispatch`` commits False ->
    True before any candidate exists — so the row cannot pass for AI work.
    It is never IN_PROGRESS/GENERATED, so it never takes the active-owner slot
    and never meets abandonment, allowance settlement or quota refunds.

    Lock order is ``commit_plan``'s: the receipt INSERT first (its
    ``uq_training_plan_generation_user_key`` arbitrates a same-key race), then
    the OWNER row, and the absence check under that lock, then the plan.
    Every refusal rolls the receipt back with everything else, so a refused
    request consumes no key.
    """
    if not isinstance(document_json, str) or len(document_json.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise GenerationPersistenceUnavailable()
    try:
        now = datetime.utcnow()
        operation = TrainingPlanGenerationOperation(
            user_id=user_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status=TRAINING_PLAN_GENERATION_SUCCEEDED,
            attempt_count=1,
            provider_dispatch_possible=False,
            quota_reserved=False,
            created_at=now,
            updated_at=now,
        )
        db.session.add(operation)
        db.session.flush()
        lock_plan_owner(user_id)
        if get_active_plan(user_id) is not None:
            raise ExistingPlanRefused()
        if _generation_live(user_id):
            raise GenerationInProgress()
        plan = _insert_plan(user_id=user_id, plan_data=document_json, score=None)
        db.session.flush()
        operation.training_plan_id = plan.id
        operation.plan_lineage_id = plan.lineage_id
        operation.completed_at = now
        db.session.commit()
        return plan
    except (ExistingPlanRefused, GenerationInProgress):
        db.session.rollback()
        raise
    except IntegrityError as error:
        db.session.rollback()
        raise GeneralKeyRaced() from error
    except Exception as error:
        db.session.rollback()
        raise GenerationPersistenceUnavailable() from error
