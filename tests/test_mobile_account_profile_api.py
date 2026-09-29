"""Native onboarding profile HTTP contract (LP-03).

    PUT /api/v1/account/profile

Wire contract (exact body = the `GET /api/v1/account/me` projection, ADR 0001
envelope), strict request parsing, locale-independent tokens, owner taken only
from the Bearer principal, fail-closed authority fields, atomic failure,
idempotent repeat, account isolation and the sensitive-logging boundary.

    python -m pytest tests/test_mobile_account_profile_api.py -v
"""
import json
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import User, UserSession
from app.services import mobile_auth

PATH = "/api/v1/account/profile"
ME = "/api/v1/account/me"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
BODY = {
    "weight_kg": 80, "height_cm": 180, "age": 30, "gender": "male",
    "goal": "lose_weight", "fitness_level": "beginner",
    "activity_level": "active",
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
        return {"Authorization": "Bearer opaque-profile-access"}
    return as_user


@pytest.fixture
def native(make_user):
    return make_user("native-profile")


def _put(client, headers, body=BODY, **kwargs):
    return client.put(PATH, json=body, headers=headers, **kwargs)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


def _sessions(user_id):
    db.session.expire_all()
    return UserSession.query.filter_by(user_id=user_id).all()


def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _assert_untouched(user_id):
    user = _fresh(user_id)
    assert user.profile_complete is not True
    assert (user.weight, user.height, user.age, user.goal) == (None, None, None, None)
    assert _sessions(user_id) == []


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------

def test_success_returns_the_account_projection_and_flips_completeness(
        raw_client, native, principal):
    headers = principal(native)
    before = raw_client.get(ME, headers=headers)
    assert before.json["user"]["profile_complete"] is False
    assert before.json["user"]["goal"] is None

    response = _put(raw_client, headers)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert response.get_json() == {"user": {
        "username": "native-profile",
        "display_name": "native-profile",
        "profile_complete": True,
        "preferred_language": "tr",
        "goal": "lose_weight",
        "goal_type": "loss",
    }}
    after = raw_client.get(ME, headers=headers)
    assert after.get_json() == response.get_json()


def test_success_persists_canonical_domain_values(raw_client, native, principal):
    _put(raw_client, principal(native), {**BODY, "goal": "build_muscle",
                                         "target_weight_kg": 85.5})
    user = _fresh(native.id)
    assert (user.weight, user.height, user.age, user.gender) == (80.0, 180.0, 30, "male")
    assert (user.goal, user.goal_type) == ("kas kazanma", "gain")
    assert (user.fitness_level, user.current_activity) == ("beginner", "active")
    assert user.target_weight == 85.5
    [session] = _sessions(native.id)
    assert (session.goal, session.current_activity) == ("kas kazanma", "active")
    assert session.target_calories == pytest.approx(1780 * 1.55 + 300)


@pytest.mark.parametrize("target", [None, "<absent>"])
def test_optional_target_weight_absent_or_null_keeps_stored_value(
        raw_client, make_user, principal, target):
    user = make_user("target-keep", target_weight=66.0)
    body = dict(BODY)
    if target is None:
        body["target_weight_kg"] = None
    assert _put(raw_client, principal(user), body).status_code == 200
    assert _fresh(user.id).target_weight == 66.0


def test_goal_on_the_wire_is_never_the_stored_literal(raw_client, native, principal):
    headers = principal(native)
    for goal in ("lose_weight", "build_muscle"):
        text = _put(raw_client, headers, {**BODY, "goal": goal}).get_data(as_text=True)
        assert "kilo" not in text and "kas kazanma" not in text
        assert "kilo" not in raw_client.get(ME, headers=headers).get_data(as_text=True)


def test_response_exposes_no_identifiers_or_submitted_health_values(
        raw_client, native, principal):
    text = _put(raw_client, principal(native),
                {**BODY, "weight_kg": 81.25}).get_data(as_text=True)
    for forbidden in ("user_id", "email", "sub-", '"id"', "81.25", "cognito"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_repeat_submission_is_idempotent(raw_client, native, principal):
    headers = principal(native)
    first = _put(raw_client, headers)
    [session] = _sessions(native.id)
    second = _put(raw_client, headers)
    assert second.status_code == 200
    assert second.get_json() == first.get_json()
    assert [row.id for row in _sessions(native.id)] == [session.id]


def test_revised_submission_updates_the_same_session(raw_client, native, principal):
    headers = principal(native)
    _put(raw_client, headers)
    [session] = _sessions(native.id)
    revised = _put(raw_client, headers, {**BODY, "weight_kg": 72,
                                         "goal": "build_muscle"})
    assert revised.json["user"]["goal"] == "build_muscle"
    [updated] = _sessions(native.id)
    assert updated.id == session.id
    assert (updated.weight, updated.goal) == (72.0, "kas kazanma")


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------

def test_requires_bearer_and_browser_cookie_cannot_authorize(raw_client, client, native):
    missing = raw_client.put(PATH, json=BODY)
    with client.session_transaction() as session:
        session["_user_id"] = str(native.id)
        session["_fresh"] = True
    cookie = client.put(PATH, json=BODY)
    for response in (missing, cookie):
        assert response.status_code == 401
        assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    _assert_untouched(native.id)


@pytest.mark.parametrize("field", [
    "user_id", "account_id", "owner_id", "email", "username",
    "profile_complete", "id", "cognito_sub"])
def test_authority_and_server_authored_fields_are_refused(
        raw_client, native, make_user, principal, field):
    victim = make_user("victim")
    value = {"user_id": victim.id, "account_id": victim.id, "owner_id": victim.id,
             "email": victim.email, "username": victim.username,
             "profile_complete": True, "id": victim.id,
             "cognito_sub": victim.cognito_sub}[field]
    response = _put(raw_client, principal(native), {**BODY, field: value})
    assert response.status_code == 400
    assert _error(response)["code"] == "PROFILE_INVALID_REQUEST"
    _assert_untouched(native.id)
    _assert_untouched(victim.id)


def test_owner_is_the_bearer_principal_only(raw_client, make_user, principal):
    alice = make_user("alice-profile")
    bob = make_user("bob-profile")
    _put(raw_client, principal(alice))
    _assert_untouched(bob.id)
    assert raw_client.get(ME, headers=principal(bob)).json["user"][
        "profile_complete"] is False

    _put(raw_client, principal(bob), {**BODY, "weight_kg": 95,
                                      "goal": "build_muscle"})
    assert (_fresh(alice.id).weight, _fresh(alice.id).goal) == (80.0, "kilo verme")
    assert len(_sessions(alice.id)) == len(_sessions(bob.id)) == 1
    assert _sessions(alice.id)[0].id != _sessions(bob.id)[0].id


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", sorted(BODY))
def test_every_required_field_is_required(raw_client, native, principal, field):
    body = {k: v for k, v in BODY.items() if k != field}
    response = _put(raw_client, principal(native), body)
    assert response.status_code == 400
    assert _error(response)["code"] == "PROFILE_INVALID_REQUEST"
    _assert_untouched(native.id)


@pytest.mark.parametrize("body", ["<none>", "not json", "[]", "null", '"text"', "7"])
def test_non_object_bodies_are_invalid_requests(raw_client, native, principal, body):
    headers = principal(native)
    if body == "<none>":
        response = raw_client.put(PATH, headers=headers)
    else:
        response = raw_client.put(PATH, data=body, headers=headers,
                                  content_type="application/json")
    assert response.status_code == 400
    assert _error(response)["code"] == "PROFILE_INVALID_REQUEST"
    _assert_untouched(native.id)


def test_unknown_key_is_refused_not_ignored(raw_client, native, principal):
    response = _put(raw_client, principal(native), {**BODY, "weight": 80})
    assert response.status_code == 400
    _assert_untouched(native.id)


@pytest.mark.parametrize(("field", "value"), [
    ("weight_kg", "80"), ("weight_kg", None), ("weight_kg", True),
    ("height_cm", "180"), ("height_cm", [180]),
    ("age", 30.0), ("age", 30.5), ("age", "30"), ("age", None), ("age", False),
    ("gender", None), ("gender", 1), ("goal", None), ("goal", ["lose_weight"]),
    ("fitness_level", {}), ("activity_level", None), ("target_weight_kg", "75"),
])
def test_wrong_json_types_are_invalid_requests(raw_client, native, principal, field, value):
    response = _put(raw_client, principal(native), {**BODY, field: value})
    assert response.status_code == 400
    assert _error(response)["code"] == "PROFILE_INVALID_REQUEST"
    _assert_untouched(native.id)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_numbers_are_invalid_requests(raw_client, native, principal, literal):
    raw = json.dumps(BODY).replace('"weight_kg": 80', f'"weight_kg": {literal}')
    response = raw_client.put(PATH, data=raw, headers=principal(native),
                              content_type="application/json")
    assert response.status_code == 400
    _assert_untouched(native.id)


@pytest.mark.parametrize(("field", "value"), [
    ("goal", "kilo verme"), ("goal", "kas kazanma"), ("goal", "maintain"),
    ("goal", "Lose_Weight"), ("gender", "other"), ("fitness_level", "olympian"),
    ("activity_level", "very active"), ("activity_level", "aktif"),
])
def test_unsupported_tokens_fail_closed(raw_client, native, principal, field, value):
    response = _put(raw_client, principal(native), {**BODY, field: value})
    assert response.status_code == 422
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("PROFILE_INVALID_VALUE", False)
    assert value not in error["message"]
    _assert_untouched(native.id)


@pytest.mark.parametrize(("field", "value"), [
    ("weight_kg", 19.9), ("weight_kg", 500.5), ("weight_kg", -80),
    ("target_weight_kg", 10), ("target_weight_kg", 600),
    ("height_cm", 0), ("height_cm", -170), ("age", 0), ("age", -1),
])
def test_values_outside_canonical_rules_are_refused(
        raw_client, native, principal, field, value):
    response = _put(raw_client, principal(native), {**BODY, field: value})
    assert response.status_code == 422
    assert _error(response)["code"] == "PROFILE_INVALID_VALUE"
    _assert_untouched(native.id)


def test_oversized_body_uses_the_mobile_envelope(app, raw_client, native, principal):
    app.config["MAX_CONTENT_LENGTH"] = 256
    response = _put(raw_client, principal(native), {**BODY, "pad": "x" * 1024})
    assert response.status_code == 413
    assert _error(response)["code"] == "REQUEST_TOO_LARGE"
    _assert_untouched(native.id)


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------

@pytest.fixture
def refuse_session_rows():
    def listener(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.new | session.dirty):
            raise RuntimeError("injected persistence failure weight=80 sub-secret")

    event.listen(db.session, "before_flush", listener)
    yield
    event.remove(db.session, "before_flush", listener)


def test_storage_failure_is_retryable_profile_error_and_rolls_back(
        raw_client, native, principal, refuse_session_rows, caplog):
    headers = principal(native)
    with caplog.at_level(logging.INFO):
        response = _put(raw_client, headers, {**BODY, "weight_kg": 83.75,
                                              "target_weight_kg": 71.5})
    assert response.status_code == 503
    error = _error(response)
    assert (error["code"], error["retryable"]) == (
        "PROFILE_TEMPORARILY_UNAVAILABLE", True)
    text = response.get_data(as_text=True)
    assert "injected" not in text and "RuntimeError" not in text
    _assert_untouched(native.id)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=profile_write_failed error_type=RuntimeError" in logged
    for secret in ("83.75", "71.5", "lose_weight", "kilo verme", "injected",
                   "native-profile", "sub-secret"):
        assert secret not in logged


def test_failed_write_then_retry_succeeds_exactly_once(
        raw_client, native, principal):
    headers = principal(native)

    def refuse(session, _ctx, _instances):
        if any(isinstance(o, UserSession) for o in session.new):
            raise RuntimeError("injected")

    event.listen(db.session, "before_flush", refuse)
    try:
        assert _put(raw_client, headers).status_code == 503
    finally:
        event.remove(db.session, "before_flush", refuse)
    assert raw_client.get(ME, headers=headers).json["user"]["profile_complete"] is False

    assert _put(raw_client, headers).status_code == 200
    assert len(_sessions(native.id)) == 1


def test_success_logs_nothing_about_the_profile(raw_client, native, principal, caplog):
    with caplog.at_level(logging.DEBUG):
        _put(raw_client, principal(native), {**BODY, "weight_kg": 83.75})
    for record in caplog.records:
        message = record.getMessage()
        assert "83.75" not in message
        assert "lose_weight" not in message
