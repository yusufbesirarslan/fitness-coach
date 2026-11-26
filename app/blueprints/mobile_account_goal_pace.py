"""Canonical daily calorie target read and goal-pace edit (PD1, #547).

    GET /api/v1/account/calorie-target   200  {"contract_version": 1,
                                                "calorie_target": {...}}
    PUT /api/v1/account/goal-pace        200  same body as the GET

`calorie_target` is `account_profile.calorie_target_view` verbatim: the
stored canonical `UserSession.target_calories` (the value Nutrition, Today
and Coach read), why it is that value, and the target each pace of the
stored goal would give. No value is derived, defaulted or rounded here, and
no client ever computes a target: it shows these numbers.

The PUT body is the closed key set `{"goal_pace": "<token>"}`. It changes the
pace alone and re-derives the target from the stored profile in one
transaction (`account_profile.set_goal_pace`); repeating it is a no-op. Its
answer is the read that follows, so a client never shows a target the server
did not store.

    400 PROFILE_INVALID_REQUEST   not a JSON object, other keys, wrong type
    409 PROFILE_INCOMPLETE        no complete adult profile to apply it to
    422 PROFILE_INVALID_VALUE     unknown pace, a pace of another goal, minor
    503 PROFILE_TEMPORARILY_UNAVAILABLE   storage fault; retry is safe

Attached to the existing `mobile_api` blueprint: same `/api/v1` surface,
gate, envelope, no-store and 429 handler, and the approved-route allow-list
(tests/test_mobile_auth_feature_gate.py). Neither path is a health-boundary
safe endpoint, so both need the owner's health-data consent like the profile.
Owner: the verified Bearer principal only. Nothing submitted is logged.
"""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import _json_object, bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import account_profile
from app.services.account_eligibility import EligibilityDenied
from app.services.account_profile import (
    ProfileIncomplete, ProfileRejected, REASON_TYPE,
)

CONTRACT_VERSION = 1
BODY_FIELDS = frozenset({"goal_pace"})


def _invalid_request():
    return mobile_error(
        "PROFILE_INVALID_REQUEST", "Invalid goal pace request.", 400, False)


def _invalid_value():
    return mobile_error(
        "PROFILE_INVALID_VALUE", "This goal pace is not available.", 422,
        False)


def _incomplete():
    return mobile_error(
        "PROFILE_INCOMPLETE", "Complete your profile first.", 409, False)


def _unavailable():
    return mobile_error(
        "PROFILE_TEMPORARILY_UNAVAILABLE",
        "Profile is temporarily unavailable.", 503, True)


def _rollback():
    try:
        db.session.rollback()
    except Exception:
        pass


def _log_failure(event, error):
    cause = error.__cause__ if error.__cause__ is not None else error
    current_app.logger.error(
        "mobile_account_goal_pace event=%s error_type=%s request_id=%s",
        event, type(cause).__name__, current_request_id())


def _body(user):
    return {"contract_version": CONTRACT_VERSION,
            "calorie_target": account_profile.calorie_target_view(user)}


@bp.get("/account/calorie-target")
@require_mobile_auth
def get_calorie_target():
    try:
        payload = _body(g.mobile_user)
    except Exception as error:
        _rollback()
        _log_failure("calorie_target_read_failed", error)
        return _unavailable()
    return jsonify(payload)


@bp.put("/account/goal-pace")
@require_mobile_auth
def put_goal_pace():
    data = _json_object()
    if data is None or set(data) != BODY_FIELDS:
        return _invalid_request()
    user = g.mobile_user
    try:
        account_profile.set_goal_pace(user, data["goal_pace"])
        payload = _body(user)
    except ProfileRejected as rejection:
        _rollback()
        if rejection.reason == REASON_TYPE:
            return _invalid_request()
        return _invalid_value()
    except ProfileIncomplete:
        _rollback()
        return _incomplete()
    except EligibilityDenied:
        _rollback()
        raise
    except Exception as error:
        _rollback()
        _log_failure("goal_pace_write_failed", error)
        return _unavailable()
    return jsonify(payload)
