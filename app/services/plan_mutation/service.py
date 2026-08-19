"""The transaction + ownership owner for training-plan mutations.

This is the only place in the application allowed to write ``TrainingPlan``
targeted-mutation state. It is server-authoritative, owner-scoped, deterministic
and free of any AI/provider dependency; the pure targeted-mutation logic lives in
``document.py``, the bounds in ``validation.py``, the replay identity in
``fingerprint.py`` and the durable history in ``journal.py``.

PR1 shipped this boundary without exactly-once semantics: a caller that retried
after a lost response could apply the same change twice, and for ``add_exercise``
that meant a duplicated entry. Sprint 1 PR2 closes that with three things that
must move as ONE unit — the plan document, its version, and a journal row:

    validate command type
    → validate the mutation envelope (key / actor / reason)
    → durable replay check
    → resolve the caller's OWN canonical active plan (row lock)
    → re-check the operation key under the lock
    → capture the authoritative before state + version
    → apply the targeted mutation to a copy
    → derive the after state; version + 1 only if state actually changed
    → append the journal row, assign the plan, flush
    → ONE commit

Nothing here trusts process memory, a cache, or a clock to decide a race. The
unique constraint on ``(user_id, idempotency_key)`` is the final arbiter: two
concurrent requests carrying one key both reach the insert, exactly one wins, and
the loser's ``IntegrityError`` becomes a replay of the winner's durable result.

Versioning obeys one rule with no exceptions: ``mutation_version`` only ever
counts UP. An undo is a new forward version that happens to restore old bytes,
never a decrement — a version number that can go backwards is not a version.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import TrainingPlan
from app.services.today_facts import get_active_plan

from .commands import PLAN_MUTATION_COMMANDS
from .context import validate_context
from .document import (
    apply_command,
    parse_plan_document,
    serialize_plan_document,
)
from .errors import (
    IdempotencyConflict,
    InvalidMutation,
    PlanNotFound,
    PlanStateConflict,
    UndoConflict,
    UndoUnavailable,
)
from .fingerprint import (
    UNDO_COMMAND_TYPE,
    command_type,
    semantic_fingerprint,
    snapshot_fingerprint,
    undo_fingerprint,
)
from .journal import (
    KIND_MUTATION,
    KIND_UNDO,
    OUTCOME_APPLIED,
    OUTCOME_NO_OP,
    build_record,
    find_by_key,
    latest_reversible,
)


@dataclass(frozen=True)
class PlanMutationResult:
    """The outcome of one mutation or undo.

    ``outcome`` distinguishes a real persisted state transition from a
    deterministic no-op — "this was already true" is an accepted request, not a
    failure, and not a change. ``plan`` is the canonical resulting document, so
    the caller never has to re-read. ``plan_version`` is the durable version the
    plan carries after the call. ``mutation_id`` is an OPAQUE identity for the
    journal entry: a random token, never the row's primary key, so nothing about
    the size or ordering of the table leaks to a future API. ``replayed`` says
    this result was served from durable history rather than newly applied.

    ``changed`` is derived, not stored — one source of truth for "did state
    move", and it keeps every PR1 caller and test working unchanged.
    """
    outcome: str
    plan: Any
    plan_version: int
    mutation_id: str
    replayed: bool = False

    @property
    def changed(self) -> bool:
        return self.outcome == OUTCOME_APPLIED


def _locked_active_plan(user_id):
    """The caller's own canonical active plan, row-locked and freshly read.

    The plan is chosen by ``get_active_plan`` — the repository's canonical
    active-plan selector, reused verbatim — and only then locked by id. Writing a
    second "newest row" query here would be a second active-plan authority: if
    the two ever disagreed (a tie-break this module invented, say), the boundary
    would mutate a row that every reader considers inactive.

    Scoping the locking query by ``user_id`` is the authorization, and it is
    re-asserted here rather than inherited from the selector, so no race can
    substitute another user's row between the two statements. There is no code
    path that can address another user's plan, which makes cross-user mutation
    structurally impossible rather than merely checked; "no plan" and "someone
    else's plan" both reach ``PlanNotFound``, so existence never leaks.

    ``populate_existing`` is load-bearing, not decoration. ``get_active_plan``
    has already put this row in the session's identity map, and SQLAlchemy would
    otherwise hand the locking query the SAME in-memory instance with its
    pre-lock column values — so the lock would be real but the data read under it
    would be stale, and two concurrent mutations could both compute a version
    from the same base and lose one update. This is the repository's known
    identity-map staleness footgun (tests/test_concurrency_staleness.py);
    ``populate_existing`` re-reads the columns under the lock we just took.
    """
    active = get_active_plan(user_id)
    if active is None:
        raise PlanNotFound("no active training plan")

    plan = (
        TrainingPlan.query.filter_by(id=active.id, user_id=user_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if plan is None:
        raise PlanNotFound("no active training plan")
    if not plan.lineage_id:
        # Only reachable on a database the plan-history migration has not
        # touched. Refuse rather than write a journal entry that belongs to no
        # lineage — an entry like that could later be selected by an undo for a
        # completely different plan.
        raise PlanStateConflict("this plan has no history identity")
    return plan


def _replay(record) -> PlanMutationResult:
    """Serve a durable past result again, byte-for-byte.

    The plan comes from the record's stored after-snapshot, NOT from the plan row
    as it stands now. That is the difference between "this key already did its
    work" and "here is whatever the plan happens to be" — a later, unrelated
    mutation must not change what a retry of an older key reports.
    """
    document, _program = parse_plan_document(record.after_snapshot)
    return PlanMutationResult(
        outcome=record.outcome,
        plan=document,
        plan_version=record.after_version,
        mutation_id=record.public_id,
        replayed=True,
    )


def _replay_or_conflict(record, expected_type, expected_fingerprint):
    """Replay when the key means the same operation; otherwise fail closed.

    Comparing BOTH the command type and the semantic fingerprint is deliberate:
    the type alone would let two different ``add_exercise`` calls share a key and
    silently collapse into one, and the fingerprint alone would treat a digest
    collision across kinds as agreement.
    """
    if (record.command_type != expected_type
            or record.command_fingerprint != expected_fingerprint):
        # ``from None`` because one of this function's two callers runs inside
        # an ``except IntegrityError`` handler, where implicit chaining would
        # attach the driver's error — statement and bound ``plan_data`` included
        # — to a conflict that is a perfectly ordinary domain outcome. On the
        # other path there is no active exception and this is a no-op.
        raise IdempotencyConflict(
            "this operation key is already bound to a different change"
        ) from None
    return _replay(record)


def _existing_result(user_id, context, expected_type, expected_fingerprint):
    """The durable result for this key, or ``None`` if the key is unused."""
    record = find_by_key(user_id, context.idempotency_key)
    if record is None:
        return None
    return _replay_or_conflict(record, expected_type, expected_fingerprint)


def _arbitrated_result(user_id, context, expected_type, expected_fingerprint):
    """Resolve a lost insert race from durable history, holding nothing open.

    The winning row can only be found by re-reading AFTER the rollback, and
    that read autobegins a fresh transaction. PR2 shipped this boundary with no
    production caller, so leaving that read to request teardown was a
    documented Minor. Sprint 1 PR3 gives it a caller — an AI Coach tool with a
    blocking provider call immediately after it — and a transaction held across
    provider network I/O is exactly what the repository's provider/transaction
    discipline forbids. So the read is closed here instead.

    The rollback is in a ``finally`` and runs on both exits: a replay returns a
    result whose columns are already materialized into plain values, and a
    conflict raises after the same rollback. Nothing is discarded by it — the
    only statement in this transaction is the lookup.
    """
    record = find_by_key(user_id, context.idempotency_key)
    try:
        if record is None:
            return None
        return _replay_or_conflict(record, expected_type, expected_fingerprint)
    finally:
        db.session.rollback()


def apply_plan_mutation(user_id, command, context) -> PlanMutationResult:
    """Apply one typed mutation to the authenticated user's active plan.

    ``user_id`` MUST come from the authenticated server context. It is never read
    from a request body, a tool argument, or model output. ``context`` carries
    the operation key, the actor and an optional bounded reason; it never carries
    authorization and never influences what the mutation does.
    """
    if not isinstance(command, PLAN_MUTATION_COMMANDS):
        # Refused before any read: an unsupported command must not even cause a
        # query, let alone take a lock or claim an operation identity.
        raise InvalidMutation("unsupported mutation command")

    context = validate_context(context)
    ctype = command_type(command)
    fingerprint = semantic_fingerprint(command)

    try:
        replayed = _existing_result(user_id, context, ctype, fingerprint)
        if replayed is not None:
            # Nothing was locked and nothing was written; end the read
            # transaction rather than leaving it open behind the caller.
            db.session.rollback()
            return replayed

        plan = _locked_active_plan(user_id)

        # Second look, now that the plan row is ours. A request that raced us and
        # committed first is visible here, so the common case converges without
        # relying on an exception. The unique constraint still backs this up for
        # the case where both transactions read before either committed.
        replayed = _existing_result(user_id, context, ctype, fingerprint)
        if replayed is not None:
            db.session.rollback()
            return replayed

        before_snapshot = plan.plan_data
        before_version = plan.mutation_version
        lineage_id = plan.lineage_id

        document, _program = parse_plan_document(before_snapshot)
        # A validation failure raises out of here, before anything is inserted:
        # a rejected command consumes no operation identity and leaves no
        # "accepted mutation" in history that never happened.
        mutated, changed = apply_command(document, command)

        if changed:
            after_snapshot = serialize_plan_document(mutated)
            after_version = before_version + 1
            outcome = OUTCOME_APPLIED
        else:
            # A no-op still gets a durable row. Without it, a retry of this key
            # after a later real mutation would be treated as a fresh request and
            # could move state the caller never asked to move.
            after_snapshot = before_snapshot
            after_version = before_version
            outcome = OUTCOME_NO_OP

        record = build_record(
            user_id=user_id,
            lineage_id=lineage_id,
            context=context,
            operation_kind=KIND_MUTATION,
            command_type=ctype,
            command_fingerprint=fingerprint,
            outcome=outcome,
            before_version=before_version,
            after_version=after_version,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
        )
        db.session.add(record)

        if changed:
            plan.plan_data = after_snapshot
            plan.mutation_version = after_version

        db.session.flush()
        mutation_id = record.public_id
        db.session.commit()
    except IntegrityError:
        # The database arbitrated, which is the only arbiter that can be trusted
        # across processes. Whoever holds the key now owns the result.
        db.session.rollback()
        arbitrated = _arbitrated_result(user_id, context, ctype, fingerprint)
        if arbitrated is None:
            raise PlanStateConflict(
                "the plan changed while this mutation was being applied") from None
    except Exception:
        db.session.rollback()
        raise
    else:
        return PlanMutationResult(
            outcome=outcome,
            plan=mutated,
            plan_version=after_version,
            mutation_id=mutation_id,
            replayed=False,
        )

    # Reached only from the IntegrityError branch. The arbitration itself now
    # happens inside the handler, because the winning row has to be materialized
    # before its transaction is closed; a resulting conflict still carries no
    # chained SQL error, which ``_replay_or_conflict`` guarantees with
    # ``from None`` rather than by where it is called.
    return arbitrated


def undo_last_change(user_id, context) -> PlanMutationResult:
    """Reverse the latest reversible change to the caller's own active plan.

    The ONE canonical reversal. There is no redo, no ``rollback_to_version`` and
    no ``restore_snapshot``: an operation that can restore an arbitrary past
    state is a plan-writing API with extra steps, and the whole point of this
    boundary is that plan writes are typed and narrow.

    Restoration is the exact stored bytes of the state that mutation started
    from — never an inferred inverse command, never a model call. An inverse of
    "replace X with Y" looks obvious and is wrong the moment the plan holds
    fields this domain does not model.
    """
    context = validate_context(context)
    fingerprint = undo_fingerprint()

    try:
        replayed = _existing_result(
            user_id, context, UNDO_COMMAND_TYPE, fingerprint)
        if replayed is not None:
            db.session.rollback()
            return replayed

        plan = _locked_active_plan(user_id)

        replayed = _existing_result(
            user_id, context, UNDO_COMMAND_TYPE, fingerprint)
        if replayed is not None:
            db.session.rollback()
            return replayed

        before_snapshot = plan.plan_data
        before_version = plan.mutation_version

        target = latest_reversible(user_id, plan.lineage_id)
        if target is None:
            raise UndoUnavailable("there is no change to undo")

        # Fail closed unless the plan is EXACTLY the state this mutation
        # produced. Version alone cannot carry this: after two undos the plan
        # sits at a much higher version than the mutation being reversed, which
        # is correct. Bytes are the honest precondition, and a mismatch means
        # something wrote the plan outside this boundary — restoring an old
        # snapshot on top of that would silently destroy the other writer's work.
        if snapshot_fingerprint(before_snapshot) != target.after_fingerprint:
            raise UndoConflict("the plan has changed since that update")
        if before_version < target.after_version:
            # Monotonicity sanity: history claims a version the plan never
            # reached, so the two disagree about what happened. Refuse.
            raise UndoConflict("the plan has changed since that update")

        after_snapshot = target.before_snapshot
        after_version = before_version + 1
        # Parsed BEFORE the write, not after the commit: a snapshot this service
        # cannot read is a snapshot it must not restore, and finding that out
        # after committing would leave the plan changed and the caller told it
        # failed.
        restored, _program = parse_plan_document(after_snapshot)

        record = build_record(
            user_id=user_id,
            lineage_id=plan.lineage_id,
            context=context,
            operation_kind=KIND_UNDO,
            command_type=UNDO_COMMAND_TYPE,
            command_fingerprint=fingerprint,
            outcome=OUTCOME_APPLIED,
            before_version=before_version,
            after_version=after_version,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            reverts_mutation_id=target.id,
        )
        db.session.add(record)

        # Forward, never backward. The reverted mutation's row stays exactly as
        # it was written; undo is a compensating event appended to history, not
        # an erasure of it.
        plan.plan_data = after_snapshot
        plan.mutation_version = after_version

        db.session.flush()
        mutation_id = record.public_id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        arbitrated = _arbitrated_result(
            user_id, context, UNDO_COMMAND_TYPE, fingerprint)
        if arbitrated is None:
            # Not our key, so it was the one-undo-per-mutation constraint:
            # another request reversed this mutation first. Deterministically one
            # winner and one refusal, never two undos of one change.
            raise UndoConflict("the plan has changed since that update") from None
    except Exception:
        db.session.rollback()
        raise
    else:
        return PlanMutationResult(
            outcome=OUTCOME_APPLIED,
            plan=restored,
            plan_version=after_version,
            mutation_id=mutation_id,
            replayed=False,
        )

    return arbitrated
