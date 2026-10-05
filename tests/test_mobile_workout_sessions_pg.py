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


# -- LP-13 P2: start racing a session-linked completion -----------------------
#
# Invariant: once both transactions settle, no NEW active session exists on a
# day whose linked completion committed. The completion authority terminalizes
# the linked session in the SAME commit as the day's PumpCheck claim, so a start
# either still sees that session ACTIVE (replay) or already sees the claim
# (refused by the completed-today guard). The two orderings are pinned with
# events at the real seams, not with sleeps.

_ROLE = threading.local()
_STEP_TIMEOUT = 15


def _assert_no_active_on_completed_day(flask_app, user_id):
    from app.models import (
        WORKOUT_SESSION_ACTIVE,
        WORKOUT_SESSION_COMPLETED,
        PumpCheck,
        WorkoutSession,
    )

    with flask_app.app_context():
        rows = WorkoutSession.query.filter_by(user_id=user_id).all()
        assert [row.status for row in rows] == [WORKOUT_SESSION_COMPLETED], rows
        assert WorkoutSession.query.filter_by(
            user_id=user_id, status=WORKOUT_SESSION_ACTIVE).count() == 0
        assert PumpCheck.query.filter_by(user_id=user_id).count() == 1


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_start_seeing_the_session_active_before_completion_commits_replays(
    monkeypatch,
):
    """Ordering A: completion has staged its PumpCheck + COMPLETED transition but
    not committed; the start reads the session as still ACTIVE and replays it."""
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_completion import service as completion_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    staged, start_done = threading.Event(), threading.Event()
    real_mark = completion_service.mark_session_completed

    def _hold_before_commit(session, now):
        changed = real_mark(session, now)
        staged.set()
        assert start_done.wait(_STEP_TIMEOUT), "start never finished"
        return changed

    monkeypatch.setattr(
        completion_service, "mark_session_completed", _hold_before_commit)

    def _complete():
        try:
            return sessions.complete(
                user_id, session_ref, 0, **_completion_kwargs()).status
        finally:
            staged.set()

    def _start():
        try:
            assert staged.wait(_STEP_TIMEOUT), "completion never staged"
            result = sessions.start(user_id, secret, reference)
            return result.status, result.payload["session"]["session_ref"]
        finally:
            start_done.set()

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "start": _start})
        assert results["complete"] == ("ok", 200), results
        assert results["start"] == ("ok", (200, session_ref)), results
        _assert_no_active_on_completed_day(flask_app, user_id)
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_start_after_a_racing_completion_commits_is_refused(monkeypatch):
    """Ordering B: the start's transaction is already open (plan snapshot read)
    when the linked completion commits; its active lookup then finds no active
    session and the completed-today guard must refuse instead of inserting."""
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import service as session_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    at_lookup, committed = threading.Event(), threading.Event()
    real_lookup = session_service.get_active_session

    def _lookup_after_completion(owner_id):
        if getattr(_ROLE, "name", None) == "start":
            at_lookup.set()
            assert committed.wait(_STEP_TIMEOUT), "completion never committed"
        return real_lookup(owner_id)

    monkeypatch.setattr(
        session_service, "get_active_session", _lookup_after_completion)

    def _complete():
        try:
            assert at_lookup.wait(_STEP_TIMEOUT), "start never reached lookup"
            return sessions.complete(
                user_id, session_ref, 0, **_completion_kwargs()).status
        finally:
            committed.set()

    def _start():
        _ROLE.name = "start"
        try:
            return sessions.start(user_id, secret, reference).status
        finally:
            _ROLE.name = None
            at_lookup.set()

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "start": _start})
        assert results["complete"] == ("ok", 200), results
        assert results["start"] == ("raise", "WorkoutNotStartable"), results
        _assert_no_active_on_completed_day(flask_app, user_id)
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_free_running_start_versus_completion_never_leaves_a_new_active_row():
    _require_pg()
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        session_ref = sessions.start(
            user_id, secret, reference).payload["session"]["session_ref"]

    def _complete():
        return sessions.complete(
            user_id, session_ref, 0, **_completion_kwargs()).status

    def _start():
        return sessions.start(user_id, secret, reference).status

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "start": _start})
        assert results["complete"] == ("ok", 200), results
        # Either ordering is legal; a new 201 session never is.
        assert results["start"] in (
            ("ok", 200), ("raise", "WorkoutNotStartable")), results
        _assert_no_active_on_completed_day(flask_app, user_id)
    finally:
        _teardown(flask_app)


# -- LP-13 P2: start racing a SESSION-LESS completion -------------------------
#
# A session-less completion (the browser's legacy ``/workout/complete`` and the
# AI-coach gym-photo tool: ``CompleteWorkoutCommand(session_id=None)``) has no
# session row to lock or terminalize, so the #385 argument above does not cover
# it. Without a shared serialization point it could commit the day's claim
# between a start's completed-today guard and its INSERT, leaving a NEW active
# session on a completed day (``lifecycle_inconsistent``).
#
# The boundary is ``workout_completion.lock_completion_day``: a transaction-
# scoped PostgreSQL advisory lock on (owner, Istanbul day) taken by
# ``complete_workout`` before it writes the claim and by ``start_session``
# before it re-checks the claim and inserts. Each ordering below is pinned at a
# real seam. A side that must be serialized is released only once PostgreSQL
# itself reports the other side WAITING on an advisory lock (or the other side
# has already finished, which is what happens when no boundary exists, so the
# same tests fail closed on the unfixed code).

_ADVISORY_WAIT_SQL = (
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() "
    "AND wait_event_type = 'Lock' AND wait_event = 'advisory'"
)


class _AdvisoryWatch:
    """Observe advisory-lock waiters on a connection outside the app's pool."""

    def __init__(self):
        import sqlalchemy as sa

        self._engine = sa.create_engine(_PG_URL)
        self._text = sa.text(_ADVISORY_WAIT_SQL)

    def waiters(self):
        with self._engine.connect() as conn:
            return int(conn.execute(self._text).scalar())

    def wait_for_waiter_or(self, done, timeout=_STEP_TIMEOUT):
        """True once some backend waits on an advisory lock; False if ``done``
        is set first (the contender finished without ever waiting)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.waiters() > 0:
                return True
            if done.is_set():
                return False
            time.sleep(0.02)
        raise AssertionError("contender neither waited nor finished")

    def dispose(self):
        self._engine.dispose()


def _legacy_complete(user_id):
    """The session-less canonical completion, exactly as the browser legacy path
    and the AI-coach tool reach it (remote proof work already done)."""
    from app.services.workout_completion import (
        CompleteWorkoutCommand,
        complete_workout,
    )
    from app.timeutil import app_today

    result = complete_workout(CompleteWorkoutCommand(
        user_id=user_id, today=app_today(), session_id=None,
        **_completion_kwargs()))
    return result.outcome.value


def _day_rows(flask_app, user_id):
    from app.models import PumpCheck, WorkoutSession
    from app.timeutil import app_today

    with flask_app.app_context():
        claims = PumpCheck.query.filter_by(
            user_id=user_id, date_key=app_today().isoformat()).count()
        statuses = [row.status for row in WorkoutSession.query.filter_by(
            user_id=user_id).order_by(WorkoutSession.id).all()]
    return claims, statuses


def _assert_claim_and_no_session(flask_app, user_id):
    claims, statuses = _day_rows(flask_app, user_id)
    assert claims == 1
    # The integrity failure this section exists for: a completed day that ALSO
    # holds a session the start inserted after the claim committed.
    assert statuses == [], (
        f"completed claim + new session(s) {statuses} on the same day")


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_session_less_completion_between_guard_and_insert_leaves_no_active_row(
    monkeypatch,
):
    """Race A (the LP-13 reproducer): the start has passed its active lookup and
    its first completed-today guard; a session-less completion then commits the
    claim; the start resumes. It must be refused, never insert."""
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import service as session_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    past_guard, committed = threading.Event(), threading.Event()
    real_guard = session_service.completed_today

    def _guard_then_pause(owner_id, day):
        answer = real_guard(owner_id, day)
        if getattr(_ROLE, "name", None) == "start" and not past_guard.is_set():
            assert answer is False
            past_guard.set()
            assert committed.wait(_STEP_TIMEOUT), "completion never committed"
        return answer

    monkeypatch.setattr(session_service, "completed_today", _guard_then_pause)

    def _complete():
        try:
            assert past_guard.wait(_STEP_TIMEOUT), "start never passed the guard"
            return _legacy_complete(user_id)
        finally:
            committed.set()

    def _start():
        _ROLE.name = "start"
        try:
            return sessions.start(user_id, secret, reference).status
        finally:
            _ROLE.name = None
            past_guard.set()

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "start": _start})
        assert results["complete"] == ("ok", "created"), results
        assert results["start"] == ("raise", "WorkoutNotStartable"), results
        _assert_claim_and_no_session(flask_app, user_id)
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_start_holding_the_day_lock_serializes_a_session_less_completion(
    monkeypatch,
):
    """Race B (start-first, ACCEPTED CONTRACT): the start owns the boundary
    (lock taken, claim re-checked) and is about to INSERT when a session-less
    completion arrives. The completion must WAIT for the start's commit, so the
    history is exactly the serial order start -> complete — never a claim
    committed inside the start's window.

    The end state (completed day + the start's untouched ACTIVE session,
    classified ``lifecycle_inconsistent``) is deliberate, NOT a missing
    assertion: it is the same state main reaches sequentially (start, then a
    legacy/AI-coach completion). A session-less completion carries no
    session_id / expected_checkpoint_revision, so it may neither terminalize
    the session nor be refused under the current contract. See
    docs/WORKOUT_STATE.md, "Follow-up — session-less completion contract"."""
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import service as session_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    at_insert, complete_done = threading.Event(), threading.Event()
    seen = {}
    watch = _AdvisoryWatch()
    real_insert = session_service.insert_active_session

    def _insert_after_completion_queues(*args, **kwargs):
        at_insert.set()
        seen["completion_waited"] = watch.wait_for_waiter_or(complete_done)
        seen["claims_at_insert"] = _committed_claims(user_id)
        row = real_insert(*args, **kwargs)
        seen["session_id"] = row.id
        return row

    monkeypatch.setattr(
        session_service, "insert_active_session", _insert_after_completion_queues)

    def _complete():
        try:
            assert at_insert.wait(_STEP_TIMEOUT), "start never reached insert"
            return _legacy_complete(user_id)
        finally:
            complete_done.set()

    def _start():
        try:
            return sessions.start(user_id, secret, reference).status
        finally:
            at_insert.set()

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "start": _start})
        assert seen.get("completion_waited") is True, (
            "the completion committed its claim inside the start's window", seen)
        assert seen["claims_at_insert"] == 0, seen
        assert results["start"] == ("ok", 201), results
        assert results["complete"] == ("ok", "created"), results
        claims, statuses = _day_rows(flask_app, user_id)
        # Exactly one claim (no lost/double completion) and exactly one session
        # (no duplicate, no post-completion start insert).
        assert (claims, statuses) == (1, ["active"])
        _assert_start_first_session_untouched(
            flask_app, user_id, seen["session_id"])
    finally:
        watch.dispose()
        _teardown(flask_app)


def _assert_start_first_session_untouched(flask_app, user_id, session_id):
    """Accepted start-first contract: the later session-less completion leaves
    the start's session exactly as the start committed it, and readers surface
    the pair as ``lifecycle_inconsistent`` (existing, recoverable via abandon)."""
    from app.models import WorkoutSession
    from app.services.workout_session import (
        STALE_LIFECYCLE_INCONSISTENT, build_session_view)
    from app.timeutil import app_today

    with flask_app.app_context():
        row = WorkoutSession.query.filter_by(user_id=user_id).one()
        assert row.id == session_id
        assert row.status == "active"
        assert (row.version, row.checkpoint_revision) == (1, 0)
        assert (row.completed_at, row.abandoned_at, row.terminal_reason) == (
            None, None, None)
        view = build_session_view(row, app_today())
        assert view.stale_reason == STALE_LIFECYCLE_INCONSISTENT
        assert view.resumable is False


def _committed_claims(user_id):
    """Claims visible to a brand-new connection (i.e. committed)."""
    import sqlalchemy as sa

    engine = sa.create_engine(_PG_URL)
    try:
        with engine.connect() as conn:
            return int(conn.execute(sa.text(
                "SELECT count(*) FROM pump_check "
                "WHERE user_id = :u AND date_key IS NOT NULL"), {"u": user_id}
            ).scalar())
    finally:
        engine.dispose()


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_start_waits_for_a_session_less_completion_then_refuses(monkeypatch):
    """Race C: the completion owns the boundary and has staged (not committed)
    its claim. The start must queue on the same lock, wake after the commit,
    see the claim and refuse — no ACTIVE row."""
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_completion import service as completion_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    staged, start_done = threading.Event(), threading.Event()
    seen = {}
    watch = _AdvisoryWatch()
    real_stage = completion_service._record_pump_check_created

    # The seam is right after the claim is flushed and BEFORE award_xp takes the
    # owner row FOR UPDATE, so the only thing a start can queue on here is the
    # day boundary itself (a row-lock wait would prove nothing).
    def _stage_then_hold(*args, **kwargs):
        real_stage(*args, **kwargs)
        staged.set()
        seen["start_waited"] = watch.wait_for_waiter_or(start_done)

    monkeypatch.setattr(
        completion_service, "_record_pump_check_created", _stage_then_hold)

    def _complete():
        try:
            return _legacy_complete(user_id)
        finally:
            staged.set()

    def _start():
        try:
            assert staged.wait(_STEP_TIMEOUT), "completion never staged"
            return sessions.start(user_id, secret, reference).status
        finally:
            start_done.set()

    try:
        results = _race(
            flask_app, user_id, {"complete": _complete, "start": _start})
        assert results["complete"] == ("ok", "created"), results
        assert results["start"] == ("raise", "WorkoutNotStartable"), results
        assert seen.get("start_waited") is True, seen
        _assert_claim_and_no_session(flask_app, user_id)
    finally:
        watch.dispose()
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_two_starts_serialized_on_the_day_lock_leave_one_active_session(
    monkeypatch,
):
    """Race D (deterministic): the second start queues on the boundary while the
    first holds it at INSERT; it then re-checks, loses the partial-index claim
    and replays the winner — exactly one ACTIVE session."""
    _require_pg()
    from app.extensions import db
    from app.models import WORKOUT_SESSION_ACTIVE, WorkoutSession
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import service as session_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    first_at_insert, second_done = threading.Event(), threading.Event()
    seen = {}
    watch = _AdvisoryWatch()
    real_insert = session_service.insert_active_session

    def _insert(*args, **kwargs):
        if getattr(_ROLE, "name", None) == "first":
            first_at_insert.set()
            seen["second_waited"] = watch.wait_for_waiter_or(second_done)
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(session_service, "insert_active_session", _insert)

    def _first():
        _ROLE.name = "first"
        try:
            result = sessions.start(user_id, secret, reference)
            return result.status, result.payload["session"]["session_ref"]
        finally:
            _ROLE.name = None
            first_at_insert.set()

    def _second():
        try:
            assert first_at_insert.wait(_STEP_TIMEOUT), "first never at insert"
            result = sessions.start(user_id, secret, reference)
            return result.status, result.payload["session"]["session_ref"]
        finally:
            second_done.set()

    try:
        results = _race(
            flask_app, user_id, {"first": _first, "second": _second})
        assert seen.get("second_waited") is True, seen
        assert results["first"][1][0] == 201, results
        assert results["second"][1] == (200, results["first"][1][1]), results
        with flask_app.app_context():
            assert db.session.query(WorkoutSession).filter_by(
                user_id=user_id, status=WORKOUT_SESSION_ACTIVE).count() == 1
    finally:
        watch.dispose()
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_free_running_session_less_completion_versus_start_stays_serial():
    """Race F (stress): many owners, each racing one start against one
    session-less completion with no seams. Every owner must end in one of the
    two serial histories, with no deadlock and no unexpected error:
    complete -> start (refused, no session) or start -> complete (201, one
    ACTIVE session, which the legacy completion leaves as is)."""
    _require_pg()
    from app.extensions import db
    from app.models import TrainingPlan, User
    from app.services import mobile_training
    from app.services import mobile_workout_sessions as sessions
    from app.timeutil import app_today

    flask_app, first_user, first_ref = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    owners = [(first_user, first_ref)]
    with flask_app.app_context():
        template = TrainingPlan.query.filter_by(user_id=first_user).one()
        slot = app_today().weekday()
        for index in range(1, 12):
            user = User(
                username=f"pg_native_{index}",
                email=f"pg_native_{index}@example.com",
                cognito_sub=f"sub-pg-native-{index}")
            db.session.add(user)
            db.session.flush()
            db.session.add(TrainingPlan(
                user_id=user.id, plan_data=template.plan_data, score=8.0,
                created_at=template.created_at,
                lineage_id=f"{LINEAGE}-{index}", mutation_version=VERSION))
            owners.append((user.id, mobile_training.workout_ref(
                secret, user.id, f"{LINEAGE}-{index}", VERSION, slot)))
        db.session.commit()

    try:
        for owner_id, reference in owners:
            results = _race(flask_app, owner_id, {
                "complete": lambda o=owner_id: _legacy_complete(o),
                "start": lambda o=owner_id, r=reference: sessions.start(
                    o, secret, r).status,
            })
            assert results["complete"] == ("ok", "created"), results
            claims, statuses = _day_rows(flask_app, owner_id)
            assert claims == 1
            if results["start"] == ("raise", "WorkoutNotStartable"):
                assert statuses == [], (owner_id, statuses)
            else:
                assert results["start"] == ("ok", 201), results
                assert statuses == ["active"], (owner_id, statuses)
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_day_lock_domains_are_per_owner_and_per_day():
    """Different owners, and the same owner on another Istanbul day, never queue
    behind a held (owner, day) lock; the same (owner, day) does."""
    _require_pg()
    from datetime import timedelta

    from app.extensions import db
    from app.services.workout_completion import lock_completion_day
    from app.timeutil import app_today

    flask_app, user_id, _ = _make_pg_app()
    other_id = user_id + 1000
    day = app_today()
    held, release, same_done = (
        threading.Event(), threading.Event(), threading.Event())
    watch = _AdvisoryWatch()
    seen = {}

    def _holder():
        with flask_app.app_context():
            lock_completion_day(user_id, day)
            held.set()
            assert release.wait(_STEP_TIMEOUT)
            db.session.rollback()  # the xact lock ends with the transaction
            db.session.remove()

    def _acquire(owner, on_day):
        with flask_app.app_context():
            lock_completion_day(owner, on_day)
            db.session.rollback()
            db.session.remove()

    holder = threading.Thread(target=_holder)
    holder.start()
    try:
        assert held.wait(_STEP_TIMEOUT)
        for owner, on_day in ((other_id, day), (user_id, day + timedelta(days=1))):
            free = threading.Thread(target=_acquire, args=(owner, on_day))
            free.start()
            free.join(timeout=5)
            assert not free.is_alive(), f"blocked on an unrelated domain {owner}"
        assert watch.waiters() == 0

        def _same():
            try:
                _acquire(user_id, day)
            finally:
                same_done.set()

        same = threading.Thread(target=_same)
        same.start()
        seen["queued"] = watch.wait_for_waiter_or(same_done)
        release.set()
        same.join(timeout=_STEP_TIMEOUT)
        assert not same.is_alive()
        assert seen["queued"] is True
    finally:
        release.set()
        holder.join(timeout=_STEP_TIMEOUT)
        watch.dispose()
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_a_failed_completion_releases_the_day_lock(monkeypatch):
    """A completion that raises inside its critical section rolls back: no claim
    survives and the boundary is released, so a following start proceeds."""
    _require_pg()
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_completion import service as completion_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    watch = _AdvisoryWatch()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("activity write failed")

    monkeypatch.setattr(completion_service, "log_activity", _boom)
    try:
        with flask_app.app_context():
            with pytest.raises(RuntimeError):
                _legacy_complete(user_id)
            from app.extensions import db
            db.session.remove()
        monkeypatch.undo()
        done = threading.Event()
        results = {}

        def _start():
            with flask_app.app_context():
                try:
                    results["start"] = sessions.start(
                        user_id, secret, reference).status
                finally:
                    done.set()

        starter = threading.Thread(target=_start)
        starter.start()
        assert watch.wait_for_waiter_or(done) is False, "the lock leaked"
        starter.join(timeout=_STEP_TIMEOUT)
        assert results["start"] == 201
        assert _day_rows(flask_app, user_id) == (0, ["active"])
    finally:
        watch.dispose()
        _teardown(flask_app)
