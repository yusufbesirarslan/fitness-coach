"""Bearer-authenticated native weekly check-in (LP16-B). Transport only.

    POST /api/v1/progress/check-ins   one full check-in (Idempotency-Key required)
    GET  /api/v1/progress/check-ins   last 12 full check-ins + current Istanbul week

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`Cache-Control: no-store` policy, the error envelope, the 413 handler and the
approved-route gate in tests/test_mobile_auth_feature_gate.py stay
single-sourced. A sibling of `mobile_progress` rather than part of it: that
module is pinned (tests/test_mobile_progress_summary_architecture.py) to be
the Progress summary transport and nothing else.

What this module does: owner = `g.mobile_user`, nothing else; the per-owner
write ceiling (typed 429, never `AUTH_RATE_LIMITED`); `Idempotency-Key`
syntax; mapping the closed native error vocabulary. Everything else lives in
`app/services/mobile_weekly_checkin` (strict parser, `native.v1`
fingerprint, write composition, history read), and persistence is the LP16-A
`weekly_checkin` service. No provider/AI call. Logs carry a fixed event, a
fixed outcome, the request id and, on failure, an exception type - never a
weight, a metric, the body, the key, the fingerprint or an identity.
See docs/MOBILE_WEEKLY_CHECKIN.md.
"""
from flask import current_app, g, jsonify, request
from flask_limiter.errors import RateLimitExceeded

from app.blueprints.mobile_api import bp, mobile_error
from app.config import CHECKIN_WRITE_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import meal_idempotency, mobile_weekly_checkin
from app.services.weekly_review import operations, provider
from app.services.workout_state.snapshot import coherent_read_snapshot


_CHECKIN_UNAVAILABLE = ("CHECKIN_TEMPORARILY_UNAVAILABLE",
                        "Check-in is temporarily unavailable.", 503, True)


def _contract_versions():
    return request.args.getlist("contract_version")


def _contract_version():
    return mobile_weekly_checkin.parse_contract_version(_contract_versions())


def _invalid_request():
    return mobile_error(
        "CHECKIN_INVALID_REQUEST", "Invalid check-in request.", 400, False)


def _owner_key():
    return str(g.mobile_user.id)


def _checkin_unavailable(error, event):
    """Check-in-shaped retryable 503; never the blueprint's auth-shaped one.

    A type name and a request id only - never a weight, a metric, the body,
    the key, the fingerprint or an account identifier.
    """
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.error(
        "mobile_checkin event=%s error_type=%s request_id=%s",
        event, type(error).__name__, current_request_id())
    return mobile_error(*_CHECKIN_UNAVAILABLE)


def _invalid_value(error):
    """422 with the bounded field/reason pair (closed vocabularies only)."""
    response = jsonify({"error": {
        "code": "CHECKIN_INVALID_VALUE",
        "message": "A check-in value is outside its range.",
        "retryable": False,
        "request_id": current_request_id(),
        "field": error.field,
        "reason": error.reason,
    }})
    response.status_code = 422
    return response


@bp.post("/progress/check-ins")
@require_mobile_auth
def create_check_in():
    """One full weekly check-in for the Bearer owner; 201 new, 200 replay."""
    try:
        # Checked in the view so a rejection is a typed check-in 429, not the
        # blueprint's auth-flavoured AUTH_RATE_LIMITED.
        with limiter.limit(CHECKIN_WRITE_RATELIMIT, key_func=_owner_key):
            pass
    except RateLimitExceeded as error:
        return mobile_error(
            "CHECKIN_RATE_LIMITED", "Too many check-in requests.", 429, True,
            retry_after=error.limit.limit.get_expiry())

    key = meal_idempotency.read_idempotency_key()
    if key is None:
        return mobile_error(
            "INVALID_IDEMPOTENCY_KEY", "A valid Idempotency-Key is required.",
            400, False)
    try:
        command = mobile_weekly_checkin.parse_request(
            request.get_data(cache=False), is_json=request.is_json,
            version=_contract_version())
        expected_week = mobile_weekly_checkin.parse_week_precondition(
            request.headers.get(mobile_weekly_checkin.WEEK_PRECONDITION_HEADER))
    except mobile_weekly_checkin.InvalidRequest:
        return _invalid_request()
    except mobile_weekly_checkin.InvalidValue as error:
        return _invalid_value(error)

    try:
        body, created = mobile_weekly_checkin.submit(
            g.mobile_user, key, command, expected_week_start=expected_week,
            review_intent=(provider.capture_intent(g.mobile_user.id)
                           if command.contract_version == 2 else None))
    except mobile_weekly_checkin.WeekChanged:
        # Nothing was staged; release the owner lock the claim took.
        db.session.rollback()
        current_app.logger.info(
            "mobile_checkin event=write outcome=week_changed request_id=%s",
            current_request_id())
        return mobile_error(
            "CHECKIN_WEEK_CHANGED",
            "The check-in week has changed; nothing was saved.", 409, False)
    except mobile_weekly_checkin.NutritionApplicabilityChanged:
        db.session.rollback()
        return mobile_error(
            "CHECKIN_NUTRITION_APPLICABILITY_CHANGED",
            "Nutrition plan availability changed. Refresh the check-in.", 409, False)
    except mobile_weekly_checkin.IdempotencyConflict:
        current_app.logger.info(
            "mobile_checkin event=write outcome=conflict request_id=%s",
            current_request_id())
        return mobile_error(
            "IDEMPOTENCY_CONFLICT",
            "The Idempotency-Key belongs to a different check-in.", 409, False)
    except Exception as error:
        return _checkin_unavailable(error, "write_failed")

    current_app.logger.info(
        "mobile_checkin event=write outcome=%s request_id=%s",
        "created" if created else "replayed", current_request_id())
    response = jsonify(body)
    response.status_code = 201 if created else 200
    return response


@bp.get("/progress/check-ins")
@require_mobile_auth
def list_check_ins():
    """The Bearer owner's last 12 full check-ins + the current Istanbul week.

    Only the explicit contract_version opt-in is read.
    """
    try:
        body = mobile_weekly_checkin.build_history(g.mobile_user.id, _contract_version())
    except mobile_weekly_checkin.InvalidRequest:
        return _invalid_request()
    except Exception as error:
        return _checkin_unavailable(error, "history_read_failed")
    return jsonify(body)


@bp.get("/progress/check-ins/<int:check_in_id>/evaluation")
@require_mobile_auth
def read_check_in_evaluation(check_in_id):
    """Read only. Opening a result or history never creates or dispatches AI."""
    versions = _contract_versions()
    if versions not in (["1"], ["2"]):
        return _invalid_request()
    owner = g.mobile_user.id
    try:
        with coherent_read_snapshot():
            body = operations.read(owner, check_in_id, int(versions[0]))
    except Exception as error:
        return _checkin_unavailable(error, "evaluation_read_failed")
    if body is None:
        return mobile_error("CHECKIN_EVALUATION_NOT_FOUND",
                            "No evaluation exists for this check-in.", 404, False)
    return jsonify(body)
