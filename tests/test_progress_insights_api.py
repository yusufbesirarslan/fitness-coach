"""Contract tests for GET /api/progress/axis-insights (Progress Redesign PR3).

The endpoint is the only runtime surface of ``app/services/progress_insights``,
so what is pinned here is the *wire contract* and the boundary behaviours the
service tests cannot see: auth, input refusal, cache headers, the deliberate
difference between "no insight" and "the backend broke", and the promise that
the legacy insight endpoint keeps its exact contract.

    python -m pytest tests/test_progress_insights_api.py -v
"""
import json
from datetime import datetime, timedelta

from app.blueprints import tracking
from app.extensions import db
from app.models import WeeklyCheckIn, WorkoutLog
from app.services.progress_insights import (
    CONTRACT_VERSION,
    DOMAINS,
    INSIGHTS_WEEKS,
    NEXT_MOVE_CODES,
    SLOT_STATUSES,
    UnknownCanonicalVocabulary,
    WATCH_CODES,
    WORKING_CODES,
)
from app.timeutil import app_today

INSIGHTS_URL = "/api/progress/axis-insights"
LEGACY_URL = "/api/progress/insights"
SUMMARY_URL = "/api/progress/summary"

SLOT_KEYS = ("working", "watch", "next_move")
CODES_BY_SLOT = {
    "working": WORKING_CODES,
    "watch": WATCH_CODES,
    "next_move": NEXT_MOVE_CODES,
}


def _login(make_user, login, name="axapi", **fields):
    user = make_user(name, profile_complete=True, **fields)
    login(name)
    return user


def _add_workout(user_id, day, volume=100.0):
    db.session.add(WorkoutLog(
        user_id=user_id, exercise_name="Squat", sets=3, reps=5, weight_kg=50,
        volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _consistent_history(user_id, weeks=4, volume=1000.0):
    """One trained day in each of the last ``weeks`` trailing windows."""
    today = app_today()
    for week in range(weeks):
        _add_workout(user_id, today - timedelta(days=week * 7), volume)
    db.session.commit()


# ---------------------------------------------------------------------------
# Auth + input surface
# ---------------------------------------------------------------------------

def test_insights_require_authentication(app, client):
    r = client.get(INSIGHTS_URL)
    assert r.status_code in (302, 401)
    assert b"next_move" not in r.data


def test_insights_serve_only_the_authenticated_user(app, client, make_user, login):
    """Rule 10: no request input can name another owner.

    A ``user_id`` parameter must not merely be rejected — it must be
    inexpressible, i.e. ignored entirely with the signed-in user still served.
    """
    other = make_user("axapiother", profile_complete=True)
    _consistent_history(other.id)
    _login(make_user, login, "axapimine")

    mine = client.get(INSIGHTS_URL).get_json()
    assert mine["next_move"]["code"] == "build_baseline"   # my own empty history

    ignored = client.get(f"{INSIGHTS_URL}?user_id={other.id}").get_json()
    assert ignored == mine


def test_window_is_server_pinned_and_not_a_query_parameter(
        app, client, make_user, login):
    """§3.14: no arbitrary analysis window input."""
    _login(make_user, login, "axapiwindow")
    pinned = client.get(INSIGHTS_URL).get_json()["window"]
    assert pinned["weeks"] == INSIGHTS_WEEKS == 4
    assert pinned["timezone"] == "Europe/Istanbul"
    assert pinned["end"] == app_today().isoformat()
    assert pinned["start"] == (
        app_today() - timedelta(days=INSIGHTS_WEEKS * 7 - 1)).isoformat()

    for query in ("?weeks=52", "?end_day=2020-01-01", "?window=1", "?range=year"):
        assert client.get(INSIGHTS_URL + query).get_json()["window"] == pinned


def test_window_matches_the_canonical_summary_window(app, client, make_user, login):
    """Two endpoints, one analysis period — they cannot report different ones."""
    _login(make_user, login, "axapisamewindow")
    assert client.get(INSIGHTS_URL).get_json()["window"] == \
        client.get(SUMMARY_URL).get_json()["window"]


# ---------------------------------------------------------------------------
# The wire contract
# ---------------------------------------------------------------------------

def test_contract_is_versioned_and_bounded(app, client, make_user, login):
    user = _login(make_user, login, "axapicontract")
    _consistent_history(user.id)

    r = client.get(INSIGHTS_URL)
    assert r.status_code == 200
    d = r.get_json()

    assert d["contract_version"] == CONTRACT_VERSION == 1
    assert set(d) == {"contract_version", "window", "working", "watch", "next_move"}
    assert set(d["window"]) == {"weeks", "start", "end", "timezone"}

    # Exactly three slots, each with the SAME five keys — one renderer, no
    # optional keys a client has to probe for.
    for key in SLOT_KEYS:
        slot = d[key]
        assert set(slot) == {"status", "code", "domain", "evidence", "action"}
        assert slot["status"] in SLOT_STATUSES
        if slot["status"] == "available":
            assert slot["code"] in CODES_BY_SLOT[key]
            assert slot["domain"] in DOMAINS
        else:
            assert slot["code"] is None and slot["domain"] is None


def test_no_slot_ever_carries_a_list_of_insights(app, client, make_user, login):
    """§3.4: at most ONE primary insight per slot — no carousel, no ranking."""
    user = _login(make_user, login, "axapisingle")
    _consistent_history(user.id)
    d = client.get(INSIGHTS_URL).get_json()

    for key in SLOT_KEYS:
        assert isinstance(d[key], dict)
        assert not isinstance(d[key]["code"], list)
        for container in (d[key]["evidence"], d[key]["action"]):
            assert container is None or isinstance(container, dict)
            if isinstance(container, dict):
                assert not any(isinstance(v, list) for v in container.values())


def test_next_move_action_is_the_canonical_fraction(app, client, make_user, login):
    """§3.9: the magnitude is the planner's, published unconverted."""
    user = _login(make_user, login, "axapiaction")
    _consistent_history(user.id)
    action = client.get(INSIGHTS_URL).get_json()["next_move"]["action"]

    assert set(action) == {"week_focus", "volume_action", "intensity_action",
                           "volume_delta_pct"}
    assert isinstance(action["volume_delta_pct"], float)
    # A fraction, never a pre-formatted percentage the server decided to render.
    assert -1.0 <= action["volume_delta_pct"] <= 1.0


def test_contract_leaks_no_identifiers_prose_or_provider_content(
        app, client, make_user, login):
    user = _login(make_user, login, "axapileak", email="axapileak@example.com")
    _consistent_history(user.id)
    raw = json.dumps(client.get(INSIGHTS_URL).get_json(), ensure_ascii=False)

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key != "id" and not key.endswith("_id"), key
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    payload = client.get(INSIGHTS_URL).get_json()
    _walk(payload)
    assert user.username not in raw and user.email not in raw

    # Stronger than a substring hunt for the user id (which collides with any
    # small integer): every STRING the slots publish must come from the declared
    # bounded vocabulary. A database identifier, a note, a name or a sentence
    # could not survive that, because none of them is in the enum.
    allowed_strings = (
        set(SLOT_STATUSES) | set(DOMAINS) | set(WORKING_CODES)
        | set(WATCH_CODES) | set(NEXT_MOVE_CODES)
        | {"insufficient_data", "build_consistency", "deload", "maintenance",
           "overload", "steady", "increase", "hold", "decrease", "progress",
           "consistent", "inconsistent", "progressing", "steady_state",
           "inconsistent_training", "deload_due", "plateau_detected",
           "plateau", "volume_trend_down", "strength_trend_down",
           "insufficient_history", "building_baseline", "building_consistency"}
    )
    for key in SLOT_KEYS:
        for container in (payload[key], payload[key]["evidence"] or {},
                          payload[key]["action"] or {}):
            for value in container.values():
                if isinstance(value, str):
                    assert value in allowed_strings, value

    # No localized prose: the client owns every word on this surface.
    for word in ("Your", "training is", "Antrenman", "hafta", "Keep", "Sürdür"):
        assert word not in raw, word


def test_response_is_private_and_not_stored(app, client, make_user, login):
    _login(make_user, login, "axapicache")
    r = client.get(INSIGHTS_URL)
    assert r.headers["Cache-Control"] == "private, no-store"


def test_empty_user_gets_a_canonical_baseline_move_not_a_blank_page(
        app, client, make_user, login):
    """§"Success condition": NEXT MOVE may be a canonical neutral action."""
    _login(make_user, login, "axapiempty")
    d = client.get(INSIGHTS_URL).get_json()

    assert d["working"]["status"] == "insufficient_data"
    assert d["watch"]["status"] == "insufficient_data"
    assert d["next_move"] == {
        "status": "available", "code": "build_baseline", "domain": "training",
        "evidence": None,
        "action": {"week_focus": "insufficient_data", "volume_action": "hold",
                   "intensity_action": "hold", "volume_delta_pct": 0.0},
    }


def test_no_fabricated_positive_or_warning_for_a_sparse_user(
        app, client, make_user, login):
    """Rule 6 + STOP S: the slots stay honest rather than full."""
    user = _login(make_user, login, "axapisparse")
    _add_workout(user.id, app_today())
    db.session.commit()

    d = client.get(INSIGHTS_URL).get_json()
    assert d["working"]["code"] is None
    assert d["watch"]["code"] is None


def test_no_score_streak_or_body_verdict_is_published(app, client, make_user, login):
    """§4: the excluded domains must not appear anywhere in the payload."""
    user = _login(make_user, login, "axapiforbidden", weight=80.0, target_weight=75.0)
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80.0, yogunluk=3,
                                 created_at=datetime.utcnow() - timedelta(days=8)))
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=78.0, yogunluk=3,
                                 created_at=datetime.utcnow()))
    _consistent_history(user.id)

    raw = json.dumps(client.get(INSIGHTS_URL).get_json())
    for banned in ("score", "streak", "weight", "calorie", "protein", "water",
                   "hydration", "fatigue", "sleep", "bmi", "readiness",
                   "confidence", "adherence", "percent", "xp", "level"):
        assert banned not in raw.lower(), banned


# ---------------------------------------------------------------------------
# Failure is not emptiness
# ---------------------------------------------------------------------------

def test_backend_failure_is_not_reported_as_an_empty_insight(
        app, client, make_user, login, monkeypatch):
    """Rule 7 + STOP: an all-clear WATCH THIS must never mean "we broke"."""
    _login(make_user, login, "axapifail")

    def _boom(*_a, **_kw):
        raise RuntimeError("upstream exploded: SELECT * FROM workout_log")

    monkeypatch.setattr(tracking, "build_progress_insights", _boom)

    r = client.get(INSIGHTS_URL)
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    for leaked in ("exploded", "SELECT", "workout_log", "Traceback",
                   "app/services", "empty", "no_insight"):
        assert leaked not in body, leaked
    assert r.get_json()["error"]
    assert "status" not in r.get_json()


def test_unknown_canonical_vocabulary_surfaces_as_failure_not_advice(
        app, client, make_user, login, monkeypatch):
    """Rule 13 end to end: a contract drift is a 500, never a next move."""
    _login(make_user, login, "axapidrift")

    def _drift(*_a, **_kw):
        raise UnknownCanonicalVocabulary("unmapped canonical week focus")

    monkeypatch.setattr(tracking, "build_progress_insights", _drift)

    r = client.get(INSIGHTS_URL)
    assert r.status_code == 500
    assert "build_baseline" not in r.get_data(as_text=True)
    assert tracking._progress_summary_error_class(
        UnknownCanonicalVocabulary("x")) == "contract_drift"


def test_insights_failure_does_not_break_the_other_progress_endpoints(
        app, client, make_user, login, monkeypatch):
    """Rule 18: an Axis Insights outage degrades one section, not the page."""
    _login(make_user, login, "axapiisolated")

    def _boom(*_a, **_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(tracking, "build_progress_insights", _boom)

    assert client.get(INSIGHTS_URL).status_code == 500
    assert client.get(SUMMARY_URL).status_code == 200
    assert client.get(LEGACY_URL).status_code == 200
    assert client.get("/checkin-history").status_code == 200
    assert client.get("/api/progress/achievements").status_code == 200
    assert client.get("/progress-page").status_code == 200


# ---------------------------------------------------------------------------
# Legacy compatibility (§3.14, Rule 14, STOP I)
# ---------------------------------------------------------------------------

def test_legacy_insight_endpoint_keeps_its_exact_contract(
        app, client, make_user, login):
    """PR3 adds a route; it does not change or delete the old one."""
    user = _login(make_user, login, "axapilegacy", weight=80.0)
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80.0, yogunluk=3,
                                 created_at=datetime.utcnow() - timedelta(days=8)))
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=78.5, yogunluk=3,
                                 created_at=datetime.utcnow()))
    _consistent_history(user.id)

    r = client.get(LEGACY_URL)
    assert r.status_code == 200
    d = r.get_json()
    assert set(d) == {"insights"}
    assert isinstance(d["insights"], list) and d["insights"]
    for item in d["insights"]:
        assert set(item) == {"icon", "title", "body", "tone"}
        assert item["tone"] in ("success", "warning", "info")

    # The legacy heuristics (streak, calorie %, workout count, weight delta)
    # still live there and are still localized prose — which is exactly why the
    # new surface is a separate route rather than a change to this one.
    assert any("kg" in item["body"] for item in d["insights"])


def test_the_two_insight_routes_are_independent(app, client, make_user, login):
    """Different paths, different shapes, neither affects the other."""
    user = _login(make_user, login, "axapiboth")
    _consistent_history(user.id)

    legacy = client.get(LEGACY_URL).get_json()
    canonical = client.get(INSIGHTS_URL).get_json()
    assert "insights" not in canonical
    assert "next_move" not in legacy
    assert client.get(LEGACY_URL).get_json() == legacy   # unchanged by the new read


def test_legacy_route_is_not_given_the_new_cache_header(
        app, client, make_user, login):
    """The no-store rule is scoped to the new path; the old response is untouched."""
    _login(make_user, login, "axapilegacycache")
    assert client.get(LEGACY_URL).headers.get("Cache-Control") != "private, no-store"
