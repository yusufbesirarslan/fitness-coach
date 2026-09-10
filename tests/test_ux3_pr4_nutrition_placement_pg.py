"""PostgreSQL proof that a failed Nutrition sub-read stays isolated."""
import os

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


@pytest.fixture(scope="module", autouse=True)
def _postgres_database_url():
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip(
            "PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        os.environ["DATABASE_URL"] = previous


def test_failed_sql_subread_rolls_back_savepoint_and_later_reads_work(
    app, make_user, monkeypatch,
):
    from app.extensions import db
    from app.models import MealLog, NutritionPlan, Supplement, UserSession
    from app.services import plan_facts as pf
    from app.timeutil import app_today

    # Non-vacuity: this module exists ONLY to prove PostgreSQL aborted-transaction
    # recovery. SQLite does not abort a transaction on a failed statement, so a
    # green run against it would prove nothing at all.
    assert db.engine.dialect.name == "postgresql", (
        "PG isolation proof must run on real PostgreSQL, got "
        f"{db.engine.dialect.name}")

    user = make_user("nutrition-pg-isolation", profile_complete=True)
    db.session.add_all([
        UserSession(user_id=user.id, target_calories=2000),
        MealLog(
            user_id=user.id, ogun="Breakfast", yemekler="Oats", kalori=100.5,
            tarih=app_today().isoformat(),
        ),
        NutritionPlan(user_id=user.id, plan_data='{"name":"Plan"}'),
        Supplement(user_id=user.id, product_name="Creatine", brand="AxisAI"),
    ])
    db.session.commit()

    def abort_target_read(_user_id):
        with db.session.begin_nested():
            db.session.execute(sa.text("SELECT 1 / 0"))

    monkeypatch.setattr(pf, "_read_nutrition_target", abort_target_read)

    facts = pf.gather_plan_facts(user.id)

    assert facts.nutrition_state == "partial"
    assert facts.nutrition_target_state == "unavailable"
    assert facts.nutrition_intake_state == "available"
    assert facts.nutrition_consumed_calories == 101
    assert facts.nutrition_plan_state == "available"
    assert facts.has_nutrition_plan is True
    assert facts.supplements_state == "available"
    assert facts.read_ok is True
