"""Separate first-party permission; never populates D1 approval registries."""
from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user

from app.auth_middleware import require_auth
from app.mobile_auth_middleware import require_mobile_auth
from app.extensions import db
from app.services import account_eligibility, health_consent as consent, health_erasure

bp = Blueprint("health_consent", __name__)


def _respond(user):
    data = request.get_json(silent=True) if request.method == "POST" else None
    if isinstance(data, dict) and data.get("action") == "affirmative_grant":
        # Existing eligibility rules still apply. A health grant never opens
        # a teen cohort, body-photo permission, AI purpose or purchase gate.
        account_eligibility.require_capability(user, "automated_weight_targets")
    if request.method == "GET":
        result = consent.status(user.id)
    else:
        result = consent.mutate(user.id, data)
        if (result.get("erasure") or {}).get("state") == "pending":
            # First attempt now; the worker timer (hourly), daily maintenance and
            # the operator CLI retry.
            try:
                health_erasure.run(user.id)
            except Exception as error:
                db.session.rollback()
                current_app.logger.warning(
                    "health_erasure event=deferred owner_id=%s error_type=%s",
                    user.id, type(error).__name__)
            result = consent.status(user.id)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/api/health-consent", methods=["GET", "POST"])
@require_auth
def web_consent():
    return _respond(current_user)


@require_mobile_auth
def health_consent():
    return _respond(g.mobile_user)
