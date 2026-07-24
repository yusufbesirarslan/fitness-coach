"""Opt-in real-concurrency proof for the canonical completion mutation (Postgres).

Hermetic in-memory SQLite (the default suite) enforces ``uq_pump_check_day`` but
cannot reproduce production Postgres row-locking / multi-connection races. This
module adds a genuine two-contender race against a real Postgres — the same major
version as production/CI (``postgres:16``, see ``.github/workflows/ci.yml``).

It is **opt-in and never part of the default run**:

* gated behind the ``pg_concurrency`` marker AND two env vars —
  ``FITX_PG_CONCURRENCY_TEST=1`` and ``PG_TEST_DATABASE_URL`` (a reachable
  ``postgresql://…`` pointing at a disposable test database);
* it verifies connectivity first and *skips* (never errors) when Postgres is
  unavailable, so ordinary CI / developer runs are unaffected and no system
  infrastructure is required.

Strategy: two threads, each with its own app context and therefore its own
thread-local SQLAlchemy session/connection, are released simultaneously by a
``threading.Barrier`` (not sleeps) to contend for the same user+day completion.
After both finish we assert on the *persisted* rows: exactly one canonical
completion, a deterministic replay/conflict outcome for the loser, and no
duplicated PumpCheck / marker / XP side effects.

Faithful-context note: each contender first loads its ``User`` and keeps a
**strong reference** to it for the whole completion. In production the canonical
service is only ever invoked inside an authenticated request, where Flask-Login
holds ``current_user`` for the request's lifetime, so the row stays resident in
the session's (weak-ref) identity map. The leaderboard ``after_commit`` hook
(``gamification._flush_lb_dirty``) does ``db.session.get(User, id)``; with the
user resident that is a no-SQL identity-map hit, which is legal post-commit. A
detached worker thread that discards the loaded user would let it be
weak-ref-collected, forcing that ``get`` to emit SQL inside ``after_commit`` and
raising ``InvalidRequestError`` — an artifact of the test harness, never of the
production request path (proven: the real HTTP ``/workout/complete`` flow passes
against this same postgres:16). Holding the reference keeps the race faithful.

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


@pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON)
def test_concurrent_completion_has_single_winner_on_postgres():
    if not _pg_reachable(_PG_URL):
        pytest.skip(f"Postgres not reachable at PG_TEST_DATABASE_URL ({_SKIP_REASON})")

    # Configure a real-Postgres app. Env must be set before app import so config
    # reads the Postgres URL (mirrors conftest's hermetic-env discipline).
    os.environ["DATABASE_URL"] = _PG_URL
    os.environ["FITX_SKIP_DB_INIT"] = "1"

    from app import create_app
    from app.extensions import db
    from app.models import PumpCheck, User, WORKOUT_COMPLETION_MARKER, WorkoutLog
    from app.services.workout_completion import (
        CompleteWorkoutCommand,
        CompletionOutcome,
        complete_workout,
    )
    from app.timeutil import app_today

    flask_app = create_app()
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        # Isolated schema for a clean assertion surface.
        db.drop_all()
        db.create_all()
        user = User(username="pg_race", email="pg_race@example.com", cognito_sub="sub-pg")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        today = app_today()

    barrier = threading.Barrier(2)
    results = {}

    def _contender(tag):
        # Each thread gets its own app context → its own thread-local session and
        # DB connection, so the two completions are genuinely independent txns.
        with flask_app.app_context():
            # Keep a STRONG reference to the user for the whole completion, exactly
            # as Flask-Login's current_user does inside a real request. Without it
            # the row is weak-ref-collected from the identity map and the
            # leaderboard after_commit hook's db.session.get() would emit SQL on a
            # committed session (harness artifact, not a production defect). See the
            # module docstring's faithful-context note.
            resident_user = db.session.get(User, user_id)
            assert resident_user is not None
            cmd = CompleteWorkoutCommand(
                user_id=user_id,
                today=today,
                location_type="salon",
                description="race",
                workout_score=7.0,
                visibility="private",
                base_xp=10,
                photo_bonus=25,
                activity_text="race",
                entry_path="pg_test",
            )
            barrier.wait()  # release both contenders at the same instant
            try:
                results[tag] = ("ok", complete_workout(cmd).outcome)
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
        # Neither contender may crash with an unexpected internal error.
        assert all(kind == "ok" for kind, _ in outcomes), outcomes
        values = [outcome for _, outcome in outcomes]
        # Exactly one winner, one deterministic replay/conflict loser.
        assert values.count(CompletionOutcome.CREATED) == 1, values
        assert values.count(CompletionOutcome.ALREADY_COMPLETED) == 1, values

        with flask_app.app_context():
            assert PumpCheck.query.filter_by(user_id=user_id).count() == 1
            assert (
                WorkoutLog.query.filter_by(
                    user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER
                ).count()
                == 1
            )
            # XP credited exactly once (35 = 10 base + 25 photo, no quest seeded).
            assert db.session.get(User, user_id).rank_points == 35
    finally:
        with flask_app.app_context():
            # Dispose the pool first: a contender connection returned to the pool
            # while still mid-transaction would hold locks and make drop_all's
            # ACCESS EXCLUSIVE DROP TABLE block. Disposing closes them cleanly.
            db.session.remove()
            db.engine.dispose()
            db.drop_all()
