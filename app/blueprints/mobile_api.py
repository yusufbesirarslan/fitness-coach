"""Versioned JSON API for opaque mobile authentication."""

from datetime import timezone

from flask import Blueprint, current_app, g, jsonify, request
from flask_limiter.util import get_remote_address

from app.mobile_auth_middleware import (
    _safe_message, parse_bearer_header, require_mobile_auth,
)
from app.observability import current_request_id
from app.extensions import db, limiter, login_throttle_available
from app.services import mobile_auth
from app.services.mobile_credentials import (
    InvalidMobileCredential, credential_rate_limit_key,
)


bp = Blueprint("mobile_api", __name__, url_prefix="/api/v1")


@bp.after_request
def prevent_mobile_response_caching(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def mobile_error(code, message, status, retryable, retry_after=None):
    response = jsonify({"error": {
        "code": code,
        "message": message,
        "retryable": bool(retryable),
        "request_id": current_request_id(),
    }})
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _session_response(issued):
    return jsonify({"session": {
        "type": "opaque",
        "token_type": "Bearer",
        "access_credential": issued.access_credential,
        "refresh_credential": issued.refresh_credential,
        "access_expires_at": _iso(issued.access_expires_at),
        "refresh_expires_at": _iso(issued.refresh_expires_at),
    }})


def _json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _login_username_key():
    data = _json_object() or {}
    submitted = data.get("username")
    username = submitted.strip().lower() if isinstance(submitted, str) else ""
    return f"mobile-login-user:{username}" if username else get_remote_address()


def _refresh_credential_key():
    data = _json_object() or {}
    credential = data.get("refresh_credential")
    if not isinstance(credential, str):
        return get_remote_address()
    try:
        return credential_rate_limit_key(
            credential,
            current_app.config["MOBILE_AUTH_DERIVATION_KEYRING"],
            current_app.config["MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION"],
        )
    except (InvalidMobileCredential, KeyError):
        return get_remote_address()


def _run_issuance(operation, *args, refresh_context=False):
    try:
        return _session_response(operation(*args))
    except mobile_auth.MobileAuthFailure as exc:
        code = exc.code
        if refresh_context and code == "AUTH_INVALID_CREDENTIALS":
            code = "AUTH_REFRESH_FAILED"
        return mobile_error(
            code, _safe_message(code), exc.status, exc.retryable,
            retry_after=exc.retry_after)


@bp.post("/auth/login")
@limiter.limit("10 per minute; 50 per hour")
@limiter.limit(
    "15 per 15 minutes", key_func=_login_username_key,
    deduct_when=lambda response: response.status_code in (401, 403))
def login():
    data = _json_object()
    submitted_username = data.get("username") if data else None
    submitted_password = data.get("password") if data else None
    if (not isinstance(submitted_username, str)
            or not isinstance(submitted_password, str)):
        return mobile_error(
            "AUTH_INVALID_REQUEST", "Invalid request.", 400, False)
    username = submitted_username.strip()
    password = submitted_password
    if not username or not password:
        return mobile_error(
            "AUTH_INVALID_REQUEST", "Invalid request.", 400, False)
    if (current_app.config.get("LOGIN_FAIL_CLOSED", True)
            and not login_throttle_available()):
        return mobile_error(
            "AUTH_TEMPORARILY_UNAVAILABLE",
            "Authentication is temporarily unavailable.", 503, True)
    return _run_issuance(mobile_auth.login, username, password)


@bp.post("/auth/refresh")
@limiter.limit(
    lambda: current_app.config["MOBILE_AUTH_REFRESH_RATELIMIT"],
    key_func=get_remote_address)
@limiter.limit(
    lambda: current_app.config["MOBILE_AUTH_REFRESH_RATELIMIT"],
    key_func=_refresh_credential_key)
def refresh():
    data = _json_object()
    credential = data.get("refresh_credential") if data else None
    if not isinstance(credential, str) or not credential:
        return mobile_error(
            "AUTH_INVALID_REQUEST", "Invalid request.", 400, False)
    return _run_issuance(
        mobile_auth.refresh, credential, refresh_context=True)


@bp.post("/auth/logout")
@limiter.limit(
    lambda: current_app.config["MOBILE_AUTH_LOGOUT_RATELIMIT"],
    key_func=get_remote_address)
def logout():
    access_credential = None
    try:
        access_credential = parse_bearer_header(
            request.headers.get("Authorization"))
    except ValueError:
        pass
    data = _json_object() or {}
    refresh_credential = data.get("refresh_credential")
    if not isinstance(refresh_credential, str):
        refresh_credential = None
    try:
        result = mobile_auth.prepare_logout(
            access_credential, refresh_credential)
    except mobile_auth.MobileAuthFailure as exc:
        return mobile_error(
            exc.code, _safe_message(exc.code), exc.status, exc.retryable)
    mobile_auth.best_effort_provider_revoke(result)
    return "", 204


@bp.errorhandler(429)
def rate_limited(error):
    retry_after = error.limit.limit.get_expiry()
    return mobile_error(
        "AUTH_RATE_LIMITED", "Too many requests.", 429, True,
        retry_after=retry_after)


@bp.errorhandler(Exception)
def normalize_unhandled_mobile_failure(error):
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.error(
        "mobile_auth event=unhandled_failure category=storage "
        "error_type=%s request_id=%s",
        type(error).__name__, current_request_id())
    return mobile_error(
        "AUTH_TEMPORARILY_UNAVAILABLE",
        "Authentication is temporarily unavailable.", 503, True)


@bp.get("/account/me")
@require_mobile_auth
def me():
    user = g.mobile_user
    return jsonify({"user": {
        "username": user.username,
        "display_name": user.full_name or user.username,
        "profile_complete": bool(user.profile_complete),
        "preferred_language": user.language,
        "goal": user.goal,
        "goal_type": user.goal_type,
    }})


# Product route modules that extend this same blueprint, imported last so `bp`
# and `mobile_error` already exist. Keeping them on one blueprint keeps one
# `/api/v1` surface, one no-store policy, one throttling handler and one feature
# gate — and keeps every mobile route inside the approved-route allow-list in
# tests/test_mobile_auth_feature_gate.py.
from app.blueprints import mobile_nutrition  # noqa: E402,F401
from app.blueprints import mobile_pump_checks  # noqa: E402,F401
from app.blueprints import mobile_pump_check_comparisons  # noqa: E402,F401
