"""Owner-only canonical mobile Pump Check comparison HTTP contract."""
from flask import current_app, g, jsonify, request

from app.blueprints.mobile_api import bp, mobile_error
from app.config import BEDROCK_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services.ai_gate import mobile_ai_concurrency_gate
from app.services import meal_idempotency
from app.services.mobile_pump_check_comparisons import service


def _response(row, status=200):
    response = jsonify({
        "pump_check_comparison": service.serialize_comparison(row),
    })
    response.status_code = status
    return response


@bp.post("/pump-check-comparisons")
@require_mobile_auth
@limiter.limit(BEDROCK_RATELIMIT, key_func=lambda: str(g.mobile_user.id))
@mobile_ai_concurrency_gate(
    "PUMP_CHECK_COMPARISON_PROVIDER_BUSY",
    "Pump Check comparison analysis is busy.")
def create_pump_check_comparison():
    key = meal_idempotency.read_idempotency_key()
    if key is None:
        return mobile_error(
            "INVALID_IDEMPOTENCY_KEY", "A valid Idempotency-Key is required.",
            400, False)
    try:
        command = service.create_command(request.get_json(silent=True))
        row, created = service.create_or_replay(
            g.mobile_user.id, key, command)
        return _response(row, 201 if created else 200)
    except service.InvalidCommand:
        return mobile_error(
            "INVALID_PUMP_CHECK_COMPARISON",
            "Invalid Pump Check comparison input.", 400, False)
    except service.PumpCheckNotFound:
        return mobile_error(
            "PUMP_CHECK_NOT_FOUND", "Pump Check was not found.", 404, False)
    except service.ChecksNotComparable:
        # Deterministic incompatibility and permanently unusable canonical
        # media share one non-retryable answer; neither reveals which rule.
        return mobile_error(
            "PUMP_CHECKS_NOT_COMPARABLE",
            "These Pump Checks cannot be compared.", 422, False)
    except service.IdempotencyConflict:
        return mobile_error(
            "IDEMPOTENCY_CONFLICT",
            "The Idempotency-Key belongs to a different command.", 409, False)
    except service.ComparisonUnavailable as error:
        db.session.rollback()
        current_app.logger.error(
            "mobile_pump_check_comparison event=create_failed "
            "error_type=%s request_id=%s",
            type(error).__name__, current_request_id())
        return mobile_error(
            "PUMP_CHECK_COMPARISON_UNAVAILABLE",
            "Pump Check comparison is temporarily unavailable.", 503, True)


@bp.get("/pump-check-comparisons/<comparison_id>")
@require_mobile_auth
def get_pump_check_comparison(comparison_id):
    try:
        row = service.get_owned(g.mobile_user.id, comparison_id)
    except service.ComparisonNotFound:
        return mobile_error(
            "PUMP_CHECK_COMPARISON_NOT_FOUND",
            "Pump Check comparison was not found.", 404, False)
    return _response(row)
