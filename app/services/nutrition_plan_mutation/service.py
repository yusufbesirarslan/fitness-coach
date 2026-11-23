"""C1 unit-of-work owner: durable preparation, confirmation, replay and undo.

No route/tool registration here; the C2 Coach bridge
(``coach_plan_tools.nutrition``) is the only caller. No public nutrient or arbitrary plan input. Callers
derive owner and both authorities server-side. One estimate per operation key;
ambiguous/crashed dispatch is never automatically retried. Cancel preparation
explicitly to release an interrupted claim. Failed estimation never writes a
plan, proposal or mutation journal (only bounded operation failure bookkeeping).

Lock order: proposal (resolutions only) -> owner -> operation -> canonical plan.
All full Nutrition replacements already share this owner lock. Prepared and
training confirmations share the EXISTING one-pending-per-owner table/index.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import re
import secrets

from app.extensions import db
from app.models import NutritionPlanEditOperation as Operation, NutritionPlanMutationRecord as Record, TrainingPlanConfirmationProposal as Proposal
from app.services import ai_consent, nutrition_plan_store as store
from app.services.plan_owner_lock import lock_plan_owner
from app.services.plan_mutation.context import validate_context
from app.services.plan_confirmation import has_pending, lock_proposal
from . import access, estimation
from .access import Authorities
from .document import Edit, EditError, finish_edit, parse_snapshot, target_edit

COMMAND = "nutrition_plan_edit"
SOURCE = "independent_whole_meal_estimate"
ESTIMATE_NOTICE = ("Recalculated nutrients are estimates. Estimates for unchanged foods "
                   "in the affected meal may also change.")


@dataclass(frozen=True)
class Result:
    status: str
    operation_id: str | None = None
    proposal_id: str | None = None
    mutation_id: str | None = None
    plan_data: str | None = None
    plan_version: int | None = None
    replayed: bool = False
    is_current: bool = False
    estimated: bool = False
    estimate_notice: str | None = None
    requires_confirmation: bool = False


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _entry(owner):
    access.gate()
    if type(owner) is not int or owner <= 0:
        raise EditError("invalid_owner")
    if db.session.new or db.session.dirty or db.session.deleted:
        raise EditError("transaction_busy")


def _plan(owner):
    row = store.newest_plan_query(owner).populate_existing().with_for_update().first()
    if row is None:
        raise EditError("plan_not_found")
    if ((row.lineage_id is not None and (not isinstance(row.lineage_id, str) or not row.lineage_id))
            or type(row.mutation_version) is not int or row.mutation_version < 0):
        raise EditError("plan_history_unavailable")
    return row


def _bound(plan, op):
    # New claims bind the canonical row as well as its exact state. A row
    # inserted by old code may lack lineage: allocate only in the claim and
    # adopt it atomically at confirmation, never during a failed estimate.
    payload = op.command_payload
    if "plan_id" in payload or "base_lineage_id" in payload:
        identity = (type(payload.get("plan_id")) is int
                    and "base_lineage_id" in payload
                    and (payload["base_lineage_id"] is None
                         or isinstance(payload["base_lineage_id"], str))
                    and plan.id == payload["plan_id"]
                    and plan.lineage_id == payload["base_lineage_id"])
    else:
        identity = plan.lineage_id == op.plan_lineage_id
    return (identity
            and plan.mutation_version == op.base_version and plan.plan_data == op.before_snapshot)


def _authorities(owner, op):
    return Authorities.from_context(owner, op.command_payload.get("authorities"))


def _fence(owner, op):
    authorities = _authorities(owner, op)
    access.fence(owner, authorities)
    return authorities


def _operation(owner, **filters):
    return Operation.query.filter_by(user_id=owner, **filters).populate_existing().first()


def _record(owner, key):
    return Record.query.filter_by(user_id=owner, idempotency_key=key).first()


def _result(op, replayed=False):
    return Result(op.status, operation_id=op.public_id, proposal_id=op.proposal_public_id,
                  plan_data=op.after_snapshot, plan_version=op.base_version,
                  replayed=replayed, estimated=op.status == "prepared",
                  estimate_notice=ESTIMATE_NOTICE if op.status == "prepared" else None,
                  requires_confirmation=op.status == "prepared")


def _record_result(record, plan, replayed, operation_id=None, proposal_id=None):
    current = (plan.lineage_id == record.plan_lineage_id
               and plan.mutation_version == record.after_version
               and plan.plan_data == record.after_snapshot)
    estimated = record.nutrient_source == SOURCE
    return Result("applied", operation_id, proposal_id, record.public_id,
                  record.after_snapshot, record.after_version, replayed, current,
                  estimated, ESTIMATE_NOTICE if estimated else None)


def _replay(owner, op):
    _fence(owner, op)
    if op.status == "failed":
        raise EditError(op.failure_code or "estimate_unavailable")
    if op.status == "applied":
        record = _record(owner, op.idempotency_key)
        if record is None:
            raise EditError("history_unavailable")
        return _record_result(record, _plan(owner), True, op.public_id, op.proposal_public_id)
    return _result(op, True)


def prepare_edit(owner, edit, context, *, authorities):
    """Read canonical state, claim key, estimate outside tx, persist one quote.

    The typed edit contains no plan/owner IDs and no nutrients. No-op and bad
    references avoid a provider call. Retries reuse the exact durable result.
    """
    _entry(owner)
    context = validate_context(context)
    if not isinstance(edit, Edit):
        raise EditError("invalid_edit")
    fingerprint = _digest({"edit": asdict(edit), "actor": context.actor, "reason": context.reason})
    try:
        lock_plan_owner(owner)
        op = _operation(owner, idempotency_key=context.idempotency_key)
        if op is not None:
            if op.command_fingerprint != fingerprint:
                raise EditError("idempotency_conflict")
            result = _replay(owner, op)
            db.session.rollback()
            return result
        access.fence(owner, authorities)
        if _record(owner, context.idempotency_key) is not None:
            raise EditError("idempotency_conflict")
        if has_pending(owner) or Operation.query.filter(
                Operation.user_id == owner, Operation.status.in_(("estimating", "prepared"))).first():
            raise EditError("pending_change_exists")
        plan = _plan(owner)
        target = target_edit(parse_snapshot(plan.plan_data), edit)
        op = Operation(user_id=owner, idempotency_key=context.idempotency_key,
            command_fingerprint=fingerprint,
            command_payload={"edit": asdict(edit), "authorities": authorities.to_context(),
                             "actor": context.actor, "reason": context.reason,
                             "plan_id": plan.id, "base_lineage_id": plan.lineage_id},
            plan_lineage_id=plan.lineage_id or secrets.token_urlsafe(32), base_version=plan.mutation_version,
            before_snapshot=plan.plan_data, after_snapshot=plan.plan_data if not target.changed else None,
            status="estimating" if target.changed else "no_op")
        db.session.add(op)
        db.session.flush()
        public_id = op.public_id
        result = _result(op)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    if not target.changed:
        return result
    try:
        # Every physical call still uses the existing D1/provider/allowance/
        # spend/concurrency guards. No open SQL transaction across provider I/O.
        if db.session().in_transaction():
            raise EditError("transaction_busy")
        with ai_consent.operation_scope(authorities.nutrition):
            nutrients = estimation.estimate_meal(target.items)
        prepared = finish_edit(target, nutrients)
        return _publish(owner, public_id, prepared)
    except Exception:
        db.session.rollback()
        _fail(owner, public_id)
        raise


def _fail(owner, public_id):
    # Failure bookkeeping cannot publish content or revive a cancelled claim.
    try:
        lock_plan_owner(owner)
        op = _operation(owner, public_id=public_id)
        if op is not None and op.status == "estimating":
            op.status, op.failure_code = "failed", "estimate_unavailable"
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise EditError("operation_unavailable") from None


def _publish(owner, public_id, prepared):
    try:
        lock_plan_owner(owner)
        op = _operation(owner, public_id=public_id)
        if op is None or op.status != "estimating":
            raise EditError("proposal_unavailable")
        _fence(owner, op)
        plan = _plan(owner)
        if not _bound(plan, op) or prepared.before.raw != op.before_snapshot:
            raise EditError("plan_stale")
        if has_pending(owner):
            raise EditError("pending_change_exists")
        proposal = Proposal(user_id=owner, plan_lineage_id=op.plan_lineage_id,
            base_mutation_version=op.base_version,
            base_snapshot_fingerprint=prepared.before.digest, command_type=COMMAND,
            command_payload={"operation_id": op.public_id},
            command_fingerprint=op.command_fingerprint,
            reason_codes=["whole_meal_reestimated", "unchanged_food_estimates_may_change"],
            summary=ESTIMATE_NOTICE, status="pending")
        db.session.add(proposal)
        db.session.flush()
        op.after_snapshot, op.status, op.proposal_public_id = prepared.after.raw, "prepared", proposal.public_id
        result = _result(op)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def cancel_preparation(owner, operation_id):
    """Release an interrupted estimator claim without ever retrying its AI call."""
    _entry(owner)
    try:
        lock_plan_owner(owner)
        op = _operation(owner, public_id=operation_id)
        if op is None:
            raise EditError("proposal_unavailable")
        # Cancellation discards private work; withdrawn AI authority must not
        # prevent the authenticated owner from releasing an interrupted claim.
        if op.status not in ("estimating", "cancelled") or op.proposal_public_id is not None:
            raise EditError("proposal_unavailable")
        op.status = "cancelled"
        result = _result(op)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


@dataclass(frozen=True)
class Quote:
    """Detached read of one owner-scoped quote (C2 display/dispatch only)."""
    proposal_id: str
    status: str
    edit: dict
    before: str
    after: str


def find_quote(owner, proposal_id=None):
    """The owner's PENDING quote, or the named quote in any state; else None.

    Read-only; the caller settles its read transaction. Owner-scoped by query
    and the proposal/operation pair must agree exactly, so a foreign, Training
    or tampered id is simply absent.
    """
    if type(owner) is not int or owner <= 0:
        return None
    filters = {"user_id": owner, "command_type": COMMAND}
    if proposal_id is None:
        filters["status"] = "pending"
    elif isinstance(proposal_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{20,64}", proposal_id):
        filters["public_id"] = proposal_id
    else:
        return None
    proposal = Proposal.query.filter_by(**filters).first()
    if proposal is None or not isinstance(proposal.command_payload, dict):
        return None
    op = _operation(owner, proposal_public_id=proposal.public_id)
    if (op is None or op.after_snapshot is None
            or proposal.command_payload.get("operation_id") != op.public_id
            or not isinstance(op.command_payload.get("edit"), dict)
            or (proposal_id is None and op.status != "prepared")):
        return None
    return Quote(proposal.public_id, proposal.status, dict(op.command_payload["edit"]),
                 op.before_snapshot, op.after_snapshot)


def release_unavailable(owner, proposal_id):
    """Owner-only cancel of a quote stranded while the boundary is CLOSED.

    Rollback hazard: closing the source gate would otherwise leave a pending
    quote holding the shared one-pending slot (blocking Training proposals)
    with no way to answer it. Like ``cancel_preparation``, releasing private
    unapplied work needs no AI authority; this never writes a plan or journal
    and refuses while the boundary is open (``resolve_edit`` owns that case).
    """
    if type(owner) is not int or owner <= 0:
        raise EditError("invalid_owner")
    if access.NUTRITION_EDITS_ENABLED and access.APPROVED_COACH_EDIT_DISCLOSURES:
        raise EditError("proposal_unavailable")
    if db.session.new or db.session.dirty or db.session.deleted:
        raise EditError("transaction_busy")
    try:
        proposal = lock_proposal(owner, proposal_id)
        if proposal is None or proposal.command_type != COMMAND:
            raise EditError("proposal_unavailable")
        lock_plan_owner(owner)
        op = _operation(owner, public_id=proposal.command_payload.get("operation_id"))
        if op is None or op.proposal_public_id != proposal.public_id:
            raise EditError("proposal_unavailable")
        if op.status == "cancelled" and proposal.status == "cancelled":
            result = _result(op, True)
            db.session.rollback()
            return result
        if op.status != "prepared" or proposal.status != "pending":
            raise EditError("proposal_unavailable")
        op.status, proposal.status, proposal.resolved_at = "cancelled", "cancelled", datetime.utcnow()
        result = _result(op)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def interrupted_preparations(owner, started_before):
    """Public ids of this owner's estimator claims started before a bound.

    Estimation runs inside one bounded request; a claim older than that bound
    is an interrupted request, released only via ``cancel_preparation``.
    """
    if type(owner) is not int or owner <= 0:
        return []
    return [op.public_id for op in Operation.query.filter(
        Operation.user_id == owner, Operation.status == "estimating",
        Operation.created_at < started_before)]


def resolve_edit(owner, proposal_id, decision):
    """Named Confirm/Cancel, no model/Coach turn/allowance operation here."""
    _entry(owner)
    if decision not in ("confirm", "cancel") or not isinstance(proposal_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", proposal_id):
        raise EditError("invalid_decision")
    operation_id = None
    try:
        proposal = lock_proposal(owner, proposal_id)
        if proposal is None or proposal.command_type != COMMAND:
            raise EditError("proposal_unavailable")
        op = _operation(owner, public_id=proposal.command_payload.get("operation_id"))
        if op is None or op.proposal_public_id != proposal.public_id:
            raise EditError("proposal_unavailable")
        operation_id = op.public_id
        _fence(owner, op)  # locks owner after proposal, before the plan
        if op.status == "applied":
            result = _record_result(_record(owner, op.idempotency_key), _plan(owner), True, op.public_id, proposal.public_id)
            if not result.is_current:
                raise EditError("plan_stale")
            db.session.rollback()
            return result
        if decision == "cancel" and op.status == "cancelled":
            result = _result(op, True)
            db.session.rollback()
            return result
        if op.status == "stale" or proposal.status == "stale":
            raise EditError("plan_stale")
        if op.status != "prepared" or proposal.status != "pending":
            raise EditError("proposal_unavailable")
        if decision == "cancel":
            op.status, proposal.status, proposal.resolved_at = "cancelled", "cancelled", datetime.utcnow()
            result = _result(op)
            db.session.commit()
            return result
        plan = _plan(owner)
        if not _bound(plan, op):
            op.status, proposal.status, proposal.resolved_at = "stale", "stale", datetime.utcnow()
            db.session.commit()
            raise EditError("plan_stale")
        # Reconstruct from immutable typed intent + stored complete quote;
        # no client/model totals or recomputation provider call at confirmation.
        record = Record(user_id=owner, plan_lineage_id=op.plan_lineage_id,
            idempotency_key=op.idempotency_key, command_fingerprint=op.command_fingerprint,
            operation_kind="mutation", before_version=op.base_version,
            after_version=op.base_version + 1, before_snapshot=op.before_snapshot,
            after_snapshot=op.after_snapshot, nutrient_source=SOURCE)
        db.session.add(record)
        store.write_targeted_locked(plan, lineage_id=op.plan_lineage_id,
            version=op.base_version, before=op.before_snapshot, after=op.after_snapshot,
            before_lineage_id=op.command_payload.get("base_lineage_id", op.plan_lineage_id))
        op.status, proposal.status, proposal.resolved_at = "applied", "applied", datetime.utcnow()
        db.session.flush()
        result = _record_result(record, plan, False, op.public_id, proposal.public_id)
        db.session.commit()
        return result
    except ai_consent.ConsentDenied as denied:
        db.session.rollback()
        if denied.status < 500 and operation_id is not None:
            _retire_denied(owner, operation_id, proposal_id)
        raise
    except Exception:
        db.session.rollback()
        raise


def _retire_denied(owner, operation_id, proposal_id):
    """Retire unusable original authority; never publish or mutate plan data."""
    if ai_consent.current_subject() not in (None, owner):
        return
    try:
        proposal = lock_proposal(owner, proposal_id)
        lock_plan_owner(owner)
        op = _operation(owner, public_id=operation_id)
        if (proposal is not None and proposal.command_type == COMMAND
                and op is not None and op.proposal_public_id == proposal.public_id
                and op.status == "prepared" and proposal.status == "pending"):
            op.status, proposal.status, proposal.resolved_at = "stale", "stale", datetime.utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def undo_last_change(owner, context, *, authorities):
    """Append a forward version restoring exact bytes; never call an estimator."""
    _entry(owner)
    context = validate_context(context)
    fingerprint = _digest({"command": "undo_nutrition_plan_change"})
    try:
        lock_plan_owner(owner)
        access.fence(owner, authorities)
        prior = _record(owner, context.idempotency_key)
        plan = _plan(owner)
        if prior is not None:
            if prior.command_fingerprint != fingerprint:
                raise EditError("idempotency_conflict")
            result = _record_result(prior, plan, True)
            db.session.rollback()
            return result
        if _operation(owner, idempotency_key=context.idempotency_key) is not None:
            raise EditError("idempotency_conflict")
        reverted = db.session.query(Record.reverts_mutation_id).filter(
            Record.user_id == owner, Record.reverts_mutation_id.isnot(None))
        target = Record.query.filter(Record.user_id == owner,
            Record.plan_lineage_id == plan.lineage_id, Record.operation_kind == "mutation",
            Record.id.notin_(reverted)).order_by(Record.id.desc()).first()
        if target is None:
            raise EditError("undo_unavailable")
        if plan.plan_data != target.after_snapshot or plan.mutation_version < target.after_version:
            raise EditError("plan_stale")
        before, version = plan.plan_data, plan.mutation_version
        record = Record(user_id=owner, plan_lineage_id=plan.lineage_id,
            idempotency_key=context.idempotency_key, command_fingerprint=fingerprint,
            operation_kind="undo", before_version=version, after_version=version + 1,
            before_snapshot=before, after_snapshot=target.before_snapshot,
            reverts_mutation_id=target.id)
        db.session.add(record)
        store.write_targeted_locked(plan, lineage_id=plan.lineage_id,
            version=version, before=before, after=target.before_snapshot)
        db.session.flush()
        result = _record_result(record, plan, False)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise
