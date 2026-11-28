"""What one Coach turn did to the owner's training plan — a transport fact.

Adaptive Coach Foundation V1 B1. Not a tool result (never sent to a model):
a bounded pair a native client uses to re-read its Plan view and show a
pending-confirmation hint without parsing model prose. Kept out of
``executor``/``results`` because those two modules own the closed tool-result
vocabulary the model sees.
"""
from app.extensions import db
from app.services.plan_confirmation import get_pending

from .proposals import is_training_proposal

from .executor import (
    _settle_transaction,
    plan_mutation_tools_enabled,
    training_plan_changed_this_turn,
)


def turn_plan_effects(user_id, language=None):
    """``{"changed", "pending_confirmation", "proposal_id"}``.

    ``changed`` is this request's own durable evidence (applied/replayed only;
    fail-safe False). ``pending_confirmation`` is a fresh owner-scoped read of
    the one PENDING proposal: ``None`` when it cannot be read (unknown is never
    reported as "nothing pending"); always False with the flag OFF, because
    OFF writes no proposals and publishes no confirmation tools.
    ``proposal_id`` (B2) is that proposal's opaque owner-scoped public id, the
    only handle the native Confirm/Cancel button may send; ``None`` otherwise.
    """
    changed = training_plan_changed_this_turn()
    if not plan_mutation_tools_enabled():
        return {"changed": changed, "pending_confirmation": False,
                "proposal_id": None}
    try:
        row = get_pending(user_id)
        pending = is_training_proposal(row)
        proposal_id = row.public_id if pending else None
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        pending, proposal_id = None, None
    finally:
        _settle_transaction()
    effects = {"changed": changed, "pending_confirmation": pending,
               "proposal_id": proposal_id}
    # C2: additive, and present ONLY while the Nutrition boundary is available
    # (closed in this source), so the production wire is unchanged.
    from .nutrition import turn_effects as nutrition_effects
    nutrition = nutrition_effects(user_id, language)
    if nutrition is not None:
        effects["nutrition_plan"] = nutrition
    # #533 D-1: additive, only when this Coach turn asked for a capability the
    # owner can still be offered (re-verified now, in the same operation).
    from .capability_request import turn_effect as capability_effect
    capability = capability_effect(user_id)
    if capability is not None:
        effects["capability_request"] = capability
    return effects
