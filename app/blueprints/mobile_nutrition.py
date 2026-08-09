"""Mobile-authenticated nutrition contract, attached to the `/api/v1` blueprint.

Registered on the existing `mobile_api` blueprint rather than a second one, so
the versioned surface, the `Cache-Control: no-store` policy, the throttling
handler and the error envelope stay single-sourced — and so the approved-route
gate in tests/test_mobile_auth_feature_gate.py keeps covering every `/api/v1`
route there is instead of quietly stopping at the auth ones.

The web nutrition routes are untouched. They are unreachable from the app for a
structural reason, not an oversight: `@require_auth` resolves a Flask-Login
cookie plus a `cognito_sid` session value and answers a browser, and a native
client has neither. Bolting `@require_mobile_auth` onto them would also have
published their ambiguities — `DD.MM` days, no entry identity, naive timestamps,
fabricated zeroes — as the mobile contract. This is an adapter over the same
canonical ledger instead.
"""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import mobile_nutrition


@bp.get("/nutrition/diary/today")
@require_mobile_auth
def nutrition_diary_today():
    """The canonical diary day for the authenticated mobile user.

    The user comes from the verified Bearer credential (`g.mobile_user`) and
    from nowhere else — there is no account parameter to tamper with, so there
    is no cross-user read to defend against downstream.
    """
    try:
        payload = mobile_nutrition.build_diary_day(
            g.mobile_user.id, current_app.config["SECRET_KEY"])
    except Exception as error:
        # Fail closed with a nutrition-shaped error instead of falling through
        # to the blueprint's auth-flavoured handler: a storage fault here is not
        # an authentication outcome, and a client that read it as one would
        # discard a perfectly good session and send the user back to login.
        # The log line carries a type name and a request id — never a meal,
        # a macro, a target or an account identifier.
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_nutrition event=diary_read_failed error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
        return mobile_error(
            "NUTRITION_TEMPORARILY_UNAVAILABLE",
            "Nutrition data is temporarily unavailable.", 503, True)
    return jsonify(payload)
