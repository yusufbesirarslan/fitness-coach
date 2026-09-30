"""Opt-in PostgreSQL proof for quest/challenge commit semantics (triage 2026-09-30).

Why this is PostgreSQL-only. SQLite (pysqlite) cannot prove either property: when a
SAVEPOINT is the first statement of a transaction, releasing it COMMITS the outer
transaction, so the challenge increment ``record_event`` stages inside
``begin_nested`` is durable on SQLite even if nothing ever commits. Finding #1 is
therefore invisible to the SQLite suite and only real PostgreSQL distinguishes
"committed" from "staged and rolled back at request teardown".

  #1  ``complete_quest_for_user`` must commit the challenge progress staged ahead of
      the daily-quest short-circuit, or per-event challenges ("log 10 meals") advance
      at most once per day.
  #5  the water ``quest_fired`` claim and its award share ONE transaction; the claim
      keeps its row lock until that commit, so a concurrent second claim still loses.
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

DAY_KEY = "2026-09-30"


@pytest.fixture
def pg_app():
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
    from app.models import Challenge, DailyQuest, User, WaterLog

    app = Flask("quest-challenge-commit-pg")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-quest-challenge-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="pg-quest", email="pg-quest@example.invalid",
                    cognito_sub="pg-quest-sub")
        db.session.add(user)
        db.session.add(DailyQuest(title="Meal", description="x", points_reward=20,
                                  quest_type="meal_logged", is_active=True))
        db.session.add(DailyQuest(title="Water", description="x", points_reward=10,
                                  quest_type="water_logged", is_active=True))
        db.session.add(Challenge(
            code="weekly_meals", title="Meals", description="10 meals",
            category="nutrition", metric="meal_logged", target_value=10,
            xp_reward=100, badge_code=None, challenge_type="global",
            period_type="weekly", is_active=True))
        db.session.add(Challenge(
            code="weekly_water", title="Water", description="5 days",
            category="hydration", metric="water_logged", target_value=5,
            xp_reward=75, badge_code=None, challenge_type="global",
            period_type="weekly", is_active=True))
        db.session.flush()
        db.session.add(WaterLog(user_id=user.id, date_key=DAY_KEY, count=3))
        db.session.commit()
        user_id = user.id
    try:
        yield app, user_id
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _progress(app, user_id, code):
    """Progress as a SEPARATE connection sees it — i.e. only what was COMMITTED."""
    from app.extensions import db
    with app.app_context():
        with db.engine.connect() as connection:
            return connection.execute(sa.text(
                "SELECT p.progress FROM user_challenge_progress p "
                "JOIN challenge c ON c.id = p.challenge_id "
                "WHERE p.user_id = :u AND c.code = :code"),
                {"u": user_id, "code": code}).scalar()


def _quest_fired(app, user_id):
    from app.extensions import db
    with app.app_context():
        with db.engine.connect() as connection:
            return connection.execute(sa.text(
                "SELECT quest_fired FROM water_log WHERE user_id = :u"),
                {"u": user_id}).scalar()


def test_every_meal_is_committed_to_the_weekly_challenge_not_only_the_first(pg_app):
    from app.extensions import db
    from app.services.gamification import complete_quest_for_user
    app, user_id = pg_app

    with app.app_context():
        results = [complete_quest_for_user(user_id, "meal_logged") for _ in range(4)]
        db.session.remove()                      # request teardown: drops anything uncommitted

    assert results[0] is not None and results[1:] == [None, None, None]
    assert _progress(app, user_id, "weekly_meals") == 4


def test_weekly_meals_challenge_completes_within_one_day_and_pays_out_once(pg_app):
    from app.extensions import db
    from app.models import User
    from app.services.gamification import complete_quest_for_user
    app, user_id = pg_app

    with app.app_context():
        for _ in range(12):                      # two past the target
            complete_quest_for_user(user_id, "meal_logged")
        db.session.remove()

    assert _progress(app, user_id, "weekly_meals") == 10   # frozen once completed
    with app.app_context():
        # daily quest 20 + challenge 100, each exactly once.
        assert db.session.get(User, user_id).rank_points == 120


def test_quest_claim_and_award_are_one_transaction_and_roll_back_together(
        pg_app, monkeypatch):
    from app.blueprints.training import _claim_water_funnel_for_today
    from app.extensions import db
    from app.services.gamification import complete_quest_for_user
    app, user_id = pg_app

    with app.app_context():
        assert _claim_water_funnel_for_today(user_id, DAY_KEY) is True
        # The claim has NOT committed on its own: no other connection can see it.
        assert _quest_fired(app, user_id) is False

        real_commit = db.session.commit

        def failing_commit():
            raise RuntimeError("award commit failed")
        monkeypatch.setattr(db.session, "commit", failing_commit)
        assert complete_quest_for_user(user_id, "water_logged") is None
        monkeypatch.setattr(db.session, "commit", real_commit)

    assert _quest_fired(app, user_id) is False            # claim rolled back with the award
    assert _progress(app, user_id, "weekly_water") is None  # nothing half-applied

    with app.app_context():                               # the retry succeeds end to end
        assert _claim_water_funnel_for_today(user_id, DAY_KEY) is True
        assert complete_quest_for_user(user_id, "water_logged")["xp"] == 10
    assert _quest_fired(app, user_id) is True
    assert _progress(app, user_id, "weekly_water") == 1


def test_concurrent_water_claims_fire_exactly_once_while_claim_is_uncommitted(pg_app):
    from app.blueprints.training import _claim_water_funnel_for_today
    from app.extensions import db
    app, user_id = pg_app

    a_claimed = threading.Event()
    release_a = threading.Event()
    outcomes = {}

    def claimer_a():
        with app.app_context():
            outcomes["a"] = _claim_water_funnel_for_today(user_id, DAY_KEY)
            a_claimed.set()
            release_a.wait(10)                   # hold the uncommitted claim open
            db.session.commit()
            db.session.remove()

    def claimer_b():
        with app.app_context():
            outcomes["b"] = _claim_water_funnel_for_today(user_id, DAY_KEY)
            db.session.commit()
            db.session.remove()

    ta = threading.Thread(target=claimer_a)
    ta.start()
    assert a_claimed.wait(10)
    tb = threading.Thread(target=claimer_b)
    tb.start()
    tb.join(0.5)
    assert tb.is_alive(), "B must block on A's row lock until A commits"
    release_a.set()
    ta.join(10)
    tb.join(10)

    assert outcomes == {"a": True, "b": False}            # one winner, never two
