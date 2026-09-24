"""Opt-in PostgreSQL proof that a standalone Pump Check is not completion proof
(Native Progress PR2, defect D1).

The hermetic SQLite suite (``tests/test_progress_pump_check_completion_proof.py``)
proves the rule. This module proves it where it can actually break: two real
connections on postgres:16. A standalone ``POST /api/v1/pump-checks`` row and
the canonical completion claim live in the SAME ``pump_check`` table, so a
standalone insert racing the completion transaction must neither suppress the
completion (no false ``already_completed``) nor collide with the
``uq_pump_check_day`` claim (standalone rows carry ``date_key IS NULL``, and
PostgreSQL treats NULLs as distinct inside a UNIQUE constraint).

Races:

* standalone creation vs canonical completion, released together by a
  ``threading.Barrier`` and repeated over fresh owners to vary the interleaving;
* standalone committed first, then canonical completion (the D1 ordering);
* canonical vs canonical completion with a standalone row already present —
  still exactly one winner and one ``already_completed`` loser.

Every assertion is made on the persisted rows. Gating matches the other
Postgres race modules: the ``pg_concurrency`` marker AND
``FITX_PG_CONCURRENCY_TEST=1`` AND a reachable ``PG_TEST_DATABASE_URL``;
unreachable Postgres SKIPS, never errors.

Run it:
    FITX_PG_CONCURRENCY_TEST=1 \
    PG_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fitx_test \
    python -m pytest -m pg_concurrency -q tests/test_progress_pump_check_completion_pg.py
"""
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

BASE_XP = 10
PHOTO_BONUS = 25
RACE_ROUNDS = 6


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


@pytest.fixture
def pg_app(monkeypatch):
    if not (_ENABLED and _PG_URL):
        pytest.skip(_SKIP_REASON)
    if not _pg_reachable(_PG_URL):
        pytest.skip(f"Postgres not reachable at PG_TEST_DATABASE_URL ({_SKIP_REASON})")

    os.environ["DATABASE_URL"] = _PG_URL
    os.environ["FITX_SKIP_DB_INIT"] = "1"

    from app import create_app
    from app.extensions import db
    from app.services.mobile_pump_checks import service as pump_service

    flask_app = create_app()
    flask_app.config["TESTING"] = True

    monkeypatch.setattr(pump_service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(
        pump_service.s3_helper, "upload_image",
        lambda *args, user_id=None, **kwargs: f"pump-checks/{user_id}/private.jpg")
    monkeypatch.setattr(pump_service, "analyze_image", lambda *a, **k: {
        "summary": "Visible result.", "observations": [], "strengths": [],
        "focus_areas": [], "limitations": ["Single image."],
        "next_check_guidance": "Repeat framing.", "quality": "limited",
    })

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
    try:
        yield flask_app
    finally:
        with flask_app.app_context():
            # Dispose first: a pooled connection left mid-transaction would hold
            # locks and block drop_all's ACCESS EXCLUSIVE DROP TABLE.
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _make_user(flask_app, tag):
    from app.extensions import db
    from app.models import User

    with flask_app.app_context():
        user = User(username=f"pg_d1_{tag}", email=f"pg_d1_{tag}@example.invalid",
                    cognito_sub=f"sub-pg-d1-{tag}")
        db.session.add(user)
        db.session.commit()
        return user.id


def _completion_command(user_id, today):
    from app.services.workout_completion import CompleteWorkoutCommand

    return CompleteWorkoutCommand(
        user_id=user_id, today=today, location_type="salon",
        description="d1 race", workout_score=7.0, visibility="private",
        base_xp=BASE_XP, photo_bonus=PHOTO_BONUS, activity_text="d1 race",
        entry_path="pg_test",
    )


def _standalone_command():
    from app.services.mobile_pump_checks.service import CreateCommand

    return CreateCommand(
        image_bytes=b"stable-image", media_type="image/jpeg",
        body_region="upper_body", environment="gym", description="",
        captured_at=datetime.utcnow().replace(microsecond=0),
    )


def _complete(flask_app, user_id, today):
    """One canonical completion in its own app context / connection."""
    from app.extensions import db
    from app.models import User
    from app.services.workout_completion import complete_workout

    with flask_app.app_context():
        # Strong reference, as Flask-Login's current_user holds in a request (see
        # tests/test_workout_completion_pg.py faithful-context note).
        resident = db.session.get(User, user_id)
        assert resident is not None
        try:
            return complete_workout(_completion_command(user_id, today)).outcome
        finally:
            db.session.remove()


def _standalone(flask_app, user_id, key):
    from app.extensions import db
    from app.services.mobile_pump_checks.service import create_or_replay

    with flask_app.app_context():
        try:
            row, created = create_or_replay(user_id, key, _standalone_command())
            return created, row.date_key
        finally:
            db.session.remove()


def _run_together(*callables):
    barrier = threading.Barrier(len(callables))
    results = {}

    def contender(index, fn):
        barrier.wait(timeout=10)
        try:
            results[index] = ("ok", fn())
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            results[index] = ("raise", f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=contender, args=(i, fn), daemon=True)
               for i, fn in enumerate(callables)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), results
    return [results[i] for i in range(len(callables))]


def _persisted(flask_app, user_id):
    from app.extensions import db
    from app.models import WORKOUT_COMPLETION_MARKER, PumpCheck, User, WorkoutLog
    from app.services.workout_completion import already_completed_today
    from app.services.workout_state import resolve_workout_state
    from app.timeutil import app_today

    with flask_app.app_context():
        rows = PumpCheck.query.filter_by(user_id=user_id).all()
        state = {
            "standalone": sum(1 for r in rows if r.date_key is None),
            "proofs": sum(1 for r in rows if r.date_key is not None),
            "markers": WorkoutLog.query.filter_by(
                user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER).count(),
            "xp": db.session.get(User, user_id).rank_points,
            "completed": already_completed_today(user_id, app_today()),
            "state_completed": resolve_workout_state(user_id).completed_today,
        }
        db.session.remove()
        return state


def test_standalone_creation_racing_completion_never_suppresses_it(pg_app):
    from app.services.workout_completion import CompletionOutcome
    from app.timeutil import app_today

    today = app_today()
    for round_no in range(RACE_ROUNDS):
        user_id = _make_user(pg_app, f"race{round_no}")
        standalone, completion = _run_together(
            lambda: _standalone(pg_app, user_id, f"d1-race-key-{round_no:04d}"),
            lambda: _complete(pg_app, user_id, today),
        )
        assert standalone == ("ok", (True, None)), (round_no, standalone)
        assert completion == ("ok", CompletionOutcome.CREATED), (round_no, completion)
        assert _persisted(pg_app, user_id) == {
            "standalone": 1, "proofs": 1, "markers": 1,
            "xp": BASE_XP + PHOTO_BONUS, "completed": True, "state_completed": True,
        }, round_no


def test_standalone_committed_first_then_completion_is_created(pg_app):
    from app.services.workout_completion import CompletionOutcome
    from app.timeutil import app_today

    user_id = _make_user(pg_app, "ordered")
    assert _standalone(pg_app, user_id, "d1-ordered-key-0001") == (True, None)
    before = _persisted(pg_app, user_id)
    assert before["completed"] is False
    assert before["state_completed"] is False

    assert _complete(pg_app, user_id, app_today()) == CompletionOutcome.CREATED
    assert _persisted(pg_app, user_id) == {
        "standalone": 1, "proofs": 1, "markers": 1,
        "xp": BASE_XP + PHOTO_BONUS, "completed": True, "state_completed": True,
    }


def test_canonical_completion_race_stays_single_winner_beside_standalone(pg_app):
    from app.services.workout_completion import CompletionOutcome
    from app.timeutil import app_today

    today = app_today()
    user_id = _make_user(pg_app, "double")
    assert _standalone(pg_app, user_id, "d1-double-key-0001") == (True, None)

    outcomes = _run_together(
        lambda: _complete(pg_app, user_id, today),
        lambda: _complete(pg_app, user_id, today),
    )
    assert all(kind == "ok" for kind, _ in outcomes), outcomes
    values = [value for _, value in outcomes]
    assert values.count(CompletionOutcome.CREATED) == 1, values
    assert values.count(CompletionOutcome.ALREADY_COMPLETED) == 1, values
    assert _persisted(pg_app, user_id) == {
        "standalone": 1, "proofs": 1, "markers": 1,
        "xp": BASE_XP + PHOTO_BONUS, "completed": True, "state_completed": True,
    }


def test_another_users_rows_do_not_complete_this_user(pg_app):
    from app.timeutil import app_today

    owner = _make_user(pg_app, "owner")
    other = _make_user(pg_app, "other")
    assert _standalone(pg_app, other, "d1-other-key-0001") == (True, None)
    _complete(pg_app, other, app_today())

    state = _persisted(pg_app, owner)
    assert state == {
        "standalone": 0, "proofs": 0, "markers": 0, "xp": state["xp"],
        "completed": False, "state_completed": False,
    }
