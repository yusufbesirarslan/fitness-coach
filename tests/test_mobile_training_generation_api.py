import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    Activity, CoachConversation, FeedItem, NutritionPlan, PumpCheck,
    TrainingPlan, TrainingPlanGenerationOperation, UserQuestProgress,
    UserSession, WorkoutLog, WorkoutSession,
)
from app.services import mobile_auth
from app.services.ai_gate import BlockingConcurrencyLimit
from app.services.mobile_training_generation import (
    GenerationInProgress, GenerationPersistenceUnavailable,
)
from app.services.mobile_training_generation import service as generation_service
from app.services.training_generation.output_errors import GenerationUnavailableError


POST_PATH = "/api/v1/training/plans"
CURRENT_PATH = "/api/v1/training/plans/current"
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
WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


def _document(exercise_count=1):
    days = []
    for index, weekday in enumerate(WEEKDAYS):
        workout = index in (0, 2, 4)
        days.append({
            "gun": weekday,
            "tip": "antrenman" if workout else "dinlenme",
            "odak": "Full body" if workout else "Recovery",
            "sure_dk": 45 if workout else 0,
            "tahmini_kalori": 300 if workout else 0,
            "egzersizler": [{
                "exercise_id": "ex_barbell_back_squat",
                "isim": "Barbell Back Squat",
                "set": 3,
                "tekrar": "8-10",
                "dinlenme": "90 sn",
                "not": "Controlled",
            } for _ in range(exercise_count)] if workout else [],
        })
    return {
        "program": days,
        "haftalik_ozet": {
            "toplam_antrenman_gun": 3,
            "toplam_tahmini_kalori": 900,
            "yogunluk_skoru": 8,
            "denge_skoru": 8,
            "uygunluk_skoru": 8,
        },
        "exercise_context": {
            "equipment_context": "spor_salonu",
            "cardio_type": "yok",
            "style": "genel",
            "catalog_version": "2026.07",
        },
    }


@pytest.fixture
def mobile_user(make_user):
    user = make_user("training-generation-mobile")
    db.session.add(UserSession(
        user_id=user.id, goal="fit", fitness_level="beginner",
        current_activity="active", tdee=2400))
    db.session.commit()
    return user


@pytest.fixture
def as_mobile(monkeypatch):
    def headers(user, key=None):
        monkeypatch.setattr(
            mobile_auth,
            "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}),
        )
        result = {"Authorization": "Bearer opaque-training-access"}
        if key is not None:
            result["Idempotency-Key"] = key
        return result
    return headers


@pytest.fixture
def successful_provider(monkeypatch):
    calls = []

    def candidate(user, last_session, preferences, chat_fn, **kwargs):
        calls.append("candidate")
        chat_fn()
        return SimpleNamespace(document=_document(), overall_score=8.5)

    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", candidate)
    monkeypatch.setattr(
        "app.blueprints.mobile_training._heavy_chat", lambda **kwargs: "unused")
    return calls


def test_post_requires_bearer_and_browser_cookie_cannot_authorize(
        client, mobile_user):
    missing = client.post(
        POST_PATH, json=CANONICAL, headers={"Idempotency-Key": "generation-key-1"})
    with client.session_transaction() as session:
        session["_user_id"] = str(mobile_user.id)
        session["_fresh"] = True
    cookie = client.post(
        POST_PATH, json=CANONICAL, headers={"Idempotency-Key": "generation-key-1"})

    assert missing.status_code == cookie.status_code == 401
    assert missing.json["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert missing.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("body", "key", "status", "code"),
    [
        (CANONICAL, None, 400, "TRAINING_PLAN_INVALID_IDEMPOTENCY_KEY"),
        (CANONICAL, "short", 400, "TRAINING_PLAN_INVALID_IDEMPOTENCY_KEY"),
        ({**CANONICAL, "unknown": 1}, "generation-key-2", 422,
         "TRAINING_PLAN_INVALID_REQUEST"),
        ({**CANONICAL, "sure": "45"}, "generation-key-3", 422,
         "TRAINING_PLAN_INVALID_REQUEST"),
        ({**CANONICAL, "kardiyo_gun": 1}, "generation-key-4", 422,
         "TRAINING_PLAN_CONFLICTING_PREFERENCES"),
    ],
)
def test_post_rejects_invalid_contract_before_provider(
        client, mobile_user, as_mobile, monkeypatch, body, key, status, code):
    monkeypatch.setattr(
        generation_service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider path reached"))

    response = client.post(POST_PATH, json=body, headers=as_mobile(mobile_user, key))

    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert TrainingPlanGenerationOperation.query.count() == 0


def test_post_rejects_oversized_body_in_the_mobile_envelope(
        app, client, mobile_user, as_mobile, monkeypatch):
    app.config["MAX_CONTENT_LENGTH"] = 512
    monkeypatch.setattr(
        generation_service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider path reached"))

    response = client.post(
        POST_PATH, json={**CANONICAL, "injuries": "x" * 1024},
        headers=as_mobile(mobile_user, "generation-key-oversized"))

    assert response.status_code == 413
    assert response.json["error"]["code"] == "REQUEST_TOO_LARGE"
    assert response.json["error"]["retryable"] is False


def test_post_success_equals_immediate_get_current(
        client, mobile_user, as_mobile, successful_provider):
    headers = as_mobile(mobile_user, "generation-key-success")

    created = client.post(POST_PATH, json=CANONICAL, headers=headers)
    current = client.get(CURRENT_PATH, headers=as_mobile(mobile_user))

    assert created.status_code == 201
    assert created.json == current.json
    assert created.headers["Idempotency-Replayed"] == "false"
    assert created.headers["Cache-Control"] == "no-store"
    assert successful_provider == ["candidate"]
    serialized = json.dumps(created.json)
    assert "training_plan_id" not in serialized
    assert "user_id" not in serialized


def test_response_loss_retry_is_exact_replay_without_provider(
        client, mobile_user, as_mobile, successful_provider, monkeypatch):
    headers = as_mobile(mobile_user, "generation-key-replay")
    first = client.post(POST_PATH, json=CANONICAL, headers=headers)
    monkeypatch.setattr(
        generation_service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider called on replay"))

    replay = client.post(POST_PATH, json=CANONICAL, headers=headers)

    assert replay.status_code == 201
    assert replay.json == first.json
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert TrainingPlan.query.count() == 1


def test_post_existing_plan_refuses_replacement(
        client, mobile_user, as_mobile, successful_provider):
    db.session.add(TrainingPlan(
        user_id=mobile_user.id, plan_data=json.dumps(_document()), score=8))
    db.session.commit()

    response = client.post(
        POST_PATH, json=CANONICAL,
        headers=as_mobile(mobile_user, "generation-key-existing"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_REPLACEMENT_REFUSED"
    assert successful_provider == []


def test_post_fingerprint_conflict_is_409_without_second_provider(
        client, mobile_user, as_mobile, successful_provider, monkeypatch):
    headers = as_mobile(mobile_user, "generation-key-conflict")
    assert client.post(POST_PATH, json=CANONICAL, headers=headers).status_code == 201
    monkeypatch.setattr(
        generation_service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider called on conflict"))

    response = client.post(
        POST_PATH, json={**CANONICAL, "sure": 60}, headers=headers)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_IDEMPOTENCY_CONFLICT"


def test_provider_unavailable_is_safe_retryable_503_and_replays_failure(
        client, mobile_user, as_mobile, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append("provider")
        raise GenerationUnavailableError("secret provider detail")

    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", fail)
    headers = as_mobile(mobile_user, "generation-key-provider-fail")
    first = client.post(POST_PATH, json=CANONICAL, headers=headers)
    second = client.post(POST_PATH, json=CANONICAL, headers=headers)

    assert first.status_code == second.status_code == 503
    for field in ("code", "message", "retryable"):
        assert first.json["error"][field] == second.json["error"][field]
    assert first.json["error"]["code"] == "TRAINING_PLAN_GENERATION_UNAVAILABLE"
    assert "secret" not in json.dumps(first.json)
    assert calls == ["provider"]


def test_post_is_owner_scoped_even_when_users_share_a_key(
        client, mobile_user, make_user, as_mobile, successful_provider):
    other = make_user("training-generation-other")
    db.session.add(UserSession(user_id=other.id, goal="fit", fitness_level="beginner"))
    db.session.commit()
    key = "generation-key-shared"

    first = client.post(POST_PATH, json=CANONICAL, headers=as_mobile(mobile_user, key))
    second = client.post(POST_PATH, json=CANONICAL, headers=as_mobile(other, key))

    assert first.status_code == second.status_code == 201
    assert TrainingPlan.query.count() == 2
    assert TrainingPlanGenerationOperation.query.count() == 2


def test_today_converges_from_no_plan_to_the_canonical_generated_plan(
        client, mobile_user, as_mobile, successful_provider):
    headers = as_mobile(mobile_user)
    before = client.get("/api/v1/today", headers=headers)

    created = client.post(
        POST_PATH, json=CANONICAL,
        headers=as_mobile(mobile_user, "generation-key-today"))
    after = client.get("/api/v1/today", headers=as_mobile(mobile_user))

    assert before.status_code == after.status_code == 200
    assert created.status_code == 201
    assert before.json["today"]["plan"]["exists"] is False
    assert before.json["today"]["status"] == "no_plan"
    assert after.json["today"]["plan"]["exists"] is True
    assert after.json["today"]["status"] != "no_plan"


def test_generation_writes_only_the_plan_and_operation(
        client, mobile_user, as_mobile, successful_provider):
    protected_models = (
        WorkoutSession, WorkoutLog, PumpCheck, UserQuestProgress, Activity,
        CoachConversation, FeedItem, NutritionPlan,
    )
    before = {model: model.query.count() for model in protected_models}
    weekly_xp = mobile_user.weekly_xp

    response = client.post(
        POST_PATH, json=CANONICAL,
        headers=as_mobile(mobile_user, "generation-key-side-effects"))

    assert response.status_code == 201
    assert {model: model.query.count() for model in protected_models} == before
    assert db.session.get(type(mobile_user), mobile_user.id).weekly_xp == weekly_xp


@pytest.mark.parametrize(
    ("error", "code", "retry_after"),
    [
        (GenerationInProgress(), "TRAINING_PLAN_GENERATION_IN_PROGRESS", "15"),
        (GenerationPersistenceUnavailable(),
         "TRAINING_PLAN_PERSISTENCE_UNAVAILABLE", None),
    ],
)
def test_command_contention_and_persistence_failures_use_safe_mobile_envelopes(
        client, mobile_user, as_mobile, monkeypatch, error, code, retry_after):
    monkeypatch.setattr(
        "app.blueprints.mobile_training.generate_and_persist",
        lambda *args, **kwargs: (_ for _ in ()).throw(error))

    response = client.post(
        POST_PATH, json=CANONICAL,
        headers=as_mobile(mobile_user, f"generation-key-{code.lower()}"))

    assert response.status_code == error.http_status
    assert response.json["error"]["code"] == code
    assert response.json["error"]["retryable"] is error.retryable
    assert response.headers.get("Retry-After") == retry_after


def test_provider_capacity_rejection_is_retryable_and_refunds_fresh_claim(
        client, mobile_user, as_mobile, successful_provider, monkeypatch):
    @contextmanager
    def reject_capacity():
        raise BlockingConcurrencyLimit("secret capacity detail")
        yield

    monkeypatch.setattr(
        "app.blueprints.mobile_training.blocking_concurrency_slot",
        reject_capacity)

    response = client.post(
        POST_PATH, json=CANONICAL,
        headers=as_mobile(mobile_user, "generation-key-capacity"))

    assert response.status_code == 503
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERATION_BUSY"
    assert response.json["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "15"
    assert TrainingPlanGenerationOperation.query.count() == 0
    assert TrainingPlan.query.count() == 0


@pytest.mark.parametrize("exercise_count", [1, 20])
def test_success_has_bounded_constant_sql_cost_without_exercise_n_plus_one(
        client, mobile_user, as_mobile, monkeypatch, exercise_count):
    def candidate(user, last_session, preferences, chat_fn, **kwargs):
        chat_fn()
        return SimpleNamespace(
            document=_document(exercise_count), overall_score=8.5)

    monkeypatch.setattr(
        generation_service, "generate_training_plan_candidate", candidate)
    monkeypatch.setattr(
        "app.blueprints.mobile_training._heavy_chat", lambda **kwargs: "unused")
    statements = []

    def record(conn, cursor, statement, params, context, many):
        statements.append(" ".join(statement.split()))

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = client.post(
            POST_PATH, json=CANONICAL,
            headers=as_mobile(
                mobile_user, f"generation-key-sql-{exercise_count}"))
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert response.status_code == 201
    assert len(statements) <= 20, statements
    operation_lookups = [
        sql for sql in statements
        if "FROM training_plan_generation_operation" in sql
    ]
    assert operation_lookups
    assert any(
        "user_id" in sql and "idempotency_key" in sql
        for sql in operation_lookups
    )
