"""Tests for the training-history foundation (Sprint 6 PR1).

Two layers, matching repo convention:
- Pure analysis functions — no fixtures (like tests/test_calculations.py).
- DB-backed foundation reads/roll-up — `make_user` fixture (like
  tests/test_analytics_engine.py).

    python -m pytest tests/test_training_history.py -v
"""
from datetime import date, datetime

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_history import (
    WeeklyVolume,
    WorkoutEntry,
    bucket_by_week,
    build_training_history_summary,
    count_sessions,
    estimated_1rm,
    fetch_workout_entries,
    is_completion_marker,
    session_days,
    total_sets,
    total_volume,
    volume_trend,
    weekly_windows,
)

END_DAY = date(2026, 7, 15)  # weeks 4 → starts 06-24, 07-01, 07-08, 07-15


def _entry(day, volume=100.0, sets=3, reps=10, weight=50.0, marker=False):
    """Build a WorkoutEntry for pure tests (no DB)."""
    return WorkoutEntry(
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        performed_on=day, created_at=datetime(day.year, day.month, day.day, 12),
        is_marker=marker,
    )


# ---------------------------------------------------------------------------
# Pure analysis (fixture-free, deterministic)
# ---------------------------------------------------------------------------

def test_is_completion_marker():
    assert is_completion_marker(WORKOUT_COMPLETION_MARKER) is True
    assert is_completion_marker("Squat") is False


def test_estimated_1rm_epley():
    assert estimated_1rm(100, 10) == round(100 * (1 + 10 / 30), 2)  # 133.33
    assert estimated_1rm(100, 1) == 100.0  # a single rep is the 1RM


def test_estimated_1rm_guards_nonpositive():
    assert estimated_1rm(0, 5) == 0.0
    assert estimated_1rm(100, 0) == 0.0
    assert estimated_1rm(-10, 5) == 0.0


def test_total_volume_excludes_markers():
    entries = [_entry(date(2026, 7, 2), volume=100), _entry(date(2026, 7, 2), volume=200),
               _entry(date(2026, 7, 2), volume=0, marker=True)]
    assert total_volume(entries) == 300.0
    assert total_sets(entries) == 6  # 3 + 3, marker excluded


def test_session_days_and_count_include_markers():
    entries = [_entry(date(2026, 7, 2)), _entry(date(2026, 7, 2)),
               _entry(date(2026, 7, 5), marker=True)]
    assert session_days(entries) == {date(2026, 7, 2), date(2026, 7, 5)}
    assert count_sessions(entries) == 2  # marker-only day still counts as trained


def test_weekly_windows_oldest_first():
    assert weekly_windows(END_DAY, 4) == [
        date(2026, 6, 24), date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15)]
    assert weekly_windows(END_DAY, 0) == []


def test_bucket_by_week_buckets_and_excludes_out_of_range():
    entries = [
        _entry(date(2026, 7, 2), volume=100, sets=3),
        _entry(date(2026, 7, 5), volume=50, sets=2),
        _entry(date(2026, 7, 15), volume=500, sets=5),
        _entry(date(2026, 5, 1), volume=999),  # before all windows → ignored
    ]
    weekly = bucket_by_week(entries, END_DAY, 4)
    assert [w.week_start for w in weekly] == weekly_windows(END_DAY, 4)
    assert weekly[0].total_volume == 0.0                 # 06-24 week empty
    assert weekly[1].total_volume == 150.0               # 07-01 week: 100 + 50
    assert weekly[1].session_count == 2                  # two distinct days
    assert weekly[1].entry_count == 2
    assert weekly[1].total_sets == 5
    assert weekly[3].total_volume == 500.0               # 07-15 week


def test_volume_trend_directions():
    def wk(vol):
        return WeeklyVolume(week_start=date(2026, 7, 1), total_volume=vol)
    assert volume_trend([wk(100), wk(0), wk(200)]) == "up"
    assert volume_trend([wk(200), wk(100)]) == "down"
    assert volume_trend([wk(100), wk(102)]) == "flat"   # within ±5% band
    assert volume_trend([wk(0), wk(100)]) == "flat"     # only one active week
    assert volume_trend([]) == "flat"


def test_bucket_by_week_is_deterministic():
    entries = [_entry(date(2026, 7, 2), volume=100), _entry(date(2026, 7, 15), volume=500)]
    assert bucket_by_week(entries, END_DAY, 4) == bucket_by_week(entries, END_DAY, 4)


# ---------------------------------------------------------------------------
# DB-backed foundation reads / roll-up
# ---------------------------------------------------------------------------

def _add_workout(user_id, day, volume=100.0, sets=3, reps=10, weight=50.0, marker=False):
    """Seed a WorkoutLog on a specific Istanbul day (noon UTC is unambiguous)."""
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def test_fetch_workout_entries_marker_flag_and_filter(make_user):
    user = make_user("hist1")
    _add_workout(user.id, date(2026, 7, 2), volume=100)
    _add_workout(user.id, date(2026, 7, 2), volume=0, marker=True)
    db.session.commit()

    real = fetch_workout_entries(user.id, date(2026, 7, 1), date(2026, 7, 7))
    assert [e.volume for e in real] == [100]
    assert all(not e.is_marker for e in real)

    both = fetch_workout_entries(user.id, date(2026, 7, 1), date(2026, 7, 7), include_markers=True)
    assert len(both) == 2
    assert any(e.is_marker for e in both)
    assert both[0].performed_on == date(2026, 7, 2)  # Istanbul day of noon-UTC


def test_summary_windows_volumes_and_sessions(make_user):
    user = make_user("hist2")
    # Week 07-01: two real on 07-02 + marker on 07-02, one real on 07-05
    _add_workout(user.id, date(2026, 7, 2), volume=100, sets=3)
    _add_workout(user.id, date(2026, 7, 2), volume=200, sets=4)
    _add_workout(user.id, date(2026, 7, 2), volume=0, marker=True)
    _add_workout(user.id, date(2026, 7, 5), volume=50, sets=2)
    # Week 07-08: marker only (no real exercise) → counts as a trained day
    _add_workout(user.id, date(2026, 7, 9), volume=0, marker=True)
    # Week 07-15: one real
    _add_workout(user.id, date(2026, 7, 15), volume=500, sets=5)
    # Another user's row must not leak in
    other = make_user("hist2_other")
    _add_workout(other.id, date(2026, 7, 2), volume=9999)
    db.session.commit()

    s = build_training_history_summary(user.id, weeks=4, end_day=END_DAY)
    assert s.weeks == 4 and s.has_data is True
    assert [w.week_start for w in s.weekly] == weekly_windows(END_DAY, 4)
    assert s.weekly[0].total_volume == 0.0                       # 06-24 empty
    assert s.weekly[1].session_count == 2                        # 07-02 + 07-05
    assert s.weekly[1].entry_count == 3                          # marker excluded
    assert s.weekly[1].total_volume == 350.0
    assert s.weekly[1].total_sets == 9
    assert s.weekly[2].session_count == 1                        # marker-only day
    assert s.weekly[2].entry_count == 0
    assert s.weekly[3].total_volume == 500.0
    assert s.total_sessions == 4
    assert s.total_volume == 850.0
    assert s.volume_trend == "up"                                # 350 → 500


def test_summary_empty_history(make_user):
    user = make_user("hist3")
    s = build_training_history_summary(user.id, weeks=4, end_day=END_DAY)
    assert s.has_data is False
    assert s.total_sessions == 0
    assert s.total_volume == 0.0
    assert all(w.total_volume == 0.0 for w in s.weekly)


def test_summary_is_deterministic(make_user):
    user = make_user("hist4")
    _add_workout(user.id, date(2026, 7, 2), volume=100)
    _add_workout(user.id, date(2026, 7, 15), volume=200)
    db.session.commit()
    a = build_training_history_summary(user.id, weeks=4, end_day=END_DAY)
    b = build_training_history_summary(user.id, weeks=4, end_day=END_DAY)
    assert a == b
