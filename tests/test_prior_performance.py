"""Prior-workout defaults read completed session checkpoints and nothing else."""
import json
from datetime import datetime, timedelta

from app.extensions import db
from app.models import WORKOUT_SESSION_ABANDONED, WORKOUT_SESSION_ACTIVE, WorkoutSession
from app.services.workout_session.prior_performance import (
    load_prior_performance,
    select_historical_defaults,
)
from test_sprint14_workout_execution_contract import BENCH, ROW, SQUAT


def _session(user_id, public_id, status, snapshot, completed_at=None):
    row = WorkoutSession(
        public_id=public_id,
        user_id=user_id,
        status=status,
        workout_date="2020-01-02",
        weekday_slot="Pazartesi",
        source="scheduled",
        completed_at=completed_at,
        checkpoint_revision=1,
        checkpoint_data=json.dumps(snapshot),
    )
    db.session.add(row)
    return row


def _snapshot(exercise_id, sets):
    return {
        "current_exercise_index": 0,
        "elapsed_seconds": 30,
        "exercises": [{"exercise_id": exercise_id, "sets": sets}],
    }


def test_select_historical_defaults_uses_the_first_completed_set_of_the_newest_session():
    older = _snapshot(SQUAT, [
        {"index": 0, "completed": True, "reps": 10, "weight_kg": 40},
    ])
    newer = {
        "current_exercise_index": 0,
        "elapsed_seconds": 30,
        "exercises": [
            {"exercise_id": SQUAT, "sets": [
                {"index": 0, "completed": False, "reps": None, "weight_kg": None},
                {"index": 1, "completed": True, "reps": 8, "weight_kg": 60},
                {"index": 2, "completed": True, "reps": 5, "weight_kg": 80},
            ]},
            {"exercise_id": BENCH, "sets": [
                {"index": 0, "completed": True, "reps": 12, "weight_kg": None},
            ]},
            {"exercise_id": ROW, "sets": [
                {"index": 0, "completed": False, "reps": 10, "weight_kg": 100},
            ]},
        ],
    }
    assert select_historical_defaults([newer, older]) == {
        SQUAT: {"weight_kg": 60, "reps": 8},
        BENCH: {"weight_kg": None, "reps": 12},
    }


def test_load_prior_performance_ignores_other_users_and_non_completed_sessions(
    app, auth_user, make_user,
):
    other = make_user("other-history")
    now = datetime.utcnow()
    with app.app_context():
        _session(auth_user.id, "prior-squat-new", "completed", _snapshot(SQUAT, [
            {"index": 0, "completed": True, "reps": 8, "weight_kg": 60},
            {"index": 1, "completed": True, "reps": 5, "weight_kg": 80},
        ]), completed_at=now)
        _session(auth_user.id, "prior-squat-old", "completed", _snapshot(SQUAT, [
            {"index": 0, "completed": True, "reps": 10, "weight_kg": 40},
        ]), completed_at=now - timedelta(days=3))
        _session(auth_user.id, "prior-bench-bw", "completed", _snapshot(BENCH, [
            {"index": 0, "completed": True, "reps": 12, "weight_kg": None},
        ]), completed_at=now - timedelta(days=1))
        _session(other.id, "prior-other-user", "completed", _snapshot(SQUAT, [
            {"index": 0, "completed": True, "reps": 3, "weight_kg": 200},
        ]), completed_at=now)
        _session(auth_user.id, "prior-abandoned", WORKOUT_SESSION_ABANDONED, _snapshot(SQUAT, [
            {"index": 0, "completed": True, "reps": 1, "weight_kg": 150},
        ]), completed_at=now)
        _session(auth_user.id, "prior-active", WORKOUT_SESSION_ACTIVE, _snapshot(ROW, [
            {"index": 0, "completed": True, "reps": 4, "weight_kg": 100},
        ]))
        db.session.commit()
        loaded = load_prior_performance(auth_user.id)

    assert loaded == {
        SQUAT: {"weight_kg": 60.0, "reps": 8},
        BENCH: {"weight_kg": None, "reps": 12},
    }
