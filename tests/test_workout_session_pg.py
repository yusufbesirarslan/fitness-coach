"""Opt-in real-concurrency proof for the persisted workout-session lifecycle
(Postgres, Sprint 7 PR3).

Hermetic in-memory SQLite (the default suite) enforces the
``uq_workout_session_active_owner`` partial unique index but cannot reproduce
production Postgres row-locking / multi-connection races. This module adds
genuine two-contender races against a real Postgres — the same major version as
production/CI (``postgres:16``, see ``.github/workflows/ci.yml``).

It is **opt-in and never part of the default run** — identical gating to
``test_workout_completion_pg.py``:

* the ``pg_concurrency`` marker AND ``FITX_PG_CONCURRENCY_TEST=1`` AND
  ``PG_TEST_DATABASE_URL`` (a reachable disposable ``postgresql://…`` database);
* connectivity is verified first and the test *skips* (never errors) when
  Postgres is unavailable, so ordinary CI / developer runs are unaffected.

Strategy: two threads, each with its own app context (its own thread-local
session/connection), released simultaneously by a ``threading.Barrier`` (not
sleeps). We assert on the *persisted* rows: exactly one ACTIVE session, a
deterministic replay/conflict for the loser, and — for the terminal race — that a
completion and an abandon can never both win.

Faithful-context note: each contender holds a **strong reference** to its
``User`` for the whole operation, exactly as Flask-Login's ``current_user`` does
inside a real request (see the completion PG test's docstring for the full
rationale — the leaderboard ``after_commit`` hook must see a resident user).

Run it:
    FITX_PG_CONCURRENCY_TEST=1 \
    PG_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fitx_test \
    python -m pytest -m pg_concurrency -q
"""
import os
import threading

import pytest

pytestmark = pytest.mark.pg_concurrency

_PG_URL = os.environ.get("PG_TEST_DATABASE_URL")
_ENABLED = os.environ.get("FITX_PG_CONCURRENCY_TEST") == "1"

_SKIP_REASON = (
    "opt-in Postgres concurrency test — set FITX_PG_CONCURRENCY_TEST=1 and "
    "PG_TEST_DATABASE_URL (postgres:16) to run"
)


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


def _make_pg_app():
    os.environ["DATABASE_URL"] = _PG_URL
    os.environ["FITX_SKIP_DB_INIT"] = "1"
    os.environ["FITX_WORKOUT_SESSIONS_ENABLED"] = "1"

    from app import create_app
    from app.extensions import db
    from app.models import User

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    flask_app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="pg_sess", email="pg_sess@example.com", cognito_sub="sub-sess")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    return flask_app, user_id


def _teardown(flask_app):
    from app.extensions import db

    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()
        db.drop_all()


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_concurrent_start_yields_single_active_on_postgres():
    if not _pg_reachable(_PG_URL):
        pytest.skip(f"Postgres not reachable at PG_TEST_DATABASE_URL ({_SKIP_REASON})")

    from app.extensions import db
    from app.models import User, WORKOUT_SESSION_ACTIVE, WorkoutSession
    from app.services.workout_session import SessionOutcome, start_session

    flask_app, user_id = _make_pg_app()
    barrier = threading.Barrier(2)
    results = {}

    def _contender(tag):
        with flask_app.app_context():
            resident_user = db.session.get(User, user_id)
            assert resident_user is not None
            barrier.wait()
            try:
                results[tag] = ("ok", start_session(user_id).outcome)
            except Exception as exc:  # pragma: no cover - surfaced via assertion
                results[tag] = ("raise", type(exc).__name__)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=_contender, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    try:
        outcomes = [results[i] for i in range(2)]
        assert all(kind == "ok" for kind, _ in outcomes), outcomes
        values = [outcome for _, outcome in outcomes]
        # Exactly one winner; the loser is a deterministic idempotent replay.
        assert values.count(SessionOutcome.CREATED) == 1, values
        assert values.count(SessionOutcome.EXISTING_ACTIVE) == 1, values
        with flask_app.app_context():
            assert WorkoutSession.query.filter_by(
                user_id=user_id, status=WORKOUT_SESSION_ACTIVE).count() == 1
    finally:
        _teardown(flask_app)


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_complete_versus_abandon_only_one_terminal_wins_on_postgres():
    if not _pg_reachable(_PG_URL):
        pytest.skip(f"Postgres not reachable at PG_TEST_DATABASE_URL ({_SKIP_REASON})")

    from app.extensions import db
    from app.models import (
        PumpCheck,
        User,
        WORKOUT_SESSION_ABANDONED,
        WORKOUT_SESSION_COMPLETED,
        WorkoutSession,
    )
    from app.services.workout_session import (
        SessionOutcome,
        abandon_session,
        complete_session,
        start_session,
    )

    flask_app, user_id = _make_pg_app()
    with flask_app.app_context():
        public_id = start_session(user_id).session.public_id

    barrier = threading.Barrier(2)
    results = {}

    def _complete():
        with flask_app.app_context():
            resident = db.session.get(User, user_id)
            assert resident is not None
            barrier.wait()
            try:
                results["complete"] = ("ok", complete_session(user_id, public_id).outcome)
            except Exception as exc:  # pragma: no cover
                results["complete"] = ("raise", type(exc).__name__)
            finally:
                db.session.remove()

    def _abandon():
        with flask_app.app_context():
            resident = db.session.get(User, user_id)
            assert resident is not None
            barrier.wait()
            try:
                results["abandon"] = ("ok", abandon_session(user_id, public_id).outcome)
            except Exception as exc:  # pragma: no cover
                results["abandon"] = ("raise", type(exc).__name__)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=_complete), threading.Thread(target=_abandon)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    try:
        assert all(kind == "ok" for kind, _ in results.values()), results
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            # Exactly one terminal state — never both, never still ACTIVE.
            assert row.status in (WORKOUT_SESSION_COMPLETED, WORKOUT_SESSION_ABANDONED)
            # If completion won there is exactly one PumpCheck; if abandon won there
            # is none (abandon never creates completion artifacts).
            pumps = PumpCheck.query.filter_by(user_id=user_id).count()
            if row.status == WORKOUT_SESSION_COMPLETED:
                assert pumps == 1
            else:
                assert pumps == 0
    finally:
        _teardown(flask_app)
