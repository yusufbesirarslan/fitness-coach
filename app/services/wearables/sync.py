from datetime import datetime

from app.extensions import db
from app.models import (UserWearableConnection, WearableActivityLog,
                        WearableSleepLog, WearableWorkoutLog)
from app.services.wearables.adapters import get_adapter
from app.timeutil import app_today


def _copy_attrs(target, source, names):
    for name in names:
        setattr(target, name, getattr(source, name))


def _upsert_sleep(log):
    row = WearableSleepLog.query.filter_by(
        user_id=log.user_id, provider=log.provider, source_id=log.source_id
    ).first()
    if row is None:
        db.session.add(log)
        return log
    _copy_attrs(row, log, [
        "date_key", "start_at", "end_at", "duration_min", "deep_sleep_min",
        "rem_sleep_min", "light_sleep_min", "awake_min", "sleep_efficiency",
        "sleep_score", "resting_heart_rate", "hrv_rmssd", "raw_payload",
    ])
    row.updated_at = datetime.utcnow()
    return row


def _upsert_workout(log):
    row = WearableWorkoutLog.query.filter_by(
        user_id=log.user_id, provider=log.provider, source_id=log.source_id
    ).first()
    if row is None:
        db.session.add(log)
        return log
    _copy_attrs(row, log, [
        "date_key", "sport_name", "start_at", "end_at", "calories_burned",
        "strain", "average_heart_rate", "max_heart_rate", "distance_km",
        "raw_payload",
    ])
    row.updated_at = datetime.utcnow()
    return row


def _upsert_activity(log):
    row = WearableActivityLog.query.filter_by(
        user_id=log.user_id, provider=log.provider, date_key=log.date_key
    ).first()
    if row is None:
        db.session.add(log)
        return log
    _copy_attrs(row, log, [
        "source_id", "steps", "active_minutes", "calories_burned",
        "resting_heart_rate", "hrv_rmssd", "raw_payload",
    ])
    row.updated_at = datetime.utcnow()
    return row


def sync_provider_day(user_id, provider, target_date=None):
    target_date = target_date or app_today()
    adapter = get_adapter(provider)
    sleeps = adapter.fetch_sleep(user_id, target_date)
    workouts = adapter.fetch_workouts(user_id, target_date)
    activity = adapter.fetch_activity(user_id, target_date)

    for sleep in sleeps:
        _upsert_sleep(sleep)
    for workout in workouts:
        _upsert_workout(workout)
    if activity is not None:
        _upsert_activity(activity)

    conn = UserWearableConnection.query.filter_by(
        user_id=user_id, provider=adapter.provider
    ).first()
    if conn:
        conn.last_sync_at = datetime.utcnow()
    db.session.commit()
    return {
        "provider": adapter.provider,
        "date_key": target_date.isoformat(),
        "sleep": len(sleeps),
        "workouts": len(workouts),
        "activity": 1 if activity is not None else 0,
    }
