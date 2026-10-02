"""NUTR-PR7 — opt-in PostgreSQL races for the native Nutrition write contracts.

SQLite admits one writer at a time, so it cannot prove a stale-write guarantee.
These run the REAL services concurrently on PostgreSQL 16 behind a barrier:

  P1  plan replacement, two clients from ONE revision  → exactly one wins
  P2  plan create (If-None-Match: *) from two clients  → exactly one plan
  P3  planned meal, same key + same command            → one MealLog, replay
  P4  hydration absolute set from one revision         → one wins, one stale
  P5  hydration first write of the day (no row)        → one wins, one row
  P6  supplement create from one cabinet revision      → exactly one row
  P7  supplement update from one item revision         → one wins, one stale

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \\
        python -m pytest -m pg_concurrency tests/test_nutrition_vnext_pr7_pg.py
"""
import json
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

SECRET = "disposable-pg-nutrition-pr7"
DOC_A = {"isim": "Plan A", "ogle": {"yemekler": ["Rice - 100g"], "kalori": 500,
                                    "protein": 30, "karb": 60, "yag": 10}}
DOC_B = {"isim": "Plan B", "aksam": {"yemekler": ["Fish - 150g"], "kalori": 600,
                                     "protein": 40, "karb": 20, "yag": 20}}


@pytest.fixture
def pg_app():
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            assert connection.dialect.name == "postgresql"
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    from flask import Flask
    from app.extensions import db
    from app.models import User

    app = Flask("nutrition-pr7-pg-race")
    app.config.update(TESTING=True, SECRET_KEY=SECRET, SQLALCHEMY_DATABASE_URI=url,
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="pg-pr7", email="pg-pr7@example.invalid",
                    cognito_sub="pg-pr7-sub")
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


def _race(app, operations):
    from app.extensions import db
    from app.services.nutrition_native import errors

    barrier = threading.Barrier(len(operations))
    outcomes = {}

    def contender(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                outcomes[index] = ("ok", operations[index]())
            except errors.NativeNutritionError as error:
                outcomes[index] = ("refused", error.code)
            except Exception as error:  # pragma: no cover - surfaced below
                outcomes[index] = ("unexpected", type(error).__name__, str(error)[:200])
            finally:
                db.session.remove()

    threads = [threading.Thread(target=contender, args=(i,), daemon=True)
               for i in range(len(operations))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), outcomes
    assert not [o for o in outcomes.values() if o[0] == "unexpected"], outcomes
    return outcomes


def _seed_plan(app, user_id, document=DOC_A):
    from app.extensions import db
    from app.models import NutritionPlan
    from app.services.nutrition_native import plan

    with app.app_context():
        row = NutritionPlan(user_id=user_id, plan_data=json.dumps(document), score=7.0)
        db.session.add(row)
        db.session.commit()
        return plan.plan_revision(SECRET, row)


def _proposal_body(app, user_id, document):
    from app.services.nutrition_native import plan

    with app.app_context():
        return {"plan": plan.native_document(document),
                "proposal_token": plan.issue_proposal_token(SECRET, user_id, document, 7.0)}


def _plans(app, user_id):
    from app.models import NutritionPlan

    with app.app_context():
        return [json.loads(r.plan_data)["isim"]
                for r in NutritionPlan.query.filter_by(user_id=user_id).all()]


def _save(app, user_id, precondition, body):
    from app.services.nutrition_native import plan

    return lambda: plan.save_plan(user_id, SECRET, precondition, body)["plan"]["name"]


def test_p1_two_replacements_from_one_revision_cannot_both_succeed(pg_app):
    from app.services.nutrition_native.preconditions import Precondition

    app, user_id = pg_app
    revision = _seed_plan(app, user_id, {"isim": "Old", **{k: v for k, v in DOC_A.items()
                                                           if k != "isim"}})
    precondition = Precondition(revision)
    outcomes = _race(app, [
        _save(app, user_id, precondition, _proposal_body(app, user_id, DOC_A)),
        _save(app, user_id, precondition, _proposal_body(app, user_id, DOC_B)),
    ])
    kinds = sorted(o[0] for o in outcomes.values())
    assert kinds == ["ok", "refused"], outcomes
    refused = next(o for o in outcomes.values() if o[0] == "refused")
    winner = next(o for o in outcomes.values() if o[0] == "ok")[1]
    assert refused[1] == "STALE_NUTRITION_PLAN"
    assert _plans(app, user_id) == [winner]


def test_p2_two_creates_leave_exactly_one_plan(pg_app):
    from app.services.nutrition_native.preconditions import CREATE, Precondition

    app, user_id = pg_app
    create = Precondition(CREATE)
    outcomes = _race(app, [
        _save(app, user_id, create, _proposal_body(app, user_id, DOC_A)),
        _save(app, user_id, create, _proposal_body(app, user_id, DOC_B)),
    ])
    assert sorted(o[0] for o in outcomes.values()) == ["ok", "refused"], outcomes
    assert len(_plans(app, user_id)) == 1


def test_p3_same_key_planned_meal_converges_to_one_meal(pg_app):
    from app.models import MealLog
    from app.services.nutrition_native import plan

    app, user_id = pg_app
    revision = _seed_plan(app, user_id)
    with app.app_context():
        from app.models import NutritionPlan
        row = NutritionPlan.query.filter_by(user_id=user_id).one()
        meal_id = plan.planned_meal_id(SECRET, user_id, row.id, "ogle")

    def log():
        meal, created = plan.log_planned_meal(user_id, SECRET, revision, meal_id,
                                              "pg-planned-key-01")
        return meal["id"], created

    outcomes = _race(app, [log, log, log])
    assert all(o[0] == "ok" for o in outcomes.values()), outcomes
    ids = {o[1][0] for o in outcomes.values()}
    created = [o[1][1] for o in outcomes.values()]
    assert len(ids) == 1 and created.count(True) == 1
    with app.app_context():
        assert MealLog.query.filter_by(user_id=user_id).count() == 1


def _water(app, user_id, revision, amount):
    from app.services.nutrition_native import hydration

    return lambda: hydration.set_today(user_id, SECRET, revision, amount)["hydration"]["amount"]


def _water_revision(app, user_id):
    from app.services.nutrition_native import hydration

    with app.app_context():
        return hydration.read_today(user_id, SECRET)["hydration"]["revision"]


def test_p4_two_devices_setting_water_from_one_state(pg_app):
    from app.models import WaterLog
    from app.services import hydration as service
    from app.timeutil import app_today

    app, user_id = pg_app
    with app.app_context():
        service.set_today_count(user_id, app_today().isoformat(), 2, lambda _c: None)
    revision = _water_revision(app, user_id)
    outcomes = _race(app, [_water(app, user_id, revision, 3),
                           _water(app, user_id, revision, 6)])
    assert sorted(o[0] for o in outcomes.values()) == ["ok", "refused"], outcomes
    winner = next(o for o in outcomes.values() if o[0] == "ok")[1]
    with app.app_context():
        assert [r.count for r in WaterLog.query.filter_by(user_id=user_id)] == [winner]


def test_p5_first_write_of_the_day_creates_one_row(pg_app):
    from app.models import WaterLog

    app, user_id = pg_app
    revision = _water_revision(app, user_id)
    outcomes = _race(app, [_water(app, user_id, revision, 1),
                           _water(app, user_id, revision, 4)])
    assert sorted(o[0] for o in outcomes.values()) == ["ok", "refused"], outcomes
    with app.app_context():
        assert WaterLog.query.filter_by(user_id=user_id).count() == 1


def test_p6_two_creates_from_one_cabinet_revision_make_one_row(pg_app):
    from app.models import Supplement
    from app.services.nutrition_native import supplements

    app, user_id = pg_app
    with app.app_context():
        revision = supplements.read_cabinet(user_id, SECRET)["cabinet"]["revision"]
    body = {"product_name": "Whey", "brand": "ON", "category": "protein", "is_public": False}

    def create():
        return supplements.create(user_id, SECRET, revision, body)["supplement"]["id"]

    outcomes = _race(app, [create, create])
    assert sorted(o[0] for o in outcomes.values()) == ["ok", "refused"], outcomes
    assert next(o for o in outcomes.values() if o[0] == "refused")[1] == \
        "STALE_SUPPLEMENT_CABINET"
    with app.app_context():
        assert Supplement.query.filter_by(user_id=user_id).count() == 1


def test_p7_two_updates_from_one_item_revision(pg_app):
    from app.extensions import db
    from app.models import Supplement
    from app.services.nutrition_native import supplements

    app, user_id = pg_app
    with app.app_context():
        row = Supplement(user_id=user_id, product_name="Zinc", brand="X")
        db.session.add(row)
        db.session.commit()
        token = supplements.supplement_id(SECRET, user_id, row.id)
        revision = supplements.supplement_revision(SECRET, row)

    def update(status):
        return lambda: supplements.update(user_id, SECRET, token, revision,
                                          {"status": status})["supplement"]["status"]

    outcomes = _race(app, [update("low_stock"), update("finished")])
    assert sorted(o[0] for o in outcomes.values()) == ["ok", "refused"], outcomes
    winner = next(o for o in outcomes.values() if o[0] == "ok")[1]
    with app.app_context():
        stored = Supplement.query.filter_by(user_id=user_id).one().status
    assert supplements.STATUS_TOKENS[stored] == winner
