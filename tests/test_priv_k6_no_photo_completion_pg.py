"""PRIV-K6-B1 on real PostgreSQL 16: photo-free completion exact-once.

SQLite admits one writer at a time, so it cannot show the arbitration this task
adds: the ``workout_completion_day`` claim under real multi-connection races, the
re-check taken under the (owner, Istanbul day) advisory lock, and the
migration's verify-or-create path against PostgreSQL's own catalog.

Same gating as every PG module here: the ``pg_concurrency`` marker AND
``FITX_PG_CONCURRENCY_TEST=1`` AND a reachable ``PG_TEST_DATABASE_URL`` naming a
DISPOSABLE database. Races are released by barriers/events, never sleeps; every
assertion is on persisted rows. Synthetic users only; no provider, no S3.
"""
import os
import threading
import time

import pytest
import sqlalchemy as sa

from tests.test_mobile_training_allowance_pg import (  # noqa: F401
    ROOT, _module, _run, migrate_engine,
)
from tests.test_mobile_workout_sessions_pg import (
    _ENABLED, _PG_URL, _SKIP_REASON, _make_pg_app, _race, _require_pg, _teardown,
)

pytestmark = pytest.mark.pg_concurrency

_STEP_TIMEOUT = 15
MIGRATION = "f9c6d0a1b2e3_workout_completion_day_claim.py"
needs_pg = pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)


def _no_photo():
    from app.services.workout_completion import PROOF_NONE
    return {"proof": PROOF_NONE, "base_xp": 10, "photo_bonus": 0,
            "activity_text": "Bugünkü antrenmanını tamamladı", "entry_path": "mobile_route"}


def _photo_command(user_id, today):
    from app.services.workout_completion import CompleteWorkoutCommand
    return CompleteWorkoutCommand(
        user_id=user_id, today=today, image_key=None, location_type="gym",
        description="pg mixed", valid=True, fallback=False, base_xp=10,
        photo_bonus=25, entry_path="route")


def _started(flask_app, user_id, reference):
    from app.services import mobile_workout_sessions as sessions
    with flask_app.app_context():
        return sessions.start(
            user_id, flask_app.config["SECRET_KEY"], reference
        ).payload["session"]["session_ref"]


def _complete_no_photo(user_id, session_ref, revision=0):
    from app.services import mobile_workout_sessions as sessions
    result = sessions.complete(user_id, session_ref, revision, **_no_photo())
    return result.status, result.replayed, result.payload["completion"]["outcome"]


def _rows(flask_app, user_id):
    from app.models import (
        WORKOUT_COMPLETION_MARKER, Activity, PumpCheck, User, WorkoutCompletionDay,
        WorkoutLog, WorkoutSession,
    )
    with flask_app.app_context():
        return {
            "claims": [(c.date_key, c.proof) for c in
                       WorkoutCompletionDay.query.filter_by(user_id=user_id).all()],
            "pump_checks": PumpCheck.query.filter_by(user_id=user_id).count(),
            "markers": WorkoutLog.query.filter_by(
                user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER).count(),
            "activities": Activity.query.filter_by(
                user_id=user_id, activity_type="workout_completed").count(),
            "xp": db_user_xp(User, user_id),
            "sessions": [s.status for s in
                         WorkoutSession.query.filter_by(user_id=user_id).all()],
        }


def db_user_xp(User, user_id):
    from app.extensions import db
    return db.session.get(User, user_id).rank_points or 0


def _day_lock_key(user_id, day):
    from app.services.workout_completion.queries import completion_day_lock_key
    return completion_day_lock_key(user_id, day)


def _wait_for_lock_waiters(engine, count):
    """Block until ``count`` sessions are waiting on an advisory lock."""
    deadline = time.monotonic() + _STEP_TIMEOUT
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            waiting = probe.execute(sa.text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
            )).scalar()
            if waiting >= count:
                return
            probe.rollback()
            time.sleep(0.02)
    raise AssertionError("contender never reached the day lock")


# -- K6-G: two photo-free attempts race ---------------------------------------

@needs_pg
def test_k6g_two_concurrent_no_photo_completions_produce_one_completion():
    _require_pg()
    flask_app, user_id, reference = _make_pg_app()
    session_ref = _started(flask_app, user_id, reference)
    try:
        results = _race(flask_app, user_id, {
            "a": lambda: _complete_no_photo(user_id, session_ref),
            "b": lambda: _complete_no_photo(user_id, session_ref),
        })
        assert sorted(r for _, r in results.values()) == [
            (200, False, "created"), (200, True, "already_completed")], results
        rows = _rows(flask_app, user_id)
        from app.timeutil import app_today
        assert rows["claims"] == [(app_today().isoformat(), "none")]
        assert rows["pump_checks"] == 0
        assert rows["markers"] == 1 and rows["activities"] == 1
        assert rows["xp"] == 10
        assert rows["sessions"] == ["completed"]
    finally:
        _teardown(flask_app)


# -- K6-H: photo completion and photo-free completion race --------------------

@needs_pg
@pytest.mark.parametrize("attempt", range(3))
def test_k6h_mixed_mode_race_yields_one_completion_and_one_reward(attempt):
    _require_pg()
    from app.services.workout_completion import complete_workout
    from app.timeutil import app_today

    flask_app, user_id, reference = _make_pg_app()
    session_ref = _started(flask_app, user_id, reference)
    try:
        results = _race(flask_app, user_id, {
            "no_photo": lambda: _complete_no_photo(user_id, session_ref)[2],
            "photo": lambda: complete_workout(
                _photo_command(user_id, app_today())).outcome.value,
        })
        outcomes = sorted(r for _, r in results.values())
        assert outcomes == ["already_completed", "created"], results
        rows = _rows(flask_app, user_id)
        assert len(rows["claims"]) == 1
        (_, proof), = rows["claims"]
        if proof == "photo":
            assert rows["pump_checks"] == 1 and rows["xp"] == 35
        else:
            assert rows["pump_checks"] == 0 and rows["xp"] == 10
        assert rows["markers"] == 1 and rows["activities"] == 1
        # Whichever won, the linked session is never left ACTIVE.
        assert rows["sessions"] == ["completed"]
    finally:
        _teardown(flask_app)


@needs_pg
def test_k6h_locked_recheck_refuses_a_day_claimed_while_waiting_for_the_lock():
    """The negative control for C6: a HISTORICAL claim (PumpCheck.date_key, no
    WorkoutCompletionDay row) commits while the photo-free writer waits on the
    day lock. No unique constraint spans the two tables, so only the re-check
    taken under the lock can refuse; without it the day would complete twice."""
    _require_pg()
    from app.extensions import db
    from app.timeutil import app_today

    flask_app, user_id, reference = _make_pg_app()
    session_ref = _started(flask_app, user_id, reference)
    today = app_today()
    holder = None
    try:
        with flask_app.app_context():
            engine = db.engine
            holder = engine.connect()
            tx = holder.begin()
            holder.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"),
                           {"k": _day_lock_key(user_id, today)})
        result = {}

        def _contender():
            with flask_app.app_context():
                try:
                    result["value"] = _complete_no_photo(user_id, session_ref)
                except Exception as exc:  # pragma: no cover - surfaced below
                    result["value"] = ("raise", type(exc).__name__)
                finally:
                    db.session.remove()

        thread = threading.Thread(target=_contender)
        thread.start()
        _wait_for_lock_waiters(engine, 1)
        holder.execute(sa.text(
            "INSERT INTO pump_check (user_id, image_key, location_type, description, "
            "visibility, shared_friend_ids, likes_count, comments_count, reposts_count, "
            "valid, fallback, date_key, created_at) VALUES (:u, NULL, 'gym', 'legacy', "
            "'private', '[]', 0, 0, 0, true, false, :d, now())"),
            {"u": user_id, "d": today.isoformat()})
        tx.commit()
        thread.join(_STEP_TIMEOUT)
        assert result["value"] == (200, True, "already_completed"), result
        rows = _rows(flask_app, user_id)
        assert rows["claims"] == []
        assert rows["pump_checks"] == 1 and rows["markers"] == 0 and rows["xp"] == 0
        # Reconciled, not left ACTIVE.
        assert rows["sessions"] == ["completed"]
    finally:
        if holder is not None:
            holder.close()
        _teardown(flask_app)


@needs_pg
def test_k6h_photo_writer_waiting_on_the_lock_loses_to_a_photo_free_claim():
    _require_pg()
    from app.extensions import db
    from app.services.workout_completion import complete_workout
    from app.timeutil import app_today

    flask_app, user_id, _ = _make_pg_app()
    today = app_today()
    holder = None
    try:
        with flask_app.app_context():
            engine = db.engine
            holder = engine.connect()
            tx = holder.begin()
            holder.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"),
                           {"k": _day_lock_key(user_id, today)})
        result = {}

        def _contender():
            with flask_app.app_context():
                try:
                    result["value"] = complete_workout(
                        _photo_command(user_id, today)).outcome.value
                finally:
                    db.session.remove()

        thread = threading.Thread(target=_contender)
        thread.start()
        _wait_for_lock_waiters(engine, 1)
        holder.execute(sa.text(
            "INSERT INTO workout_completion_day (user_id, date_key, proof, created_at) "
            "VALUES (:u, :d, 'none', now())"), {"u": user_id, "d": today.isoformat()})
        tx.commit()
        thread.join(_STEP_TIMEOUT)
        assert result["value"] == "already_completed"
        rows = _rows(flask_app, user_id)
        assert rows["claims"] == [(today.isoformat(), "none")]
        assert rows["pump_checks"] == 0 and rows["xp"] == 0 and rows["markers"] == 0
    finally:
        if holder is not None:
            holder.close()
        _teardown(flask_app)


# -- K6-L: session start versus photo-free completion -------------------------

@needs_pg
def test_k6l_start_racing_a_photo_free_completion_never_leaves_a_new_active_row():
    _require_pg()
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    session_ref = _started(flask_app, user_id, reference)

    def _start():
        try:
            return sessions.start(user_id, secret, reference).status
        except Exception as exc:  # noqa: BLE001 - the refusal is an outcome
            return type(exc).__name__

    try:
        for _ in range(3):
            results = _race(flask_app, user_id, {
                "complete": lambda: _complete_no_photo(user_id, session_ref)[2],
                "start": _start,
            })
            assert results["start"][1] in (200, "WorkoutNotStartable"), results
        rows = _rows(flask_app, user_id)
        assert rows["sessions"] == ["completed"]
        assert len(rows["claims"]) == 1
    finally:
        _teardown(flask_app)


@needs_pg
def test_k6l_start_after_a_photo_free_completion_commits_is_refused():
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session.errors import WorkoutNotStartable

    flask_app, user_id, reference = _make_pg_app()
    session_ref = _started(flask_app, user_id, reference)
    try:
        with flask_app.app_context():
            assert _complete_no_photo(user_id, session_ref)[2] == "created"
        with flask_app.app_context():
            with pytest.raises(WorkoutNotStartable):
                sessions.start(user_id, flask_app.config["SECRET_KEY"], reference)
        assert _rows(flask_app, user_id)["sessions"] == ["completed"]
    finally:
        _teardown(flask_app)


# -- K6-J: forced failure rolls back every required write ---------------------

@needs_pg
@pytest.mark.parametrize("seam", ["award_xp", "log_activity", "mark_session_completed"])
def test_k6j_forced_failure_rolls_back_the_whole_photo_free_completion(monkeypatch, seam):
    _require_pg()
    from app.services.workout_completion import service as completion_service

    flask_app, user_id, reference = _make_pg_app()
    session_ref = _started(flask_app, user_id, reference)

    def _boom(*args, **kwargs):
        raise RuntimeError("forced required-write failure")

    monkeypatch.setattr(completion_service, seam, _boom)
    try:
        with flask_app.app_context():
            with pytest.raises(RuntimeError):
                _complete_no_photo(user_id, session_ref)
        rows = _rows(flask_app, user_id)
        assert rows == {"claims": [], "pump_checks": 0, "markers": 0,
                        "activities": 0, "xp": 0, "sessions": ["active"]}
    finally:
        _teardown(flask_app)


# -- C7: only a genuine day-claim violation is a replay ------------------------

@needs_pg
def test_c7_only_the_day_claim_unique_violation_classifies_as_replay():
    _require_pg()
    from sqlalchemy.exc import IntegrityError

    from app.extensions import db
    from app.services.workout_completion.queries import is_completion_day_violation

    flask_app, user_id, _ = _make_pg_app()
    insert = ("INSERT INTO workout_completion_day (user_id, date_key, proof, created_at) "
              "VALUES (:u, :d, :p, now())")
    cases = {
        "duplicate_day": ({"u": user_id, "d": "2026-10-18", "p": "none"}, True),
        "proof_check": ({"u": user_id, "d": "2026-10-19", "p": "bogus"}, False),
        "missing_owner": ({"u": user_id + 999, "d": "2026-10-20", "p": "none"}, False),
        "null_day": ({"u": user_id, "d": None, "p": "none"}, False),
    }
    try:
        with flask_app.app_context():
            db.session.execute(sa.text(insert), {"u": user_id, "d": "2026-10-18", "p": "photo"})
            db.session.commit()
            for name, (params, expected) in cases.items():
                with pytest.raises(IntegrityError) as caught:
                    db.session.execute(sa.text(insert), params)
                    db.session.flush()
                db.session.rollback()
                assert is_completion_day_violation(caught.value) is expected, name
    finally:
        _teardown(flask_app)


# -- migration: verify-or-create, negative controls, downgrade, no backfill ----

USER_FK = 'REFERENCES "user"(id) ON DELETE CASCADE'
CHECK = "CONSTRAINT ck_workout_completion_day_proof CHECK (proof IN ('photo', 'none'))"


def _claim_table(*, id_="SERIAL PRIMARY KEY", user_id=f"INTEGER NOT NULL {USER_FK}",
                 date_key="VARCHAR(10) NOT NULL", proof="VARCHAR(8) NOT NULL",
                 created_at="TIMESTAMP WITHOUT TIME ZONE NOT NULL", extra="",
                 unique="CONSTRAINT uq_workout_completion_day UNIQUE (user_id, date_key)",
                 check=CHECK, after=()):
    """The exact migration DDL, with one part replaced per negative control."""
    def make(conn):
        parts = [f"id {id_}", f"user_id {user_id}", f"date_key {date_key}",
                 f"proof {proof}", f"created_at {created_at}", extra, unique, check]
        conn.exec_driver_sql(
            f"CREATE TABLE workout_completion_day ({', '.join(p for p in parts if p)})")
        for statement in after:
            conn.exec_driver_sql(statement)
    return make


def _create_all_shape(conn):
    from app.models import WorkoutCompletionDay
    WorkoutCompletionDay.__table__.create(conn)


ALTER = "ALTER TABLE workout_completion_day "
MIGRATION_CASES = {
    "create_all_shape": (_create_all_shape, None),
    "exact_ddl": (_claim_table(), None),
    "date_key_wider": (_claim_table(date_key="VARCHAR(16) NOT NULL"), "columns"),
    "date_key_char": (_claim_table(date_key="CHAR(10) NOT NULL"), "columns"),
    "proof_text": (_claim_table(proof="TEXT NOT NULL"), "columns"),
    "owner_nullable": (_claim_table(user_id=f"INTEGER {USER_FK}"), "columns"),
    "created_at_timestamptz": (
        _claim_table(created_at="TIMESTAMP WITH TIME ZONE NOT NULL"), "columns"),
    "extra_column": (_claim_table(extra="pump_check_id INTEGER"), "columns"),
    "proof_server_default": (
        _claim_table(proof="VARCHAR(8) NOT NULL DEFAULT 'none'"), "column defaults"),
    "missing_day_unique": (_claim_table(unique=""), "day uniqueness"),
    "unique_on_wrong_columns": (_claim_table(
        unique="CONSTRAINT uq_workout_completion_day UNIQUE (date_key, user_id)"),
        "day uniqueness"),
    "owner_fk_without_cascade": (
        _claim_table(user_id='INTEGER NOT NULL REFERENCES "user"(id)'), "ownership cascade"),
    "owner_fk_missing": (_claim_table(user_id="INTEGER NOT NULL"), "ownership cascade"),
    "proof_check_missing": (_claim_table(check=""), "proof check"),
    "proof_check_widened": (_claim_table(check=(
        "CONSTRAINT ck_workout_completion_day_proof "
        "CHECK (proof IN ('photo', 'none', 'maybe'))")), "proof check"),
    "proof_check_not_valid": (_claim_table(check="", after=(
        ALTER + "ADD " + CHECK + " NOT VALID",)), "proof check"),
}


def _user_table(conn):
    conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')


def _definition(conn):
    """Every column and constraint (with validation state) of the claim table."""
    columns = conn.exec_driver_sql(
        "SELECT column_name, data_type, character_maximum_length, is_nullable, "
        "column_default FROM information_schema.columns "
        "WHERE table_name = 'workout_completion_day' ORDER BY column_name").all()
    constraints = conn.exec_driver_sql(
        "SELECT conname, pg_get_constraintdef(oid), convalidated FROM pg_constraint "
        "WHERE conrelid = 'workout_completion_day'::regclass ORDER BY conname").all()
    return columns, constraints


@pytest.mark.parametrize("case", sorted(MIGRATION_CASES))
def test_migration_accepts_only_its_own_existing_schema(migrate_engine, case):
    maker, refusal = MIGRATION_CASES[case]
    module = _module(MIGRATION)
    with migrate_engine.begin() as conn:
        _user_table(conn)
        maker(conn)
        before = _definition(conn)
    with migrate_engine.begin() as conn:
        if refusal is None:
            _run(conn, module.upgrade)
            _run(conn, module.upgrade)  # re-entry is idempotent
        else:
            with pytest.raises(RuntimeError, match=refusal):
                _run(conn, module.upgrade)
    with migrate_engine.connect() as conn:
        # Accepted or refused, the existing definition is never rewritten.
        assert _definition(conn) == before


def test_migration_creates_the_exact_table_on_an_empty_schema(migrate_engine):
    module = _module(MIGRATION)
    with migrate_engine.begin() as conn:
        _user_table(conn)
        _run(conn, module.upgrade)
    with migrate_engine.begin() as conn:
        _run(conn, module.upgrade)  # created table verifies as its own DDL
        columns, constraints = _definition(conn)
    assert [(c[0], c[3]) for c in columns] == [
        ("created_at", "NO"), ("date_key", "NO"), ("id", "NO"),
        ("proof", "NO"), ("user_id", "NO")]
    assert all(validated for _, _, validated in constraints)
    assert {name for name, _, _ in constraints} == {
        "ck_workout_completion_day_proof", "uq_workout_completion_day",
        "workout_completion_day_pkey", "workout_completion_day_user_id_fkey"}


def test_downgrade_refuses_while_any_claim_exists_and_drops_when_empty(migrate_engine):
    module = _module(MIGRATION)
    with migrate_engine.begin() as conn:
        _user_table(conn)
        conn.exec_driver_sql('INSERT INTO "user" (id) VALUES (1)')
        _run(conn, module.upgrade)
        conn.exec_driver_sql(
            "INSERT INTO workout_completion_day (user_id, date_key, proof, created_at) "
            "VALUES (1, '2026-10-18', 'none', now())")
    with migrate_engine.begin() as conn:
        with pytest.raises(RuntimeError, match="must be retained"):
            _run(conn, module.downgrade)
    with migrate_engine.begin() as conn:
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM workout_completion_day").scalar() == 1
        conn.exec_driver_sql("DELETE FROM workout_completion_day")
        _run(conn, module.downgrade)
    with migrate_engine.connect() as conn:
        assert not sa.inspect(conn).has_table("workout_completion_day")


def test_full_chain_upgrade_preserves_pump_checks_and_backfills_nothing(migrate_engine):
    """Historical rows across the real chain: a daily completion PumpCheck, a
    standalone PumpCheck (date_key NULL) taken the same day, and an old day.
    The upgrade copies none of them into the claim table and changes none."""
    from flask import Flask
    from flask_migrate import Migrate, check, upgrade

    from app.extensions import db
    from app.models import User
    from app.services.workout_completion.queries import completed_days
    from datetime import date

    app = Flask("synthetic-k6-chain")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=migrate_engine.url.render_as_string(hide_password=False),
        SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    Migrate(app, db, directory=str(ROOT / "migrations"))
    with app.app_context():
        upgrade(revision="e7a1c3d5f9b2")
        db.session.add(User(id=1, username="k6-chain", email="k6-chain@example.invalid"))
        db.session.commit()
        with db.engine.begin() as conn:
            for date_key, description in (
                    ("2026-10-17", "completion"), (None, "standalone"), ("2026-09-01", "old")):
                conn.execute(sa.text(
                    "INSERT INTO pump_check (user_id, image_key, location_type, description, "
                    "visibility, shared_friend_ids, likes_count, comments_count, reposts_count, "
                    "valid, fallback, date_key, created_at) VALUES (1, :k, 'gym', :d, "
                    "'private', '[]', 0, 0, 0, true, false, :day, '2026-10-17 08:00:00')"),
                    {"k": f"pump-checks/1/{description}.jpg", "d": description, "day": date_key})
            before = conn.exec_driver_sql("SELECT * FROM pump_check ORDER BY id").all()
        db.session.remove()
        upgrade()
        check()
        with db.engine.connect() as conn:
            assert conn.exec_driver_sql(
                "SELECT version_num FROM alembic_version").scalar_one() == "f9c6d0a1b2e3"
            assert conn.exec_driver_sql("SELECT * FROM pump_check ORDER BY id").all() == before
            assert conn.exec_driver_sql(
                "SELECT count(*) FROM workout_completion_day").scalar_one() == 0
        # The standalone check shares the completion's created_at day but is not
        # a completion; the historical day claim is still recognised.
        days = (date(2026, 10, 17), date(2026, 10, 16), date(2026, 9, 1))
        assert completed_days(1, days) == {date(2026, 10, 17), date(2026, 9, 1)}
        db.session.remove()
        db.engine.dispose()
