"""Owner-only canonical mobile Today HTTP contract (Sprint 12 PR3).

Attached to the existing `mobile_api` blueprint rather than a second one, so the
`/api/v1` surface, the `Cache-Control: no-store` policy, the throttling handler,
the error envelope and the approved-route gate in
tests/test_mobile_auth_feature_gate.py all stay single-sourced.

The route holds no business logic: it resolves the principal, calls the canonical
projection and serializes. It decides nothing about workouts, rest days,
completion or dates - see app/services/mobile_today.py for why each of those is
delegated.
"""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import mobile_today


@bp.get("/today")
@require_mobile_auth
def today():
    """The canonical Today state of the authenticated mobile user.

    The user comes from the verified Bearer credential (`g.mobile_user`) and from
    nowhere else: this route reads no query parameter, no body and no header
    beyond the auth boundary, so there is no owner to tamper with and no client
    clock that could move the day.
    """
    try:
        return jsonify(mobile_today.build_today(g.mobile_user.id))
    except mobile_today.TodayUnavailable as error:
        # Fail closed with a Today-shaped error rather than falling through to the
        # blueprint's auth-flavoured handler: a storage fault is not an
        # authentication outcome, and a client that read it as one would discard a
        # good session and send the user back to login. An empty or resting Today
        # is never synthesized from a failed read.
        try:
            db.session.rollback()
        except Exception:
            pass
        # A type name and a request id only - never a plan, a workout, an injury
        # or an account identifier.
        current_app.logger.error(
            "mobile_today event=today_read_failed error_type=%s request_id=%s",
            type(error.__cause__ or error).__name__, current_request_id())
        return mobile_error(
            "TODAY_TEMPORARILY_UNAVAILABLE",
            "Today is temporarily unavailable.", 503, True)
