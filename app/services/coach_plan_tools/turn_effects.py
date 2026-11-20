"""What one Coach turn did to the owner's training plan — a transport fact.

Adaptive Coach Foundation V1 B1. Not a tool result (never sent to a model):
a bounded pair a native client uses to re-read its Plan view and show a
pending-confirmation hint without parsing model prose. Kept out of
``executor``/``results`` because those two modules own the closed tool-result
vocabulary the model sees.
"""
from app.extensions import db
from app.services.plan_confirmation import get_pending

from .executor import (
    _settle_transaction,
    plan_changed_this_turn,
    plan_mutation_tools_enabled,
)


def turn_plan_effects(user_id):
    """``{"changed", "pending_confirmation", "proposal_id"}``.

    ``changed`` is this request's own durable evidence (applied/replayed only;
    fail-safe False). ``pending_confirmation`` is a fresh owner-scoped read of
    the one PENDING proposal: ``None`` when it cannot be read (unknown is never
    reported as "nothing pending"); always False with the flag OFF, because
    OFF writes no proposals and publishes no confirmation tools.
    ``proposal_id`` (B2) is that proposal's opaque owner-scoped public id, the
    only handle the native Confirm/Cancel button may send; ``None`` otherwise.
    """
    changed = plan_changed_this_turn()
    if not plan_mutation_tools_enabled():
        return {"changed": changed, "pending_confirmation": False,
                "proposal_id": None}
    try:
        row = get_pending(user_id)
        pending = row is not None
        proposal_id = row.public_id if pending else None
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        pending, proposal_id = None, None
    finally:
        _settle_transaction()
    return {"changed": changed, "pending_confirmation": pending,
            "proposal_id": proposal_id}
