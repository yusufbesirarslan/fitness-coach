"""PRIV-K6 rollback gate, Layer A, on real PostgreSQL 16/18: the completion fence.

The fence is installed by the reviewed migration's own ``upgrade()`` (on the
create_all schema, i.e. the fresh-boot adoption path) and, separately, by the
full Alembic chain. Historical-only writers are the EXACT pre-K6 BASE
(01befa99) completion modules (``tests/fixtures/k6_base_completion``, SHA-256
pinned) plus raw SQL for every other evidence representation (COPY, UPDATE,
upsert, savepoints, replica role, deferred/immediate constraints).

Same gating as every PG module here: the ``pg_concurrency`` marker AND
``FITX_PG_CONCURRENCY_TEST=1`` AND a reachable ``PG_TEST_DATABASE_URL`` naming a
DISPOSABLE database. Synthetic users only; no provider, no S3, no AWS.
Interleavings are released by events, locks and pg_sleep markers; assertions
are on committed rows.
"""
from __future__ import annotations

import hashlib
import sys
import threading
import time
import types
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.test_mobile_training_allowance_pg import _module, _run, migrate_engine  # noqa: F401
from tests.test_mobile_workout_sessions_pg import (
    _ENABLED, _PG_URL, _SKIP_REASON, _make_pg_app, _race, _teardown,
)
from tests import test_priv_k6_no_photo_completion_pg as k6

pytestmark = [pytest.mark.pg_concurrency,
              pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)]

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE / "fixtures" / "k6_base_completion"
BASE_SHA256 = {
    "base_queries.py": "4814559e92d954c23bb380ad2b02876b441d9d47ad82129649302c4973acd0f1",
    "base_service.py": "9b4ff6ade49a4d878dacba76df08a9b01256bf812705712ecb5f486e352edd19",
}
MIGRATION = "d7e3a9c1f5b8_completion_authority_fence.py"
REV = "c" * 40
MARKER = "Antrenman tamamlandı (Pump Check)"
PHOTO_TEXT = "Bugünkü antrenmanını tamamladı (foto eklendi)"
KEY = -3391128469959823719
CODES = {"AXF01", "AXF02", "AXF03", "AXF04", "AXF05", "AXF06"}


# ── harness ────────────────────────────────────────────────────────────────

def _engine():
    return sa.create_engine(_PG_URL, poolclass=sa.pool.NullPool)


def _sql(statement, **params):
    engine = _engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(sa.text(statement), params)
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def _code(callable_):
    """Run ``callable_``; return the SQLSTATE it failed with, or None."""
    try:
        callable_()
    except sa.exc.DBAPIError as exc:
        return getattr(exc.orig, "pgcode", None)
    return None


def _drop_fence_objects():
    migration = _module(MIGRATION)
    engine = _engine()
    try:
        with engine.begin() as conn:
            for table in ("completion_claim_transaction", "completion_capability_floor"):
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS public.{table} CASCADE")
            for name in migration.FUNCTIONS:
                conn.exec_driver_sql(f"DROP FUNCTION IF EXISTS public.{name}() CASCADE")
    finally:
        engine.dispose()


def _install_fence():
    """The reviewed migration's upgrade() on the create_all schema."""
    migration = _module(MIGRATION)
    engine = _engine()
    try:
        with engine.begin() as conn:
            _run(conn, migration.upgrade)
    finally:
        engine.dispose()


def _verify():
    migration = _module(MIGRATION)
    engine = _engine()
    try:
        with engine.connect() as conn:
            migration.verify_fence(conn)
    finally:
        engine.dispose()


def _raise_floor(revision=REV):
    from app.services import completion_fence
    engine = _engine()
    try:
        with engine.connect() as conn:
            return completion_fence.raise_floor(conn, revision)
    finally:
        engine.dispose()


def _status():
    from app.services import completion_fence
    engine = _engine()
    try:
        with engine.connect() as conn:
            return completion_fence.floor_status(conn)
    finally:
        engine.dispose()


@pytest.fixture
def world():
    _drop_fence_objects()
    app, uid, reference = _make_pg_app()
    _install_fence()
    try:
        yield app, uid, reference
    finally:
        _teardown(app)
        _drop_fence_objects()


@pytest.fixture(scope="module")
def base():
    """The exact pre-K6 BASE completion writer (01befa99), beside K6 models."""
    for name, digest in BASE_SHA256.items():
        assert hashlib.sha256((BASE_DIR / name).read_bytes()).hexdigest() == digest, name
    prefix = "app.services.workout_completion."
    queries = types.ModuleType(prefix + "gate_base_queries")
    queries.__package__ = "app.services.workout_completion"
    exec(compile((BASE_DIR / "base_queries.py").read_text(), "base_queries", "exec"),
         queries.__dict__)
    sys.modules[queries.__name__] = queries
    service = types.ModuleType(prefix + "gate_base_service")
    service.__package__ = "app.services.workout_completion"
    source = (BASE_DIR / "base_service.py").read_text().replace(
        "from .queries import (", "from .gate_base_queries import (")
    exec(compile(source, "base_service", "exec"), service.__dict__)
    sys.modules[service.__name__] = service
    return types.SimpleNamespace(queries=queries, service=service)


def _base_complete(app, base, uid):
    """One historical-only (pre-K6 image) photo completion; returns its outcome."""
    from app.services.workout_completion import CompleteWorkoutCommand
    from app.timeutil import app_today
    with app.app_context():
        try:
            result = base.service.complete_workout(CompleteWorkoutCommand(
                user_id=uid, today=app_today(), activity_text=PHOTO_TEXT))
        except sa.exc.DBAPIError as exc:
            return ("refused", getattr(exc.orig, "pgcode", None))
        return ("ok", "created" if result.created else "already")


def _k6_no_photo(app, uid, ref):
    from app.services.workout_completion import CompletionFenceRefused
    from app.services.workout_session import SessionCommandError
    with app.app_context():
        try:
            return ("ok",) + k6._complete_no_photo(uid, ref)
        except (CompletionFenceRefused, SessionCommandError) as exc:
            return ("refused", type(exc).__name__, getattr(exc, "public_code", None))


def _k6_photo(app, uid):
    from app.services.workout_completion import CompletionFenceRefused, complete_workout
    from app.timeutil import app_today
    with app.app_context():
        try:
            result = complete_workout(k6._photo_command(uid, app_today()))
        except CompletionFenceRefused as exc:
            return ("refused", exc.reason)
        return ("ok", "created" if result.created else "already")


def _rows(app, uid):
    rows = k6._rows(app, uid)
    rows["quests"] = _sql("SELECT count(*) FROM user_quest_progress WHERE user_id = :u",
                          u=uid)[0][0]
    return rows


def _today():
    from app.timeutil import app_today
    return app_today().isoformat()


def _second_user(app, name):
    from app.extensions import db
    from app.models import User
    with app.app_context():
        user = User(username=name, email=f"{name}@example.com", cognito_sub=f"sub-{name}")
        db.session.add(user)
        db.session.commit()
        return user.id


PUMP = ("INSERT INTO pump_check (user_id, image_key, location_type, description, visibility, "
        "shared_friend_ids, likes_count, comments_count, reposts_count, valid, fallback, "
        "date_key, created_at) VALUES (:u, NULL, 'gym', 'synthetic', 'private', '[]', 0, 0, 0, "
        "true, false, :d, now())")
LOG = ("INSERT INTO workout_log (user_id, exercise_name, sets, reps, weight_kg, volume, "
       "created_at) VALUES (:u, :n, 1, 1, 0, 0, now())")
CLAIM = ("INSERT INTO workout_completion_day (user_id, date_key, proof, created_at) "
         "VALUES (:u, :d, :p, now())")


def _wait(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _waiting_advisory():
    return _sql("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")[0][0]


# ── A: integrity ────────────────────────────────────────────────────────────

def test_a0_dormant_fence_refuses_claims_and_keeps_historical_writers(world, base):
    app, uid, reference = world
    _verify()
    assert _status()["floor"] == "absent"
    ref = k6._started(app, uid, reference)
    refused = _k6_no_photo(app, uid, ref)
    assert refused[0] == "refused" and refused[2] == "TRAINING_SESSION_UNAVAILABLE"
    assert _rows(app, uid)["claims"] == [] and _rows(app, uid)["xp"] == 0
    assert _base_complete(app, base, uid) == ("ok", "created")      # pre-K6 unchanged
    rows = _rows(app, uid)
    assert rows["pump_checks"] == 1 and rows["markers"] == 1 and rows["xp"] == 35


def test_a1_k6_photo_free_completion_succeeds_after_floor(world):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[0:4] == ("ok", 200, False, "created")
    rows = _rows(app, uid)
    assert rows["claims"] == [(_today(), "none")] and rows["markers"] == 1 and rows["xp"] == 10


def test_a2_k6_photo_completion_succeeds_after_floor(world):
    app, uid, _ = world
    _raise_floor()
    assert _k6_photo(app, uid) == ("ok", "created")
    rows = _rows(app, uid)
    assert rows["claims"] == [(_today(), "photo")] and rows["pump_checks"] == 1
    assert rows["xp"] == 35


def test_a3_legacy_writer_refused_after_photo_claim_and_gallery_delete(world, base):
    """Scenario B: K6 photo, gallery delete, then an old image (70 XP without fence)."""
    app, uid, _ = world
    _raise_floor()
    assert _k6_photo(app, uid) == ("ok", "created")
    _sql("DELETE FROM pump_check WHERE user_id = :u", u=uid)        # gallery delete
    assert _base_complete(app, base, uid) == ("refused", "AXF02")
    rows = _rows(app, uid)
    assert rows["markers"] == 1 and rows["xp"] == 35 and rows["pump_checks"] == 0
    assert rows["claims"] == [(_today(), "photo")]                    # day stays completed


def test_a4_legacy_writer_refused_after_photo_free_claim(world, base):
    """Scenario A: K6 photo-free, then an old image (45 XP without fence)."""
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[3] == "created"
    assert _base_complete(app, base, uid) == ("refused", "AXF02")
    rows = _rows(app, uid)
    assert rows["markers"] == 1 and rows["activities"] == 1 and rows["xp"] == 10
    assert rows["pump_checks"] == 0


def test_a5_an_earlier_claim_never_authorizes_new_evidence(world):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[3] == "created"              # claim committed earlier
    assert _code(lambda: _sql(PUMP, u=uid, d=_today())) == "AXF02"
    assert _code(lambda: _sql(LOG, u=uid, n=MARKER)) == "AXF02"


def test_a6_another_owners_claim_never_authorizes(world):
    app, uid, _ = world
    other = _second_user(app, "fence_other")
    _raise_floor()

    def mixed():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(CLAIM), {"u": other, "d": _today(), "p": "photo"})
                conn.execute(sa.text(PUMP), {"u": uid, "d": _today()})
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(mixed) == "AXF02"
    assert _sql("SELECT count(*) FROM workout_completion_day")[0][0] == 0


def test_a6b_another_owners_claim_never_authorizes_a_marker_alone(world):
    """The marker check binds the claim to the marker's OWNER (no PumpCheck in
    the transaction, so the dated-PumpCheck check cannot mask it)."""
    app, uid, _ = world
    other = _second_user(app, "fence_marker_other")
    _raise_floor()

    def marker_only():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(CLAIM), {"u": other, "d": _today(), "p": "none"})
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(marker_only) == "AXF02"
    assert _sql("SELECT count(*) FROM workout_log")[0][0] == 0


def test_a7_claim_for_another_day_never_authorizes_a_dated_pump_check(world):
    app, uid, _ = world
    _raise_floor()
    yesterday = (__import__("datetime").date.fromisoformat(_today()) - timedelta(days=1)).isoformat()

    def other_day():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(CLAIM), {"u": uid, "d": yesterday, "p": "photo"})
                conn.execute(sa.text(PUMP), {"u": uid, "d": _today()})
        finally:
            engine.dispose()
    assert _code(other_day) == "AXF02"


def test_a8_a_refused_completion_grants_no_xp_quest_or_activity(world, base):
    app, uid, _ = world
    _raise_floor()
    assert _base_complete(app, base, uid) == ("refused", "AXF02")
    rows = _rows(app, uid)
    assert (rows["xp"], rows["quests"], rows["activities"], rows["markers"],
            rows["pump_checks"]) == (0, 0, 0, 0, 0)


def test_a9_standalone_pump_checks_and_ordinary_logs_are_unaffected(world):
    app, uid, _ = world
    _raise_floor()
    _sql(PUMP, u=uid, d=None)                                        # standalone Pump Check
    _sql(LOG, u=uid, n="Bench Press")                                # performed exercise
    _sql("UPDATE pump_check SET visibility = 'friends' WHERE user_id = :u", u=uid)
    assert _sql("SELECT count(*) FROM pump_check WHERE user_id = :u", u=uid)[0][0] == 1
    assert _sql("SELECT count(*) FROM workout_log WHERE user_id = :u", u=uid)[0][0] == 1


def test_a10_historical_completion_stays_recognized_and_blocks_k6(world, base):
    app, uid, reference = world
    ref = k6._started(app, uid, reference)                          # K6 session first
    assert _base_complete(app, base, uid) == ("ok", "created")      # before the floor
    _raise_floor()
    _sql("UPDATE pump_check SET visibility = 'friends' WHERE user_id = :u", u=uid)
    # Under the raised floor K6 re-checks the union under the day lock: the
    # historical day is complete, so neither path writes a claim or evidence.
    outcome = _k6_no_photo(app, uid, ref)
    assert outcome[0] == "ok" and outcome[3] == "already_completed"
    assert _k6_photo(app, uid) == ("ok", "already")
    from app.services.workout_session import WorkoutNotStartable
    with pytest.raises(WorkoutNotStartable):                        # LP-13 invariant
        k6._started(app, uid, reference)
    rows = _rows(app, uid)
    assert rows["claims"] == [] and rows["markers"] == 1 and rows["xp"] == 35


def test_a11_a_claim_in_a_rolled_back_savepoint_authorizes_nothing(world):
    app, uid, _ = world
    _raise_floor()

    def savepoint():
        engine = _engine()
        try:
            with engine.begin() as conn:
                nested = conn.begin_nested()
                conn.execute(sa.text(CLAIM), {"u": uid, "d": _today(), "p": "none"})
                nested.rollback()
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(savepoint) == "AXF02"


def test_a12_updates_that_create_completion_evidence_are_fenced(world):
    app, uid, _ = world
    other = _second_user(app, "fence_update")
    _sql(PUMP, u=uid, d=None)
    _sql(LOG, u=uid, n="Squat")
    _sql(PUMP, u=other, d=_today())                                  # historical, pre-floor
    _raise_floor()
    assert _code(lambda: _sql("UPDATE pump_check SET date_key = :d WHERE user_id = :u "
                              "AND date_key IS NULL", u=uid, d=_today())) == "AXF02"
    assert _code(lambda: _sql("UPDATE workout_log SET exercise_name = :n WHERE user_id = :u",
                              u=uid, n=MARKER)) == "AXF02"
    assert _code(lambda: _sql("UPDATE pump_check SET user_id = :u WHERE user_id = :o",
                              u=uid, o=other)) == "AXF02"
    assert _code(lambda: _sql("UPDATE pump_check SET date_key = date_key WHERE user_id = :o",
                              o=other)) == "AXF02"
    assert _sql("SELECT count(*) FROM pump_check WHERE date_key IS NOT NULL AND user_id = :u",
                u=uid)[0][0] == 0


def test_a13_copy_and_upsert_representations_are_fenced(world):
    import io
    app, uid, _ = world
    _raise_floor()
    engine = _engine()
    try:
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cur:
                buffer = io.StringIO(f"{uid}\t{MARKER}\t1\t1\t0\t0\t2026-10-19 08:00:00\n")
                cur.copy_expert("COPY workout_log (user_id, exercise_name, sets, reps, "
                                "weight_kg, volume, created_at) FROM STDIN", buffer)
            with pytest.raises(Exception) as caught:
                raw.commit()
            assert getattr(caught.value, "pgcode", None) == "AXF02"
        finally:
            raw.close()
    finally:
        engine.dispose()
    assert _code(lambda: _sql(PUMP + " ON CONFLICT DO NOTHING", u=uid, d=_today())) == "AXF02"


def test_a14_replica_role_and_immediate_constraints_do_not_bypass(world):
    app, uid, _ = world
    _raise_floor()

    def replica():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("SET LOCAL session_replication_role = replica")
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()

    def immediate():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(replica) == "AXF02"
    assert _code(immediate) == "AXF02"


def test_a15_a_temp_table_shadow_cannot_satisfy_the_fence(world):
    """The design prototype's unqualified SQL was satisfied by a session TEMP
    table (Phase Zero P2). Schema-qualified relations + pinned search_path."""
    app, uid, _ = world
    _raise_floor()

    def shadow():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("SET LOCAL search_path = pg_temp, public")
                for table in ("workout_completion_day", "completion_claim_transaction",
                              "completion_capability_floor"):
                    conn.exec_driver_sql(
                        f"CREATE TEMP TABLE {table} (id int, user_id int, date_key text, "
                        "claim_id int, claim_txid bigint, capability text) ON COMMIT DROP")
                conn.exec_driver_sql(
                    "INSERT INTO pg_temp.workout_completion_day VALUES "
                    f"(1, {uid}, '{_today()}', 1, pg_current_xact_id()::text::bigint, NULL)")
                conn.exec_driver_sql(
                    "INSERT INTO pg_temp.completion_claim_transaction VALUES "
                    "(1, NULL, NULL, 1, pg_current_xact_id()::text::bigint, NULL)")
                conn.execute(sa.text(LOG.replace("workout_log", "public.workout_log")),
                             {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(shadow) == "AXF02"


def test_a16_repeatable_read_and_serializable_writers_are_refused(world):
    app, uid, _ = world
    for level in ("REPEATABLE READ", "SERIALIZABLE"):
        def isolated():
            engine = _engine()
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(f"SET TRANSACTION ISOLATION LEVEL {level}")
                    conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
            finally:
                engine.dispose()
        assert _code(isolated) == "AXF06", level        # dormant or not: never trusted


def test_a17_account_deletion_cascades_claims_and_nothing_else_deletes_them(world):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[3] == "created"
    assert _code(lambda: _sql("DELETE FROM workout_completion_day WHERE user_id = :u",
                              u=uid)) == "AXF05"
    assert _code(lambda: _sql("TRUNCATE workout_completion_day CASCADE")) == "AXF05"
    assert _code(lambda: _sql("UPDATE workout_completion_day SET proof = 'photo'")) == "AXF05"
    from app.cli import _purge_user
    from app.extensions import db
    from app.models import User
    with app.app_context():
        _purge_user(db.session.get(User, uid))
        db.session.commit()
    assert _sql("SELECT count(*) FROM workout_completion_day")[0][0] == 0
    assert _sql("SELECT count(*) FROM completion_claim_transaction")[0][0] == 0
    other = _second_user(app, "fence_cascade")
    with app.app_context():
        from app.services.workout_completion import complete_workout
        from app.timeutil import app_today
        assert complete_workout(k6._photo_command(other, app_today())).created
    _sql('DELETE FROM "user" WHERE id = :u', u=other)                 # FK cascade path
    assert _sql("SELECT count(*) FROM completion_claim_transaction")[0][0] == 0


def test_a18_gallery_delete_of_a_k6_photo_keeps_the_day_completed(world):
    app, uid, _ = world
    _raise_floor()
    assert _k6_photo(app, uid) == ("ok", "created")
    _sql("DELETE FROM pump_check WHERE user_id = :u", u=uid)
    assert _k6_photo(app, uid) == ("ok", "already")
    assert _rows(app, uid)["xp"] == 35


# ── C: concurrency ──────────────────────────────────────────────────────────

def test_c1_a_transaction_started_before_the_raise_cannot_commit_after_it(world):
    app, uid, _ = world
    engine = _engine()
    try:
        conn = engine.connect()
        tx = conn.begin()
        conn.execute(sa.text(PUMP), {"u": uid, "d": _today()})
        conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        assert _raise_floor()["result"] == "raised"                   # commits meanwhile
        with pytest.raises(sa.exc.DBAPIError) as caught:
            tx.commit()
        assert caught.value.orig.pgcode == "AXF02"
        conn.close()
    finally:
        engine.dispose()
    assert _sql("SELECT count(*) FROM pump_check WHERE user_id = :u", u=uid)[0][0] == 0


def _slow_commit_trigger(table, when=""):
    _sql("""CREATE OR REPLACE FUNCTION public.zz_gate_test_sleep() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(2); RETURN NULL; END $$""")
    _sql(f"""CREATE CONSTRAINT TRIGGER zz_gate_test_sleep AFTER INSERT ON public.{table}
             DEFERRABLE INITIALLY DEFERRED FOR EACH ROW {when}
             EXECUTE FUNCTION public.zz_gate_test_sleep()""")


def _drop_slow_commit_trigger(table):
    _sql(f"DROP TRIGGER IF EXISTS zz_gate_test_sleep ON public.{table}")
    _sql("DROP FUNCTION IF EXISTS public.zz_gate_test_sleep()")


def test_c2_raise_waits_for_an_in_flight_historical_commit(world, base):
    """A historical commit already past every fence check is serialized BEFORE
    the floor: the raise waits for it, and no historical commit is still in
    flight when the floor becomes visible."""
    app, uid, reference = world
    ref = k6._started(app, uid, reference)                          # before the day closes
    _slow_commit_trigger("workout_log", f"WHEN (NEW.exercise_name = '{MARKER}')")
    try:
        results = {}
        writer = threading.Thread(target=lambda: results.__setitem__(
            "historical", _base_complete(app, base, uid)))
        writer.start()
        assert _wait(lambda: _sql("SELECT count(*) FROM pg_stat_activity "
                                  "WHERE wait_event = 'PgSleep'")[0][0] >= 1)
        started = time.monotonic()
        _raise_floor()
        results["raise_wait"] = time.monotonic() - started
        results["in_flight"] = _sql("SELECT count(*) FROM pg_stat_activity "
                                    "WHERE wait_event = 'PgSleep'")[0][0]
        writer.join(30)
    finally:
        _drop_slow_commit_trigger("workout_log")
    assert results["historical"] == ("ok", "created")
    assert results["raise_wait"] >= 1.0, results
    assert results["in_flight"] == 0, results
    assert _k6_no_photo(app, uid, ref)[3] == "already_completed"
    assert _rows(app, uid)["xp"] == 35


def test_c3_a_commit_arriving_during_the_raise_queues_behind_it_and_is_refused(world, base):
    app, uid, _ = world
    other = _second_user(app, "fence_queue")
    _slow_commit_trigger("pump_check")
    try:
        results = {}
        first = threading.Thread(target=lambda: results.__setitem__(
            "first", _base_complete(app, base, uid)))
        first.start()
        assert _wait(lambda: _sql("SELECT count(*) FROM pg_stat_activity "
                                  "WHERE wait_event = 'PgSleep'")[0][0] >= 1)
        raiser = threading.Thread(target=lambda: results.__setitem__("raise", _raise_floor()))
        raiser.start()
        assert _wait(lambda: _waiting_advisory() >= 1)
        late = threading.Thread(target=lambda: results.__setitem__(
            "late", _base_complete(app, base, other)))
        late.start()
        for thread in (first, raiser, late):
            thread.join(30)
    finally:
        _drop_slow_commit_trigger("pump_check")
    assert results["first"] == ("ok", "created")
    assert results["late"] == ("refused", "AXF02")
    assert _rows(app, other)["xp"] == 0


@pytest.mark.parametrize("attempt", range(3))
def test_c3b_new_and_old_writers_race_for_one_day_exactly_once(world, base, attempt):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    from app.services.workout_completion import CompleteWorkoutCommand
    from app.timeutil import app_today
    contenders = {}
    for i in range(3):
        contenders[f"k6_{i}"] = lambda: k6._complete_no_photo(uid, ref)
        contenders[f"base_{i}"] = lambda: base.service.complete_workout(CompleteWorkoutCommand(
            user_id=uid, today=app_today(), activity_text=PHOTO_TEXT)).created
    results = _race(app, uid, contenders)
    rows = _rows(app, uid)
    assert all(results[f"base_{i}"][0] == "raise" for i in range(3)), results
    assert rows["markers"] == 1 and rows["xp"] == 10 and len(rows["claims"]) == 1


def test_c4_photo_free_claim_racing_an_unlocked_historical_writer(world, base, monkeypatch):
    """A pre-LP13 writer (no day lock) commits between K6's locked re-check and
    the raise; the photo-free claim must not commit beside it (I4)."""
    app, uid, reference = world
    ref = k6._started(app, uid, reference)
    from app.services.workout_completion import service
    real_read = service.already_completed_today
    paused, release = threading.Event(), threading.Event()
    calls = []

    def pausing_read(*args, **kwargs):
        result = real_read(*args, **kwargs)
        if threading.current_thread().name == "k6":
            calls.append(result)
            if len(calls) == 2:
                paused.set()
                assert release.wait(20)
        return result

    monkeypatch.setattr(service, "already_completed_today", pausing_read)
    monkeypatch.setattr(base.service, "lock_completion_day", lambda *a, **k: None)
    results = {}
    worker = threading.Thread(name="k6", target=lambda: results.__setitem__(
        "k6", _k6_no_photo(app, uid, ref)))
    worker.start()
    assert paused.wait(15)
    results["historical"] = _base_complete(app, base, uid)
    _raise_floor()
    release.set()
    worker.join(30)
    rows = _rows(app, uid)
    assert results["historical"] == ("ok", "created")
    assert results["k6"][0] == "refused", results
    assert rows["claims"] == [] and rows["markers"] == 1 and rows["xp"] == 35


@pytest.mark.parametrize("attempt", range(3))
def test_c5_two_k6_writers_race_for_one_day_exactly_once(world, attempt):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    results = _race(app, uid, {
        "a": lambda: k6._complete_no_photo(uid, ref),
        "b": lambda: k6._complete_no_photo(uid, ref)})
    rows = _rows(app, uid)
    assert sorted(r[1][2] for r in results.values()) == ["already_completed", "created"]
    assert rows["markers"] == 1 and rows["xp"] == 10 and len(rows["claims"]) == 1


def test_c6_the_fence_migration_waits_for_in_flight_writers_and_never_half_installs(world):
    app, uid, _ = world
    _drop_fence_objects()
    engine = _engine()
    try:
        writer = engine.connect()
        tx = writer.begin()
        writer.execute(sa.text(LOG), {"u": uid, "n": "Deadlift"})      # holds ROW EXCLUSIVE
        migration = _module(MIGRATION)

        def bounded_upgrade():
            with engine.begin() as conn:
                conn.exec_driver_sql("SET LOCAL lock_timeout = '500ms'")
                _run(conn, migration.upgrade)
        assert _code(bounded_upgrade) == "55P03"
        tx.commit()
        writer.close()
    finally:
        engine.dispose()
    functions = _sql("SELECT count(*) FROM pg_proc WHERE proname LIKE 'completion\\_fence\\_%'")
    assert functions[0][0] == 0                                      # nothing half-installed
    _install_fence()                                                 # after the writer: installs
    _verify()


def test_c7_crash_during_and_after_activation_and_restart_keep_a_consistent_floor(world):
    app, uid, reference = world
    engine = _engine()
    try:
        victim = engine.connect()
        tx = victim.begin()
        victim.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": KEY})
        victim.execute(sa.text(
            "INSERT INTO completion_capability_floor VALUES "
            "('workout_completion_claims_v1', now(), :r)"), {"r": REV})
        pid = victim.execute(sa.text("SELECT pg_backend_pid()")).scalar()
        _sql("SELECT pg_terminate_backend(:p)", p=pid)                # crash mid-raise
        with pytest.raises(sa.exc.DBAPIError):
            tx.commit()
    finally:
        engine.dispose()
    assert _status()["floor"] == "absent"                            # state D, consistent
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[0] == "refused"               # retryable AXF01
    assert _raise_floor()["result"] == "raised"                      # safe re-run
    assert _raise_floor()["result"] == "already_raised"
    from app import create_app
    restarted = create_app()                                         # "restart"
    restarted.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False        # feature OFF
    assert _status()["floor"] == "raised"
    assert _k6_no_photo(app, uid, ref)[3] == "created"


# ── D: adversarial schema and tooling ──────────────────────────────────────

def test_d1_floor_cannot_be_lowered_deleted_or_truncated(world):
    _raise_floor()
    for statement in ("UPDATE completion_capability_floor SET raised_by_revision = repeat('0', 40)",
                      "DELETE FROM completion_capability_floor",
                      "TRUNCATE completion_capability_floor"):
        assert _code(lambda: _sql(statement)) == "AXF03", statement
    assert _status()["floor"] == "raised"


def test_d2_floor_insert_without_the_exclusive_lock_is_refused(world):
    assert _code(lambda: _sql(
        "INSERT INTO completion_capability_floor VALUES "
        "('workout_completion_claims_v1', now(), :r)", r=REV)) == "AXF03"

    def shared_only():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": KEY})
                conn.execute(sa.text("INSERT INTO completion_capability_floor VALUES "
                                     "('workout_completion_claims_v1', now(), :r)"), {"r": REV})
        finally:
            engine.dispose()
    assert _code(shared_only) == "AXF03"
    assert _status()["floor"] == "absent"


def test_d3_floor_table_recreated_by_ddl_with_claims_present_stays_enforced(world, base):
    """A schema owner can drop and re-create the floor table (break-glass DDL).
    Because claims exist, historical-only evidence is still refused."""
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[3] == "created"
    _sql("DROP TABLE completion_capability_floor")
    _sql("CREATE TABLE completion_capability_floor (capability varchar(64) PRIMARY KEY, "
         "raised_at timestamptz NOT NULL, raised_by_revision varchar(40) NOT NULL)")
    other = _second_user(app, "fence_recreated")
    assert _base_complete(app, base, other) == ("refused", "AXF02")
    # Each evidence kind on its own: both checks hold while any claim exists.
    assert _code(lambda: _sql(PUMP, u=other, d=_today())) == "AXF02"
    assert _code(lambda: _sql(LOG, u=other, n=MARKER)) == "AXF02"
    assert _status()["fence"] == "invalid"


def test_d4_bindings_cannot_be_forged_or_rewritten(world):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[3] == "created"
    claim_id = _sql("SELECT id FROM workout_completion_day")[0][0]
    assert _code(lambda: _sql("UPDATE completion_claim_transaction SET "
                              "claim_txid = pg_current_xact_id()::text::bigint")) == "AXF05"
    assert _code(lambda: _sql("DELETE FROM completion_claim_transaction")) == "AXF05"

    def forge():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("DELETE FROM completion_claim_transaction "
                                     "WHERE claim_id = :c"), {"c": claim_id})
                conn.execute(sa.text("INSERT INTO completion_claim_transaction VALUES "
                                     "(:c, pg_current_xact_id()::text::bigint)"),
                             {"c": claim_id})
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(forge) == "AXF05"

    def forge_insert_only():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text("INSERT INTO completion_claim_transaction VALUES "
                                     "(:c, pg_current_xact_id()::text::bigint)"),
                             {"c": claim_id + 1000})
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(forge_insert_only) in {"AXF05", "23503"}
    assert _rows(app, uid)["markers"] == 1


def test_d5_a_claim_cannot_be_inserted_with_a_chosen_transaction_id(world):
    app, uid, _ = world
    _raise_floor()

    def explicit():
        engine = _engine()
        try:
            with engine.begin() as conn:
                conn.execute(sa.text(CLAIM), {"u": uid, "d": _today(), "p": "photo"})
                conn.execute(sa.text(PUMP), {"u": uid, "d": _today()})
                conn.execute(sa.text(LOG), {"u": uid, "n": MARKER})
        finally:
            engine.dispose()
    assert _code(explicit) is None          # a REAL claim is the canonical exact-once claim
    txid = _sql("SELECT b.claim_txid FROM completion_claim_transaction b")[0][0]
    assert isinstance(txid, int) and txid > 0
    assert _code(lambda: _sql(PUMP, u=uid, d=_today())) == "23505"   # uq_pump_check_day


@pytest.mark.parametrize("tamper", [
    "ALTER TABLE public.workout_log DISABLE TRIGGER completion_fence_marker",
    "ALTER TABLE public.workout_log ENABLE TRIGGER completion_fence_marker",
    "CREATE OR REPLACE FUNCTION public.completion_fence_marker() RETURNS trigger "
    "LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$BEGIN RETURN NULL; END$$",
    "CREATE TRIGGER zz_extra BEFORE INSERT ON public.completion_capability_floor "
    "FOR EACH ROW EXECUTE FUNCTION public.completion_fence_floor_admission()",
    "CREATE RULE zz_rule AS ON INSERT TO public.pump_check DO ALSO NOTHING",
    "ALTER TABLE public.completion_capability_floor SET UNLOGGED",
    "ALTER TABLE public.completion_capability_floor ENABLE ROW LEVEL SECURITY",
    "ALTER FUNCTION public.completion_fence_marker() SECURITY DEFINER",
    "ALTER FUNCTION public.completion_fence_marker() RESET search_path",
], ids=["disabled", "origin-only", "replaced-body", "extra-trigger", "rule",
        "unlogged", "rls", "security-definer", "search-path"])
def test_d6_the_verifier_rejects_any_tampered_fence_and_upgrade_never_repairs_it(world, tamper):
    _sql(tamper)
    with pytest.raises(RuntimeError, match="completion authority fence"):
        _verify()
    assert _status()["fence"] == "invalid"
    with pytest.raises(RuntimeError):
        _install_fence()                                             # re-entry fails closed


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE completion_capability_floor (capability text PRIMARY KEY, "
    "raised_at timestamptz NOT NULL, raised_by_revision varchar(40) NOT NULL)",
    "CREATE TABLE completion_capability_floor (capability varchar(64) PRIMARY KEY, "
    "raised_at timestamptz NOT NULL DEFAULT now(), raised_by_revision varchar(40) NOT NULL)",
], ids=["wrong-type", "server-default"])
def test_d7_a_malformed_existing_floor_table_is_refused_on_adoption(world, ddl):
    _drop_fence_objects()
    _sql(ddl)
    with pytest.raises(RuntimeError, match="completion authority fence"):
        _install_fence()


def test_d8_repeated_upgrade_is_idempotent_and_downgrade_refuses_retained_state(world):
    _install_fence()                                                 # re-entry: verify only
    _verify()
    migration = _module(MIGRATION)
    _raise_floor()
    engine = _engine()
    try:
        with engine.begin() as conn:
            with pytest.raises(RuntimeError, match="must be retained"):
                _run(conn, migration.downgrade)
    finally:
        engine.dispose()
    _verify()


def test_d9_the_full_alembic_chain_installs_exactly_the_reviewed_fence(migrate_engine):
    from flask import Flask
    from flask_migrate import Migrate, check, downgrade, upgrade

    from app.extensions import db
    from app.services import completion_fence
    app = Flask("synthetic-fence-chain")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=migrate_engine.url.render_as_string(hide_password=False),
        SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    root = Path(__file__).resolve().parents[1]
    Migrate(app, db, directory=str(root / "migrations"))
    with app.app_context():
        upgrade(revision="b1a8c9d0e1f2")                           # recovered staging revision
        upgrade()
        check()
        with db.engine.connect() as conn:
            completion_fence.verify_fence(conn)
            # MON-02-BE-02's empty Apple tables (a4d6f8b0c2e1) follow the fence.
            assert conn.exec_driver_sql(
                "SELECT version_num FROM alembic_version").scalar_one() == "a4d6f8b0c2e1"
        upgrade()                                                    # repeated: no-op
        downgrade(revision="f9c6d0a1b2e3")                           # empty: drops cleanly
        with db.engine.connect() as conn:
            assert not sa.inspect(conn).has_table("completion_capability_floor")
            assert conn.execute(sa.text("SELECT count(*) FROM pg_proc WHERE proname "
                                        "LIKE 'completion\\_fence\\_%'")).scalar() == 0
        upgrade()
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def fence_tables(migrate_engine):
    """Only the real floor/binding DDL, independent of fence installation."""
    migration = _module(MIGRATION)
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE workout_completion_day (id INTEGER PRIMARY KEY)")
        _run(conn, lambda: migration._create_tables(conn))
    return migrate_engine, migration


@pytest.mark.parametrize("table,columns,verifier", [
    ("completion_capability_floor", {"capability", "raised_at", "raised_by_revision"},
     "_verify_floor_table"),
    ("completion_claim_transaction", {"claim_id", "claim_txid"}, "_verify_binding_table"),
])
def test_d10_valid_constraint_catalog_and_existing_table_adoption(
        fence_tables, table, columns, verifier):
    engine, migration = fence_tables
    with engine.begin() as conn:
        not_null = [(valid, definition) for kind, valid, definition in
                    migration._pg_constraints(conn, table).values() if kind == "n"]
        if conn.dialect.server_version_info >= (18,):
            assert sorted(not_null) == sorted((True, f"NOT NULL {c}") for c in columns)
        else:
            assert not not_null
        getattr(migration, verifier)(conn)
        _run(conn, lambda: migration._create_tables(conn))           # existing-schema path


@pytest.mark.parametrize("table,alteration", [
    ("completion_capability_floor", "ALTER COLUMN raised_at DROP NOT NULL"),
    ("completion_capability_floor", "DROP CONSTRAINT completion_capability_floor_pkey"),
    ("completion_capability_floor", "ADD CONSTRAINT extra UNIQUE (raised_at)"),
    ("completion_capability_floor", "ADD CONSTRAINT extra CHECK (true)"),
    ("completion_capability_floor", "DROP CONSTRAINT ck_completion_capability_floor_revision, "
     "ADD CONSTRAINT ck_completion_capability_floor_revision "
     "CHECK (raised_by_revision ~ '^[0-9a-f]{40}$') NOT VALID"),
    ("completion_capability_floor", "DROP CONSTRAINT ck_completion_capability_floor_revision, "
     "ADD CONSTRAINT ck_completion_capability_floor_revision CHECK (true)"),
    ("completion_claim_transaction", "ALTER COLUMN claim_txid DROP NOT NULL"),
    ("completion_claim_transaction", "DROP CONSTRAINT completion_claim_transaction_pkey"),
    ("completion_claim_transaction", "ADD CONSTRAINT extra UNIQUE (claim_txid)"),
    ("completion_claim_transaction", "ADD CONSTRAINT extra CHECK (true)"),
    ("completion_claim_transaction", "DROP CONSTRAINT completion_claim_transaction_claim_id_fkey, "
     "ADD FOREIGN KEY (claim_id) REFERENCES workout_completion_day(id)"),
    ("completion_claim_transaction", "DROP CONSTRAINT completion_claim_transaction_claim_id_fkey, "
     "ADD FOREIGN KEY (claim_id) REFERENCES workout_completion_day(id) ON DELETE CASCADE NOT VALID"),
    ("completion_claim_transaction", "ADD CONSTRAINT extra FOREIGN KEY (claim_id) "
     "REFERENCES workout_completion_day(id) ON DELETE CASCADE"),
])
def test_d11_incompatible_constraints_still_fail_closed(fence_tables, table, alteration):
    engine, migration = fence_tables
    verifier = (migration._verify_floor_table if table == migration.FLOOR
                else migration._verify_binding_table)
    with engine.begin() as conn:
        conn.exec_driver_sql(f"ALTER TABLE public.{table} {alteration}")
        before = migration._pg_constraints(conn, table)
        with pytest.raises(RuntimeError, match="completion authority fence"):
            verifier(conn)
        with pytest.raises(RuntimeError, match="completion authority fence"):
            _run(conn, lambda: migration._create_tables(conn))
        assert migration._pg_constraints(conn, table) == before      # no repairs


@pytest.mark.parametrize("table,column", [
    ("completion_capability_floor", "raised_at"),
    ("completion_claim_transaction", "claim_txid"),
])
def test_d12_pg18_unvalidated_not_null_is_not_ignored(fence_tables, table, column):
    engine, migration = fence_tables
    with engine.begin() as conn:
        if conn.dialect.server_version_info < (18,):
            pytest.skip("NOT NULL catalog constraints require PostgreSQL 18")
        conn.exec_driver_sql(f"ALTER TABLE public.{table} ALTER COLUMN {column} DROP NOT NULL")
        conn.exec_driver_sql(
            f"ALTER TABLE public.{table} ADD CONSTRAINT unvalidated NOT NULL {column} NOT VALID")
        assert any(kind == "n" and not valid for kind, valid, _ in
                   migration._pg_constraints(conn, table).values())
        with pytest.raises(RuntimeError, match="completion authority fence"):
            _run(conn, lambda: migration._create_tables(conn))


def test_e1_floor_status_and_raise_tooling(world):
    status = _status()
    assert status == {"capability": "workout_completion_claims_v1", "fence": "verified",
                      "floor": "absent", "raised_by_revision": None, "claims_present": False}
    assert _raise_floor()["result"] == "raised"
    assert _raise_floor("d" * 40) == {"result": "already_raised", "raised_by_revision": REV}
    assert _status()["floor"] == "raised"
    from app.services import completion_fence
    with pytest.raises(completion_fence.FloorError):
        _raise_floor("not-a-sha")


def test_e2_raise_refuses_an_unverified_fence(world):
    from app.services import completion_fence
    _sql("ALTER TABLE public.pump_check DISABLE TRIGGER completion_fence_dated_pump_check")
    with pytest.raises(completion_fence.FloorError) as caught:
        _raise_floor()
    assert caught.value.reason == "fence_not_verified"
    assert _sql("SELECT count(*) FROM completion_capability_floor")[0][0] == 0


def test_e3_compatible_restore_preserves_days_and_exact_once(world):
    app, uid, reference = world
    _raise_floor()
    ref = k6._started(app, uid, reference)
    assert _k6_no_photo(app, uid, ref)[3] == "created"
    from app import create_app
    restored = create_app()
    restored.config["TESTING"] = True
    restored.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    assert _k6_no_photo(restored, uid, ref)[3] == "already_completed"
    assert _k6_photo(restored, uid) == ("ok", "already")
    assert _rows(app, uid)["xp"] == 10


def test_every_refusal_code_is_a_known_fence_code(world, base):
    """Defense against a silent fallthrough: refusals seen in this suite are
    all members of the closed vocabulary classified by the application."""
    from app.services.completion_fence import REFUSALS
    assert set(REFUSALS) == CODES


# ── F: release tooling contract (CLI line -> r6 parser) and fail-closed probes ──

def _r6_module():
    sys.path.insert(0, str(HERE.parent / "scripts"))
    try:
        import r6_deploy_transaction
    finally:
        sys.path.pop(0)
    return r6_deploy_transaction


def _cli(app, *args):
    """Run ``flask completion-floor ...``; return (exit code, the ONE payload)."""
    import json
    result = app.test_cli_runner().invoke(args=["completion-floor", *args])
    r6 = _r6_module()
    lines = [line[len(r6.CAPABILITY_FLOOR_PREFIX):] for line in result.output.splitlines()
             if line.startswith(r6.CAPABILITY_FLOOR_PREFIX)]
    assert len(lines) == 1, result.output
    return result.exit_code, json.loads(lines[0])


def test_f1_cli_status_and_raise_lines_are_what_the_release_engine_parses(world):
    app, _, _ = world
    r6 = _r6_module()
    code, payload = _cli(app, "status")
    assert code == 0
    assert r6.parse_floor_status(payload) == {
        "floor": "absent", "fence": "verified", "claims_present": False}
    code, payload = _cli(app, "raise", "--revision", REV)
    assert (code, payload) == (0, {"result": "raised", "raised_by_revision": REV})
    code, payload = _cli(app, "raise", "--revision", "e" * 40)
    assert (code, payload) == (0, {"result": "already_raised", "raised_by_revision": REV})
    code, payload = _cli(app, "status")
    assert code == 0 and r6.parse_floor_status(payload)["floor"] == "raised"
    code, payload = _cli(app, "raise", "--revision", "nope")
    assert code == 1 and payload == {"reason": "invalid_revision", "result": "refused"}
    assert r6.parse_floor_status(payload) is None


def test_f2_cli_reports_a_tampered_fence_as_invalid_and_raise_refuses(world):
    app, _, _ = world
    r6 = _r6_module()
    _sql("ALTER TABLE public.workout_log DISABLE TRIGGER completion_fence_marker")
    code, payload = _cli(app, "status")
    assert code == 0 and r6.parse_floor_status(payload)["fence"] == "invalid"
    code, payload = _cli(app, "raise", "--revision", REV)
    assert code == 1 and payload["reason"] == "fence_not_verified"
    assert _sql("SELECT count(*) FROM completion_capability_floor")[0][0] == 0


def test_f3_insufficient_privileges_are_unknown_never_absent(world):
    from app.services import completion_fence
    role = "fence_probe_synthetic"
    _sql(f"DROP ROLE IF EXISTS {role}")
    _sql(f"CREATE ROLE {role} NOLOGIN")
    engine = _engine()
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(f"SET ROLE {role}")
            conn.commit()
            with pytest.raises(sa.exc.ProgrammingError) as caught:
                completion_fence.floor_status(conn)
            assert caught.value.orig.pgcode == "42501"           # insufficient_privilege
            conn.rollback()
            with pytest.raises(Exception) as raised:
                completion_fence.raise_floor(conn, REV)
            assert not isinstance(raised.value, AssertionError)
            conn.rollback()
            conn.exec_driver_sql("RESET ROLE")
            conn.commit()
    finally:
        engine.dispose()
        _sql(f"DROP ROLE IF EXISTS {role}")
    assert _sql("SELECT count(*) FROM completion_capability_floor")[0][0] == 0


def test_f4_a_blocked_probe_times_out_as_unknown_never_absent(world):
    from app.services import completion_fence
    holder = _engine()
    probe = _engine()
    try:
        with holder.connect() as held:
            held.exec_driver_sql(
                "LOCK TABLE public.completion_capability_floor IN ACCESS EXCLUSIVE MODE")
            started = time.monotonic()
            with probe.connect() as conn:
                with pytest.raises(sa.exc.OperationalError) as caught:
                    completion_fence.floor_status(conn, timeout_ms=300)
            assert caught.value.orig.pgcode in {"55P03", "57014"}  # lock / statement timeout
            assert time.monotonic() - started < 5
            held.rollback()
    finally:
        holder.dispose()
        probe.dispose()


def test_f5_cli_with_an_unreachable_database_reports_error_not_absent(world, monkeypatch):
    import os
    from app import create_app
    r6 = _r6_module()
    monkeypatch.setitem(os.environ, "DATABASE_URL",
                        "postgresql://postgres@127.0.0.1:9/fitx_unreachable?connect_timeout=2")
    monkeypatch.setitem(os.environ, "FITX_SKIP_DB_INIT", "1")
    unreachable = create_app()
    code, payload = _cli(unreachable, "status")
    assert code == 1 and payload["result"] == "error"
    assert r6.parse_floor_status(payload) is None
    code, payload = _cli(unreachable, "raise", "--revision", REV)
    assert code == 1 and payload["result"] == "error"
