"""Bearer-authenticated native workout-session write contracts (PR5).

Transport only. Every route here does the same four things and nothing more:

1. refuse to exist while ``FITX_WORKOUT_SESSIONS_ENABLED`` is OFF (404, so a
   disabled surface is indistinguishable from an absent one);
2. read the request contract from headers/body and hand it to
   ``app.services.mobile_workout_sessions``;
3. perform the one piece of work a service must never do -- the completion
   proof's provider validation and object-store upload, which are network I/O
   and therefore belong OUTSIDE the completion transaction;
4. translate the typed command result into the ``/api/v1`` envelope.

No route here writes a session, a checkpoint, a PumpCheck, a WorkoutLog or XP.
Completion goes through ``workout_session.complete_session`` into the single
canonical ``workout_completion.complete_workout`` transaction that the browser
route and the AI-coach tool already share.
"""
from functools import wraps

from flask import current_app, g, jsonify, request

from app.blueprints.mobile_api import bp, mobile_error
from app.blueprints.mobile_training import _sessions_enabled
from app.config import BEDROCK_RATELIMIT, WORKOUT_CHECKPOINT_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.models import WORKOUT_SESSION_COMPLETED
from app.observability import current_request_id
from app.services import mobile_workout_sessions as sessions
from app.services.ai_gate import mobile_ai_concurrency_gate
from app.services.menu_extract import validate_pump_check
from app.services.pump_checks import latest_training_plan_score
from app.services.validators import validate_uploaded_pump_check_image
from app.services.workout_completion import already_completed_today
from app.timeutil import app_today
import s3_helper

# What a client should DO about a refusal, independent of the HTTP status
# (PR5 section 42). ``retry`` = send the same command again; ``reread`` = the
# command can never succeed as sent, re-read canonical state and rebuild it;
# ``terminal`` = neither helps.
_RESOLUTION_HEADER = "Session-Resolution"

# The native completion contract is deliberately PRIVATE-only: PR5 excludes the
# feed/social surface, so no native completion can fan a Pump Check out to
# friends. The canonical completion service still owns the sharing behaviour for
# the paths that do use it; this transport simply never asks for it.
_NATIVE_VISIBILITY = "private"
_NATIVE_ACTIVITY_TEXT = "Bugünkü antrenmanını tamamladı (foto eklendi)"
_BASE_XP = 10
_PHOTO_BONUS = 25


def _disabled():
    """A dark flag makes the whole surface absent, not merely forbidden."""
    return mobile_error(
        "TRAINING_SESSION_NOT_FOUND", "Not found.", 404, False)


def _flag_gated(view):
    """Answer 404 while the flag is OFF, BEFORE any other decorator can answer.

    Decorator order is load-bearing, not cosmetic: applied outside the throttles
    and the concurrency gate, so a dark deployment can never answer 429 or 503
    on a route that is supposed to be absent. A saturated or hammered surface
    that is switched off must look exactly like a surface that does not exist.

    ``functools.wraps`` copies the wrapped view's ``__dict__``, so the markers
    the inner decorators set (``_ai_concurrency_gated``, ``_require_mobile_auth``)
    still reach the registered view function and their coverage guards keep
    seeing this route.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _sessions_enabled():
            return _disabled()
        return view(*args, **kwargs)

    return wrapper


def _resolution(error):
    if error.retryable:
        return "retry"
    return "reread" if error.requires_reread else "terminal"


def _failure(error):
    """One typed error, one envelope. No SQL, provider or session detail leaks:
    the message is a fixed public string chosen by the error's own code."""
    retry_after = 15 if error.http_status == 503 else None
    response = mobile_error(
        error.public_code,
        "The workout session request could not be completed.",
        error.http_status,
        error.retryable,
        retry_after=retry_after,
    )
    response.headers[_RESOLUTION_HEADER] = _resolution(error)
    return response


def _ok(result):
    response = jsonify(result.payload)
    response.status_code = result.status
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return response


def _unavailable(error, event):
    """A genuine backend failure: rolled back, logged without any payload, and
    reported as retryable. Never a 500 leaking an internal exception."""
    db.session.rollback()
    current_app.logger.error(
        "mobile_workout_session event=%s error_type=%s request_id=%s",
        event, type(error).__name__, current_request_id())
    return _failure(sessions.SessionPersistenceUnavailable())


@bp.post("/training/workout-sessions")
@require_mobile_auth
@_flag_gated
def start_workout_session():
    body = request.get_json(silent=True)
    workout_ref = body.get("workout_ref") if isinstance(body, dict) else None
    try:
        result = sessions.start(
            g.mobile_user.id, current_app.config["SECRET_KEY"], workout_ref)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001 - never leak an internal failure
        return _unavailable(error, "start_failed")
    return _ok(result)


@bp.get("/training/workout-sessions/current")
@require_mobile_auth
@_flag_gated
def current_workout_session():
    try:
        result = sessions.current(g.mobile_user.id)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "current_failed")
    return _ok(result)


@bp.post("/training/workout-sessions/<session_reference>/resume")
@require_mobile_auth
@_flag_gated
def resume_workout_session(session_reference):
    try:
        revision = sessions.parse_optional_revision(
            request.headers.get("If-Match"))
        result = sessions.resume(g.mobile_user.id, session_reference, revision)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "resume_failed")
    return _ok(result)


@bp.put("/training/workout-sessions/<session_reference>/checkpoint")
@require_mobile_auth
@_flag_gated
@limiter.limit(
    WORKOUT_CHECKPOINT_RATELIMIT, key_func=lambda: str(g.mobile_user.id))
def checkpoint_workout_session(session_reference):
    """Durable progress. Cheap by construction: no provider, no object store,
    one conditional UPDATE. ``If-Match`` and ``Idempotency-Key`` are both
    REQUIRED -- a progress write with no declared base revision cannot be
    ordered, and one with no key cannot be safely retried."""
    body = request.get_json(silent=True)
    try:
        key = sessions.parse_idempotency_key(request.headers.get("Idempotency-Key"))
        revision = sessions.parse_revision(request.headers.get("If-Match"))
        payload = body.get("checkpoint") if isinstance(body, dict) else None
        result = sessions.checkpoint(
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            session_reference,
            key,
            revision,
            lambda allowed: sessions.parse_checkpoint(payload, allowed),
        )
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "checkpoint_failed")
    return _ok(result)


@bp.post("/training/workout-sessions/<session_reference>/abandon")
@require_mobile_auth
@_flag_gated
def abandon_workout_session(session_reference):
    body = request.get_json(silent=True)
    try:
        revision = sessions.parse_optional_revision(
            request.headers.get("If-Match"))
        reason = sessions.parse_reason(
            body.get("reason") if isinstance(body, dict) else None)
        result = sessions.abandon(
            g.mobile_user.id, session_reference, revision, reason)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "abandon_failed")
    return _ok(result)


def _completion_proof():
    """Validate and store the Pump Check proof for a native completion.

    Runs entirely BEFORE the completion transaction, exactly like the browser
    route: bounded multipart image → vision validation → private object store.
    Returns the completion kwargs, or raises a typed error. A storage failure is
    fail-open (the completion proceeds without an image key) because the S3
    object is evidence, not the completion claim itself.
    """
    image_bytes, media_type, image_error = validate_uploaded_pump_check_image(
        request.files.get("image"))
    if image_error:
        raise sessions.InvalidSessionRequest("the completion image is not usable")
    location_type = (request.form.get("location_type") or "")[:50]
    description = (request.form.get("description") or "")[:200]

    check = validate_pump_check(image_bytes, location_type, description)
    if not check.get("valid"):
        raise sessions.CompletionRejected("the completion proof was rejected")

    image_key = None
    try:
        if s3_helper.is_enabled():
            image_key = s3_helper.upload_image(
                image_bytes, content_type=media_type,
                prefix="pump-checks", user_id=g.mobile_user.id,
            )
    except Exception as error:  # noqa: BLE001 - evidence upload is best-effort
        current_app.logger.info(
            "mobile_workout_session event=proof_upload_failed error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
    return {
        "image_key": image_key,
        "location_type": location_type,
        "description": description,
        "workout_score": latest_training_plan_score(g.mobile_user.id),
        "visibility": _NATIVE_VISIBILITY,
        "valid": True,
        "fallback": bool(check.get("fallback", False)),
        "base_xp": _BASE_XP,
        "photo_bonus": _PHOTO_BONUS,
        "activity_text": _NATIVE_ACTIVITY_TEXT,
        "entry_path": "mobile_route",
    }


@bp.post("/training/workout-sessions/<session_reference>/complete")
@require_mobile_auth
@_flag_gated
@limiter.limit(BEDROCK_RATELIMIT, key_func=lambda: str(g.mobile_user.id))
@mobile_ai_concurrency_gate(
    "TRAINING_SESSION_COMPLETION_BUSY", "Workout completion is busy.")
def complete_workout_session(session_reference):
    """The terminal command, routed through the canonical completion authority.

    ``If-Match`` is REQUIRED: completion is the one command that can destroy
    unsynced progress, so the client must declare the revision it believes it is
    completing, and that declaration is verified under the session row lock
    inside the completion transaction.

    A replay costs nothing: when the day already carries a completion the proof
    work is skipped entirely and the canonical service reconciles the session
    and reports ``already_completed`` with no second PumpCheck, marker, XP,
    quest claim or activity row.
    """
    try:
        # Validated and then deliberately UNUSED. The published contract
        # requires the header, and rejecting a malformed one keeps clients
        # honest -- but exact-once here is the canonical uq_pump_check_day
        # claim, never this key. Nothing about completion may depend on a
        # client-minted string.
        sessions.parse_idempotency_key(request.headers.get("Idempotency-Key"))
        revision = sessions.parse_revision(request.headers.get("If-Match"))
        row = sessions.prepare_complete(
            g.mobile_user.id, session_reference, revision)
        replaying = (
            row.status == WORKOUT_SESSION_COMPLETED
            or already_completed_today(g.mobile_user.id, app_today())
        )
        proof = {} if replaying else _completion_proof()
        result = sessions.complete(
            g.mobile_user.id, session_reference, revision, **proof)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "complete_failed")
    return _ok(result)
