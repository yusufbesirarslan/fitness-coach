"""Owner-scoped persistence for one pending plan confirmation per user.

Every query includes ``user_id``. There is no code path that can load, confirm,
cancel or even detect another user's proposal by id — the model never supplies
one, and the store never accepts one.
"""
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    PLAN_CONFIRMATION_APPLIED,
    PLAN_CONFIRMATION_CANCELLED,
    PLAN_CONFIRMATION_PENDING,
    PLAN_CONFIRMATION_STALE,
    PLAN_CONFIRMATION_SUPERSEDED,
    TrainingPlanConfirmationProposal,
)


STATUS_PENDING = PLAN_CONFIRMATION_PENDING
STATUS_APPLIED = PLAN_CONFIRMATION_APPLIED
STATUS_CANCELLED = PLAN_CONFIRMATION_CANCELLED
STATUS_STALE = PLAN_CONFIRMATION_STALE
STATUS_SUPERSEDED = PLAN_CONFIRMATION_SUPERSEDED

_SUMMARY_MAX = 240


class PendingConfirmationConflict(Exception):
    """A different pending proposal already exists for this user."""


def get_pending(user_id):
    """The authenticated user's one PENDING proposal, or ``None``.

    Structural scoping: the query is owner-filtered. A missing user or a
    different user's pending row is indistinguishable — both are ``None``.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    return (
        TrainingPlanConfirmationProposal.query
        .filter_by(user_id=user_id, status=STATUS_PENDING)
        .one_or_none()
    )


def lock_proposal(user_id, public_id):
    """Owner-scoped ``SELECT … FOR UPDATE`` of one proposal by public id.

    Confirm and cancel both serialize here so a successful cancellation of a
    still-pending row cannot be followed by a new plan mutation of that same
    proposal. ``populate_existing`` re-reads columns under the lock — the
    identity-map footgun documented on ``PlanMutationService._locked_active_plan``.

    Lock order when a confirmed write also needs the plan row: proposal, then
    plan. Nothing in this package locks a plan row.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    if not isinstance(public_id, str) or not public_id:
        return None
    return (
        TrainingPlanConfirmationProposal.query
        .filter_by(user_id=user_id, public_id=public_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )


def lock_latest_proposal(user_id):
    """Lock the owner's latest proposal, including a resolved one.

    Cancel has no model/user-supplied proposal identity. Selecting the latest
    durable owner-scoped row lets a cancel that starts after confirm completed
    observe APPLIED and replay that exact proposal instead of claiming the plan
    stayed unchanged.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    return (
        TrainingPlanConfirmationProposal.query
        .filter_by(user_id=user_id)
        .order_by(TrainingPlanConfirmationProposal.id.desc())
        .populate_existing()
        .with_for_update()
        .first()
    )


def create_or_reuse_pending(
        user_id, *, lineage_id, mutation_version, snapshot_fingerprint,
        command_type, command_payload, command_fingerprint, reason_codes,
        summary):
    """Insert a pending proposal, or reuse the identical still-valid one.

    A *different* pending proposal is a conflict, not a silent stack and not
    an automatic supersede — the user must cancel first. Same fingerprint
    bound to the same plan state is the same request and is reused.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("user_id is required")

    existing = get_pending(user_id)
    if existing is not None:
        if _same_intent(existing, lineage_id, mutation_version,
                        snapshot_fingerprint, command_fingerprint):
            return existing, True
        raise PendingConfirmationConflict("a pending plan confirmation exists")

    row = TrainingPlanConfirmationProposal(
        user_id=user_id,
        plan_lineage_id=lineage_id,
        base_mutation_version=mutation_version,
        base_snapshot_fingerprint=snapshot_fingerprint,
        command_type=command_type,
        command_payload=dict(command_payload),
        command_fingerprint=command_fingerprint,
        reason_codes=list(reason_codes),
        summary=_clip_summary(summary),
        status=STATUS_PENDING,
    )
    db.session.add(row)
    try:
        db.session.commit()
        _settle()
    except IntegrityError:
        db.session.rollback()
        raced = get_pending(user_id)
        if raced is not None and _same_intent(
                raced, lineage_id, mutation_version, snapshot_fingerprint,
                command_fingerprint):
            return raced, True
        if raced is not None:
            raise PendingConfirmationConflict(
                "a pending plan confirmation exists") from None
        raise
    return row, False


def cancel_pending(user_id):
    """Cancel the user's pending proposal. Idempotent. Never mutates the plan.

    Takes the proposal row lock before the status transition so a confirmer
    that already observed the same pending row cannot apply after this
    cancellation commits.
    """
    pending = get_pending(user_id)
    if pending is None:
        return None
    row = lock_proposal(user_id, pending.public_id)
    if row is None or row.status != STATUS_PENDING:
        if row is not None:
            db.session.rollback()
        return None
    row.status = STATUS_CANCELLED
    row.resolved_at = datetime.utcnow()
    db.session.commit()
    _settle()
    return row


def mark_applied(user_id, public_id):
    """Bookkeeping after PlanMutationService has already committed.

    Conditional on PENDING so a concurrent confirmer that lost the mutation
    race (and replayed) converges without rewriting an already-applied row.
    Returns the row when this caller performed the transition, else ``None``.
    """
    if not public_id:
        return None
    updated = (
        TrainingPlanConfirmationProposal.query
        .filter_by(user_id=user_id, public_id=public_id, status=STATUS_PENDING)
        .update(
            {"status": STATUS_APPLIED, "resolved_at": datetime.utcnow()},
            synchronize_session=False,
        )
    )
    db.session.commit()
    _settle()
    if not updated:
        return (
            TrainingPlanConfirmationProposal.query
            .filter_by(user_id=user_id, public_id=public_id)
            .one_or_none()
        )
    return (
        TrainingPlanConfirmationProposal.query
        .filter_by(user_id=user_id, public_id=public_id)
        .one_or_none()
    )


def mark_stale(user_id, public_id):
    """The stored proposal no longer matches canonical plan state."""
    pending = get_pending(user_id)
    if pending is None or pending.public_id != public_id:
        return None
    pending.status = STATUS_STALE
    pending.resolved_at = datetime.utcnow()
    db.session.commit()
    _settle()
    return pending


def _same_intent(row, lineage_id, mutation_version, snapshot_fingerprint,
                 command_fingerprint):
    return (
        row.plan_lineage_id == lineage_id
        and row.base_mutation_version == mutation_version
        and row.base_snapshot_fingerprint == snapshot_fingerprint
        and row.command_fingerprint == command_fingerprint
    )


def _settle():
    """Leave no leftover read transaction after a committed proposal write."""
    try:
        session = db.session()
        if not session.in_transaction():
            return
        if session.new or session.dirty or session.deleted:
            return
        session.rollback()
    except Exception:
        current_app.logger.debug(
            "Plan confirmation transaction cleanup failed", exc_info=True)


def _clip_summary(summary):
    text = (summary or "").strip()
    return text[:_SUMMARY_MAX]
