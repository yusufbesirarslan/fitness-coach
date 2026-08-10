"""Opt-in PostgreSQL races for canonical mobile LogFood idempotency."""
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


@pytest.fixture
def pg_log_food_app():
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
    from app.models import User

    app = Flask("mobile-log-food-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-log-food-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(
            username="pg-log-food", email="pg-log-food@example.invalid",
            cognito_sub="pg-log-food-sub")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    try:
        yield app, user_id
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _manual(description):
    from app.services.mobile_log_food import parse_command
    return parse_command({
        "kind": "manual",
        "description": description,
        "slot": "ogle",
        "nutrition": {
            "energy_kcal": 420,
            "protein_g": 25,
            "carbohydrate_g": 40,
            "fat_g": 14,
        },
    })


def _race(app, user_id, commands):
    from app.extensions import db
    from app.services.mobile_log_food import (
        IdempotencyConflict, log_food, response_meal,
    )

    barrier = threading.Barrier(2)
    outcomes = {}

    def contender(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                entry, created = log_food(
                    user_id, "pg-log-food-key-0001", commands[index])
                outcomes[index] = (
                    "ok", created,
                    response_meal(entry, app.config["SECRET_KEY"], user_id),
                    entry.idempotency_fingerprint,
                )
            except IdempotencyConflict:
                outcomes[index] = ("conflict",)
            except Exception as error:  # pragma: no cover - surfaced below
                outcomes[index] = ("unexpected", type(error).__name__)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=contender, args=(index,), daemon=True)
               for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), outcomes
    return outcomes


def test_concurrent_same_command_converges_to_one_row_and_identity(
        pg_log_food_app):
    from app.models import MealLog

    app, user_id = pg_log_food_app
    command = _manual("Same semantic command")
    outcomes = _race(app, user_id, [command, command])

    assert [outcomes[index][0] for index in range(2)] == ["ok", "ok"]
    assert sum(outcomes[index][1] for index in range(2)) == 1
    assert outcomes[0][2]["id"] == outcomes[1][2]["id"]
    assert outcomes[0][3] == outcomes[1][3]
    with app.app_context():
        assert MealLog.query.filter_by(user_id=user_id).count() == 1


def test_concurrent_different_commands_never_silently_replay(
        pg_log_food_app):
    from app.models import MealLog

    app, user_id = pg_log_food_app
    outcomes = _race(app, user_id, [
        _manual("First semantic command"),
        _manual("Second semantic command"),
    ])

    assert sorted(outcome[0] for outcome in outcomes.values()) == [
        "conflict", "ok"]
    with app.app_context():
        assert MealLog.query.filter_by(user_id=user_id).count() == 1
