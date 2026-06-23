"""Tests for the boot-time DB initialisation (app/db_init.py) and CLI commands.

Gerçek boot yolu: create_all + quest tohumlama + Alembic stamp'ın in-memory
SQLite'ta uçtan uca çalıştığını ve idempotent olduğunu doğrular.

    python -m pytest tests/test_db_init.py -v
"""
import pytest


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
