"""Durable immutable proposal + terminal receipt authority.

Stage accepts an already validated server candidate. Confirm invokes no provider.
Each admitted confirmation key gets one terminal receipt, atomically with any
replacement. Refusal replay is stable; retrying an ACTIVE/Coach refusal after
resolution needs a fresh confirm key. Infrastructure failures roll back and
consume no key. The per-owner transition lock serializes even missing receipts.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (TrainingPlanReplacementProposal as Proposal,
                        TrainingPlanReplacementReceipt as Receipt,
                        TrainingPlanConfirmationProposal as CoachProposal,
                        WorkoutSession, WORKOUT_SESSION_ACTIVE)
from app.services.plan_mutation.fingerprint import snapshot_fingerprint
from app.services.plan_replacement import (PlanExpectation, PlanReplacementConflict,
                                          replace_training_plan_in_transaction)
from app.services.plan_session_transition import lock_plan_session_transition

PROPOSAL_TTL = timedelta(minutes=30)
REPLAY_HORIZON = timedelta(days=90)
_KEY = re.compile(r"[A-Za-z0-9._:-]{8,64}\Z")


@dataclass(frozen=True)
class BaseBinding:
    lineage_id: str
    mutation_version: int
    snapshot_digest: str


@dataclass(frozen=True)
class ConfirmationResult:
    receipt_id: str
    status: str
    lineage_id: str | None
    mutation_version: int | None
    replayed: bool = False


class ReplacementRefusal(Exception):
    """Bounded terminal refusal with its durable result for B1 mapping."""
    def __init__(self, result):
        super().__init__(result.status)
        self.result = result
        self.reason = result.status


class ConfirmationConflict(Exception):
    """Same owner/key, different frozen intent; zero write."""


class ProposalUnavailable(Exception):
    """Missing or wrong-owner proposal; zero write."""


def _digest(domain, material):
    return hashlib.sha256((domain + "\0" + material).encode("utf-8")).hexdigest()


def candidate_fingerprint(plan_data, score):
    return _digest("axisai/replacement-candidate/v1", json.dumps(
        [plan_data, float(score)], ensure_ascii=False, separators=(",", ":")))


def intent_fingerprint(proposal):
    return _digest("axisai/replacement-confirm/v1", json.dumps([
        proposal.public_id, proposal.base_lineage_id, proposal.base_mutation_version,
        proposal.base_snapshot_digest, proposal.candidate_fingerprint, True],
        separators=(",", ":")))


def stage_proposal(user_id, binding, *, plan_data, score):
    """Persist immutable server-owned review content, no canonical plan write.

    B1 owns validation/projectability and generation idempotency before calling.
    No generation ledger/HTTP semantics are invented in this foundation.
    """
    if type(user_id) is not int or not 0 < user_id < 2**31:
        raise ValueError("invalid proposal owner")
    if (not isinstance(binding, BaseBinding) or not binding.lineage_id
            or len(binding.lineage_id) > 64 or type(binding.mutation_version) is not int
            or binding.mutation_version < 0
            or not re.fullmatch(r"[0-9a-f]{64}", binding.snapshot_digest)):
        raise ValueError("invalid base binding")
    if (not isinstance(plan_data, str) or not plan_data
            or len(plan_data.encode("utf-8")) > 262144
            or isinstance(score, bool) or not isinstance(score, (float, int))
            or not math.isfinite(score) or not 1 <= score <= 10):
        raise ValueError("invalid staged candidate")
    now = datetime.utcnow()
    row = Proposal(user_id=user_id, base_lineage_id=binding.lineage_id,
                   base_mutation_version=binding.mutation_version,
                   base_snapshot_digest=binding.snapshot_digest,
                   candidate_plan_data=plan_data, candidate_score=float(score),
                   candidate_fingerprint=candidate_fingerprint(plan_data, score),
                   created_at=now, expires_at=now + PROPOSAL_TTL)
    try:
        db.session.add(row)
        db.session.flush()
        public_id = row.public_id
        db.session.commit()
        return public_id
    except Exception:
        db.session.rollback()
        raise


def _result(receipt, replayed=False):
    return ConfirmationResult(receipt.public_id, receipt.status,
                              receipt.result_lineage_id,
                              receipt.result_mutation_version, replayed)


def _deliver(result):
    if result.status != "APPLIED":
        raise ReplacementRefusal(result)
    return result


def _guard(user_id, coach_rows):
    # Column read ONLY: never lock session behind User/plan (completion inversion).
    if db.session.query(WorkoutSession.id).filter_by(
            user_id=user_id, status=WORKOUT_SESSION_ACTIVE).first() is not None:
        return "ACTIVE_REFUSED"
    if coach_rows:
        return "COACH_PENDING_REFUSED"
    return None


def confirm_replacement(user_id, proposal_public_id, idempotency_key):
    """One short commit-owning confirmation transaction; all intent is server-stored.

    Receipt lookup precedes proposal expiry/current-state checks. Successful
    replay works even after proposal/old plan deletion. Different proposal with
    same key conflicts even if it no longer exists. No raw exception logging.
    """
    if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
        raise ValueError("invalid confirmation key")
    if not isinstance(proposal_public_id, str) or not 1 <= len(proposal_public_id) <= 64:
        raise ProposalUnavailable("proposal unavailable")
    key_digest = _digest("axisai/replacement-confirm-key/v1", idempotency_key)
    try:
        lock_plan_session_transition(user_id)
        receipt = Receipt.query.filter_by(user_id=user_id, key_digest=key_digest).populate_existing().first()
        if receipt is not None:
            if receipt.proposal_public_id != proposal_public_id:
                raise ConfirmationConflict("confirmation intent conflict")
            # The immutable proposal can be deleted for privacy retention. If
            # present, detect corrupt/changed frozen intent as well.
            proposal = Proposal.query.filter_by(user_id=user_id, public_id=proposal_public_id).first()
            if proposal is not None and receipt.intent_fingerprint != intent_fingerprint(proposal):
                raise ConfirmationConflict("confirmation intent conflict")
            result = _result(receipt, True)
            db.session.rollback()
        else:
            proposal = (Proposal.query.filter_by(user_id=user_id, public_id=proposal_public_id)
                        .populate_existing().with_for_update().first())
            if proposal is None:
                raise ProposalUnavailable("proposal unavailable")
            fingerprint = intent_fingerprint(proposal)
            status = None
            applied = Receipt.query.filter_by(user_id=user_id, proposal_public_id=proposal_public_id,
                                             status="APPLIED").first()
            if applied is not None:
                status = "CONSUMED"
            elif proposal.expires_at <= datetime.utcnow():
                status = "EXPIRED"
            elif candidate_fingerprint(proposal.candidate_plan_data, proposal.candidate_score) != proposal.candidate_fingerprint:
                raise ProposalUnavailable("proposal unavailable")
            replacement = None
            if status is None:
                # Lock before User/plan. READ COMMITTED waits refresh a Coach
                # winner. New pending rows after this read may bind old plan;
                # canonical Coach mutation rechecks full binding under plan lock.
                coach_rows = (CoachProposal.query.filter_by(user_id=user_id, status="pending")
                              .order_by(CoachProposal.id).populate_existing().with_for_update().all())
                def final_guard():
                    refusal = _guard(user_id, coach_rows)
                    if refusal:
                        raise ReplacementRefusal(ConfirmationResult("", refusal, None, None))
                try:
                    replacement = replace_training_plan_in_transaction(
                        user_id, PlanExpectation(False, proposal.base_lineage_id,
                                                 proposal.base_mutation_version),
                        plan_data=proposal.candidate_plan_data, score=proposal.candidate_score,
                        snapshot_digest=proposal.base_snapshot_digest,
                        before_replace=final_guard)
                    status = "APPLIED"
                except PlanReplacementConflict:
                    status = "STALE"
                except ReplacementRefusal as refusal:
                    status = refusal.reason
            receipt = Receipt(user_id=user_id, key_digest=key_digest,
                              intent_fingerprint=fingerprint, proposal_public_id=proposal.public_id,
                              status=status,
                              result_lineage_id=replacement.lineage_id if replacement else None,
                              result_mutation_version=replacement.mutation_version if replacement else None)
            db.session.add(receipt)
            db.session.flush()
            result = _result(receipt)
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return _deliver(result)
