"""The ONE per-owner serialization point every ``TrainingPlan`` create takes.

A plan row can be locked once it exists, and ``plan_mutation`` and
``plan_replacement`` both do exactly that. Creation is the case a plan-row lock
physically cannot cover: a user with no plan has no row to lock, so two callers
can each observe "there is no plan" and each insert one. Locking the owner's
``user`` row is what closes that window — it is the repository's established
idiom for serializing a per-user singleton decision
(``memory_manager.get_or_create_active_conversation``, ``supplements``,
``premium.reserve_ai_quota_in_transaction``).

This module exists because there is more than one create writer, and a
per-owner boundary is only a boundary if every one of them stands behind the
SAME object. There are exactly two:

* ``plan_replacement.replace_training_plan`` — the browser's
  ``POST /training-plan/save``;
* ``mobile_training_generation.store.commit_plan`` — the native
  generate-and-persist command.

The native command already serialized itself against other native commands
(``mobile_training_generation.locking``, a PostgreSQL advisory lock) and locked
its own idempotency-operation row, but neither of those objects is anything a
browser tab contends for. So a browser create and a native create could both
observe an absent plan and both insert, and the owner would hold two plans —
a state every reader treats as impossible, because ``get_active_plan`` picks the
newer row and the older one silently becomes data the user owns but cannot
reach. Duplicating the lock statement in the second writer would have made that
correct today and re-divergent at the next writer; naming the primitive once is
what makes "all create writers share one contract" checkable.

LOCK ORDER
----------
One order, repository-wide, and every writer takes a prefix of it:

1. ``training_plan_generation_operation`` row — native idempotency
2. ``user`` row — THIS lock, and the AI-quota/XP writers
3. ``training_plan`` rows — ``plan_mutation``, ``plan_replacement``

``plan_replacement`` takes 2 then 3. ``store.commit_plan`` takes 1, then 2, then
inserts at 3. ``store.claim`` and ``store.record_failure`` take 1 then 2 (the
quota reservation and its refund). ``plan_mutation`` and the Coach executor take
3 alone. Nothing takes 3 before 2 or 2 before 1, so there is no cycle. The
native advisory lock sits outside this ordering entirely and cannot join a
cycle: it is ``pg_try_advisory_lock``, which never waits.

Ordering is not merely a convention here, because PostgreSQL enforces part of
it: ``training_plan.user_id`` references ``user.id``, so an INSERT takes a
``FOR KEY SHARE`` lock on the owner row, and that conflicts with the ``FOR
UPDATE`` this module takes. A writer that inserted first and locked the owner
afterwards would be requesting a lock upgrade, which is the classic way two
such writers deadlock. Taking the owner lock BEFORE the insert — as both create
writers do — means the FK's share lock is always already covered by a stronger
lock the same transaction holds.
"""
from app.extensions import db
from app.models import User


def lock_plan_owner(user_id):
    """``SELECT … FOR UPDATE`` the owner row, for its serializing effect alone.

    A COLUMN query, not an entity query. An entity query returns the
    already-identity-mapped ``current_user`` WITHOUT refreshing it, so the lock
    would be real while the data read under it was stale — the repository's
    known footgun (``app/hooks.py``, ``app/services/gamification.py``). Nothing
    is read off the result: callers re-read what they need under the lock.

    On SQLite this is a no-op, as everywhere else in the repository: that
    backend admits one writer at a time, so the critical section is already
    exclusive.

    The caller owns the transaction. The lock is held until that transaction
    commits or rolls back, so callers must take it INSIDE the transaction whose
    decision it protects — never in a helper that commits.
    """
    db.session.query(User.id).filter_by(id=user_id).with_for_update().first()
