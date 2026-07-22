"""Tests for the adaptive-planning layer (Sprint 6 PR3).

Two layers, matching repo convention (see tests/test_training_progression.py):
- Pure decision rules — no fixtures, deterministic (like tests/test_calculations.py).
- DB-backed roll-up (`build_adaptive_plan`) — `make_user` fixture, fixed `END_DAY`.

    python -m pytest tests/test_training_planning.py -v
"""
from datetime import date, datetime

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_history import weekly_windows
from app.services.training_planning import (
    AdaptivePlan,
    build_adaptive_plan,
    derive_adaptive_plan,
    derive_intensity_action,
    derive_reason_codes,
    derive_volume_action,
    derive_week_focus,
    volume_delta_for,
)
from app.services.training_planning.analysis import (
    DELOAD_VOLUME_CUT,
    VOLUME_INCREASE_STEP,
)
from app.services.training_progression import ProgressionReport

# Trailing windows: weeks 4 → starts 06-18, 06-25, 07-02, 07-09; W3 ends *on* END_DAY.
END_DAY = date(2026, 7, 15)
W0, W1, W2, W3 = weekly_windows(END_DAY, 4)

SIGNALS = ("insufficient_data", "build_consistency", "deload",
           "plateau", "progressing", "keep_pushing")
FOCUSES = ("insufficient_data", "build_consistency", "deload",
           "maintenance", "overload", "steady")


def _report(next_signal="insufficient_data", **overrides):
    """Build a ProgressionReport for pure tests (no DB)."""
    fields = {"weeks": 4, "has_data": True, "next_signal": next_signal}
    fields.update(overrides)
    return ProgressionReport(**fields)


# ---------------------------------------------------------------------------
# Pure decision rules (fixture-free, deterministic)
# ---------------------------------------------------------------------------

def test_derive_week_focus_mapping():
    # 1:1 with the progression layer's canonical next_signal — no second precedence.
    assert derive_week_focus("insufficient_data") == "insufficient_data"
    assert derive_week_focus("build_consistency") == "build_consistency"
    assert derive_week_focus("deload") == "deload"
    assert derive_week_focus("plateau") == "maintenance"
    assert derive_week_focus("progressing") == "overload"
    assert derive_week_focus("keep_pushing") == "steady"
    # Unknown/future signal strings fall back to the fully neutral focus.
    assert derive_week_focus("some_future_signal") == "insufficient_data"


def test_derive_volume_action_per_focus():
    assert derive_volume_action("overload") == "increase"
    assert derive_volume_action("deload") == "decrease"
    for focus in ("steady", "maintenance", "build_consistency", "insufficient_data"):
        assert derive_volume_action(focus) == "hold"


def test_derive_intensity_action_per_focus():
    assert derive_intensity_action("overload") == "progress"
    assert derive_intensity_action("deload") == "deload"
    for focus in ("steady", "maintenance", "build_consistency", "insufficient_data"):
        assert derive_intensity_action(focus) == "hold"


def test_volume_delta_values_and_constant_sanity():
    assert volume_delta_for("increase") == VOLUME_INCREASE_STEP
    assert volume_delta_for("decrease") == -DELOAD_VOLUME_CUT
    assert volume_delta_for("hold") == 0.0
    # Pin conservatism: increase stays under the common 10% guideline, the deload
    # cut stays inside the standard 50-60% training-volume band.
    assert 0 < VOLUME_INCREASE_STEP <= 0.10
    assert 0 < DELOAD_VOLUME_CUT <= 0.5


def test_overload_ready_only_for_progressing_signal():
    ready = [s for s in SIGNALS if derive_adaptive_plan(_report(s)).overload_ready]
    assert ready == ["progressing"]


def test_maintenance_recommended_only_for_plateau_signal():
    maint = [s for s in SIGNALS
             if derive_adaptive_plan(_report(s)).maintenance_recommended]
    assert maint == ["plateau"]


def test_never_increase_without_consistent_progression():
    # Sweep invariant: only the canonical "progressing" signal may increase volume.
    for signal in SIGNALS:
        plan = derive_adaptive_plan(_report(signal))
        if signal != "progressing":
            assert plan.volume_action != "increase", signal
    # Pathological report: raw booleans scream "progress" but the canonical signal
    # says the user is inconsistent. The plan must key off next_signal only.
    pathological = _report("build_consistency", is_progressing=True,
                           load_consistency="inconsistent", volume_trend="up")
    plan = derive_adaptive_plan(pathological)
    assert plan.week_focus == "build_consistency"
    assert plan.volume_action == "hold"
    assert plan.overload_ready is False


def test_reason_codes_deterministic_and_ordered():
    # Position 0 is always the primary cause; trend nuances follow in fixed order.
    plain = _report("keep_pushing")
    assert derive_reason_codes(plain) == ("steady_state",)
    nuanced = _report("keep_pushing", volume_trend="down", strength_trend="down")
    assert derive_reason_codes(nuanced) == (
        "steady_state", "volume_trend_down", "strength_trend_down")
    # Same input → same tuple (deterministic).
    assert derive_reason_codes(nuanced) == derive_reason_codes(_report(
        "keep_pushing", volume_trend="down", strength_trend="down"))
    # Every focus has a primary code.
    for signal in SIGNALS:
        assert len(derive_reason_codes(_report(signal))) >= 1


def test_neutral_report_yields_neutral_plan():
    # The default-constructed plan IS the neutral plan — no special-casing needed.
    assert derive_adaptive_plan(ProgressionReport(weeks=0)) == AdaptivePlan(weeks=0)


def test_plan_echoes_progression():
    report = _report("progressing", weeks=6)
    plan = derive_adaptive_plan(report)
    assert plan.progression is report      # embedded, not copied
    assert plan.weeks == 6
    assert plan.has_data is True


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


def test_plan_overload_when_progressing(make_user):
    user = make_user("plan1")
    _add_workout(user.id, W0, volume=100, weight=50, reps=5)
    _add_workout(user.id, W1, volume=200, weight=60, reps=5)
    _add_workout(user.id, W2, volume=300, weight=70, reps=5)
    _add_workout(user.id, W3, volume=400, weight=80, reps=5)
    db.session.commit()

    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert plan.week_focus == "overload"
    assert plan.volume_action == "increase"
    assert plan.intensity_action == "progress"
    assert plan.volume_delta_pct == VOLUME_INCREASE_STEP
    assert plan.overload_ready is True
    assert plan.maintenance_recommended is False
    assert plan.reason_codes[0] == "progressing"
    assert plan.progression.next_signal == "progressing"


def test_plan_deload_when_block_stalls(make_user):
    user = make_user("plan2")
    for day, vol in ((W0, 300), (W1, 305),
                     (W2, 298), (W3, 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)  # flat strength too
    db.session.commit()

    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert plan.week_focus == "deload"
    assert plan.volume_action == "decrease"
    assert plan.intensity_action == "deload"
    assert plan.volume_delta_pct == -DELOAD_VOLUME_CUT
    assert plan.overload_ready is False
    assert plan.maintenance_recommended is False
    assert plan.reason_codes[0] == "deload_due"


def test_plan_maintenance_on_plateau_without_deload(make_user):
    # Rest week at W0 + a flat active W1-W3 block: plateau fires but deload does
    # not (the rest week already deloaded) → deliberate consolidation week.
    user = make_user("plan3")
    for day, vol in ((W1, 305), (W2, 298),
                     (W3, 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)
    db.session.commit()

    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert plan.progression.is_plateau is True
    assert plan.progression.deload_due is False
    assert plan.week_focus == "maintenance"
    assert plan.volume_action == "hold"
    assert plan.intensity_action == "hold"
    assert plan.maintenance_recommended is True
    assert plan.overload_ready is False
    assert plan.reason_codes[0] == "plateau_detected"


def test_plan_build_consistency_when_sparse(make_user):
    user = make_user("plan4")
    _add_workout(user.id, W1, volume=100)
    _add_workout(user.id, W3, volume=120)
    db.session.commit()

    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert plan.week_focus == "build_consistency"
    assert plan.volume_action == "hold"
    assert plan.intensity_action == "hold"
    assert plan.overload_ready is False
    assert plan.maintenance_recommended is False
    assert plan.reason_codes[0] == "inconsistent_training"


def test_plan_steady_for_marker_only_history(make_user):
    # Markers prove attendance, never justify overload: consistent-but-loadless
    # history reads "keep_pushing" upstream and must stay a hold-everything week.
    user = make_user("plan5")
    for day in (W0, W1, W2, W3):
        _add_workout(user.id, day, volume=0, marker=True)
    db.session.commit()

    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert plan.progression.next_signal == "keep_pushing"
    assert plan.week_focus == "steady"
    assert plan.volume_action == "hold"
    assert plan.intensity_action == "hold"
    assert plan.overload_ready is False
    assert plan.reason_codes[0] == "steady_state"


def test_plan_empty_history_is_neutral(make_user):
    user = make_user("plan6")
    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert plan.has_data is False
    assert plan.week_focus == "insufficient_data"
    assert plan.volume_action == "hold" and plan.intensity_action == "hold"
    assert plan.volume_delta_pct == 0.0
    assert plan.overload_ready is False and plan.maintenance_recommended is False
    assert plan.reason_codes == ("insufficient_history",)
    assert len(plan.progression.weekly_volume) == 4
    assert all(w.total_volume == 0.0 for w in plan.progression.weekly_volume)


def test_plan_weeks_non_positive_is_neutral(make_user):
    user = make_user("plan7")
    plan = build_adaptive_plan(user.id, weeks=0, end_day=END_DAY)
    assert plan == AdaptivePlan(weeks=0)


def test_plan_is_user_scoped(make_user):
    user = make_user("plan8")
    _add_workout(user.id, W1, volume=100, weight=50, reps=5)
    _add_workout(user.id, W2, volume=110, weight=52, reps=5)
    other = make_user("plan8_other")
    _add_workout(other.id, W1, volume=99999, weight=300, reps=5)
    db.session.commit()

    plan = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert sum(w.total_volume for w in plan.progression.weekly_volume) == 210.0


def test_plan_is_deterministic(make_user):
    user = make_user("plan9")
    _add_workout(user.id, W1, volume=100, weight=50, reps=5)
    _add_workout(user.id, W3, volume=200, weight=60, reps=5)
    db.session.commit()
    a = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    b = build_adaptive_plan(user.id, weeks=4, end_day=END_DAY)
    assert a == b
