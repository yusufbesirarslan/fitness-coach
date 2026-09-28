"""Native registration and email-verification contract (LP-01).

    POST /api/v1/auth/register        201  {"registration": {...}}
    POST /api/v1/auth/verify          200  {"verification": {...}}
    POST /api/v1/auth/verify/resend   202  {"verification_resend": {...}}

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`no-store` policy, the ADR 0001 error envelope, the 429/413/unhandled handlers,
the CSRF exemption and the approved-route allow-list
(tests/test_mobile_auth_feature_gate.py) stay single-sourced.

These routes are transport only. Normalization, validation, the provider call
(inside the shared `blocking_concurrency_slot`), provider-error classification
and the local account row all live in `app/services/account_registration.py`,
the SAME module the browser `/register`, `/verify` and `/verify/resend` call.
This module maps that service's closed `Outcome` vocabulary onto stable
`AUTH_*` codes and never reads the provider's own message.

Session boundary: none of these routes issues a credential, a cookie or a
session row. A verified account signs in through `POST /api/v1/auth/login`
like every other account.

Enumeration: a taken username and a taken e-mail answer identically; a wrong,
expired, unknown-user or already-confirmed verification answers identically;
every resend for a well-formed username answers the same 202 unless the
provider or our capacity is genuinely unavailable. Details:
docs/MOBILE_REGISTRATION.md §5.
"""
from flask import current_app, jsonify
from flask_limiter.util import get_remote_address

from app.blueprints.mobile_api import _json_object, bp, mobile_error
from app.config import COGNITO_ENABLED
from app.extensions import limiter, login_throttle_available
from app.i18n import AVAILABLE_LOCALES
from app.mobile_auth_middleware import _safe_message
from app.services import account_registration
from app.services.account_registration import Outcome, RegistrationFailure


# Per-IP budgets are the browser routes' budgets. The per-username budgets add
# what an IP key alone cannot: a distributed guesser spreading attempts on one
# account's code across many addresses, and a resend flood aimed at one inbox.
REGISTER_IP_LIMIT = "5 per hour"
VERIFY_IP_LIMIT = "10 per 15 minutes"
VERIFY_USERNAME_LIMIT = "10 per 15 minutes"
RESEND_IP_LIMIT = "3 per 15 minutes"
RESEND_USERNAME_LIMIT = "3 per 15 minutes"

# Transport bounds checked before any provider work. Register fields are
# bounded by the canonical validators themselves.
MAX_USERNAME_CHARS = 128
MAX_CODE_CHARS = 64

CAPACITY_RETRY_AFTER_SECONDS = 15

_FAILURES = {
    Outcome.FIELDS_REQUIRED: ("AUTH_INVALID_REQUEST", 400, False),
    Outcome.INPUT_REJECTED: ("AUTH_INVALID_REQUEST", 400, False),
    Outcome.USERNAME_INVALID: ("AUTH_USERNAME_INVALID", 400, False),
    Outcome.EMAIL_INVALID: ("AUTH_EMAIL_INVALID", 400, False),
    Outcome.PASSWORD_INVALID: ("AUTH_PASSWORD_POLICY", 400, False),
    Outcome.IDENTITY_UNAVAILABLE: ("AUTH_IDENTITY_UNAVAILABLE", 409, False),
    # Expired is a distinct internal outcome but one wire code: whether a code
    # was ever issued for this username is not something the answer may say.
    Outcome.CODE_INVALID: ("AUTH_VERIFICATION_CODE_INVALID", 400, False),
    Outcome.CODE_EXPIRED: ("AUTH_VERIFICATION_CODE_INVALID", 400, False),
    Outcome.THROTTLED: ("AUTH_RATE_LIMITED", 429, True),
    Outcome.PROVIDER_UNAVAILABLE: ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True),
    Outcome.CAPACITY_EXHAUSTED: ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True),
    Outcome.STORAGE_FAILED: ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True),
}

# Resend outcomes that are facts about one account. They answer exactly like a
# successful resend, so the endpoint is not an existence/confirmation oracle.
_RESEND_ACCEPTED_OUTCOMES = frozenset({Outcome.NOT_PENDING, Outcome.THROTTLED})


def _invalid_request():
    return mobile_error(
        "AUTH_INVALID_REQUEST", _safe_message("AUTH_INVALID_REQUEST"), 400,
        False)


def _unavailable(retry_after=None):
    return mobile_error(
        "AUTH_TEMPORARILY_UNAVAILABLE",
        _safe_message("AUTH_TEMPORARILY_UNAVAILABLE"), 503, True,
        retry_after=retry_after)


def _failure_response(failure):
    code, status, retryable = _FAILURES.get(
        failure.outcome, ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True))
    retry_after = (CAPACITY_RETRY_AFTER_SECONDS
                   if failure.outcome == Outcome.CAPACITY_EXHAUSTED else None)
    return mobile_error(
        code, _safe_message(code), status, retryable, retry_after=retry_after)


def _submitted_username_key(prefix):
    data = _json_object() or {}
    submitted = data.get("username")
    username = submitted.strip().lower() if isinstance(submitted, str) else ""
    if not username or len(username) > MAX_USERNAME_CHARS:
        return get_remote_address()
    return f"{prefix}:{username}"


def _verify_username_key():
    return _submitted_username_key("mobile-verify-user")


def _resend_username_key():
    return _submitted_username_key("mobile-resend-user")


def _bounded_string(data, key, max_chars):
    value = data.get(key)
    if not isinstance(value, str) or len(value) > max_chars:
        return None
    return value


@bp.post("/auth/register")
@limiter.limit(REGISTER_IP_LIMIT, key_func=get_remote_address)
def register():
    data = _json_object()
    if data is None:
        return _invalid_request()
    username, email, password = (
        data.get("username"), data.get("email"), data.get("password"))
    if not all(isinstance(value, str) for value in (username, email, password)):
        return _invalid_request()
    language = data.get("language")
    if language is not None and language not in AVAILABLE_LOCALES:
        return _invalid_request()
    if not COGNITO_ENABLED:
        return _unavailable()
    try:
        account = account_registration.register_account(
            username, email, password, language=language)
    except RegistrationFailure as failure:
        return _failure_response(failure)
    response = jsonify({"registration": {
        "status": "verification_required",
        "username": account.username,
    }})
    response.status_code = 201
    return response


@bp.post("/auth/verify")
@limiter.limit(VERIFY_IP_LIMIT, key_func=get_remote_address)
@limiter.limit(
    VERIFY_USERNAME_LIMIT, key_func=_verify_username_key,
    deduct_when=lambda response: response.status_code == 400)
def verify():
    data = _json_object()
    if data is None:
        return _invalid_request()
    username = _bounded_string(data, "username", MAX_USERNAME_CHARS)
    code = _bounded_string(data, "code", MAX_CODE_CHARS)
    if username is None or code is None:
        return _invalid_request()
    # Code guessing is a credential-guessing surface: like login, refuse it
    # while the distributed throttle store is unreachable.
    if (current_app.config.get("LOGIN_FAIL_CLOSED", True)
            and not login_throttle_available()):
        return _unavailable()
    if not COGNITO_ENABLED:
        return _unavailable()
    try:
        account_registration.confirm_account(username, code)
    except RegistrationFailure as failure:
        return _failure_response(failure)
    return jsonify({"verification": {"status": "verified"}})


@bp.post("/auth/verify/resend")
@limiter.limit(RESEND_IP_LIMIT, key_func=get_remote_address)
@limiter.limit(RESEND_USERNAME_LIMIT, key_func=_resend_username_key)
def verify_resend():
    data = _json_object()
    if data is None:
        return _invalid_request()
    username = _bounded_string(data, "username", MAX_USERNAME_CHARS)
    if username is None:
        return _invalid_request()
    if not COGNITO_ENABLED:
        return _unavailable()
    try:
        account_registration.resend_confirmation(username)
    except RegistrationFailure as failure:
        if failure.outcome not in _RESEND_ACCEPTED_OUTCOMES:
            return _failure_response(failure)
    response = jsonify({"verification_resend": {"status": "accepted"}})
    response.status_code = 202
    return response
