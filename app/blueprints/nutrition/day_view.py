"""NUTR-PR6 — browser-session transport for the Nutrition day view.

A thin adapter: the owner is the authenticated session user, the body is the
transport-neutral read model (``app/services/nutrition_day_view.py``) and
nothing else. No query parameter selects a user, a day or a section. Reading
it writes nothing and calls no provider or model. Not a native endpoint —
NUTR-PR7 owns ``/api/v1`` exposure and will wrap the same service.
"""
from flask import current_app, jsonify
from flask_login import current_user

from app.auth_middleware import require_auth
from app.blueprints.nutrition import bp
from app.observability import current_request_id
from app.services.nutrition_day_view import (
    build_nutrition_day_view,
    nutrition_day_view_payload,
)


@bp.route("/nutrition-day-view")
@require_auth
def nutrition_day_view():
    try:
        payload = nutrition_day_view_payload(build_nutrition_day_view(current_user.id))
    except Exception as exc:
        # Sections already fail independently inside the service; reaching this
        # is a defect. Answer a typed failure — never a partially invented view.
        current_app.logger.warning(
            "[NUTRITION][DAY_VIEW] request_id=%s state=unavailable error_class=%s",
            current_request_id(), type(exc).__name__)
        response = jsonify({"error": "nutrition_day_view_unavailable"})
        response.status_code = 503
    else:
        response = jsonify(payload)
    # Private per-user facts: never stored by a shared cache or the bfcache.
    response.headers["Cache-Control"] = "private, no-store"
    return response
