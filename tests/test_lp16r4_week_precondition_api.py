"""LP16-R4 (D3): the native check-in week precondition.

``Check-In-Week-Start: <Monday>`` on ``POST /api/v1/progress/check-ins``
refuses a FRESH write whose own server instant falls in another
Europe/Istanbul Monday–Sunday week (409 ``CHECKIN_WEEK_CHANGED``) and leaves
every committed key's replay exactly as before. Absent header → the LP16-B
behaviour, byte for byte.

    python -m pytest tests/test_lp16r4_week_precondition_api.py -q
"""
import json
import logging
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import User, UserSession, WeeklyCheckIn
from app.services import mobile_auth, mobile_weekly_checkin
from app.timeutil import APP_TZ, UTC, audit_clock

PATH = "/api/v1/progress/check-ins"
HEADER = "Check-In-Week-Start"
BODY = {"weight_kg": 78.4, "training_intensity": 4, "fatigue": 2,
        "sleep_quality": 5, "nutrition_adherence": 4,
        "progressive_overload": "yes"}
OTHER_BODY = {**BODY, "fatigue": 3}

# Week W = Mon 2026-07-20 .. Sun 2026-07-26; week W+1 starts Mon 2026-07-27.
WEEK = "2026-07-20"
NEXT_WEEK = "2026-07-27"
THURSDAY = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
LAST_INSTANT = datetime(2026, 7, 26, 23, 59, 59, 999999, tzinfo=APP_TZ)
# Monday 00:00 Istanbul is still Sunday 21:00 UTC: a UTC week rule would call
# this instant week W. The canonical rule is Istanbul, so it is W+1.
MIDNIGHT = datetime(2026, 7, 27, 0, 0, tzinfo=APP_TZ)
MONDAY_LATER = datetime(2026, 7, 27, 9, 30, tzinfo=APP_TZ)


@pytest.fixture
def principal(monkeypatch):
    current = {}

    def authenticate(raw):
        user = db.session.get(User, current["user_id"])
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def as_user(user, key, week=None):
        current["user_id"] = user.id
        headers = {"Authorization": "Bearer opaque-r4",
                   "Idempotency-Key": key}
        if week is not None:
            headers[HEADER] = week
        return headers
    return as_user


@pytest.fixture
def athlete(make_user):
    def _make(name):
        user = make_user(name, weight=80.0, height=180.0, age=30,
                         gender="male", goal="kilo verme",
                         current_activity="active", target_weight=72.0)
        db.session.add(UserSession(user_id=user.id, weight=80.0, bmr=1.0,
                                   tdee=2.0, target_calories=3.0))
        db.session.commit()
        return user
    return _make


def post(client, headers, now, body=BODY):
    with audit_clock(now):
        return client.post(PATH, headers=headers, json=body)


def _rows(user_id):
    return (WeeklyCheckIn.query.filter_by(user_id=user_id)
            .order_by(WeeklyCheckIn.id).all())


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    return body["error"]


def _effects(user_id):
    db.session.expire_all()
    owner = db.session.get(User, user_id)
    session = UserSession.query.filter_by(user_id=user_id).one()
    return (owner.weight, session.weight, session.bmr, session.tdee,
            session.target_calories, len(_rows(user_id)))


# -- Same week: unchanged ------------------------------------------------------
def test_matching_week_writes_exactly_as_without_the_header(
        client, athlete, principal):
    plain, guarded = athlete("r4-plain"), athlete("r4-guarded")
    a = post(client, principal(plain, "r4-plain-0001"), THURSDAY)
    b = post(client, principal(guarded, "r4-guard-0001", WEEK), THURSDAY)

    assert (a.status_code, b.status_code) == (201, 201)
    assert a.get_json() == b.get_json()
    [row_a], [row_b] = _rows(plain.id), _rows(guarded.id)
    # The precondition is not part of the intent: same fingerprint.
    assert row_a.request_fingerprint == row_b.request_fingerprint


def test_last_instant_of_the_week_still_matches(client, athlete, principal):
    user = athlete("r4-last")
    response = post(client, principal(user, "r4-last-00001", WEEK),
                    LAST_INSTANT)

    assert response.status_code == 201
    assert response.get_json()["analysis_day"] == "2026-07-26"


# -- Week changed: refused, nothing durable ------------------------------------
def test_stale_week_fresh_write_is_409_and_writes_nothing(
        client, athlete, principal):
    user = athlete("r4-stale")
    before = _effects(user.id)
    response = post(client, principal(user, "r4-stale-0001", WEEK),
                    MONDAY_LATER)

    assert response.status_code == 409
    error = _error(response)
    assert error["code"] == "CHECKIN_WEEK_CHANGED"
    assert error["retryable"] is False
    assert _effects(user.id) == before


def test_istanbul_midnight_not_utc_decides_the_week(client, athlete, principal):
    """Monday 00:00 Istanbul (Sunday 21:00 UTC) already belongs to W+1."""
    assert MIDNIGHT.astimezone(UTC).date() == date(2026, 7, 26)
    user = athlete("r4-midnight")

    stale = post(client, principal(user, "r4-midn-0001", WEEK), MIDNIGHT)
    fresh = post(client, principal(user, "r4-midn-0002", NEXT_WEEK), MIDNIGHT)

    assert stale.status_code == 409
    assert _error(stale)["code"] == "CHECKIN_WEEK_CHANGED"
    assert fresh.status_code == 201
    assert fresh.get_json()["analysis_day"] == "2026-07-27"
    assert len(_rows(user.id)) == 1


def test_a_future_week_is_refused_too(client, athlete, principal):
    user = athlete("r4-future")
    response = post(client, principal(user, "r4-future-001", NEXT_WEEK),
                    THURSDAY)
    assert response.status_code == 409
    assert _rows(user.id) == []


def test_a_refused_week_holds_no_lock_and_consumes_nothing(
        client, athlete, principal):
    user = athlete("r4-release")
    assert post(client, principal(user, "r4-release-01", WEEK),
                MONDAY_LATER).status_code == 409
    # The same owner can write at once (the owner lock was released) ...
    ok = post(client, principal(user, "r4-release-02", NEXT_WEEK),
              MONDAY_LATER)
    assert ok.status_code == 201
    # ... and the refused key was never stored.
    assert [r.idempotency_key for r in _rows(user.id)] == ["r4-release-02"]


def test_the_refused_key_keeps_refusing_while_the_week_differs(
        client, athlete, principal):
    """Terminal for the client: the same key + body + header never writes."""
    user = athlete("r4-terminal")
    headers = principal(user, "r4-terminal-1", WEEK)
    for now in (MIDNIGHT, MONDAY_LATER, datetime(2026, 8, 2, 23, 0,
                                                 tzinfo=APP_TZ)):
        assert post(client, headers, now).status_code == 409
    assert _rows(user.id) == []


# -- Committed intents replay unchanged across the boundary --------------------
def test_committed_intent_replays_after_the_boundary_with_its_own_week(
        client, athlete, principal):
    user = athlete("r4-replay")
    headers = principal(user, "r4-replay-0001", WEEK)
    first = post(client, headers, LAST_INSTANT)
    after = _effects(user.id)
    replay = post(client, headers, MONDAY_LATER)

    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.get_json() == first.get_json()
    assert replay.get_json()["analysis_day"] == "2026-07-26"
    assert _effects(user.id) == after               # no second effect


def test_unguarded_committed_key_replays_even_under_a_stale_header(
        client, athlete, principal):
    """A key committed before R4 (no header) still replays: REPLAYED wins."""
    user = athlete("r4-legacy")
    first = post(client, principal(user, "r4-legacy-001"), THURSDAY)
    replay = post(client, principal(user, "r4-legacy-001", WEEK), MONDAY_LATER)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.get_json() == first.get_json()


def test_conflict_still_outranks_the_week_check(client, athlete, principal):
    user = athlete("r4-conflict")
    post(client, principal(user, "r4-conflict-1", WEEK), THURSDAY)
    response = post(client, principal(user, "r4-conflict-1", WEEK),
                    MONDAY_LATER, body=OTHER_BODY)
    assert response.status_code == 409
    assert _error(response)["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(_rows(user.id)) == 1


# -- Ownership -----------------------------------------------------------------
def test_another_owner_never_replays_or_moves_a_committed_intent(
        client, athlete, principal):
    a, b = athlete("r4-owner-a"), athlete("r4-owner-b")
    first = post(client, principal(a, "r4-shared-key", WEEK), LAST_INSTANT)
    a_after = _effects(a.id)
    b_before = _effects(b.id)

    stale = post(client, principal(b, "r4-shared-key", WEEK), MONDAY_LATER)

    assert first.status_code == 201
    # B is not answered with A's snapshot (no replay across owners) and the
    # stale week is refused for B's own FRESH attempt.
    assert stale.status_code == 409
    assert _error(stale)["code"] == "CHECKIN_WEEK_CHANGED"
    assert _effects(a.id) == a_after
    assert _effects(b.id) == b_before


# -- Malformed precondition ----------------------------------------------------
@pytest.mark.parametrize("value", [
    "", "2026-07-21", "2026-7-20", "20260720", "2026-07-20T00:00:00",
    "2026-02-30", " 2026-07-20", "next", "2026-07-20,2026-07-27",
])
def test_malformed_precondition_is_400_and_writes_nothing(
        client, athlete, principal, value):
    user = athlete("r4-bad")
    response = post(client, principal(user, "r4-bad-000001", value), THURSDAY)
    assert response.status_code == 400
    assert _error(response)["code"] == "CHECKIN_INVALID_REQUEST"
    assert _rows(user.id) == []


def test_parse_week_precondition_is_strict_and_total():
    parse = mobile_weekly_checkin.parse_week_precondition
    assert parse(None) is None
    assert parse(WEEK) == date(2026, 7, 20)
    for bad in ("2026-07-26", "2026-13-01", "x", 20260720):
        with pytest.raises(mobile_weekly_checkin.InvalidRequest):
            parse(bad)


def test_week_start_is_the_history_week(client, athlete, principal):
    """The guard and GET current_week share one rule."""
    user = athlete("r4-history")
    for now, expected in ((THURSDAY, WEEK), (LAST_INSTANT, WEEK),
                          (MIDNIGHT, NEXT_WEEK)):
        with audit_clock(now):
            response = client.get(PATH, headers=principal(user, "unused-key"))
        assert response.get_json()["current_week"]["start_day"] == expected


# -- Privacy -------------------------------------------------------------------
def test_week_changed_log_is_a_fixed_event_without_values(
        client, athlete, principal, caplog):
    user = athlete("r4-log")
    with caplog.at_level(logging.INFO):
        post(client, principal(user, "r4-log-secret", WEEK), MONDAY_LATER)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "mobile_checkin event=write outcome=week_changed request_id=" in text
    for secret in ("r4-log-secret", "78.4", WEEK, user.username,
                   json.dumps(BODY)):
        assert secret not in text
