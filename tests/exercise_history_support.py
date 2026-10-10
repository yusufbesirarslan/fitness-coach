"""Deterministic TD-01 fixture builders shared by the unit, parity and PG suites.

Rows are built as the five selected columns (``CandidateRow``) and persisted as
real ``WorkoutSession`` rows when a test needs the query. Checkpoints are the
exact canonical snapshot shape; corrupt variants are explicit strings.
"""
import json
from datetime import date, datetime, timedelta

from app.services.exercise_performance_history.models import CandidateRow
from app.timeutil import APP_TZ

SQUAT = "ex_barbell_back_squat"
BENCH = "ex_barbell_bench_press"
ROW = "ex_barbell_row"
PUSH_UP = "ex_push_up"
ANCHOR = date(2026, 10, 1)
# 15:00 Istanbul on the anchor day: the pinned application clock.
ANCHOR_NOW = datetime(2026, 10, 1, 15, 0, tzinfo=APP_TZ)
_UNSET = object()


def day(offset_days):
    return ANCHOR + timedelta(days=offset_days)


def utc_noon(on, minute=0, second=0):
    """Naive-UTC ``completed_at`` whose Istanbul date is ``on`` (15:mm local)."""
    return datetime(on.year, on.month, on.day, 12, minute, second)


def snapshot(exercises):
    """``exercises``: ordered ``[(exercise_id, [(index, completed, reps, kg), ...])]``."""
    return {"current_exercise_index": 0, "elapsed_seconds": 1200, "exercises": [
        {"exercise_id": exercise_id, "sets": [
            {"index": index, "completed": completed, "reps": reps, "weight_kg": weight}
            for index, completed, reps, weight in sets]}
        for exercise_id, sets in exercises]}


def checkpoint_json(exercises):
    return json.dumps(snapshot(exercises))


def straight(load, *reps, completed=True):
    return [(index, completed, count, load) for index, count in enumerate(reps)]


def candidate(ref, on=ANCHOR, exercises=None, *, minute=0, second=0, revision=3,
              completed_at=_UNSET, checkpoint_data=_UNSET, workout_date=_UNSET):
    """One ``CandidateRow``; by default a clean squat session completed at 15:mm
    Istanbul on ``on``."""
    if exercises is None:
        exercises = [(SQUAT, straight(60.0, 8, 8))]
    return CandidateRow(
        public_id=ref,
        workout_date=on.isoformat() if workout_date is _UNSET else workout_date,
        completed_at=utc_noon(on, minute, second) if completed_at is _UNSET else completed_at,
        checkpoint_revision=revision,
        checkpoint_data=checkpoint_json(exercises) if checkpoint_data is _UNSET else checkpoint_data,
    )


def persist(user_id, row, *, status="completed", execution_context_data=None,
            prescription_data=None):
    """Store ``row`` as a real ``WorkoutSession`` for ``user_id`` and commit."""
    from app.extensions import db
    from app.models import WorkoutSession

    started = (row.completed_at or utc_noon(ANCHOR)) - timedelta(minutes=50)
    db.session.add(WorkoutSession(
        public_id=row.public_id, user_id=user_id, status=status,
        workout_date=row.workout_date, weekday_slot="Perşembe", source="scheduled",
        started_at=started, last_activity_at=started + timedelta(minutes=50),
        completed_at=row.completed_at if status == "completed" else None,
        checkpoint_revision=row.checkpoint_revision, checkpoint_data=row.checkpoint_data,
        execution_context_data=execution_context_data, prescription_data=prescription_data,
    ))
    db.session.commit()
