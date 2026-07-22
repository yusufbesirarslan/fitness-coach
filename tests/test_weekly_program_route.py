"""Route tests for the read-only weekly-program surface (Sprint 6 PR5 part 2).

`GET /api/training/weekly-program` — the one runtime exposure of
`app/services/weekly_program/`. What these tests protect is the *thinness* of that
route: it must call the canonical consumer once and publish its object unchanged.
Anything else (a query, a threshold, a renamed field, a second window knob) would make
the HTTP layer a competing authority, so each is pinned here — including structurally,
by reading the view's own AST.

Windows are anchored to `weekly_windows(app_today(), 4)` rather than a fixed date: the
route deliberately takes no `end_day`, so the fixtures follow the clock while the
assertions stay exact.

    python -m pytest tests/test_weekly_program_route.py -v
"""
import ast
import dataclasses
from datetime import datetime
from pathlib import Path

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_history import weekly_windows
from app.services.training_planning import build_adaptive_plan
from app.services.weekly_program import (
    UNSUPPORTED_CAPABILITIES,
    WeeklyProgramRecommendation,
    build_weekly_program,
    weekly_program_payload,
)
from app.timeutil import app_today

ROUTE = "/api/training/weekly-program"
VIEW_NAME = "get_weekly_program"
TRAINING_BLUEPRINT = Path("app/blueprints/training.py")


def _signin(make_user, login, name="wpr"):
    user = make_user(name, profile_complete=True)
    login(name)
    return user


def _add_workout(user_id, day, volume=100.0, weight=50.0, reps=5, marker=False):
    """Seed a WorkoutLog on a specific Istanbul day (noon UTC is unambiguous)."""
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=3, reps=reps, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _seed_progressing_block(user_id):
    """Four windows of rising load ending today → overload, 400 kg baseline."""
    windows = weekly_windows(app_today(), 4)
    for day, volume, weight in zip(windows, (100, 200, 300, 400), (50, 60, 70, 80)):
        _add_workout(user_id, day, volume=volume, weight=weight)
    db.session.commit()
    return windows


def _view_function_ast():
    tree = ast.parse(TRAINING_BLUEPRINT.read_text(encoding="utf-8"),
                     filename=str(TRAINING_BLUEPRINT))
    view = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == VIEW_NAME
    )
    return view


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_route_requires_authentication(app, client):
    # Unauthenticated callers are redirected to login by @require_auth — the
    # recommendation is per-user data and has no anonymous form.
    response = client.get(ROUTE)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_route_is_scoped_to_the_signed_in_user(app, client, make_user, login):
    other = make_user("wprother", profile_complete=True)
    _seed_progressing_block(other.id)
    _signin(make_user, login, "wprme")

    payload = client.get(ROUTE).get_json()

    # Another user's block must not leak a single number into this response.
    assert payload["has_data"] is False
    assert payload["week_focus"] == "insufficient_data"
    assert payload["baseline_weekly_volume"] is None
    assert payload["target_weekly_volume"] is None


def test_route_is_read_only(app, client, make_user, login):
    _signin(make_user, login, "wprro")

    # Registered for GET only; a write attempt never reaches the view.
    assert client.post(ROUTE, json={}).status_code == 405
    assert client.delete(ROUTE).status_code == 405


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------

def test_response_matches_the_service_contract_exactly(app, client, make_user, login):
    user = _signin(make_user, login, "wprc")
    _seed_progressing_block(user.id)

    response = client.get(ROUTE)

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    # The route publishes the consumer's object verbatim — no route-side reshaping.
    assert response.get_json() == weekly_program_payload(build_weekly_program(user.id))


def test_payload_exposes_every_recommendation_field_and_nothing_more(app, client,
                                                                    make_user, login):
    _signin(make_user, login, "wprf")

    payload = client.get(ROUTE).get_json()

    # Both directions: a new model field cannot be silently withheld from the API,
    # and the API cannot grow a field the canonical object does not have.
    assert set(payload) == {
        field.name for field in dataclasses.fields(WeeklyProgramRecommendation)
    }


def test_neutral_payload_when_history_is_empty(app, client, make_user, login):
    _signin(make_user, login, "wprn")

    payload = client.get(ROUTE).get_json()

    assert payload == {
        "weeks": 4,
        "has_data": False,
        "week_focus": "insufficient_data",
        "volume_action": "hold",
        "intensity_action": "hold",
        "volume_delta_pct": 0.0,
        "overload_ready": False,
        "maintenance_recommended": False,
        "baseline_week_start": None,
        "baseline_weekly_volume": None,
        "target_weekly_volume": None,
        "reason_codes": ["insufficient_history"],
        "explanation_keys": ["weekly_program.focus.insufficient_data",
                             "weekly_program.reason.insufficient_history"],
        "unsupported": list(UNSUPPORTED_CAPABILITIES),
    }


def test_unsupported_capabilities_are_published_not_invented(app, client,
                                                             make_user, login):
    user = _signin(make_user, login, "wpru")
    _seed_progressing_block(user.id)

    payload = client.get(ROUTE).get_json()

    # AdaptivePlan models none of these, so the API says so instead of guessing.
    assert payload["unsupported"] == ["session_frequency", "intensity_magnitude",
                                      "exercise_selection"]
    assert not set(payload) & set(UNSUPPORTED_CAPABILITIES)


# ---------------------------------------------------------------------------
# Volume semantics (unchanged from part 1 — the route must not reinterpret them)
# ---------------------------------------------------------------------------

def test_baseline_is_observed_and_target_is_plan_arithmetic(app, client,
                                                            make_user, login):
    user = _signin(make_user, login, "wprv")
    windows = _seed_progressing_block(user.id)

    payload = client.get(ROUTE).get_json()

    assert payload["week_focus"] == "overload"
    assert payload["volume_action"] == "increase"
    assert payload["volume_delta_pct"] == 0.05
    # Newest positive window, observed — not recomputed from WorkoutLog by the route.
    assert payload["baseline_week_start"] == windows[-1].isoformat()
    assert payload["baseline_weekly_volume"] == 400.0
    # Pure arithmetic on the plan's own delta: 400 * 1.05, rounded to 2 dp.
    assert payload["target_weekly_volume"] == 420.0


def test_zero_volume_week_is_not_used_as_a_baseline(app, client, make_user, login):
    user = _signin(make_user, login, "wprz")
    windows = weekly_windows(app_today(), 4)
    _add_workout(user.id, windows[1], volume=250.0, weight=60)
    # Newest window holds only a completion marker: a trained day carrying no load.
    _add_workout(user.id, windows[3], volume=0.0, marker=True)
    db.session.commit()

    payload = client.get(ROUTE).get_json()

    # The marker week is missing data, not a measurement of zero — the baseline falls
    # back to the last real training week rather than collapsing the target to 0.
    assert payload["baseline_week_start"] == windows[1].isoformat()
    assert payload["baseline_weekly_volume"] == 250.0
    assert payload["target_weekly_volume"] is not None
    assert payload["target_weekly_volume"] > 0


def test_missing_positive_volume_yields_null_not_zero(app, client, make_user, login):
    user = _signin(make_user, login, "wpr0")
    _add_workout(user.id, weekly_windows(app_today(), 4)[3], volume=0.0, marker=True)
    db.session.commit()

    payload = client.get(ROUTE).get_json()

    # `0` would read as "train nothing this week"; null reads as "not enough data".
    assert payload["has_data"] is True
    assert payload["baseline_week_start"] is None
    assert payload["baseline_weekly_volume"] is None
    assert payload["target_weekly_volume"] is None


# ---------------------------------------------------------------------------
# Determinism and absence of side effects
# ---------------------------------------------------------------------------

def test_repeated_calls_return_identical_payloads(app, client, make_user, login):
    user = _signin(make_user, login, "wprd")
    _seed_progressing_block(user.id)

    first = client.get(ROUTE).get_json()
    second = client.get(ROUTE).get_json()

    assert first == second


def test_request_does_not_mutate_the_plan_or_progression(app, client,
                                                         make_user, login):
    user = _signin(make_user, login, "wprm")
    _seed_progressing_block(user.id)
    before = dataclasses.asdict(build_adaptive_plan(user.id))

    assert client.get(ROUTE).status_code == 200

    # The consumer reads the plan; serving it must leave the planning layer — and the
    # ProgressionReport embedded in it — byte-identical.
    assert dataclasses.asdict(build_adaptive_plan(user.id)) == before


def test_request_writes_nothing(app, client, make_user, login):
    user = _signin(make_user, login, "wprw")
    _seed_progressing_block(user.id)
    logs_before = WorkoutLog.query.count()

    assert client.get(ROUTE).status_code == 200

    assert WorkoutLog.query.count() == logs_before
    assert not db.session.new and not db.session.dirty and not db.session.deleted


def test_view_calls_the_service_once_with_pinned_arguments(app, client, make_user,
                                                           login, monkeypatch):
    user = _signin(make_user, login, "wprs")
    _seed_progressing_block(user.id)
    calls = []

    def _spy(user_id, weeks=4, *, end_day=None):
        calls.append((user_id, weeks, end_day))
        return build_weekly_program(user_id, weeks, end_day=end_day)

    monkeypatch.setattr(training_bp, "build_weekly_program", _spy)

    assert client.get(ROUTE).status_code == 200

    # Exactly one planning pass per request, over the fixed 4-week live-clock window.
    assert calls == [(user.id, 4, None)]


def test_query_string_cannot_retune_the_window(app, client, make_user, login):
    user = _signin(make_user, login, "wprq")
    _seed_progressing_block(user.id)

    default = client.get(ROUTE).get_json()
    tampered = client.get(f"{ROUTE}?weeks=1&end_day=2020-01-01").get_json()

    # The window is a planning knob; the HTTP layer has no authority to offer one.
    assert tampered == default
    assert tampered["weeks"] == 4


# ---------------------------------------------------------------------------
# Structural guard — the route stays a consumer, not a planner
# ---------------------------------------------------------------------------

def test_view_reads_no_history_and_no_planner_directly():
    view = _view_function_ast()
    names = {node.id for node in ast.walk(view) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(view) if isinstance(node, ast.Attribute)}

    # A behavioural test cannot prove this: `training.py` legitimately imports
    # WorkoutLog and db for other routes, so the guard has to read the view itself.
    assert not names & {"WorkoutLog", "db", "PumpCheck", "TrainingPlan",
                        "AdaptivePlan", "build_adaptive_plan", "fetch_workout_entries"}
    assert not attributes & {"query", "session"}


def test_view_body_is_one_service_call_and_one_projection():
    view = _view_function_ast()
    called = [
        node.func.id for node in ast.walk(view)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]

    # Thin by construction: build once, project once, hand it to jsonify. Any extra
    # call is where a second planning authority would start.
    assert sorted(called) == ["build_weekly_program", "jsonify",
                             "weekly_program_payload"]
