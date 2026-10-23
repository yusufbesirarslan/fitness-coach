"""Transport for the shared age declaration screen. Never logs payloads."""
from flask import Blueprint, jsonify, request
from flask_login import current_user
from flask_limiter.util import get_remote_address

from app.auth_middleware import require_auth
from app.extensions import db, limiter
from app.i18n import t
from app.services import account_eligibility as eligibility

bp = Blueprint("account_eligibility", __name__)


def failure(error):
    message = ("This feature is not available for this account."
               if error.code == "ELIGIBILITY_FEATURE_UNAVAILABLE"
               else "Review age and country eligibility before continuing.")
    if request.blueprint == "mobile_api":
        from app.blueprints.mobile_api import mobile_error
        return mobile_error(error.code, message, error.status, error.status == 503)
    key = ("eligibility.feature_unavailable"
           if error.code == "ELIGIBILITY_FEATURE_UNAVAILABLE" else "eligibility.required")
    response = jsonify(error=t(key), code=error.code)
    response.status_code = error.status
    response.headers["Cache-Control"] = "no-store"
    return response


def preview():
    return jsonify(eligibility.preview(request.get_json(silent=True)))


@bp.post("/eligibility")
@limiter.limit("30 per hour", key_func=get_remote_address)
def web_preview():
    return preview()


@bp.route("/account/eligibility", methods=["GET", "POST"])
@require_auth
def web_existing():
    return existing(current_user)


def existing(user):
    if not eligibility.enabled():
        raise eligibility.EligibilityDenied("AUTH_ELIGIBILITY_UNAVAILABLE", 503)
    if request.method == "GET":
        return jsonify(eligibility=eligibility.projection(user))
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"eligibility_token"}:
        raise eligibility.EligibilityDenied()
    try:
        return jsonify(eligibility=eligibility.review_existing(user, data["eligibility_token"]))
    except eligibility.EligibilityDenied:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise eligibility.EligibilityDenied("AUTH_ELIGIBILITY_UNAVAILABLE", 503) from None


def install(app):
    app.register_blueprint(bp)
    app.register_error_handler(eligibility.EligibilityDenied, failure)


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response
