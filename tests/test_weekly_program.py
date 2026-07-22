"""Tests for the weekly-program consumer layer (Sprint 6 PR5).

Two layers, matching repo convention (see tests/test_training_planning.py):
- Pure translation rules — no fixtures, deterministic (like tests/test_calculations.py).
- DB-backed roll-up (`build_weekly_program`) — `make_user` fixture, fixed `END_DAY`.

The central invariant under test is that this layer *translates* and never *decides*:
every decision field is a verbatim echo of `AdaptivePlan`, and the observed volumes
can be changed arbitrarily without moving a single decision
(`test_decisions_ignore_observed_volume`).

    python -m pytest tests/test_weekly_program.py -v
"""
import dataclasses
from datetime import date, datetime

import pytest

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_history import WeeklyVolume, weekly_windows
from app.services.training_planning import AdaptivePlan, derive_adaptive_plan
from app.services.training_planning.analysis import (
    DELOAD_VOLUME_CUT,
    VOLUME_INCREASE_STEP,
)
from app.services.training_progression import ProgressionReport
from app.services.weekly_program import (
    UNSUPPORTED_CAPABILITIES,
    WeeklyProgramRecommendation,
    build_weekly_program,
    derive_explanation_keys,
    derive_weekly_program,
    select_volume_baseline,
    target_volume_for,
)

END_DAY = date(2026, 7, 15)          # weeks 4 → starts 06-24, 07-01, 07-08, 07-15
W0, W1, W2, W3 = weekly_windows(END_DAY, 4)

SIGNALS = ("insufficient_data", "build_consistency", "deload",
           "plateau", "progressing", "keep_pushing")

DECISION_FIELDS = ("weeks", "has_data", "week_focus", "volume_action",
                   "intensity_action", "volume_delta_pct", "overload_ready",
                   "maintenance_recommended", "reason_codes")


def _weekly(volumes):
    """WeeklyVolume series aligned to END_DAY's windows, oldest first."""
    return [
        WeeklyVolume(
            week_start=start,
            session_count=1 if volume else 0,
            entry_count=1 if volume else 0,
            total_volume=float(volume),
        )
        for start, volume in zip(weekly_windows(END_DAY, len(volumes)), volumes)
    ]


def _plan(next_signal="insufficient_data", volumes=(), **overrides):
    """A real AdaptivePlan (derived, not hand-built) carrying a chosen volume series."""
    fields = {
        "weeks": 4,
        "has_data": bool(volumes),
        "next_signal": next_signal,
        "weekly_volume": _weekly(volumes),
    }
    fields.update(overrides)
    return derive_adaptive_plan(ProgressionReport(**fields))


# ---------------------------------------------------------------------------
# Pure translation rules (fixture-free, deterministic)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("signal", SIGNALS)
def test_every_decision_field_is_echoed_verbatim(signal):
    # The consumer restates the plan; it never re-derives a decision, so each
    # decision field must be identical on both objects for every signal.
    plan = _plan(signal, volumes=(100, 110, 120, 130))
    program = derive_weekly_program(plan)
    for field in DECISION_FIELDS:
        assert getattr(program, field) == getattr(plan, field), field


def test_decisions_ignore_observed_volume():
    # Same signal, wildly different history: only the observed/derived volume
    # numbers may move. If a decision field ever tracks the volumes, this layer has
    # started planning on its own.
    small = derive_weekly_program(_plan("progressing", volumes=(1, 2, 3, 4)))
    large = derive_weekly_program(_plan("progressing", volumes=(1000, 2000, 3000, 9999)))
    for field in DECISION_FIELDS:
        assert getattr(small, field) == getattr(large, field), field
    assert small.baseline_weekly_volume == 4.0
    assert large.baseline_weekly_volume == 9999.0


def test_baseline_is_the_latest_positive_window():
    plan = _plan("keep_pushing", volumes=(100, 200, 300, 400))
    baseline = select_volume_baseline(plan)
    assert baseline.week_start == W3
    assert baseline.total_volume == 400.0


def test_baseline_skips_trailing_zero_volume_weeks():
    # A rest week (or a marker-only week) is missing data, not a measurement of zero —
    # anchoring to it would scale every recommendation down to nothing.
    plan = _plan("keep_pushing", volumes=(100, 250, 0, 0))
    baseline = select_volume_baseline(plan)
    assert baseline.week_start == W1
    assert baseline.total_volume == 250.0

    program = derive_weekly_program(plan)
    assert program.baseline_week_start == W1
    assert program.baseline_weekly_volume == 250.0


def test_baseline_is_none_when_no_window_has_volume():
    assert select_volume_baseline(_plan("keep_pushing", volumes=(0, 0, 0, 0))) is None


def test_baseline_is_none_for_an_empty_series():
    assert select_volume_baseline(_plan("insufficient_data")) is None


def test_target_volume_for_positive_delta():
    # 400 * 1.05 == 420.00000000000006 in binary floats — the contract is the rounded
    # value, so consumers never display noise.
    assert target_volume_for(400.0, VOLUME_INCREASE_STEP) == 420.0


def test_target_volume_for_hold_equals_baseline():
    assert target_volume_for(302.5, 0.0) == 302.5


def test_target_volume_for_negative_delta():
    assert target_volume_for(302.0, -DELOAD_VOLUME_CUT) == 181.2


def test_target_volume_for_rounds_to_two_decimals():
    assert target_volume_for(333.333, 0.0) == 333.33
    assert target_volume_for(1000.0, VOLUME_INCREASE_STEP) == 1050.0


def test_target_volume_is_none_without_a_baseline():
    # None, never 0.0: "not enough data to say" must not read as "train nothing".
    assert target_volume_for(None, VOLUME_INCREASE_STEP) is None

    program = derive_weekly_program(_plan("keep_pushing", volumes=(0, 0, 0, 0)))
    assert program.baseline_weekly_volume is None
    assert program.baseline_week_start is None
    assert program.target_weekly_volume is None


def test_overload_program_targets_five_percent_above_baseline():
    program = derive_weekly_program(_plan("progressing", volumes=(100, 200, 300, 400)))
    assert program.week_focus == "overload"
    assert program.volume_action == "increase"
    assert program.intensity_action == "progress"
    assert program.volume_delta_pct == VOLUME_INCREASE_STEP
    assert program.baseline_weekly_volume == 400.0
    assert program.target_weekly_volume == 420.0


def test_deload_program_targets_sixty_percent_of_baseline():
    program = derive_weekly_program(_plan("deload", volumes=(300, 305, 298, 302)))
    assert program.week_focus == "deload"
    assert program.volume_action == "decrease"
    assert program.intensity_action == "deload"
    assert program.volume_delta_pct == -DELOAD_VOLUME_CUT
    assert program.target_weekly_volume == 181.2


def test_hold_program_targets_the_baseline_unchanged():
    program = derive_weekly_program(_plan("plateau", volumes=(300, 305, 298, 302)))
    assert program.week_focus == "maintenance"
    assert program.volume_action == "hold"
    assert program.volume_delta_pct == 0.0
    assert program.target_weekly_volume == program.baseline_weekly_volume == 302.0


def test_explanation_keys_are_ordered_and_one_to_one():
    plan = _plan("progressing", volumes=(400, 300, 200, 100),
                 volume_trend="down", strength_trend="down")
    assert plan.reason_codes == ("progressing", "volume_trend_down", "strength_trend_down")
    assert derive_explanation_keys(plan) == (
        "weekly_program.focus.overload",
        "weekly_program.reason.progressing",
        "weekly_program.reason.volume_trend_down",
        "weekly_program.reason.strength_trend_down",
    )


def test_explanation_keys_introduce_no_new_vocabulary():
    # Every key is an existing canonical code behind a prefix — no second taxonomy.
    plan = _plan("deload", volumes=(300, 305, 298, 302))
    keys = derive_explanation_keys(plan)
    assert keys[0] == f"weekly_program.focus.{plan.week_focus}"
    assert keys[1:] == tuple(f"weekly_program.reason.{c}" for c in plan.reason_codes)


def test_neutral_plan_yields_the_neutral_recommendation():
    # The default-constructed recommendation IS the neutral one — no special-casing.
    assert derive_weekly_program(AdaptivePlan(weeks=0)) == WeeklyProgramRecommendation(weeks=0)


def test_neutral_recommendation_explains_itself():
    program = WeeklyProgramRecommendation(weeks=0)
    assert program.reason_codes == ("insufficient_history",)
    assert program.explanation_keys == (
        "weekly_program.focus.insufficient_data",
        "weekly_program.reason.insufficient_history",
    )
    assert program.baseline_weekly_volume is None
    assert program.target_weekly_volume is None


def test_unsupported_capabilities_are_declared():
    # AdaptivePlan models none of these, so PR5 reports them unsupported rather than
    # inventing a heuristic (spec: "return a neutral/unsupported state instead").
    program = derive_weekly_program(_plan("progressing", volumes=(100, 200, 300, 400)))
    assert program.unsupported == UNSUPPORTED_CAPABILITIES
    assert set(program.unsupported) == {
        "session_frequency", "intensity_magnitude", "exercise_selection",
    }


def test_derivation_is_deterministic():
    plan = _plan("progressing", volumes=(100, 200, 300, 400))
    assert derive_weekly_program(plan) == derive_weekly_program(plan)


def test_recommendation_is_immutable():
    program = derive_weekly_program(_plan("progressing", volumes=(100, 200, 300, 400)))
    with pytest.raises(dataclasses.FrozenInstanceError):
        program.week_focus = "deload"
    with pytest.raises(dataclasses.FrozenInstanceError):
        program.target_weekly_volume = 999.0


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


def test_program_overload_when_progressing(make_user):
    user = make_user("wp1")
    _add_workout(user.id, date(2026, 6, 25), volume=100, weight=50, reps=5)
    _add_workout(user.id, date(2026, 7, 2), volume=200, weight=60, reps=5)
    _add_workout(user.id, date(2026, 7, 9), volume=300, weight=70, reps=5)
    _add_workout(user.id, date(2026, 7, 15), volume=400, weight=80, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "overload"
    assert program.overload_ready is True
    assert program.baseline_week_start == W3
    assert program.baseline_weekly_volume == 400.0
    assert program.target_weekly_volume == 420.0
    assert program.explanation_keys[0] == "weekly_program.focus.overload"


def test_program_deload_when_block_stalls(make_user):
    # end_day is explicit, so the recommendation is exercised even though audit
    # finding #5 makes `deload` rare against a live clock (docs/WEEKLY_PROGRAM.md).
    user = make_user("wp2")
    for day, vol in ((date(2026, 6, 25), 300), (date(2026, 7, 2), 305),
                     (date(2026, 7, 9), 298), (date(2026, 7, 15), 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "deload"
    assert program.volume_action == "decrease"
    assert program.volume_delta_pct == -DELOAD_VOLUME_CUT
    assert program.baseline_weekly_volume == 302.0
    assert program.target_weekly_volume == 181.2


def test_program_maintenance_holds_the_baseline(make_user):
    user = make_user("wp3")
    for day, vol in ((date(2026, 7, 2), 305), (date(2026, 7, 9), 298),
                     (date(2026, 7, 15), 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "maintenance"
    assert program.maintenance_recommended is True
    assert program.volume_delta_pct == 0.0
    assert program.target_weekly_volume == program.baseline_weekly_volume == 302.0


def test_program_build_consistency_when_sparse(make_user):
    user = make_user("wp4")
    _add_workout(user.id, date(2026, 7, 2), volume=100)
    _add_workout(user.id, date(2026, 7, 15), volume=120)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "build_consistency"
    assert program.volume_action == "hold"
    assert program.overload_ready is False
    assert program.baseline_weekly_volume == 120.0
    assert program.target_weekly_volume == 120.0
    assert program.explanation_keys[1] == "weekly_program.reason.inconsistent_training"


def test_program_marker_only_history_has_no_volume_baseline(make_user):
    # Markers prove attendance and carry volume=0, so they are a trained day but never
    # a volume baseline — the program holds and reports no numbers.
    user = make_user("wp5")
    for day in (date(2026, 6, 25), date(2026, 7, 2), date(2026, 7, 9), date(2026, 7, 15)):
        _add_workout(user.id, day, volume=0, marker=True)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "steady"
    assert program.has_data is True
    assert program.baseline_week_start is None
    assert program.baseline_weekly_volume is None
    assert program.target_weekly_volume is None


def test_program_empty_history_is_neutral(make_user):
    user = make_user("wp6")
    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.has_data is False
    assert program.week_focus == "insufficient_data"
    assert program.volume_action == "hold" and program.intensity_action == "hold"
    assert program.volume_delta_pct == 0.0
    assert program.baseline_weekly_volume is None
    assert program.target_weekly_volume is None
    assert program.reason_codes == ("insufficient_history",)


def test_program_weeks_non_positive_is_neutral(make_user):
    user = make_user("wp7")
    program = build_weekly_program(user.id, weeks=0, end_day=END_DAY)
    assert program == WeeklyProgramRecommendation(weeks=0)


def test_program_is_user_scoped(make_user):
    user = make_user("wp8")
    _add_workout(user.id, date(2026, 7, 2), volume=100, weight=50, reps=5)
    _add_workout(user.id, date(2026, 7, 9), volume=110, weight=52, reps=5)
    other = make_user("wp8_other")
    _add_workout(other.id, date(2026, 7, 2), volume=99999, weight=300, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.baseline_weekly_volume == 110.0


def test_program_is_deterministic(make_user):
    user = make_user("wp9")
    _add_workout(user.id, date(2026, 7, 2), volume=100, weight=50, reps=5)
    _add_workout(user.id, date(2026, 7, 15), volume=200, weight=60, reps=5)
    db.session.commit()
    assert (build_weekly_program(user.id, weeks=4, end_day=END_DAY)
            == build_weekly_program(user.id, weeks=4, end_day=END_DAY))
