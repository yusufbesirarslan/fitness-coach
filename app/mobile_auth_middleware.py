"""Bearer-only authentication boundary for the mobile API."""

from functools import wraps

from flask import g, request

from app.services import auth_contract, mobile_auth

_UNSET = object()
_MOBILE_AUTH_CACHE = "fitx.mobile_auth_result"
_MOBILE_PREAUTH_ENDPOINTS = frozenset({
    "mobile_api.login",
    "mobile_api.refresh",
    "mobile_api.logout",
})


def parse_bearer_header(value):
    if not isinstance(value, str) or not value.startswith("Bearer "):
        raise ValueError("invalid bearer header")
    credential = value[7:]
    if not credential or any(char.isspace() for char in credential):
        raise ValueError("invalid bearer header")
    return credential


def _load_mobile_principal():
    """Resolve the mobile principal via the existing Bearer pipeline.

    Cached on this request's WSGI environ so the default limiter
    (before_request) and ``require_mobile_auth`` share one
    authenticate_access call. Flask ``g`` is app-context scoped and can
    outlive a request in tests; the environ cannot. Does not parse JWTs
    or trust unverified claims.
    """
    cached = request.environ.get(_MOBILE_AUTH_CACHE, _UNSET)
    if cached is not _UNSET:
        return cached
    try:
        raw = parse_bearer_header(request.headers.get("Authorization"))
        principal = mobile_auth.authenticate_access(raw)
    except Exception as exc:
        result = (None, exc)
    else:
        g.mobile_user = principal.user
        g.mobile_session = principal.family
        g.mobile_claims = principal.claims
        result = (principal, None)
    request.environ[_MOBILE_AUTH_CACHE] = result
    return result


def bind_mobile_request_principal():
    """Bind a verified mobile principal before default limiter evaluation.

    Flask-Limiter checks default limits in before_request, which runs before
    ``require_mobile_auth``. Pre-auth /api/v1/auth/* routes are skipped so
    login/refresh/logout stay IP-keyed. Invalid credentials are not turned
    into a user limiter identity.
    """
    if request.blueprint != "mobile_api":
        return
    for attr in ("mobile_user", "mobile_session", "mobile_claims"):
        if hasattr(g, attr):
            delattr(g, attr)
    from app.extensions import limiter
    if not limiter.enabled:
        return
    if request.endpoint in _MOBILE_PREAUTH_ENDPOINTS:
        return
    if not request.headers.get("Authorization"):
        return
    _load_mobile_principal()


def require_mobile_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from app.blueprints.mobile_api import mobile_error
        _principal, error = _load_mobile_principal()
        if isinstance(error, ValueError):
            # A missing or malformed Authorization header is the mobile
            # equivalent of an anonymous browser request, not a rejected token.
            auth_contract.record_outcome(
                auth_contract.MOBILE, auth_contract.OUTCOME_NO_IDENTITY)
            return mobile_error(
                "AUTH_SESSION_EXPIRED", "Mobile session expired.", 401, False)
        if isinstance(error, mobile_auth.MobileAuthFailure):
            auth_contract.record_outcome(
                auth_contract.MOBILE, auth_contract.mobile_outcome_for(error.code))
            return mobile_error(
                error.code, _safe_message(error.code), error.status, error.retryable)
        if error is not None:
            raise error
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
