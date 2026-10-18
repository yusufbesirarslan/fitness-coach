"""Narrow original-expiry correction, never a provider or entitlement authority.

Lock order: owner -> operation NOWAIT -> binding -> reservation -> bucket.
NOWAIT removes the reverse blocking edge against dispatch's operation -> owner.
Every caller owns a short transaction. No locks survive provider I/O.
"""
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import (AiAllowanceBucket, AiStarterGrant, AiUsageReservation,
                        TrainingAllowanceCompensation, TrainingPlanGenerationAllowance,
                        TrainingPlanGenerationOperation)
from . import service as ledger

APPLIED = "COMPENSATION_APPLIED"
ALREADY_APPLIED = "COMPENSATION_ALREADY_APPLIED"
PROOF = "TRACKED_TRAINING_V1"
REASON = "EXPIRED_ZERO_ATTEMPTS"


def enabled():
    return (current_app.config.get("AI_ALLOWANCE_LEDGER_ENABLED") is True
            and current_app.config.get("AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED") is True)


def _locked(model, *conditions, nowait=False):
    return db.session.execute(sa.select(model).where(*conditions).with_for_update(nowait=nowait)
        .execution_options(populate_existing=True)).scalar_one_or_none()


def correct_within(user_id, operation_id, now):
    """Owner already locked; return a privacy-safe disposition, never commit.

    Complete retained authorities are required even for an applied replay.
    The immutable terminal operation is locked NOWAIT: no operation->owner
    blocking cycle with the dispatch or retirement paths is introduced.
    """
    op = _locked(TrainingPlanGenerationOperation,
                 TrainingPlanGenerationOperation.id == operation_id,
                 TrainingPlanGenerationOperation.user_id == user_id, nowait=True)
    if op is None:
        return "COMPENSATION_OPERATION_UNAVAILABLE"
    if (op.status != "FAILED" or op.allowance_metered is not True
            or op.provider_dispatch_possible is not True
            or op.provider_attempt_possible is not False):
        return "COMPENSATION_UNPROVEN"
    if (op.completed_at is None or not op.error_code or op.quota_reserved
            or op.candidate_plan_data is not None or op.candidate_score is not None
            or op.training_plan_id is not None or op.plan_lineage_id is not None):
        return "COMPENSATION_CONTRADICTORY_OPERATION"
    binding = _locked(TrainingPlanGenerationAllowance,
                      TrainingPlanGenerationAllowance.operation_id == op.id)
    if (binding is None or binding.user_id != user_id
            or binding.state not in ("RESERVED", "EXPIRED")):
        return "COMPENSATION_BINDING_UNAVAILABLE"
    hold = _locked(AiUsageReservation, AiUsageReservation.public_id == binding.reservation_ref)
    if hold is None:
        return "COMPENSATION_EVIDENCE_UNAVAILABLE"
    if (hold.user_id != user_id or hold.feature != "training_generation"
            or hold.state != "EXPIRED" or hold.outcome != "expired"
            or hold.billing_evidence != "UNKNOWN" or hold.provider_billed is not None
            or hold.finalized_at is None
            or ledger._utc(hold.finalized_at) < ledger._utc(hold.expires_at)
            or ledger._utc(hold.finalized_at) > now or ledger._utc(op.completed_at) > now
            or ledger._utc(hold.expires_at) - ledger._utc(hold.created_at) != timedelta(minutes=15)
            or binding.created_at != ledger._utc(hold.created_at).replace(tzinfo=None)
            or op.created_at > binding.created_at or op.completed_at < binding.created_at):
        return "COMPENSATION_CONTRADICTORY_RESERVATION"
    bucket = _locked(AiAllowanceBucket, AiAllowanceBucket.id == hold.bucket_id)
    if (bucket is None or bucket.user_id != user_id or bucket.feature != hold.feature
            or not bucket.policy_version
            or (bucket.scope == "STARTER" and bucket.period_key != "NEVER")
            or bucket.scope not in ("STARTER", "PREMIUM")
            or ledger._utc(bucket.created_at) > ledger._utc(hold.created_at)
            or (bucket.scope == "PREMIUM" and not _premium_period(
                bucket.period_key, ledger._utc(hold.created_at)))):
        return "COMPENSATION_BUCKET_UNAVAILABLE"
    if bucket.scope == "STARTER":
        grant = db.session.get(AiStarterGrant, user_id, populate_existing=True)
        if (grant is None or grant.policy_version != bucket.policy_version
                or ledger._utc(grant.granted_at) > ledger._utc(hold.created_at)):
            return "COMPENSATION_STARTER_PROVENANCE_UNAVAILABLE"
    receipt = db.session.get(TrainingAllowanceCompensation, hold.id, populate_existing=True)
    if receipt is not None:
        if (receipt.operation_id, receipt.user_id, receipt.bucket_id, receipt.proof_version,
                receipt.reason) != (op.id, user_id, bucket.id, PROOF, REASON):
            return "COMPENSATION_CONTRADICTORY_RECEIPT"
        return ALREADY_APPLIED
    db.session.add(TrainingAllowanceCompensation(
        reservation_id=hold.id, operation_id=op.id, user_id=user_id,
        bucket_id=hold.bucket_id, proof_version=PROOF, reason=REASON, applied_at=now))
    db.session.flush()  # DB uniqueness is the second exactly-once guard.
    changed = db.session.execute(sa.update(AiAllowanceBucket).where(
        AiAllowanceBucket.id == hold.bucket_id, AiAllowanceBucket.user_id == user_id,
        AiAllowanceBucket.feature == "training_generation", AiAllowanceBucket.used >= 1)
        .values(used=AiAllowanceBucket.used - 1, updated_at=now)
        .execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        # Caller must roll back the receipt as well as the refused movement.
        raise ledger.AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    return APPLIED


def _premium_period(key, reserved_at):
    from datetime import date
    try:
        if len(key) == 8 and key[4:6] == "-W":
            date.fromisocalendar(int(key[:4]), int(key[6:]), 1)
            return ledger.P.period_key(ledger.P.PERIOD_WEEKLY, reserved_at) == key
        if len(key) == 7 and key[4] == "-":
            date(int(key[:4]), int(key[5:]), 1)
            return ledger.P.period_key(ledger.P.PERIOD_MONTHLY, reserved_at) == key
    except (ValueError, TypeError):
        pass
    return False


def compensate(user_id, operation_id, *, now=None):
    """Internal source primitive: no endpoint, no SDK, no new spend authority."""
    if not enabled():
        return "COMPENSATION_DISABLED"  # zero SQL in either OFF mode
    try:
        if db.engine.dialect.name == "postgresql":
            db.session.execute(sa.text("SET LOCAL lock_timeout = '2000ms'"))
        ledger._lock_owner(user_id)
        result = correct_within(user_id, operation_id, ledger._now(now))
        db.session.commit()
        return result
    except ledger.AllowanceError as error:
        db.session.rollback()
        return ("COMPENSATION_ACCOUNT_UNAVAILABLE" if error.code == "AI_ALLOWANCE_ACCOUNT_UNAVAILABLE"
                else "COMPENSATION_DEFERRED")
    except SQLAlchemyError:
        db.session.rollback()
        return "COMPENSATION_DEFERRED"
