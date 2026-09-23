"""First-set defaults from the most recent completed execution snapshot.

The canonical record of what the user actually lifted is the checkpoint stored
on a COMPLETED ``WorkoutSession``. This module reads those snapshots and picks
one value per canonical exercise id. It does not score, average, or progress
the load.

Selection, newest completed session first:

* the exercise id must match exactly
* the set must be completed
* reps must be a real integer
* weight is kept when it is a real kilogram value, including 0
* null weight stays null
* within that occurrence, the lowest set index that qualifies wins

Only the latest 24 completed checkpoints are read. An exercise that has not
appeared in that window has no default.
"""
from __future__ import annotations

import math

from sqlalchemy import nullslast

from app.extensions import db
from app.models import WORKOUT_SESSION_COMPLETED, WorkoutSession
from app.services.workout_session.checkpoint import (
    MAX_REPS,
    MAX_WEIGHT_KG,
    load_snapshot,
)

MAX_PRIOR_SESSIONS = 24


def usable_historical_set(sets) -> dict | None:
    """The first completed set with actual reps, in index order."""
    if not isinstance(sets, list):
        return None
    ordered = []
    for item in sets:
        if not isinstance(item, dict) or type(item.get("index")) is not int:
            continue
        ordered.append(item)
    ordered.sort(key=lambda item: item["index"])
    for item in ordered:
        if item.get("completed") is not True:
            continue
        reps = item.get("reps")
        if type(reps) is not int or reps < 0 or reps > MAX_REPS:
            continue
        weight = item.get("weight_kg")
        if weight is None:
            return {"weight_kg": None, "reps": reps}
        if type(weight) is bool or type(weight) not in (int, float):
            continue
        numeric = float(weight)
        if not math.isfinite(numeric) or numeric < 0 or numeric > MAX_WEIGHT_KG:
            continue
        return {"weight_kg": round(numeric, 1), "reps": reps}
    return None


def select_historical_defaults(snapshots) -> dict:
    """Newest-first snapshots to ``{exercise_id: {weight_kg, reps}}``."""
    found = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        exercises = snapshot.get("exercises")
        if not isinstance(exercises, list):
            continue
        for entry in exercises:
            if not isinstance(entry, dict):
                continue
            exercise_id = entry.get("exercise_id")
            if (not isinstance(exercise_id, str) or not exercise_id
                    or exercise_id in found):
                continue
            chosen = usable_historical_set(entry.get("sets"))
            if chosen is not None:
                found[exercise_id] = chosen
    return found


def load_prior_performance(user_id: int) -> dict:
    """Prior actuals for ``user_id`` from completed session checkpoints."""
    rows = (
        db.session.query(WorkoutSession.checkpoint_data)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_COMPLETED,
            WorkoutSession.checkpoint_data.isnot(None),
        )
        .order_by(
            nullslast(WorkoutSession.completed_at.desc()),
            WorkoutSession.id.desc(),
        )
        .limit(MAX_PRIOR_SESSIONS)
        .all()
    )
    snapshots = []
    for (raw,) in rows:
        parsed = load_snapshot(raw)
        if parsed is not None:
            snapshots.append(parsed)
    return select_historical_defaults(snapshots)
