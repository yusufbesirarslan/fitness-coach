"""Bearer-authenticated native workout-session contracts.

Transport only. Every route here does the same four things and nothing more:

1. refuse to exist while ``FITX_WORKOUT_SESSIONS_ENABLED`` is OFF (404, so a
   disabled surface is indistinguishable from an absent one);
2. read the request contract from headers/body and hand it to
   ``app.services.mobile_workout_sessions``;
3. perform the one piece of work a service must never do -- the OPTIONAL
   completion photo's provider validation and object-store upload, which are
   network I/O and therefore belong OUTSIDE the completion transaction (a
   photo-free completion, PRIV-K6-B1, does none of it);
4. translate the typed command result into the ``/api/v1`` envelope.

No route here writes a session, a checkpoint, a PumpCheck, a WorkoutLog or XP.
Completion goes through ``workout_session.complete_session`` into the single
canonical ``workout_completion.complete_workout`` transaction that the browser
route and the AI-coach tool already share.
"""
from functools import wraps

from app.services.ai_consent import ConsentDenied
from flask import current_app, g, jsonify, make_response, request
from werkzeug.exceptions import HTTPException

from app.blueprints.mobile_api import bp, mobile_error
from app.blueprints.mobile_training import _sessions_enabled
from app.config import (
    BEDROCK_RATELIMIT, WORKOUT_CHECKPOINT_RATELIMIT, WORKOUT_COMPLETION_RATELIMIT,
)
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.models import WORKOUT_SESSION_COMPLETED
from app.observability import current_request_id
from app.services import mobile_workout_sessions as sessions
from app.services import training_intelligence
from app.services.workout_session.context import (
    MAX_V2_BODY_BYTES, parse_contract, parse_v2, project_context,
)
from app.services.workout_session.execution import owned_session
from app.services.workout_session.service import build_session_view
from app.services.workout_session.prescription import project as project_prescription
from app.services.ai_gate import mobile_ai_concurrency_gate
from app.services.menu_extract import validate_pump_check
from app.services.pump_checks import latest_training_plan_score
from app.services.validators import validate_uploaded_pump_check_image
from app.services.workout_completion import PROOF_NONE, already_completed_today
from app.timeutil import app_today
import s3_helper

# What a client should DO about a refusal, independent of the HTTP status
# (PR5 section 42). ``retry`` = send the same command again; ``reread`` = the
# command can never succeed as sent, re-read canonical state and rebuild it;
# ``terminal`` = neither helps. ``terminal`` describes the submitted command and
# input, never the session: a 422 completion rejection leaves the session ACTIVE.
_RESOLUTION_HEADER = "Session-Resolution"

# The native completion contract is deliberately PRIVATE-only: PR5 excludes the
# feed/social surface, so no native completion can fan a Pump Check out to
# friends. The canonical completion service still owns the sharing behaviour for
# the paths that do use it; this transport simply never asks for it.
_NATIVE_VISIBILITY = "private"
_NATIVE_ACTIVITY_TEXT = "Bugünkü antrenmanını tamamladı (foto eklendi)"
_NATIVE_NO_PHOTO_ACTIVITY_TEXT = "Bugünkü antrenmanını tamamladı"
_BASE_XP = 10
_PHOTO_BONUS = 25
# JSON keys that belong to the optional completion photo; naming any of them
# makes the request a photo completion (see _photo_evidence_submitted).
_PHOTO_FIELDS = ("image", "location_type", "description")


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
        try:
            g.workout_contract = parse_contract(request.headers.get("AxisAI-Workout-Contract"))
            if g.workout_contract == 2 and not _context_enabled():
                response = _disabled()
            else:
                response = view(*args, **kwargs)
        except sessions.SessionCommandError as error:
            response = _failure(error)
        response = make_response(response)
        response.vary.add("AxisAI-Workout-Contract")
        response.headers['Cache-Control'] = 'private, no-store'
        return response

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
    if g.workout_contract == 2:
        try:
            session_payload = result.payload.get('session')
            row = owned_session(g.mobile_user.id, session_payload['session_ref']) if session_payload else None
            if row:
                # Build the entire negotiated envelope from one row snapshot,
                # even if another writer advanced after command acceptance.
                result.payload['session'] = sessions.project_session(row, build_session_view(row, app_today()))
            result.payload['execution_context'] = project_context(row)
            result.payload['prescription'] = project_prescription(row.prescription_data) if row else None
        except sessions.SessionCommandError as error:
            return _failure(error)
        except Exception as error:  # noqa: BLE001 - same bounded API failure path
            return _unavailable(error, 'projection_failed')
    response = jsonify(result.payload)
    response.status_code = result.status
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return response


def _context_enabled():
    return _sessions_enabled() and current_app.config.get('FITX_TRAINING_EXECUTION_CONTEXT_ENABLED', False)


def _insights_enabled():
    """P1 readiness (TI-00 §17): P1 ON requires P0 ON. P1 ON with P0 OFF is an
    invalid rollout state, so P1 is absent rather than silently advertised."""
    return bool(_context_enabled()
                and current_app.config.get('FITX_TRAINING_INSIGHTS_ENABLED', False))


@bp.get('/training/workout-execution-capabilities')
@require_mobile_auth
@_flag_gated
def workout_execution_capabilities():
    enabled = _context_enabled()
    return jsonify({'contract_version': 1, 'checkpoint_versions': [1, 2] if enabled else [1],
                    'execution_context_enabled': bool(enabled),
                    'insights_enabled': _insights_enabled()})


@bp.get("/training/workout-sessions/<session_reference>/training-insight")
@require_mobile_auth
@_flag_gated
def read_training_insight(session_reference):
    """TI-03 deterministic Training Insight: a read projection, never a write.

    Absent while P1 is not ready (404, the same envelope as an absent session).
    An absent or foreign session is 404. Missing history is a 200 with explicit
    missing codes; only a genuine read failure is a retryable 503.
    """
    if not _insights_enabled():
        return _disabled()
    try:
        payload = training_intelligence.build_training_insight(
            g.mobile_user.id, session_reference)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001 - never leak an internal failure
        training_intelligence.record_insight_event("insight_unavailable")
        return _unavailable(error, "insight_failed")
    return jsonify(payload)


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


@bp.get("/training/workout-sessions/<session_reference>")
@require_mobile_auth
@_flag_gated
def read_workout_session(session_reference):
    try:
        result = sessions.read(g.mobile_user.id, session_reference)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "read_failed")
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
    # Bound the decoded transport body as well as canonical component JSON.
    # V1 and multipart completion retain their existing transport limits.
    if g.workout_contract == 2 and len(request.get_data(cache=True)) > MAX_V2_BODY_BYTES:
        return _failure(sessions.InvalidSessionRequest('V2 checkpoint exceeds its size bound'))
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
            lambda allowed: parse_v2(body, allowed) if g.workout_contract == 2
            else sessions.parse_checkpoint(payload, allowed),
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


def _photo_evidence_submitted():
    """True unless the request affirmatively carries NO photo (PRIV-K6-B1).

    Photo-free means an empty body, or a JSON object with none of the photo
    fields. Anything else is the photo transport and is validated as one: any
    multipart/form body (the photo contract's own encoding -- even with a
    missing, empty, misnamed or unusable file), a JSON body naming a photo
    field, or any other non-empty body that cannot be read as JSON. A submitted
    photo therefore always meets the photo path's consent gate and validation
    and can never be silently discarded into a photo-free success.
    """
    # Classify transport BEFORE parsing: malformed multipart can consume the
    # stream while producing empty files/form collections. Those collections
    # cannot establish affirmative absence of submitted photo transport.
    if request.mimetype in {"multipart/form-data", "application/x-www-form-urlencoded"}:
        return True
    if not request.get_data(cache=True):
        return False
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        return any(name in body for name in _PHOTO_FIELDS)
    return True


def _no_photo_completion():
    """Completion kwargs for a first-party, photo-free completion (PRIV-K6-B1).

    No consent check (no provider is contacted), no image validation, no
    object-store write, no PumpCheck, no photo bonus. The canonical service
    still enforces ownership, the locked checkpoint revision and exact-once.
    """
    return {
        "proof": PROOF_NONE,
        "base_xp": _BASE_XP,
        "photo_bonus": 0,
        "activity_text": _NATIVE_NO_PHOTO_ACTIVITY_TEXT,
        "entry_path": "mobile_route",
    }


def _completion_proof():
    """Validate and store the Pump Check proof for a native completion.

    Runs entirely BEFORE the completion transaction, exactly like the browser
    route: bounded multipart image → vision validation → private object store.
    Returns the completion kwargs, or raises a typed error. A storage failure is
    fail-open (the completion proceeds without an image key) because the S3
    object is evidence, not the completion claim itself.
    """
    from app.blueprints.ai_consent import require_legacy_ai
    require_legacy_ai()
    image_bytes, media_type, image_error = validate_uploaded_pump_check_image(
        request.files.get("image"))
    if image_error:
        raise sessions.InvalidSessionRequest("the completion image is not usable")
    location_type = (request.form.get("location_type") or "")[:50]
    description = (request.form.get("description") or "")[:200]

    check = validate_pump_check(image_bytes, location_type, description)
    if check.get("fallback"):
        # Fail closed (LP-13 P2): the proof could not be EVALUATED (validator
        # unavailable, provider error, timeout, malformed answer). Checked first
        # and independently of ``valid``, so fallback=True can never be read as
        # permission. Not a rejection either: nothing is written, the session
        # stays ACTIVE, and the same command may be retried.
        current_app.logger.warning(
            "mobile_workout_session event=completion_unverified "
            "category=completion_proof_unverified request_id=%s",
            current_request_id())
        raise sessions.CompletionProofUnverified(
            "the completion proof could not be verified")
    if check.get("valid") is not True:
        # Refuses THIS proof, not the session: nothing has been written, the
        # session stays ACTIVE with its checkpoint, and a later attempt may
        # succeed. Fixed category + request id only -- the model's free-text
        # reason, the user's description and the session reference are never
        # logged (the mobile request log keeps owner handles out on purpose).
        current_app.logger.info(
            "mobile_workout_session event=completion_rejected "
            "category=completion_proof_rejected request_id=%s",
            current_request_id())
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
        # Only an evaluated, accepted proof reaches here (fallback raised above).
        "fallback": False,
        "base_xp": _BASE_XP,
        "photo_bonus": _PHOTO_BONUS,
        "activity_text": _NATIVE_ACTIVITY_TEXT,
        "entry_path": "mobile_route",
    }


@mobile_ai_concurrency_gate(
    "TRAINING_SESSION_COMPLETION_BUSY", "Workout completion is busy.")
def _complete_with_photo(session_reference, revision):
    proof = _completion_proof()
    return _ok(sessions.complete(
        g.mobile_user.id, session_reference, revision, **proof))


@bp.post("/training/workout-sessions/<session_reference>/complete")
@require_mobile_auth
@_flag_gated
@limiter.limit(WORKOUT_COMPLETION_RATELIMIT, key_func=lambda: str(g.mobile_user.id))
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

    The photo is OPTIONAL evidence (PRIV-K6-B1). A request with no photo input
    at all completes through the same canonical transaction with no consent
    check, no provider, no upload and no PumpCheck. A request that submits a
    photo keeps the photo path unchanged: D1 fail-closed consent first, then
    validation -- a submitted photo is never discarded into a photo-free success.
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
        # A replay never processes a submitted photo and never builds a photo
        # command: with no photo there is nothing that could become a PumpCheck.
        if replaying or not _photo_evidence_submitted():
            proof = _no_photo_completion()
        else:
            # D1 refusal precedes AI admission. Only actual photo work pays
            # the provider quota and shared blocking-work concurrency budget.
            from app.blueprints.ai_consent import require_legacy_ai
            require_legacy_ai()
            g.completion_photo_admission = True
            with limiter.limit(
                    BEDROCK_RATELIMIT, key_func=lambda: str(g.mobile_user.id),
                    exempt_when=lambda: not getattr(g, "completion_photo_admission", False)):
                return _complete_with_photo(session_reference, revision)
        result = sessions.complete(
            g.mobile_user.id, session_reference, revision, **proof)
    except sessions.SessionCommandError as error:
        return _failure(error)
    except (ConsentDenied, HTTPException):
        raise
    except Exception as error:  # noqa: BLE001
        return _unavailable(error, "complete_failed")
    return _ok(result)
