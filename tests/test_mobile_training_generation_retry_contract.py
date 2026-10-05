"""LP-14 PR-B2: `retryable` on the native generation command means actionable.

The envelope's `retryable` answers one question (ADR 0001, and the shipped
Flutter `TrainingPlanGenerationFailure.retryable`): will sending this same
command with this same Idempotency-Key make progress? A durable FAILED
operation consumes its key (PR4A §11), so its answer is always `false`, on the
first response and on every replay. Whether a NEW logical attempt may succeed
is carried by the public code (`TRAINING_PLAN_GENERATION_UNAVAILABLE`), and the
ledger keeps the cause classification in `error_retryable` for audit.

Every answer that does stay `retryable=true` is proven here to make progress
with the same key.
"""
import json

import pytest

from app.extensions import db
from app.models import TrainingPlan, TrainingPlanGenerationOperation, User
from app.services import premium
from app.services.mobile_training_generation import service, store
from app.services.mobile_training_generation.errors import (
    GenerationInProgress,
    GenerationPersistenceUnavailable,
    StoredGenerationFailure,
)
from app.services.mobile_training_generation.locking import try_owner_lock
from app.services.training_generation.output_errors import (
    GenerationExerciseAmbiguousError,
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseIncompatibleError,
    GenerationExerciseUnresolvedError,
    GenerationUnavailableError,
    ParseFailedError,
    SchemaInvalidError,
    SemanticInvalidError,
    TruncatedError,
)
from tests.test_mobile_training_generation_api import (  # noqa: F401 - fixtures
    CANONICAL,
    POST_PATH,
    _provider_document,
    as_mobile,
    mobile_user,
)
from tests.test_mobile_training_generation_service import (  # noqa: F401 - fixtures
    _run as _run_command,
    command_user,
    fake_generator,
    native_request,
)


def _charged(user_id):
    """The raw weekly training reservations held for this owner."""
    db.session.expire_all()
    meta = db.session.get(User, user_id).user_metadata or {}
    return int((meta.get("ai_plan_quota") or {}).get("training", 0))


@pytest.fixture
def roomy_quota(app, monkeypatch):
    """Quota on, with an allowance of 2: at the production allowance of 1 a
    second reservation is refused, so a double charge would be invisible."""
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    monkeypatch.setattr(premium, "FREE_WEEKLY_AI_PLANS", 2)


def _operations(user_id):
    db.session.expire_all()
    return TrainingPlanGenerationOperation.query.filter_by(
        user_id=user_id).order_by(TrainingPlanGenerationOperation.id).all()


def test_durable_unavailable_failure_is_not_same_key_retryable_and_new_key_recovers(
        client, mobile_user, as_mobile, monkeypatch, roomy_quota):
    """The P2: this failure used to answer retryable=true while the same key
    could only ever replay it. Real route and real canonical generator."""
    calls = []

    def complete(**kwargs):
        calls.append("provider")
        if len(calls) == 1:
            raise RuntimeError("provider endpoint detail")
        return json.dumps(_provider_document("ex_barbell_back_squat"))

    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", complete)
    k1 = as_mobile(mobile_user, "durable-retry-key-1")

    first = client.post(POST_PATH, json=CANONICAL, headers=k1)

    assert first.status_code == 503
    assert first.json["error"]["code"] == "TRAINING_PLAN_GENERATION_UNAVAILABLE"
    assert first.json["error"]["retryable"] is False
    assert "Retry-After" not in first.headers
    assert "detail" not in json.dumps(first.json)
    [failed] = _operations(mobile_user.id)
    assert failed.status == "FAILED"
    assert failed.error_code == "TRAINING_PLAN_GENERATION_UNAVAILABLE"
    assert failed.error_http_status == 503
    assert failed.error_retryable is True  # cause classification kept for audit
    assert failed.attempt_count == 1
    assert failed.quota_reserved is False
    assert _charged(mobile_user.id) == 0  # the failed attempt was refunded

    for _ in range(3):
        replay = client.post(POST_PATH, json=CANONICAL, headers=k1)
        assert replay.status_code == 503
        assert {k: replay.json["error"][k] for k in ("code", "message", "retryable")} == {
            k: first.json["error"][k] for k in ("code", "message", "retryable")}
    assert calls == ["provider"]
    assert _charged(mobile_user.id) == 0
    assert TrainingPlan.query.count() == 0

    k2 = as_mobile(mobile_user, "durable-retry-key-2")
    fresh = client.post(POST_PATH, json=CANONICAL, headers=k2)

    assert fresh.status_code == 201
    assert fresh.headers["Idempotency-Replayed"] == "false"
    assert calls == ["provider", "provider"]
    assert _charged(mobile_user.id) == 1  # the successful attempt: one charge

    replayed = client.post(POST_PATH, json=CANONICAL, headers=k2)
    assert replayed.status_code == 201
    assert replayed.headers["Idempotency-Replayed"] == "true"
    assert replayed.json == fresh.json
    stale = client.post(POST_PATH, json=CANONICAL, headers=k1)
    assert stale.status_code == 503
    assert stale.json["error"]["retryable"] is False
    assert calls == ["provider", "provider"]
    assert _charged(mobile_user.id) == 1
    assert TrainingPlan.query.count() == 1
    operations = _operations(mobile_user.id)
    assert [op.status for op in operations] == ["FAILED", "SUCCEEDED"]
    assert [op.attempt_count for op in operations] == [1, 1]
    assert operations[0].error_code == "TRAINING_PLAN_GENERATION_UNAVAILABLE"


def test_parse_failure_after_repair_is_durable_and_not_same_key_retryable(
        client, mobile_user, as_mobile, monkeypatch, roomy_quota):
    calls = []

    def complete(**kwargs):
        calls.append("provider")
        return "this is not a plan"

    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", complete)
    headers = as_mobile(mobile_user, "durable-parse-key")

    first = client.post(POST_PATH, json=CANONICAL, headers=headers)
    replay = client.post(POST_PATH, json=CANONICAL, headers=headers)

    assert first.status_code == replay.status_code == 500
    assert first.json["error"]["code"] == "TRAINING_PLAN_GENERATION_PARSE_FAILED"
    assert first.json["error"]["retryable"] is False
    assert replay.json["error"]["retryable"] is False
    assert len(calls) == 2  # primary + the one bounded repair, never again
    [failed] = _operations(mobile_user.id)
    assert failed.status == "FAILED"
    assert failed.error_retryable is True
    assert _charged(mobile_user.id) == 0


# Every GenerationOutputError the canonical generator can raise after the claim.
# (exception, public code, HTTP, recorded cause classification)
DURABLE_FAILURE_MATRIX = [
    (GenerationUnavailableError("x"),
     "TRAINING_PLAN_GENERATION_UNAVAILABLE", 503, True),
    (ParseFailedError("x"), "TRAINING_PLAN_GENERATION_PARSE_FAILED", 500, True),
    (TruncatedError("x"), "TRAINING_PLAN_GENERATION_TRUNCATED", 500, True),
    (SchemaInvalidError("x"), "TRAINING_PLAN_GENERATION_SCHEMA_INVALID", 422, False),
    (SemanticInvalidError("x"),
     "TRAINING_PLAN_GENERATION_SEMANTICALLY_INVALID", 422, False),
    (GenerationExerciseUnresolvedError("x", resolution_category="inactive_id"),
     "TRAINING_PLAN_GENERATION_EXERCISE_UNRESOLVED", 422, False),
    (GenerationExerciseAmbiguousError("x"),
     "TRAINING_PLAN_GENERATION_EXERCISE_AMBIGUOUS", 422, False),
    (GenerationExerciseIdentityInvalidError("x", resolution_category="unknown_id"),
     "TRAINING_PLAN_GENERATION_EXERCISE_IDENTITY_INVALID", 422, False),
    (GenerationExerciseIncompatibleError("x", resolution_category="equipment"),
     "TRAINING_PLAN_GENERATION_EXERCISE_INCOMPATIBLE", 422, False),
]


@pytest.mark.parametrize(
    "error,code,http_status,cause_transient", DURABLE_FAILURE_MATRIX,
    ids=[row[1] for row in DURABLE_FAILURE_MATRIX])
def test_every_durable_failure_consumes_its_key_and_is_never_retryable(
        command_user, native_request, fake_generator, roomy_quota,
        error, code, http_status, cause_transient):
    fake_generator(error=error)

    with pytest.raises(StoredGenerationFailure) as first:
        _run_command(command_user, native_request, lambda: None)
    with pytest.raises(StoredGenerationFailure) as replay:
        _run_command(
            command_user, native_request,
            lambda: pytest.fail("provider called for a FAILED key"))

    for caught in (first.value, replay.value):
        assert (caught.public_code, caught.http_status, caught.retryable) == (
            code, http_status, False)
    [operation] = _operations(command_user.id)
    assert operation.status == "FAILED"
    assert operation.attempt_count == 1
    assert operation.error_retryable is cause_transient
    assert operation.quota_reserved is False
    assert _charged(command_user.id) == 0
    assert TrainingPlan.query.count() == 0


def test_legacy_failed_row_recorded_retryable_replays_as_not_retryable(
        client, mobile_user, as_mobile, monkeypatch, native_request):
    """Rows written before this change carry error_retryable=true. They are
    read, never migrated, so the projection itself must refuse the flag."""
    db.session.add(TrainingPlanGenerationOperation(
        user_id=mobile_user.id,
        idempotency_key="legacy-failed-key",
        request_fingerprint=native_request.fingerprint,
        status="FAILED",
        error_code="TRAINING_PLAN_GENERATION_UNAVAILABLE",
        error_http_status=503,
        error_retryable=True,
    ))
    db.session.commit()
    monkeypatch.setattr(
        service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider called for a FAILED key"))

    response = client.post(
        POST_PATH, json=CANONICAL,
        headers=as_mobile(mobile_user, "legacy-failed-key"))

    assert response.status_code == 503
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERATION_UNAVAILABLE"
    assert response.json["error"]["retryable"] is False
    [operation] = _operations(mobile_user.id)
    assert (operation.status, operation.attempt_count, operation.error_retryable) == (
        "FAILED", 1, True)


# The answers that stay retryable=true: each one makes progress on the same key.

def test_in_progress_is_retryable_and_the_same_key_proceeds_once_released(
        command_user, native_request, fake_generator):
    fake_generator()
    calls = []

    with try_owner_lock(command_user.id) as held:
        assert held is True
        with pytest.raises(GenerationInProgress) as caught:
            _run_command(command_user, native_request, lambda: calls.append(1))
    assert caught.value.retryable is True
    assert calls == []

    result = _run_command(command_user, native_request, lambda: calls.append(1))

    assert result.replayed is False
    assert calls == [1]
    assert TrainingPlan.query.count() == 1


def test_persistence_unavailable_is_retryable_and_the_same_key_resumes_once(
        command_user, native_request, fake_generator, monkeypatch, roomy_quota):
    fake_generator()
    real_stage = store.stage_candidate
    stage_calls = []

    def stage_fails_once(*args, **kwargs):
        stage_calls.append(1)
        if len(stage_calls) == 1:
            raise GenerationPersistenceUnavailable()
        return real_stage(*args, **kwargs)

    monkeypatch.setattr(store, "stage_candidate", stage_fails_once)
    calls = []

    with pytest.raises(GenerationPersistenceUnavailable) as caught:
        _run_command(command_user, native_request, lambda: calls.append(1))
    assert caught.value.retryable is True
    [pending] = _operations(command_user.id)
    assert (pending.status, pending.attempt_count, pending.quota_reserved) == (
        "IN_PROGRESS", 1, True)
    assert _charged(command_user.id) == 1

    result = _run_command(command_user, native_request, lambda: calls.append(1))

    assert result.replayed is False
    assert calls == [1, 1]
    [operation] = _operations(command_user.id)
    assert (operation.status, operation.attempt_count) == ("SUCCEEDED", 2)
    assert _charged(command_user.id) == 1  # one charge across both attempts
    assert TrainingPlan.query.count() == 1


def test_provider_capacity_refusal_is_retryable_and_the_same_key_then_succeeds(
        command_user, native_request, fake_generator, roomy_quota):
    fake_generator()

    class RefuseOnce:
        refused = False

        def __enter__(self):
            if not RefuseOnce.refused:
                RefuseOnce.refused = True
                raise RuntimeError("capacity unavailable")

        def __exit__(self, *args):
            return False

    def run(provider):
        return service.generate_and_persist(
            command_user, native_request, "native-generation-key",
            chat_fn=provider, provider_guard=RefuseOnce)

    with pytest.raises(RuntimeError):
        run(lambda: pytest.fail("provider called past a refused guard"))
    assert _operations(command_user.id) == []
    assert _charged(command_user.id) == 0

    calls = []
    result = run(lambda: calls.append(1))

    assert result.replayed is False
    assert calls == [1]
    [operation] = _operations(command_user.id)
    assert (operation.status, operation.attempt_count) == ("SUCCEEDED", 1)
    assert _charged(command_user.id) == 1
