"""Native onboarding profile contract (LP-03).

    PUT /api/v1/account/profile   200  {"user": {...}}

The response is `account_projection` — byte-for-byte what the next
`GET /api/v1/account/me` returns — so `profile_complete` in it is the server's
canonical onboarding answer, not an echo of the request.

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`no-store` policy, the ADR 0001 error envelope, the 429/413 handlers, the CSRF
exemption, the `MOBILE_AUTH_ENABLED` gate and the approved-route allow-list
(tests/test_mobile_auth_feature_gate.py) stay single-sourced.

Transport only. Validation, the token vocabulary, the atomic write and the
readiness rule live in `app/services/account_profile.py`, the SAME module the
browser `/setup` calls. This module maps the native wire names onto that
service's domain fields and its typed failures onto `PROFILE_*` codes.

Owner: the verified Bearer principal (`g.mobile_user`) and nothing else. The
body is a closed key set; any other key — `user_id`, `account_id`,
`owner_id`, `email`, `username`, `profile_complete` included — is refused, not
ignored, so a client can never believe it selected or authored something.

Idempotent: repeating a submission updates the same canonical state and never
creates a second session. Nothing submitted is logged.
"""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import (
    _json_object, account_projection, bp, mobile_error,
)
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import account_profile
from app.services.account_profile import (
    OnboardingProfile, ProfileRejected, REASON_TYPE,
)


# Native wire name -> domain field. Units are in the wire names on purpose.
REQUIRED_FIELDS = {
    "weight_kg": account_profile.WEIGHT,
    "height_cm": account_profile.HEIGHT,
    "age": account_profile.AGE,
    "gender": account_profile.GENDER,
    "goal": account_profile.GOAL,
    "fitness_level": account_profile.FITNESS_LEVEL,
    "activity_level": account_profile.CURRENT_ACTIVITY,
}
OPTIONAL_FIELDS = {"target_weight_kg": account_profile.TARGET_WEIGHT}
ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS) | frozenset(OPTIONAL_FIELDS)


def _invalid_request():
    return mobile_error(
        "PROFILE_INVALID_REQUEST", "Invalid profile request.", 400, False)


def _invalid_value():
    return mobile_error(
        "PROFILE_INVALID_VALUE", "A profile value is not supported.", 422,
        False)


def _unavailable():
    return mobile_error(
        "PROFILE_TEMPORARILY_UNAVAILABLE",
        "Profile is temporarily unavailable.", 503, True)


@bp.put("/account/profile")
@require_mobile_auth
def put_account_profile():
    data = _json_object()
    if data is None or not set(data) <= ALLOWED_FIELDS:
        return _invalid_request()
    if any(key not in data for key in REQUIRED_FIELDS):
        return _invalid_request()

    values = {domain: data[wire] for wire, domain in REQUIRED_FIELDS.items()}
    values.update({domain: data.get(wire)
                   for wire, domain in OPTIONAL_FIELDS.items()})
    try:
        profile = OnboardingProfile(**values)
    except ProfileRejected as rejection:
        # Wrong JSON type is a malformed request; a well-typed value the
        # domain does not support (unknown token, outside the canonical
        # range) is a semantic refusal. Neither names the value.
        if rejection.reason == REASON_TYPE:
            return _invalid_request()
        return _invalid_value()

    user = g.mobile_user
    try:
        account_profile.complete_onboarding(user, profile)
        payload = account_projection(user)
    except Exception as error:
        # Not an authentication outcome: answer with a profile-shaped,
        # retryable error instead of the blueprint's auth-flavoured handler,
        # so a client keeps its session and simply retries the idempotent PUT.
        try:
            db.session.rollback()
        except Exception:
            pass
        cause = error.__cause__ if error.__cause__ is not None else error
        current_app.logger.error(
            "mobile_account_profile event=profile_write_failed "
            "error_type=%s request_id=%s",
            type(cause).__name__, current_request_id())
        return _unavailable()
    return jsonify(payload)
