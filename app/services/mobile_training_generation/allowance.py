"""MON-02-BE-03B-01B: native initial Training generation <-> AI allowance ledger.

The only Training module that imports the ledger. The generation operation stays
the authority for owner, key, fingerprint, dispatch marker, candidate and plan;
the ledger stays the authority for tier, quota, hold and settlement. This module
binds exactly one hold to one metered operation:

* ``store.claim`` fixes the metering mode on the operation row
  (``allowance_metered``) and, when metered, skips the legacy weekly quota;
* ``bind_for_dispatch`` runs INSIDE ``store.authorize_dispatch``'s transaction
  after the operation row lock, so the hold, its binding row and the dispatch
  marker commit together or not at all (lock order: operation -> ledger owner
  -> reservations -> bucket; the binding is a new row);
* ``settle`` runs only after the operation's terminal commit, in its own short
  transactions, and is idempotent: the ledger replays a repeated settlement.

Every dispatch-bound hold is UNKNOWN billing evidence: the adapter supplies no
charge receipt. Nothing here logs identifiers, keys, prompts or plan content.
"""
from __future__ import annotations

import secrets
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.models import (
    TRAINING_ALLOWANCE_RESERVED,
    TRAINING_PLAN_GENERATION_FAILED,
    TRAINING_PLAN_GENERATION_SUCCEEDED,
    TrainingPlanGenerationAllowance,
    TrainingPlanGenerationOperation,
)
from app.services import ai_allowances
from app.services.training_generation.preference_contract import (
    CODE_GENERATION_INVALID,
    CODE_GENERATION_PARSE_FAILED,
    CODE_GENERATION_TRUNCATED,
)

from .errors import (
    INVALID_CANDIDATE_CODES,
    TrainingAllowanceRefused,
)

FEATURE = "training_generation"
UNOBSERVABLE = "UNOBSERVABLE"
OUTCOME_UNKNOWN_CODE = "TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN"
_TERMINAL = (TRAINING_PLAN_GENERATION_SUCCEEDED, TRAINING_PLAN_GENERATION_FAILED)
# The provider answered but the answer was unusable (after the bounded repair).
_INVALID_OUTPUT_CODES = INVALID_CANDIDATE_CODES | {
    CODE_GENERATION_INVALID, CODE_GENERATION_PARSE_FAILED, CODE_GENERATION_TRUNCATED}
_SETTLE_PER_COMMAND = 3


def enforcement_enabled():
    return current_app.config.get("AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED") is True


def _ledger_enabled():
    return current_app.config.get("AI_ALLOWANCE_LEDGER_ENABLED") is True


def _refused(code):
    status, retryable = ai_allowances.CATALOG[code]
    return TrainingAllowanceRefused(code, status, retryable)


def _binding(operation_id, *, lock=False):
    query = (db.session.query(TrainingPlanGenerationAllowance)
             .filter_by(operation_id=operation_id).populate_existing())
    return (query.with_for_update() if lock else query).one_or_none()


def bind_for_dispatch(operation, now):
    """Create and bind the hold inside the dispatch-authorization transaction.

    The caller holds the operation row lock and has proven the marker is False,
    so no binding can exist yet. The metering mode was fixed at claim; if the
    flag has moved since, refuse (retryable) rather than dispatch under a mode
    the claim did not take. OFF + unmetered reads nothing and writes nothing.
    """
    metered = operation.allowance_metered is True
    if metered != enforcement_enabled():
        raise _refused("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    if not metered:
        return
    try:
        hold = ai_allowances.reserve_within(
            operation.user_id, FEATURE, "training-dispatch:" + secrets.token_hex(16), now=now)
    except ai_allowances.AllowanceError as error:
        raise _refused(error.code) from None
    if not hold.may_execute:
        raise _refused("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    from . import store  # store imports this module
    if hold.expires_at > now + store.ABANDON_AFTER:
        # Retirement (marker instant + ABANDON_AFTER) must find this hold
        # already expired, so a retired operation consumes exactly one unit.
        # ABANDON_AFTER < RESERVATION_TTL, or a hold stamped after the marker,
        # would let retirement release it instead: refuse before any dispatch.
        raise _refused("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    stamp = now.replace(tzinfo=None)
    db.session.add(TrainingPlanGenerationAllowance(
        operation_id=operation.id, user_id=operation.user_id,
        reservation_ref=hold.reference, state=TRAINING_ALLOWANCE_RESERVED,
        created_at=stamp, updated_at=stamp))


def _outcome(operation):
    if operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED:
        return ai_allowances.OUTCOME_SUCCESS
    if operation.error_code in _INVALID_OUTPUT_CODES:
        return "invalid_output"
    if operation.error_code == OUTCOME_UNKNOWN_CODE:
        return "provider_timeout"
    return "provider_error"


def _deferred(code):
    current_app.logger.warning("training_allowance event=settlement_deferred code=%s", code)


def settle(operation_id):
    """Settle one terminal metered operation once; never raises.

    Returns the observed binding state, or ``None`` when nothing was (or could
    be) settled now. The ledger finalize commits first; the binding then records
    what the ledger reports. Losing either step is safe: a repeat replays the
    ledger's stored settlement for the same outcome and never moves a unit
    twice. A hold that already expired is reported as EXPIRED (consumed once),
    never as a fresh COMMITTED charge.
    """
    if not _ledger_enabled():
        return None
    try:
        row = (db.session.query(TrainingPlanGenerationOperation, TrainingPlanGenerationAllowance)
               .join(TrainingPlanGenerationAllowance,
                     TrainingPlanGenerationAllowance.operation_id == TrainingPlanGenerationOperation.id)
               .filter(TrainingPlanGenerationOperation.id == operation_id)
               .populate_existing().one_or_none())
        if (row is None or row[1].state != TRAINING_ALLOWANCE_RESERVED
                or row[0].status not in _TERMINAL or row[1].user_id != row[0].user_id):
            db.session.rollback()
            return None if row is None else row[1].state
        operation, binding = row
        user_id, reference, outcome = operation.user_id, binding.reservation_ref, _outcome(operation)
        db.session.rollback()  # no read transaction across the ledger owner lock
        try:
            observed = ai_allowances.finalize(
                user_id, reference, outcome,
                billing_evidence=ai_allowances.BILLING_UNKNOWN).state
        except ai_allowances.AllowanceError as error:
            if error.code != "AI_ALLOWANCE_RESERVATION_NOT_FOUND":
                _deferred(error.code)
                return None
            observed = UNOBSERVABLE  # the ledger no longer retains this hold
        binding = _binding(operation_id, lock=True)
        if binding is not None and binding.state == TRAINING_ALLOWANCE_RESERVED:
            binding.state = observed
            binding.updated_at = datetime.utcnow()
        db.session.commit()
        return observed
    except Exception as error:
        db.session.rollback()
        _deferred(type(error).__name__)
        return None


def settle_pending(user_id):
    """Bounded catch-up for this owner's terminal operations still holding."""
    if not _ledger_enabled():
        return
    try:
        pending = [operation_id for (operation_id,) in (
            db.session.query(TrainingPlanGenerationAllowance.operation_id)
            .join(TrainingPlanGenerationOperation,
                  TrainingPlanGenerationOperation.id == TrainingPlanGenerationAllowance.operation_id)
            .filter(TrainingPlanGenerationAllowance.user_id == user_id,
                    TrainingPlanGenerationAllowance.state == TRAINING_ALLOWANCE_RESERVED,
                    TrainingPlanGenerationOperation.status.in_(_TERMINAL))
            .order_by(TrainingPlanGenerationAllowance.operation_id)
            .limit(_SETTLE_PER_COMMAND).all())]
        db.session.rollback()
    except Exception as error:
        db.session.rollback()
        _deferred(type(error).__name__)
        return
    for operation_id in pending:
        settle(operation_id)
