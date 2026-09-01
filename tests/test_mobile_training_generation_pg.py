"""Opt-in PostgreSQL races for durable native plan generation."""
import os
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


@pytest.fixture
def pg_generation_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    from flask import Flask
    from app.extensions import db
    from app.models import User, UserSession

    app = Flask("mobile-training-generation-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-training-generation-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        AI_PLAN_QUOTA_ENABLED=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            User(username=f"pg-generation-{index}",
                 email=f"pg-generation-{index}@example.invalid",
                 cognito_sub=f"pg-generation-sub-{index}")
            for index in range(2)
        ]
        db.session.add_all(users)
        db.session.flush()
        db.session.add_all([
            UserSession(user_id=user.id, goal="fit", fitness_level="beginner")
            for user in users
        ])
        db.session.commit()
        user_ids = [user.id for user in users]
    try:
        yield app, user_ids, monkeypatch
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _request(duration=45):
    from app.services.mobile_training_generation import parse_native_request
    return parse_native_request({
        "gun_sayisi": 3,
        "ekipman": "spor_salonu",
        "odak": "tum_vucut",
        "sure": duration,
        "kardiyo_tipi": "yok",
        "kardiyo_gun": 0,
        "kardiyo_sure": 20,
        "kardiyo_yogunluk": "orta",
        "antrenman_tarzi": "genel",
        "odak_hedef": "genel",
        "injuries": "",
    })


def _document():
    weekdays = [
        "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
    ]
    return {
        "program": [{
            "gun": weekday,
            "tip": "antrenman" if index in (0, 2, 4) else "dinlenme",
            "odak": "Full body" if index in (0, 2, 4) else "Recovery",
            "sure_dk": 45 if index in (0, 2, 4) else 0,
            "tahmini_kalori": 300 if index in (0, 2, 4) else 0,
            "egzersizler": [{
                "exercise_id": "ex_barbell_back_squat", "isim": "Squat",
                "set": 3, "tekrar": "8", "dinlenme": "90 sn", "not": "",
            }] if index in (0, 2, 4) else [],
        } for index, weekday in enumerate(weekdays)],
        "haftalik_ozet": {
            "toplam_antrenman_gun": 3,
            "toplam_tahmini_kalori": 900,
            "yogunluk_skoru": 8,
            "denge_skoru": 8,
            "uygunluk_skoru": 8,
        },
        "exercise_context": {
            "equipment_context": "spor_salonu", "cardio_type": "yok",
            "style": "genel", "catalog_version": "2026.07",
        },
    }


def _run(app, user_id, request, key, outcomes, index):
    from app.extensions import db
    from app.models import User
    from app.services.mobile_training_generation import (
        ExistingPlanRefused, GenerationInProgress, IdempotencyConflict,
        generate_and_persist,
    )
    try:
        with app.app_context():
            user = db.session.get(User, user_id)
            try:
                result = generate_and_persist(
                    user, request, key, chat_fn=lambda **kwargs: "unused",
                    provider_guard=nullcontext)
                outcomes[index] = ("ok", result.replayed, result.plan.id)
            except GenerationInProgress:
                outcomes[index] = ("in_progress",)
            except IdempotencyConflict:
                outcomes[index] = ("conflict",)
            except ExistingPlanRefused:
                outcomes[index] = ("existing",)
            finally:
                db.session.remove()
    except Exception as error:  # pragma: no cover - surfaced by assertions
        outcomes[index] = ("unexpected", type(error).__name__, str(error))


def _install_blocking_provider(monkeypatch):
    from app.services.mobile_training_generation import service
    entered = threading.Event()
    release = threading.Event()
    calls = []
    guard = threading.Lock()

    def candidate(*args, **kwargs):
        with guard:
            calls.append(threading.get_ident())
        entered.set()
        assert release.wait(timeout=20), "provider release event was not set"
        return SimpleNamespace(document=_document(), overall_score=8.5)

    monkeypatch.setattr(service, "generate_training_plan_candidate", candidate)
    return entered, release, calls


def _assert_one_success(app, user_id, outcomes, calls):
    from app.models import TrainingPlan, TrainingPlanGenerationOperation
    assert sorted(outcome[0] for outcome in outcomes.values()) == ["in_progress", "ok"]
    assert len(calls) == 1
    with app.app_context():
        plan = TrainingPlan.query.filter_by(user_id=user_id).one()
        operation = TrainingPlanGenerationOperation.query.filter_by(user_id=user_id).one()
        assert operation.status == "SUCCEEDED"
        assert operation.training_plan_id == plan.id
        assert operation.plan_lineage_id == plan.lineage_id
        assert plan.mutation_version == 1


def test_same_owner_key_fingerprint_runs_one_provider_execution(pg_generation_app):
    app, user_ids, monkeypatch = pg_generation_app
    entered, release, calls = _install_blocking_provider(monkeypatch)
    outcomes = {}
    first = threading.Thread(
        target=_run, args=(app, user_ids[0], _request(), "pg-generation-key-1", outcomes, 0),
        daemon=True)
    first.start()
    assert entered.wait(timeout=20)
    second = threading.Thread(
        target=_run, args=(app, user_ids[0], _request(), "pg-generation-key-1", outcomes, 1),
        daemon=True)
    second.start()
    second.join(timeout=10)
    assert not second.is_alive(), "duplicate request blocked behind provider"
    release.set()
    first.join(timeout=20)

    _assert_one_success(app, user_ids[0], outcomes, calls)


def test_same_owner_key_different_fingerprint_conflicts_without_second_call(
        pg_generation_app):
    app, user_ids, monkeypatch = pg_generation_app
    entered, release, calls = _install_blocking_provider(monkeypatch)
    outcomes = {}
    first = threading.Thread(
        target=_run, args=(app, user_ids[0], _request(), "pg-generation-key-2", outcomes, 0),
        daemon=True)
    first.start()
    assert entered.wait(timeout=20)
    second = threading.Thread(
        target=_run, args=(app, user_ids[0], _request(60), "pg-generation-key-2", outcomes, 1),
        daemon=True)
    second.start()
    second.join(timeout=10)
    assert not second.is_alive(), "conflicting request blocked behind provider"
    release.set()
    first.join(timeout=20)

    assert sorted(outcome[0] for outcome in outcomes.values()) == ["conflict", "ok"]
    assert len(calls) == 1


def test_different_owners_same_key_are_independent(pg_generation_app):
    from app.services.mobile_training_generation import service
    app, user_ids, monkeypatch = pg_generation_app
    barrier = threading.Barrier(2)
    calls = []
    guard = threading.Lock()

    def candidate(*args, **kwargs):
        with guard:
            calls.append(threading.get_ident())
        barrier.wait(timeout=20)
        return SimpleNamespace(document=_document(), overall_score=8.5)

    monkeypatch.setattr(service, "generate_training_plan_candidate", candidate)
    outcomes = {}
    threads = [threading.Thread(
        target=_run,
        args=(app, user_id, _request(), "pg-generation-shared-key", outcomes, index),
        daemon=True) for index, user_id in enumerate(user_ids)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads), outcomes
    assert [outcomes[index][0] for index in range(2)] == ["ok", "ok"]
    assert len(calls) == 2
    assert outcomes[0][2] != outcomes[1][2]


def test_same_owner_different_keys_cannot_create_two_plans(pg_generation_app):
    app, user_ids, monkeypatch = pg_generation_app
    entered, release, calls = _install_blocking_provider(monkeypatch)
    outcomes = {}
    first = threading.Thread(
        target=_run, args=(app, user_ids[0], _request(), "pg-generation-key-3", outcomes, 0),
        daemon=True)
    first.start()
    assert entered.wait(timeout=20)
    second = threading.Thread(
        target=_run, args=(app, user_ids[0], _request(), "pg-generation-key-4", outcomes, 1),
        daemon=True)
    second.start()
    second.join(timeout=10)
    assert not second.is_alive(), "second key blocked behind provider"
    release.set()
    first.join(timeout=20)

    _assert_one_success(app, user_ids[0], outcomes, calls)


def test_successful_replay_detonates_provider_and_keeps_one_plan(pg_generation_app):
    from app.extensions import db
    from app.models import TrainingPlan, TrainingPlanGenerationOperation, User
    from app.services.mobile_training_generation import service
    app, user_ids, monkeypatch = pg_generation_app
    calls = []
    monkeypatch.setattr(
        service, "generate_training_plan_candidate",
        lambda *args, **kwargs: (
            calls.append("provider") or
            SimpleNamespace(document=_document(), overall_score=8.5)))
    outcomes = {}
    _run(app, user_ids[0], _request(), "pg-generation-key-5", outcomes, 0)
    monkeypatch.setattr(
        service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider called on replay"))
    _run(app, user_ids[0], _request(), "pg-generation-key-5", outcomes, 1)

    assert outcomes[0][0:2] == ("ok", False)
    assert outcomes[1][0:2] == ("ok", True)
    assert outcomes[0][2] == outcomes[1][2]
    assert calls == ["provider"]
    with app.app_context():
        assert TrainingPlan.query.filter_by(user_id=user_ids[0]).count() == 1
        assert TrainingPlanGenerationOperation.query.filter_by(
            user_id=user_ids[0]).count() == 1


def test_generated_crash_recovery_persists_without_provider(pg_generation_app):
    import json
    from app.extensions import db
    from app.models import TrainingPlanGenerationOperation, User
    from app.services.mobile_training_generation import service, store
    app, user_ids, monkeypatch = pg_generation_app
    request = _request()
    with app.app_context():
        operation = store.claim(
            user_ids[0], "pg-generation-key-6", request.fingerprint)
        store.stage_candidate(
            operation.id, json.dumps(_document(), ensure_ascii=False), 8.5)
        operation_id = operation.id
        db.session.remove()

    monkeypatch.setattr(
        service, "generate_training_plan_candidate",
        lambda *args, **kwargs: pytest.fail("provider called during recovery"))
    outcomes = {}
    _run(app, user_ids[0], request, "pg-generation-key-6", outcomes, 0)

    assert outcomes[0][0:2] == ("ok", False)
    with app.app_context():
        operation = db.session.get(TrainingPlanGenerationOperation, operation_id)
        assert operation.status == "SUCCEEDED"
        assert operation.training_plan_id == outcomes[0][2]
        assert operation.candidate_plan_data is None
