"""Opt-in PostgreSQL races through the real Pump Check service and ORM schema."""
import os
import threading
from datetime import datetime

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


@pytest.fixture
def pg_pump_app(monkeypatch):
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
    from app.services.mobile_pump_checks import service

    app = Flask("mobile-pump-check-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-pump-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            User(username=f"pg-pump-{n}", email=f"pg-pump-{n}@example.invalid",
                 cognito_sub=f"pg-pump-{n}-sub")
            for n in (1, 2)
        ]
        db.session.add_all(users)
        db.session.commit()
        user_ids = [user.id for user in users]

    lock = threading.Lock()
    calls = {"upload": 0, "analysis": 0}

    def upload(*args, user_id=None, **kwargs):
        with lock:
            calls["upload"] += 1
        return f"pump-checks/{user_id}/2026/08/private.jpg"

    def analyze(*args, **kwargs):
        with lock:
            calls["analysis"] += 1
        return {
            "summary": "Visible result.",
            "observations": [],
            "strengths": [],
            "focus_areas": [],
            "limitations": ["Single image."],
            "next_check_guidance": "Repeat framing.",
            "quality": "limited",
        }

    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(service.s3_helper, "upload_image", upload)
    monkeypatch.setattr(service, "analyze_image", analyze)
    try:
        yield app, user_ids, calls
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _command(description):
    from app.services.mobile_pump_checks.service import CreateCommand
    return CreateCommand(
        image_bytes=b"stable-image",
        media_type="image/jpeg",
        body_region="upper_body",
        environment="gym",
        description=description,
        captured_at=datetime(2026, 8, 13, 5, 0),
    )


def _race(app, users, commands):
    from app.extensions import db
    from app.services.mobile_pump_checks.service import (
        IdempotencyConflict,
        create_or_replay,
    )
    from app.services.mobile_pump_checks.identity import pump_check_id

    barrier = threading.Barrier(2)
    outcomes = {}

    def contender(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                row, created = create_or_replay(
                    users[index], "pump-race-key-0001", commands[index])
                outcomes[index] = (
                    "ok", created,
                    pump_check_id(app.config["SECRET_KEY"], users[index], row.id),
                )
            except IdempotencyConflict:
                outcomes[index] = ("conflict",)
            except Exception as error:
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


def test_same_user_same_command_converges_to_one_row_id_and_analysis(pg_pump_app):
    from app.models import PumpCheck
    app, user_ids, calls = pg_pump_app
    command = _command("same")
    outcomes = _race(app, [user_ids[0], user_ids[0]], [command, command])
    assert [outcomes[n][0] for n in range(2)] == ["ok", "ok"]
    assert sum(outcomes[n][1] for n in range(2)) == 1
    assert outcomes[0][2] == outcomes[1][2]
    with app.app_context():
        assert PumpCheck.query.filter_by(user_id=user_ids[0]).count() == 1
    assert calls == {"upload": 1, "analysis": 1}


def test_same_user_different_command_has_one_winner_and_conflict(pg_pump_app):
    from app.models import PumpCheck
    app, user_ids, _calls = pg_pump_app
    outcomes = _race(
        app, [user_ids[0], user_ids[0]], [_command("first"), _command("second")])
    assert sorted(outcome[0] for outcome in outcomes.values()) == ["conflict", "ok"]
    with app.app_context():
        assert PumpCheck.query.filter_by(user_id=user_ids[0]).count() == 1


def test_cross_user_same_key_is_independent(pg_pump_app):
    from app.models import PumpCheck
    app, user_ids, calls = pg_pump_app
    command = _command("same")
    outcomes = _race(app, user_ids, [command, command])
    assert [outcomes[n][0] for n in range(2)] == ["ok", "ok"]
    with app.app_context():
        assert PumpCheck.query.count() == 2
    assert calls == {"upload": 2, "analysis": 2}
