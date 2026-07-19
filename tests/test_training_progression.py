"""Tests for the progression-analysis layer (Sprint 6 PR2).

Two layers, matching repo convention (see tests/test_training_history.py):
- Pure analysis functions — no fixtures, deterministic (like tests/test_calculations.py).
- DB-backed roll-up (`build_progression_report`) — `make_user` fixture, fixed `END_DAY`.

    python -m pytest tests/test_training_progression.py -v
"""
from datetime import date, datetime

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_history import (
    WeeklyVolume,
    WorkoutEntry,
    estimated_1rm,
    weekly_windows,
)
from app.services.training_progression import (
    ProgressionReport,
    WeeklyStrength,
    assess_consistency,
    build_progression_report,
    derive_next_signal,
    detect_deload_due,
    detect_plateau,
    is_progressing,
    series_trend,
    weekly_best_estimated_1rm,
)

END_DAY = date(2026, 7, 15)          # weeks 4 → starts 06-24, 07-01, 07-08, 07-15
W0, W1, W2, W3 = weekly_windows(END_DAY, 4)


def _entry(day, volume=100.0, sets=3, reps=5, weight=50.0, marker=False):
    """Build a WorkoutEntry for pure tests (no DB)."""
    return WorkoutEntry(
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        performed_on=day, created_at=datetime(day.year, day.month, day.day, 12),
        is_marker=marker,
    )


def _wv(week_start, volume=0.0, sessions=0):
    return WeeklyVolume(week_start=week_start, session_count=sessions,
                        entry_count=sessions, total_sets=sessions, total_volume=volume)


def _ws(week_start, best=0.0, entries=0):
    return WeeklyStrength(week_start=week_start, best_estimated_1rm=best, entry_count=entries)


# ---------------------------------------------------------------------------
# Pure analysis (fixture-free, deterministic)
# ---------------------------------------------------------------------------

def test_series_trend_directions():
    assert series_trend([100, 200]) == "up"
    assert series_trend([200, 100]) == "down"
    assert series_trend([100, 102]) == "flat"      # within ±5% band
    assert series_trend([0, 100]) == "flat"        # only one active value
    assert series_trend([]) == "flat"


def test_weekly_best_estimated_1rm_peak_and_marker_exclusion():
    entries = [
        _entry(date(2026, 7, 2), weight=100, reps=5),   # 1RM 116.67 (peak)
        _entry(date(2026, 7, 2), weight=80, reps=10),   # 1RM 106.67
        _entry(date(2026, 7, 2), volume=0, marker=True),  # excluded
    ]
    weekly = weekly_best_estimated_1rm(entries, END_DAY, 4)
    assert [w.week_start for w in weekly] == [W0, W1, W2, W3]
    assert weekly[0] == _ws(W0, 0.0, 0)                 # empty week
    assert weekly[1].best_estimated_1rm == estimated_1rm(100, 5)  # peak, marker excluded
    assert weekly[1].entry_count == 2


def test_weekly_best_estimated_1rm_bodyweight_zero_load():
    # A real bodyweight entry (weight 0) counts as an entry but contributes no 1RM.
    weekly = weekly_best_estimated_1rm([_entry(date(2026, 7, 2), weight=0, reps=10)], END_DAY, 4)
    assert weekly[1].best_estimated_1rm == 0.0
    assert weekly[1].entry_count == 1


def test_is_progressing():
    assert is_progressing("up", "flat") is True
    assert is_progressing("flat", "up") is True
    assert is_progressing("flat", "flat") is False
    assert is_progressing("down", "down") is False


def test_detect_plateau_true_when_volume_flat_and_strength_not_rising():
    weekly_volume = [_wv(W0, 0), _wv(W1, 300, 2), _wv(W2, 305, 2), _wv(W3, 298, 2)]
    weekly_strength = [_ws(W0), _ws(W1), _ws(W2), _ws(W3)]   # insufficient strength data
    assert detect_plateau(weekly_volume, weekly_strength) is True


def test_detect_plateau_false_when_volume_rising():
    weekly_volume = [_wv(W1, 100, 2), _wv(W2, 200, 2), _wv(W3, 400, 2)]
    assert detect_plateau(weekly_volume, [_ws(W1), _ws(W2), _ws(W3)]) is False


def test_detect_plateau_false_insufficient_active_weeks():
    weekly_volume = [_wv(W0, 0), _wv(W1, 0), _wv(W2, 300, 2), _wv(W3, 300, 2)]  # only 2 active
    assert detect_plateau(weekly_volume, [_ws(W) for W in (W0, W1, W2, W3)]) is False


def test_detect_plateau_false_when_strength_still_rising():
    # Volume flat but estimated strength climbing → progressing via intensity.
    weekly_volume = [_wv(W1, 300, 2), _wv(W2, 305, 2), _wv(W3, 298, 2)]
    weekly_strength = [_ws(W1, 100, 1), _ws(W2, 110, 1), _ws(W3, 130, 1)]
    assert detect_plateau(weekly_volume, weekly_strength) is False


def test_detect_deload_due_true_when_block_stalled():
    weekly_volume = [_wv(W0, 300, 2), _wv(W1, 305, 2), _wv(W2, 298, 2), _wv(W3, 302, 2)]
    weekly_strength = [_ws(W) for W in (W0, W1, W2, W3)]     # flat/insufficient strength
    assert detect_deload_due(weekly_volume, weekly_strength) is True


def test_detect_deload_due_false_on_rest_week():
    weekly_volume = [_wv(W0, 300, 2), _wv(W1, 0), _wv(W2, 305, 2), _wv(W3, 298, 2)]
    assert detect_deload_due(weekly_volume, [_ws(W) for W in (W0, W1, W2, W3)]) is False


def test_detect_deload_due_false_when_still_rising():
    weekly_volume = [_wv(W0, 100, 2), _wv(W1, 200, 2), _wv(W2, 300, 2), _wv(W3, 400, 2)]
    assert detect_deload_due(weekly_volume, [_ws(W) for W in (W0, W1, W2, W3)]) is False


def test_detect_deload_due_false_insufficient_history():
    weekly_volume = [_wv(W1, 300, 2), _wv(W2, 305, 2), _wv(W3, 298, 2)]  # only 3 weeks
    assert detect_deload_due(weekly_volume, [_ws(W1), _ws(W2), _ws(W3)]) is False


def test_assess_consistency():
    consistent = [_wv(W0, 0), _wv(W1, 100, 2), _wv(W2, 100, 2), _wv(W3, 100, 2)]  # 3/4
    assert assess_consistency(consistent) == "consistent"
    inconsistent = [_wv(W0, 0), _wv(W1, 0), _wv(W2, 100, 2), _wv(W3, 100, 2)]     # 2/4
    assert assess_consistency(inconsistent) == "inconsistent"
    sparse = [_wv(W0, 0), _wv(W1, 0), _wv(W2, 0), _wv(W3, 100, 2)]                # 1/4
    assert assess_consistency(sparse) == "insufficient_data"


def test_derive_next_signal_precedence():
    # No data / insufficient consistency dominates everything.
    assert derive_next_signal(has_data=False, load_consistency="consistent",
                              deload_due=True, is_plateau=True, is_progressing=True) == "insufficient_data"
    assert derive_next_signal(has_data=True, load_consistency="insufficient_data",
                              deload_due=True, is_plateau=True, is_progressing=True) == "insufficient_data"
    # Inconsistent outranks deload/plateau/progressing.
    assert derive_next_signal(has_data=True, load_consistency="inconsistent",
                              deload_due=True, is_plateau=True, is_progressing=True) == "build_consistency"
    # Deload outranks plateau and progressing.
    assert derive_next_signal(has_data=True, load_consistency="consistent",
                              deload_due=True, is_plateau=True, is_progressing=True) == "deload"
    assert derive_next_signal(has_data=True, load_consistency="consistent",
                              deload_due=False, is_plateau=True, is_progressing=True) == "plateau"
    assert derive_next_signal(has_data=True, load_consistency="consistent",
                              deload_due=False, is_plateau=False, is_progressing=True) == "progressing"
    assert derive_next_signal(has_data=True, load_consistency="consistent",
                              deload_due=False, is_plateau=False, is_progressing=False) == "keep_pushing"


def test_weeks_non_positive_returns_neutral_report():
    r = build_progression_report(1, weeks=0, end_day=END_DAY)
    assert r == ProgressionReport(weeks=0)
    assert r.has_data is False and r.next_signal == "insufficient_data"
    assert r.weekly_volume == [] and r.weekly_strength == []


# ---------------------------------------------------------------------------
# DB-backed roll-up
# ---------------------------------------------------------------------------

def _add_workout(user_id, day, volume=100.0, sets=3, reps=5, weight=50.0, marker=False):
    """Seed a WorkoutLog on a specific Istanbul day (noon UTC is unambiguous)."""
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def test_report_progressing_when_volume_and_strength_rise(make_user):
    user = make_user("prog1")
    _add_workout(user.id, date(2026, 6, 25), volume=100, weight=50, reps=5)
    _add_workout(user.id, date(2026, 7, 2), volume=200, weight=60, reps=5)
    _add_workout(user.id, date(2026, 7, 9), volume=300, weight=70, reps=5)
    _add_workout(user.id, date(2026, 7, 15), volume=400, weight=80, reps=5)
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.has_data is True
    assert [w.week_start for w in r.weekly_volume] == [W0, W1, W2, W3]
    assert [w.total_volume for w in r.weekly_volume] == [100.0, 200.0, 300.0, 400.0]
    assert r.weekly_strength[3].best_estimated_1rm == estimated_1rm(80, 5)
    assert r.volume_trend == "up"
    assert r.strength_trend == "up"
    assert r.is_progressing is True
    assert r.is_plateau is False
    assert r.deload_due is False
    assert r.load_consistency == "consistent"
    assert r.next_signal == "progressing"


def test_report_plateau_and_deload_when_block_stalls(make_user):
    user = make_user("prog2")
    for day, vol in ((date(2026, 6, 25), 300), (date(2026, 7, 2), 305),
                     (date(2026, 7, 9), 298), (date(2026, 7, 15), 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)  # flat strength too
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.volume_trend == "flat"
    assert r.strength_trend == "flat"
    assert r.is_progressing is False
    assert r.is_plateau is True
    assert r.deload_due is True
    assert r.load_consistency == "consistent"
    assert r.next_signal == "deload"


def test_report_build_consistency_when_training_sparse(make_user):
    user = make_user("prog3")
    _add_workout(user.id, date(2026, 7, 2), volume=100)
    _add_workout(user.id, date(2026, 7, 15), volume=120)
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.load_consistency == "inconsistent"
    assert r.is_plateau is False
    assert r.deload_due is False
    assert r.next_signal == "build_consistency"


def test_report_empty_history_is_neutral(make_user):
    user = make_user("prog4")
    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.has_data is False
    assert r.volume_trend == "flat" and r.strength_trend == "flat"
    assert r.is_progressing is False and r.is_plateau is False and r.deload_due is False
    assert r.load_consistency == "insufficient_data"
    assert r.next_signal == "insufficient_data"
    assert len(r.weekly_volume) == 4 and all(w.total_volume == 0.0 for w in r.weekly_volume)
    assert len(r.weekly_strength) == 4 and all(w.best_estimated_1rm == 0.0 for w in r.weekly_strength)


def test_report_is_user_scoped(make_user):
    user = make_user("prog5")
    _add_workout(user.id, date(2026, 7, 2), volume=100, weight=50, reps=5)
    _add_workout(user.id, date(2026, 7, 9), volume=110, weight=52, reps=5)
    other = make_user("prog5_other")
    _add_workout(other.id, date(2026, 7, 2), volume=99999, weight=300, reps=5)
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert sum(w.total_volume for w in r.weekly_volume) == 210.0  # other user's row excluded
    assert max(w.best_estimated_1rm for w in r.weekly_strength) == estimated_1rm(52, 5)


def test_report_is_deterministic(make_user):
    user = make_user("prog6")
    _add_workout(user.id, date(2026, 7, 2), volume=100, weight=50, reps=5)
    _add_workout(user.id, date(2026, 7, 15), volume=200, weight=60, reps=5)
    db.session.commit()
    a = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    b = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert a == b


# ---------------------------------------------------------------------------
# Golden / characterization tests (Sprint 6 PR2 audit follow-up)
# Lock current behavior for the edge cases the audit flagged as under-covered.
# No new behavior — these pin what the code already does, so PR3 can refactor safely.
# ---------------------------------------------------------------------------

def test_report_single_workout_is_neutral(make_user):
    # One workout is not enough to judge any trend or consistency → all-neutral.
    user = make_user("golden_single")
    _add_workout(user.id, date(2026, 7, 9), volume=100, weight=50, reps=5)
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.has_data is True
    assert r.volume_trend == "flat" and r.strength_trend == "flat"
    assert r.is_progressing is False
    assert r.is_plateau is False
    assert r.deload_due is False
    assert r.load_consistency == "insufficient_data"   # only 1 trained window (< 2)
    assert r.next_signal == "insufficient_data"


def test_report_marker_only_history(make_user):
    # Only completion markers (a session happened, but zero measurable load): each
    # window counts as a trained day, but there is no volume/strength to judge — so
    # the trends and progression booleans stay neutral while consistency still reads.
    user = make_user("golden_marker")
    for day in (date(2026, 6, 25), date(2026, 7, 2), date(2026, 7, 9), date(2026, 7, 15)):
        _add_workout(user.id, day, volume=0, marker=True)
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.has_data is True                                       # a session happened
    assert all(w.total_volume == 0.0 for w in r.weekly_volume)      # markers carry no volume
    assert all(w.session_count == 1 for w in r.weekly_volume)       # but each is a trained day
    assert all(w.best_estimated_1rm == 0.0 for w in r.weekly_strength)
    assert r.volume_trend == "flat" and r.strength_trend == "flat"
    assert r.is_progressing is False
    assert r.is_plateau is False
    assert r.deload_due is False
    assert r.load_consistency == "consistent"                       # trained every window
    assert r.next_signal == "keep_pushing"                          # consistent, no load signal


def test_report_progressing_and_plateau_coexist(make_user):
    # Big early jump then a flat recent block: is_progressing (full-window endpoints
    # rose) and is_plateau (recent 3-week block is flat) are BOTH true. This is not a
    # contradiction — they measure different windows — and next_signal is the single
    # canonical output. The only way this state arises is a sustained active block that
    # stalled, which also satisfies deload_due, so next_signal resolves to "deload".
    user = make_user("golden_coexist")
    for day, vol in ((date(2026, 6, 25), 100), (date(2026, 7, 2), 300),
                     (date(2026, 7, 9), 305), (date(2026, 7, 15), 298)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)   # flat strength too
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert r.volume_trend == "up"          # earliest→latest active volume rose
    assert r.is_progressing is True
    assert r.is_plateau is True            # recent 3-week block is flat
    assert r.deload_due is True            # sustained active block that stalled
    assert r.next_signal == "deload"       # canonical: precedence resolves the overlap


def test_report_non_consecutive_weeks(make_user):
    # Trained in W0 and W2 only (gaps at W1 and W3): windows still bucket correctly, the
    # active-only trend ignores the empty weeks, and the gaps read as inconsistent.
    user = make_user("golden_gaps")
    _add_workout(user.id, date(2026, 6, 25), volume=100, weight=50, reps=5)  # W0
    _add_workout(user.id, date(2026, 7, 9), volume=200, weight=60, reps=5)   # W2
    db.session.commit()

    r = build_progression_report(user.id, weeks=4, end_day=END_DAY)
    assert [w.total_volume for w in r.weekly_volume] == [100.0, 0.0, 200.0, 0.0]
    assert r.volume_trend == "up"                  # active-only: 100 → 200
    assert r.is_progressing is True
    assert r.is_plateau is False                   # only 2 active weeks (< MIN_PLATEAU_WEEKS)
    assert r.deload_due is False                   # rest weeks break the block
    assert r.load_consistency == "inconsistent"    # trained only 2 of 4 windows
    assert r.next_signal == "build_consistency"    # precedence: consistency before progressing
