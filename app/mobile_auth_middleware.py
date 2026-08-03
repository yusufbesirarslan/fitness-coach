"""Bearer-only authentication boundary for the mobile API."""

from functools import wraps

from flask import g, request

from app.services import auth_contract, mobile_auth


def parse_bearer_header(value):
    if not isinstance(value, str) or not value.startswith("Bearer "):
        raise ValueError("invalid bearer header")
    credential = value[7:]
    if not credential or any(char.isspace() for char in credential):
        raise ValueError("invalid bearer header")
    return credential


def require_mobile_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from app.blueprints.mobile_api import mobile_error
        try:
            raw = parse_bearer_header(request.headers.get("Authorization"))
            principal = mobile_auth.authenticate_access(raw)
        except ValueError:
            # A missing or malformed Authorization header is the mobile
            # equivalent of an anonymous browser request, not a rejected token.
            auth_contract.record_outcome(
                auth_contract.MOBILE, auth_contract.OUTCOME_NO_IDENTITY)
            return mobile_error(
                "AUTH_SESSION_EXPIRED", "Mobile session expired.", 401, False)
        except mobile_auth.MobileAuthFailure as exc:
            auth_contract.record_outcome(
                auth_contract.MOBILE, auth_contract.mobile_outcome_for(exc.code))
            return mobile_error(
                exc.code, _safe_message(exc.code), exc.status, exc.retryable)
        g.mobile_user = principal.user
        g.mobile_session = principal.family
        g.mobile_claims = principal.claims
        auth_contract.record_outcome(
            auth_contract.MOBILE, auth_contract.OUTCOME_OK)
        return view(*args, **kwargs)
    wrapped._require_mobile_auth = True
    return wrapped


def _safe_message(code):
    return {
        "AUTH_INVALID_REQUEST": "Invalid request.",
        "AUTH_INVALID_CREDENTIALS": "Invalid credentials.",
        "AUTH_RATE_LIMITED": "Too many requests.",
        "AUTH_SESSION_EXPIRED": "Mobile session expired.",
        "AUTH_REFRESH_FAILED": "Refresh failed. Sign in again.",
        "AUTH_TEMPORARILY_UNAVAILABLE": "Authentication is temporarily unavailable.",
    }.get(code, "Authentication failed.")
