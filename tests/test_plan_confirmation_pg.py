"""Opt-in PostgreSQL race: two workers confirm the same proposal.

SQLite serializes writers, so it cannot prove this. Mirrors
tests/test_plan_mutation_history_pg.py: barrier, no sleep, outcome invariant.
"""
import os
import threading

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


PROGRAM = (
    '{"program": ['
    '{"gun": "Pazartesi", "tip": "antrenman", "odak": "Itis", '
    '"egzersizler": [{"isim": "Bench Press", "set": 3, "tekrar": "8-12"}, '
    '{"isim": "Shoulder Press", "set": 4, "tekrar": "10-12"}]}, '
    '{"gun": "Sali", "tip": "dinlenme", "egzersizler": []}, '
    '{"gun": "Carsamba", "tip": "antrenman", "egzersizler": ['
    '{"isim": "Barbell Row", "set": 4, "tekrar": "6-10"}]}, '
    '{"gun": "Persembe", "tip": "dinlenme", "egzersizler": []}, '
    '{"gun": "Cuma", "tip": "antrenman", "egzersizler": ['
    '{"isim": "Squat", "set": 5, "tekrar": "5"}]}, '
    '{"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []}, '
    '{"gun": "Pazar", "tip": "dinlenme", "egzersizler": []}'
    '], "haftalik_ozet": {"yogunluk_skoru": 7}}'
)


@pytest.fixture
def pg_confirm_app():
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
    from app.models import TrainingPlan, User

    app = Flask("plan-confirmation-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-plan-confirmation-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        AI_COACH_PLAN_MUTATION_TOOLS_ENABLED=True,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="pg-confirm", email="pg-confirm@example.invalid",
                    cognito_sub="pg-confirm-sub")
        db.session.add(user)
        db.session.commit()
        db.session.add(TrainingPlan(
            user_id=user.id, score=8, plan_data=PROGRAM))
        db.session.commit()
        yield app, user.id
        db.session.remove()
        db.drop_all()


def test_two_workers_confirming_the_same_proposal_mutate_once(pg_confirm_app):
    app, user_id = pg_confirm_app
    from app.observability import assign_request_id
    from app.services import coach_plan_tools, plan_confirmation
    from app.models import PlanMutationRecord, TrainingPlan

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        coach_plan_tools.begin_turn()
        staged = coach_plan_tools.execute_plan_tool(
            user_id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Bench Press"})
        assert staged["status"] == "confirmation_required"

    barrier = threading.Barrier(2)
    results = [None, None]
    errors = []

    def worker(index):
        try:
            with app.test_request_context("/ask", method="POST"):
                assign_request_id()
                coach_plan_tools.begin_turn("evet")
                barrier.wait(timeout=10)
                results[index] = coach_plan_tools.execute_plan_tool(
                    user_id, coach_plan_tools.CONFIRM_TOOL, {})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    statuses = {payload["status"] for payload in results if payload}
    assert "applied" in statuses or "replayed" in statuses
    with app.app_context():
        assert PlanMutationRecord.query.filter_by(user_id=user_id).count() == 1
        plan = TrainingPlan.query.filter_by(user_id=user_id).one()
        assert "Bench Press" not in plan.plan_data
        assert plan_confirmation.get_pending(user_id) is None
