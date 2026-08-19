"""Opt-in PostgreSQL proof for canonical Pump Check history paging and index.

SQLite answers "is the SQL correct?". Only PostgreSQL answers "does the planner
use the index we added, and does keyset paging hold under the real ordering
engine?" — the two questions PR4A's index decision rests on.
"""
import os
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


SECRET = "disposable-pg-history-secret"
INDEX = "ix_pump_check_user_captured"
BASE = datetime(2026, 8, 13, 5, 0, 0)


@pytest.fixture
def pg_history_app():
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
    from app.models import PumpCheck, User

    app = Flask("mobile-pump-check-history-pg")
    app.config.update(
        TESTING=True,
        SECRET_KEY=SECRET,
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            User(username=f"pg-history-{n}",
                 email=f"pg-history-{n}@example.invalid",
                 cognito_sub=f"pg-history-{n}-sub")
            for n in (1, 2)
        ]
        db.session.add_all(users)
        db.session.commit()
        owner_id, stranger_id = [user.id for user in users]

        rows = []
        # 400 owned rows, deliberately including a block that shares one exact
        # capture time so the tie-break is exercised, plus a legacy row and a
        # stranger's row.
        for index in range(400):
            captured = (
                BASE if index < 40 else BASE - timedelta(minutes=index))
            rows.append(PumpCheck(
                user_id=owner_id, public_id="pg%022d" % index,
                captured_at=captured, body_region="upper_body",
                analysis_status="completed",
                analysis={"quality": "sufficient"}))
        rows.append(PumpCheck(
            user_id=owner_id, public_id="pg%022d" % 900, captured_at=None))
        rows.append(PumpCheck(
            user_id=stranger_id, public_id="pg%022d" % 901,
            captured_at=BASE + timedelta(days=1), body_region="upper_body",
            analysis_status="completed"))
        db.session.add_all(rows)
        db.session.commit()
        db.session.execute(sa.text("ANALYZE pump_check"))
        db.session.commit()

        yield app, owner_id, stranger_id

        db.drop_all()


def _plan(db, user_id, cursor_position=None):
    from app.services.mobile_pump_checks import history

    query = history._query(user_id, cursor_position).limit(21)
    compiled = query.statement.compile(
        db.engine, compile_kwargs={"literal_binds": True})
    rows = db.session.execute(
        sa.text("EXPLAIN (ANALYZE, COSTS OFF) " + str(compiled))).fetchall()
    return "\n".join(str(row[0]) for row in rows)


def test_the_planner_uses_the_history_index_for_the_first_page(pg_history_app):
    from app.extensions import db

    app, owner_id, _ = pg_history_app
    with app.app_context():
        plan = _plan(db, owner_id)

    assert INDEX in plan
    assert "Seq Scan" not in plan
    # A Sort node would mean the index did not satisfy the ordering.
    assert "Sort" not in plan


def test_the_planner_uses_the_index_as_a_bound_for_a_seek_page(pg_history_app):
    """The seek must be an Index Cond, not a post-scan Filter.

    As a Filter, page N re-walks every earlier row, so deep paging degrades
    linearly.
    """
    from app.extensions import db
    from app.services.mobile_pump_checks import history

    app, owner_id, _ = pg_history_app
    with app.app_context():
        first = history.list_history(owner_id, SECRET, limit=20)
        position = history.decode_cursor(
            SECRET, owner_id, first["next_cursor"])
        plan = _plan(db, owner_id, position)

    assert INDEX in plan
    assert "Sort" not in plan
    assert "Rows Removed by Filter" not in plan


def test_a_full_postgresql_walk_repeats_and_skips_nothing(pg_history_app):
    from app.services.mobile_pump_checks import history

    app, owner_id, _ = pg_history_app
    with app.app_context():
        seen, pages, cursor = [], 0, None
        while True:
            page = history.list_history(
                owner_id, SECRET, limit=20, cursor=cursor)
            seen.extend(item["id"] for item in page["pump_checks"])
            pages += 1
            cursor = page["next_cursor"]
            if not cursor:
                break

    assert len(seen) == 400
    assert len(set(seen)) == 400
    assert pages == 20


def test_forty_identical_capture_times_page_deterministically(pg_history_app):
    """Identical timestamps are exactly where an unstable sort loses rows."""
    from app.services.mobile_pump_checks import history

    app, owner_id, _ = pg_history_app
    with app.app_context():
        first_walk, cursor = [], None
        while True:
            page = history.list_history(
                owner_id, SECRET, limit=7, cursor=cursor)
            first_walk.extend(item["id"] for item in page["pump_checks"])
            cursor = page["next_cursor"]
            if not cursor:
                break

        second_walk, cursor = [], None
        while True:
            page = history.list_history(
                owner_id, SECRET, limit=13, cursor=cursor)
            second_walk.extend(item["id"] for item in page["pump_checks"])
            cursor = page["next_cursor"]
            if not cursor:
                break

    # The tied block is the newest 40 rows; every walk must agree on its order.
    assert first_walk == second_walk
    assert len(set(first_walk[:40])) == 40


def test_postgresql_history_stays_owner_scoped_and_skips_legacy_rows(
        pg_history_app):
    from app.services.mobile_pump_checks import history

    app, owner_id, stranger_id = pg_history_app
    with app.app_context():
        owner_ids = []
        cursor = None
        while True:
            page = history.list_history(
                owner_id, SECRET, limit=50, cursor=cursor)
            owner_ids.extend(item["id"] for item in page["pump_checks"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        stranger = history.list_history(stranger_id, SECRET, limit=50)

    assert "pg%022d" % 901 not in owner_ids   # the stranger's row
    assert "pg%022d" % 900 not in owner_ids   # the legacy null-captured_at row
    assert [item["id"] for item in stranger["pump_checks"]] == ["pg%022d" % 901]


def test_the_index_exists_on_a_postgresql_schema(pg_history_app):
    from app.extensions import db

    app, _, _ = pg_history_app
    with app.app_context():
        indexes = {
            item["name"]: tuple(item["column_names"])
            for item in sa.inspect(db.engine).get_indexes("pump_check")
        }

    assert indexes[INDEX] == ("user_id", "captured_at", "id")
