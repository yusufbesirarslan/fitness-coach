"""The ONE persistence boundary for the saved Nutrition Plan (``NutritionPlan``).

Two transports replace a saved plan: the browser's ``POST /nutrition-plan/save``
and the native ``PUT /api/v1/nutrition/plan`` (NUTR-PR7). Both call
:func:`replace_nutrition_plan`; neither holds a delete or an insert of its own.

Invariant (unchanged from F2/F3 and PR5): the caller validates score and schema
FIRST, this module verifies the replacement precondition under the lock, and
only then is the old plan removed and the new one written — in one commit. A
refused precondition leaves the old plan canonical, with zero destructive
writes.

LOCKS. The owner's ``user`` row first (``plan_owner_lock.lock_plan_owner`` — the
repository's per-owner serialization idiom, and the only object that exists
when the owner has NO plan, i.e. for a create), then the owner's
``nutrition_plan`` rows. That is a prefix of the documented repository lock
order, so it cannot form a cycle with the Training writers.

The browser route passes :data:`UNCONDITIONAL`: its behaviour stays exactly
"replace whatever is there" (the accepted PR5 cross-tab residual). The native
route passes a precondition check, so two native clients that read one plan
revision cannot both succeed.
"""
from datetime import datetime
import json

from app.extensions import db
from app.models import NutritionPlan
from app.services.plan_owner_lock import lock_plan_owner


UNCONDITIONAL = None


def newest_plan_query(user_id):
    """The canonical "active plan" selector: the owner's newest row.

    Same ordering ``/nutrition-plan/active``, quick-add and the PR6 day view
    use, with the primary key as a deterministic tiebreak.
    """
    return (NutritionPlan.query.filter_by(user_id=user_id)
            .order_by(NutritionPlan.created_at.desc(), NutritionPlan.id.desc()))


def newest_plan(user_id):
    return newest_plan_query(user_id).first()


def replace_nutrition_plan(user_id, document, score, check=UNCONDITIONAL):
    """Replace the owner's saved plan with an already-validated document.

    ``check`` is ``UNCONDITIONAL`` or a callable receiving the CURRENT newest
    row (or ``None``) read under the lock; it raises to refuse. Nothing is
    deleted before it returns. Returns the committed new row.
    """
    lock_plan_owner(user_id)
    current = (newest_plan_query(user_id)
               .populate_existing().with_for_update().first())
    if check is not UNCONDITIONAL:
        try:
            check(current)
        except Exception:
            db.session.rollback()
            raise
    NutritionPlan.query.filter_by(user_id=user_id).delete()
    row = NutritionPlan(
        user_id=user_id,
        plan_data=json.dumps(document, ensure_ascii=False),
        score=score,
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.commit()
    return row
