"""Contract tests for the native Progress summary (LP-04, spec J1).

    GET /api/v1/progress/summary

The route is a transport of the canonical `app/services/progress_summary` read
model, so what is pinned here is the *native wire contract* and the boundary
behaviour the service tests cannot see:

  * the literal `contract_version: 1` body, for a new account and a populated one;
  * missing is `null`, a measured zero is `0`, and "not enough data yet" is a
    bounded state - never a fabricated trend, streak or zero;
  * the owner is the Bearer principal and nothing else (real opaque credentials,
    two accounts, interleaved, with ownership selectors in every request slot);
  * `Cache-Control: no-store`;
  * a failed computation is a typed 503 in the ADR 0001 envelope, never a
    baseline summary, and never an auth-flavoured error.

    python -m pytest tests/test_mobile_progress_summary_api.py -v
"""
import calendar
import json
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, WeeklyCheckIn, WorkoutLog
from app.services import cognito_jwt, cognito_service, mobile_auth
from app.services import progress_summary as progress_summary_service
from app.services.progress_summary import (
    BODY_STATUSES,
    CONSISTENCY_STATES,
    PERFORMANCE_STATES,
    SUMMARY_WEEKS,
    TRAJECTORY_STATES,
    UnknownProgressionSignal,
    build_progress_summary,
    progress_summary_payload,
)
from app.timeutil import APP_TZ, audit_clock


PATH = "/api/v1/progress/summary"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}

# A fixed Thursday, 15:00 Istanbul - safely inside the day, so a suite run near
# midnight cannot make the pinned window disagree with itself.
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
TODAY = date(2026, 7, 23)
WINDOW_START = TODAY - timedelta(days=SUMMARY_WEEKS * 7 - 1)   # 2026-06-26


# -- Principals ----------------------------------------------------------------
@pytest.fixture
def principal(monkeypatch):
    """Bearer principal stub: whichever user was last selected.

    Used where the credential store is not the subject. The isolation tests
    below use `real_bearer` instead, so the ownership proof runs through the
    real opaque-credential pipeline.
    """
    current = {}

    def authenticate(raw):
        user = current["user"]
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def as_user(user):
        current["user"] = user
        return {"Authorization": "Bearer opaque-progress-access"}
    return as_user


@pytest.fixture
def real_bearer(monkeypatch):
    """Issue a real opaque mobile session for a user; only Cognito is faked.

    `mobile_auth.login` mints and stores the credential and
    `authenticate_access` resolves it on every request, exactly as in
    production - so "A's bearer" really is A's credential and nothing else.
    """
    subs = {}

    def authenticate(username, password):
        sub = subs[username]
        return {"tokens": {
            "access_token": f"access|{sub}", "id_token": f"id|{sub}",
            "refresh_token": f"refresh|{sub}", "expires_in": 3600},
            "claims": {"sub": sub}}

    def validate(token, expected_use, leeway_seconds=0):
        sub = token.split("|", 1)[1]
        if expected_use == "id":
            return {"sub": sub, "email": f"{sub}@example.com",
                    "email_verified": True}
        return {"sub": sub, "exp": calendar.timegm(
            (datetime.utcnow() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)

    def issue(user):
        subs[user.username] = user.cognito_sub
        issued = mobile_auth.login(user.username, "Sifre123")
        return {"Authorization": f"Bearer {issued.access_credential}"}
    return issue


# -- Canonical fixtures (real persistence, never a stubbed authority) ----------
def _workout(user_id, day, volume=1000.0, weight=50.0, marker=False):
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=WORKOUT_COMPLETION_MARKER if marker else "Squat",
        sets=0 if marker else 3, reps=0 if marker else 5,
        weight_kg=0 if marker else weight, volume=0 if marker else volume,
        created_at=datetime(day.year, day.month, day.day, 9)))


def _checkin(user_id, day, weight, qualifying=True):
    db.session.add(WeeklyCheckIn(
        user_id=user_id, weight=weight, yogunluk=3 if qualifying else None,
        created_at=datetime(day.year, day.month, day.day, 9)))


def seed_body(user):
    """Three qualifying weekly check-ins, one sparse /update-weight row."""
    for offset, weight in ((14, 79.4), (7, 79.0), (0, 78.4)):
        _checkin(user.id, TODAY - timedelta(days=offset), weight)
    _checkin(user.id, TODAY - timedelta(days=3), 99.0, qualifying=False)


def seed_training(user, weeks=SUMMARY_WEEKS, per_week=3, base=1000.0, step=150.0):
    """`per_week` sessions in each of the newest `weeks` weeks, rising load."""
    for week in range(weeks):
        week_end = TODAY - timedelta(days=7 * (SUMMARY_WEEKS - 1 - week))
        for session in range(per_week):
            _workout(user.id, week_end - timedelta(days=2 * session),
                     volume=base + step * week, weight=50.0 + 2.5 * week)


def seed_mixed(user):
    seed_body(user)
    seed_training(user)


def read(client, headers, path=PATH, now=FIXED_NOW, **kwargs):
    with audit_clock(now):
        return client.get(path, headers=headers, **kwargs)


def canonical(user_id):
    """The answer the canonical builder gives for this user on the pinned day."""
    return progress_summary_payload(build_progress_summary(user_id, end_day=TODAY))


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


# -- Literal contract ------------------------------------------------------------
BASELINE_BODY = {
    "contract_version": 1,
    "window": {"weeks": 4, "start": "2026-06-26", "end": "2026-07-23",
               "timezone": "Europe/Istanbul"},
    "trajectory": {"state": "building_baseline", "reason": "insufficient_data"},
    "body": {"status": "insufficient_data", "current_weight_kg": None,
             "weight_delta_kg": None, "target_weight_kg": None,
             "distance_to_target_kg": None, "weight_series": []},
    "performance": {"state": "building_baseline", "volume_trend": "flat",
                    "strength_trend": "flat", "next_signal": "insufficient_data"},
    "consistency": {"state": "insufficient_data", "active_weeks": 0,
                    "analyzed_weeks": 4, "sessions": 0},
    "weekly": [
        {"start": "2026-06-26", "sessions": 0, "active": False, "volume_kg": 0.0},
        {"start": "2026-07-03", "sessions": 0, "active": False, "volume_kg": 0.0},
        {"start": "2026-07-10", "sessions": 0, "active": False, "volume_kg": 0.0},
        {"start": "2026-07-17", "sessions": 0, "active": False, "volume_kg": 0.0},
    ],
}

POPULATED_BODY = {
    "contract_version": 1,
    "window": {"weeks": 4, "start": "2026-06-26", "end": "2026-07-23",
               "timezone": "Europe/Istanbul"},
    "trajectory": {"state": "on_track", "reason": "progressing"},
    "body": {"status": "available", "current_weight_kg": 78.4,
             "weight_delta_kg": -0.6, "target_weight_kg": 75.0,
             "distance_to_target_kg": 3.4,
             "weight_series": [
                 {"day": "2026-07-09", "weight_kg": 79.4},
                 {"day": "2026-07-16", "weight_kg": 79.0},
                 {"day": "2026-07-23", "weight_kg": 78.4}]},
    "performance": {"state": "progressing", "volume_trend": "up",
                    "strength_trend": "up", "next_signal": "progressing"},
    "consistency": {"state": "consistent", "active_weeks": 4,
                    "analyzed_weeks": 4, "sessions": 12},
    "weekly": [
        {"start": "2026-06-26", "sessions": 3, "active": True, "volume_kg": 3000.0},
        {"start": "2026-07-03", "sessions": 3, "active": True, "volume_kg": 3450.0},
        {"start": "2026-07-10", "sessions": 3, "active": True, "volume_kg": 3900.0},
        {"start": "2026-07-17", "sessions": 3, "active": True, "volume_kg": 4350.0},
    ],
}


def test_new_account_reads_the_literal_baseline_contract(
        client, make_user, principal):
    """A: no data at all. Every state says "not enough yet"; nothing invented."""
    user = make_user("progress-new")
    response = read(client, principal(user))

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == BASELINE_BODY


def test_populated_account_reads_the_literal_canonical_contract(
        client, make_user, principal):
    """E: body + training history, rendered through the canonical interpretation."""
    user = make_user("progress-full", weight=78.4, target_weight=75.0)
    seed_mixed(user)
    db.session.commit()

    response = read(client, principal(user))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == POPULATED_BODY
    # The literal is the canonical builder's answer, not a mobile opinion of it.
    assert response.get_json() == canonical(user.id)


def test_contract_shape_types_and_bounded_states(client, make_user, principal):
    user = make_user("progress-shape", weight=78.4, target_weight=75.0)
    seed_mixed(user)
    db.session.commit()
    d = read(client, principal(user)).get_json()

    assert type(d["contract_version"]) is int and d["contract_version"] == 1
    assert set(d) == {"contract_version", "window", "trajectory", "body",
                      "performance", "consistency", "weekly"}
    assert set(d["window"]) == {"weeks", "start", "end", "timezone"}
    assert set(d["trajectory"]) == {"state", "reason"}
    assert set(d["body"]) == {"status", "current_weight_kg", "weight_delta_kg",
                              "target_weight_kg", "distance_to_target_kg",
                              "weight_series"}
    assert set(d["performance"]) == {"state", "volume_trend", "strength_trend",
                                     "next_signal"}
    assert set(d["consistency"]) == {"state", "active_weeks", "analyzed_weeks",
                                     "sessions"}

    assert type(d["window"]["weeks"]) is int
    for key in ("start", "end"):
        date.fromisoformat(d["window"][key])
    assert d["trajectory"]["state"] in TRAJECTORY_STATES
    assert d["body"]["status"] in BODY_STATUSES
    assert d["performance"]["state"] in PERFORMANCE_STATES
    assert d["consistency"]["state"] in CONSISTENCY_STATES
    for trend in ("volume_trend", "strength_trend"):
        assert d["performance"][trend] in ("up", "flat", "down")
    for key in ("current_weight_kg", "weight_delta_kg", "target_weight_kg",
                "distance_to_target_kg"):
        assert d["body"][key] is None or type(d["body"][key]) is float
    for point in d["body"]["weight_series"]:
        assert set(point) == {"day", "weight_kg"}
        date.fromisoformat(point["day"])
        assert type(point["weight_kg"]) is float
    for key in ("active_weeks", "analyzed_weeks", "sessions"):
        assert type(d["consistency"][key]) is int
    assert len(d["weekly"]) == SUMMARY_WEEKS
    for week in d["weekly"]:
        assert set(week) == {"start", "sessions", "active", "volume_kg"}
        date.fromisoformat(week["start"])
        assert type(week["sessions"]) is int
        assert type(week["active"]) is bool
        assert type(week["volume_kg"]) is float


def test_contract_carries_no_identifier_owner_field_or_prose(
        client, make_user, principal):
    user = make_user("progress-leak", weight=78.4, target_weight=75.0)
    seed_mixed(user)
    db.session.commit()

    raw = read(client, principal(user)).get_data(as_text=True)
    d = json.loads(raw)

    def _keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from _keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from _keys(item)

    for key in _keys(d):
        assert key != "id" and not key.endswith("_id"), key
        assert key not in {"user", "username", "email", "owner", "account",
                           "created_at", "updated_at", "debug"}, key
    assert user.username not in raw and user.email not in raw
    assert user.cognito_sub not in raw
    # No score, rate or streak the canonical model does not own.
    for banned in ("score", "percent", "adherence", "streak", "confidence", "bmi"):
        assert banned not in raw.lower()


# -- Baseline / insufficient-data matrix ------------------------------------------
def _body_only(user):
    seed_body(user)


def _training_only(user):
    seed_training(user)


def _consistency_only(user):
    # One light session in every analysed week at a constant load: consistent
    # attendance with no progression claim.
    seed_training(user, per_week=1, step=0.0)


def _markers_only(user):
    # Native completions write only the completion marker (spec C, known
    # limitation): sessions count, performance trends stay in baseline.
    for offset in (0, 7, 14, 21):
        _workout(user.id, TODAY - timedelta(days=offset), marker=True)


def _incomplete(user):
    # One qualifying check-in (no delta derivable) plus one old session.
    _checkin(user.id, TODAY - timedelta(days=2), 81.0)
    _workout(user.id, TODAY - timedelta(days=20))


MATRIX = {
    # name: (user fields, seeder, expected literal facts)
    "body_only": ({"weight": 78.4, "target_weight": 75.0}, _body_only, {
        ("trajectory", "state"): "building_baseline",
        ("body", "status"): "available",
        ("body", "weight_delta_kg"): -0.6,
        ("consistency", "sessions"): 0,
        ("performance", "state"): "building_baseline",
    }),
    "training_only": ({}, _training_only, {
        ("trajectory", "state"): "on_track",
        ("body", "status"): "insufficient_data",
        ("body", "current_weight_kg"): None,
        ("consistency", "sessions"): 12,
        ("performance", "state"): "progressing",
    }),
    "consistency_only": ({}, _consistency_only, {
        ("consistency", "state"): "consistent",
        ("consistency", "active_weeks"): 4,
        ("consistency", "sessions"): 4,
        ("performance", "volume_trend"): "flat",
    }),
    "completion_markers_only": ({}, _markers_only, {
        ("consistency", "sessions"): 4,
        ("performance", "volume_trend"): "flat",
        ("performance", "strength_trend"): "flat",
    }),
    "incomplete": ({"target_weight": 0}, _incomplete, {
        ("body", "status"): "partial",
        ("body", "current_weight_kg"): 81.0,
        ("body", "weight_delta_kg"): None,
        ("body", "target_weight_kg"): None,
        ("body", "distance_to_target_kg"): None,
        ("consistency", "sessions"): 1,
    }),
}


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_matrix_serves_the_canonical_interpretation(
        client, make_user, principal, name):
    fields, seed, expected = MATRIX[name]
    user = make_user(f"progress-{name}", **fields)
    seed(user)
    db.session.commit()

    response = read(client, principal(user))
    assert response.status_code == 200
    d = response.get_json()

    # Mobile is the canonical builder's answer, byte for byte ...
    assert d == canonical(user.id)
    # ... and that answer carries the documented semantics for this history.
    for (section, key), value in expected.items():
        assert d[section][key] == value, (name, section, key)


def test_missing_and_zero_are_different_claims(client, make_user, principal):
    """null = unknown, 0 = a real measured zero; never the other way round."""
    user = make_user("progress-zero", weight=78.4)
    d = read(client, principal(user)).get_json()

    assert d["body"]["status"] == "partial"
    assert d["body"]["current_weight_kg"] == 78.4
    assert d["body"]["target_weight_kg"] is None
    assert d["body"]["weight_delta_kg"] is None
    assert d["body"]["distance_to_target_kg"] is None
    assert d["body"]["weight_series"] == []
    assert d["consistency"]["sessions"] == 0
    assert d["consistency"]["active_weeks"] == 0
    assert d["consistency"]["analyzed_weeks"] == SUMMARY_WEEKS
    assert d["trajectory"]["state"] == "building_baseline"


def test_sparse_weight_row_never_becomes_a_progress_observation(
        client, make_user, principal):
    """BUG-5 filter: an /update-weight row feeds current weight, never a trend."""
    user = make_user("progress-sparse")
    _checkin(user.id, TODAY, 90.0, qualifying=False)
    db.session.commit()

    body = read(client, principal(user)).get_json()["body"]
    assert body["current_weight_kg"] == 90.0
    assert body["weight_delta_kg"] is None
    assert body["weight_series"] == []


# -- Server-pinned window: the caller cannot re-window or re-date -----------------
def test_window_is_the_server_istanbul_day_not_a_request_parameter(
        client, make_user, principal):
    user = make_user("progress-window")
    headers = principal(user)
    pinned = read(client, headers).get_json()

    moved = read(client, headers,
                 PATH + "?weeks=52&end_day=2020-01-01&start=2019-01-01"
                        "&timezone=UTC").get_json()
    assert moved == pinned
    assert pinned["window"]["end"] == TODAY.isoformat()
    assert pinned["window"]["start"] == WINDOW_START.isoformat()

    # 23:30 Istanbul is still the same day even though UTC is too; 00:30 is the
    # next one. The day comes from app.timeutil, never from a second rule here.
    late = read(client, headers,
                now=datetime(2026, 7, 23, 23, 30, tzinfo=APP_TZ)).get_json()
    after = read(client, headers,
                 now=datetime(2026, 7, 24, 0, 30, tzinfo=APP_TZ)).get_json()
    assert late["window"]["end"] == "2026-07-23"
    assert after["window"]["end"] == "2026-07-24"


# -- Authentication boundary --------------------------------------------------------
@pytest.mark.parametrize("headers", [
    None,
    {"Authorization": "Bearer"},
    {"Authorization": "Basic dXNlcjpwYXNz"},
    {"Authorization": "Bearer two words"},
])
def test_missing_or_malformed_bearer_is_401_without_data(raw_client, headers):
    response = raw_client.get(PATH, headers=headers)
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert response.headers["Cache-Control"] == "no-store"
    assert "trajectory" not in response.get_data(as_text=True)


def test_unknown_bearer_is_rejected_through_the_shared_middleware(
        raw_client, make_user, real_bearer):
    real_bearer(make_user("progress-someone"))
    response = raw_client.get(
        PATH, headers={"Authorization": "Bearer not-a-real-credential"})
    assert response.status_code == 401
    assert _error(response)["code"].startswith("AUTH_")
    assert "trajectory" not in response.get_data(as_text=True)


def test_web_cookie_session_cannot_read_the_native_summary(
        client, make_user, login):
    make_user("progress-cookie", weight=80.0)
    login("progress-cookie")
    # The browser route answers for the cookie session ...
    assert client.get("/api/progress/summary").status_code == 200
    # ... the native one does not: it is Bearer-only.
    response = client.get(PATH)
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert "trajectory" not in response.get_data(as_text=True)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_only_get_is_published(client, make_user, principal, method):
    user = make_user(f"progress-{method}")
    response = getattr(client, method)(PATH, headers=principal(user), json={})
    assert response.status_code == 405
    assert "trajectory" not in response.get_data(as_text=True)


# -- Account isolation (real opaque credentials) ------------------------------------
@pytest.fixture
def two_accounts(make_user, real_bearer):
    """A and B with deliberately different histories and distinctive ids.

    The ids are chosen so that neither can occur by accident as another bound
    SQL parameter, which is what makes the parameter scan below meaningful.
    """
    a = make_user("progress-alice", id=7301, cognito_sub="sub-progress-alice",
                  weight=78.4, target_weight=75.0)
    b = make_user("progress-bob", id=8402, cognito_sub="sub-progress-bob",
                  weight=101.0, target_weight=90.0)
    seed_mixed(a)
    _checkin(b.id, TODAY - timedelta(days=1), 101.0)
    _workout(b.id, TODAY - timedelta(days=5), volume=222.0)
    db.session.commit()
    return a, b, real_bearer(a), real_bearer(b)


def test_each_bearer_reads_only_its_own_summary_in_any_order(
        client, two_accounts):
    a, b, a_headers, b_headers = two_accounts
    expected = {a.id: canonical(a.id), b.id: canonical(b.id)}
    assert expected[a.id] != expected[b.id]

    order = [a, b, b, a, b, a, a, b]
    headers = {a.id: a_headers, b.id: b_headers}
    for user in order:
        response = read(client, headers[user.id])
        assert response.status_code == 200
        assert response.get_json() == expected[user.id], user.username

    a_body = read(client, a_headers).get_json()["body"]
    assert a_body["current_weight_kg"] == 78.4          # never B's 101.0
    b_body = read(client, b_headers).get_json()["body"]
    assert b_body["current_weight_kg"] == 101.0         # never A's 78.4


def test_ownership_selectors_in_every_request_slot_are_ignored(
        client, two_accounts):
    a, b, a_headers, _ = two_accounts
    own = read(client, a_headers).get_json()

    selectors = {"user_id": b.id, "account_id": b.id, "owner_id": b.id,
                 "username": b.username, "email": b.email}
    query = "&".join(f"{k}={v}" for k, v in selectors.items())
    spoof_headers = dict(a_headers, **{
        "X-User-Id": str(b.id), "X-Account-Id": str(b.id),
        "X-Username": b.username, "X-Owner-Id": str(b.id)})

    attempts = [
        read(client, a_headers, PATH + "?" + query),
        read(client, spoof_headers),
        read(client, a_headers, json=selectors),
        read(client, spoof_headers, PATH + "?" + query, json=selectors),
    ]
    for response in attempts:
        assert response.status_code == 200
        assert response.get_json() == own


def test_a_request_never_binds_the_other_accounts_id(client, two_accounts):
    """Statement-level proof: B's id is never a parameter of A's summary read.

    A's id must appear (otherwise the scan proves nothing), and B's must not -
    not in the credential lookup, not in any Progress read.
    """
    a, b, a_headers, b_headers = two_accounts
    read(client, b_headers)        # warm any per-process state with B first

    params = []

    def _record(conn, cursor, statement, parameters, context, many):
        rows = parameters if many else [parameters]
        for row in rows:
            values = row.values() if isinstance(row, dict) else row
            params.extend(values or ())

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        response = read(client, a_headers)
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)

    assert response.status_code == 200
    assert a.id in params
    assert b.id not in params and str(b.id) not in params


# -- Temporary unavailability ---------------------------------------------------------
def _assert_typed_503(response):
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    error = _error(response)
    assert error == {
        "code": "PROGRESS_UNAVAILABLE",
        "message": "Progress is temporarily unavailable.",
        "retryable": True,
        "request_id": error["request_id"],
    }
    raw = response.get_data(as_text=True)
    # Never a summary-shaped body, and never the baseline in disguise.
    for token in ("contract_version", "trajectory", "building_baseline",
                  "insufficient_data", "weekly"):
        assert token not in raw
    return raw


def test_storage_failure_is_a_typed_503_not_a_baseline(
        client, make_user, principal, monkeypatch, caplog):
    user = make_user("progress-db-down")

    def _down(user_id):
        raise OperationalError(
            "SELECT weight FROM user WHERE id = 42", {}, Exception("secret-dsn"))

    monkeypatch.setattr(progress_summary_service, "fetch_body_facts", _down)
    with caplog.at_level(logging.INFO):
        raw = _assert_typed_503(read(client, principal(user)))

    for leak in ("SELECT", "secret", "OperationalError", "Traceback",
                 "app/services", "AUTH_"):
        assert leak not in raw
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "mobile_progress event=summary_read_failed" in logged
    assert "error_type=OperationalError" in logged
    assert "secret" not in logged and "SELECT" not in logged


def test_unknown_canonical_signal_fails_closed_as_a_typed_503(
        client, make_user, principal, monkeypatch):
    """Contract drift upstream is a fault, never a state the client can render."""
    user = make_user("progress-drift")
    real_report = progress_summary_service.build_progression_report

    def _drifted(*args, **kwargs):
        return replace(real_report(*args, **kwargs), next_signal="brand_new")

    monkeypatch.setattr(
        progress_summary_service, "build_progression_report", _drifted)
    with pytest.raises(UnknownProgressionSignal):
        build_progress_summary(user.id, end_day=TODAY)

    _assert_typed_503(read(client, principal(user)))


def test_unexpected_failure_is_progress_shaped_not_auth_shaped(
        client, make_user, principal, monkeypatch):
    """The blueprint's catch-all would say AUTH_TEMPORARILY_UNAVAILABLE and
    make a client discard a good session; this route must not reach it."""
    user = make_user("progress-bug")
    monkeypatch.setattr(
        progress_summary_service, "summarize_weeks",
        lambda report: (_ for _ in ()).throw(TypeError("bug at app/x.py:12")))

    raw = _assert_typed_503(read(client, principal(user)))
    assert "bug at" not in raw and "TypeError" not in raw


def test_recovery_after_a_failure_serves_the_real_summary(
        client, make_user, principal, monkeypatch):
    user = make_user("progress-recover", weight=80.0)
    headers = principal(user)
    with monkeypatch.context() as patch:
        patch.setattr(progress_summary_service, "fetch_body_facts",
                      lambda user_id: (_ for _ in ()).throw(
                          OperationalError("x", {}, Exception("down"))))
        assert read(client, headers).status_code == 503

    response = read(client, headers)
    assert response.status_code == 200
    assert response.get_json() == canonical(user.id)


# -- Read-only, and quiet about private data --------------------------------------------
def test_summary_read_performs_no_write(client, make_user, principal):
    user = make_user("progress-readonly", weight=78.4, target_weight=75.0)
    seed_mixed(user)
    db.session.commit()
    headers = principal(user)
    read(client, headers)     # let any once-a-day request hook settle first

    writes = []

    def _record(conn, cursor, statement, parameters, context, many):
        verb = statement.lstrip().split(None, 1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        assert read(client, headers).status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)
    assert writes == []


def test_success_log_carries_states_only(client, make_user, principal, caplog):
    user = make_user("progress-log", weight=78.4, target_weight=75.0)
    seed_mixed(user)
    db.session.commit()

    with caplog.at_level(logging.INFO):
        assert read(client, principal(user)).status_code == 200

    lines = [r.getMessage() for r in caplog.records
             if "mobile_progress" in r.getMessage()]
    assert lines == [
        "mobile_progress event=summary_read trajectory=on_track body=available "
        f"request_id={lines[0].rsplit('=', 1)[1]}"]
    for private in ("78.4", "75.0", "3450", user.username, "Bearer"):
        assert private not in lines[0]
