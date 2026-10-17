"""Owner-only, read-only native AI allowance contract (MON-02-BE-03A).

A TRANSPORT over ``ai_allowances.allowance_view``. Attached to ``mobile_api``
and behind its own default-OFF flag ``AI_ALLOWANCE_LEDGER_ENABLED``: OFF answers
404 BEFORE authentication (LP17-B1 pattern) and the ledger never runs. ON, the
owner is the Bearer principal only; no query, body or header is read, so the
client can choose neither tier, period nor policy.
"""
from functools import wraps

from flask import abort, current_app, g, jsonify

from app.blueprints.mobile_api import bp, mobile_error
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import ai_allowances


def _rollout_gated(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_app.config.get("AI_ALLOWANCE_LEDGER_ENABLED") is not True:
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.get("/ai/allowances")
@_rollout_gated
@require_mobile_auth
def ai_allowances_read():
    try:
        return jsonify(ai_allowances.allowance_view(g.mobile_user.id))
    except ai_allowances.AllowanceError as error:
        if error.status >= 500:
            current_app.logger.error(
                "mobile_ai_allowances event=allowance_read_failed code=%s request_id=%s",
                error.code, current_request_id())
        # Typed and never the blueprint's auth-flavoured catch-all.
        return mobile_error(error.code, "AI allowances could not be read.",
                            error.status, error.retryable,
                            retry_after=30 if error.retryable else None)
