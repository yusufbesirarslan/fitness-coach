"""Contract tests for GET /api/progress/summary (Progress Redesign PR2).

The endpoint is the only runtime surface of `app/services/progress_summary`, so
what is pinned here is the *wire contract* and the boundary behaviours the service
tests cannot see: auth, input refusal, cache headers, and the deliberate
difference between "not enough data" and "the backend broke".

    python -m pytest tests/test_progress_summary_api.py -v
"""
import json
from datetime import datetime, timedelta

from app.blueprints import tracking
from app.extensions import db
from app.models import WeeklyCheckIn, WorkoutLog
from app.services.progress_summary import (
    BODY_STATUSES,
    CONSISTENCY_STATES,
    CONTRACT_VERSION,
    PERFORMANCE_STATES,
    SUMMARY_WEEKS,
    TRAJECTORY_STATES,
    UnknownProgressionSignal,
)
from app.timeutil import app_today

SUMMARY_URL = "/api/progress/summary"


def _login(make_user, login, name="pssum", **fields):
    user = make_user(name, profile_complete=True, **fields)
    login(name)
    return user


def _add_workout(user_id, day, volume=100.0):
    db.session.add(WorkoutLog(
        user_id=user_id, exercise_name="Squat", sets=3, reps=5, weight_kg=50,
        volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _add_checkin(user_id, day, weight, qualifying=True):
    db.session.add(WeeklyCheckIn(
        user_id=user_id, weight=weight, yogunluk=3 if qualifying else None,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


# ---------------------------------------------------------------------------
# Auth + input surface
# ---------------------------------------------------------------------------

def test_summary_requires_authentication(app, client):
    r = client.get(SUMMARY_URL)
    assert r.status_code in (302, 401)
    assert b"trajectory" not in r.data


def test_summary_serves_only_the_authenticated_user(app, client, make_user, login):
    other = make_user("psapiother", weight=120.0)
    _add_workout(other.id, app_today())
    _add_checkin(other.id, app_today() - timedelta(days=7), 121.0)
    _add_checkin(other.id, app_today(), 120.0)
    db.session.commit()

    _login(make_user, login, "psapime")
    d = client.get(SUMMARY_URL).get_json()
    # The other user's data is invisible, and there is no input that could ask
    # for it — the route takes no parameters at all.
    assert d["body"]["current_weight_kg"] is None
    assert d["consistency"]["sessions"] == 0

    ignored = client.get(
        SUMMARY_URL + f"?user_id={other.id}&weeks=52&end_day=2020-01-01").get_json()
    assert ignored == d


def test_window_is_server_pinned_to_four_weeks(app, client, make_user, login):
    _login(make_user, login, "pswindowapi")
    d = client.get(SUMMARY_URL).get_json()
    w = d["window"]
    assert w["weeks"] == SUMMARY_WEEKS == 4
    assert w["timezone"] == "Europe/Istanbul"
    assert w["end"] == app_today().isoformat()
    assert w["start"] == (app_today() - timedelta(days=SUMMARY_WEEKS * 7 - 1)).isoformat()


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------

def test_contract_is_versioned_and_bounded(app, client, make_user, login):
    user = _login(make_user, login, "pscontract", weight=78.4, target_weight=75.0)
    for i in range(SUMMARY_WEEKS * 7):
        _add_workout(user.id, app_today() - timedelta(days=i))
    _add_checkin(user.id, app_today() - timedelta(days=7), 79.0)
    _add_checkin(user.id, app_today(), 78.4)
    db.session.commit()

    r = client.get(SUMMARY_URL)
    assert r.status_code == 200
    d = r.get_json()

    assert d["contract_version"] == CONTRACT_VERSION == 1
    assert set(d) == {"contract_version", "window", "trajectory", "body",
                      "performance", "consistency"}
    assert set(d["window"]) == {"weeks", "start", "end", "timezone"}
    assert set(d["trajectory"]) == {"state", "reason"}
    assert set(d["body"]) == {"status", "current_weight_kg", "weight_delta_kg",
                              "target_weight_kg", "distance_to_target_kg"}
    assert set(d["performance"]) == {"state", "volume_trend", "strength_trend",
                                     "next_signal"}
    assert set(d["consistency"]) == {"state", "active_weeks", "analyzed_weeks",
                                     "sessions"}

    # Every published state is from a bounded enum.
    assert d["trajectory"]["state"] in TRAJECTORY_STATES
    assert d["body"]["status"] in BODY_STATUSES
    assert d["performance"]["state"] in PERFORMANCE_STATES
    assert d["consistency"]["state"] in CONSISTENCY_STATES
    assert d["performance"]["volume_trend"] in ("up", "flat", "down")
    assert d["performance"]["strength_trend"] in ("up", "flat", "down")


def test_contract_leaks_no_identifiers_or_prose(app, client, make_user, login):
    user = _login(make_user, login, "psleak", weight=78.4)
    _add_workout(user.id, app_today())
    db.session.commit()

    raw = client.get(SUMMARY_URL).get_data(as_text=True)
    d = json.loads(raw)

    # No id-shaped key at any depth (a raw `str(user.id)` scan would be
    # meaningless — "1" occurs inside every legitimate count).
    def _keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from _keys(v)

    for key in _keys(d):
        assert key != "id" and not key.endswith("_id"), key
    for banned in ("username", "email", "created_at", "user"):
        assert banned not in raw

    assert user.username not in raw and user.email not in raw
    # No localized copy: the client interprets enums through its own catalog.
    for word in ("Track", "Attention", "Yolunda", "Dikkat", "kg "):
        assert word not in raw


def test_missing_and_zero_are_different_claims(app, client, make_user, login):
    """A user with no target and no second observation, but a real zero session count."""
    _login(make_user, login, "pszero", weight=78.4)

    d = client.get(SUMMARY_URL).get_json()
    assert d["body"]["target_weight_kg"] is None        # unset, not 0
    assert d["body"]["weight_delta_kg"] is None         # underivable, not 0.0
    assert d["body"]["distance_to_target_kg"] is None
    assert d["consistency"]["sessions"] == 0            # a real measured zero
    assert d["consistency"]["active_weeks"] == 0
    assert d["consistency"]["analyzed_weeks"] == SUMMARY_WEEKS


def test_empty_user_reads_building_baseline(app, client, make_user, login):
    _login(make_user, login, "psbaseline")
    d = client.get(SUMMARY_URL).get_json()
    assert d["trajectory"] == {"state": "building_baseline",
                               "reason": "insufficient_data"}
    assert d["performance"]["state"] == "building_baseline"


def test_summary_response_is_private_and_not_stored(app, client, make_user, login):
    _login(make_user, login, "pscache")
    r = client.get(SUMMARY_URL)
    assert r.headers["Cache-Control"] == "private, no-store"


def test_no_composite_score_is_published(app, client, make_user, login):
    user = _login(make_user, login, "psnoscore", weight=78.4, target_weight=75.0)
    _add_workout(user.id, app_today())
    db.session.commit()

    raw = client.get(SUMMARY_URL).get_data(as_text=True).lower()
    for banned in ("score", "confidence", "adherence", "percent", "bmi", "rating"):
        assert banned not in raw


# ---------------------------------------------------------------------------
# Failure semantics
# ---------------------------------------------------------------------------

def test_backend_failure_is_not_reported_as_insufficient_data(
        app, client, make_user, login, monkeypatch):
    """Rule 9: a system fault must never masquerade as an honest user state."""
    _login(make_user, login, "psfail")

    def _boom(*a, **kw):
        raise RuntimeError("SELECT * FROM user WHERE id = 42 -- secret")

    monkeypatch.setattr(tracking, "build_progress_summary", _boom)

    r = client.get(SUMMARY_URL)
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert "building_baseline" not in body
    assert "trajectory" not in body
    # No exception text, SQL, identifier or path leaks.
    for leak in ("SELECT", "secret", "RuntimeError", "Traceback", "app/services"):
        assert leak not in body
    assert r.get_json()["error"]


def test_unknown_canonical_signal_surfaces_as_failure_not_a_state(
        app, client, make_user, login, monkeypatch):
    """Contract drift fails closed at the boundary, with its own log class."""
    _login(make_user, login, "psdrift")

    def _boom(*a, **kw):
        raise UnknownProgressionSignal("unmapped training progression signal")

    monkeypatch.setattr(tracking, "build_progress_summary", _boom)
    r = client.get(SUMMARY_URL)
    assert r.status_code == 500
    assert "building_baseline" not in r.get_data(as_text=True)
    assert tracking._progress_summary_error_class(
        UnknownProgressionSignal("x")) == "contract_drift"


def test_summary_failure_does_not_break_the_other_progress_endpoints(
        app, client, make_user, login, monkeypatch):
    """Rule 13: Insights / Physique / History stay independently loadable."""
    user = _login(make_user, login, "psisolation")
    _add_checkin(user.id, app_today(), 78.0)
    db.session.commit()

    monkeypatch.setattr(
        tracking, "build_progress_summary",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))

    assert client.get(SUMMARY_URL).status_code == 500
    assert client.get("/api/progress/insights").status_code == 200
    assert client.get("/checkin-history").status_code == 200
    assert client.get("/api/progress/achievements").status_code == 200
    assert client.get("/api/progress/workout?range=month").status_code == 200


# ---------------------------------------------------------------------------
# Compatibility: PR2 converges, it does not clean up
# ---------------------------------------------------------------------------

def test_existing_progress_endpoints_keep_their_semantics(
        app, client, make_user, login):
    user = _login(make_user, login, "pscompat", weight=78.0)
    _add_workout(user.id, app_today())
    _add_checkin(user.id, app_today(), 78.0)
    db.session.commit()

    workout = client.get("/api/progress/workout?range=month").get_json()
    assert set(workout) == {"days", "totals", "current"}
    assert set(workout["totals"]) == {"sessions", "volume"}

    achievements = client.get("/api/progress/achievements").get_json()
    for key in ("level", "title", "rank_points", "weekly_xp", "streak",
                "quests_done", "weekly_wins", "milestones"):
        assert key in achievements

    history = client.get("/checkin-history").get_json()
    assert isinstance(history, list) and history
    assert set(history[0]) == {"tarih", "kilo", "yogunluk", "fatigue", "overload",
                               "uyku", "beslenme", "feedback"}

    insights = client.get("/api/progress/insights").get_json()
    assert "insights" in insights and isinstance(insights["insights"], list)


def test_summary_read_performs_no_write(app, client, make_user, login):
    """The endpoint must not mutate streak/XP or create rows.

    The baseline is captured after an unrelated authenticated GET on purpose.
    `app/hooks.py::update_streak` is an app-wide before_request hook that fires
    on the first *authenticated* request of the day (login itself does not
    trigger it — the user is still anonymous at before_request time). Seeding a
    value and comparing straight against it would attribute that global write
    to this endpoint. Letting the hook settle first isolates what /summary
    itself does.
    """
    user = _login(make_user, login, "psnowrite", weight=78.0, streak_count=5,
                  rank_points=99)
    assert client.get("/api/progress/achievements").status_code == 200
    db.session.expire_all()
    before = db.session.get(type(user), user.id)
    before_streak, before_xp = before.streak_count, before.rank_points
    before_checkins = WeeklyCheckIn.query.filter_by(user_id=user.id).count()

    assert client.get(SUMMARY_URL).status_code == 200

    db.session.expire_all()
    refreshed = db.session.get(type(user), user.id)
    assert refreshed.streak_count == before_streak
    assert refreshed.rank_points == before_xp
    assert WeeklyCheckIn.query.filter_by(user_id=user.id).count() == before_checkins
