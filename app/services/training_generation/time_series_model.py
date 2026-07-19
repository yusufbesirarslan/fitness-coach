from datetime import timedelta

from app.models import PumpCheck, WeeklyCheckIn
from app.services.training_generation.models import PerformanceHistory
from app.services.training_history import fetch_workout_entries, total_volume
from app.timeutil import app_today, utc_day_bounds


def build_performance_history(user_id: int) -> PerformanceHistory:
    today = app_today()
    weekly_sessions: list[int] = []
    volume_trend: list[float] = []
    for offset in (21, 14, 7, 0):
        start_day = today - timedelta(days=offset)
        window_end = start_day + timedelta(days=6)
        # WorkoutLog windowing/marker handling comes from the shared training-history
        # foundation (Sprint 6 PR1). Markers are included so the session count keeps
        # the prior COUNT(*) semantics; volume excludes them.
        entries = fetch_workout_entries(user_id, start_day, window_end, include_markers=True)
        sessions = len(entries)
        volume = total_volume(entries)
        start, _ = utc_day_bounds(start_day)
        _, end = utc_day_bounds(window_end)
        pump_sessions = PumpCheck.query.filter(
            PumpCheck.user_id == user_id,
            PumpCheck.created_at >= start,
            PumpCheck.created_at < end,
        ).count()
        weekly_sessions.append(max(sessions, pump_sessions))
        volume_trend.append(float(volume))

    adherence = sum(1 for s in weekly_sessions if s > 0) / 4
    stable_weeks = 0
    for sessions in reversed(weekly_sessions):
        if sessions > 0:
            stable_weeks += 1
        else:
            break

    checkins = (WeeklyCheckIn.query.filter_by(user_id=user_id)
                .order_by(WeeklyCheckIn.created_at.desc()).limit(4).all())
    fatigue_values = [c.fatigue for c in checkins if c.fatigue is not None]
    sleep_values = [c.uyku_kalitesi for c in checkins if c.uyku_kalitesi is not None]
    fatigue = sum(fatigue_values) / len(fatigue_values) if fatigue_values else 3.0
    sleep = sum(sleep_values) / len(sleep_values) if sleep_values else 3.0

    return PerformanceHistory(
        weekly_training_sessions=weekly_sessions,
        volume_trend=volume_trend,
        adherence_score=round(adherence, 2),
        fatigue_trend=round(fatigue, 2),
        sleep_quality=round(sleep, 2),
        stable_score_weeks=stable_weeks,
        dropout_risk=weekly_sessions[-1] == 0 and any(s > 0 for s in weekly_sessions[:-1]),
    )
