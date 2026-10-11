"""TD-01 PR1 on real PostgreSQL 16 (H-P1-H-P8).

SQLite cannot prove the production tie order (``public_id COLLATE "C"`` --
SQLite cannot even express it), ``NULLS LAST``, ``IN`` matching on the real
``VARCHAR(10)`` column, the statement count on the production dialect, naive
timestamp round trips, or owner isolation under the real planner. Gated like
every PG module: ``pg_concurrency`` marker + ``FITX_PG_CONCURRENCY_TEST=1`` +
reachable ``PG_TEST_DATABASE_URL``.

The G9 ``EXPLAIN (ANALYZE, BUFFERS)`` representative qualification is NOT part
of this suite and is not a PR1 merge gate (TD-01 §Performance and Storage).
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.exercise_history_support import (
    ANCHOR, ANCHOR_NOW, BENCH, SQUAT, candidate, day, persist, straight, utc_noon,
)
from tests.test_mobile_workout_sessions_pg import (
    _ENABLED, _PG_URL, _SKIP_REASON, LINEAGE, VERSION, _completion_kwargs, _plan_document,
    _require_pg,
)

pytestmark = [pytest.mark.pg_concurrency,
              pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)]

# Differ only in case and in '-'/'_'. Byte order (DESC): a > _ > B > -.
TIE_IDS = ("Btie", "atie", "-tie", "_tie")
REQUIRED_TIE_ORDER = ["atie", "_tie", "Btie", "-tie"]


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
        owner = User(username="eph_owner", email="eph_owner@example.com", cognito_sub="sub-eph-a")
        stranger = User(username="eph_other", email="eph_other@example.com", cognito_sub="sub-eph-b")
        db.session.add_all([owner, stranger])
        db.session.commit()
        ids = (owner.id, stranger.id)
    yield app, ids
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _read(user_id, exercise_id=SQUAT, when=ANCHOR_NOW):
    from app.services.exercise_performance_history import build_exercise_history
    from app.timeutil import audit_clock
    with audit_clock(when):
        return build_exercise_history(user_id, exercise_id)


def _refs(history):
    return [item.session_ref for item in history.occurrences]


class _Statements:
    def __enter__(self):
        from sqlalchemy import event
        from app.extensions import db
        self.statements = []
        self._listener = lambda *args: self.statements.append(args[2])
        event.listen(db.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import event
        from app.extensions import db
        event.remove(db.engine, "before_cursor_execute", self._listener)


def test_h_p1_exactly_one_select_on_workout_session(pg):
    import re
    app, (owner, _) = pg
    with app.app_context():
        persist(owner, candidate("a", day(-1)))
        persist(owner, candidate("bad", day(-2), checkpoint_data="{x"))
        with _Statements() as seen:
            history = _read(owner)
        assert history.state == "available_with_gaps"
        assert len(seen.statements) == 1
        statement = seen.statements[0]
        assert statement.lstrip().upper().startswith("SELECT")
        assert set(re.findall(r'\bFROM\s+"?(\w+)', statement, re.IGNORECASE)) == {"workout_session"}
        assert "JOIN" not in statement.upper()
        assert set(re.findall(r'"?(\w+)"?\.\w+', statement)) == {"workout_session"}
        assert 'workout_session.public_id COLLATE "C" DESC' in statement
        assert "NULLS LAST" in statement.upper()
        for absent in ("workout_log", "execution_context_data", "prescription_data", "->", "jsonb"):
            assert absent not in statement.lower(), absent


def test_h_p2_window_edges_are_57_istanbul_day_keys(pg):
    app, (owner, _) = pg
    with app.app_context():
        for ref, offset in (("minus57", -57), ("minus56", -56), ("today", 0), ("future", 1)):
            persist(owner, candidate(ref, day(offset)))
        # 21:30 UTC on anchor-57 is 00:30 Istanbul on anchor-56: a cross-date row
        # dated anchor-57 stays outside the window, never re-dated into it.
        edge = day(-57)
        persist(owner, candidate("late57", edge, completed_at=datetime(edge.year, edge.month, edge.day, 21, 30)))
        history = _read(owner)
        assert _refs(history) == ["today", "minus56"]
        assert history.coverage.scanned_sessions == 2
        assert (history.window_start, history.window_end) == (day(-56), ANCHOR)


def test_h_p3_null_completed_at_sorts_last_and_is_counted_excluded(pg):
    app, (owner, _) = pg
    from app.services.exercise_performance_history import queries
    with app.app_context():
        persist(owner, candidate("no-time", day(-1), completed_at=None))
        persist(owner, candidate("older", day(-9)))
        persist(owner, candidate("newer", day(-2)))
        rows, truncated = queries.load_candidates(owner, ANCHOR)
        assert [row.public_id for row in rows] == ["newer", "older", "no-time"]
        assert rows[-1].completed_at is None and not truncated
        assert rows[0].completed_at == utc_noon(day(-2))       # naive UTC round trip
        history = _read(owner)
        assert _refs(history) == ["newer", "older"]
        assert history.coverage.excluded_missing_completed_at == 1
        assert history.state == "available"


def test_h_p4_ties_break_by_public_id_byte_order_and_the_collation_matters(pg):
    app, (owner, _) = pg
    from sqlalchemy import text
    from app.extensions import db
    from app.services.exercise_performance_history import queries
    with app.app_context():
        for ref in TIE_IDS:
            persist(owner, candidate(ref, day(-3)))
        assert _refs(_read(owner)) == REQUIRED_TIE_ORDER
        rows, _ = queries.load_candidates(owner, ANCHOR)
        assert [row.public_id for row in rows] == REQUIRED_TIE_ORDER
        assert sorted(TIE_IDS, reverse=True) == REQUIRED_TIE_ORDER   # Python agrees

        collation = db.session.execute(text(
            "SELECT datcollate FROM pg_database WHERE datname = current_database()")).scalar()
        print(f"H-P4 datcollate={collation}")
        default_order = [ref for (ref,) in db.session.execute(text(
            "SELECT public_id FROM workout_session WHERE user_id = :owner "
            "ORDER BY completed_at DESC NULLS LAST, public_id DESC"), {"owner": owner})]
        if collation in ("C", "POSIX", "C.UTF-8", "C.utf8"):
            pytest.skip(f"database default collation {collation} is already byte order; "
                        "non-vacuity needs a locale-aware default (CI postgres:16 = en_US.utf8)")
        # Non-vacuity: without the explicit COLLATE "C" the database's own
        # collation produces a different order, so removing the collation from
        # queries.py fails the assertions above.
        assert default_order != REQUIRED_TIE_ORDER, (collation, default_order)


def test_h_p5_scan_bound_is_enforced_by_the_database(pg):
    app, (owner, _) = pg
    with app.app_context():
        for n in range(65):
            persist(owner, candidate(f"r{n:03d}", day(-(n % 50)), [(BENCH, straight(40.0, 8))],
                                     minute=n % 60, second=n // 60))
        history = _read(owner)
        assert history.coverage.scanned_sessions == 64
        assert history.coverage.scan_limit_reached
        assert history.state == "undetermined"


def test_h_p6_two_owners_with_interleaved_identical_rows_are_isolated(pg):
    app, (owner, stranger) = pg
    with app.app_context():
        for n in range(6):
            persist(owner, candidate(f"a{n}", day(-n), [(SQUAT, straight(60.0 + n, 8))], minute=1))
            persist(stranger, candidate(f"b{n}", day(-n), [(SQUAT, straight(100.0 + n, 3))], minute=2))
        persist(stranger, candidate("b-bad", day(-7), checkpoint_data="{broken"))
        mine, theirs = _read(owner), _read(stranger)
        assert _refs(mine) == [f"a{n}" for n in range(6)] and mine.state == "available"
        assert _refs(theirs) == [f"b{n}" for n in range(6)] and theirs.state == "available_with_gaps"
        assert {s.weight_kg for o in mine.occurrences for s in o.sets} == {60.0 + n for n in range(6)}
        assert {s.weight_kg for o in theirs.occurrences for s in o.sets} == {100.0 + n for n in range(6)}


class _CompletionClock(datetime):
    """``audit_clock`` pins Istanbul app time; completion stamps ``completed_at``
    from ``datetime.utcnow()``. Pin both to one instant (TD-01 R9)."""
    moment = None

    @classmethod
    def utcnow(cls):
        return cls.moment


def test_h_p7_full_lifecycle_through_the_real_services(pg, monkeypatch):
    import json
    from app.extensions import db
    from app.models import TrainingPlan
    from app.services import mobile_training
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_completion import service as completion_service
    from app.timeutil import audit_clock

    app, (owner, stranger) = pg
    when = ANCHOR_NOW
    _CompletionClock.moment = when.astimezone(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(completion_service, "datetime", _CompletionClock)
    with app.app_context():
        slot = when.weekday()
        db.session.add(TrainingPlan(
            user_id=owner, plan_data=json.dumps(_plan_document(slot), ensure_ascii=False),
            score=8.0, created_at=datetime(2026, 7, 1, 8, 30),
            lineage_id=LINEAGE, mutation_version=VERSION))
        db.session.commit()
        secret = app.config["SECRET_KEY"]
        reference = mobile_training.workout_ref(secret, owner, LINEAGE, VERSION, slot)
        progress = {"current_exercise_index": 0, "elapsed_seconds": 900, "exercises": [
            {"exercise_id": SQUAT, "sets": [
                {"index": 0, "completed": True, "reps": 8, "weight_kg": 60},
                {"index": 1, "completed": True, "reps": None, "weight_kg": 0},
                {"index": 2, "completed": False, "reps": 8, "weight_kg": 62.5}]}]}
        with audit_clock(when):
            ref = sessions.start(owner, secret, reference).payload["session"]["session_ref"]
            assert _read(owner).state == "no_history"     # ACTIVE is not history
            sessions.checkpoint(owner, secret, ref, "eph-pr1-key-1", 0,
                                lambda allowed: sessions.parse_checkpoint(progress, allowed))
            done = sessions.complete(owner, ref, 1, **_completion_kwargs())
            assert done.payload["completion"]["outcome"] == "created"
        history = _read(owner, when=when + timedelta(hours=1))
        assert history.state == "available"
        (occurrence,) = history.occurrences
        assert (occurrence.session_ref, occurrence.checkpoint_revision, occurrence.workout_date) == (
            ref, 1, ANCHOR)
        assert occurrence.completed_at == _CompletionClock.moment
        assert [(s.index, s.reps, s.weight_kg) for s in occurrence.sets] == [(0, 8, 60.0), (1, None, 0.0)]
        assert _read(stranger, when=when + timedelta(hours=1)).state == "no_history"


def test_h_p8_corruption_written_out_of_band_degrades_coverage(pg):
    app, (owner, _) = pg
    from sqlalchemy import text
    from app.extensions import db
    with app.app_context():
        persist(owner, candidate("only", day(-1)))
        db.session.execute(text("UPDATE workout_session SET checkpoint_data = '{broken' "
                                "WHERE public_id = 'only'"))
        db.session.commit()
        history = _read(owner)
        assert history.state == "undetermined"
        assert [m.reason for m in history.coverage.unavailable] == ["checkpoint_invalid"]
        persist(owner, candidate("fine", day(-4)))
        db.session.execute(text("UPDATE workout_session SET checkpoint_data = '' "
                                "WHERE public_id = 'fine'"))
        db.session.commit()
        persist(owner, candidate("clean", day(-6)))
        history = _read(owner)
        assert history.state == "available_with_gaps" and _refs(history) == ["clean"]
        assert [m.reason for m in history.coverage.unavailable] == ["checkpoint_invalid"] * 2
