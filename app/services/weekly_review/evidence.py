"""Minimal evidence captured from committed rows at the worker's first read."""
import math

from app.services.mobile_weekly_checkin.contract import OVERLOAD_STORED_TO_WIRE
from app.services.mobile_weekly_checkin.history import prior_weight_day_row, recorded_rating
from app.services.progress_history import historical_body
from app.services.weekly_checkin import queries
from app.services.weekly_checkin.models import NOT_APPLICABLE, recorded_nutrition_adherence
from app.timeutil import app_date_of

_METRICS = {
    "training_intensity": lambda row: recorded_rating(row.yogunluk),
    "fatigue": lambda row: recorded_rating(row.fatigue),
    "sleep_quality": lambda row: recorded_rating(row.uyku_kalitesi),
    "progressive_overload": lambda row: OVERLOAD_STORED_TO_WIRE.get(row.progressive_overload),
}


def load(owner, check_in_id, current):
    previous = queries.previous_full_checkin(owner, check_in_id)
    day = app_date_of(current.created_at)
    if day is None:
        return None

    weight_row = prior_weight_day_row(owner, day)
    weight, delta = historical_body(current, weight_row)
    metrics = {key: read(current) for key, read in _METRICS.items()}
    changes = {}
    if previous is not None:
        for key, read in _METRICS.items():
            old, new = read(previous), metrics[key]
            if old is not None and new is not None and old != new:
                changes[key] = {"before": old, "after": new}
    nutrition = recorded_nutrition_adherence(current)
    scale, adherence = nutrition.scale, nutrition.value
    if previous is not None and scale is not None and adherence not in (None, NOT_APPLICABLE):
        old_nutrition = recorded_nutrition_adherence(previous)
        old_scale, old = old_nutrition.scale, old_nutrition.value
        if scale == old_scale and old not in (None, NOT_APPLICABLE, adherence):
            changes["nutrition_adherence"] = {"scale": scale, "before": old, "after": adherence}
    if delta is not None and math.isfinite(delta) and delta != 0:
        changes["weight_kg"] = {"delta": delta}

    recorded = {key: value for key, value in metrics.items() if value is not None}
    if weight is not None and math.isfinite(weight):
        recorded["weight_kg"] = weight
    if scale is not None:
        recorded["nutrition_adherence"] = {"scale": scale, "value": adherence}

    return {
        "analysis_day": day.isoformat(),
        "recorded": recorded,
        "changes": changes,
    }
