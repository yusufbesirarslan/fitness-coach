"""The transaction owner for WHOLE-plan training replacement.

``POST /training-plan/save`` is the only destructive ``TrainingPlan`` path in
the app: it removes the user's current plan and installs a generated proposal in
its place. Until now it did that unconditionally, which made a real lost update
reachable — a client generated a proposal against plan N, another authorized
mutation (an AI Coach change, a second tab) moved the plan to N+1, and the save
then deleted N+1 and installed a proposal derived from N. A client-side
freshness re-read narrows that window but cannot close it: between the re-read
and the write there is always a gap, and only the database can decide a race.

So the server became the final authority. A destructive save now carries the
identity of the plan the caller *believes* it is replacing, and this module
compares that expectation against what actually exists **under the same lock
that protects the replacement**. Match -> replace. Mismatch -> refuse with zero
destructive write.

This is not a new version authority. The freshness identity is the pair the
canonical plan row has carried since Sprint 1 PR2 and the native surface already
publishes: ``(lineage_id, mutation_version)``. ``lineage_id`` names the plan
lineage — a fresh row always gets a fresh one, so a replacement is always
distinguishable from a mutation of the same plan — and ``mutation_version`` is
the server-authoritative history position within that lineage. BOTH must match:
version alone would accept an expectation aimed at a completely different
lineage that happens to sit at the same number, which is exactly what a
regeneration produces (every replacement starts a new lineage at version 0).

LOCK ORDER, and why there are two locks
---------------------------------------
1. the owner's ``user`` row, then 2. the owner's ``training_plan`` rows.

The plan-row lock is the load-bearing one and is taken exactly the way
``plan_mutation`` takes it (``populate_existing().with_for_update()``), because
that is what makes a narrow Coach mutation and a whole replacement serialize on
the *same* object rather than through two unrelated schemes.

The owner-row lock exists for the one case the plan-row lock physically cannot
cover: creation. When the user has no plan there is no row to lock, so two
concurrent "there is no plan, create one" requests would both pass their check
and the second would delete the first's brand-new plan. Locking the owner row is
the repository's established idiom for serializing a per-user singleton decision
(``memory_manager.get_or_create_active_conversation``, ``supplements``,
``premium.reserve_ai_quota_in_transaction``).

No other writer takes a ``TrainingPlan`` lock and then a ``User`` lock, so this
order introduces no cycle: ``plan_mutation`` and the Coach executor lock the plan
row only, workout completion locks a session row before awarding XP, and native
generation locks its own operation row.
"""
from dataclasses import dataclass

from app.extensions import db
from app.models import TrainingPlan, User
from app.services.today_facts import get_active_plan


# The request field carrying the precondition. ``None`` is a meaningful value
# ("I expect no active plan"), so callers must test membership, never truthiness.
EXPECTATION_FIELD = "expected_plan"

CODE_PRECONDITION_INVALID = "TRAINING_PLAN_SAVE_PRECONDITION_INVALID"
CODE_PLAN_CHANGED = "TRAINING_PLAN_SAVE_PLAN_CHANGED"

I18N_PRECONDITION_INVALID = "plan.save_precondition_invalid"
I18N_PLAN_CHANGED = "plan.save_plan_changed"

# ``lineage_id`` is a 32-character token today; the column allows 64. Bound the
# accepted string by the column rather than by today's generator, so a future
# widening does not silently start rejecting real identities.
_MAX_LINEAGE_LENGTH = 64


class PlanReplacementError(Exception):
    """A destructive save this boundary refuses.

    ``reason`` is a bounded, low-cardinality token for logs ONLY. The public body
    carries the class's code and nothing else: a caller learns *that* its
    expectation did not hold, never which field of the current plan it missed.
    """

    public_code = CODE_PRECONDITION_INVALID
    i18n_key = I18N_PRECONDITION_INVALID
    http_status = 400
    retryable = False

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason

    def to_body(self, translate) -> dict:
        return {
            "error": translate(self.i18n_key),
            "code": self.public_code,
            "retryable": self.retryable,
        }


class PlanPreconditionInvalid(PlanReplacementError):
    """The request did not state a readable expectation. Nothing was read."""


class PlanReplacementConflict(PlanReplacementError):
    """The plan the caller intended to replace is not the plan that exists.

    Deliberately 409 and not a validation failure: the proposal may be perfectly
    valid. What changed is the world, and the only honest client response is to
    re-read the canonical plan and decide again.
    """

    public_code = CODE_PLAN_CHANGED
    i18n_key = I18N_PLAN_CHANGED
    http_status = 409


@dataclass(frozen=True)
class PlanExpectation:
    """What the caller believes the server currently holds.

    ``absent`` is explicit rather than inferred from empty identity fields:
    "I expect no plan" is a real, checkable assertion, and collapsing it into a
    missing value is how a create silently becomes an unconditional overwrite.
    """

    absent: bool
    lineage_id: str = ""
    mutation_version: int = -1


@dataclass(frozen=True)
class ReplacementResult:
    """The identity of the plan that now exists, for the caller to re-anchor on."""

    lineage_id: str
    mutation_version: int


def parse_expectation(payload) -> PlanExpectation:
    """Read the caller's precondition, or refuse.

    Fail-closed on every axis: the field must be present (an omission IS an
    unconditional destructive save, which is the bug this module exists to
    remove), the object must carry exactly the two identity fields and nothing
    else, and neither may be a type that merely coerces. ``bool`` is rejected
    explicitly because it is an ``int`` subclass in Python, so ``True`` would
    otherwise sail through as version 1.
    """
    if not isinstance(payload, dict):
        raise PlanPreconditionInvalid("payload_invalid")
    if EXPECTATION_FIELD not in payload:
        raise PlanPreconditionInvalid("missing")

    raw = payload[EXPECTATION_FIELD]
    if raw is None:
        return PlanExpectation(absent=True)
    if not isinstance(raw, dict):
        raise PlanPreconditionInvalid("not_an_object")
    if set(raw) != {"lineage_id", "mutation_version"}:
        raise PlanPreconditionInvalid("unexpected_fields")

    lineage_id = raw["lineage_id"]
    mutation_version = raw["mutation_version"]
    if (not isinstance(lineage_id, str)
            or not 1 <= len(lineage_id) <= _MAX_LINEAGE_LENGTH):
        raise PlanPreconditionInvalid("lineage_invalid")
    if isinstance(mutation_version, bool) or not isinstance(mutation_version, int):
        raise PlanPreconditionInvalid("version_invalid")
    if mutation_version < 0:
        raise PlanPreconditionInvalid("version_invalid")
    return PlanExpectation(
        absent=False, lineage_id=lineage_id, mutation_version=mutation_version)


def _lock_owner(user_id):
    """Serialize this owner's whole-plan writes, including creation.

    A COLUMN query, not an entity query: an entity query returns the
    already-identity-mapped ``current_user`` without refreshing it, so the lock
    would be real while the data read under it was stale — the repository's known
    footgun (``app/hooks.py``, ``app/services/gamification.py``). Nothing is read
    off the result; the row is locked for its serializing effect alone.

    On SQLite this is a no-op, as everywhere else in the repository: that backend
    admits one writer at a time, so the critical section is already exclusive.
    """
    db.session.query(User.id).filter_by(id=user_id).with_for_update().first()


def replace_training_plan(user_id, expectation, *, plan_data,
                          score) -> ReplacementResult:
    """Install ``plan_data`` as the owner's plan iff ``expectation`` still holds.

    ``user_id`` MUST come from the authenticated server context. The expectation
    is a freshness assertion and never an authorization one: every statement here
    is scoped by ``user_id``, so no lineage a caller can name addresses another
    user's row — a wrong lineage is a conflict, never a cross-owner read.

    The proposal is expected to be ALREADY validated. Signature, structure,
    semantics, catalog resolution and equipment compatibility are expensive and
    must not run with a row lock held; the caller does them first, and this
    critical section stays short enough that ordinary concurrent saves queue
    rather than contend.
    """
    try:
        _lock_owner(user_id)

        # Lock the owner's plan rows themselves. ``populate_existing`` is
        # load-bearing: without it SQLAlchemy hands back identity-mapped
        # instances holding their pre-lock column values, and the comparison
        # below would be made against a version another transaction has already
        # moved (tests/test_concurrency_staleness.py).
        owned = (TrainingPlan.query.filter_by(user_id=user_id)
                 .populate_existing().with_for_update().all())

        # WHICH row is active is not this module's decision to make. Reusing the
        # canonical selector keeps a replacement from ever targeting a row that
        # every reader considers inactive.
        current = get_active_plan(user_id)
        locked_ids = {row.id for row in owned}
        if current is not None and current.id not in locked_ids:
            # A row appeared from a writer that does not participate in this
            # lock. Refusing is the only safe answer: it is not ours to delete.
            raise PlanReplacementConflict("plan_appeared")

        if expectation.absent:
            if current is not None:
                raise PlanReplacementConflict("expected_absent_but_plan_exists")
            # A create deletes NOTHING. Issuing the destructive statement anyway
            # (as the unconditional version did) is what let a request that
            # believed the user had no plan wipe one that had just been created.
            doomed = ()
        else:
            if current is None:
                raise PlanReplacementConflict("expected_present_but_absent")
            if current.lineage_id != expectation.lineage_id:
                raise PlanReplacementConflict("lineage_mismatch")
            if current.mutation_version != expectation.mutation_version:
                raise PlanReplacementConflict("version_mismatch")
            # Exactly the rows observed under the lock — never a blanket
            # ``WHERE user_id = :me``, which would also delete a row committed by
            # a non-participating writer after this read.
            doomed = tuple(locked_ids)

        if doomed:
            # ``synchronize_session="fetch"`` rather than ``False``: these rows
            # are in this session's identity map (the locking read put them
            # there), and a bulk delete that does not tell the session about
            # them leaves persistent instances behind for rows that no longer
            # exist — which SQLAlchemy then collides with when the replacement
            # is flushed onto a reused primary key.
            (TrainingPlan.query.filter(TrainingPlan.id.in_(doomed))
             .delete(synchronize_session="fetch"))

        # Replacement semantics are UNCHANGED: no lineage or version is assigned
        # here, so the column defaults mint a fresh lineage at version 0 exactly
        # as they always have. This module adds a precondition, not a lineage
        # redesign.
        replacement = TrainingPlan(
            user_id=user_id, plan_data=plan_data, score=score)
        db.session.add(replacement)
        # Materialize the new identity BEFORE the commit expires the instance, so
        # the caller can hand it back without a second read (and without a second
        # race).
        db.session.flush()
        result = ReplacementResult(
            lineage_id=replacement.lineage_id,
            mutation_version=replacement.mutation_version,
        )
        db.session.commit()
    except Exception:
        # Every refusal and every failure leaves the current plan exactly as it
        # was: the delete and the insert are in this transaction, so a rollback
        # can never leave the owner with zero plans or a half-written one.
        db.session.rollback()
        raise
    return result
