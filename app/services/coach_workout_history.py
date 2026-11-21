"""Closed Coach projection of recent completed-session checkpoint facts."""
import json

from app.services.exercise_catalog import ExerciseResolutionError, resolve_historical_exercise
from app.services.exercise_performance_history.models import ROW_ELIGIBLE, ROW_EXCLUDED
from app.services.exercise_performance_history.queries import load_candidates
from app.services.exercise_performance_history.selection import classify, performed_sets

_WORKOUT_LOOKBACK = 13
_MAX_SESSIONS = 4
_MAX_EXERCISES = 8
_MAX_SETS = 8


def workout_history(user_id, today):
    rows, truncated = load_candidates(user_id, today, lookback=_WORKOUT_LOOKBACK)
    sessions = []
    for row in rows:
        verdict = classify(row)
        if verdict.row_class == ROW_EXCLUDED:
            continue
        if len(sessions) == _MAX_SESSIONS:
            truncated = True
            break
        exercises = []
        incomplete = verdict.row_class != ROW_ELIGIBLE
        for identity in verdict.exercises:
            exercise_id = identity["exercise_id"]
            sets = performed_sets(verdict.exercises, exercise_id)
            if not sets:
                continue
            try:
                name = resolve_historical_exercise(exercise_id).canonical_name
            except ExerciseResolutionError:
                incomplete = True
                continue
            exercises.append({
                "exercise": name,
                "sets_completed": len(sets),
                "sets": [{"index": item.index, "reps": item.reps,
                          "weight_kg": item.weight_kg} for item in sets[:_MAX_SETS]],
                "sets_truncated": len(sets) > _MAX_SETS,
            })
        session = {"day": verdict.workout_date.isoformat() if verdict.workout_date else None,
                   "exercise_details": "unavailable" if not exercises else
                   "partial" if incomplete or len(exercises) > _MAX_EXERCISES else "available",
                   "exercises": exercises[:_MAX_EXERCISES]}
        sessions.append(session)
    return json.dumps({"window_days": _WORKOUT_LOOKBACK + 1, "sessions": sessions,
                       "sessions_truncated": truncated})
