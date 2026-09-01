import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    PumpCheck,
    TrainingPlan,
    TrainingPlanGenerationOperation,
    UserQuestProgress,
    UserSession,
    WorkoutLog,
    WorkoutSession,
)
from app.services import premium
from app.services.mobile_training_generation import parse_native_request
from app.services.mobile_training_generation import service, store
from app.services.mobile_training_generation.errors import (
    ExistingPlanRefused,
    GenerationPersistenceUnavailable,
    GenerationPrerequisiteMissing,
    GenerationQuotaExceeded,
    IdempotencyConflict,
    StoredGenerationFailure,
)
from app.services.mobile_training_generation.locking import try_owner_lock
from app.services.training_generation.output_errors import (
    GenerationUnavailableError,
    SchemaInvalidError,
)


CANONICAL = {
    "gun_sayisi": 3,
    "ekipman": "spor_salonu",
    "odak": "tum_vucut",
    "sure": 45,
    "kardiyo_tipi": "yok",
    "kardiyo_gun": 0,
    "kardiyo_sure": 20,
    "kardiyo_yogunluk": "orta",
    "antrenman_tarzi": "genel",
    "odak_hedef": "genel",
    "injuries": "",
}
CANDIDATE_DOCUMENT = {
    "program": [{"gun": "Pazartesi", "tip": "dinlenme", "egzersizler": []}],
    "haftalik_ozet": {"toplam_antrenman_gun": 0},
    "exercise_context": {
        "equipment_context": "spor_salonu",
        "cardio_type": "yok",
        "style": "genel",
        "catalog_version": "test-v1",
    },
}


@pytest.fixture
def command_user(make_user):
    user = make_user("native-generation-command")
    db.session.add(UserSession(
        user_id=user.id, goal="fit", fitness_level="beginner",
        current_activity="active", tdee=2400))
    db.session.commit()
    return user


@pytest.fixture
def native_request():
    return parse_native_request(CANONICAL)


@pytest.fixture
def fake_generator(monkeypatch):
    def install(candidate=None, error=None):
        candidate = candidate or SimpleNamespace(
            document=CANDIDATE_DOCUMENT, overall_score=8.25)

        def generate(user, last_session, preferences, chat_fn, **kwargs):
            chat_fn()
            if error is not None:
                raise error
            return candidate

        monkeypatch.setattr(service, "generate_training_plan_candidate", generate)
        return candidate

    return install


def _run(user, request, provider, key="native-generation-key"):
    return service.generate_and_persist(
        user, request, key, chat_fn=provider,
        provider_guard=nullcontext)


def test_no_plan_success_persists_one_canonical_plan(
        command_user, native_request, fake_generator):
    fake_generator()
    calls = []

    result = _run(command_user, native_request, lambda: calls.append("provider"))

    assert result.replayed is False
    assert calls == ["provider"]
    assert TrainingPlan.query.count() == 1
    assert json.loads(result.plan.plan_data) == CANDIDATE_DOCUMENT
    operation = TrainingPlanGenerationOperation.query.one()
    assert operation.status == "SUCCEEDED"
    assert operation.training_plan_id == result.plan.id
    assert operation.plan_lineage_id == result.plan.lineage_id
    assert operation.candidate_plan_data is None


def test_succeeded_same_key_replays_without_provider(
        command_user, native_request, fake_generator):
    fake_generator()
    first = _run(command_user, native_request, lambda: None)

    second = _run(
        command_user, native_request,
        lambda: pytest.fail("provider called during replay"))

    assert second.replayed is True
    assert second.plan.id == first.plan.id
    assert TrainingPlan.query.count() == 1


def test_same_key_different_fingerprint_conflicts_before_provider(
        command_user, native_request, fake_generator):
    fake_generator()
    _run(command_user, native_request, lambda: None)
    changed = parse_native_request({**CANONICAL, "sure": 60})

    with pytest.raises(IdempotencyConflict):
        _run(command_user, changed, lambda: pytest.fail("provider called"))


def test_existing_plan_refused_before_provider_and_consumes_no_key(
        command_user, native_request, fake_generator):
    fake_generator()
    db.session.add(TrainingPlan(
        user_id=command_user.id, plan_data=json.dumps(CANDIDATE_DOCUMENT)))
    db.session.commit()

    with pytest.raises(ExistingPlanRefused):
        _run(command_user, native_request, lambda: pytest.fail("provider called"))

    assert TrainingPlanGenerationOperation.query.count() == 0


def test_missing_session_consumes_no_key(make_user, native_request, fake_generator):
    user = make_user("native-generation-no-session")
    fake_generator()

    with pytest.raises(GenerationPrerequisiteMissing):
        _run(user, native_request, lambda: pytest.fail("provider called"))

    assert TrainingPlanGenerationOperation.query.count() == 0


def test_generated_state_persists_without_regeneration_after_crash(
        command_user, native_request):
    operation = TrainingPlanGenerationOperation(
        user_id=command_user.id,
        idempotency_key="native-generation-key",
        request_fingerprint=native_request.fingerprint,
        status="GENERATED",
        candidate_plan_data=json.dumps(CANDIDATE_DOCUMENT),
        candidate_score=7.5,
    )
    db.session.add(operation)
    db.session.commit()

    result = _run(
        command_user, native_request,
        lambda: pytest.fail("provider called for staged operation"))

    assert result.replayed is False
    assert json.loads(result.plan.plan_data) == CANDIDATE_DOCUMENT
    assert result.plan.score == 7.5


def test_provider_failure_is_stored_bounded_and_refunds_quota(
        app, command_user, native_request, fake_generator):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    fake_generator(error=GenerationUnavailableError("provider secret detail"))

    with pytest.raises(StoredGenerationFailure) as caught:
        _run(command_user, native_request, lambda: None)

    operation = TrainingPlanGenerationOperation.query.one()
    assert operation.status == "FAILED"
    assert operation.error_code == caught.value.public_code
    assert operation.error_http_status == 503
    assert "secret" not in operation.error_code
    assert operation.candidate_plan_data is None
    assert operation.quota_reserved is False
    assert premium.remaining_ai_plans(command_user, "training") == 1
    assert TrainingPlan.query.count() == 0


def test_invalid_candidate_failure_is_stored_as_nonretryable_422(
        command_user, native_request, fake_generator):
    fake_generator(error=SchemaInvalidError("private candidate detail"))

    with pytest.raises(StoredGenerationFailure) as caught:
        _run(command_user, native_request, lambda: None)

    assert caught.value.http_status == 422
    assert caught.value.retryable is False


def test_quota_exhaustion_creates_no_operation(
        app, command_user, native_request, fake_generator):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    command_user.user_metadata = {
        "ai_plan_quota": {"week": premium._week_key(), "training": 1}}
    db.session.commit()
    fake_generator()

    with pytest.raises(GenerationQuotaExceeded):
        _run(command_user, native_request, lambda: pytest.fail("provider called"))

    assert TrainingPlanGenerationOperation.query.count() == 0


def test_provider_guard_refusal_removes_fresh_claim_and_refunds_quota(
        app, command_user, native_request, fake_generator):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    fake_generator()

    class RefusingGuard:
        def __enter__(self):
            raise RuntimeError("capacity unavailable")

        def __exit__(self, *args):
            return False

    with pytest.raises(RuntimeError, match="capacity unavailable"):
        service.generate_and_persist(
            command_user, native_request, "native-generation-key",
            chat_fn=lambda: pytest.fail("provider called"),
            provider_guard=RefusingGuard,
        )

    assert TrainingPlanGenerationOperation.query.count() == 0
    assert premium.remaining_ai_plans(command_user, "training") == 1


def test_sqlite_owner_lock_is_nonblocking_for_same_owner(app, command_user):
    with try_owner_lock(command_user.id) as outer:
        with try_owner_lock(command_user.id) as inner:
            assert outer is True
            assert inner is False


def test_failed_same_key_replays_without_provider(
        command_user, native_request, fake_generator):
    fake_generator(error=GenerationUnavailableError("private"))
    with pytest.raises(StoredGenerationFailure):
        _run(command_user, native_request, lambda: None)

    with pytest.raises(StoredGenerationFailure):
        _run(command_user, native_request, lambda: pytest.fail("provider called"))


def test_staging_failure_returns_no_success_and_leaves_recoverable_claim(
        command_user, native_request, fake_generator, monkeypatch):
    fake_generator()
    monkeypatch.setattr(
        store, "stage_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            GenerationPersistenceUnavailable()))

    with pytest.raises(GenerationPersistenceUnavailable):
        _run(command_user, native_request, lambda: None)

    assert TrainingPlan.query.count() == 0
    assert TrainingPlanGenerationOperation.query.one().status == "IN_PROGRESS"


def test_final_insert_failure_leaves_generated_candidate(
        command_user, native_request, fake_generator, monkeypatch):
    fake_generator()
    monkeypatch.setattr(
        store, "_insert_plan",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("database detail")))

    with pytest.raises(GenerationPersistenceUnavailable):
        _run(command_user, native_request, lambda: None)

    operation = TrainingPlanGenerationOperation.query.one()
    assert operation.status == "GENERATED"
    assert operation.candidate_plan_data is not None
    assert TrainingPlan.query.count() == 0


def test_final_current_plan_race_refuses_replacement_and_preserves_candidate(
        command_user, native_request, fake_generator, monkeypatch):
    fake_generator()
    real_stage = store.stage_candidate

    def stage_then_race(*args, **kwargs):
        real_stage(*args, **kwargs)
        db.session.add(TrainingPlan(
            user_id=command_user.id,
            plan_data=json.dumps({"program": [{"gun": "race"}]})))
        db.session.commit()

    monkeypatch.setattr(store, "stage_candidate", stage_then_race)

    with pytest.raises(ExistingPlanRefused):
        _run(command_user, native_request, lambda: None)

    operation = TrainingPlanGenerationOperation.query.one()
    assert operation.status == "GENERATED"
    assert operation.candidate_plan_data is not None
    assert TrainingPlan.query.count() == 1


def test_generation_creates_no_unrelated_domain_records(
        command_user, native_request, fake_generator):
    fake_generator()
    _run(command_user, native_request, lambda: None)

    assert WorkoutSession.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert PumpCheck.query.count() == 0
    assert UserQuestProgress.query.count() == 0
