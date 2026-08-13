"""Owner-only canonical mobile Pump Check HTTP contract."""
from flask import current_app, g, jsonify, request

from app.blueprints.mobile_api import bp, mobile_error
from app.config import BEDROCK_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services.ai_gate import mobile_ai_concurrency_gate
from app.services import meal_idempotency
from app.services.mobile_pump_checks import service
from app.services.validators import validate_uploaded_pump_check_image


def _response(row, status=200):
    payload = service.serialize_pump_check(
        row, g.mobile_user.id, current_app.config["SECRET_KEY"])
    response = jsonify({"pump_check": payload})
    response.status_code = status
    return response


@bp.post("/pump-checks")
@require_mobile_auth
@limiter.limit(BEDROCK_RATELIMIT, key_func=lambda: str(g.mobile_user.id))
@mobile_ai_concurrency_gate(
    "PUMP_CHECK_PROVIDER_BUSY", "Pump Check analysis is busy.")
def create_pump_check():
    key = meal_idempotency.read_idempotency_key()
    if key is None:
        return mobile_error(
            "INVALID_IDEMPOTENCY_KEY", "A valid Idempotency-Key is required.",
            400, False)
    image_bytes, media_type, image_error = validate_uploaded_pump_check_image(
        request.files.get("image"))
    if image_error:
        return mobile_error(
            "INVALID_PUMP_CHECK_IMAGE", "Invalid Pump Check image.", 400, False)
    try:
        command = service.create_command(
            image_bytes,
            media_type,
            request.form.get("body_region"),
            request.form.get("environment"),
            request.form.get("description"),
            request.form.get("captured_at"),
        )
        row, created = service.create_or_replay(g.mobile_user.id, key, command)
        return _response(row, 201 if created else 200)
    except service.InvalidCommand:
        return mobile_error(
            "INVALID_PUMP_CHECK", "Invalid Pump Check input.", 400, False)
    except service.IdempotencyConflict:
        return mobile_error(
            "IDEMPOTENCY_CONFLICT",
            "The Idempotency-Key belongs to a different command.", 409, False)
    except (
            service.StorageUnavailable,
            service.ProviderUnavailable,
            service.AnalysisInvalid,
            service.PumpCheckPersistenceUnavailable,
    ) as error:
        db.session.rollback()
        current_app.logger.error(
            "mobile_pump_check event=create_failed error_type=%s request_id=%s",
            type(error).__name__, current_request_id())
        if isinstance(error, service.PumpCheckPersistenceUnavailable):
            code = "PUMP_CHECK_PERSISTENCE_UNAVAILABLE"
        elif isinstance(error, service.StorageUnavailable):
            code = "PUMP_CHECK_STORAGE_UNAVAILABLE"
        elif isinstance(error, service.AnalysisInvalid):
            code = "PUMP_CHECK_ANALYSIS_INVALID"
        else:
            code = "PUMP_CHECK_PROVIDER_UNAVAILABLE"
        return mobile_error(
            code, "Pump Check is temporarily unavailable.", 503, True)


@bp.get("/pump-checks/<pump_check_token>")
@require_mobile_auth
def get_pump_check(pump_check_token):
    try:
        row = service.get_owned(
            g.mobile_user.id,
            pump_check_token,
            current_app.config["SECRET_KEY"],
        )
    except service.PumpCheckNotFound:
        return mobile_error(
            "PUMP_CHECK_NOT_FOUND", "Pump Check was not found.", 404, False)
    return _response(row)
