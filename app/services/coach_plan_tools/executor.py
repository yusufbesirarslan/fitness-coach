"""The trusted execution context around one AI-requested plan change.

Everything the model is not allowed to decide is decided here, in this order,
and every step can only refuse — none of them can widen what the call does:

1. **capability** — the rollout flag, fail-closed;
2. **turn identity** — the server's ``request_id``, fail-closed;
3. **arguments → typed command** — the parser, which cannot produce a command
   type it does not name;
4. **operation identity** — server-minted from the turn and the command's own
   semantic fingerprint;
5. **budget** — a per-turn ceiling on how many *distinct* plan operations one
   Coach turn may attempt;
6. **actor / reason** — ``ai_coach`` and no reason, both server-set;
7. **the domain call** — PR1/PR2's boundary, which owns the transaction;
8. **result** — translated into the bounded vocabulary;
9. **settle** — no transaction is left open for the provider call that follows.

``user_id`` arrives as an argument from the Coach dispatcher, which reads it
from the authenticated session. It is never a tool property, never parsed, and
never defaulted (brief §12).

This module is the ONLY thing in the Coach that may call the mutation domain.
``tests/test_plan_mutation_architecture.py`` enforces that structurally: if
``ai_coach`` or ``ai_stream`` ever imports ``plan_mutation`` directly, the
architecture test fails rather than a reviewer having to notice.
"""
from flask import current_app, g

from app.extensions import db
from app.observability import current_request_id
from app.services.plan_mutation import (
    ACTOR_AI_COACH,
    MutationContext,
    PlanMutationError,
    apply_plan_mutation,
    undo_last_change,
)

from . import results
from .identity import (
    TurnIdentityUnavailable,
    mutation_operation_key,
    undo_operation_key,
)
from .parser import ToolArgumentError, build_command
from .schemas import PLAN_MUTATION_TOOL_NAMES, UNDO_TOOL


#: How many DISTINCT plan operations one Coach turn may attempt.
#:
#: Deliberately far below the tool-loop cap (brief §34). The loop cap bounds how
#: many times the model may call *any* tool before the turn is cut off; it was
#: never sized as a limit on durable writes, and a model that misreads one
#: request as five edits would spend it happily. Two is what an honest single
#: message asks for — "swap X for Y and drop Z" — and a request genuinely
#: needing more is a plan redesign, which brief §24 says must not be decomposed
#: into a swarm of mutations.
#:
#: A test pins that this stays strictly below ``ai_coach._COACH_TOOL_LOOP_CAP``.
#: The assertion lives in the test rather than here because importing the Coach
#: from inside its own tool package would invert the dependency this package
#: exists to keep one-directional.
MAX_PLAN_OPERATIONS_PER_TURN = 2

#: Request-scoped attribute holding the operation keys already attempted this
#: turn. On ``g``, so it dies with the request and can never leak between users.
_BUDGET_ATTR = "_coach_plan_operation_keys"


def begin_turn():
    """Reset the per-turn mutation budget.

    Called from the Coach's own turn setup, so blocking and streaming share one
    definition of "a turn" instead of each inventing one. Outside a request
    context there is no turn and nothing to reset — plan tools cannot run there
    either (``current_turn_id`` refuses), so this is a no-op rather than an
    error.
    """
    try:
        setattr(g, _BUDGET_ATTR, [])
    except RuntimeError:
        pass


def _attempted_keys():
    try:
        keys = getattr(g, _BUDGET_ATTR, None)
    except RuntimeError:
        # No request context. The identity check below refuses anyway; return a
        # throwaway list so this function never becomes the thing that raises.
        return []
    if keys is None:
        # A turn that never called begin_turn() still gets a budget: defaulting
        # to "unbounded" would make a missed wiring silently remove the ceiling.
        keys = []
        setattr(g, _BUDGET_ATTR, keys)
    return keys


def _charge_budget(operation_key):
    """Claim budget for ``operation_key``. Returns False when exhausted.

    Re-presenting a key already attempted this turn costs nothing. That is not
    an optimisation — it is the property that makes duplicate delivery safe: if
    a repeat consumed budget, the second delivery of ONE mutation could be
    refused as "too many changes" instead of replaying the first (brief §35),
    and the model would then tell the user their edit was rejected when it had
    in fact been applied.
    """
    keys = _attempted_keys()
    if operation_key in keys:
        return True
    if len(keys) >= MAX_PLAN_OPERATIONS_PER_TURN:
        return False
    keys.append(operation_key)
    return True


#: The rollout flag key. Registered in ``app/feature_flags.py::ROLLOUT_FLAGS``
#: (default OFF) — the canonical registry, not a private ``os.getenv``.
FLAG_KEY = "AI_COACH_PLAN_MUTATION_TOOLS_ENABLED"


def plan_mutation_tools_enabled():
    """Whether the plan-mutation tool surface is active for this request.

    Reads the canonical rollout flag through ``current_app.config``, the same
    path every other flag in this repository uses, so ``/health?deep=1`` and the
    ``[FLAGS]`` boot line report it without a second mechanism. Fail-closed on
    anything unexpected: no app context, a missing key, or a config object that
    raises all mean "we cannot prove this is on", and the safe reading of that
    is OFF (brief §45).
    """
    try:
        return bool(current_app.config.get(FLAG_KEY, False))
    except Exception:
        return False


def _settle_transaction():
    """Leave no transaction open for the provider call that follows.

    The mutation service already commits or rolls back its own work, so in the
    normal case there is nothing here to do. What this catches is a *read* that
    autobegan afterwards — SQLAlchemy opens a transaction on the next statement,
    and a Coach tool returns straight into a blocking provider call that can run
    for the rest of the turn deadline. Holding a database connection across that
    is what the repository's provider discipline forbids (brief §43).

    It rolls back ONLY a provably read-only residual. If anything is pending,
    this walks away and leaves it to the owner of that work: a blanket rollback
    here would be a second, uninvited transaction authority, and the failure it
    would cause — silently discarding another layer's pending write — is far
    worse than the one it would prevent.
    """
    try:
        session = db.session()
        if not session.in_transaction():
            return
        if session.new or session.dirty or session.deleted:
            return
        session.rollback()
    except Exception:
        # Settling is hygiene, not correctness. The mutation has already been
        # decided; failing the tool result over a cleanup problem would report
        # a successful change as an error.
        pass


def _log(name, outcome):
    """One bounded, PII-free line per plan-tool call.

    ``name`` and ``outcome`` both come from closed server-owned vocabularies.
    Nothing else is logged — not the arguments, the plan, the summary, the
    operation key or the user's message (brief §60).
    """
    try:
        current_app.logger.info(
            "[COACH][PLAN_TOOL] request_id=%s tool=%s outcome=%s",
            current_request_id(), name, outcome)
    except Exception:
        pass


def _outcome_of(payload):
    if payload.get("status") == results.STATUS_ERROR:
        return payload.get("error", results.ERROR_INTERNAL)
    return payload.get("status", results.STATUS_ERROR)


def invalid_arguments_result(detail):
    """A bounded refusal for a call that never reached this package.

    The dispatcher owns JSON decoding, so "the provider sent arguments that are
    not JSON" is decided one layer up. It still has to answer in this package's
    vocabulary rather than inventing a shape, and it must NOT fall back to an
    empty object: ``undo`` takes no arguments, so decoding failure collapsed to
    ``{}`` would execute a change nobody asked for (brief §38).
    """
    return results.error_result(results.ERROR_INVALID_ARGUMENTS, detail)


def execute_plan_tool(user_id, name, arguments):
    """Run one plan-mutation tool call and return its bounded result.

    Never raises: a Coach tool result is a message to the model, and an
    exception escaping here would abort a turn in which a mutation may already
    have been committed. Every failure becomes a code from the bounded
    vocabulary instead.
    """
    payload = _execute(user_id, name, arguments)
    _settle_transaction()
    _log(name, _outcome_of(payload))
    return payload


def _execute(user_id, name, arguments):
    if not plan_mutation_tools_enabled():
        # Defence in depth. When the flag is OFF these tools are absent from
        # both provider schemas, so a call can only arrive from a model
        # inventing a name it saw in an earlier turn — or from a future caller
        # that forgot the gate. Either way: refuse, do not mutate.
        return results.error_result(results.ERROR_CAPABILITY_DISABLED)

    if name not in PLAN_MUTATION_TOOL_NAMES:
        return results.error_result(
            results.ERROR_INVALID_ARGUMENTS, "bilinmeyen plan aracı")

    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        # The dispatcher passes the authenticated user. A missing or malformed
        # one is a server defect, and guessing an owner is the one mistake this
        # whole boundary exists to make impossible. ``bool`` is excluded first
        # for the same reason the parser excludes it from ``sets``: it is an
        # ``int`` subclass, so ``True`` would pass this guard and then mutate
        # user 1's plan.
        return results.error_result(results.ERROR_INTERNAL)

    try:
        if name == UNDO_TOOL:
            return _execute_undo(user_id, arguments)
        return _execute_mutation(user_id, name, arguments)
    except TurnIdentityUnavailable:
        return results.error_result(results.ERROR_TURN_IDENTITY_UNAVAILABLE)
    except ToolArgumentError as e:
        # The parser's message is server-authored and describes the argument
        # shape only; it never carries domain state.
        return results.error_result(results.ERROR_INVALID_ARGUMENTS, str(e))
    except PlanMutationError as e:
        # Mapped by exception CLASS, never by message: no domain wording, no
        # SQL and no snapshot reaches the model (brief §27).
        return results.error_result(results.error_code_for(e))
    except Exception as exc:
        # The exception CLASS, not the exception. A traceback is not deliberately
        # audit material, but it carries the exception's own message, and an
        # unexpected failure here is most likely a SQLAlchemy StatementError —
        # whose message embeds the statement and its bound parameters, i.e. the
        # whole plan document. "No plan text in logs" has to be structural
        # (§60); the class name is fixed-cardinality and enough to route a
        # diagnosis alongside the request id.
        failure = type(exc).__name__
        current_app.logger.warning(
            "[COACH][PLAN_TOOL] beklenmeyen hata tool=%s failure=%s",
            name, failure)
        return results.error_result(results.ERROR_INTERNAL)


def _context(operation_key):
    """The audit envelope for one AI-requested change.

    ``actor`` is server-set and is audit metadata, not authorization — nothing
    downstream grants the AI Coach a permission the user does not have; the
    mutation is scoped to the caller's own plan either way (brief §13).

    ``reason`` is deliberately ``None``. The candidates were all worse: the raw
    user message is untrusted input and would put user text in a durable audit
    field, model-authored text is unverified narration of what the model
    *believes* it did, and a fixed string like "AI Coach" says nothing the
    ``actor`` column does not already say (brief §14).
    """
    return MutationContext(
        idempotency_key=operation_key, actor=ACTOR_AI_COACH, reason=None)


def _execute_mutation(user_id, name, arguments):
    command = build_command(name, arguments)
    operation_key = mutation_operation_key(command)
    if not _charge_budget(operation_key):
        return results.error_result(results.ERROR_MUTATION_BUDGET_EXHAUSTED)
    result = apply_plan_mutation(user_id, command, _context(operation_key))
    return results.mutation_result(command, result)


def _execute_undo(user_id, arguments):
    # Undo takes no arguments; anything sent is still refused rather than
    # ignored, because a model that thinks it can pass "which change" needs to
    # learn it cannot (brief §31).
    build_undo_arguments(arguments)
    operation_key = undo_operation_key()
    if not _charge_budget(operation_key):
        return results.error_result(results.ERROR_MUTATION_BUDGET_EXHAUSTED)
    result = undo_last_change(user_id, _context(operation_key))
    return results.undo_result(result)


def build_undo_arguments(arguments):
    """Validate that an undo call carries no arguments."""
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise ToolArgumentError("araç argümanları bir nesne olmalı")
    if arguments:
        raise ToolArgumentError(
            "beklenmeyen alan: " + ", ".join(sorted(arguments)))
    return {}
