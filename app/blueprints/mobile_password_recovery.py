"""Native password-recovery contract (LP-02).

    POST /api/v1/auth/password/forgot   202  {"password_reset": {"status": "accepted"}}
    POST /api/v1/auth/password/reset    200  {"password_reset": {"status": "completed"}}

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`no-store` policy, the ADR 0001 error envelope, the 429/413/unhandled handlers,
the CSRF exemption and the approved-route allow-list
(tests/test_mobile_auth_feature_gate.py) stay single-sourced.

These routes are transport only. Identifier normalization, the canonical
password policy, the provider call (inside the shared
`blocking_concurrency_slot`), provider-error classification, the
non-enumeration rule and the post-reset session revocation all live in
`app/services/account_recovery.py`, the SAME module the browser
`/forgot-password` and `/reset-password` call. This module maps that service's
closed `Outcome` vocabulary onto stable `AUTH_*` codes and never reads the
validator's sentence.

Session boundary: neither route issues a credential, a cookie or a session
row. A successful reset revokes every session issued under the old password
(web and native) and ends with an unauthenticated caller, who signs in through
`POST /api/v1/auth/login` like every other account.

Enumeration: every forgot request with a usable identifier answers the same
202 unless OUR capacity is exhausted; reset answers a wrong, expired or
unknown-account code identically. Details: docs/MOBILE_PASSWORD_RECOVERY.md §5.
"""
from flask import current_app, jsonify
from flask_limiter.util import get_remote_address

from app.blueprints.mobile_api import _json_object, bp, mobile_error
from app.config import COGNITO_ENABLED
from app.extensions import limiter, login_throttle_available
from app.mobile_auth_middleware import _safe_message
from app.services import account_recovery
from app.services.account_recovery import Outcome, RecoveryFailure


# Per-IP budgets are the browser routes' budgets. The per-identifier budgets
# add what an IP key alone cannot: a reset-mail flood aimed at one inbox from
# many addresses, and a distributed guesser spreading attempts on one
# account's reset code. They are always stacked on an IP limit, never alone.
FORGOT_IP_LIMIT = "5 per 15 minutes"
FORGOT_IDENTIFIER_LIMIT = "3 per 15 minutes"
RESET_IP_LIMIT = "10 per 15 minutes"
# Charged on wrong/expired codes only. Kept at or below the provider's own
# per-account failure threshold so OUR answer — identical for every account —
# is normally the first wall a guesser meets.
RESET_IDENTIFIER_FAILURE_LIMIT = "5 per 15 minutes"

# Transport bounds checked before any provider work. The password is bounded
# by the canonical policy itself (8–128), exactly as registration is.
MAX_IDENTIFIER_CHARS = 254
MAX_CODE_CHARS = 64

CAPACITY_RETRY_AFTER_SECONDS = 15

_FAILURES = {
    Outcome.FIELDS_REQUIRED: ("AUTH_INVALID_REQUEST", 400, False),
    Outcome.PASSWORD_INVALID: ("AUTH_PASSWORD_POLICY", 400, False),
    # Expired is a distinct internal outcome but one wire code: whether a code
    # was ever issued for this identifier is not something the answer may say.
    Outcome.CODE_INVALID: ("AUTH_VERIFICATION_CODE_INVALID", 400, False),
    Outcome.CODE_EXPIRED: ("AUTH_VERIFICATION_CODE_INVALID", 400, False),
    Outcome.ATTEMPTS_EXHAUSTED: ("AUTH_RATE_LIMITED", 429, True),
    Outcome.THROTTLED: ("AUTH_RATE_LIMITED", 429, True),
    Outcome.PROVIDER_UNAVAILABLE: ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True),
    Outcome.CAPACITY_EXHAUSTED: ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True),
    # The password changed but open sessions could not be revoked. Replaying
    # this request cannot help (the code is spent), so it is not retryable: the
    # client restarts recovery, whose next success revokes them.
    Outcome.SESSIONS_NOT_REVOKED: ("AUTH_TEMPORARILY_UNAVAILABLE", 503, False),
}

_CODE_FAILURES = frozenset({"AUTH_VERIFICATION_CODE_INVALID"})


def _invalid_request():
    return mobile_error(
        "AUTH_INVALID_REQUEST", _safe_message("AUTH_INVALID_REQUEST"), 400,
        False)


def _unavailable():
    return mobile_error(
        "AUTH_TEMPORARILY_UNAVAILABLE",
        _safe_message("AUTH_TEMPORARILY_UNAVAILABLE"), 503, True)


def _failure_response(failure):
    code, status, retryable = _FAILURES.get(
        failure.outcome, ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True))
    retry_after = (CAPACITY_RETRY_AFTER_SECONDS
                   if failure.outcome == Outcome.CAPACITY_EXHAUSTED else None)
    return mobile_error(
        code, _safe_message(code), status, retryable, retry_after=retry_after)


def _submitted_identifier_key(prefix):
    data = _json_object() or {}
    submitted = data.get("identifier")
    identifier = submitted.strip().lower() if isinstance(submitted, str) else ""
    if not identifier or len(identifier) > MAX_IDENTIFIER_CHARS:
        return get_remote_address()
    return f"{prefix}:{identifier}"


def _forgot_identifier_key():
    return _submitted_identifier_key("mobile-forgot-identifier")


def _reset_identifier_key():
    return _submitted_identifier_key("mobile-reset-identifier")


def _is_code_failure(response):
    if response.status_code != 400:
        return False
    body = response.get_json(silent=True) or {}
    return (body.get("error") or {}).get("code") in _CODE_FAILURES


def _bounded_string(data, key, max_chars):
    value = data.get(key)
    if not isinstance(value, str) or len(value) > max_chars:
        return None
    return value


@bp.post("/auth/password/forgot")
@limiter.limit(FORGOT_IP_LIMIT, key_func=get_remote_address)
@limiter.limit(FORGOT_IDENTIFIER_LIMIT, key_func=_forgot_identifier_key)
def password_forgot():
    data = _json_object()
    if data is None:
        return _invalid_request()
    identifier = _bounded_string(data, "identifier", MAX_IDENTIFIER_CHARS)
    if identifier is None:
        return _invalid_request()
    if not COGNITO_ENABLED:
        return _unavailable()
    try:
        account_recovery.request_password_reset(identifier)
    except RecoveryFailure as failure:
        return _failure_response(failure)
    response = jsonify({"password_reset": {"status": "accepted"}})
    response.status_code = 202
    return response


@bp.post("/auth/password/reset")
@limiter.limit(RESET_IP_LIMIT, key_func=get_remote_address)
@limiter.limit(
    RESET_IDENTIFIER_FAILURE_LIMIT, key_func=_reset_identifier_key,
    deduct_when=_is_code_failure)
def password_reset():
    data = _json_object()
    if data is None:
        return _invalid_request()
    identifier = _bounded_string(data, "identifier", MAX_IDENTIFIER_CHARS)
    code = _bounded_string(data, "code", MAX_CODE_CHARS)
    new_password = data.get("new_password")
    if identifier is None or code is None or not isinstance(new_password, str):
        return _invalid_request()
    # Code guessing is a credential-guessing surface: like login and
    # verification, refuse it while the distributed throttle store is down.
    if (current_app.config.get("LOGIN_FAIL_CLOSED", True)
            and not login_throttle_available()):
        return _unavailable()
    if not COGNITO_ENABLED:
        return _unavailable()
    try:
        account_recovery.reset_password(identifier, code, new_password)
    except RecoveryFailure as failure:
        return _failure_response(failure)
    return jsonify({"password_reset": {"status": "completed"}})
