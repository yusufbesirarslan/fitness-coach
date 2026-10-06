"""Bearer-owned canonical language write (LP14 PR-D).

PUT /api/v1/account/language accepts only {"language": "tr"|"en"} and
returns the committed account_projection with HTTP 200. Uses the same
verified-user DEFAULT_RATELIMIT as account/profile (600/hour by default).
No session, provider, email or content-generation side effects.
"""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import (
    _json_object, account_projection, bp, mobile_error,
)
from app.extensions import db
from app.i18n import AVAILABLE_LOCALES
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id


@bp.put("/account/language")
@require_mobile_auth
def put_account_language():
    data = _json_object()
    if (data is None or set(data) != {"language"}
            or not isinstance(data["language"], str)
            or data["language"] not in AVAILABLE_LOCALES):
        return mobile_error(
            "LANGUAGE_INVALID_REQUEST", "Invalid language request.", 400, False)

    user = g.mobile_user
    try:
        user.language = data["language"]
        db.session.commit()
        payload = account_projection(user)
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_account_language event=language_write_failed "
            "error_type=%s request_id=%s",
            type(error).__name__, current_request_id())
        return mobile_error(
            "LANGUAGE_TEMPORARILY_UNAVAILABLE",
            "Language is temporarily unavailable.", 503, True)
    return jsonify(payload)
