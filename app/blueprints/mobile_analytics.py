"""Optional product-analytics consent and client observation ingress (v2).

    GET  /api/v1/analytics/consent   200 consent view
    PUT  /api/v1/analytics/consent   {"decision": "grant", "notice_version": "<current>"}
                                     {"decision": "withdraw"}
    POST /api/v1/analytics/events    202 {"accepted": n, "dropped": m}

    400 ANALYTICS_INVALID_REQUEST         wrong shape (body never logged)
    409 ANALYTICS_NOTICE_OUTDATED         grant against a non-current notice
    409 ANALYTICS_CONSENT_STALE           events under a grant that is not current
    429 ANALYTICS_RATE_LIMITED
    503 ANALYTICS_TEMPORARILY_UNAVAILABLE

Flag ``FITX_PRODUCT_ANALYTICS_ENABLED`` OFF (default) -> every route answers
404 BEFORE authentication (rule stays registered, as Today Guidance/PD2).
Owner = the verified Bearer principal only. The consent routes are health
boundary SAFE so withdrawal can never be blocked by another consent.
"""
from functools import wraps

from flask import abort, current_app, g, jsonify, request
from flask_limiter.errors import RateLimitExceeded

from app.blueprints.mobile_api import _json_object, bp, mobile_error
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import product_analytics
from app.services.product_analytics import consent, ingress

EVENTS_RATELIMIT = "30 per minute"
ENDPOINTS = frozenset({"mobile_api.analytics_consent_read",
                       "mobile_api.analytics_consent_write",
                       "mobile_api.analytics_events"})


def rollout_gated(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not product_analytics.enabled():
            abort(404)
        return view(*args, **kwargs)
    return wrapper


def _unavailable(event, error):
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.error(
        "mobile_analytics event=%s error_type=%s request_id=%s",
        event, type(error).__name__, current_request_id())
    return mobile_error("ANALYTICS_TEMPORARILY_UNAVAILABLE",
                        "Analytics is temporarily unavailable.", 503, True)


def _invalid():
    return mobile_error("ANALYTICS_INVALID_REQUEST", "Invalid analytics request.", 400, False)


@bp.get("/analytics/consent")
@rollout_gated
@require_mobile_auth
def analytics_consent_read():
    try:
        return jsonify(consent.view(consent.current(g.mobile_user.id)))
    except Exception as error:
        return _unavailable("consent_read_failed", error)


@bp.put("/analytics/consent")
@rollout_gated
@require_mobile_auth
def analytics_consent_write():
    data = _json_object()
    if data == {"decision": "withdraw"}:
        try:
            return jsonify(consent.withdraw(g.mobile_user.id))
        except Exception as error:
            return _unavailable("consent_withdraw_failed", error)
    if (data is None or set(data) != {"decision", "notice_version"}
            or data["decision"] != "grant" or type(data["notice_version"]) is not str):
        return _invalid()
    try:
        return jsonify(consent.grant(g.mobile_user.id, data["notice_version"]))
    except consent.NoticeOutdated:
        return mobile_error("ANALYTICS_NOTICE_OUTDATED",
                            "Review the current analytics notice.", 409, False)
    except Exception as error:
        return _unavailable("consent_grant_failed", error)


@bp.post("/analytics/events")
@rollout_gated
@require_mobile_auth
def analytics_events():
    try:
        with limiter.limit(EVENTS_RATELIMIT, key_func=lambda: f"analytics:{g.mobile_user.id}"):
            pass
    except RateLimitExceeded as error:
        return mobile_error("ANALYTICS_RATE_LIMITED", "Too many analytics requests.", 429, True,
                            retry_after=error.limit.limit.get_expiry())
    try:
        accepted, dropped = ingress.accept(g.mobile_user.id, request.get_data(cache=False))
    except ingress.InvalidEnvelope:
        return _invalid()
    except ingress.StaleGrant:
        return mobile_error("ANALYTICS_CONSENT_STALE",
                            "Analytics permission changed.", 409, False)
    except Exception as error:
        return _unavailable("events_failed", error)
    response = jsonify({"accepted": accepted, "dropped": dropped})
    response.status_code = 202
    return response
