"""Native profile read contract (LP-12 prefill of the LP-03 write).

    GET /api/v1/account/profile   200  {"profile": {...}}

Exact key set = the PUT body's wire names; values are the canonical `User`
columns (never the `UserSession` copy), as stored, with `null` for nothing
usable; owner = Bearer principal only (a query string is ignored); no write;
a read fault is a retryable PROFILE_* error, never an auth-shaped one; no
value is logged. The startup gate (`MOBILE_AUTH_ENABLED` off -> 404) is
pinned with every other mobile route in tests/test_mobile_auth_feature_gate.py.

    python -m pytest tests/test_mobile_account_profile_read_api.py -v
"""
import calendar
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import User, UserSession
from app.services import account_profile, cognito_jwt, cognito_service, mobile_auth

PATH = "/api/v1/account/profile"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
KEYS = {
    "weight_kg", "height_cm", "age", "gender", "goal", "fitness_level",
    "activity_level", "target_weight_kg"}
ALL_NULL = dict.fromkeys(KEYS)
VALID = {
    "weight_kg": 80.5, "height_cm": 180.0, "age": 30, "gender": "male",
    "goal": "lose_weight", "fitness_level": "beginner",
    "activity_level": "active", "target_weight_kg": 75.0,
}


@pytest.fixture
def principal(monkeypatch):
    """Bearer principal stub: whichever user was last selected."""
    current = {}

    def authenticate(raw):
        user = current["user"]
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def as_user(user):
        current["user"] = user
        return {"Authorization": "Bearer opaque-profile-read-access"}
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


@pytest.fixture
def native(make_user):
    return make_user("native-read")


def _get(client, headers, query=""):
    return client.get(PATH + query, headers=headers)


def _profile(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    # Strict JSON: a NaN/Infinity literal anywhere fails here.
    body = json.loads(response.get_data(as_text=True),
                      parse_constant=lambda literal: pytest.fail(literal))
    assert set(body) == {"profile"}
    assert set(body["profile"]) == KEYS
    return body["profile"]


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


def _store(user, **columns):
    for key, value in columns.items():
        setattr(user, key, value)
    db.session.commit()


def _snapshot(user_id):
    db.session.expire_all()
    user = db.session.get(User, user_id)
    return ({column.name: getattr(user, column.name)
             for column in User.__table__.columns},
            UserSession.query.filter_by(user_id=user_id).count())


# ---------------------------------------------------------------------------
# Shape and null semantics
# ---------------------------------------------------------------------------

def test_fresh_account_answers_every_key_as_null(raw_client, native, principal):
    response = _get(raw_client, principal(native))

    assert _profile(response) == ALL_NULL
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers


def test_stored_profile_is_answered_under_the_write_wire_names(
        raw_client, native, principal):
    _store(native, weight=80.5, height=180.0, age=30, gender="male",
           goal="kas kazanma", fitness_level="advanced",
           current_activity="very_active", target_weight=85.0)

    assert _profile(_get(raw_client, principal(native))) == {
        "weight_kg": 80.5, "height_cm": 180.0, "age": 30, "gender": "male",
        "goal": "build_muscle", "fitness_level": "advanced",
        "activity_level": "very_active", "target_weight_kg": 85.0,
    }


def test_json_types_are_numbers_integers_and_tokens(raw_client, native, principal):
    _store(native, weight=80, height=180, age=30, gender="female",
           goal="kilo verme", fitness_level="beginner",
           current_activity="sedentary", target_weight=70)

    profile = _profile(_get(raw_client, principal(native)))

    for key in ("weight_kg", "height_cm", "target_weight_kg"):
        assert isinstance(profile[key], float), key
    assert type(profile["age"]) is int
    for key in ("gender", "goal", "fitness_level", "activity_level"):
        assert isinstance(profile[key], str), key


def test_never_set_target_weight_is_null(raw_client, native, principal):
    _store(native, weight=80.0, height=180.0, age=30, gender="male",
           goal="kilo verme", fitness_level="beginner",
           current_activity="active", target_weight=None)

    profile = _profile(_get(raw_client, principal(native)))

    assert profile["target_weight_kg"] is None
    assert profile["weight_kg"] == 80.0


def test_goal_on_the_wire_is_the_token_never_the_stored_literal(
        raw_client, native, principal):
    _store(native, goal="kilo verme")

    response = _get(raw_client, principal(native))

    assert _profile(response)["goal"] == "lose_weight"
    assert "kilo verme" not in response.get_data(as_text=True)


@pytest.mark.parametrize("column,value,key", [
    ("gender", "erkek", "gender"),
    ("gender", "MALE", "gender"),
    ("goal", "lose_weight", "goal"),        # a token stored raw is not canonical
    ("goal", "kilo almak", "goal"),
    ("fitness_level", "orta", "fitness_level"),
    ("current_activity", "moderate", "activity_level"),
])
def test_tokens_outside_the_vocabulary_are_null(
        raw_client, native, principal, column, value, key):
    _store(native, **{column: value})

    response = _get(raw_client, principal(native))

    assert _profile(response)[key] is None
    assert value not in response.get_data(as_text=True)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("column,key", [
    ("weight", "weight_kg"), ("height", "height_cm"),
    ("target_weight", "target_weight_kg"),
])
def test_non_finite_stored_numbers_are_null_and_the_body_stays_json(
        raw_client, native, principal, column, key, value):
    # Reachable for target weight: the web profile edit stores float(text)
    # with no range or finiteness check.
    _store(native, **{column: value})

    assert _profile(_get(raw_client, principal(native)))[key] is None


def test_legacy_finite_values_outside_write_ranges_are_answered_as_stored(
        raw_client, native, principal):
    # Truth over tidiness: a client must correct these before writing back.
    _store(native, weight=12.5, height=0.0, age=0, target_weight=900.0)

    profile = _profile(_get(raw_client, principal(native)))

    assert (profile["weight_kg"], profile["height_cm"], profile["age"],
            profile["target_weight_kg"]) == (12.5, 0.0, 0, 900.0)


# ---------------------------------------------------------------------------
# Canonical source
# ---------------------------------------------------------------------------

def test_put_then_get_answers_the_saved_canonical_values(
        raw_client, native, principal):
    headers = principal(native)
    assert raw_client.put(PATH, json=VALID, headers=headers).status_code == 200

    assert _profile(_get(raw_client, headers)) == VALID


def test_complete_projection_sent_back_unchanged_is_accepted(
        raw_client, native, principal):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)
    projection = _profile(_get(raw_client, headers))

    again = raw_client.put(PATH, json=projection, headers=headers)

    assert again.status_code == 200
    assert _profile(_get(raw_client, headers)) == projection


def test_put_with_null_target_keeps_the_stored_target(
        raw_client, native, principal):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)

    raw_client.put(PATH, json={**VALID, "target_weight_kg": None},
                   headers=headers)

    assert _profile(_get(raw_client, headers))["target_weight_kg"] == 75.0


def test_user_columns_win_over_a_diverged_user_session(
        raw_client, native, principal):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)
    session = UserSession.query.filter_by(user_id=native.id).one()
    session.weight, session.height, session.age = 999.0, 999.0, 99
    session.goal, session.fitness_level = "kas kazanma", "advanced"
    session.current_activity = "very_active"
    db.session.commit()

    assert _profile(_get(raw_client, headers)) == VALID


def test_weight_log_update_is_reflected(
        raw_client, client, native, principal, login):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)
    login("native-read")

    response = client.post("/update-weight", json={"weight": 78.2})

    assert response.status_code == 200, response.get_data(as_text=True)
    assert _profile(_get(raw_client, headers))["weight_kg"] == 78.2


def test_web_profile_edit_of_goal_and_target_is_reflected(
        raw_client, client, native, principal, login):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)
    login("native-read")

    response = client.post("/edit-profile", json={
        "full_name": "", "goal": "kas kazanma", "target_weight": 82})

    assert response.status_code == 200, response.get_data(as_text=True)
    profile = _profile(_get(raw_client, headers))
    assert (profile["goal"], profile["target_weight_kg"]) == ("build_muscle", 82.0)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

def test_a_with_user_id_of_b_still_answers_a_only(
        raw_client, make_user, real_bearer):
    a = make_user("read-owner-a", weight=70.0, gender="female",
                  goal="kilo verme", target_weight=65.0)
    b = make_user("read-owner-b", weight=95.0, gender="male",
                  goal="kas kazanma", target_weight=100.0)
    a_headers, b_headers = real_bearer(a), real_bearer(b)

    for query in (f"?user_id={b.id}", f"?username={b.username}",
                  f"?owner_id={b.id}&account_id={b.id}", f"?email={b.email}"):
        profile = _profile(_get(raw_client, a_headers, query))
        assert (profile["weight_kg"], profile["gender"], profile["goal"],
                profile["target_weight_kg"]) == (
            70.0, "female", "lose_weight", 65.0), query

    profile = _profile(_get(raw_client, b_headers, f"?user_id={a.id}"))
    assert (profile["weight_kg"], profile["gender"], profile["goal"],
            profile["target_weight_kg"]) == (95.0, "male", "build_muscle", 100.0)


def test_response_carries_no_identifier(raw_client, make_user, real_bearer):
    user = make_user("read-no-ids", weight=70.0)

    response = _get(raw_client, real_bearer(user))

    text = response.get_data(as_text=True)
    for identifier in (user.username, user.email, user.cognito_sub,
                       '"user_id"', '"profile_complete"', '"id"'):
        assert identifier not in text


def test_requires_bearer_and_browser_cookie_cannot_authorize(
        raw_client, client, native, login):
    missing = raw_client.get(PATH)
    assert missing.status_code == 401
    assert _error(missing)["code"].startswith("AUTH_")

    invalid = raw_client.get(PATH, headers={"Authorization": "Bearer nope"})
    assert invalid.status_code == 401
    assert _error(invalid)["code"].startswith("AUTH_")

    login("native-read")
    assert client.get(PATH).status_code == 401


# ---------------------------------------------------------------------------
# Read-only and failure
# ---------------------------------------------------------------------------

def test_read_writes_nothing(raw_client, native, principal):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)
    before = _snapshot(native.id)
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        for _ in range(3):
            _profile(_get(raw_client, headers))
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert not set(statements) & {"INSERT", "UPDATE", "DELETE"}
    assert _snapshot(native.id) == before


@pytest.fixture
def failing_read(monkeypatch):
    def fail(user):
        raise RuntimeError("storage exploded with weight 80.5")
    monkeypatch.setattr(account_profile, "current_profile", fail)


def test_read_fault_is_a_retryable_profile_error_not_an_auth_one(
        raw_client, native, principal, failing_read, caplog):
    with caplog.at_level(logging.INFO):
        response = _get(raw_client, principal(native))

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    error = _error(response)
    assert error["code"] == "PROFILE_TEMPORARILY_UNAVAILABLE"
    assert error["retryable"] is True
    assert not error["code"].startswith("AUTH_")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=profile_read_failed error_type=RuntimeError" in logged
    assert "80.5" not in logged and "exploded" not in logged


def test_success_logs_no_profile_value(raw_client, native, principal, caplog):
    headers = principal(native)
    raw_client.put(PATH, json=VALID, headers=headers)
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        _profile(_get(raw_client, headers))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for value in ("80.5", "75.0", "lose_weight", "beginner", "native-read"):
        assert value not in logged
