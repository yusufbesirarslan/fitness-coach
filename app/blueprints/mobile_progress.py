"""Owner-only canonical mobile Progress summary contract (LP-04, spec J1).

    GET /api/v1/progress/summary

Attached to the existing `mobile_api` blueprint rather than a second one, so the
`/api/v1` surface, the `Cache-Control: no-store` policy, the throttling handler,
the error envelope and the approved-route gate in
tests/test_mobile_auth_feature_gate.py all stay single-sourced.

A transport adapter and nothing else. Every state in the response was decided by
`app/services/progress_summary` - the same builder and the same hand-written
projection the web `GET /api/progress/summary` serves - so native and web cannot
disagree about trajectory, body, performance or consistency. This module holds no
threshold, no window, no date and no fallback of its own. See
docs/MOBILE_PROGRESS_SUMMARY.md.
"""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services.progress_summary import (
    build_progress_summary, progress_summary_payload,
)


@bp.get("/progress/summary")
@require_mobile_auth
def progress_summary():
    """The canonical Progress summary of the authenticated mobile user.

    The owner comes from the verified Bearer credential (`g.mobile_user`) and from
    nowhere else: this route reads no query parameter, no body and no header
    beyond the auth boundary, so there is no owner to tamper with. The window is
    pinned by the service (4 weeks ending on the application's Istanbul day), not
    by the caller - a client that can re-window the analysis owns the trajectory.
    """
    try:
        summary = build_progress_summary(g.mobile_user.id)
    except Exception as error:
        # Fail closed with a Progress-shaped 503. `building_baseline` is a real
        # user state ("not enough of your history yet"), so answering it for a
        # storage fault or an unmapped canonical signal would report broken
        # infrastructure as an honest assessment of the user. Falling through to
        # the blueprint's auth-flavoured handler would be wrong too: a client that
        # read AUTH_TEMPORARILY_UNAVAILABLE would discard a good session.
        try:
            db.session.rollback()
        except Exception:
            pass
        # A type name and a request id only - never a weight, a session count,
        # the payload or an account identifier.
        current_app.logger.error(
            "mobile_progress event=summary_read_failed error_type=%s "
            "request_id=%s",
            type(error).__name__, current_request_id())
        return mobile_error(
            "PROGRESS_UNAVAILABLE",
            "Progress is temporarily unavailable.", 503, True)

    current_app.logger.info(
        "mobile_progress event=summary_read trajectory=%s body=%s request_id=%s",
        summary.trajectory.state, summary.body.status, current_request_id())
    return jsonify(progress_summary_payload(summary))
