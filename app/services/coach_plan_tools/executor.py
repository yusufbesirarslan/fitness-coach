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
from app.services.coach_plan_policy import (
    APPLY_NOW,
    CANCEL,
    CONFIRM,
    NONE,
    REFUSE_UNSUPPORTED,
    REQUIRE_CONFIRMATION,
    UNDO_LAST_CHANGE,
    decide_plan_mutation_impact,
    parse_confirmation_intent,
)
from app.services.plan_confirmation import (
    STATUS_APPLIED,
    STATUS_PENDING,
    STATUS_STALE,
    PendingConfirmationConflict,
    cancel_pending,
    create_or_reuse_pending,
    get_pending,
    lock_proposal,
    lock_latest_proposal,
    mark_applied,
    mark_stale,
)
from app.services.plan_mutation import (
    ACTOR_AI_COACH,
    MutationContext,
    PlanMutationResult,
    PlanMutationError,
    apply_plan_mutation,
    undo_last_change,
)
from app.services.plan_mutation.fingerprint import UNDO_COMMAND_TYPE
from app.services.plan_mutation.journal import (
    KIND_MUTATION,
    KIND_UNDO,
    OUTCOME_APPLIED,
    find_by_key,
)
from app.services.today_facts import get_active_plan

from . import results
from .identity import (
    TurnIdentityUnavailable,
    confirmation_operation_key,
    mutation_operation_key,
    undo_operation_key,
)
from .parser import ToolArgumentError, build_command
from .proposals import (
    binding_matches,
    decode_command,
    encode_command,
    plan_binding,
    preview_command,
    session_impact_facts,
)
from .schemas import (
    CANCEL_PENDING_TOOL,
    CONFIRM_TOOL,
    CONFIRMATION_TOOL_DEFS,
    CONFIRMATION_TOOL_NAMES,
    PLAN_MUTATION_TOOL_DEFS,
    PLAN_MUTATION_TOOL_NAMES,
    PLAN_WRITE_TOOL_NAMES,
    UNDO_TOOL,
)


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

#: Request-scoped flag: did this turn move persisted plan state?
#:
#: Read by the Coach when a turn degrades to its soft error text. That text
#: ("I couldn't complete that, could you try again?") is a lie once a mutation
#: has committed, and acting on it — asking again in a NEW request, with a new
#: turn identity — applies the same edit a second time. Same placement and
#: lifetime as the budget: on ``g``, so it dies with the request.
_CHANGED_ATTR = "_coach_plan_change_applied"
_PROPOSAL_ATTR = "_coach_plan_proposal_created"
_NEW_PROPOSAL_ATTR = "_coach_plan_new_proposal_created"
_INTENT_ATTR = "_coach_plan_confirmation_intent"


_USER_MSG_ATTR = "_coach_plan_user_message"
_HISTORY_ATTR = "_coach_plan_history"
_USER_ID_ATTR = "_coach_plan_user_id"


def begin_turn(user_message="", history=None, user_id=None):
    """Reset the per-turn mutation budget, markers and confirmation intent.

    Called from the Coach's own turn setup, so blocking and streaming share one
    definition of "a turn" instead of each inventing one. Outside a request
    context there is no turn and nothing to reset — plan tools cannot run there
    either (``current_turn_id`` refuses), so this is a no-op rather than an
    error.

    ``user_message`` is the raw current user turn. Confirmation authority and
    prescription/workout grounding are derived from it here, not from the
    model. A leftover clarification is discarded when this turn names a new
    exercise; ``yes`` keeps the server-owned record so it can be accepted.
    """
    try:
        setattr(g, _BUDGET_ATTR, [])
        setattr(g, _CHANGED_ATTR, False)
        setattr(g, _PROPOSAL_ATTR, False)
        setattr(g, _NEW_PROPOSAL_ATTR, False)
        setattr(g, _INTENT_ATTR, parse_confirmation_intent(user_message))
        setattr(g, _USER_MSG_ATTR, user_message or "")
        setattr(g, _HISTORY_ATTR, list(history or ()))
        setattr(g, _USER_ID_ATTR, user_id)
        from .grounding import refresh_clarification_for_turn
        refresh_clarification_for_turn(user_message, user_id=user_id)
    except RuntimeError:
        pass


def set_turn_history(history):
    """Attach conversation history after the Coach has resolved it."""
    try:
        setattr(g, _HISTORY_ATTR, list(history or ()))
    except RuntimeError:
        pass


def current_confirmation_intent():
    """CONFIRM / CANCEL / NONE for this turn. Fail-safe NONE."""
    try:
        return getattr(g, _INTENT_ATTR, None) or NONE
    except RuntimeError:
        return NONE


def proposal_created_this_turn():
    """Whether this turn persisted a confirmation-required proposal."""
    try:
        return bool(getattr(g, _PROPOSAL_ATTR, False))
    except RuntimeError:
        return False


def new_proposal_created_this_turn():
    """Whether this turn inserted a NEW pending proposal (not a reuse).

    A confirm-form user message is not license to stage a confirmation-required
    mutation and then apply it in the same turn: the user has not yet seen the
    durable proposal on a later request.
    """
    try:
        return bool(getattr(g, _NEW_PROPOSAL_ATTR, False))
    except RuntimeError:
        return False


def published_plan_tool_defs(user_id=None):
    """The plan-write tools published to the provider for this turn.

    Fail-closed around a pending proposal: CONFIRM exposes only confirm,
    CANCEL only cancel, NONE none of the plan-write family. No pending
    restores the original six command tools, except a bare confirm/cancel
    turn with nothing pending publishes none of them — a later "yes"
    must not start a new mutation. Confirmation tools are never
    published when there is nothing to confirm.
    """
    if not plan_mutation_tools_enabled():
        return ()
    try:
        pending = get_pending(user_id) if user_id else None
    except Exception:
        return ()
    if pending is None:
        intent = current_confirmation_intent()
        if intent in (CONFIRM, CANCEL):
            return ()
        return PLAN_MUTATION_TOOL_DEFS
    if new_proposal_created_this_turn():
        # Staged this turn: the user has not seen it yet. Confirm/cancel
        # belong to a later authenticated turn.
        return ()
    intent = current_confirmation_intent()
    if intent == CONFIRM:
        return (CONFIRMATION_TOOL_DEFS[0],)
    if intent == CANCEL:
        return (CONFIRMATION_TOOL_DEFS[1],)
    return ()


def plan_changed_this_turn():
    """Whether a plan tool moved persisted state during this turn.

    ``replayed`` counts: it means this turn's own key already applied the
    change, so the plan on disk differs from the one the turn started with.
    ``no_op`` does not — nothing moved — and neither does any error.

    Fail-safe FALSE. A wrong ``True`` would tell a user their plan changed when
    it did not, which is the worse of the two mistakes; the caller only ever
    uses this to choose between two honest sentences.
    """
    try:
        return bool(getattr(g, _CHANGED_ATTR, False))
    except RuntimeError:
        return False


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
    _mark_plan_changed(payload)
    _mark_proposal_created(payload)
    _log(name, _outcome_of(payload))
    return payload


def _mark_plan_changed(payload):
    """Record that persisted plan state moved, for the degraded-turn wording.

    Derived from the bounded result rather than from "the domain call did not
    raise": ``no_op`` also returns without raising and must NOT count.
    """
    if payload.get("status") not in (results.STATUS_APPLIED,
                                     results.STATUS_REPLAYED):
        return
    try:
        setattr(g, _CHANGED_ATTR, True)
        from .clarifications import clear
        clear()
    except RuntimeError:
        pass


def _mark_proposal_created(payload):
    if payload.get("status") != results.STATUS_CONFIRMATION_REQUIRED:
        return
    try:
        setattr(g, _PROPOSAL_ATTR, True)
        from .clarifications import clear
        clear()
    except RuntimeError:
        pass


def _execute(user_id, name, arguments):
    if not plan_mutation_tools_enabled():
        # Defence in depth. When the flag is OFF these tools are absent from
        # both provider schemas, so a call can only arrive from a model
        # inventing a name it saw in an earlier turn — or from a future caller
        # that forgot the gate. Either way: refuse, do not mutate.
        return results.error_result(results.ERROR_CAPABILITY_DISABLED)

    if name not in PLAN_WRITE_TOOL_NAMES:
        return results.error_result(
            results.ERROR_INVALID_ARGUMENTS, "bilinmeyen plan aracı")

    try:
        if getattr(g, _USER_ID_ATTR, None) is None:
            setattr(g, _USER_ID_ATTR, user_id)
    except RuntimeError:
        pass

    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        # The dispatcher passes the authenticated user. A missing or malformed
        # one is a server defect, and guessing an owner is the one mistake this
        # whole boundary exists to make impossible. ``bool`` is excluded first
        # for the same reason the parser excludes it from ``sets``: it is an
        # ``int`` subclass, so ``True`` would pass this guard and then mutate
        # user 1's plan.
        return results.error_result(results.ERROR_INTERNAL)

    try:
        if name in CONFIRMATION_TOOL_NAMES:
            return _execute_confirmation_tool(user_id, name, arguments)
        # BEFORE the parser: a new mutation request must invalidate an
        # incompatible pending clarification even when the new request is about
        # to be refused for a missing field. Otherwise the failure of one
        # request leaves the previous one armed for the next "Monday"/"yes".
        from .grounding import supersede_stale_clarification
        supersede_stale_clarification(user_id, name, arguments)
        pending = get_pending(user_id)
        if pending is not None:
            return _reuse_or_block_pending(pending, name, arguments)
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
    from .grounding import ground_command
    grounded = ground_command(user_id, command)
    if not grounded.ready:
        return grounded.result
    command = grounded.command
    plan, _document, changed = preview_command(user_id, command)
    if not changed:
        return _apply_typed(user_id, command)
    facts = session_impact_facts(user_id, command)
    decision = decide_plan_mutation_impact(command, facts)
    if decision.verdict == REFUSE_UNSUPPORTED:
        return results.error_result(results.ERROR_UNSUPPORTED_IMPACT)
    if decision.verdict == REQUIRE_CONFIRMATION:
        return _stage_proposal(user_id, plan, command, decision)
    if decision.verdict != APPLY_NOW:
        return results.error_result(results.ERROR_UNSUPPORTED_IMPACT)
    return _apply_typed(user_id, command)


def _execute_undo(user_id, arguments):
    # Undo takes no arguments; anything sent is still refused rather than
    # ignored, because a model that thinks it can pass "which change" needs to
    # learn it cannot (brief §31).
    build_undo_arguments(arguments)
    facts = session_impact_facts(user_id, UNDO_LAST_CHANGE)
    decision = decide_plan_mutation_impact(UNDO_LAST_CHANGE, facts)
    if decision.verdict == REFUSE_UNSUPPORTED:
        return results.error_result(results.ERROR_UNSUPPORTED_IMPACT)
    if decision.verdict == REQUIRE_CONFIRMATION:
        plan = get_active_plan(user_id)
        from app.services.plan_mutation.errors import PlanNotFound
        from app.services.plan_mutation.journal import latest_reversible
        if plan is None:
            raise PlanNotFound("no active training plan")
        if latest_reversible(user_id, plan.lineage_id) is None:
            return _apply_undo(user_id)
        return _stage_proposal(user_id, plan, UNDO_LAST_CHANGE, decision)
    return _apply_undo(user_id)


def _apply_typed(user_id, command):
    operation_key = mutation_operation_key(command)
    if not _charge_budget(operation_key):
        return results.error_result(results.ERROR_MUTATION_BUDGET_EXHAUSTED)
    result = apply_plan_mutation(user_id, command, _context(operation_key))
    return results.mutation_result(command, result)


def _apply_undo(user_id):
    operation_key = undo_operation_key()
    if not _charge_budget(operation_key):
        return results.error_result(results.ERROR_MUTATION_BUDGET_EXHAUSTED)
    result = undo_last_change(user_id, _context(operation_key))
    return results.undo_result(result)


def _reuse_or_block_pending(pending, name, arguments):
    """Same still-valid command reuses the proposal; anything else is blocked."""
    if name == UNDO_TOOL:
        build_undo_arguments(arguments)
        command = UNDO_LAST_CHANGE
    else:
        command = build_command(name, arguments)
    _kind, _payload, fingerprint = encode_command(command)
    if pending.command_fingerprint == fingerprint:
        return results.confirmation_required_result(
            command, tuple(pending.reason_codes or ()))
    return results.error_result(results.ERROR_PENDING_CONFIRMATION_EXISTS)


def _stage_proposal(user_id, plan, command, decision):
    kind, payload, fingerprint = encode_command(command)
    binding = plan_binding(plan)
    try:
        _row, reused = create_or_reuse_pending(
            user_id,
            lineage_id=binding.lineage_id,
            mutation_version=binding.mutation_version,
            snapshot_fingerprint=binding.snapshot_fingerprint,
            command_type=kind,
            command_payload=payload,
            command_fingerprint=fingerprint,
            reason_codes=decision.reason_codes,
            summary=results.confirmation_required_result(
                command, decision.reason_codes)["summary"],
        )
    except PendingConfirmationConflict:
        return results.error_result(results.ERROR_PENDING_CONFIRMATION_EXISTS)
    if not reused:
        try:
            setattr(g, _NEW_PROPOSAL_ATTR, True)
        except RuntimeError:
            pass
    return results.confirmation_required_result(command, decision.reason_codes)


def _execute_confirmation_tool(user_id, name, arguments):
    build_undo_arguments(arguments)
    if name == CONFIRM_TOOL:
        return _confirm_pending(user_id)
    if name == CANCEL_PENDING_TOOL:
        return _cancel_pending(user_id)
    return results.error_result(
        results.ERROR_INVALID_ARGUMENTS, "bilinmeyen plan aracı")


def _confirm_pending(user_id):
    """Apply a pending proposal, serialized on the proposal row.

    Lock order: the proposal row, then PlanMutationService's plan row.
    The proposal lock is released when PlanMutationService commits — that
    commit also inserts the journal row, so a waiter re-checks the journal
    under its own proposal lock and cannot claim a still-pending cancel.
    """
    if current_confirmation_intent() != CONFIRM:
        return results.error_result(results.ERROR_CONFIRMATION_NOT_AUTHORIZED)
    if new_proposal_created_this_turn():
        return results.error_result(results.ERROR_CONFIRMATION_NOT_AUTHORIZED)
    pending = get_pending(user_id)
    if pending is None:
        return results.error_result(results.ERROR_NO_PENDING_CONFIRMATION)
    row = lock_proposal(user_id, pending.public_id)
    if row is None:
        return results.error_result(results.ERROR_NO_PENDING_CONFIRMATION)
    key = confirmation_operation_key(row.public_id)
    already = find_by_key(user_id, key)
    if row.status != STATUS_PENDING:
        if (row.status == STATUS_APPLIED
                and already is not None
                and _record_matches_proposal(row, already)
                and _record_matches_current_plan(user_id, already)):
            return _resolved_replay_result(row, already)
        db.session.rollback()
        if row.status == STATUS_STALE:
            return results.error_result(results.ERROR_PENDING_CONFIRMATION_STALE)
        return results.error_result(results.ERROR_NO_PENDING_CONFIRMATION)
    plan = get_active_plan(user_id)
    if already is None and (
            plan is None
            or plan.lineage_id != row.plan_lineage_id
            or not binding_matches(row, plan)):
        mark_stale(user_id, row.public_id)
        return results.error_result(results.ERROR_PENDING_CONFIRMATION_STALE)
    if not _charge_budget(key):
        db.session.rollback()
        return results.error_result(results.ERROR_MUTATION_BUDGET_EXHAUSTED)
    return _apply_confirmed(user_id, row)


def _record_matches_current_plan(user_id, record):
    """Whether replay still describes the owner's canonical plan exactly."""
    plan = get_active_plan(user_id)
    if plan is None:
        return False
    binding = plan_binding(plan)
    return (
        binding.lineage_id == record.plan_lineage_id
        and binding.mutation_version == record.after_version
        and binding.snapshot_fingerprint == record.after_fingerprint
    )


def _record_matches_proposal(proposal, record):
    """Whether the journal row is exact durable evidence for this proposal."""
    expected_kind = (
        KIND_UNDO
        if proposal.command_type == UNDO_COMMAND_TYPE
        else KIND_MUTATION
    )
    return (
        record.operation_kind == expected_kind
        and record.command_type == proposal.command_type
        and record.command_fingerprint == proposal.command_fingerprint
        and record.outcome == OUTCOME_APPLIED
        and record.plan_lineage_id == proposal.plan_lineage_id
        and record.before_version == proposal.base_mutation_version
        and record.after_version == proposal.base_mutation_version + 1
        and record.before_fingerprint == proposal.base_snapshot_fingerprint
    )


def _resolved_replay_result(proposal, record):
    """Build a replay response from journal evidence without mutation calls."""
    command = decode_command(proposal.command_type, proposal.command_payload)
    replayed = PlanMutationResult(
        outcome=record.outcome,
        plan=None,
        plan_version=record.after_version,
        mutation_id=record.public_id,
        replayed=True,
    )
    db.session.rollback()
    if command is UNDO_LAST_CHANGE:
        return results.undo_result(replayed)
    return results.mutation_result(command, replayed)


def _cancel_pending(user_id):
    """Cancel a pending proposal, or converge if its mutation already committed.

    Same proposal-row lock as confirm. Linearization is the status transition
    (PENDING → CANCELLED) or the journal insert from PlanMutationService,
    whichever commits first. A cancel that observes an already-applied
    mutation must not claim the plan was unchanged.
    """
    if current_confirmation_intent() != CANCEL:
        return results.error_result(results.ERROR_CONFIRMATION_NOT_AUTHORIZED)
    row = lock_latest_proposal(user_id)
    if row is None:
        return results.cancelled_pending_result()
    key = confirmation_operation_key(row.public_id)
    already = find_by_key(user_id, key)
    if already is not None:
        if (_record_matches_proposal(row, already)
                and _record_matches_current_plan(user_id, already)):
            if row.status == STATUS_PENDING:
                # Crash window: reconcile and finish proposal bookkeeping.
                return _apply_confirmed(user_id, row)
            if row.status == STATUS_APPLIED:
                # Historical reconciliation is evidence-only. A resolved
                # proposal is never executable, even through idempotent PMS.
                return _resolved_replay_result(row, already)
            db.session.rollback()
            return results.cancelled_pending_result()
        if row.status == STATUS_PENDING:
            mark_applied(user_id, row.public_id)
        else:
            db.session.rollback()
        return results.cancelled_pending_result()
    if row.status != STATUS_PENDING:
        db.session.rollback()
        return results.cancelled_pending_result()
    cancel_pending(user_id)
    return results.cancelled_pending_result()


def _apply_confirmed(user_id, pending):
    """Apply or replay the stored command, then mark the proposal APPLIED."""
    key = confirmation_operation_key(pending.public_id)
    command = decode_command(pending.command_type, pending.command_payload)
    if command is UNDO_LAST_CHANGE:
        result = undo_last_change(user_id, _context(key))
        payload = results.undo_result(result)
    else:
        result = apply_plan_mutation(user_id, command, _context(key))
        payload = results.mutation_result(command, result)
    mark_applied(user_id, pending.public_id)
    return payload


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
