"""The transaction + ownership owner for training-plan mutations (brief §§11,13).

This is the only place in the application allowed to write ``TrainingPlan``
targeted-mutation state. It is server-authoritative, owner-scoped, deterministic
and free of any AI/provider dependency; the pure targeted-mutation logic lives in
``document.py`` and the bounds in ``validation.py``.

Sequence, in order, with no network call anywhere inside the transaction:

    validate command type
    → resolve the caller's OWN canonical active plan (row lock)
    → parse authoritative current state
    → apply the targeted mutation to a copy
    → no-op? return without writing
    → serialize, assign, flush
    → commit

Any failure rolls back, so there is never a partial logical mutation. Exactly-once
behaviour is **not** claimed: a caller that retries after a lost response can
apply the same change twice. For the supported commands that is either idempotent
(replace/update/move converge on the desired state) or additive (a second `add`
appends a second entry), and PR1 deliberately does not build the persistent
mutation journal that would make it exactly-once — that is reserved for later work
(brief §14).
"""
from dataclasses import dataclass
from typing import Any

from app.extensions import db
from app.models import TrainingPlan
from app.services.today_facts import get_active_plan

from .commands import PLAN_MUTATION_COMMANDS
from .document import (
    apply_command,
    parse_plan_document,
    serialize_plan_document,
)
from .errors import InvalidMutation, PlanNotFound


@dataclass(frozen=True)
class PlanMutationResult:
    """The outcome of one mutation.

    ``changed`` distinguishes an applied mutation from a deterministic no-op, so
    a caller can tell "I made this true" from "this was already true" without
    inspecting the plan. ``plan`` is the canonical updated document — the caller
    never has to re-read to learn the result.
    """
    changed: bool
    plan: Any


def _locked_active_plan(user_id):
    """The caller's own canonical active plan, row-locked for update.

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
    else's plan" both reach ``PlanNotFound``, so existence never leaks (§11).

    ``with_for_update`` is the repository's established PostgreSQL-safe pattern
    (a harmless no-op on SQLite) and prevents the obvious lost update when two
    mutations race on one plan. A plan deleted between the select and the lock
    fails deterministically as ``PlanNotFound`` rather than mutating nothing.
    """
    active = get_active_plan(user_id)
    if active is None:
        raise PlanNotFound("no active training plan")

    plan = (
        TrainingPlan.query.filter_by(id=active.id, user_id=user_id)
        .with_for_update()
        .first()
    )
    if plan is None:
        raise PlanNotFound("no active training plan")
    return plan


def apply_plan_mutation(user_id, command) -> PlanMutationResult:
    """Apply one typed mutation to the authenticated user's active plan.

    ``user_id`` MUST come from the authenticated server context. It is never read
    from a request body, a tool argument, or model output.
    """
    if not isinstance(command, PLAN_MUTATION_COMMANDS):
        # Refused before any read: an unsupported command must not even cause a
        # query, let alone a lock.
        raise InvalidMutation("unsupported mutation command")

    try:
        plan = _locked_active_plan(user_id)
        document, _program = parse_plan_document(plan.plan_data)
        mutated, changed = apply_command(document, command)

        if not changed:
            # Deterministic no-op: no write, no churn, no history, and no
            # regeneration. The read lock is released by the rollback below.
            db.session.rollback()
            return PlanMutationResult(changed=False, plan=document)

        plan.plan_data = serialize_plan_document(mutated)
        db.session.flush()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return PlanMutationResult(changed=True, plan=mutated)
