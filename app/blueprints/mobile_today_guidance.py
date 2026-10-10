"""Owner-only native Today Guidance HTTP contract (LP17-B1).

A TRANSPORT over the LP17 read model (`app/services/today_guidance_read_model`),
never a second Today authority: training comes from `mobile_today.build_today`,
Nutrition/Hydration from the day view and check-ins from the native history
reader, all resolved inside `build_today_guidance`. This route decides nothing.

Attached to the existing `mobile_api` blueprint (one `/api/v1` surface, one
`no-store` policy, one error envelope, one approved-route allow-list) and
additionally behind its own default-OFF rollout flag,
`FITX_MOBILE_TODAY_GUIDANCE_ENABLED`, because mobile auth may already be on
where this contract has not been approved. `GET /api/v1/today` is untouched.
"""
from functools import wraps

from flask import abort, current_app, g, jsonify

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import today_guidance_read_model


def _guidance_enabled():
    return bool(current_app.config.get("FITX_MOBILE_TODAY_GUIDANCE_ENABLED", False))


def _rollout_gated(view):
    """While the flag is OFF the route is absent, BEFORE authentication runs.

    Outermost on purpose: with the flag OFF there is no 401 for a missing token
    and no guidance for a valid one - the caller gets the application's own
    not-found answer, the same one an unregistered path gets. `wraps` copies the
    inner `_require_mobile_auth` marker so the auth coverage guards still see it.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _guidance_enabled():
            abort(404)
        return view(*args, **kwargs)
    return wrapper


@bp.get("/today/guidance")
@_rollout_gated
@require_mobile_auth
def today_guidance():
    """The LP17 version-1 guidance payload of the authenticated mobile user.

    The owner is the verified Bearer principal (`g.mobile_user`) and nothing
    else: no query parameter, body or header beyond the auth boundary is read,
    so there is no owner, date or timezone to spoof. The id is captured before
    the read model runs because its canonical snapshots release the session.
    """
    owner_id = g.mobile_user.id
    try:
        return jsonify(today_guidance_read_model.build_today_guidance(owner_id))
    except today_guidance_read_model.GuidanceUnavailable as error:
        # Typed and retryable, never the blueprint's auth-flavoured catch-all
        # (a client would drop a good session), and never a fabricated
        # successful, empty or resting day.
        try:
            db.session.rollback()
        except Exception:
            pass
        # A type name and a request id only - no message, owner or fact.
        current_app.logger.error(
            "mobile_today_guidance event=guidance_read_failed error_type=%s request_id=%s",
            type(error.__cause__ or error).__name__, current_request_id())
        return mobile_error(
            "TODAY_GUIDANCE_TEMPORARILY_UNAVAILABLE",
            "Today guidance is temporarily unavailable.", 503, True)
