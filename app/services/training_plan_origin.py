"""Read-side origin of a canonical Training plan lineage (LP21).

Answers one question for the native reads (``GET /training/plans/current``,
``GET /today``): *how was this plan lineage created?* The answer is derived
only from durable server evidence — never from plan content, a client hint or
a guess:

* ``general_template`` — exactly one ``training_plan_generation_operation``
  row is bound to the plan's lineage, it belongs to the owner, it names this
  plan row, and it is a general receipt (``is_general_receipt``). Only
  ``store.commit_general_plan`` writes that state, in the same transaction as
  the plan, so the receipt proves the lineage was born from the template.
* ``unknown`` — anything else. That covers AI first plans, browser-saved
  plans, replacement results, plans that predate receipts, and contradictory
  ledger evidence. ``unknown`` claims nothing: it is not "AI", and a client
  must not render it as AI or as personalized.

Origin describes the lineage's CREATION. Later journal edits keep the lineage
(and so the origin); ``mutation_version > 0`` is how a client sees them.

Neutral on purpose: it imports only the model, so the Today and Training read
projections can use it without reaching the general-plan package (whose
``__init__`` pulls the canonical save validator) or the AI ledger module
(whose ``store`` imports provider and premium modules). Read-only: one
owner-scoped SELECT, no writes. A failed read raises; callers fail closed and
never downgrade a read failure to ``unknown``.
"""
from __future__ import annotations

from app.models import (
    TRAINING_PLAN_GENERATION_SUCCEEDED,
    TrainingPlanGenerationOperation,
)


# The only template the general command accepts (``general_training_plan``
# ``TEMPLATE_ID``; parity pinned by tests). A receipt cannot carry the template
# itself — only the fingerprint that binds it — so a second template must
# version this projection before it ships.
GENERAL_TEMPLATE_ID = "general_full_body_v1"

ORIGIN_GENERAL = "general_template"
ORIGIN_UNKNOWN = "unknown"


def is_general_receipt(operation) -> bool:
    """The only ledger state a general create writes; no AI path reaches it."""
    return (operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED
            and operation.provider_dispatch_possible is False
            and operation.allowance_metered is None
            and operation.quota_reserved is False)


def general_origin() -> dict:
    return {"kind": ORIGIN_GENERAL, "template": GENERAL_TEMPLATE_ID,
            "personalized": False}


def unknown_origin() -> dict:
    return {"kind": ORIGIN_UNKNOWN, "template": None, "personalized": None}


def resolve_plan_origin(plan, user_id: int) -> dict:
    """The owner's plan lineage origin; ``unknown`` unless a receipt proves it."""
    rows = (TrainingPlanGenerationOperation.query
            .filter_by(user_id=user_id, plan_lineage_id=plan.lineage_id)
            .limit(2)
            .all())
    if (len(rows) == 1
            and plan.user_id == user_id
            and rows[0].training_plan_id == plan.id
            and is_general_receipt(rows[0])):
        return general_origin()
    return unknown_origin()
