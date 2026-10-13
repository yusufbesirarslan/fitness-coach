"""Dark, bearer-only subscription foundation; no legacy Premium mutations."""
from functools import wraps

from flask import current_app, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.services import subscriptions
from app.services.apple_subscriptions import SubscriptionError


def _failure(error):
    return mobile_error(error.code, "Subscription request could not be completed.",
                        error.status, error.status == 503,
                        retry_after=30 if error.status == 503 else None)


def _ready(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_app.config.get("APPLE_SUBSCRIPTIONS_ENABLED"):
            return _failure(SubscriptionError("SUBSCRIPTIONS_DISABLED", 503))
        try:
            return view(*args, **kwargs)
        except SubscriptionError as error:
            return _failure(error)
    return wrapped


@bp.get("/subscription/purchase-context")
@require_mobile_auth
@_ready
def subscription_purchase_context():
    return jsonify(subscriptions.purchase_context(g.mobile_user.id))


@bp.post("/subscription/verify")
@require_mobile_auth
@_ready
@limiter.limit("5 per minute; 30 per hour", key_func=lambda: str(g.mobile_user.id),
               error_message="SUBSCRIPTION_RATE_LIMITED")
def subscription_verify():
    request.max_content_length = 1024
    try:
        body = request.get_json(silent=True)
    except RequestEntityTooLarge:
        return _failure(SubscriptionError("SUBSCRIPTION_INVALID_REQUEST", 413))
    if (not isinstance(body, dict) or set(body) != {"transaction_id"}
            or not isinstance(body["transaction_id"], str)
            or not body["transaction_id"].isascii()
            or not body["transaction_id"].isdigit()
            or not 1 <= len(body["transaction_id"]) <= 64):
        return _failure(SubscriptionError("SUBSCRIPTION_INVALID_REQUEST", 400))
    return jsonify({"contract_version": 1, "entitlement": subscriptions.verify_purchase(
        g.mobile_user.id, body["transaction_id"])})


@bp.get("/subscription/entitlement")
@require_mobile_auth
@_ready
def subscription_entitlement():
    return jsonify({"contract_version": 1, "entitlement": subscriptions.entitlement(g.mobile_user.id)})
