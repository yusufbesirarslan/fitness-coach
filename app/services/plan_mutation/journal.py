"""Durable mutation history: the queries and the record shape.

Owner-scoped by construction. Every function here takes ``user_id`` and puts it
in the WHERE clause; there is no code path that can read or select another user's
history, which makes cross-user replay and cross-user undo structurally
impossible rather than merely checked.

The journal is **evidence, never authority**. Nothing here decides whether a
caller may do something — a matching ``user_id`` on a record is a fact about the
past, not a permission. Authorization stays where PR1 put it: the authenticated
server context plus the owner-scoped ``TrainingPlan`` query.

This module holds no transaction. It reads, and it builds a row; the service owns
begin/commit/rollback so that plan, version and journal move as one unit.
"""
from app.extensions import db
from app.models import PlanMutationRecord

from .fingerprint import snapshot_fingerprint


#: An original typed command, versus its compensating reversal.
KIND_MUTATION = "mutation"
KIND_UNDO = "undo"

#: A real persisted state transition, versus an accepted request that found the
#: desired state already true. Only ``OUTCOME_APPLIED`` is reversible.
OUTCOME_APPLIED = "applied"
OUTCOME_NO_OP = "no_op"


def find_by_key(user_id, idempotency_key):
    """The user's record for this operation key, or ``None``."""
    return PlanMutationRecord.query.filter_by(
        user_id=user_id, idempotency_key=idempotency_key).first()


def latest_reversible(user_id, lineage_id):
    """The newest still-effective reversible mutation of one plan lineage.

    Four filters, each carrying an invariant from the brief:

    * ``plan_lineage_id`` — history from a replaced plan is never a candidate, so
      an undo cannot restore an old plan's snapshot into a regenerated one just
      because the day and exercise names happen to match (§11).
    * ``operation_kind == mutation`` — an undo is a compensating event, not
      something to "undo the undo" of. Redo is out of scope (§26).
    * ``outcome == applied`` — an accepted no-op produced no state transition and
      so has nothing to reverse (§26).
    * not already reverted — the row a previous undo points at is spent. Combined
      with the unique constraint on ``reverts_mutation_id``, this is what lets a
      second undo reach further back instead of re-reversing the same mutation.

    Ordering is by primary key, not ``created_at``: two records inside one
    millisecond must still have one deterministic "latest".
    """
    reverted = db.session.query(PlanMutationRecord.reverts_mutation_id).filter(
        PlanMutationRecord.user_id == user_id,
        PlanMutationRecord.reverts_mutation_id.isnot(None),
    )
    return (
        PlanMutationRecord.query
        .filter(
            PlanMutationRecord.user_id == user_id,
            PlanMutationRecord.plan_lineage_id == lineage_id,
            PlanMutationRecord.operation_kind == KIND_MUTATION,
            PlanMutationRecord.outcome == OUTCOME_APPLIED,
            PlanMutationRecord.id.notin_(reverted),
        )
        .order_by(PlanMutationRecord.id.desc())
        .first()
    )


def build_record(*, user_id, lineage_id, context, operation_kind, command_type,
                 command_fingerprint, outcome, before_version, after_version,
                 before_snapshot, after_snapshot, reverts_mutation_id=None):
    """Construct one journal row.

    Snapshots are the EXACT persisted ``plan_data`` text on both sides, not a
    projection of the fields this domain happens to model. That is the whole
    point: PR1 preserves unknown plan fields through a mutation, and rebuilding a
    snapshot from ``plan_facts``, a DTO or an exercise list would quietly discard
    them, so an undo would "restore" a lossy plan. An accepted no-op stores the
    same text on both sides — honest, and it lets a later replay return the
    result the caller originally got.

    ``created_at`` is left to the column default rather than passed in, so the
    journal cannot be back-dated by a caller.
    """
    return PlanMutationRecord(
        user_id=user_id,
        plan_lineage_id=lineage_id,
        operation_kind=operation_kind,
        command_type=command_type,
        command_fingerprint=command_fingerprint,
        actor=context.actor,
        reason=context.reason,
        outcome=outcome,
        before_version=before_version,
        after_version=after_version,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        before_fingerprint=snapshot_fingerprint(before_snapshot),
        after_fingerprint=snapshot_fingerprint(after_snapshot),
        idempotency_key=context.idempotency_key,
        reverts_mutation_id=reverts_mutation_id,
    )
