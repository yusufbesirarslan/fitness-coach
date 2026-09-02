"""Opt-in real-concurrency proof for the NATIVE workout-session write contracts
(Postgres 16, Mobile Training PR5).

The hermetic in-memory SQLite suite proves the contract's *logic*. It cannot
prove the part that only exists under a real multi-connection database: that the
partial unique index, the conditional revision UPDATE and the completion row
lock actually arbitrate two simultaneous native clients. That is exactly the
scenario PR5 exists for — a phone that taps twice, retries a request whose
response was lost, or has two devices signed in — so SQLite-only proof is
insufficient (PR5 section 62).

Gating matches the existing Postgres race modules: the ``pg_concurrency`` marker
AND ``FITX_PG_CONCURRENCY_TEST=1`` AND a reachable ``PG_TEST_DATABASE_URL``.
Unreachable Postgres SKIPS (never errors), so ordinary runs are unaffected.

Every race is released by a ``threading.Barrier`` — never a sleep — and every
assertion is made on the PERSISTED rows, not on the return values alone.

Run it:
    FITX_PG_CONCURRENCY_TEST=1 \
    PG_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fitx_test \
    python -m pytest -m pg_concurrency -q
"""
import json
import os
import threading
from datetime import datetime

import pytest

pytestmark = pytest.mark.pg_concurrency

_PG_URL = os.environ.get("PG_TEST_DATABASE_URL")
_ENABLED = os.environ.get("FITX_PG_CONCURRENCY_TEST") == "1"

_SKIP_REASON = (
    "opt-in Postgres concurrency test — set FITX_PG_CONCURRENCY_TEST=1 and "
    "PG_TEST_DATABASE_URL (postgres:16) to run"
)

WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]
LINEAGE = "pg-native-lineage"
VERSION = 1
EXERCISE = "ex_barbell_back_squat"


def _pg_reachable(url):
    try:
        import sqlalchemy as sa

        engine = sa.create_engine(url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _require_pg():
    if not _pg_reachable(_PG_URL):
        pytest.skip(f"Postgres not reachable at PG_TEST_DATABASE_URL ({_SKIP_REASON})")


def _plan_document(today_index):
    days = [
        {"gun": name, "tip": "dinlenme", "odak": "Recovery", "sure_dk": 0,
         "tahmini_kalori": 0, "egzersizler": []}
        for name in WEEKDAYS
    ]
    days[today_index] = {
        "gun": WEEKDAYS[today_index], "tip": "antrenman", "odak": "Full body",
        "sure_dk": 45, "tahmini_kalori": 320,
        "egzersizler": [{
            "exercise_id": EXERCISE, "isim": "Squat", "set": 3,
            "tekrar": "8-10", "dinlenme": "90 sn", "not": "",
        }],
    }
    return {"program": days}


def _make_pg_app():
    """A real Postgres app plus one owner whose plan trains TODAY.

    The plan is built around the real current weekday rather than a frozen one:
    the native command resolves "is this startable now?" through the canonical
    read authority, and freezing the clock inside worker threads would not carry
    across them.
    """
    os.environ["DATABASE_URL"] = _PG_URL
    os.environ["FITX_SKIP_DB_INIT"] = "1"
    os.environ["FITX_WORKOUT_SESSIONS_ENABLED"] = "1"

    from app import create_app
    from app.extensions import db
    from app.models import TrainingPlan, User
    from app.services import mobile_training
    from app.timeutil import app_today

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    flask_app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        user = User(
            username="pg_native", email="pg_native@example.com",
            cognito_sub="sub-pg-native")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        slot = app_today().weekday()
        db.session.add(TrainingPlan(
            user_id=user_id,
            plan_data=json.dumps(_plan_document(slot), ensure_ascii=False),
            score=8.0, created_at=datetime(2026, 7, 1, 8, 30),
            lineage_id=LINEAGE, mutation_version=VERSION))
        db.session.commit()
        reference = mobile_training.workout_ref(
            flask_app.config["SECRET_KEY"], user_id, LINEAGE, VERSION, slot)
    return flask_app, user_id, reference


def _teardown(flask_app):
    from app.extensions import db

    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()
        db.drop_all()


def _snapshot(elapsed):
    return {
        "current_exercise_index": 0,
        "elapsed_seconds": elapsed,
        "exercises": [{"exercise_id": EXERCISE, "sets": [
            {"index": 0, "completed": True, "reps": 8, "weight_kg": 60.0},
        ]}],
    }


def _race(flask_app, user_id, contenders):
    """Run N contenders simultaneously, each on its own connection."""
    from app.extensions import db
    from app.models import User

    barrier = threading.Barrier(len(contenders))
    results = {}

    def _run(tag, work):
        with flask_app.app_context():
            resident = db.session.get(User, user_id)
            assert resident is not None
            barrier.wait()
            try:
                results[tag] = ("ok", work())
            except Exception as exc:  # pragma: no cover - surfaced by assertion
                results[tag] = ("raise", type(exc).__name__)
            finally:
                db.session.remove()

    threads = [
        threading.Thread(target=_run, args=(tag, work))
        for tag, work in contenders.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert set(results) == set(contenders), results
    return results


def _completion_kwargs():
    """Completion arguments with the remote work already done (as the route does)."""
    return {
        "image_key": None, "location_type": "gym", "description": "race",
        "workout_score": None, "visibility": "private", "valid": True,
        "fallback": False, "base_xp": 10, "photo_bonus": 25,
        "activity_text": "race", "entry_path": "pg_race",
    }


# -- start --------------------------------------------------------------------

@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_two_concurrent_native_starts_leave_exactly_one_active_session():
    _require_pg()
    from app.extensions import db
    from app.models import WORKOUT_SESSION_ACTIVE, WorkoutSession
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]

    def _start():
        result = sessions.start(user_id, secret, reference)
        return result.status, result.payload["session"]["session_ref"]

    try:
        results = _race(flask_app, user_id, {"a": _start, "b": _start})
        outcomes = [results["a"], results["b"]]
        assert all(kind == "ok" for kind, _ in outcomes), outcomes
        statuses = sorted(value[0] for _, value in outcomes)
        # Exactly one caller CREATED it; the other observed the same session.
        assert statuses == [200, 201], outcomes
        refs = {value[1] for _, value in outcomes}
        assert len(refs) == 1, outcomes
        with flask_app.app_context():
            assert WorkoutSession.query.filter_by(
                user_id=user_id, status=WORKOUT_SESSION_ACTIVE).count() == 1
            assert WorkoutSession.query.count() == 1
    finally:
        _teardown(flask_app)


# -- checkpoint ---------------------------------------------------------------

@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_same_revision_different_snapshots_one_wins_and_one_conflicts():
    _require_pg()
    from app.extensions import db
    from app.models import WorkoutSession
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    def _writer(key, elapsed):
        def _work():
            result = sessions.checkpoint(
                user_id, secret, session_ref, key, 0,
                lambda allowed: sessions.parse_checkpoint(
                    _snapshot(elapsed), allowed))
            return ("won", result.payload["session"]["revision"])
        return _work

    try:
        results = _race(flask_app, user_id, {
            "a": _writer("pg-key-aaaaaaaa", 60),
            "b": _writer("pg-key-bbbbbbbb", 900),
        })
        kinds = [kind for kind, _ in results.values()]
        # One caller wins the conditional UPDATE; the other is told to re-read.
        assert kinds.count("ok") == 1, results
        assert kinds.count("raise") == 1, results
        loser = next(value for kind, value in results.values() if kind == "raise")
        assert loser == "RevisionConflict", results
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            # Exactly ONE advancement, and the stored snapshot is the winner's.
            assert row.checkpoint_revision == 1
            stored = json.loads(row.checkpoint_data)["elapsed_seconds"]
            assert stored in (60, 900)
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_a_duplicated_checkpoint_advances_the_revision_exactly_once():
    _require_pg()
    from app.models import WorkoutSession
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    def _work():
        result = sessions.checkpoint(
            user_id, secret, session_ref, "pg-key-dupdupdup", 0,
            lambda allowed: sessions.parse_checkpoint(_snapshot(60), allowed))
        return (result.status, result.replayed,
                result.payload["session"]["revision"])

    try:
        results = _race(flask_app, user_id, {"a": _work, "b": _work})
        assert all(kind == "ok" for kind, _ in results.values()), results
        values = [value for _, value in results.values()]
        # Both callers see revision 1 — the SAME logical mutation, observed
        # twice. Exactly one of them is the writer.
        assert all(status == 200 for status, _, _ in values), values
        assert all(revision == 1 for _, _, revision in values), values
        assert sorted(replayed for _, replayed, _ in values) == [False, True], values
        with flask_app.app_context():
            assert WorkoutSession.query.filter_by(
                user_id=user_id).one().checkpoint_revision == 1
    finally:
        _teardown(flask_app)


# -- complete -----------------------------------------------------------------

@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_two_concurrent_completes_produce_one_set_of_side_effects():
    _require_pg()
    from app.models import (
        WORKOUT_COMPLETION_MARKER,
        WORKOUT_SESSION_COMPLETED,
        PumpCheck,
        WorkoutLog,
        WorkoutSession,
    )
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    def _work():
        result = sessions.complete(
            user_id, session_ref, 0, **_completion_kwargs())
        return (result.status, result.replayed,
                result.payload["completion"]["outcome"])

    try:
        results = _race(flask_app, user_id, {"a": _work, "b": _work})
        assert all(kind == "ok" for kind, _ in results.values()), results
        outcomes = sorted(value[2] for _, value in results.values())
        assert outcomes == ["already_completed", "created"], results
        with flask_app.app_context():
            # The uq_pump_check_day claim is the arbiter: one proof, one marker,
            # one terminal session. The race loser duplicates nothing.
            assert PumpCheck.query.filter_by(user_id=user_id).count() == 1
            assert WorkoutLog.query.filter_by(
                user_id=user_id,
                exercise_name=WORKOUT_COMPLETION_MARKER).count() == 1
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert row.status == WORKOUT_SESSION_COMPLETED
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_complete_versus_abandon_yields_exactly_one_terminal_outcome():
    _require_pg()
    from app.models import (
        WORKOUT_SESSION_ABANDONED,
        WORKOUT_SESSION_COMPLETED,
        PumpCheck,
        WorkoutSession,
    )
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    def _complete():
        return sessions.complete(
            user_id, session_ref, 0, **_completion_kwargs()).payload[
                "session"]["status"]

    def _abandon():
        return sessions.abandon(user_id, session_ref).payload["session"]["status"]

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "abandon": _abandon})
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert row.status in (
                WORKOUT_SESSION_COMPLETED, WORKOUT_SESSION_ABANDONED)
            # The two outcomes are mutually exclusive at the artifact level too:
            # an abandoned session leaves no completion proof behind.
            proofs = PumpCheck.query.filter_by(user_id=user_id).count()
            if row.status == WORKOUT_SESSION_ABANDONED:
                assert proofs == 0
                assert results["complete"][0] == "raise"
            else:
                assert proofs == 1
        # Neither contender may crash with an unexpected error type.
        for kind, value in results.values():
            assert kind == "ok" or value in (
                "SessionTerminal", "RevisionConflict"), results
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_complete_versus_checkpoint_leaves_a_coherent_terminal_state():
    _require_pg()
    from app.models import (
        WORKOUT_SESSION_ACTIVE,
        WORKOUT_SESSION_COMPLETED,
        PumpCheck,
        WorkoutSession,
    )
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    def _complete():
        return sessions.complete(
            user_id, session_ref, 0, **_completion_kwargs()).payload[
                "session"]["status"]

    def _checkpoint():
        return sessions.checkpoint(
            user_id, secret, session_ref, "pg-key-racecheck", 0,
            lambda allowed: sessions.parse_checkpoint(
                _snapshot(120), allowed)).payload["session"]["revision"]

    try:
        results = _race(
            flask_app, user_id,
            {"complete": _complete, "checkpoint": _checkpoint})
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            proofs = PumpCheck.query.filter_by(user_id=user_id).count()
            if row.status == WORKOUT_SESSION_COMPLETED:
                # Completion won: it was verified against revision 0 under the
                # row lock, so the checkpoint cannot have landed unseen.
                assert row.checkpoint_revision == 0
                assert proofs == 1
            else:
                # The checkpoint won: completion refused rather than silently
                # discarding the progress written after the client's read.
                assert row.status == WORKOUT_SESSION_ACTIVE
                assert row.checkpoint_revision == 1
                assert proofs == 0
                assert results["complete"][0] == "raise"
                assert results["complete"][1] == "RevisionConflict"
    finally:
        _teardown(flask_app)
