"""Tests for the boot-time DB initialisation (app/db_init.py) and CLI commands.

Gerçek boot yolu: create_all + quest tohumlama + Alembic stamp'ın in-memory
SQLite'ta uçtan uca çalıştığını ve idempotent olduğunu doğrular.

    python -m pytest tests/test_db_init.py -v
"""
from pathlib import Path

import pytest


def test_db_init_contains_no_schema_trigger_ddl():
    source = (Path(__file__).resolve().parents[1] / "app" / "db_init.py").read_text(
        encoding="utf-8"
    )

    assert "CREATE OR REPLACE FUNCTION calc_activity_calories" not in source
    assert "CREATE TRIGGER trg_calc_activity" not in source


def test_fresh_init_stamps_trigger_predecessor_then_upgrades(monkeypatch):
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    calls = []

    import flask_migrate
    from sqlalchemy import inspect

    from app import create_app
    from app.extensions import db

    def record_stamp(revision="head", **_kwargs):
        calls.append(
            ("stamp", revision, inspect(db.engine).has_table("daily_activity"))
        )

    def record_upgrade(revision="head", **_kwargs):
        from app.models import DailyQuest
        calls.append(("upgrade", revision, DailyQuest.query.count()))

    monkeypatch.setattr(flask_migrate, "stamp", record_stamp)
    monkeypatch.setattr(flask_migrate, "upgrade", record_upgrade)

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        try:
            assert calls == [
                ("stamp", "aa11bb22cc33", True),
                ("upgrade", "head", 0),
            ]
        finally:
            db.session.remove()
            db.drop_all()


def test_fresh_init_upgrade_failure_is_fatal_by_default(monkeypatch):
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_DB_UPGRADE_FAIL_OPEN", raising=False)

    import flask_migrate

    from app import create_app
    from app.extensions import db

    monkeypatch.setattr(flask_migrate, "stamp", lambda **_kwargs: None)

    def fail_upgrade(**_kwargs):
        raise RuntimeError("fresh migration failed")

    monkeypatch.setattr(flask_migrate, "upgrade", fail_upgrade)

    flask_app = None
    try:
        with pytest.raises(RuntimeError, match="fresh migration failed"):
            flask_app = create_app()
    finally:
        if flask_app is not None:
            with flask_app.app_context():
                db.session.remove()
                db.drop_all()


def test_fresh_upgrade_failure_commits_no_seed_rows(monkeypatch):
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_DB_UPGRADE_FAIL_OPEN", raising=False)

    import flask_migrate

    from app import create_app
    from app.extensions import db

    commits = []
    monkeypatch.setattr(flask_migrate, "stamp", lambda **_kwargs: None)

    def fail_upgrade(**_kwargs):
        raise RuntimeError("fresh migration failed")

    monkeypatch.setattr(flask_migrate, "upgrade", fail_upgrade)
    monkeypatch.setattr(db.session, "commit", lambda: commits.append("commit"))

    with pytest.raises(RuntimeError, match="fresh migration failed"):
        create_app()

    assert commits == []


@pytest.fixture
def boot_app(monkeypatch):
    """FITX_SKIP_DB_INIT olmadan kurulan app — init_database gerçekten çalışır."""
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    from app import create_app
    from app.extensions import db

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        yield flask_app
        db.session.remove()
        db.drop_all()


def test_init_database_seeds_quests_and_stamps_alembic(boot_app):
    from sqlalchemy import inspect
    from app.extensions import db
    from app.models import DailyQuest

    quests = DailyQuest.query.all()
    quest_types = {q.quest_type for q in quests}
    assert {"login", "workout_logged", "suggestion_sent", "supplement_added",
            "meal_logged"} <= quest_types

    # meal_logged tek tanım (mükerrer 20/15 değil) — ödül deterministik olmalı.
    meal_quests = [q for q in quests if q.quest_type == "meal_logged"]
    assert len(meal_quests) == 1
    assert meal_quests[0].points_reward == 20

    # Taze DB boot'ta Alembic zincirine damgalanır (manuel stamp gerekmez).
    assert inspect(db.engine).has_table("alembic_version")


def test_init_database_seeds_challenges(boot_app):
    # Boot yolu meydan okuma kataloğunu da tohumlar (Sprint 5 PR3, idempotent).
    from app.models import Challenge

    assert Challenge.query.count() >= 8
    assert Challenge.query.filter_by(code="weekly_workouts").first() is not None


def test_init_database_is_idempotent(boot_app):
    from app.db_init import init_database
    from app.models import DailyQuest

    before = DailyQuest.query.count()
    init_database(boot_app)  # ikinci boot — quest'ler çoğalmamalı
    assert DailyQuest.query.count() == before


def test_seed_quests_cli(boot_app):
    runner = boot_app.test_cli_runner()
    result = runner.invoke(args=["seed-quests"])
    assert "seeded successfully" in result.output


def test_weekly_reset_cli(boot_app, monkeypatch):
    runner = boot_app.test_cli_runner()
    result = runner.invoke(args=["weekly-reset"])
    assert "week_key" in result.output  # rollover sonucu yazdırılır
