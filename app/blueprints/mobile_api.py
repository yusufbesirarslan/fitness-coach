"""Versioned JSON API for opaque mobile authentication."""

from datetime import timezone

from flask import Blueprint, g, jsonify, request

from app.mobile_auth_middleware import (
    _safe_message, parse_bearer_header, require_mobile_auth,
)
from app.observability import current_request_id
from app.services import mobile_auth


bp = Blueprint("mobile_api", __name__, url_prefix="/api/v1")


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


def _run_issuance(operation, *args):
    try:
        return _session_response(operation(*args))
    except mobile_auth.MobileAuthFailure as exc:
        return mobile_error(
            exc.code, _safe_message(exc.code), exc.status, exc.retryable)


@bp.post("/auth/login")
def login():
    data = _json_object()
    username = (data.get("username") or "").strip() if data else ""
    password = data.get("password") or "" if data else ""
    if not username or not password:
        return mobile_error(
            "AUTH_INVALID_REQUEST", "Invalid request.", 400, False)
    return _run_issuance(mobile_auth.login, username, password)


@bp.post("/auth/refresh")
def refresh():
    data = _json_object()
    credential = data.get("refresh_credential") if data else None
    if not isinstance(credential, str) or not credential:
        return mobile_error(
            "AUTH_INVALID_REQUEST", "Invalid request.", 400, False)
    return _run_issuance(mobile_auth.refresh, credential)


@bp.post("/auth/logout")
def logout():
    # Task 7 fills the local-first provider-revocation lifecycle. The public
    # contract is already idempotent and empty.
    return "", 204


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
