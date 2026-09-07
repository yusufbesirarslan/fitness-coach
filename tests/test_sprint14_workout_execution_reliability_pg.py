"""Sprint 14 PR4 deterministic PostgreSQL workout-execution proofs.

This module is named explicitly by the CI PostgreSQL job.  Every competing
writer uses a distinct application context/connection, and coordination uses
Barrier/Event primitives only -- never timing sleeps.
"""
import json
import threading

import pytest

from tests.test_mobile_workout_sessions_pg import (
    _ENABLED,
    _PG_URL,
    _SKIP_REASON,
    _completion_kwargs,
    _make_pg_app,
    _race,
    _require_pg,
    _snapshot,
    _teardown,
)

pytestmark = [
    pytest.mark.pg_concurrency,
    pytest.mark.skipif(not (_ENABLED and _PG_URL), reason=_SKIP_REASON),
]


def _thread(flask_app, work):
    """Run one command on its own scoped session and retain its outcome."""
    from app.extensions import db

    outcome = {}

    def run():
        with flask_app.app_context():
            try:
                outcome["value"] = ("ok", work())
            except Exception as exc:  # pragma: no cover - asserted by caller
                outcome["value"] = ("raise", type(exc).__name__)
            finally:
                db.session.remove()

    worker = threading.Thread(target=run)
    worker.start()
    return worker, outcome


def _join(worker, outcome):
    worker.join(timeout=30)
    assert not worker.is_alive(), "database contender did not finish"
    assert "value" in outcome
    return outcome["value"]


def _start_native(flask_app, user_id, reference):
    from app.services import mobile_workout_sessions as sessions

    secret = flask_app.config["SECRET_KEY"]
    with flask_app.app_context():
        return sessions.start(
            user_id, secret, reference
        ).payload["session"]["session_ref"]


def test_pr4_same_base_checkpoint_writers_have_one_persisted_winner():
    _require_pg()
    from app.models import WorkoutSession
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import (
        planned_exercise_identities,
        record_checkpoint,
    )

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    session_ref = _start_native(flask_app, user_id, reference)

    def native_writer():
        def work():
            result = sessions.checkpoint(
                user_id, secret, session_ref, "pr4-writer-native", 0,
                lambda allowed: sessions.parse_checkpoint(
                    _snapshot(60), allowed),
            )
            return result.payload["session"]["revision"]
        return work

    def browser_writer():
        result = record_checkpoint(
            user_id, session_ref, "pr4-writer-browser", 0,
            planned_exercise_identities,
            lambda allowed: sessions.parse_checkpoint(_snapshot(900), allowed),
        )
        return result.view.checkpoint_revision

    try:
        results = _race(flask_app, user_id, {
            "native": native_writer(),
            "browser": browser_writer,
        })
        assert sorted(kind for kind, _ in results.values()) == ["ok", "raise"]
        assert next(value for kind, value in results.values() if kind == "raise") \
            == "RevisionConflict"
        winner = 60 if results["native"][0] == "ok" else 900
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert row.checkpoint_revision == 1
            assert json.loads(row.checkpoint_data)["elapsed_seconds"] == winner
    finally:
        _teardown(flask_app)


def test_pr4_duplicate_checkpoint_is_one_write_plus_one_replay():
    _require_pg()
    from app.models import WorkoutSession
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    session_ref = _start_native(flask_app, user_id, reference)

    def work():
        result = sessions.checkpoint(
            user_id, secret, session_ref, "pr4-duplicate-key", 0,
            lambda allowed: sessions.parse_checkpoint(_snapshot(60), allowed),
        )
        return result.replayed, result.payload["session"]["revision"]

    try:
        results = _race(flask_app, user_id, {"a": work, "b": work})
        assert all(kind == "ok" for kind, _ in results.values())
        assert sorted(value[0] for _, value in results.values()) == [False, True]
        assert all(value[1] == 1 for _, value in results.values())
        with flask_app.app_context():
            assert WorkoutSession.query.filter_by(
                user_id=user_id).one().checkpoint_revision == 1
    finally:
        _teardown(flask_app)


def test_pr4_completion_commits_before_checkpoint_and_terminal_state_wins(monkeypatch):
    _require_pg()
    from app.models import (
        WORKOUT_COMPLETION_MARKER, WORKOUT_SESSION_COMPLETED,
        PumpCheck, WorkoutLog, WorkoutSession,
    )
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_completion import service as completion_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    session_ref = _start_native(flask_app, user_id, reference)
    locked = threading.Event()
    release = threading.Event()
    checkpoint_entered = threading.Event()
    original = completion_service.already_completed_today

    def hold_after_session_lock(*args, **kwargs):
        locked.set()
        assert release.wait(timeout=20)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        completion_service, "already_completed_today", hold_after_session_lock)

    def complete():
        return sessions.complete(
            user_id, session_ref, 0, **_completion_kwargs()).payload[
                "session"]["status"]

    def checkpoint():
        checkpoint_entered.set()
        return sessions.checkpoint(
            user_id, secret, session_ref, "pr4-after-complete", 0,
            lambda allowed: sessions.parse_checkpoint(_snapshot(120), allowed),
        ).payload["session"]["revision"]

    try:
        completion_thread, completion = _thread(flask_app, complete)
        assert locked.wait(timeout=20)
        checkpoint_thread, checkpoint_result = _thread(flask_app, checkpoint)
        assert checkpoint_entered.wait(timeout=20)
        release.set()
        assert _join(completion_thread, completion) == ("ok", WORKOUT_SESSION_COMPLETED)
        assert _join(checkpoint_thread, checkpoint_result) == ("raise", "SessionTerminal")
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert row.status == WORKOUT_SESSION_COMPLETED
            assert row.checkpoint_revision == 0
            assert PumpCheck.query.filter_by(user_id=user_id).count() == 1
            assert WorkoutLog.query.filter_by(
                user_id=user_id,
                exercise_name=WORKOUT_COMPLETION_MARKER,
            ).count() == 1
    finally:
        release.set()
        _teardown(flask_app)


def test_pr4_checkpoint_commits_before_completion_and_stale_completion_loses(monkeypatch):
    _require_pg()
    from app.models import WORKOUT_SESSION_ACTIVE, PumpCheck, WorkoutSession
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_completion import service as completion_service

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    session_ref = _start_native(flask_app, user_id, reference)
    completion_entered = threading.Event()
    release_completion = threading.Event()
    original = completion_service.lock_session_for_completion

    def hold_before_session_lock(*args, **kwargs):
        completion_entered.set()
        assert release_completion.wait(timeout=20)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        completion_service, "lock_session_for_completion", hold_before_session_lock)

    def complete():
        return sessions.complete(user_id, session_ref, 0, **_completion_kwargs())

    def checkpoint():
        return sessions.checkpoint(
            user_id, secret, session_ref, "pr4-before-complete", 0,
            lambda allowed: sessions.parse_checkpoint(_snapshot(180), allowed),
        ).payload["session"]["revision"]

    try:
        completion_thread, completion = _thread(flask_app, complete)
        assert completion_entered.wait(timeout=20)
        checkpoint_thread, checkpoint_result = _thread(flask_app, checkpoint)
        assert _join(checkpoint_thread, checkpoint_result) == ("ok", 1)
        release_completion.set()
        assert _join(completion_thread, completion) == (
            "raise", "RevisionConflict")
        with flask_app.app_context():
            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert row.status == WORKOUT_SESSION_ACTIVE
            assert row.checkpoint_revision == 1
            assert json.loads(row.checkpoint_data)["elapsed_seconds"] == 180
            assert PumpCheck.query.filter_by(user_id=user_id).count() == 0
    finally:
        release_completion.set()
        _teardown(flask_app)


def test_pr4_concurrent_starts_leave_one_active_physical_row():
    _require_pg()
    from app.models import WORKOUT_SESSION_ACTIVE, WorkoutSession
    from app.services import mobile_workout_sessions as sessions

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]

    def start():
        result = sessions.start(user_id, secret, reference)
        return result.status, result.payload["session"]["session_ref"]

    try:
        results = _race(flask_app, user_id, {"a": start, "b": start})
        assert all(kind == "ok" for kind, _ in results.values())
        assert sorted(value[0] for _, value in results.values()) == [200, 201]
        assert len({value[1] for _, value in results.values()}) == 1
        with flask_app.app_context():
            assert WorkoutSession.query.filter_by(
                user_id=user_id, status=WORKOUT_SESSION_ACTIVE).count() == 1
            assert WorkoutSession.query.filter_by(user_id=user_id).count() == 1
    finally:
        _teardown(flask_app)


@pytest.mark.parametrize("origin", ["browser", "native"])
def test_pr4_cross_transport_commands_keep_one_physical_row(origin):
    _require_pg()
    from app.models import WORKOUT_SESSION_COMPLETED, PumpCheck, WorkoutSession
    from app.services import mobile_workout_sessions as sessions
    from app.services.workout_session import (
        complete_session, planned_exercise_identities, record_checkpoint,
        start_session,
    )

    flask_app, user_id, reference = _make_pg_app()
    secret = flask_app.config["SECRET_KEY"]
    try:
        with flask_app.app_context():
            if origin == "browser":
                public_id = start_session(user_id).session.public_id
                assert WorkoutSession.query.one().workout_ref is None
                saved = sessions.checkpoint(
                    user_id, secret, public_id, "pr4-browser-native", 0,
                    lambda allowed: sessions.parse_checkpoint(_snapshot(60), allowed),
                )
                assert saved.payload["session"]["session_ref"] == public_id
                result = sessions.complete(
                    user_id, public_id, 1, **_completion_kwargs())
                assert result.payload["session"]["session_ref"] == public_id
            else:
                public_id = sessions.start(
                    user_id, secret, reference
                ).payload["session"]["session_ref"]
                saved = record_checkpoint(
                    user_id, public_id, "pr4-native-browser", 0,
                    planned_exercise_identities,
                    lambda allowed: sessions.parse_checkpoint(_snapshot(60), allowed),
                )
                assert saved.row.public_id == public_id
                result = complete_session(
                    user_id, public_id, expected_checkpoint_revision=1,
                    **_completion_kwargs(),
                )
                assert result.session.public_id == public_id

            row = WorkoutSession.query.filter_by(user_id=user_id).one()
            assert WorkoutSession.query.filter_by(user_id=user_id).count() == 1
            assert row.public_id == public_id
            assert row.checkpoint_revision == 1
            assert row.status == WORKOUT_SESSION_COMPLETED
            assert PumpCheck.query.filter_by(user_id=user_id).count() == 1
    finally:
        _teardown(flask_app)
