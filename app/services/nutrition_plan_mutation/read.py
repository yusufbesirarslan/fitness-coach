"""Internal read of the canonical current Nutrition Plan, with no provider I/O."""
from app.services.nutrition_plan_store import newest_plan

from .document import parse_snapshot


def read_current_plan(user_id):
    """Detached exact snapshot of this owner, or None; faults propagate.

    No plan id is accepted. Canonical ordering and owner scope belong to
    nutrition_plan_store. Caller owns the read transaction and must settle it
    before any future provider call. Nothing here claims Coach consent.
    """
    row = newest_plan(user_id)
    return None if row is None else parse_snapshot(row.plan_data)
