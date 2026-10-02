"""Bearer-authenticated native Coach contract (LP-09).

    POST /api/v1/coach/messages
    GET  /api/v1/coach/history

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`Cache-Control: no-store` policy, the 413/429 handlers, the error envelope and
the approved-route gate in tests/test_mobile_auth_feature_gate.py all stay
single-sourced.

A transport over the web Coach's own authority, never a second Coach. The turn
runs through `ai_pipeline.generate_answer` - the same memory window, grounding
(`context_builder`), provider loop, plan tools, moderation and persistence web
`/ask` uses - and is admitted by the same gates in the same order: failure
cooldown, then the premium-aware weekly quota in the SHARED "chat" bucket, then
the provider. What this module adds is only what a Bearer client needs:

  * the owner is `g.mobile_user`; the body is the closed key set {"message"}
    plus (NUTR-PR7) an optional allowlisted `handoff` marker
    (`"nutrition-day"`), so no client-supplied owner, conversation, transcript
    or nutrition fact is ever read;
  * the server owns the history - `client_history` is always `[]`, so the
    pipeline uses the persisted window and never the browser cookie session;
  * a provider failure is a typed 503, never the fallback sentence dressed up
    as a Coach reply; and
  * typed mobile codes for 402/429/503 instead of the web's translated strings.

See docs/MOBILE_COACH.md.
"""
from flask import after_this_request, current_app, g, jsonify, request, session
from flask_limiter.errors import RateLimitExceeded

from app.blueprints.mobile_api import bp, mobile_error
from app.coach_handoff import REVIEW_NUTRITION_DAY, handoff_marker
from app.config import AI_BURST_RATELIMIT, AI_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import ai_recovery, coach_plan_tools, mobile_coach
from app.services.ai_gate import mobile_ai_concurrency_gate
from app.services.ai_pipeline import generate_answer
from app.services.moderation import validate_question
from app.services.premium import (
    FREE_WEEKLY_AI_CHATS, refund_ai_quota, reserve_ai_quota,
)


_REQUEST_KEYS = frozenset({"message"})
# NUTR-PR7: the ONE optional addition — an allowlisted handoff MARKER, never a
# fact. Only the Nutrition review is published natively; the server re-derives
# the PR6 day view for the Bearer owner at send time (app/coach_handoff.py).
_OPTIONAL_KEYS = frozenset({"handoff"})
NATIVE_HANDOFF_KINDS = frozenset({REVIEW_NUTRITION_DAY})


def _owner_key():
    return str(g.mobile_user.id)


def _check_rate_limits():
    """The web `/ask` ceilings (hourly + burst), keyed on the Bearer owner.

    Checked in the view rather than as decorators so a rejection is a typed
    Coach 429 and not the blueprint's auth-flavoured `AUTH_RATE_LIMITED`.
    """
    with limiter.limit(AI_RATELIMIT, key_func=_owner_key):
        with limiter.limit(AI_BURST_RATELIMIT, key_func=_owner_key):
            return


def _discard_cookie_session_writes(response):
    """A Bearer response never carries a browser session cookie.

    The shared Coach domain mirrors some turn state into the Flask cookie
    session for the web widget (a bounded plan-clarification diagnostic, see
    `coach_plan_tools.clarifications`). The authoritative copy lives in
    Redis/process memory, so dropping the mirror costs nothing - but letting it
    through would put a signed cookie holding Coach state on a native response.
    Runs before Flask saves the session; an empty, unmodified session is never
    written.
    """
    session.clear()
    session.modified = False
    return response


def _refund_quota(user):
    try:
        refund_ai_quota(user, "chat")
    except Exception:
        db.session.rollback()
        current_app.logger.error(
            "mobile_coach event=quota_refund_failed request_id=%s",
            current_request_id())


def _with_deferred_summary(response, result):
    deferred = result.get("deferred_summarize") if result else None
    if deferred is not None:
        # Same as web `/ask`: worker-less summarisation runs after the answer.
        response.call_on_close(deferred)
    return response


@bp.post("/coach/messages")
@require_mobile_auth
@mobile_ai_concurrency_gate(
    "COACH_BUSY", "The Coach is busy. Try again shortly.")
def send_coach_message():
    after_this_request(_discard_cookie_session_writes)
    user = g.mobile_user
    user_id = user.id

    try:
        _check_rate_limits()
    except RateLimitExceeded as error:
        return mobile_error(
            "COACH_RATE_LIMITED", "Too many Coach messages.", 429, True,
            retry_after=error.limit.limit.get_expiry())

    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or not _REQUEST_KEYS <= set(data)
            or set(data) - _REQUEST_KEYS - _OPTIONAL_KEYS
            or not isinstance(data["message"], str)):
        return mobile_error(
            "COACH_INVALID_REQUEST", "Invalid request.", 400, False)
    handoff = None
    if "handoff" in data:
        # Refused, not ignored: an unknown/forged marker never reaches the model.
        handoff = handoff_marker(data["handoff"])
        if handoff not in NATIVE_HANDOFF_KINDS:
            return mobile_error(
                "COACH_INVALID_REQUEST", "Invalid request.", 400, False)
    # Same normalisation and the same gate (moderation.validate_question) as
    # web `/ask`, so both surfaces accept exactly the same messages.
    question = data["message"].strip()
    err_key = validate_question(question)
    if err_key == "coach.ask_something":
        return mobile_error(
            "COACH_MESSAGE_EMPTY", "Message is empty.", 400, False)
    if err_key is not None:
        return mobile_error(
            "COACH_MESSAGE_TOO_LONG", "Message is too long.", 400, False)

    # Cooldown BEFORE the quota, as on web: a cooling user spends nothing.
    remaining = ai_recovery.ai_cooldown_remaining(user_id)
    if remaining:
        return mobile_error(
            "COACH_COOLING_DOWN",
            "The Coach is recovering from repeated failures.", 429, True,
            retry_after=remaining)

    quota_enabled = current_app.config.get("AI_CHAT_QUOTA_ENABLED", True)
    if quota_enabled and not reserve_ai_quota(
            user, "chat", FREE_WEEKLY_AI_CHATS):
        return mobile_error(
            "COACH_QUOTA_EXCEEDED",
            "The weekly Coach allowance is used up.", 402, False)

    result = None
    try:
        handoff_kw = {"handoff": handoff} if handoff else {}
        result = generate_answer(
            user_id, question, [], language=user.language, **handoff_kw)
    except Exception as error:
        db.session.rollback()
        # A type name and a request id only - never the message, the prompt,
        # the provider body or the exception text.
        current_app.logger.error(
            "mobile_coach event=reply_failed error_type=%s request_id=%s",
            type(error).__name__, current_request_id())
        ai_recovery.record_ai_failure(user_id)
        if quota_enabled:
            _refund_quota(user)
        return mobile_error(
            "COACH_UNAVAILABLE", "The Coach is temporarily unavailable.",
            503, True)

    if result["is_error_fallback"]:
        # Web `/ask` answers 200 with the friendly fallback sentence and
        # `is_error_fallback: true`. Native gets no fake reply: the same
        # bookkeeping (failure streak, quota refund, turn NOT persisted), then a
        # typed error.
        ai_recovery.record_ai_failure(user_id)
        if quota_enabled:
            db.session.rollback()
            _refund_quota(user)
        current_app.logger.warning(
            "mobile_coach event=reply_fallback request_id=%s",
            current_request_id())
        if (coach_plan_tools.plan_changed_this_turn()
                or coach_plan_tools.proposal_created_this_turn()):
            # A plan tool already committed (or staged a proposal) before the
            # reply failed. Retrying would be a new turn with a new operation
            # identity and could apply the edit twice - the exact reason the
            # web fallback says "no need to ask again". Not retryable.
            response = mobile_error(
                "COACH_REPLY_INCOMPLETE",
                "Your plan was updated or a change is awaiting confirmation, "
                "but the Coach reply could not be completed.", 503, False)
        else:
            response = mobile_error(
                "COACH_UNAVAILABLE", "The Coach is temporarily unavailable.",
                503, True)
        return _with_deferred_summary(response, result)

    ai_recovery.clear_ai_failures(user_id)
    payload = mobile_coach.turn_payload(
        result.get("recorded_turn"), question, result["answer"], user_id,
        current_app.config["SECRET_KEY"])
    return _with_deferred_summary(jsonify(payload), result)


@bp.get("/coach/history")
@require_mobile_auth
def coach_history():
    """The Bearer owner's canonical Coach history window, oldest first.

    No query parameter, body or header beyond the auth boundary is read, so
    there is no owner, conversation or window size to tamper with.
    """
    try:
        payload = mobile_coach.history_payload(
            g.mobile_user.id, current_app.config["SECRET_KEY"])
    except Exception as error:
        # Coach-shaped and retryable; falling through to the blueprint handler
        # would answer AUTH_TEMPORARILY_UNAVAILABLE and make a client drop a
        # good session over a storage blip.
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_coach event=history_read_failed error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
        return mobile_error(
            "COACH_HISTORY_UNAVAILABLE",
            "Coach history is temporarily unavailable.", 503, True)
    return jsonify(payload)
