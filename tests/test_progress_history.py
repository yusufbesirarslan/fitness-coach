"""Tests for the canonical Progress History read model (Progress Redesign PR5).

    python -m pytest tests/test_progress_history.py -v
"""
import ast
import pathlib
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import WeeklyCheckIn, WorkoutLog
from app.services.progress_history import (
    CONTRACT_VERSION,
    HISTORY_LIMIT,
    STATE_AVAILABLE,
    STATE_EMPTY,
    UnknownProgressionSignal,
    build_progress_history,
    progress_history_payload,
)
from app.services.progress_summary import (
    SUMMARY_WEEKS,
    TRAJECTORY_BUILDING_BASELINE,
    TRAJECTORY_NEEDS_ATTENTION,
    TRAJECTORY_ON_TRACK,
    build_window,
)
from app.services.training_history import weekly_windows
from app.services.training_progression import ProgressionReport
from app.timeutil import app_date_of

END_DAY = date(2026, 7, 15)
W0, W1, W2, W3 = weekly_windows(END_DAY, SUMMARY_WEEKS)


def _add_workout(user_id, day, volume=100.0, sets=3, reps=5, weight=50.0):
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name="Squat",
        sets=sets, reps=reps, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _add_checkin(user_id, day, weight, qualifying=True, created_at=None, yogunluk=3):
    db.session.add(WeeklyCheckIn(
        user_id=user_id,
        weight=weight,
        yogunluk=yogunluk if qualifying else None,
        created_at=created_at or datetime(day.year, day.month, day.day, 12),
    ))


def _progressing(user_id, end_day):
    starts = weekly_windows(end_day, SUMMARY_WEEKS)
    for day, vol, w in zip(starts, (100, 200, 300, 400), (50, 60, 70, 80)):
        _add_workout(user_id, day, volume=vol, weight=w, reps=5)


def _inconsistent(user_id, end_day):
    starts = weekly_windows(end_day, SUMMARY_WEEKS)
    _add_workout(user_id, starts[0], volume=100)
    _add_workout(user_id, starts[-1], volume=100)


# ---------------------------------------------------------------------------
# Empty / qualification / ordering
# ---------------------------------------------------------------------------

def test_no_checkins_is_empty(make_user):
    user = make_user("phempty")
    history = build_progress_history(user.id)
    assert history.state == STATE_EMPTY
    assert history.entries == ()
    assert history.has_more is False


def test_sparse_weight_only_row_is_still_empty(make_user):
    user = make_user("phsparse")
    _add_checkin(user.id, END_DAY, 80.0, qualifying=False)
    db.session.commit()
    history = build_progress_history(user.id)
    assert history.state == STATE_EMPTY
    assert history.entries == ()


def test_one_qualifying_checkin(make_user):
    user = make_user("phone")
    _add_checkin(user.id, END_DAY, 78.4)
    db.session.commit()
    history = build_progress_history(user.id)
    assert history.state == STATE_AVAILABLE
    assert len(history.entries) == 1
    entry = history.entries[0]
    assert entry.analysis_day == END_DAY
    assert entry.trajectory.state == TRAJECTORY_BUILDING_BASELINE
    assert entry.weight_kg == 78.4
    assert entry.weight_delta_kg is None
    assert history.has_more is False


def test_multiple_qualifying_newest_first(make_user):
    user = make_user("phmulti")
    _add_checkin(user.id, END_DAY - timedelta(days=14), 80.0)
    _add_checkin(user.id, END_DAY - timedelta(days=7), 79.5)
    _add_checkin(user.id, END_DAY, 78.4)
    db.session.commit()
    history = build_progress_history(user.id)
    days = [e.analysis_day for e in history.entries]
    assert days == [END_DAY, END_DAY - timedelta(days=7), END_DAY - timedelta(days=14)]


def test_same_timestamp_uses_id_desc_tiebreak(make_user):
    user = make_user("phtie")
    stamp = datetime(2026, 7, 15, 12, 0, 0)
    first = WeeklyCheckIn(user_id=user.id, weight=80.0, yogunluk=3, created_at=stamp)
    db.session.add(first)
    db.session.flush()
    second = WeeklyCheckIn(user_id=user.id, weight=79.0, yogunluk=3, created_at=stamp)
    db.session.add(second)
    db.session.commit()
    history = build_progress_history(user.id)
    assert [e.weight_kg for e in history.entries] == [79.0, 80.0]
    assert history.entries[0].weight_delta_kg == -1.0


def test_twelve_entry_bound_and_thirteenth_delta_context(make_user):
    user = make_user("phbound")
    for i in range(13):
        _add_checkin(user.id, date(2026, 4, 1) + timedelta(days=i * 7), 70.0 + i)
    db.session.commit()
    history = build_progress_history(user.id)
    assert history.state == STATE_AVAILABLE
    assert len(history.entries) == HISTORY_LIMIT
    assert history.has_more is True
    oldest = history.entries[-1]
    assert oldest.weight_kg == 71.0
    assert oldest.weight_delta_kg == 1.0


def test_has_more_false_at_exactly_twelve(make_user):
    user = make_user("ph12")
    for i in range(12):
        _add_checkin(user.id, date(2026, 4, 1) + timedelta(days=i * 7), 70.0 + i)
    db.session.commit()
    history = build_progress_history(user.id)
    assert len(history.entries) == 12
    assert history.has_more is False


def test_owner_isolation(make_user):
    other = make_user("phother")
    _progressing(other.id, END_DAY)
    _add_checkin(other.id, END_DAY, 99.0)
    db.session.commit()
    me = make_user("phme")
    history = build_progress_history(me.id)
    assert history.state == STATE_EMPTY
    assert history.entries == ()


# ---------------------------------------------------------------------------
# Historical time
# ---------------------------------------------------------------------------

def test_istanbul_midnight_anchor(make_user):
    """21:30 UTC on Aug 18 is Aug 19 in Europe/Istanbul."""
    user = make_user("phistanbul")
    _add_checkin(
        user.id, date(2026, 8, 18), 80.0,
        created_at=datetime(2026, 8, 18, 21, 30),
    )
    db.session.commit()
    history = build_progress_history(user.id)
    assert history.entries[0].analysis_day == date(2026, 8, 19)
    assert app_date_of(datetime(2026, 8, 18, 21, 30)) == date(2026, 8, 19)
    payload = progress_history_payload(history)
    assert payload["entries"][0]["analysis_day"] == "2026-08-19"
    assert payload["entries"][0]["checked_in_at"].endswith("+03:00")


def test_window_reuses_summary_geometry(make_user):
    user = make_user("phwindow")
    _add_checkin(user.id, END_DAY, 80.0)
    db.session.commit()
    entry = build_progress_history(user.id).entries[0]
    expected = build_window(END_DAY, SUMMARY_WEEKS)
    assert entry.window == expected
    assert entry.window.weeks == SUMMARY_WEEKS


# ---------------------------------------------------------------------------
# Body reconstruction
# ---------------------------------------------------------------------------

def test_weight_present_missing_and_deltas(make_user):
    user = make_user("phbody")
    _add_checkin(user.id, END_DAY - timedelta(days=21), 80.0)
    _add_checkin(user.id, END_DAY - timedelta(days=14), 80.0)
    _add_checkin(user.id, END_DAY - timedelta(days=7), 79.3)
    _add_checkin(user.id, END_DAY, 0.0)
    db.session.commit()
    history = build_progress_history(user.id)
    newest, down, flat, oldest = history.entries
    assert newest.weight_kg is None
    assert newest.weight_delta_kg is None
    assert down.weight_kg == 79.3
    assert down.weight_delta_kg == -0.7
    assert flat.weight_delta_kg == 0.0
    assert oldest.weight_delta_kg is None


# ---------------------------------------------------------------------------
# Historical training states
# ---------------------------------------------------------------------------

def test_building_baseline_historical_state(make_user):
    user = make_user("phbase")
    _add_checkin(user.id, END_DAY, 80.0)
    db.session.commit()
    entry = build_progress_history(user.id).entries[0]
    assert entry.trajectory.state == TRAJECTORY_BUILDING_BASELINE
    assert entry.trajectory.reason == "insufficient_data"
    assert entry.performance.state == "building_baseline"


def test_on_track_historical_state(make_user):
    user = make_user("phontrack")
    _progressing(user.id, END_DAY)
    _add_checkin(user.id, END_DAY, 80.0)
    db.session.commit()
    entry = build_progress_history(user.id).entries[0]
    assert entry.trajectory.state == TRAJECTORY_ON_TRACK
    assert entry.trajectory.reason == "progressing"
    assert entry.performance.state == "progressing"
    assert entry.performance.volume_trend == "up"
    assert entry.consistency.state == "consistent"


def test_needs_attention_historical_state(make_user):
    user = make_user("phattn")
    _inconsistent(user.id, END_DAY)
    _add_checkin(user.id, END_DAY, 80.0)
    db.session.commit()
    entry = build_progress_history(user.id).entries[0]
    assert entry.trajectory.state == TRAJECTORY_NEEDS_ATTENTION
    assert entry.trajectory.reason == "build_consistency"
    assert entry.performance.state == "building_consistency"
    assert entry.consistency.state == "inconsistent"


def test_unknown_progression_signal_fails_closed(make_user, monkeypatch):
    user = make_user("phunknown")
    _add_checkin(user.id, END_DAY, 80.0)
    db.session.commit()

    monkeypatch.setattr(
        "app.services.progress_history.build_progression_report",
        lambda *a, **kw: ProgressionReport(weeks=4, next_signal="off_track"),
    )
    with pytest.raises(UnknownProgressionSignal):
        build_progress_history(user.id)


# ---------------------------------------------------------------------------
# Future-data isolation
# ---------------------------------------------------------------------------

def test_future_data_does_not_change_a_past_entry(make_user):
    user = make_user("phiso", weight=70.0, target_weight=65.0)
    _progressing(user.id, END_DAY)
    _add_checkin(user.id, END_DAY, 78.4)
    db.session.commit()

    before = build_progress_history(user.id).entries[0]
    snapshot = (
        before.trajectory, before.performance.state, before.consistency.state,
        before.weight_kg, before.weight_delta_kg, before.window,
    )

    _add_workout(user.id, END_DAY + timedelta(days=3), volume=900, weight=120)
    _add_checkin(user.id, END_DAY + timedelta(days=7), 90.0)
    user.weight = 99.0
    user.target_weight = 50.0
    db.session.commit()

    after = build_progress_history(user.id)
    past = next(e for e in after.entries if e.analysis_day == END_DAY)
    assert (
        past.trajectory, past.performance.state, past.consistency.state,
        past.weight_kg, past.weight_delta_kg, past.window,
    ) == snapshot
    assert past.weight_kg == 78.4
    assert past.weight_kg != 99.0


# ---------------------------------------------------------------------------
# Payload / read-only / query budget
# ---------------------------------------------------------------------------

def test_payload_has_no_db_id_or_coach_feedback(make_user):
    user = make_user("phpay")
    db.session.add(WeeklyCheckIn(
        user_id=user.id, weight=78.4, yogunluk=3,
        coach_feedback="You looked strong this week.",
        created_at=datetime(2026, 7, 15, 12),
    ))
    db.session.commit()
    payload = progress_history_payload(build_progress_history(user.id))
    raw = str(payload)
    assert payload["contract_version"] == CONTRACT_VERSION == 1
    assert "coach_feedback" not in raw
    assert "You looked strong" not in raw
    assert '"id"' not in raw
    dumped = progress_history_payload(build_progress_history(user.id))
    assert "user_id" not in str(dumped)


def test_building_history_performs_no_write(make_user, monkeypatch):
    user = make_user("phro", weight=78.4)
    _add_checkin(user.id, END_DAY, 78.4)
    db.session.commit()
    calls = []
    for name in ("add", "add_all", "delete", "flush", "commit"):
        original = getattr(db.session, name)

        def _spy(*a, __name=name, __orig=original, **kw):
            calls.append(__name)
            return __orig(*a, **kw)

        monkeypatch.setattr(db.session, name, _spy)

    build_progress_history(user.id)
    assert calls == []
    assert not db.session.new and not db.session.dirty and not db.session.deleted


def _select_count(user_id):
    statements = []

    def _capture(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        build_progress_history(user_id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    selects = [s for s in statements if str(s).lstrip().lower().startswith("select")]
    joined = "\n".join(statements).lower()
    assert "insert " not in joined
    assert "update " not in joined
    assert "delete " not in joined
    return len(selects)


def test_query_count_is_bounded_independent_of_undisplayed_history(make_user):
    def _seed(name, visible, extra=0):
        user = make_user(name)
        for i in range(visible + extra):
            day = date(2026, 1, 7) + timedelta(days=i * 7)
            _add_checkin(user.id, day, 70.0 + i)
            _add_workout(user.id, day, volume=100)
        db.session.commit()
        return user

    one = _select_count(_seed("phq1", 1).id)
    six = _select_count(_seed("phq6", 6).id)
    twelve = _select_count(_seed("phq12", 12).id)
    twelve_plus = _select_count(_seed("phq12x", 12, extra=40).id)

    assert one == 2
    assert six == 7
    assert twelve == 13
    assert twelve_plus == twelve


# ---------------------------------------------------------------------------
# Architecture guards
# ---------------------------------------------------------------------------

_PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "progress_history"


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _code_source(path):
    """Identifiers + string literals, excluding module/class/function docstrings."""
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
    tokens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            tokens.append(node.value)
    return " ".join(tokens)


def test_history_does_not_call_full_summary_or_insights():
    for path in sorted(_PACKAGE.glob("*.py")):
        source = _code_source(path)
        assert "build_progress_summary" not in source, path.name
        assert "build_progress_insights" not in source, path.name
        assert "derive_adaptive_plan" not in source, path.name
        assert "WorkoutLog" not in source, path.name
        assert "fetch_workout_entries" not in source, path.name


def test_no_llm_or_provider_call_in_the_package():
    for path in sorted(_PACKAGE.glob("*.py")):
        imported = _imported_modules(path)
        assert not any(
            m.startswith(("app.services.ai", "openai", "boto3", "anthropic", "groq"))
            for m in imported
        ), path.name
        source = path.read_text(encoding="utf-8")
        assert "generate_checkin_feedback" not in source
        assert "coach_feedback" not in source or path.name == "payload.py"


def test_dependency_direction_is_one_way():
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    for layer in ("training_history", "training_progression", "training_planning",
                  "weekly_program", "progress_summary", "progress_insights",
                  "progress_physique"):
        for path in (root / layer).rglob("*.py"):
            assert "progress_history" not in path.read_text(encoding="utf-8"), path


def test_no_competing_today_authority_in_the_package():
    for path in sorted(_PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "utcnow" not in source, path.name
        assert "date.today" not in source, path.name
        assert "created_at.date()" not in source, path.name
