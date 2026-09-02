"""The AI Coach's plan-mutation tool surface — Adaptive Coaching Sprint 1 PR3.

The single bridge between a language model and the canonical plan-mutation
boundary. PR1 built the boundary and PR2 gave it durable history, versioning and
undo; both shipped with no caller at all. This package is the caller, and it is
the only one the Coach gets:

    provider (Bedrock / OpenAI)
            ↓  a tool call, as text
    ai_coach dispatcher            ← authenticated user_id lives here
            ↓
    coach_plan_tools               ← this package: the trusted context
            ↓
    plan_mutation                  ← PR1/PR2: owns the plan and the transaction

The arrow never skips a layer. ``ai_coach`` does not import ``plan_mutation``,
this package does not import ``ai_coach``, and
``tests/test_plan_mutation_architecture.py`` fails the build if either changes.

What "trusted context" means concretely — the model supplies user intent and
nothing else. It cannot name a user, a plan, a lineage, a version, a journal
entry, an actor, an operation key, or a snapshot; those are minted or resolved
server-side (brief §12). The model asks; the server decides, records and
reports.

Two things this package is honest about rather than papering over:

* **Replay scope.** Convergence is guaranteed within one server Coach turn — a
  provider retry, a Bedrock→OpenAI fallback and every tool-continuation round
  share the same ``request_id``. A brand-new HTTP request is a new intent. See
  ``identity`` for why claiming more would be a promise the architecture cannot
  keep.
* **Context freshness.** The plan block in the prompt is built before the tools
  run, so a committed mutation makes it stale. The tool result says so
  explicitly instead of re-rendering a block that would be stale again after
  the next call.

Default OFF behind ``AI_COACH_PLAN_MUTATION_TOOLS_ENABLED``: with the flag off
the tools are absent from both provider schemas AND refused at execution.
"""
from .executor import (
    FLAG_KEY,
    MAX_PLAN_OPERATIONS_PER_TURN,
    begin_turn,
    current_confirmation_intent,
    execute_plan_tool,
    invalid_arguments_result,
    plan_changed_this_turn,
    plan_mutation_tools_enabled,
    proposal_created_this_turn,
    published_plan_tool_defs,
    set_turn_history,
)
from .schemas import (
    CANCEL_PENDING_TOOL,
    CONFIRM_TOOL,
    CONFIRMATION_TOOL_DEFS,
    CONFIRMATION_TOOL_NAMES,
    PLAN_MUTATION_TOOL_DEFS,
    PLAN_MUTATION_TOOL_NAMES,
    PLAN_WRITE_TOOL_NAMES,
)

__all__ = [
    "CANCEL_PENDING_TOOL",
    "CONFIRM_TOOL",
    "CONFIRMATION_TOOL_DEFS",
    "CONFIRMATION_TOOL_NAMES",
    "FLAG_KEY",
    "MAX_PLAN_OPERATIONS_PER_TURN",
    "PLAN_MUTATION_TOOL_DEFS",
    "PLAN_MUTATION_TOOL_NAMES",
    "PLAN_WRITE_TOOL_NAMES",
    "begin_turn",
    "current_confirmation_intent",
    "execute_plan_tool",
    "invalid_arguments_result",
    "plan_changed_this_turn",
    "plan_mutation_tools_enabled",
    "proposal_created_this_turn",
    "published_plan_tool_defs",
    "set_turn_history",
]
