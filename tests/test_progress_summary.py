"""Tests for the canonical Progress summary read model (Progress Redesign PR2).

Two layers, matching repo convention (see tests/test_training_progression.py):
- Pure analysis — no fixtures, deterministic.
- DB-backed orchestration (`build_progress_summary`) — `make_user`, fixed END_DAY.

    python -m pytest tests/test_progress_summary.py -v
"""
import ast
import pathlib
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WeeklyCheckIn, WorkoutLog
from app.services.progress_summary import (
    BODY_AVAILABLE,
    BODY_INSUFFICIENT_DATA,
    BODY_PARTIAL,
    CONSISTENCY_STATES,
    PERFORMANCE_BY_SIGNAL,
    PERFORMANCE_STATES,
    SUMMARY_WEEKS,
    TRAJECTORY_BUILDING_BASELINE,
    TRAJECTORY_BY_SIGNAL,
    TRAJECTORY_NEEDS_ATTENTION,
    TRAJECTORY_ON_TRACK,
    TRAJECTORY_STATES,
    BodyFacts,
    UnknownProgressionSignal,
    build_progress_summary,
    build_window,
    performance_state_for_signal,
    summarize_body,
    trajectory_for_signal,
)
from app.services.training_history import weekly_windows
from app.services.training_progression import derive_next_signal

# Same trailing-window fixture geometry the progression tests use: weeks 4 →
# W3 ends *on* END_DAY.
END_DAY = date(2026, 7, 15)
W0, W1, W2, W3 = weekly_windows(END_DAY, SUMMARY_WEEKS)


def _add_workout(user_id, day, volume=100.0, sets=3, reps=5, weight=50.0, marker=False):
    """Seed a WorkoutLog on a specific Istanbul day (noon UTC is unambiguous)."""
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _add_checkin(user_id, day, weight, qualifying=True):
    """Seed a check-in. `qualifying=False` mimics a sparse /update-weight row.

    /update-weight writes a WeeklyCheckIn carrying nothing but a weight; the full
    weekly form writes `yogunluk` too. That column is the canonical divider the
    rest of Progress already uses (see /checkin-history BUG-5).
    """
    db.session.add(WeeklyCheckIn(
        user_id=user_id,
        weight=weight,
        yogunluk=3 if qualifying else None,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


# ---------------------------------------------------------------------------
# Trajectory mapping (pure, exhaustive)
# ---------------------------------------------------------------------------

def test_every_canonical_signal_maps_to_the_documented_trajectory():
    """The whole V1 contract, one assertion per signal."""
    expected = {
        "insufficient_data": TRAJECTORY_BUILDING_BASELINE,
        "progressing": TRAJECTORY_ON_TRACK,
        "keep_pushing": TRAJECTORY_ON_TRACK,
        "build_consistency": TRAJECTORY_NEEDS_ATTENTION,
        "plateau": TRAJECTORY_NEEDS_ATTENTION,
        "deload": TRAJECTORY_NEEDS_ATTENTION,
    }
    for signal, state in expected.items():
        traj = trajectory_for_signal(signal)
        assert traj.state == state, signal
        # The canonical signal is echoed verbatim so the state stays traceable.
        assert traj.reason == signal


def test_trajectory_table_covers_the_training_contract_exactly():
    """Drift guard: a new/renamed canonical signal must fail here, not in prod.

    The expected key set is derived from `derive_next_signal`'s own documented
    outputs rather than restated, so this breaks the moment the training layer
    grows a seventh signal without PR2's mapping being extended.
    """
    canonical = set()
    for has_data in (False, True):
        for consistency in CONSISTENCY_STATES:
            for deload in (False, True):
                for plateau in (False, True):
                    for progressing in (False, True):
                        canonical.add(derive_next_signal(
                            has_data=has_data,
                            load_consistency=consistency,
                            deload_due=deload,
                            is_plateau=plateau,
                            is_progressing=progressing,
                        ))
    assert canonical == set(TRAJECTORY_BY_SIGNAL)
    assert canonical == set(PERFORMANCE_BY_SIGNAL)


def test_no_off_track_state_exists():
    assert set(TRAJECTORY_STATES) == {
        TRAJECTORY_BUILDING_BASELINE, TRAJECTORY_ON_TRACK, TRAJECTORY_NEEDS_ATTENTION}
    assert "off_track" not in TRAJECTORY_STATES
    assert "off_track" not in set(TRAJECTORY_BY_SIGNAL.values())


@pytest.mark.parametrize("unknown", ["", "off_track", "regressing", "ON_TRACK", None, 7])
def test_unknown_signal_fails_closed(unknown):
    """Never guess. An unmapped value raises instead of resolving to a state.

    Mapping it to `on_track` would invent success; mapping it to
    `building_baseline` would dress a contract drift up as the user simply not
    having logged enough.
    """
    with pytest.raises(UnknownProgressionSignal):
        trajectory_for_signal(unknown)
    with pytest.raises(UnknownProgressionSignal):
        performance_state_for_signal(unknown)


def test_performance_states_are_the_documented_presentation_map():
    assert PERFORMANCE_BY_SIGNAL == {
        "insufficient_data": "building_baseline",
        "progressing": "progressing",
        "keep_pushing": "steady",
        "build_consistency": "building_consistency",
        "plateau": "plateau",
        "deload": "deload",
    }
    assert set(PERFORMANCE_BY_SIGNAL.values()) == set(PERFORMANCE_STATES)


# ---------------------------------------------------------------------------
# Window (pure)
# ---------------------------------------------------------------------------

def test_window_reuses_the_foundation_geometry():
    w = build_window(END_DAY, SUMMARY_WEEKS)
    assert w.weeks == SUMMARY_WEEKS
    assert w.end == END_DAY
    assert w.start == W0                      # trailing windows, oldest start
    assert (w.end - w.start).days == SUMMARY_WEEKS * 7 - 1
    assert w.timezone == "Europe/Istanbul"


# ---------------------------------------------------------------------------
# Body (pure)
# ---------------------------------------------------------------------------

def test_body_needs_two_qualifying_observations_for_a_delta():
    one = summarize_body(BodyFacts(current_weight_kg=80.0,
                                   recent_qualifying_weights=(80.0,)))
    assert one.status == BODY_PARTIAL
    assert one.weight_delta_kg is None        # NOT 0.0 — one point is not a change

    two = summarize_body(BodyFacts(current_weight_kg=79.4,
                                   recent_qualifying_weights=(79.4, 80.0)))
    assert two.status == BODY_AVAILABLE
    assert two.weight_delta_kg == -0.6        # newest-first: latest - previous


def test_body_without_a_current_weight_is_insufficient_not_zero():
    b = summarize_body(BodyFacts())
    assert b.status == BODY_INSUFFICIENT_DATA
    assert b.current_weight_kg is None
    assert b.weight_delta_kg is None
    assert b.target_weight_kg is None
    assert b.distance_to_target_kg is None


def test_body_target_distance_is_absolute_and_unclassified():
    over = summarize_body(BodyFacts(current_weight_kg=78.4, target_weight_kg=75.0))
    under = summarize_body(BodyFacts(current_weight_kg=71.6, target_weight_kg=75.0))
    assert over.distance_to_target_kg == 3.4
    assert under.distance_to_target_kg == 3.4          # direction carries no verdict
    # No good/bad field exists to carry one.
    assert not [f for f in vars(over) if "score" in f or "status" == f and False]


def test_body_distance_requires_both_values():
    assert summarize_body(
        BodyFacts(current_weight_kg=78.4)).distance_to_target_kg is None
    assert summarize_body(
        BodyFacts(target_weight_kg=75.0)).distance_to_target_kg is None


def test_body_delta_rounding_matches_the_existing_two_point_calculation():
    """/api/progress/insights rounds the same subtraction to 1 dp; so do we."""
    b = summarize_body(BodyFacts(current_weight_kg=79.4,
                                 recent_qualifying_weights=(79.4, 80.0)))
    assert b.weight_delta_kg == round(79.4 - 80.0, 1)
    assert b.current_weight_kg == 79.4                 # stored value echoed exactly


# ---------------------------------------------------------------------------
# DB-backed orchestration
# ---------------------------------------------------------------------------

def test_empty_history_builds_baseline_rather_than_a_verdict(make_user):
    user = make_user("psempty")
    s = build_progress_summary(user.id, end_day=END_DAY)

    assert s.trajectory.state == TRAJECTORY_BUILDING_BASELINE
    assert s.trajectory.reason == "insufficient_data"
    assert s.performance.state == "building_baseline"
    assert s.consistency.state == "insufficient_data"
    assert s.consistency.active_weeks == 0
    assert s.consistency.analyzed_weeks == SUMMARY_WEEKS
    assert s.consistency.sessions == 0
    assert s.body.status == BODY_INSUFFICIENT_DATA


def test_progressing_history_is_on_track(make_user):
    user = make_user("psprog")
    for day, vol, w in ((W0, 100, 50), (W1, 200, 60), (W2, 300, 70), (W3, 400, 80)):
        _add_workout(user.id, day, volume=vol, weight=w, reps=5)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    assert s.trajectory.reason == "progressing"
    assert s.trajectory.state == TRAJECTORY_ON_TRACK
    assert s.performance.state == "progressing"
    assert s.performance.volume_trend == "up"
    assert s.consistency.state == "consistent"
    assert s.consistency.active_weeks == 4 and s.consistency.sessions == 4


def test_keep_pushing_is_on_track(make_user):
    """Consistent, not plateauing, not rising: steady work is still on track.

    Two active weeks in the window (>= MIN_DATA_WEEKS) but too few for a plateau
    run, with volume inside the trend dead-band.
    """
    user = make_user("pskeep")
    for day in (W1, W2, W3):
        _add_workout(user.id, day, volume=300, weight=80, reps=5)
    # Break the flat-run plateau check with one clearly higher week in the middle.
    _add_workout(user.id, W2 + timedelta(days=1), volume=900, weight=80, reps=5)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    assert s.trajectory.reason == "keep_pushing"
    assert s.trajectory.state == TRAJECTORY_ON_TRACK
    assert s.performance.state == "steady"


def test_build_consistency_needs_attention(make_user):
    """Trained in only 2 of the last 4 windows → inconsistent → needs attention."""
    user = make_user("pscons")
    _add_workout(user.id, W0, volume=100)
    _add_workout(user.id, W3, volume=100)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    assert s.trajectory.reason == "build_consistency"
    assert s.trajectory.state == TRAJECTORY_NEEDS_ATTENTION
    assert s.performance.state == "building_consistency"
    assert s.consistency.state == "inconsistent"
    assert s.consistency.active_weeks == 2
    assert s.consistency.analyzed_weeks == 4


def test_plateau_and_deload_need_attention(make_user):
    """A flat four-week block reads as deload (which outranks plateau)."""
    user = make_user("psplateau")
    for day, vol in ((W0, 300), (W1, 305), (W2, 298), (W3, 302)):
        _add_workout(user.id, day, volume=vol, weight=80, reps=5)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    assert s.trajectory.reason == "deload"
    assert s.trajectory.state == TRAJECTORY_NEEDS_ATTENTION
    assert s.performance.state == "deload"

    # Three flat active weeks with a rest week in between → plateau, not deload.
    user2 = make_user("psplateau2")
    for day, vol in ((W1, 300), (W2, 305), (W3, 298)):
        _add_workout(user2.id, day, volume=vol, weight=80, reps=5)
    db.session.commit()
    s2 = build_progress_summary(user2.id, end_day=END_DAY)
    assert s2.trajectory.reason == "plateau"
    assert s2.trajectory.state == TRAJECTORY_NEEDS_ATTENTION
    assert s2.performance.state == "plateau"


def test_body_does_not_override_the_training_trajectory(make_user):
    """A big weight move in either direction leaves the state untouched.

    The point of V1 being training-led: weight is noisy and the product owns no
    validated body-trend authority, so BODY informs and never classifies.
    """
    baseline = None
    for name, weights in (("psbodyup", (82.0, 78.0)), ("psbodydown", (78.0, 82.0))):
        user = make_user(name)
        for day, vol in ((W0, 300), (W1, 305), (W2, 298), (W3, 302)):
            _add_workout(user.id, day, volume=vol, weight=80, reps=5)
        _add_checkin(user.id, W1, weights[1])
        _add_checkin(user.id, W3, weights[0])
        db.session.commit()

        s = build_progress_summary(user.id, end_day=END_DAY)
        assert s.trajectory.state == TRAJECTORY_NEEDS_ATTENTION
        assert s.trajectory.reason == "deload"
        if baseline is None:
            baseline = s.trajectory
        assert s.trajectory == baseline
        assert s.body.status == BODY_AVAILABLE


def test_current_weight_prefers_the_profile_and_falls_back_to_a_checkin(make_user):
    with_profile = make_user("psweight1", weight=78.4)
    assert build_progress_summary(
        with_profile.id, end_day=END_DAY).body.current_weight_kg == 78.4

    without_profile = make_user("psweight2")
    _add_checkin(without_profile.id, W3, 81.2, qualifying=False)
    db.session.commit()
    body = build_progress_summary(without_profile.id, end_day=END_DAY).body
    # The fallback accepts a sparse /update-weight row: it answers "what does
    # this user weigh", which is a different question from "is this a Progress
    # observation" — and it produces no delta.
    assert body.current_weight_kg == 81.2
    assert body.status == BODY_PARTIAL
    assert body.weight_delta_kg is None


def test_sparse_weight_rows_never_become_progress_observations(make_user):
    """Two /update-weight rows must NOT synthesize a delta."""
    user = make_user("pssparse")
    _add_checkin(user.id, W1, 80.0, qualifying=False)
    _add_checkin(user.id, W3, 79.0, qualifying=False)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=END_DAY).body
    assert body.weight_delta_kg is None
    assert body.status == BODY_PARTIAL


def test_two_qualifying_checkins_produce_the_delta(make_user):
    user = make_user("psdelta", weight=79.4)
    _add_checkin(user.id, W1, 80.0)
    _add_checkin(user.id, W3, 79.4)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=END_DAY).body
    assert body.status == BODY_AVAILABLE
    assert body.weight_delta_kg == -0.6


def test_missing_target_weight_stays_null(make_user):
    user = make_user("pstarget", weight=78.4)
    body = build_progress_summary(user.id, end_day=END_DAY).body
    assert body.target_weight_kg is None
    assert body.distance_to_target_kg is None

    # A stored non-positive target is an unset target, not a goal of zero kg.
    zero = make_user("pstargetzero", weight=78.4, target_weight=0)
    assert build_progress_summary(
        zero.id, end_day=END_DAY).body.target_weight_kg is None


def test_summary_is_scoped_to_the_requesting_user(make_user):
    other = make_user("psother", weight=99.0, target_weight=90.0)
    for day, vol, w in ((W0, 100, 50), (W1, 200, 60), (W2, 300, 70), (W3, 400, 80)):
        _add_workout(other.id, day, volume=vol, weight=w)
    _add_checkin(other.id, W1, 100.0)
    _add_checkin(other.id, W3, 99.0)
    db.session.commit()

    me = make_user("psme")
    s = build_progress_summary(me.id, end_day=END_DAY)
    assert s.trajectory.state == TRAJECTORY_BUILDING_BASELINE
    assert s.consistency.sessions == 0
    assert s.body.status == BODY_INSUFFICIENT_DATA
    assert s.body.current_weight_kg is None


def test_summary_is_deterministic_for_a_fixed_end_day(make_user):
    user = make_user("psdet", weight=78.4, target_weight=75.0)
    for day, vol, w in ((W0, 100, 50), (W1, 200, 60), (W2, 300, 70), (W3, 400, 80)):
        _add_workout(user.id, day, volume=vol, weight=w)
    _add_checkin(user.id, W1, 79.0)
    _add_checkin(user.id, W3, 78.4)
    db.session.commit()

    first = build_progress_summary(user.id, end_day=END_DAY)
    second = build_progress_summary(user.id, end_day=END_DAY)
    assert first == second


def test_window_matches_the_windows_the_signals_were_computed_over(make_user):
    """The reported window is the analyzed window, not parallel date math."""
    from app.services.training_progression import build_progression_report

    user = make_user("pswindow")
    _add_workout(user.id, W2, volume=100)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    report = build_progression_report(user.id, weeks=SUMMARY_WEEKS, end_day=END_DAY)
    assert s.window.start == report.weekly_volume[0].week_start
    assert s.window.weeks == report.weeks == SUMMARY_WEEKS


def test_marker_only_days_still_count_as_sessions(make_user):
    """Session identity is the foundation's, not a second definition here."""
    user = make_user("psmarker")
    _add_workout(user.id, W2, marker=True, volume=0)
    _add_workout(user.id, W3, marker=True, volume=0)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    assert s.consistency.sessions == 2
    assert s.consistency.active_weeks == 2


def test_sessions_never_exceed_the_days_in_the_window(make_user):
    """Summing per-window session_count cannot double-count: windows are disjoint."""
    user = make_user("psdisjoint")
    for i in range(SUMMARY_WEEKS * 7):
        _add_workout(user.id, W0 + timedelta(days=i), volume=100)
    db.session.commit()

    s = build_progress_summary(user.id, end_day=END_DAY)
    assert s.consistency.sessions == SUMMARY_WEEKS * 7
    assert s.consistency.active_weeks == SUMMARY_WEEKS


# ---------------------------------------------------------------------------
# Read-only invariant
# ---------------------------------------------------------------------------

def test_building_a_summary_performs_no_write(make_user, monkeypatch):
    """No add / delete / flush / commit anywhere in the read path."""
    user = make_user("psreadonly", weight=78.4)
    _add_workout(user.id, W3, volume=100)
    _add_checkin(user.id, W1, 80.0)
    _add_checkin(user.id, W3, 79.0)
    db.session.commit()

    calls = []
    for name in ("add", "add_all", "delete", "flush", "commit"):
        original = getattr(db.session, name)

        def _spy(*a, __name=name, __orig=original, **kw):
            calls.append(__name)
            return __orig(*a, **kw)

        monkeypatch.setattr(db.session, name, _spy)

    build_progress_summary(user.id, end_day=END_DAY)
    assert calls == []
    assert not db.session.new and not db.session.dirty and not db.session.deleted


def test_summary_does_not_touch_gamification_or_plan_state(make_user):
    """No streak/quest/XP/plan side effect — the read model is inert."""
    user = make_user("pssideeffect", weight=78.4, streak_count=3, rank_points=120,
                     weekly_xp=40)
    _add_workout(user.id, W3, volume=100)
    db.session.commit()

    build_progress_summary(user.id, end_day=END_DAY)
    db.session.expire_all()
    refreshed = db.session.get(type(user), user.id)
    assert refreshed.streak_count == 3
    assert refreshed.rank_points == 120
    assert refreshed.weekly_xp == 40


# ---------------------------------------------------------------------------
# Architecture guards
# ---------------------------------------------------------------------------

_PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "progress_summary"


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_progress_summary_never_reads_raw_training_data():
    """Performance/consistency must come from training_progression, not WorkoutLog.

    Guards the single-authority rule structurally: if a future change reaches for
    the raw table to "just check something", this fails instead of quietly
    creating a second progression authority.
    """
    for path in sorted(_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "WorkoutLog" not in source, path.name
        assert "training_history.queries" not in source, path.name
        # The pure geometry helper is the one allowed foundation import.
        assert "fetch_workout_entries" not in source, path.name


def test_dependency_direction_is_one_way():
    """Lower layers must not know this package exists — no cycle can form."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    for layer in ("training_history", "training_progression", "training_planning",
                  "weekly_program"):
        for path in (root / layer).rglob("*.py"):
            assert "progress_summary" not in path.read_text(encoding="utf-8"), path


def test_analysis_layer_stays_pure():
    """No DB, no Flask, no clock in the mapping layer."""
    imported = _imported_modules(_PACKAGE / "analysis.py")
    forbidden = {"flask", "app.extensions", "app.models", "sqlalchemy"}
    assert not (imported & forbidden)
    assert "app.timeutil" in imported  # only for the canonical tz constant
    source = (_PACKAGE / "analysis.py").read_text(encoding="utf-8")
    assert "app_today" not in source
    assert "datetime.utcnow" not in source and "date.today" not in source


def test_no_competing_today_authority_in_the_package():
    for path in sorted(_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "utcnow" not in source, path.name
        assert "date.today" not in source, path.name


def _code_tokens(path):
    """Identifiers + non-docstring string literals of a module.

    Prose is excluded on purpose: comments and docstrings in this package spend
    most of their length explaining what is deliberately NOT built ("nothing is
    scored", "no adherence percentage"), so a raw text scan would fail on its own
    documentation. What is under test is what the code *does*.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    tokens = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, ast.arg):
            tokens.add(node.arg)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens.add(node.name)
        elif isinstance(node, ast.alias):
            tokens.add(node.asname or node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            tokens.add(node.value)
    return tokens


def test_no_composite_score_is_computed():
    """No weighted cross-domain score, no 0-100, no confidence percentage, no BMI.

    Checked against code tokens (identifiers, field names, published string
    values) rather than raw source, so the package can keep documenting the
    decision without tripping its own guard.
    """
    for path in sorted(_PACKAGE.glob("*.py")):
        for token in _code_tokens(path):
            lowered = token.lower()
            for banned in ("score", "confidence", "adherence", "bmi", "weight_factor"):
                assert banned not in lowered, f"{path.name} defines {token!r}"


def test_no_weighted_arithmetic_across_domains():
    """Structural: the package multiplies nothing by a weighting constant.

    A hidden composite would have to combine domains numerically somewhere; the
    only arithmetic this read model is allowed is the two body subtractions.
    """
    for path in sorted(_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
                raise AssertionError(
                    f"{path.name}: unexpected weighting arithmetic at line {node.lineno}")


def test_no_llm_or_provider_call_in_the_package():
    for path in sorted(_PACKAGE.glob("*.py")):
        imported = _imported_modules(path)
        assert not any(
            m.startswith(("app.services.ai", "openai", "boto3", "anthropic"))
            for m in imported
        ), path.name
