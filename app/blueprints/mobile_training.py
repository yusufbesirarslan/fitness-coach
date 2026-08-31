"""Bearer-authenticated native Training read routes."""
from flask import current_app, g, jsonify

from app.blueprints.mobile_api import bp, mobile_error
from app.mobile_auth_middleware import require_mobile_auth
from app.services import mobile_training


def _sessions_enabled():
    return bool(current_app.config.get("FITX_WORKOUT_SESSIONS_ENABLED", False))


@bp.get("/training/preferences")
@require_mobile_auth
def training_preferences():
    return jsonify(mobile_training.preference_contract())


@bp.get("/training/plans/current")
@require_mobile_auth
def current_training_plan():
    try:
        payload = mobile_training.build_current_plan(
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            sessions_enabled=_sessions_enabled(),
        )
    except mobile_training.PlanUnprojectable:
        return mobile_error(
            "TRAINING_PLAN_UNPROJECTABLE",
            "The current Training plan cannot be displayed safely.",
            409,
            False,
        )
    except mobile_training.TrainingReadUnavailable:
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    return jsonify(payload)


@bp.get("/training/workouts/<workout_reference>")
@require_mobile_auth
def training_workout(workout_reference):
    try:
        payload = mobile_training.build_workout(
            g.mobile_user.id,
            current_app.config["SECRET_KEY"],
            workout_reference,
        )
    except mobile_training.WorkoutNotFound:
        return mobile_error(
            "TRAINING_WORKOUT_NOT_FOUND",
            "The Training workout was not found.",
            404,
            False,
        )
    except mobile_training.WorkoutStale:
        return mobile_error(
            "TRAINING_WORKOUT_STALE",
            "The Training workout reference is stale.",
            409,
            False,
        )
    except mobile_training.PlanUnprojectable:
        return mobile_error(
            "TRAINING_PLAN_UNPROJECTABLE",
            "The current Training plan cannot be displayed safely.",
            409,
            False,
        )
    except mobile_training.TrainingReadUnavailable:
        return mobile_error(
            "TRAINING_READ_UNAVAILABLE",
            "Training is temporarily unavailable.",
            503,
            True,
        )
    return jsonify(payload)
