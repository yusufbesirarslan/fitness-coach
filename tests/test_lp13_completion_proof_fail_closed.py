"""LP-13 P2: the workout-completion proof boundary is FAIL-CLOSED.

Physical-device finding (backend 2b50280): a real 285 KB PNG proof was sent to
Bedrock declared as ``image/jpeg`` (every image <= 1.5 MB was labelled JPEG),
Bedrock answered 400 (media-type mismatch), ``validate_pump_check`` turned that
provider error into ``valid=True, fallback=True`` and the workout COMPLETED --
PumpCheck, marker, XP and Activity included -- on a proof nobody evaluated.

These tests drive the real path end to end and replace ONLY the Bedrock SDK
client (``app.services.ai.bedrock_client``), so the media-type selection, the
``_bedrock_validate_image`` error wrapping, the provider-call admission and the
completion transaction all run for real:

    multipart image -> validate_uploaded_pump_check_image -> validate_pump_check
    -> detect_image_media_type -> prepare_image_for_vision
    -> _bedrock_validate_image -> (fake SDK) -> outcome -> route mapping
    -> canonical completion transaction (or nothing at all)
"""
import json
import logging
from base64 import b64encode
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from PIL import Image

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER,
    Activity,
    PumpCheck,
    TrainingPlan,
    User,
    UserChallengeProgress,
    UserQuestProgress,
    WorkoutLog,
    WorkoutSession,
)
from app.services import ai
from app.services import vision_images
from app.services.menu_extract import validate_pump_check
from app.services.vision_images import detect_image_media_type
from app.services.workout_completion import already_completed_today
from app.timeutil import app_today, audit_clock
from tests.test_mobile_workout_sessions_api import (  # noqa: F401 - fixtures
    CURRENT_PATH,
    FIXED_DAY,
    FIXED_NOW,
    SESSIONS_PATH,
    _checkpoint,
    _snapshot,
    _start,
    as_mobile,
    owner,
    plan,
    sessions_enabled,
    workout_ref,
)


# -- real image bytes -----------------------------------------------------------

def _image(fmt, size=(48, 32), **save):
    buffer = BytesIO()
    Image.new("RGB", size, (90, 120, 150)).save(buffer, format=fmt, **save)
    return buffer.getvalue()


JPEG = _image("JPEG", quality=85)
PNG = _image("PNG")
WEBP = _image("WEBP")
GIF = _image("GIF")
NOT_AN_IMAGE = b"%PDF-1.7 definitely not a picture" * 4
# A PNG whose signature and IHDR survive but whose CRC/body is cut away: the
# format is recognisable from its magic bytes, the image is not decodable.
TRUNCATED_PNG = PNG[:40]

assert JPEG[:3] == b"\xff\xd8\xff"
assert PNG[:8] == b"\x89PNG\r\n\x1a\n"
assert WEBP[:4] == b"RIFF" and WEBP[8:12] == b"WEBP"
assert GIF[:6] in (b"GIF87a", b"GIF89a")


def _large_png():
    """A real PNG over the 1.5 MB provider ceiling (noise does not compress)."""
    import os
    raw = Image.frombytes("RGB", (1100, 900), os.urandom(1100 * 900 * 3))
    buffer = BytesIO()
    raw.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) > vision_images.MAX_IMAGE_BYTES
    return data


# -- fake Bedrock SDK client ----------------------------------------------------

_REQUEST = httpx.Request("POST", "https://bedrock-runtime.test/model/invoke")
MODEL_REASON = "MODEL-REASON-SENTINEL"


def _status_error(cls, status):
    return cls("PROVIDER-MESSAGE-SENTINEL",
               response=httpx.Response(status, request=_REQUEST), body=None)


def bedrock_400():
    return _status_error(anthropic.BadRequestError, 400)


def bedrock_500():
    return _status_error(anthropic.InternalServerError, 500)


def bedrock_timeout():
    return anthropic.APITimeoutError(request=_REQUEST)


def bedrock_connection():
    return anthropic.APIConnectionError(request=_REQUEST)


def answer(text):
    return SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def verdict(is_gym):
    return answer(json.dumps({"is_gym": is_gym, "reason": MODEL_REASON}))


class FakeBedrockMessages:
    """``bedrock_client.messages`` stand-in.

    Records the media type and the decoded bytes of every image block it is
    sent; ``script`` is consumed one entry per call (an exception is raised, a
    response is returned). Once it is empty, ``always`` (a factory) answers
    every call -- the provider-call layer retries transient failures itself, so
    an outage has to persist across its attempts. Otherwise the answer is an
    explicit valid verdict.
    """

    def __init__(self):
        self.script = []
        self.sent = []
        self.always = None

    def create(self, **payload):
        from base64 import b64decode

        block = payload["messages"][0]["content"][1]
        source = block["source"]
        self.sent.append(
            SimpleNamespace(media_type=source["media_type"],
                            data=b64decode(source["data"])))
        if self.script:
            step = self.script.pop(0)
        else:
            step = self.always() if self.always else verdict(True)
        if isinstance(step, BaseException):
            raise step
        return step

    @property
    def media_types(self):
        return [call.media_type for call in self.sent]


@pytest.fixture
def bedrock(app, monkeypatch):
    """Turn the REAL vision path on, with only the SDK client replaced."""
    if ai.anthropic is None:  # pragma: no cover - the package is a hard dep
        pytest.skip("anthropic package unavailable")
    messages = FakeBedrockMessages()
    monkeypatch.setattr("app.config.BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=messages))
    return messages


UNVERIFIED_STEPS = {
    "bedrock_400": bedrock_400,
    "bedrock_500": bedrock_500,
    "timeout": bedrock_timeout,
    "connection": bedrock_connection,
    "empty_answer": lambda: answer(""),
    "prose_answer": lambda: answer("Looks like a gym to me!"),
    "json_without_verdict": lambda: answer('{"reason": "x"}'),
    "string_true_verdict": lambda: answer('{"is_gym": "true", "reason": "x"}'),
    "string_false_verdict": lambda: answer('{"is_gym": "false", "reason": "x"}'),
    "numeric_verdict": lambda: answer('{"is_gym": 1, "reason": "x"}'),
    "json_array": lambda: answer('[true]'),
    "no_text_block": lambda: SimpleNamespace(stop_reason="end_turn", content=[]),
}

# The validator itself is unavailable before any provider call: Bedrock is
# switched off, or the SDK package could not be imported (``anthropic is None``).
# Either way the proof is NOT evaluated, so it can never become a verified one.
VALIDATOR_UNAVAILABLE = {
    "bedrock_disabled": lambda mp: mp.setattr("app.config.BEDROCK_ENABLED", False),
    "client_unavailable": lambda mp: mp.setattr(ai, "anthropic", None),
}


# ==============================================================================
# 1. validate_pump_check -- media-type authority + three explicit outcomes
# ==============================================================================

@pytest.mark.parametrize("raw, expected", [
    (JPEG, "image/jpeg"),
    (PNG, "image/png"),
    (WEBP, "image/webp"),
    (GIF, "image/gif"),
], ids=["jpeg", "png", "webp", "gif"])
def test_bedrock_receives_the_media_type_the_bytes_actually_are(
    app, bedrock, raw, expected
):
    result = validate_pump_check(raw, "gym", "leg day")

    assert result == {"valid": True, "fallback": False, "reason": MODEL_REASON}
    assert bedrock.media_types == [expected]
    # Small images are passed through untouched -- same bytes, true label.
    assert bedrock.sent[0].data == raw


def test_the_exact_physical_failure_png_is_sent_as_png_not_jpeg(app, bedrock):
    """The device proof was a sub-1.5 MB PNG. Before the fix it was labelled
    ``image/jpeg`` purely because of its size."""
    assert len(PNG) < vision_images.MAX_IMAGE_BYTES

    validate_pump_check(PNG, "gym", "")

    assert bedrock.media_types == ["image/png"]
    assert "image/jpeg" not in bedrock.media_types


def test_an_oversized_proof_is_reencoded_and_labelled_as_what_it_became(
    app, bedrock
):
    validate_pump_check(_large_png(), "gym", "")

    assert bedrock.media_types == ["image/jpeg"]
    sent = bedrock.sent[0].data
    assert len(sent) <= vision_images.MAX_IMAGE_BYTES
    assert detect_image_media_type(sent) == "image/jpeg"


@pytest.mark.parametrize("raw", [TRUNCATED_PNG, NOT_AN_IMAGE, b"", b"\x89PNG"],
                         ids=["truncated_png", "not_an_image", "empty", "bare_signature"])
def test_unusable_bytes_are_rejected_without_a_provider_call(app, bedrock, raw):
    result = validate_pump_check(raw, "gym", "")

    assert result["valid"] is False
    assert result["fallback"] is False  # a decided refusal, not "unverified"
    assert bedrock.sent == []


def test_an_explicit_model_rejection_is_a_rejection(app, bedrock):
    bedrock.script.append(verdict(False))

    result = validate_pump_check(PNG, "gym", "")

    assert result == {"valid": False, "fallback": False, "reason": MODEL_REASON}


@pytest.mark.parametrize("step", sorted(UNVERIFIED_STEPS))
def test_a_proof_that_was_not_evaluated_is_never_valid(app, bedrock, step):
    bedrock.always = UNVERIFIED_STEPS[step]

    result = validate_pump_check(PNG, "gym", "")

    assert result["valid"] is False
    assert result["fallback"] is True
    assert result["reason"]
    assert MODEL_REASON not in result["reason"]


def test_an_image_preparation_failure_is_unverified(app, bedrock, monkeypatch):
    def _broken(*args, **kwargs):
        raise vision_images.ImagePreparationError("prepared image exceeds ceiling")

    monkeypatch.setattr(vision_images, "prepare_image_for_vision", _broken)

    result = validate_pump_check(PNG, "gym", "")

    assert (result["valid"], result["fallback"]) == (False, True)
    assert bedrock.sent == []


def test_a_spend_ceiling_refusal_is_unverified(app, bedrock, monkeypatch):
    from app.services.ai_spend_guard import AISpendLimitExceeded

    def _refuse(*args, **kwargs):
        raise AISpendLimitExceeded("user", "heavy")

    monkeypatch.setattr(ai, "_bedrock_validate_image", _refuse)

    result = validate_pump_check(PNG, "gym", "")

    assert (result["valid"], result["fallback"]) == (False, True)


def test_fallback_never_accompanies_valid(app, bedrock):
    """The invariant the completion boundary relies on, across every outcome."""
    outcomes = []
    for factory in [lambda: verdict(True), lambda: verdict(False),
                    *UNVERIFIED_STEPS.values()]:
        bedrock.always = factory
        outcomes.append(validate_pump_check(PNG, "gym", ""))
    bedrock.always = None
    outcomes.append(validate_pump_check(NOT_AN_IMAGE, "gym", ""))
    for disable in VALIDATOR_UNAVAILABLE.values():
        with pytest.MonkeyPatch.context() as mp:
            disable(mp)
            outcomes.append(validate_pump_check(PNG, "gym", ""))

    assert not any(o["valid"] and o["fallback"] for o in outcomes)
    assert [o for o in outcomes if o["valid"]] == [outcomes[0]]


@pytest.mark.parametrize("state", sorted(VALIDATOR_UNAVAILABLE))
def test_an_unavailable_validator_is_unverified_not_verified(
    app, bedrock, monkeypatch, caplog, state
):
    """Final-review blocker: BEDROCK_ENABLED=0 or a missing provider client
    used to return valid=True ("honest mock") and the workout completed on a
    proof nobody evaluated. It is now the third outcome: unverified."""
    VALIDATOR_UNAVAILABLE[state](monkeypatch)

    with caplog.at_level(logging.INFO):
        for raw in (PNG, JPEG, NOT_AN_IMAGE):
            result = validate_pump_check(raw, "gym", "")
            assert result == {"valid": False, "fallback": True,
                              "reason": result["reason"]}
            assert result["reason"]

    # Never reached the provider, and said so in one bounded line per call.
    assert bedrock.sent == []
    lines = [r.getMessage() for r in caplog.records
             if "event=proof_validation" in r.getMessage()]
    assert len(lines) == 3
    for line in lines:
        fields = dict(part.split("=", 1) for part in line.split()[2:])
        assert fields == {
            "event": "proof_validation", "outcome": "validator_unavailable",
            "media_type": "-", "error_type": "-", "provider_status": "-",
        }


def test_the_default_test_environment_cannot_verify_a_proof(app):
    """conftest sets BEDROCK_ENABLED=0. With no explicit validator fake there
    is no implicit success: a caller that expects completion must fake one."""
    from app import config

    assert config.BEDROCK_ENABLED is False
    result = validate_pump_check(PNG, "gym", "")
    assert (result["valid"], result["fallback"]) == (False, True)


def test_validation_logs_are_bounded(app, bedrock, caplog):
    bedrock.script.append(bedrock_400())
    with caplog.at_level(logging.INFO):
        validate_pump_check(PNG, "gym", "USER-TEXT-SENTINEL")

    lines = [r.getMessage() for r in caplog.records
             if "event=proof_validation" in r.getMessage()]
    assert len(lines) == 1
    fields = dict(part.split("=", 1) for part in lines[0].split()[2:])
    assert fields == {
        "event": "proof_validation", "outcome": "provider_error",
        "media_type": "image/png", "error_type": "RuntimeError",
        "provider_status": "4xx",
    }
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for leak in ("USER-TEXT-SENTINEL", MODEL_REASON,
                 b64encode(PNG).decode()[:24]):
        assert leak not in logged


# ==============================================================================
# 2. mobile completion boundary -- real multipart, real validators
# ==============================================================================

def _complete_with(client, headers, ref, revision, raw, declared="image/png",
                   key="proof-key-000001", filename="proof.png"):
    with audit_clock(FIXED_NOW):
        return client.post(
            f"{SESSIONS_PATH}/{ref}/complete",
            headers={**headers, "If-Match": str(revision), "Idempotency-Key": key},
            data={
                "location_type": "gym",
                "description": "USER-TEXT-SENTINEL",
                "image": (BytesIO(raw), filename, declared),
            },
            content_type="multipart/form-data",
        )


@pytest.fixture
def armed_store(monkeypatch):
    """The object store is ENABLED and counted, so any upload that ran on a
    refused or unverified proof is visible."""
    from app.blueprints import mobile_workout_sessions as routes

    uploads = []

    def _upload(image_bytes, content_type=None, **kwargs):
        uploads.append(content_type)
        return "pump-checks/upload-sentinel"

    monkeypatch.setattr(routes.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(routes.s3_helper, "upload_image", _upload)
    return uploads


def _session_columns(user):
    db.session.expire_all()
    row = WorkoutSession.query.filter_by(user_id=user.id).one()
    return {c.name: getattr(row, c.name) for c in WorkoutSession.__table__.columns}


def _side_effects(user):
    db.session.expire_all()
    row = db.session.get(User, user.id)
    return {
        "pump_checks": PumpCheck.query.filter_by(user_id=user.id).count(),
        "workout_logs": WorkoutLog.query.filter_by(user_id=user.id).count(),
        "markers": WorkoutLog.query.filter_by(
            user_id=user.id, exercise_name=WORKOUT_COMPLETION_MARKER).count(),
        "activities": Activity.query.filter_by(user_id=user.id).count(),
        "xp": row.rank_points or 0,
        "streak": row.streak_count or 0,
        "quests": UserQuestProgress.query.filter_by(user_id=user.id).count(),
        "challenges": UserChallengeProgress.query.filter_by(user_id=user.id).count(),
        "completed_today": already_completed_today(
            user.id, date.fromisoformat(FIXED_DAY)),
    }


NOTHING = {
    "pump_checks": 0, "workout_logs": 0, "markers": 0, "activities": 0,
    "xp": 0, "streak": 0, "quests": 0, "challenges": 0, "completed_today": False,
}


def _started(client, headers, workout_ref, key="proof-checkpoint-01"):
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, key)
    return reference


@pytest.mark.parametrize("raw, declared, expected", [
    (JPEG, "image/jpeg", "image/jpeg"),
    (PNG, "image/png", "image/png"),
    (WEBP, "image/webp", "image/webp"),
], ids=["jpeg", "png", "webp"])
def test_a_verified_proof_completes_exactly_once(
    client, owner, as_mobile, workout_ref, bedrock, armed_store,
    raw, declared, expected
):
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)

    response = _complete_with(client, headers, reference, 1, raw, declared)

    assert response.status_code == 200, response.json
    assert response.json["completion"]["outcome"] == "created"
    assert response.json["session"]["status"] == "completed"
    assert bedrock.media_types == [expected]
    assert armed_store == [expected]
    effects = _side_effects(owner)
    assert effects["pump_checks"] == 1 and effects["markers"] == 1
    assert effects["xp"] > 0 and effects["completed_today"] is True
    check = PumpCheck.query.filter_by(user_id=owner.id).one()
    assert (check.valid, check.fallback) == (True, False)


def test_the_physical_device_png_now_reaches_the_model_as_png(
    client, owner, as_mobile, workout_ref, bedrock, armed_store
):
    """Exact reproduction of the device run with the provider behaving as real
    Bedrock does: it refuses a PNG declared as JPEG. Fixed code never makes
    that call, so the proof is evaluated and the verdict is the model's."""
    def _strict(**payload):
        source = payload["messages"][0]["content"][1]["source"]
        from base64 import b64decode
        actual = detect_image_media_type(b64decode(source["data"]))
        if source["media_type"] != actual:
            raise bedrock_400()
        return verdict(False)

    bedrock.create = _strict
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)

    response = _complete_with(client, headers, reference, 1, PNG)

    # The model's own "not a gym" verdict -- not a provider error, not success.
    assert response.status_code == 422
    assert response.json["error"]["code"] == "TRAINING_SESSION_COMPLETION_REJECTED"
    assert _side_effects(owner) == NOTHING


def test_a_declaration_that_contradicts_the_bytes_is_refused_before_any_work(
    client, owner, as_mobile, workout_ref, bedrock, armed_store
):
    """Existing mobile policy (validate_uploaded_pump_check_image) requires the
    multipart Content-Type to match the decoded format. Pinned: the client's
    claim is never what Bedrock is told, and a mismatch never completes."""
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)
    before = _session_columns(owner)

    response = _complete_with(
        client, headers, reference, 1, PNG, declared="image/jpeg",
        filename="proof.jpg")

    assert response.status_code == 400
    assert response.json["error"]["code"] == "TRAINING_SESSION_INVALID_REQUEST"
    assert bedrock.sent == [] and armed_store == []
    assert _session_columns(owner) == before
    assert _side_effects(owner) == NOTHING


@pytest.mark.parametrize("raw", [TRUNCATED_PNG, NOT_AN_IMAGE],
                         ids=["truncated_png", "not_an_image"])
def test_an_unusable_upload_never_completes(
    client, owner, as_mobile, workout_ref, bedrock, armed_store, raw
):
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)
    before = _session_columns(owner)

    response = _complete_with(client, headers, reference, 1, raw)

    assert response.status_code == 400
    assert response.json["error"]["retryable"] is False
    assert bedrock.sent == [] and armed_store == []
    assert _session_columns(owner) == before
    assert _side_effects(owner) == NOTHING


def test_an_explicit_model_rejection_keeps_the_pr3_contract(
    client, owner, as_mobile, workout_ref, bedrock, armed_store
):
    bedrock.script.append(verdict(False))
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)
    before = _session_columns(owner)

    response = _complete_with(client, headers, reference, 1, PNG)

    assert response.status_code == 422
    assert response.json["error"]["code"] == "TRAINING_SESSION_COMPLETION_REJECTED"
    assert response.json["error"]["retryable"] is False
    assert response.headers["Session-Resolution"] == "terminal"
    assert armed_store == []
    assert _session_columns(owner) == before
    assert _side_effects(owner) == NOTHING


@pytest.mark.parametrize("step", sorted(UNVERIFIED_STEPS))
def test_an_unverified_proof_never_completes_and_is_retryable(
    client, owner, as_mobile, workout_ref, bedrock, armed_store, monkeypatch, step
):
    """Bedrock 400 is the exact physical failure class; every other step is a
    sibling way of "the proof was not evaluated"."""
    from app.services import mobile_workout_sessions as service

    entered_transaction = []
    real_complete = service.complete
    monkeypatch.setattr(
        service, "complete",
        lambda *a, **k: entered_transaction.append(True) or real_complete(*a, **k))
    bedrock.always = UNVERIFIED_STEPS[step]
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)
    with audit_clock(FIXED_NOW):
        before_read = client.get(CURRENT_PATH, headers=headers).json
    before = _session_columns(owner)

    response = _complete_with(client, headers, reference, 1, PNG)

    # Typed, retryable, distinguishable from the 422 "proof rejected".
    assert response.status_code == 503
    error = response.json["error"]
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert error["code"] == "TRAINING_SESSION_UNAVAILABLE"
    assert error["retryable"] is True
    assert response.headers["Session-Resolution"] == "retry"
    assert response.headers["Retry-After"] == "15"
    # The gate ran (transient classes are retried by the provider-call layer
    # itself); nothing after it did.
    assert len(bedrock.sent) >= 1
    assert entered_transaction == []
    assert armed_store == []
    # Session: ACTIVE, same revision, same checkpoint, no completion stamp.
    assert before["status"] == "active" and before["checkpoint_revision"] == 1
    assert _session_columns(owner) == before
    with audit_clock(FIXED_NOW):
        after_read = client.get(CURRENT_PATH, headers=headers).json
    assert after_read == before_read
    assert after_read["session"]["checkpoint"] == _snapshot()
    assert after_read["session"]["completed_at"] is None
    # No PumpCheck, marker, log, Activity, XP, streak, quest or challenge.
    assert _side_effects(owner) == NOTHING


def test_a_retry_after_a_transient_failure_completes_exactly_once(
    client, owner, as_mobile, workout_ref, bedrock, armed_store
):
    bedrock.always = bedrock_500
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)

    first = _complete_with(client, headers, reference, 1, PNG, key="retry-key-0000001")
    failed_attempts = len(bedrock.sent)
    bedrock.always = None  # the outage ends
    # The same command, unchanged -- what Session-Resolution: retry asks for.
    second = _complete_with(client, headers, reference, 1, PNG, key="retry-key-0000001")
    # A lost-response replay after success: no second artefact of any kind.
    third = _complete_with(client, headers, reference, 1, PNG, key="retry-key-0000001")

    assert first.status_code == 503
    assert second.status_code == 200
    assert second.json["completion"]["outcome"] == "created"
    assert third.status_code == 200
    assert third.json["completion"]["outcome"] == "already_completed"
    # One more provider call for the successful retry; the replay skipped proof
    # work entirely.
    assert len(bedrock.sent) == failed_attempts + 1
    assert armed_store == ["image/png"]
    effects = _side_effects(owner)
    assert effects["pump_checks"] == 1
    assert effects["markers"] == 1
    assert effects["completed_today"] is True
    xp = effects["xp"]
    assert xp > 0
    assert _side_effects(owner)["xp"] == xp
    assert PumpCheck.query.filter_by(user_id=owner.id).one().fallback is False


def test_an_unverified_completion_emits_one_bounded_diagnostic_line(
    client, owner, as_mobile, workout_ref, bedrock, armed_store, caplog
):
    bedrock.script.append(bedrock_400())
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)

    with caplog.at_level(logging.INFO):
        response = _complete_with(client, headers, reference, 1, PNG)

    assert response.status_code == 503
    lines = [r.getMessage() for r in caplog.records
             if "event=completion_unverified" in r.getMessage()]
    assert len(lines) == 1, lines
    assert lines[0].split()[0] == "mobile_workout_session"
    fields = dict(part.split("=", 1) for part in lines[0].split()[1:])
    assert fields == {
        "event": "completion_unverified",
        "category": "completion_proof_unverified",
        "request_id": response.json["error"]["request_id"],
    }
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for leak in ("USER-TEXT-SENTINEL", MODEL_REASON, reference,
                 "opaque-access-credential", b64encode(PNG).decode()[:24]):
        assert leak not in logged


@pytest.mark.parametrize("state", sorted(VALIDATOR_UNAVAILABLE))
def test_an_unavailable_validator_never_completes_on_mobile(
    client, owner, as_mobile, workout_ref, bedrock, armed_store, monkeypatch, state
):
    """BEDROCK_ENABLED=0 / no provider client: the same retryable 503 as any
    other unevaluated proof -- not a completion, not the 422 rejection."""
    from app.services import mobile_workout_sessions as service

    entered_transaction = []
    real_complete = service.complete
    monkeypatch.setattr(
        service, "complete",
        lambda *a, **k: entered_transaction.append(True) or real_complete(*a, **k))
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)
    with audit_clock(FIXED_NOW):
        before_read = client.get(CURRENT_PATH, headers=headers).json
    before = _session_columns(owner)
    VALIDATOR_UNAVAILABLE[state](monkeypatch)

    response = _complete_with(client, headers, reference, 1, PNG)

    assert response.status_code == 503
    error = response.json["error"]
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert error["code"] == "TRAINING_SESSION_UNAVAILABLE"
    assert error["retryable"] is True
    assert response.headers["Session-Resolution"] == "retry"
    assert response.headers["Retry-After"] == "15"
    assert bedrock.sent == []
    assert entered_transaction == []
    assert armed_store == []
    assert before["status"] == "active" and before["checkpoint_revision"] == 1
    assert _session_columns(owner) == before
    with audit_clock(FIXED_NOW):
        after_read = client.get(CURRENT_PATH, headers=headers).json
    assert after_read == before_read
    assert after_read["session"]["completed_at"] is None
    assert _side_effects(owner) == NOTHING


def test_an_unfaked_validator_in_the_default_environment_never_completes(
    client, owner, as_mobile, workout_ref, armed_store
):
    """No ``bedrock`` fixture, no validator fake: the conftest default
    (BEDROCK_ENABLED=0) must not be an implicit pass."""
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)
    before = _session_columns(owner)

    response = _complete_with(client, headers, reference, 1, PNG)

    assert response.status_code == 503
    assert response.json["error"]["code"] == "TRAINING_SESSION_UNAVAILABLE"
    assert _session_columns(owner) == before
    assert _side_effects(owner) == NOTHING


def test_the_route_refuses_a_fallback_even_if_it_claims_valid(
    client, owner, as_mobile, workout_ref, armed_store, monkeypatch
):
    """Defence in depth: a future regression that pairs fallback=True with
    valid=True (the old fail-open shape) still cannot complete."""
    from app.blueprints import mobile_workout_sessions as routes

    monkeypatch.setattr(
        routes, "validate_pump_check",
        lambda *a: {"valid": True, "fallback": True, "reason": "skipped"})
    headers = as_mobile(owner)
    reference = _started(client, headers, workout_ref)

    response = _complete_with(client, headers, reference, 1, PNG)

    assert response.status_code == 503
    assert response.json["error"]["retryable"] is True
    assert armed_store == []
    assert _side_effects(owner) == NOTHING


# ==============================================================================
# 3. browser completion boundary (/workout/complete)
# ==============================================================================

def _data_url(raw, declared):
    return f"data:image/{declared};base64,{b64encode(raw).decode()}"


@pytest.fixture
def browser_store(monkeypatch):
    from app.blueprints import training as routes

    uploads = []

    def _upload(image_bytes, content_type=None, **kwargs):
        uploads.append(content_type)
        return "pump-checks/upload-sentinel"

    monkeypatch.setattr(routes.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(routes.s3_helper, "upload_image", _upload)
    return uploads


@pytest.fixture
def browser_plan(auth_user):
    db.session.add(TrainingPlan(
        user_id=auth_user.id, plan_data='{"v": 1}', score=7.0))
    db.session.commit()
    return auth_user


def _browser_effects(user_id):
    db.session.expire_all()
    row = db.session.get(User, user_id)
    return (
        PumpCheck.query.filter_by(user_id=user_id).count(),
        WorkoutLog.query.filter_by(user_id=user_id).count(),
        Activity.query.filter_by(user_id=user_id).count(),
        row.rank_points or 0,
    )


def test_browser_bytes_outrank_the_declared_data_url_type(
    client, browser_plan, bedrock, browser_store
):
    """Client declares JPEG, bytes are PNG: Bedrock is told the truth, the
    proof is evaluated and completes; the stored object is labelled PNG."""
    response = client.post("/workout/complete", json={
        "image": _data_url(PNG, "jpeg"), "location_type": "salon"})

    assert response.status_code == 200, response.get_json()
    assert bedrock.media_types == ["image/png"]
    assert browser_store == ["image/png"]
    assert _browser_effects(browser_plan.id)[0] == 1


@pytest.mark.parametrize("step", ["bedrock_400", "bedrock_500", "timeout",
                                  "prose_answer"])
def test_browser_unverified_proof_never_completes(
    client, browser_plan, bedrock, browser_store, step
):
    bedrock.always = UNVERIFIED_STEPS[step]
    before = _browser_effects(browser_plan.id)

    response = client.post("/workout/complete", json={
        "image": _data_url(PNG, "png"), "location_type": "salon"})

    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "proof_unverified"
    assert body["error"]
    assert browser_store == []
    assert _browser_effects(browser_plan.id) == before
    assert already_completed_today(browser_plan.id, app_today()) is False


@pytest.mark.parametrize("state", sorted(VALIDATOR_UNAVAILABLE))
def test_browser_unavailable_validator_never_completes(
    client, browser_plan, bedrock, browser_store, monkeypatch, state
):
    VALIDATOR_UNAVAILABLE[state](monkeypatch)
    before = _browser_effects(browser_plan.id)

    response = client.post("/workout/complete", json={
        "image": _data_url(PNG, "png"), "location_type": "salon"})

    assert response.status_code == 503
    assert response.get_json()["code"] == "proof_unverified"
    assert bedrock.sent == []
    assert browser_store == []
    assert _browser_effects(browser_plan.id) == before
    assert already_completed_today(browser_plan.id, app_today()) is False


def test_browser_explicit_rejection_is_still_a_422(
    client, browser_plan, bedrock, browser_store
):
    bedrock.script.append(verdict(False))
    before = _browser_effects(browser_plan.id)

    response = client.post("/workout/complete", json={
        "image": _data_url(PNG, "png"), "location_type": "salon"})

    assert response.status_code == 422
    assert response.get_json()["error"] == MODEL_REASON
    assert browser_store == []
    assert _browser_effects(browser_plan.id) == before


# ==============================================================================
# 4. shared-caller inventory -- the stricter semantics stay at completion
# ==============================================================================

def test_validate_pump_check_is_called_only_by_the_two_completion_routes():
    """``validate_pump_check`` lives in ``menu_extract.py`` but menu/nutrition
    never call it, so fail-closed changes nothing outside workout completion.
    A new caller must decide deliberately whether it wants completion-proof
    semantics, so it fails here first."""
    root = Path(__file__).resolve().parents[1]
    callers = set()
    for path in [*root.joinpath("app").rglob("*.py"),
                 *root.joinpath("fitx_mcp").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if "validate_pump_check(" in text:
            callers.add(path.relative_to(root).as_posix())
    assert callers == {
        "app/services/menu_extract.py",            # the definition
        "app/blueprints/mobile_workout_sessions.py",
        "app/blueprints/training.py",
    }
    menu = root.joinpath("app/blueprints/menu.py").read_text(encoding="utf-8")
    assert "validate_pump_check" not in menu


def test_shared_vision_preparation_default_is_unchanged(app):
    """Menu OCR and Pump Check analysis share ``prepare_image_for_vision``; this
    PR only ADDS ``detect_image_media_type`` beside it."""
    prepared, media_type = vision_images.prepare_image_for_vision(PNG, "image/png")
    assert (prepared, media_type) == (PNG, "image/png")
    prepared, media_type = vision_images.prepare_image_for_vision(PNG)
    assert media_type == "image/jpeg"  # caller-declared default, as before
