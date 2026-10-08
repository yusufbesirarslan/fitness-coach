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


def _menu(app, user_id, *, quantity=1):
    from app.services import mobile_menu
    from app.services.mobile_log_food.menu_confirmation import parse_menu_confirmation
    secret = app.config['SECRET_KEY']
    token = mobile_menu.issue_item_proof(
        secret, user_id, analysis_id='pg-analysis', candidate_id='pg-candidate',
        name='PG Menu Dish', portion={'basis': 'serving', 'quantity': 1, 'stated_grams': None},
        nutrition={'energy_kcal': 420, 'protein_g': 25, 'carbohydrate_g': 40, 'fat_g': 14},
        source='llm', confidence=0.5)
    return parse_menu_confirmation(
        {'confirmation_token': token, 'quantity': quantity, 'slot': 'ogle', 'confirmed': True},
        secret, user_id)


@pytest.mark.parametrize('case', ['same', 'different', 'manual', 'provider'])
def test_menu_confirmation_unique_key_races(pg_log_food_app, monkeypatch, case):
    """Both independent PG sessions pass the empty preflight before INSERT.

    Barrier is at the read boundary, not a timer: every case actually exercises
    the database uniqueness arbiter and winner fingerprint comparison.
    """
    from app.models import MealLog
    from app.services import meal_idempotency
    from app.services.mobile_log_food import parse_command, service
    from app.services.mobile_log_food.commands import ManualNutritionSnapshot
    from decimal import Decimal
    from types import SimpleNamespace

    app, user_id = pg_log_food_app
    menu = _menu(app, user_id)
    if case == 'same':
        other = _menu(app, user_id, quantity=1.0)
    elif case == 'different':
        other = _menu(app, user_id, quantity=2)
    elif case == 'manual':
        other = _manual(menu.description)
    else:
        other = parse_command({'kind': 'provider_backed', 'provider': 'fatsecret',
                               'food_id': 'pg-food', 'serving_id': 'pg-serving',
                               'quantity': 1, 'slot': 'ogle', 'discovery_source': 'search'})
        monkeypatch.setattr(service, 'resolve_provider_food', lambda *args:
            SimpleNamespace(description=menu.description, nutrition=ManualNutritionSnapshot(
                Decimal(420), Decimal(25), Decimal(40), Decimal(14))))

    read_barrier = threading.Barrier(2)
    real_find = meal_idempotency.find_existing
    sessions = set()
    backend_pids = set()
    guard = threading.Lock()

    def find(user, key):
        from app.extensions import db
        entry = real_find(user, key)
        if entry is None:
            pid = db.session.execute(sa.text('SELECT pg_backend_pid()')).scalar_one()
            with guard:
                sessions.add(id(db.session()))
                backend_pids.add(pid)
            read_barrier.wait(timeout=10)
        return entry
    monkeypatch.setattr(meal_idempotency, 'find_existing', find)
    outcomes = _race(app, user_id, [menu, other])
    assert len(sessions) == len(backend_pids) == 2
    with app.app_context():
        row = MealLog.query.one()
        assert row.user_id == user_id
        assert row.idempotency_fingerprint in {service.semantic_fingerprint(menu), service.semantic_fingerprint(other)}
    if case == 'same':
        assert sorted(outcome[0] for outcome in outcomes.values()) == ['ok', 'ok']
        assert sum(outcome[1] for outcome in outcomes.values()) == 1
        assert outcomes[0][2]['id'] == outcomes[1][2]['id']
        assert outcomes[0][2]['revision'] == outcomes[1][2]['revision']
        assert outcomes[0][2]['source'] == 'menu_estimated'
    else:
        assert sorted(outcome[0] for outcome in outcomes.values()) == ['conflict', 'ok']
        winner = next(outcome for outcome in outcomes.values() if outcome[0] == 'ok')
        assert winner[1] is True
        assert row.idempotency_fingerprint == winner[3]
