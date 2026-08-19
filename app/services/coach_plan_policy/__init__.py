"""Server-owned plan-mutation impact + confirmation intent (PR4).

Pure: a typed command and already-proven facts in, a closed decision out.
No Flask, no provider, no ORM. The Coach may request a mutation; this package
decides whether it may apply now, must wait for a structural confirmation, or
is unsupported. LOW/MEDIUM/HIGH never appear here.
"""
from .confirmation import CANCEL, CONFIRM, NONE, parse_confirmation_intent
from .decisions import (
    APPLY_NOW,
    REASON_ACTIVE_SESSION_IMPACT,
    REASON_IMPACT_UNDETERMINABLE,
    REASON_MOVE_TRAINING_DAY,
    REASON_REMOVE_EXERCISE,
    REASON_UNKNOWN_COMMAND,
    REFUSE_UNSUPPORTED,
    REQUIRE_CONFIRMATION,
    UNDO_LAST_CHANGE,
    ImpactDecision,
    ImpactFacts,
    decide_plan_mutation_impact,
)

__all__ = [
    "APPLY_NOW",
    "CANCEL",
    "CONFIRM",
    "NONE",
    "REASON_ACTIVE_SESSION_IMPACT",
    "REASON_IMPACT_UNDETERMINABLE",
    "REASON_MOVE_TRAINING_DAY",
    "REASON_REMOVE_EXERCISE",
    "REASON_UNKNOWN_COMMAND",
    "REFUSE_UNSUPPORTED",
    "REQUIRE_CONFIRMATION",
    "UNDO_LAST_CHANGE",
    "ImpactDecision",
    "ImpactFacts",
    "decide_plan_mutation_impact",
    "parse_confirmation_intent",
]
