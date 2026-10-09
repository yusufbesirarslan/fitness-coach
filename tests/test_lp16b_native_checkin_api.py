"""LP16-B native weekly check-in contract (POST/GET /api/v1/progress/check-ins).

Real persistence, real routes; only the Bearer principal resolution is
stubbed (`principal`) except in the isolation tests, which run through the
real opaque-credential pipeline (`real_bearer`).

    python -m pytest tests/test_lp16b_native_checkin_api.py -q
"""
import calendar
import hashlib
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.extensions import db, limiter
from app.models import (NutritionPlan, TrainingPlan, User, UserSession,
                        WeeklyCheckIn)
from app.services import cognito_jwt, cognito_service, mobile_auth
from app.services import mobile_weekly_checkin
from app.services.calculations import (calculate_bmr, calculate_target,
                                       calculate_tdee)
from app.timeutil import APP_TZ, audit_clock

PATH = "/api/v1/progress/check-ins"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
# Thursday 15:00 Istanbul (12:00 UTC); week = Mon 2026-07-20 .. Sun 2026-07-26.
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
BODY = {"weight_kg": 78.4, "training_intensity": 4, "fatigue": 2,
        "sleep_quality": 5, "nutrition_adherence": 4,
        "progressive_overload": "yes"}
CREATED_BODY = {
    "contract_version": 1,
    "checked_in_at": "2026-07-23T15:00:00+03:00",
    "analysis_day": "2026-07-23",
    "weight_kg": 78.4,
    "training_intensity": 4,
    "fatigue": 2,
    "sleep_quality": 5,
    "nutrition_adherence": 4,
    "progressive_overload": "yes",
}
POST_KEYS = set(CREATED_BODY)
ITEM_KEYS = {"checked_in_at", "analysis_day", "weight_kg", "weight_delta_kg",
             "training_intensity", "fatigue", "sleep_quality",
             "nutrition_adherence", "progressive_overload"}


# -- Principals ----------------------------------------------------------------
@pytest.fixture
def principal(monkeypatch):
    current = {}

    def authenticate(raw):
        user = db.session.get(User, current["user_id"])
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def as_user(user, key=None):
        current["user_id"] = user.id
        headers = {"Authorization": "Bearer opaque-checkin-access"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers
    return as_user


@pytest.fixture
def real_bearer(monkeypatch):
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

    def issue(user, key=None):
        subs[user.username] = user.cognito_sub
        issued = mobile_auth.login(user.username, "Sifre123")
        headers = {"Authorization": f"Bearer {issued.access_credential}"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers
    return issue


@pytest.fixture
def athlete(make_user):
    """Complete profile + canonical session with sentinel derived targets."""
    def _make(name):
        user = make_user(name, weight=80.0, height=180.0, age=30,
                         gender="male", goal="kilo verme",
                         current_activity="active", target_weight=72.0)
        db.session.add(UserSession(user_id=user.id, weight=80.0, bmr=1.0,
                                   tdee=2.0, target_calories=3.0))
        db.session.commit()
        return user
    return _make


def post(client, headers, body=BODY, now=FIXED_NOW, **kwargs):
    if "data" not in kwargs:
        kwargs["json"] = body
    with audit_clock(now):
        return client.post(PATH, headers=headers, **kwargs)


def get(client, headers, now=FIXED_NOW, path=PATH):
    with audit_clock(now):
        return client.get(path, headers=headers)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert ENVELOPE_KEYS <= set(body["error"])
    assert body["error"]["request_id"]
    return body["error"]


def _rows(user_id):
    return (WeeklyCheckIn.query.filter_by(user_id=user_id)
            .order_by(WeeklyCheckIn.id).all())


def _expected_targets(weight):
    bmr = calculate_bmr(weight, 180.0, 30, "male")
    tdee = calculate_tdee(bmr, "active")
    return bmr, tdee, calculate_target(tdee, "kilo verme")


def _seed(user_id, at, weight, *, yogunluk=3, fatigue=3, uyku=3, beslenme=3,
          overload="kismen"):
    row = WeeklyCheckIn(user_id=user_id, weight=weight, yogunluk=yogunluk,
                        fatigue=fatigue, uyku_kalitesi=uyku,
                        beslenme_uyumu=beslenme, progressive_overload=overload,
                        created_at=at)
    db.session.add(row)
    return row


# =============================================================================
# POST — write, replay, conflict
# =============================================================================
def test_new_check_in_is_201_with_the_literal_closed_body(
        client, athlete, principal):
    user = athlete("cin-new")
    response = post(client, principal(user, "lp16b-new-0001"))

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == CREATED_BODY


def test_write_owns_row_user_weight_and_derived_session_only(
        client, athlete, principal):
    user = athlete("cin-effect")
    plans = (TrainingPlan.query.count(), NutritionPlan.query.count())
    post(client, principal(user, "lp16b-effect-01"))

    db.session.expire_all()
    [row] = _rows(user.id)
    assert (row.weight, row.yogunluk, row.fatigue, row.uyku_kalitesi,
            row.beslenme_uyumu) == (78.4, 4, 2, 5, 4)
    assert row.progressive_overload == "evet"       # stored legacy token
    assert row.note is None and row.coach_feedback is None
    assert row.created_at == datetime(2026, 7, 23, 12, 0)   # server-owned
    assert row.idempotency_key == "lp16b-effect-01"
    assert row.request_fingerprint == mobile_weekly_checkin.fingerprint(
        mobile_weekly_checkin.parse_request(
            json.dumps(BODY).encode(), is_json=True))
    owner = db.session.get(User, user.id)
    assert owner.weight == 78.4
    assert owner.target_weight == 72.0             # never moved
    session = UserSession.query.filter_by(user_id=user.id).one()
    bmr, tdee, target = _expected_targets(78.4)
    assert (session.weight, session.bmr, session.tdee,
            session.target_calories) == (78.4, bmr, tdee, target)
    assert (TrainingPlan.query.count(), NutritionPlan.query.count()) == plans


def test_incomplete_profile_moves_weight_but_not_derived_targets(
        client, make_user, principal):
    user = make_user("cin-partial", weight=80.0)    # no height/age/goal
    db.session.add(UserSession(user_id=user.id, weight=80.0, bmr=1.0,
                               tdee=2.0, target_calories=3.0))
    db.session.commit()

    assert post(client, principal(user, "lp16b-partial-1")).status_code == 201
    db.session.expire_all()
    assert db.session.get(User, user.id).weight == 78.4
    session = UserSession.query.filter_by(user_id=user.id).one()
    assert (session.weight, session.bmr, session.tdee,
            session.target_calories) == (78.4, 1.0, 2.0, 3.0)


def test_replay_is_200_same_body_and_no_second_effect(
        client, athlete, principal):
    user = athlete("cin-replay")
    headers = principal(user, "lp16b-replay-01")
    first = post(client, headers)
    # Another authority moves the weight meanwhile; a replay must not undo it.
    db.session.get(User, user.id).weight = 90.0
    db.session.commit()

    later = FIXED_NOW + timedelta(days=2)
    second = post(client, headers, now=later)

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.get_json() == first.get_json() == CREATED_BODY
    assert second.headers["Cache-Control"] == "no-store"
    db.session.expire_all()
    assert len(_rows(user.id)) == 1
    assert db.session.get(User, user.id).weight == 90.0


def test_same_key_different_intent_is_409_and_writes_nothing(
        client, athlete, principal):
    user = athlete("cin-conflict")
    headers = principal(user, "lp16b-conflict-1")
    assert post(client, headers).status_code == 201

    response = post(client, headers, body={**BODY, "fatigue": 3})

    assert response.status_code == 409
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("IDEMPOTENCY_CONFLICT", False)
    db.session.expire_all()
    assert len(_rows(user.id)) == 1
    assert db.session.get(User, user.id).weight == 78.4


def test_key_order_and_numeric_spelling_are_the_same_intent(
        client, athlete, principal):
    user = athlete("cin-canon")
    headers = principal(user, "lp16b-canon-001")
    assert post(client, headers, body={**BODY, "weight_kg": 78}).status_code == 201
    reordered = json.dumps(dict(reversed(list(
        {**BODY, "weight_kg": 78.0}.items())))).encode()
    response = post(client, {**headers, "Content-Type": "application/json"},
                    data=reordered)
    assert response.status_code == 200
    assert len(_rows(user.id)) == 1


def test_several_check_ins_a_day_and_a_week_are_all_legitimate(
        client, athlete, principal):
    """No weekly/daily uniqueness: distinct keys are distinct observations."""
    user = athlete("cin-many")
    first = post(client, principal(user, "lp16b-many-0001"))
    second = post(client, principal(user, "lp16b-many-0002"),
                  body={**BODY, "weight_kg": 77.9},
                  now=FIXED_NOW + timedelta(minutes=5))
    third = post(client, principal(user, "lp16b-many-0003"),
                 body={**BODY, "weight_kg": 77.5},
                 now=FIXED_NOW + timedelta(days=1))

    assert [r.status_code for r in (first, second, third)] == [201, 201, 201]
    db.session.expire_all()
    assert [r.weight for r in _rows(user.id)] == [78.4, 77.9, 77.5]
    # The latest observation is the current weight authority.
    assert db.session.get(User, user.id).weight == 77.5
    week = get(client, principal(user)).get_json()["current_week"]
    assert week["submitted"] is True and "can_submit" not in week


@pytest.mark.parametrize("key", [None, "", "short", "x" * 65, "bad key!!",
                                 "ключ-12345678"])
def test_missing_or_invalid_idempotency_key_is_400(
        client, athlete, principal, key):
    user = athlete("cin-key")
    headers = principal(user)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = post(client, headers)

    assert response.status_code == 400
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("INVALID_IDEMPOTENCY_KEY", False)
    assert _rows(user.id) == []


# =============================================================================
# POST — strict native parser (never the legacy web parser)
# =============================================================================
INVALID_REQUESTS = {
    "missing_metric": {k: v for k, v in BODY.items() if k != "fatigue"},
    "unknown_key_user_id": {**BODY, "user_id": 999},
    "unknown_key_note": {**BODY, "note": "hi"},
    "unknown_key_coach_feedback": {**BODY, "coach_feedback": "x"},
    "unknown_key_checked_in_at": {**BODY, "checked_in_at": "2026-01-01"},
    "legacy_web_field_names": {"weight": 78.4, "yogunluk": 4, "fatigue": 2,
                               "uyku_kalitesi": 5, "beslenme_uyumu": 4,
                               "progressive_overload": "evet"},
    "bool_rating": {**BODY, "fatigue": True},
    "bool_weight": {**BODY, "weight_kg": True},
    "string_rating": {**BODY, "sleep_quality": "3"},
    "float_rating": {**BODY, "training_intensity": 3.0},
    "null_rating": {**BODY, "nutrition_adherence": None},
    "string_weight": {**BODY, "weight_kg": "78.4"},
    "null_weight": {**BODY, "weight_kg": None},
    "overload_not_string": {**BODY, "progressive_overload": 1},
    "list_body": [BODY],
    "empty_object": {},
}


@pytest.mark.parametrize("name", sorted(INVALID_REQUESTS))
def test_malformed_request_is_400_and_writes_nothing(
        client, athlete, principal, name):
    user = athlete(f"cin-bad-{name}"[:30])
    response = post(client, principal(user, "lp16b-bad-00001"),
                    body=INVALID_REQUESTS[name])

    assert response.status_code == 400
    error = _error(response)
    assert set(error) == ENVELOPE_KEYS
    assert (error["code"], error["retryable"]) == ("CHECKIN_INVALID_REQUEST", False)
    assert _rows(user.id) == []
    assert db.session.get(User, user.id).weight == 80.0


RAW_INVALID = {
    "nan": b'{"weight_kg": NaN, "training_intensity": 4, "fatigue": 2, '
           b'"sleep_quality": 5, "nutrition_adherence": 4, '
           b'"progressive_overload": "yes"}',
    "infinity": b'{"weight_kg": Infinity, "training_intensity": 4, '
                b'"fatigue": 2, "sleep_quality": 5, "nutrition_adherence": 4, '
                b'"progressive_overload": "yes"}',
    "duplicate_key": b'{"weight_kg": 78, "weight_kg": 79, '
                     b'"training_intensity": 4, "fatigue": 2, '
                     b'"sleep_quality": 5, "nutrition_adherence": 4, '
                     b'"progressive_overload": "yes"}',
    "not_json": b"weight_kg=78",
    "empty": b"",
    "huge_int": b'{"weight_kg": 1' + b"0" * 5000 + b', "training_intensity": 4,'
                b' "fatigue": 2, "sleep_quality": 5, "nutrition_adherence": 4,'
                b' "progressive_overload": "yes"}',
}


@pytest.mark.parametrize("name", sorted(RAW_INVALID))
def test_non_standard_or_unparseable_json_is_400(
        client, athlete, principal, name):
    user = athlete(f"cin-raw-{name}")
    headers = {**principal(user, "lp16b-raw-000001"),
               "Content-Type": "application/json"}
    response = post(client, headers, data=RAW_INVALID[name])

    assert response.status_code == 400
    assert _error(response)["code"] == "CHECKIN_INVALID_REQUEST"
    assert _rows(user.id) == []


def test_non_json_content_type_is_400(client, athlete, principal):
    user = athlete("cin-ctype")
    headers = {**principal(user, "lp16b-ctype-0001"), "Content-Type": "text/plain"}
    response = post(client, headers, data=json.dumps(BODY).encode())
    assert response.status_code == 400
    assert _error(response)["code"] == "CHECKIN_INVALID_REQUEST"


INVALID_VALUES = [
    ({"weight_kg": 19.99}, "weight_kg", "out_of_range"),
    ({"weight_kg": 500.01}, "weight_kg", "out_of_range"),
    ({"weight_kg": 0}, "weight_kg", "out_of_range"),
    ({"weight_kg": -78}, "weight_kg", "out_of_range"),
    ({"weight_kg": 1e300}, "weight_kg", "out_of_range"),
    ({"training_intensity": 0}, "training_intensity", "out_of_range"),
    ({"training_intensity": 6}, "training_intensity", "out_of_range"),
    ({"fatigue": -1}, "fatigue", "out_of_range"),
    ({"sleep_quality": 99}, "sleep_quality", "out_of_range"),
    ({"nutrition_adherence": 0}, "nutrition_adherence", "out_of_range"),
    ({"progressive_overload": "evet"}, "progressive_overload", "unsupported_value"),
    ({"progressive_overload": "kismen"}, "progressive_overload", "unsupported_value"),
    ({"progressive_overload": "Yes"}, "progressive_overload", "unsupported_value"),
    ({"progressive_overload": ""}, "progressive_overload", "unsupported_value"),
]


@pytest.mark.parametrize("patch,field,reason", INVALID_VALUES)
def test_out_of_contract_value_is_422_with_bounded_metadata(
        client, athlete, principal, patch, field, reason):
    user = athlete("cin-value")
    response = post(client, principal(user, "lp16b-value-0001"),
                    body={**BODY, **patch})

    assert response.status_code == 422
    error = _error(response)
    assert set(error) == ENVELOPE_KEYS | {"field", "reason"}
    assert (error["code"], error["retryable"]) == ("CHECKIN_INVALID_VALUE", False)
    assert (error["field"], error["reason"]) == (field, reason)
    assert _rows(user.id) == []
    assert db.session.get(User, user.id).weight == 80.0


def test_overflowing_exponent_weight_is_422_never_500(client, athlete, principal):
    user = athlete("cin-overflow")
    raw = json.dumps(BODY).replace("78.4", "1e400").encode()
    response = post(client, {**principal(user, "lp16b-ovf-000001"),
                             "Content-Type": "application/json"}, data=raw)
    assert response.status_code == 422
    assert _error(response)["field"] == "weight_kg"


@pytest.mark.parametrize("wire,stored", [("yes", "evet"), ("partial", "kismen"),
                                         ("no", "hayir")])
def test_wire_overload_tokens_map_to_the_stored_legacy_tokens(
        client, athlete, principal, wire, stored):
    user = athlete(f"cin-ov-{wire}")
    response = post(client, principal(user, f"lp16b-ov-{wire}-0001"),
                    body={**BODY, "progressive_overload": wire})
    assert response.status_code == 201
    assert response.get_json()["progressive_overload"] == wire
    assert _rows(user.id)[0].progressive_overload == stored


@pytest.mark.parametrize("weight", [20, 20.0, 500, 500.0, 63.25])
def test_weight_bounds_are_inclusive(client, athlete, principal, weight):
    user = athlete("cin-bounds")
    response = post(client, principal(user, "lp16b-bounds-001"),
                    body={**BODY, "weight_kg": weight})
    assert response.status_code == 201
    assert response.get_json()["weight_kg"] == float(weight)


def test_response_exposes_no_internal_state(client, athlete, principal):
    user = athlete("cin-leak")
    key = "lp16b-leak-00001"
    response = post(client, principal(user, key))
    body = response.get_json()
    raw = response.get_data(as_text=True)
    row = _rows(user.id)[0]

    assert set(body) == POST_KEYS
    for secret in (key, row.request_fingerprint, "evet", user.username,
                   user.cognito_sub, '"id"', "user_id"):
        assert secret not in raw


# =============================================================================
# POST — idempotency domain, failures, providers, privacy, rate limit
# =============================================================================
def test_native_fingerprint_is_pinned_under_the_native_v1_domain():
    command = mobile_weekly_checkin.parse_request(
        json.dumps(BODY).encode(), is_json=True)
    preimage = ('{"domain":"native.v1","fatigue":2,"nutrition_adherence":4,'
                '"progressive_overload":"yes","sleep_quality":5,'
                '"training_intensity":4,"weight_kg":78.4}')
    assert mobile_weekly_checkin.FINGERPRINT_DOMAIN == "native.v1"
    assert mobile_weekly_checkin.fingerprint(command) == hashlib.sha256(
        preimage.encode()).hexdigest() == (
        "fce3978f3397d189e5545502886f07907163327fd0adb447c2951bc490ca15d7")


def test_a_web_keyed_check_in_never_replays_as_native_and_vice_versa(
        client, athlete, principal, login, monkeypatch):
    from app.blueprints import tracking

    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *a, **k: "fb")
    user = athlete("cin-cross")
    login(user.username)
    web = client.post("/checkin", json={
        "weight": 78.4, "yogunluk": 4, "fatigue": 2, "uyku_kalitesi": 5,
        "beslenme_uyumu": 4, "progressive_overload": "evet", "note": ""},
        headers={"Idempotency-Key": "lp16b-cross-web1"})
    assert web.status_code == 200

    native = post(client, principal(user, "lp16b-cross-web1"))
    assert native.status_code == 409
    assert _error(native)["code"] == "IDEMPOTENCY_CONFLICT"

    assert post(client, principal(user, "lp16b-cross-nat1")).status_code == 201
    back = client.post("/checkin", json={
        "weight": 78.4, "yogunluk": 4, "fatigue": 2, "uyku_kalitesi": 5,
        "beslenme_uyumu": 4, "progressive_overload": "evet", "note": ""},
        headers={"Idempotency-Key": "lp16b-cross-nat1"})
    assert back.status_code == 409
    assert len(_rows(user.id)) == 2


def test_failure_during_derived_session_update_leaves_nothing_durable(
        client, athlete, principal, monkeypatch):
    from app.services.weekly_checkin import service as checkin_service

    real_target = checkin_service.calculate_target
    armed = {"on": True}

    def _faulty(*args):
        if armed["on"]:
            raise RuntimeError("derived target fault")
        return real_target(*args)

    monkeypatch.setattr(checkin_service, "calculate_target", _faulty)
    user = athlete("cin-fault")
    response = post(client, principal(user, "lp16b-fault-0001"))

    assert response.status_code == 503
    error = _error(response)
    assert (error["code"], error["retryable"]) == (
        "CHECKIN_TEMPORARILY_UNAVAILABLE", True)
    db.session.expire_all()
    assert _rows(user.id) == []
    assert db.session.get(User, user.id).weight == 80.0
    assert UserSession.query.filter_by(user_id=user.id).one().target_calories == 3.0

    armed["on"] = False   # the failed attempt consumed nothing: same key works
    retried = post(client, principal(user, "lp16b-fault-0001"))
    assert retried.status_code == 201


def test_write_and_read_never_touch_a_provider(client, athlete, principal,
                                               monkeypatch):
    from app import extensions
    from app.blueprints import tracking

    class _Detonator:
        def __getattr__(self, name):
            raise AssertionError(f"provider client used ({name})")

    def _no_feedback(*_a, **_k):
        raise AssertionError("AI feedback called")

    monkeypatch.setattr(extensions, "openai_client", _Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", _Detonator())
    monkeypatch.setattr(tracking, "generate_checkin_feedback", _no_feedback)
    user = athlete("cin-noai")
    assert post(client, principal(user, "lp16b-noai-00001")).status_code == 201
    assert get(client, principal(user)).status_code == 200


def test_logs_carry_no_values_keys_or_identity(client, athlete, principal,
                                               caplog, monkeypatch):
    from app.services.weekly_checkin import service as checkin_service

    user = athlete("cin-logsecret")
    key = "lp16b-logsecret1"
    # Application loggers only ("app" and its children): SQLAlchemy's own
    # statement echo is not an application log line.
    caplog.set_level(logging.DEBUG, logger="app")
    post(client, principal(user, key))
    post(client, principal(user, key))
    post(client, principal(user, key), body={**BODY, "fatigue": 1})
    get(client, principal(user))
    monkeypatch.setattr(checkin_service, "calculate_target",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("78.4")))
    post(client, principal(user, "lp16b-logsecret2"))

    text = "\n".join(r.getMessage() for r in caplog.records)
    fingerprint = _rows(user.id)[0].request_fingerprint
    for secret in (key, "lp16b-logsecret2", fingerprint, "78.4", user.username,
                   user.email, user.cognito_sub, "evet"):
        assert secret not in text
    assert "mobile_checkin event=write outcome=created" in text
    assert "mobile_checkin event=write outcome=replayed" in text
    assert "mobile_checkin event=write outcome=conflict" in text
    assert "mobile_checkin event=write_failed error_type=RuntimeError" in text


@pytest.fixture
def enabled_limiter():
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


def test_write_rate_limit_is_a_typed_per_owner_429(
        client, athlete, principal, enabled_limiter, monkeypatch):
    from app.blueprints import mobile_weekly_checkin as transport

    assert transport.CHECKIN_WRITE_RATELIMIT == "10 per minute; 60 per hour"
    monkeypatch.setattr(transport, "CHECKIN_WRITE_RATELIMIT", "2 per minute")
    alice, bob = athlete("cin-rl-a"), athlete("cin-rl-b")

    codes = [post(client, principal(alice, f"lp16b-rl-{i:06d}")).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]
    response = post(client, principal(alice, "lp16b-rl-999999"))
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("CHECKIN_RATE_LIMITED", True)
    assert int(response.headers["Retry-After"]) > 0
    assert len(_rows(alice.id)) == 2
    assert post(client, principal(bob, "lp16b-rl-bob001")).status_code == 201
    # Reads are not charged against the write ceiling.
    assert get(client, principal(alice)).status_code == 200


def test_unauthenticated_requests_are_refused(client, athlete):
    athlete("cin-anon")
    assert client.post(PATH, json=BODY,
                       headers={"Idempotency-Key": "lp16b-anon-00001"}
                       ).status_code == 401
    assert client.get(PATH).status_code == 401
    assert WeeklyCheckIn.query.count() == 0


# =============================================================================
# GET — history + current week
# =============================================================================
def test_empty_history_is_the_literal_empty_contract(client, athlete, principal):
    user = athlete("cin-empty")
    response = get(client, principal(user))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "contract_version": 1,
        "current_week": {"timezone": "Europe/Istanbul",
                         "start_day": "2026-07-20", "end_day": "2026-07-26",
                         "submitted": False},
        "check_ins": [],
    }


def test_history_literal_excludes_weight_only_rows_and_nulls_legacy_values(
        client, athlete, principal):
    user = athlete("cin-hist")
    _seed(user.id, datetime(2026, 7, 9, 9), 79.4, overload="hayir")
    _seed(user.id, datetime(2026, 7, 16, 9), 79.0, yogunluk=7, fatigue=None,
          uyku=0, beslenme=3, overload="bilinmiyor")
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=99.0,
                                 created_at=datetime(2026, 7, 22, 9)))
    _seed(user.id, datetime(2026, 7, 23, 6), 78.9)
    _seed(user.id, datetime(2026, 7, 23, 9), 78.4, yogunluk=4, fatigue=2,
          uyku=5, beslenme=4, overload="evet")
    db.session.commit()

    response = get(client, principal(user))

    assert response.status_code == 200
    assert response.get_json() == {
        "contract_version": 1,
        "current_week": {"timezone": "Europe/Istanbul",
                         "start_day": "2026-07-20", "end_day": "2026-07-26",
                         "submitted": True},
        "check_ins": [
            {"checked_in_at": "2026-07-23T12:00:00+03:00",
             "analysis_day": "2026-07-23", "weight_kg": 78.4,
             "weight_delta_kg": -0.6, "training_intensity": 4, "fatigue": 2,
             "sleep_quality": 5, "nutrition_adherence": 4,
             "progressive_overload": "yes"},
            # Same Istanbul day: never each other's prior point.
            {"checked_in_at": "2026-07-23T09:00:00+03:00",
             "analysis_day": "2026-07-23", "weight_kg": 78.9,
             "weight_delta_kg": -0.1, "training_intensity": 3, "fatigue": 3,
             "sleep_quality": 3, "nutrition_adherence": 3,
             "progressive_overload": "partial"},
            # Legacy out-of-contract values read back as null, never 0 or 3.
            {"checked_in_at": "2026-07-16T12:00:00+03:00",
             "analysis_day": "2026-07-16", "weight_kg": 79.0,
             "weight_delta_kg": -0.4, "training_intensity": None,
             "fatigue": None, "sleep_quality": None, "nutrition_adherence": 3,
             "progressive_overload": None},
            {"checked_in_at": "2026-07-09T12:00:00+03:00",
             "analysis_day": "2026-07-09", "weight_kg": 79.4,
             "weight_delta_kg": None, "training_intensity": 3, "fatigue": 3,
             "sleep_quality": 3, "nutrition_adherence": 3,
             "progressive_overload": "no"},
        ],
    }


def test_history_is_the_last_twelve_newest_first_with_deterministic_ties(
        client, athlete, principal):
    user = athlete("cin-twelve")
    base = datetime(2026, 5, 1, 9)
    for day in range(14):
        _seed(user.id, base + timedelta(days=day), 70.0 + day)
    # Two rows on the newest timestamp: id breaks the tie (newest insert first).
    tie = base + timedelta(days=20)
    _seed(user.id, tie, 90.0, fatigue=1)
    _seed(user.id, tie, 91.0, fatigue=2)
    db.session.commit()

    items = get(client, principal(user)).get_json()["check_ins"]

    assert len(items) == 12
    assert all(set(item) == ITEM_KEYS for item in items)
    assert [i["weight_kg"] for i in items[:3]] == [91.0, 90.0, 83.0]
    assert [i["fatigue"] for i in items[:2]] == [2, 1]
    stamps = [i["checked_in_at"] for i in items]
    assert stamps == sorted(stamps, reverse=True)


def test_delta_of_the_oldest_item_is_resolved_past_the_window(
        client, athlete, principal):
    """Row 12 and row 13 share a day: the prior point lies beyond the read."""
    user = athlete("cin-cut")
    _seed(user.id, datetime(2026, 6, 1, 9), 70.0)          # prior day point
    for minute in range(2):                                 # rows 12-13, same day
        _seed(user.id, datetime(2026, 6, 2, 9, minute), 71.0 + minute)
    for day in range(11):                                   # rows 1-11
        _seed(user.id, datetime(2026, 6, 10 + day, 9), 80.0 + day)
    db.session.commit()

    items = get(client, principal(user)).get_json()["check_ins"]

    assert len(items) == 12
    oldest = items[-1]
    assert (oldest["analysis_day"], oldest["weight_kg"]) == ("2026-06-02", 72.0)
    assert oldest["weight_delta_kg"] == 2.0


def test_delta_matches_progress_history_for_every_shared_row(
        client, athlete, principal):
    from app.services.progress_history import build_progress_history

    user = athlete("cin-parity")
    weights = (80.2, 79.6, 79.9, 79.1, 78.8, 78.5)
    for index, weight in enumerate(weights):
        day = datetime(2026, 7, 1, 9) + timedelta(days=3 * index)
        _seed(user.id, day, weight)
        _seed(user.id, day + timedelta(hours=2), weight - 0.3)
    db.session.commit()

    native = get(client, principal(user)).get_json()["check_ins"]
    web = build_progress_history(user.id).entries

    assert len(native) == len(web) == 12
    assert [i["weight_delta_kg"] for i in native] == [
        e.weight_delta_kg for e in web]
    assert [i["weight_kg"] for i in native] == [e.weight_kg for e in web]
    assert [i["analysis_day"] for i in native] == [
        e.analysis_day.isoformat() for e in web]


@pytest.mark.parametrize("now,rows,submitted,start", [
    # Sunday 23:30 Istanbul = Sunday 20:30 UTC: still the week of Mon 20th.
    (datetime(2026, 7, 26, 23, 30, tzinfo=APP_TZ),
     [datetime(2026, 7, 19, 21, 30)], True, "2026-07-20"),   # Mon 00:30 IST
    # Monday 00:30 Istanbul is a NEW week even though it is Sunday in UTC.
    (datetime(2026, 7, 27, 0, 30, tzinfo=APP_TZ),
     [datetime(2026, 7, 26, 20, 0)], False, "2026-07-27"),   # Sun 23:00 IST
    (datetime(2026, 7, 27, 0, 30, tzinfo=APP_TZ),
     [datetime(2026, 7, 26, 21, 15)], True, "2026-07-27"),   # Mon 00:15 IST
])
def test_current_week_is_monday_to_sunday_in_istanbul(
        client, athlete, principal, now, rows, submitted, start):
    user = athlete("cin-week")
    for at in rows:
        _seed(user.id, at, 79.0)
    db.session.commit()

    week = get(client, principal(user), now=now).get_json()["current_week"]
    assert week["start_day"] == start
    assert week["submitted"] is submitted
    assert week["timezone"] == "Europe/Istanbul"


def test_weight_only_row_never_marks_the_week_submitted(
        client, athlete, principal):
    user = athlete("cin-weekonly")
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=79.0,
                                 created_at=datetime(2026, 7, 21, 9)))
    _seed(user.id, datetime(2026, 7, 19, 9), 79.5)          # last week, full
    db.session.commit()

    body = get(client, principal(user)).get_json()
    assert body["current_week"]["submitted"] is False
    assert [i["analysis_day"] for i in body["check_ins"]] == ["2026-07-19"]


def test_history_ignores_query_parameters(client, athlete, principal):
    alice, bob = athlete("cin-qa"), athlete("cin-qb")
    _seed(bob.id, datetime(2026, 7, 22, 9), 66.0)
    db.session.commit()
    response = get(client, principal(alice),
                   path=f"{PATH}?user_id={bob.id}&limit=50&owner={bob.id}")
    assert response.get_json()["check_ins"] == []


def test_history_read_failure_is_a_typed_retryable_503(
        client, athlete, principal, monkeypatch):
    from app.services.mobile_weekly_checkin import history

    def _boom(*_a, **_k):
        raise RuntimeError("storage")

    monkeypatch.setattr(history, "fetch_qualifying_checkins", _boom)
    user = athlete("cin-readfail")
    response = get(client, principal(user))

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    error = _error(response)
    assert (error["code"], error["retryable"]) == (
        "CHECKIN_TEMPORARILY_UNAVAILABLE", True)


def test_post_then_get_round_trip(client, athlete, principal):
    user = athlete("cin-trip")
    created = post(client, principal(user, "lp16b-trip-00001")).get_json()
    [item] = get(client, principal(user)).get_json()["check_ins"]
    assert {k: item[k] for k in POST_KEYS - {"contract_version"}} == {
        k: created[k] for k in POST_KEYS - {"contract_version"}}
    assert item["weight_delta_kg"] is None


# =============================================================================
# Account isolation (real opaque credentials)
# =============================================================================
def test_owners_are_isolated_for_reads_writes_and_keys(
        client, athlete, real_bearer):
    alice, bob = athlete("cin-iso-a"), athlete("cin-iso-b")
    shared = "lp16b-shared-key1"

    a = post(client, real_bearer(alice, shared))
    # Same textual key, different owner, different body: independent, 201.
    b = post(client, real_bearer(bob, shared), body={**BODY, "weight_kg": 66.0})
    # Bob reusing the key with ALICE's body: still Bob's own replay, not hers.
    b_again = post(client, real_bearer(bob, shared))

    assert (a.status_code, b.status_code) == (201, 201)
    assert b_again.status_code == 409          # Bob's key, Bob's other intent
    assert a.get_json()["weight_kg"] == 78.4
    assert b.get_json()["weight_kg"] == 66.0
    db.session.expire_all()
    assert [r.weight for r in _rows(alice.id)] == [78.4]
    assert [r.weight for r in _rows(bob.id)] == [66.0]
    assert db.session.get(User, alice.id).weight == 78.4
    assert db.session.get(User, bob.id).weight == 66.0

    a_items = get(client, real_bearer(alice)).get_json()["check_ins"]
    b_items = get(client, real_bearer(bob)).get_json()["check_ins"]
    assert [i["weight_kg"] for i in a_items] == [78.4]
    assert [i["weight_kg"] for i in b_items] == [66.0]


def test_no_foreign_existence_oracle(client, athlete, real_bearer):
    """A key Alice used tells Bob nothing: his answers equal a fresh key's."""
    alice, bob = athlete("cin-oracle-a"), athlete("cin-oracle-b")
    post(client, real_bearer(alice, "lp16b-oracle-used"))

    used = post(client, real_bearer(bob, "lp16b-oracle-used"))
    fresh = post(client, real_bearer(bob, "lp16b-oracle-new1"),
                 now=FIXED_NOW)
    assert used.status_code == fresh.status_code == 201
    assert used.get_json() == fresh.get_json()
