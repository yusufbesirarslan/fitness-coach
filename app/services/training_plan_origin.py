"""Read-side origin of a canonical Training plan lineage (LP21).

Answers one question for the native reads (``GET /training/plans/current``,
``GET /today``) **only when the client opts in** with ``include_origin=1``:
*how was this plan lineage created?* Without the opt-in both reads keep
their pre-LP21 contract exactly (shipped mobile DTOs reject unknown keys) and
issue no origin read at all.

The answer is derived only from durable server evidence — never from plan
content, a client hint or a guess. Exactly one owner-scoped
``training_plan_generation_operation`` row must be bound to the plan's
lineage AND name this plan row; then:

* ``general_template`` — the row is a general receipt (``is_general_receipt``);
  only ``store.commit_general_plan`` writes that state, in the same
  transaction as the plan. ``template`` is the one template whose closed
  request space contains the receipt's fingerprint
  (``general_plan_identity``); a general receipt whose fingerprint names
  neither template is ``unknown``.
* ``ai_generated`` — the row is ``SUCCEEDED`` with
  ``provider_dispatch_possible is True``: the native AI first-plan command
  committed ``authorize_dispatch`` before the candidate this plan was built
  from existed.
* ``unknown`` — anything else: browser-saved plans, replacement results,
  historical rows whose dispatch marker is NULL, plans that predate receipts,
  and contradictory evidence. ``unknown`` claims nothing; a missing general
  receipt is never read as "AI".

Origin describes the lineage's CREATION. Later journal edits keep the lineage
(and so the origin); ``mutation_version > 0`` is how a client sees them.

Neutral on purpose: it imports only the model and the standard-library
request identities, so the Today and Training read
projections can use it without reaching the general-plan package or the AI
ledger module (whose ``store`` imports provider and premium modules).
Read-only: one owner-scoped SELECT, no writes. A failed read raises; callers
fail closed and never downgrade a read failure to ``unknown``.
"""
from __future__ import annotations

from app.models import (
    TRAINING_PLAN_GENERATION_SUCCEEDED,
    TrainingPlanGenerationOperation,
)
from app.services.general_plan_identity import TEMPLATE_IDS, template_for_fingerprint


# The templates the general command accepts (``general_training_plan``;
# parity pinned by tests). A receipt carries only the fingerprint that binds
# its request, so the template is recovered from it, never from content.
GENERAL_TEMPLATE_IDS = TEMPLATE_IDS

ORIGIN_GENERAL = "general_template"
ORIGIN_AI = "ai_generated"
ORIGIN_UNKNOWN = "unknown"

INCLUDE_ORIGIN_PARAM = "include_origin"


class InvalidOriginExpansion(ValueError):
    """``include_origin`` was present but not exactly one ``1``."""


def origin_requested(args) -> bool:
    """The per-request opt-in. Absent → legacy contract; exactly ``1`` → origin.

    Anything else (``0``, ``true``, empty, repeated) is refused, never coerced:
    a client that thinks it asked for origin must not silently get the legacy
    shape. ``args`` is the request's query ``MultiDict``.
    """
    values = args.getlist(INCLUDE_ORIGIN_PARAM)
    if not values:
        return False
    if values == ["1"]:
        return True
    raise InvalidOriginExpansion(INCLUDE_ORIGIN_PARAM)


def is_general_receipt(operation) -> bool:
    """The only ledger state a general create writes; no AI path reaches it."""
    return (operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED
            and operation.provider_dispatch_possible is False
            and operation.allowance_metered is None
            and operation.quota_reserved is False)


def _is_ai_receipt(operation) -> bool:
    return (operation.status == TRAINING_PLAN_GENERATION_SUCCEEDED
            and operation.provider_dispatch_possible is True)


def general_origin(template: str) -> dict:
    if template not in GENERAL_TEMPLATE_IDS:
        raise ValueError("not a general template")
    return {"kind": ORIGIN_GENERAL, "template": template,
            "personalized": False}


def ai_origin() -> dict:
    return {"kind": ORIGIN_AI, "template": None, "personalized": True}


def unknown_origin() -> dict:
    return {"kind": ORIGIN_UNKNOWN, "template": None, "personalized": None}


def resolve_plan_origin(plan, user_id: int) -> dict:
    """The owner's plan lineage origin; ``unknown`` unless a receipt proves it."""
    rows = (TrainingPlanGenerationOperation.query
            .filter_by(user_id=user_id, plan_lineage_id=plan.lineage_id)
            .limit(2)
            .all())
    if (len(rows) != 1
            or plan.user_id != user_id
            or rows[0].training_plan_id != plan.id):
        return unknown_origin()
    if is_general_receipt(rows[0]):
        template = template_for_fingerprint(rows[0].request_fingerprint)
        if template is None:
            return unknown_origin()
        return general_origin(template)
    if _is_ai_receipt(rows[0]):
        return ai_origin()
    return unknown_origin()
