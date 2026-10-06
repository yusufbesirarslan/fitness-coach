"""TI-03 on real PostgreSQL 16: bounded history query, owner isolation,
deterministic ordering, Istanbul day-key windows and the full projection.

SQLite cannot prove PostgreSQL ordering of ties/NULLs, ``IN``-list matching on
the real column type, or that the projection issues exactly two statements on
the production dialect. Gated like every PG module: ``pg_concurrency`` marker +
``FITX_PG_CONCURRENCY_TEST=1`` + reachable ``PG_TEST_DATABASE_URL``.
"""
import os
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_mobile_workout_sessions_pg import _ENABLED, _PG_URL, _SKIP_REASON, _require_pg
from tests.ti03_support import (
    ANCHOR, BENCH, MONDAY, SQUAT, context_entry, day, stored, straight, utc_noon,
)

pytestmark = [pytest.mark.pg_concurrency,
              pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)]


@pytest.fixture
def pg():
    _require_pg()
    os.environ["DATABASE_URL"] = _PG_URL
    os.environ["FITX_SKIP_DB_INIT"] = "1"
    from app import create_app
    from app.extensions import db
    from app.models import User

    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        owner = User(username="ti03_owner", email="ti03_owner@example.com", cognito_sub="sub-ti03-a")
        stranger = User(username="ti03_other", email="ti03_other@example.com", cognito_sub="sub-ti03-b")
        db.session.add_all([owner, stranger])
        db.session.commit()
        ids = (owner.id, stranger.id)
    yield app, ids
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _persist(user_id, row):
    from app.extensions import db
    from app.models import WorkoutSession
    db.session.add(WorkoutSession(
        public_id=row.ref, user_id=user_id, status=row.status, workout_date=row.workout_date,
        weekday_slot=row.weekday_slot, source="scheduled",
        started_at=utc_noon(ANCHOR), last_activity_at=utc_noon(ANCHOR),
        completed_at=row.completed_at if row.status == "completed" else None,
        checkpoint_revision=row.checkpoint_revision, checkpoint_data=row.checkpoint_data,
        execution_context_data=row.execution_context_data, prescription_data=row.prescription_data))
    db.session.commit()


def test_bounded_history_query_window_owner_and_status(pg):
    app, (owner, stranger) = pg
    from app.services.training_intelligence import queries
    with app.app_context():
        for offset in (-57, -56, -30, -1, 0, 1):
            _persist(owner, stored(f"o{offset:+03d}", day(offset), [(SQUAT, straight(60.0, 8))]))
        _persist(owner, replace(stored("active", day(-2), [(SQUAT, straight(60.0, 8))]), status="active"))
        _persist(owner, replace(stored("abandoned", day(-3), [(SQUAT, straight(60.0, 8))]),
                                status="abandoned"))
        _persist(stranger, stored("foreign", day(-4), [(SQUAT, straight(60.0, 8))]))
        rows, truncated = queries.load_history(owner, ANCHOR)
        assert [row.ref for row in rows] == ["o+00", "o-01", "o-30", "o-56"]
        assert not truncated
        assert {row.status for row in rows} == {"completed"}


def test_ties_and_null_completed_at_order_deterministically(pg):
    app, (owner, _) = pg
    from app.services.training_intelligence import queries
    with app.app_context():
        for ref in ("tie-b", "tie-c", "tie-a"):
            _persist(owner, stored(ref, day(-7), [(SQUAT, straight(60.0, 8))]))
        _persist(owner, replace(stored("no-time", day(-8), [(SQUAT, straight(60.0, 8))]),
                                completed_at=None))
        _persist(owner, stored("newer", day(-1), [(SQUAT, straight(60.0, 8))]))
        rows, _ = queries.load_history(owner, ANCHOR)
        assert [row.ref for row in rows] == ["newer", "tie-c", "tie-b", "tie-a", "no-time"]


def test_row_bound_is_enforced_by_the_database(pg):
    app, (owner, _) = pg
    from app.services.training_intelligence import queries
    from app.services.training_intelligence.models import MAX_HISTORY_ROWS
    with app.app_context():
        for n in range(MAX_HISTORY_ROWS + 5):
            _persist(owner, stored(f"r{n:03d}", day(-(n % 40)), [(SQUAT, straight(60.0, 8))],
                                   minute=n % 60))
        rows, truncated = queries.load_history(owner, ANCHOR)
        assert len(rows) == MAX_HISTORY_ROWS and truncated


def test_full_projection_on_postgres_is_exact_isolated_and_two_statements(pg):
    app, (owner, stranger) = pg
    from sqlalchemy import event
    from app.extensions import db
    from app.services import training_intelligence
    from app.services.workout_session.errors import SessionNotFound
    with app.app_context():
        ctx = [context_entry(SQUAT, i, rir="2") for i in range(3)]
        _persist(owner, stored("prev", day(-7), [(SQUAT, straight(60.0, 8, 8, 8)),
                                                 (BENCH, straight(40.0, 8, 8, 8))], context=ctx))
        _persist(owner, stored("cur", ANCHOR, [(SQUAT, straight(60.0, 8, 8, 8)),
                                               (BENCH, straight(40.0, 7, 8, 8))], context=ctx))
        for n in range(3):
            _persist(owner, stored(f"mon{n}", day(-10 - 7 * n), [(SQUAT, straight(60.0, 8))],
                                   slot=MONDAY))
        # A stranger's identical-looking history must not influence the owner.
        _persist(stranger, stored("s-prev", day(-7), [(BENCH, straight(40.0, 12, 12, 12))]))
        statements = []
        listener = lambda *args: statements.append(args[2].split()[0].upper())  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            first = training_intelligence.build_training_insight(owner, "cur")
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        assert statements == ["SELECT", "SELECT"]
        insight = first["training_insight"]
        assert (insight["state"], insight["kind"], insight["exercise_id"]) == (
            "available", "performance_declined", BENCH)
        reps = insight["evidence"][0]
        assert (reps["metric"], reps["previous"], reps["current"], reps["previous_session_ref"]) == (
            "reps", 24, 23, "prev")
        assert training_intelligence.build_training_insight(owner, "cur") == first
        with pytest.raises(SessionNotFound):
            training_intelligence.build_training_insight(owner, "s-prev")
        with pytest.raises(SessionNotFound):
            training_intelligence.build_training_insight(stranger, "cur")
        # Reads never wrote: no row changed revision, status or context.
        from app.models import WorkoutSession
        db.session.expire_all()
        row = WorkoutSession.query.filter_by(public_id="cur").one()
        assert (row.status, row.checkpoint_revision) == ("completed", 4)


def test_istanbul_day_keys_not_utc_timestamps_select_the_window(pg):
    app, (owner, _) = pg
    from app.services.training_intelligence import queries
    from datetime import datetime
    with app.app_context():
        # completed 21:30 UTC on 2026-08-05 = 00:30 Istanbul 2026-08-06 (first key)
        _persist(owner, stored("edge", day(-56), [(SQUAT, straight(60.0, 8))],
                               completed_at=datetime(2026, 8, 5, 21, 30)))
        _persist(owner, stored("outside", day(-57), [(SQUAT, straight(60.0, 8))],
                               completed_at=datetime(2026, 8, 5, 12, 0)))
        rows, _ = queries.load_history(owner, ANCHOR)
        assert [row.ref for row in rows] == ["edge"]
        assert queries.history_day_keys(ANCHOR)[0] == (ANCHOR - timedelta(days=56)).isoformat()
