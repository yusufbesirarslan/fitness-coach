"""Server-owned Coach capability request (#533 / #547 F3 companion D-1).

When a Coach turn needs a capability the owner has not granted (Coach plan
editing, saved Nutrition Plan access), the write/read tools of that family are
simply not published, so the turn has no typed way to say "this needs a
permission". A client must never infer missing authority from reply text.

This module adds that typed signal and nothing else:

* **Publication.** One closed tool, ``request_coach_capability``, whose only
  argument is an enum of the capabilities that are actionable for THIS owner
  in THIS operation: the capability's launched contract is open, it is not in
  effect, every missing purpose is published and grantable for the account,
  and granting it would actually open a Coach tool (Training: the plan-tool
  flag; Nutrition: an approved saved-plan READ pair or the open C1 edit
  boundary). Nothing actionable → nothing published, so ordinary r2 chat and
  the tool list are byte-identical. It is never published on a typed
  confirm/cancel answer or without the user's own text this turn.
* **Execution.** The server re-checks the same predicate at call time and
  records the capability for the turn (first request wins; one per turn). The
  model-facing result is fixed. It never grants, stages a proposal, writes,
  reads plan content or changes allowance accounting.
* **Effect.** ``plan_effects.capability_request = {"capability", "purpose"}``
  on ``POST /api/v1/coach/messages`` only, re-verified at response time inside
  the same Coach operation (a consent change during the turn retires it). The
  purpose comes from ``coach_capabilities.SPECS``, never from the model.

What decides "this turn needed it" is the model's call of a closed tool —
the same intent channel every plan tool uses — bounded by server state the
model cannot touch. The tool establishes no authority: the most it can do is
offer the user a review of a permission the server already considers missing
and grantable. Nothing is replayed after a grant: the user re-sends their own
message.
"""
from flask import g

from app.services import ai_consent, coach_capabilities

from . import executor

REQUEST_CAPABILITY_TOOL = "request_coach_capability"

OUTCOME_OFFERED = "review_offered"
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOMES = (OUTCOME_OFFERED, OUTCOME_UNAVAILABLE)

#: Order of the enum (and of the registry): Training first, then Nutrition.
CAPABILITIES = (coach_capabilities.TRAINING_PLAN_WRITE,
                coach_capabilities.NUTRITION_PLAN_ACCESS)

_REQUEST_ATTR = "_coach_capability_request"
_OFFERABLE_ATTR = "_coach_capability_offerable"


def reset_turn():
    for attr in (_REQUEST_ATTR, _OFFERABLE_ATTR):
        try:
            if hasattr(g, attr):
                delattr(g, attr)
        except RuntimeError:
            return


def _applicable(user_id, capability):
    """Would the owner's own grants open a Coach tool of this family now?"""
    if not executor.plan_mutation_tools_enabled():
        return False
    if capability == coach_capabilities.TRAINING_PLAN_WRITE:
        return True
    if capability == coach_capabilities.NUTRITION_PLAN_ACCESS:
        from . import nutrition
        return nutrition.capability_applicable(user_id)
    return False


def _user_asked():
    """The turn carries the user's own text and is not a confirm/cancel answer."""
    from .grounding import current_user_message
    if not current_user_message().strip():
        return False
    return executor.current_confirmation_intent() not in (executor.CONFIRM, executor.CANCEL)


def _open(user_id, capability):
    operation = ai_consent.current_operation()
    if (operation is None or operation.feature != "coach" or type(user_id) is not int
            or operation.owner != user_id):
        return False
    try:
        return bool(_applicable(user_id, capability)
                    and ai_consent.capability_request_open(user_id, capability))
    except Exception:
        return False


def offerable(user_id):
    """Actionable capabilities for this turn, cached per (owner, generation)."""
    operation = ai_consent.current_operation()
    if operation is None or not _user_asked():
        return ()
    key = (user_id, operation.generation)
    try:
        cached = getattr(g, _OFFERABLE_ATTR, None)
    except RuntimeError:
        return ()
    if cached is not None and cached[0] == key:
        return cached[1]
    found = tuple(capability for capability in CAPABILITIES if _open(user_id, capability))
    try:
        setattr(g, _OFFERABLE_ATTR, (key, found))
    except RuntimeError:
        pass
    return found


def tool_def(capabilities):
    return {
        "name": REQUEST_CAPABILITY_TOOL,
        "description": (
            "Call ONLY when the user explicitly asks in this message to change their "
            "training plan (" + coach_capabilities.TRAINING_PLAN_WRITE + ") or to read "
            "or change their saved Nutrition Plan (" + coach_capabilities.NUTRITION_PLAN_ACCESS
            + ") and that capability is listed here: it is currently not permitted. "
            "This grants nothing, reads nothing and changes nothing; the app shows the "
            "user a permission they may review, allow or decline. Never call it for "
            "advice, questions or anything else. After it, say no change was made and "
            "that the user can review the permission in the app, then answer normally."),
        "parameters": {
            "type": "object",
            "properties": {"capability": {"type": "string", "enum": list(capabilities)}},
            "required": ["capability"],
            "additionalProperties": False,
        },
    }


def published_tool_defs(user_id):
    """The request tool, only with something actionable (else nothing)."""
    capabilities = offerable(user_id)
    return (tool_def(capabilities),) if capabilities else ()


def _result(outcome):
    return {"status": "ok" if outcome == OUTCOME_OFFERED else "unsupported",
            "outcome": outcome}


def execute(user_id, arguments):
    """Record a request for the turn; never grants, reads or writes anything."""
    outcome = OUTCOME_UNAVAILABLE
    try:
        capability = arguments.get("capability") if (
            isinstance(arguments, dict) and set(arguments) == {"capability"}) else None
        if (capability in CAPABILITIES and _user_asked()
                and capability in offerable(user_id) and _open(user_id, capability)):
            outcome = OUTCOME_OFFERED
            try:
                if getattr(g, _REQUEST_ATTR, None) is None:
                    setattr(g, _REQUEST_ATTR, (user_id, capability))
            except RuntimeError:
                outcome = OUTCOME_UNAVAILABLE
    finally:
        executor._settle_transaction()
    executor._log(REQUEST_CAPABILITY_TOOL, outcome)
    return _result(outcome)


def turn_effect(user_id):
    """``plan_effects.capability_request`` or ``None``, re-verified now."""
    try:
        recorded = getattr(g, _REQUEST_ATTR, None)
    except RuntimeError:
        return None
    if not (isinstance(recorded, tuple) and len(recorded) == 2 and recorded[0] == user_id):
        return None
    capability = recorded[1]
    spec = coach_capabilities.spec(capability)
    if spec is None or not _open(user_id, capability):
        return None
    return {"capability": capability, "purpose": spec.purpose}
