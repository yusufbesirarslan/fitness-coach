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
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_history import WeeklyVolume, weekly_windows
from app.services.training_planning import (
    AdaptivePlan,
    build_adaptive_plan,
    derive_adaptive_plan,
)
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

# Trailing windows: weeks 4 → starts 06-18, 06-25, 07-02, 07-09; W3 ends *on* END_DAY.
END_DAY = date(2026, 7, 15)
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
    _add_workout(user.id, W0, volume=100, weight=50, reps=5)
    _add_workout(user.id, W1, volume=200, weight=60, reps=5)
    _add_workout(user.id, W2, volume=300, weight=70, reps=5)
    _add_workout(user.id, W3, volume=400, weight=80, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "overload"
    assert program.overload_ready is True
    assert program.baseline_week_start == W3
    assert program.baseline_weekly_volume == 400.0
    assert program.target_weekly_volume == 420.0
    assert program.explanation_keys[0] == "weekly_program.focus.overload"


def test_program_deload_when_block_stalls(make_user):
    user = make_user("wp2")
    for day, vol in ((W0, 300), (W1, 305),
                     (W2, 298), (W3, 302)):
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
    for day, vol in ((W1, 305), (W2, 298),
                     (W3, 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.week_focus == "maintenance"
    assert program.maintenance_recommended is True
    assert program.volume_delta_pct == 0.0
    assert program.target_weekly_volume == program.baseline_weekly_volume == 302.0


def test_program_build_consistency_when_sparse(make_user):
    user = make_user("wp4")
    _add_workout(user.id, W1, volume=100)
    _add_workout(user.id, W3, volume=120)
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
    for day in (W0, W1, W2, W3):
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
    _add_workout(user.id, W1, volume=100, weight=50, reps=5)
    _add_workout(user.id, W2, volume=110, weight=52, reps=5)
    other = make_user("wp8_other")
    _add_workout(other.id, W1, volume=99999, weight=300, reps=5)
    db.session.commit()

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)
    assert program.baseline_weekly_volume == 110.0


def test_program_is_deterministic(make_user):
    user = make_user("wp9")
    _add_workout(user.id, W1, volume=100, weight=50, reps=5)
    _add_workout(user.id, W3, volume=200, weight=60, reps=5)
    db.session.commit()
    assert (build_weekly_program(user.id, weeks=4, end_day=END_DAY)
            == build_weekly_program(user.id, weeks=4, end_day=END_DAY))


# ---------------------------------------------------------------------------
# Multi-session regression coverage (Sprint 6 PR5 post-audit — finding F1)
#
# Every fixture above seeds exactly one workout per window, which makes a single
# day and a whole training week numerically identical. That is precisely the shape
# that hid the partial-window defect: while the newest window was forward-looking
# (`[today, today + 6]`) it could only ever hold *today's* entries, so one session
# was published as `baseline_weekly_volume`. These fixtures seed a realistic
# multi-session week instead, so a one-day bucket can never impersonate a week.
# ---------------------------------------------------------------------------

SESSION_VOLUME = 5000.0                      # one session
SESSIONS_PER_WEEK = (0, 2, 4)                # Mon/Wed/Fri-style cadence
WEEK_VOLUME = SESSION_VOLUME * len(SESSIONS_PER_WEEK)   # 15000 kg per full week


def _seed_multi_session_block(user_id, *, end_day=END_DAY, weeks=4,
                              offsets=SESSIONS_PER_WEEK,
                              session_volume=SESSION_VOLUME, weight=100.0, reps=5):
    """Seed `weeks` trailing weeks of `len(offsets)` sessions, counted back from `end_day`.

    `offsets` are days-before-`end_day` *within* each 7-day window, so every trailing
    window holds a complete, realistic training week. `(0, 2, 4)` trains on `end_day`
    itself — the "user has already trained today" case; `(1, 3, 5)` leaves `end_day` a
    rest day. Returns the training days, newest first.
    """
    days = [end_day - timedelta(days=7 * week + offset)
            for week in range(weeks) for offset in offsets]
    for day in days:
        _add_workout(user_id, day, volume=session_volume, weight=weight, reps=reps)
    db.session.commit()
    return days


def test_current_day_session_is_not_mistaken_for_a_weekly_baseline(make_user):
    """CASE 1 — multi-session completed weeks, evaluated on a day already trained.

    The regression that finding F1 describes: with the old forward-looking newest
    window this published 5000.0 (one session) as the *weekly* volume, understating
    the user's real 15000 kg week threefold, and named a `baseline_week_start` that
    ran six days into the future.
    """
    user = make_user("wp_f1_trained")
    days = _seed_multi_session_block(user.id)
    assert END_DAY in days                       # the user has trained today

    program = build_weekly_program(user.id, weeks=4, end_day=END_DAY)

    assert program.baseline_weekly_volume == WEEK_VOLUME      # 15000, a real week
    assert program.baseline_weekly_volume != SESSION_VOLUME   # not today's 5000
    # A valid *observed* window: it ends on END_DAY and never reaches past it.
    assert program.baseline_week_start == END_DAY - timedelta(days=6)
    assert program.baseline_week_start + timedelta(days=6) == END_DAY
    # Target stays pure arithmetic on the corrected baseline.
    assert program.target_weekly_volume == round(
        WEEK_VOLUME * (1 + program.volume_delta_pct), 2)


def test_rest_day_baseline_matches_the_trained_day_baseline(make_user):
    """CASE 2 — the same block evaluated on a rest day reads the same.

    The old geometry flip-flopped: on a rest day the newest bucket was empty and the
    baseline fell back to a complete week, but logging the day's first set switched it
    to that single session — so the published weekly target *dropped* the moment the
    user trained. Trailing windows remove the switching entirely.
    """
    trained = make_user("wp_f1_trained2")
    resting = make_user("wp_f1_rest")
    _seed_multi_session_block(trained.id, offsets=(0, 2, 4))     # trained today
    rest_days = _seed_multi_session_block(resting.id, offsets=(1, 3, 5))
    assert END_DAY not in rest_days                              # rest day today

    trained_program = build_weekly_program(trained.id, weeks=4, end_day=END_DAY)
    rest_program = build_weekly_program(resting.id, weeks=4, end_day=END_DAY)

    assert rest_program.baseline_weekly_volume == WEEK_VOLUME
    assert rest_program.baseline_weekly_volume == trained_program.baseline_weekly_volume
    assert rest_program.target_weekly_volume == trained_program.target_weekly_volume


def test_one_session_week_is_not_equivalent_to_a_three_session_week(make_user):
    """CASE 3 — the distinction the old one-workout-per-window fixtures could not make."""
    sparse = make_user("wp_f1_sparse")
    full = make_user("wp_f1_full")
    _seed_multi_session_block(sparse.id, offsets=(0,))           # 1 session / week
    _seed_multi_session_block(full.id, offsets=SESSIONS_PER_WEEK)  # 3 sessions / week

    sparse_program = build_weekly_program(sparse.id, weeks=4, end_day=END_DAY)
    full_program = build_weekly_program(full.id, weeks=4, end_day=END_DAY)

    assert sparse_program.baseline_weekly_volume == SESSION_VOLUME
    assert full_program.baseline_weekly_volume == WEEK_VOLUME
    assert full_program.baseline_weekly_volume != sparse_program.baseline_weekly_volume


def test_stable_weekly_volume_is_reported_as_the_weekly_total(make_user):
    """CASE 4 — three identical 15000 kg weeks, checked on a training day."""
    user = make_user("wp_f1_stable")
    _seed_multi_session_block(user.id, weeks=3)

    program = build_weekly_program(user.id, weeks=3, end_day=END_DAY)

    assert program.baseline_weekly_volume == 15000.0
    assert program.baseline_weekly_volume != SESSION_VOLUME
    assert program.target_weekly_volume == round(
        15000.0 * (1 + program.volume_delta_pct), 2)


def test_every_window_holds_a_full_week_of_sessions(make_user):
    """The fixture's own guarantee — each trailing window really does hold a week.

    Without this the cases above could pass for the wrong reason (e.g. a fixture that
    silently drops sessions outside the fetch range).
    """
    user = make_user("wp_f1_windows")
    _seed_multi_session_block(user.id)

    plan = build_adaptive_plan(user.id, 4, end_day=END_DAY)

    assert [w.session_count for w in plan.progression.weekly_volume] == [3, 3, 3, 3]
    assert [w.total_volume for w in plan.progression.weekly_volume] == [WEEK_VOLUME] * 4
    assert plan.progression.weekly_volume[-1].week_start == END_DAY - timedelta(days=6)
