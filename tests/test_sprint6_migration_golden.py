"""Characterization ("golden") tests for the Sprint 6 PR1 migrations.

Every runtime path that moved onto the shared ``app.services.training_history``
foundation is pinned here against a FROZEN COPY of its pre-migration
implementation (the exact legacy SQL). The assertion is always::

    live_production_function(...) == frozen_legacy_reference(...)

so any future refactor that silently changes windowing, marker handling, session
counting, or volume aggregation on these paths fails loudly instead of drifting.
Each scenario also carries an independent concrete-value anchor, so the guard
survives even if someone edits the legacy reference and the live code together.

Migrated paths covered (see docs/handoff.md, Sprint 6 PR1):
- ``app.services.training_generation.time_series_model.build_performance_history``
- ``app.services.ai_coach._today_workout_totals``

DB-backed, in-memory SQLite via the ``app`` / ``make_user`` fixtures (like
tests/test_analytics_engine.py). Seed datetimes are noon UTC so the Istanbul day
is unambiguous; all windows are derived from ``app_today()`` because the
production functions read the clock internally (not injectable).

    python -m pytest tests/test_sprint6_migration_golden.py -v
"""
from datetime import datetime, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER,
    PumpCheck,
    WeeklyCheckIn,
    WorkoutLog,
)
from app.services.ai_coach import _today_workout_totals
from app.services.training_generation.models import PerformanceHistory
from app.services.training_generation.time_series_model import (
    build_performance_history,
)
from app.timeutil import app_today, utc_day_bounds


# ---------------------------------------------------------------------------
# Frozen legacy references — verbatim copies of the pre-Sprint-6 implementations.
# DO NOT "modernize" these to call the training_history foundation: their whole
# purpose is to be the independent oracle the migrated code is compared against.
# If a legacy reference and its live counterpart must both change, that is a
# deliberate semantic change and belongs in a reviewed commit — never a silent one.
# ---------------------------------------------------------------------------

def _legacy_today_workout_totals(user_id):
    """Pre-migration ``ai_coach._today_workout_totals`` (D4 marker exclusion)."""
    start, end = utc_day_bounds()
    row = db.session.query(
        func.coalesce(func.sum(WorkoutLog.volume), 0),
        func.count(WorkoutLog.id),
    ).filter(
        WorkoutLog.user_id == user_id,
        WorkoutLog.created_at >= start,
        WorkoutLog.created_at < end,
        WorkoutLog.exercise_name != WORKOUT_COMPLETION_MARKER,
    ).first()
    return {"total_volume": round(row[0], 1), "entry_count": row[1]}


def _legacy_build_performance_history(user_id):
    """Pre-migration ``time_series_model.build_performance_history``.

    Sessions = COUNT(*) over the 7-day window INCLUDING markers; volume =
    SUM(volume) EXCLUDING markers. PumpCheck / WeeklyCheckIn logic was not
    migrated but is reproduced here so the whole return value is pinned.
    """
    today = app_today()
    weekly_sessions: list[int] = []
    volume_trend: list[float] = []
    for offset in (21, 14, 7, 0):
        start_day = today - timedelta(days=offset)
        start, _ = utc_day_bounds(start_day)
        _, end = utc_day_bounds(start_day + timedelta(days=6))
        sessions = WorkoutLog.query.filter(
            WorkoutLog.user_id == user_id,
            WorkoutLog.created_at >= start,
            WorkoutLog.created_at < end,
        ).count()
        volume = WorkoutLog.query.with_entities(
            func.coalesce(func.sum(WorkoutLog.volume), 0)
        ).filter(
            WorkoutLog.user_id == user_id,
            WorkoutLog.created_at >= start,
            WorkoutLog.created_at < end,
            WorkoutLog.exercise_name != WORKOUT_COMPLETION_MARKER,
        ).scalar() or 0
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


# ---------------------------------------------------------------------------
# Seed helpers — noon UTC keeps the Istanbul day unambiguous.
# ---------------------------------------------------------------------------

def _add_workout(user_id, day, volume=100.0, sets=3, reps=10, weight=50.0, marker=False):
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _add_pump(user_id, day):
    db.session.add(PumpCheck(
        user_id=user_id,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _add_checkin(user_id, day, fatigue=3, sleep=3):
    db.session.add(WeeklyCheckIn(
        user_id=user_id, weight=80.0, fatigue=fatigue, uyku_kalitesi=sleep,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


# ===========================================================================
# Path 1: ai_coach._today_workout_totals
# ===========================================================================

def test_today_workout_totals_matches_legacy_rich(make_user):
    user = make_user("golden_today")
    other = make_user("golden_today_other")
    today = app_today()

    _add_workout(user.id, today, volume=100, sets=3)
    _add_workout(user.id, today, volume=200, sets=4)
    _add_workout(user.id, today, marker=True)                    # excluded (D4)
    _add_workout(user.id, today - timedelta(days=1), volume=500)  # yesterday, excluded
    _add_workout(other.id, today, volume=999)                    # other user, excluded
    db.session.commit()

    result = _today_workout_totals(user.id)
    assert result == _legacy_today_workout_totals(user.id)
    # Independent anchor: markers/other-day/other-user all excluded.
    assert result == {"total_volume": 300.0, "entry_count": 2}


def test_today_workout_totals_empty_matches_legacy(make_user):
    user = make_user("golden_today_empty")
    result = _today_workout_totals(user.id)
    assert result == _legacy_today_workout_totals(user.id)
    assert result == {"total_volume": 0, "entry_count": 0}


def test_today_workout_totals_marker_only_excluded(make_user):
    user = make_user("golden_today_marker")
    _add_workout(user.id, app_today(), marker=True)
    db.session.commit()

    result = _today_workout_totals(user.id)
    assert result == _legacy_today_workout_totals(user.id)
    # A "session happened" marker must not inflate today's volume/entry count.
    assert result == {"total_volume": 0, "entry_count": 0}


# ===========================================================================
# Path 2: time_series_model.build_performance_history
# ===========================================================================

def test_build_performance_history_matches_legacy_rich(make_user):
    """One scenario exercising every migrated branch: marker duality, multi-entry
    windows, the PumpCheck ``max()`` path, an empty window, boundary exclusion,
    and user scoping — pinned against the frozen legacy oracle and a concrete anchor."""
    user = make_user("golden_perf")
    other = make_user("golden_perf_other")
    today = app_today()

    def d(days_ago):
        return today - timedelta(days=days_ago)

    # Window offset=21 → [today-21 .. today-15]: two real + a same-day marker,
    # plus a marker-only day → count INCLUDES markers, volume EXCLUDES them.
    _add_workout(user.id, d(21), volume=100, sets=3)
    _add_workout(user.id, d(21), volume=200, sets=4)
    _add_workout(user.id, d(21), marker=True)
    _add_workout(user.id, d(18), marker=True)
    # Window offset=14 → [today-14 .. today-8]: only a PumpCheck → max(0, 1).
    _add_pump(user.id, d(11))
    # Window offset=7 → [today-7 .. today-1]: intentionally empty.
    # Window offset=0 → [today .. today+6]: one real session today.
    _add_workout(user.id, today, volume=500, sets=6)
    # Just outside both outer edges → ignored by both implementations.
    _add_workout(user.id, d(22), volume=999)
    _add_workout(user.id, today + timedelta(days=7), volume=999)
    # Another user's rows in the same windows → must not leak in.
    _add_workout(other.id, d(21), volume=777)
    _add_pump(other.id, d(11))
    # Check-ins drive fatigue/sleep (not migrated; pinned in the whole output).
    _add_checkin(user.id, d(3), fatigue=4, sleep=2)
    _add_checkin(user.id, d(10), fatigue=2, sleep=5)
    db.session.commit()

    result = build_performance_history(user.id)
    assert result == _legacy_build_performance_history(user.id)
    # Independent anchor (oldest→newest window):
    assert result == PerformanceHistory(
        weekly_training_sessions=[4, 1, 0, 1],   # W1 incl. 2 markers; W2 pump-only; W3 empty; W4 real
        volume_trend=[300.0, 0.0, 0.0, 500.0],   # markers contribute no volume
        adherence_score=0.75,                    # 3 of 4 windows active
        fatigue_trend=3.0,                        # mean(4, 2)
        sleep_quality=3.5,                        # mean(2, 5)
        stable_score_weeks=1,                     # only the newest window is active
        dropout_risk=False,                       # newest window is non-empty
    )


def test_build_performance_history_empty_matches_legacy(make_user):
    user = make_user("golden_perf_empty")
    result = build_performance_history(user.id)
    assert result == _legacy_build_performance_history(user.id)
    assert result == PerformanceHistory(
        weekly_training_sessions=[0, 0, 0, 0],
        volume_trend=[0.0, 0.0, 0.0, 0.0],
        adherence_score=0.0,
        fatigue_trend=3.0,
        sleep_quality=3.0,
        stable_score_weeks=0,
        dropout_risk=False,
    )


def test_build_performance_history_dropout_risk_matches_legacy(make_user):
    """Older windows active, newest window empty → dropout_risk True. Pins that the
    migrated session count still drives the dropout signal identically."""
    user = make_user("golden_perf_dropout")
    today = app_today()
    _add_workout(user.id, today - timedelta(days=21), volume=100)  # W1
    _add_workout(user.id, today - timedelta(days=14), volume=100)  # W2
    # W3 and W4 (newest) intentionally empty.
    db.session.commit()

    result = build_performance_history(user.id)
    assert result == _legacy_build_performance_history(user.id)
    assert result.weekly_training_sessions == [1, 1, 0, 0]
    assert result.dropout_risk is True


def test_migrated_paths_are_deterministic(make_user):
    """Repeated reads of the same fixed state return identical results — no hidden
    clock or ordering effects on either migrated path."""
    user = make_user("golden_determinism")
    today = app_today()
    _add_workout(user.id, today, volume=100)
    _add_workout(user.id, today - timedelta(days=10), volume=200)
    db.session.commit()

    assert _today_workout_totals(user.id) == _today_workout_totals(user.id)
    assert build_performance_history(user.id) == build_performance_history(user.id)
