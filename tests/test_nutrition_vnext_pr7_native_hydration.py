"""NUTR-PR7 — native hydration over the ONE WaterLog row per Istanbul day.

    python -m pytest tests/test_nutrition_vnext_pr7_native_hydration.py -q

confirmed zero != unavailable · explicit unit · server day · absolute desired
state under If-Match · duplicate / lost response / stale screen / two devices
never double count and never silently overwrite.
"""
from datetime import datetime

import pytest

from app.models import WaterLog
from app.services import hydration as hydration_service
from app.timeutil import APP_TZ, app_today, audit_clock

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    StatementCounter, bearer, error_of, native, no_provider, quoted, set_water,
)

PATH = "/api/v1/nutrition/hydration"


def read(native, headers):
    response = native.get(PATH, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["hydration"]


def put(native, headers, revision, amount):
    hdrs = dict(headers)
    if revision is not None:
        hdrs["If-Match"] = quoted(revision)
    return native.put(PATH, headers=hdrs, json={"amount": amount})


def stored(user_id):
    return {r.date_key: r.count for r in WaterLog.query.filter_by(user_id=user_id)}


def test_no_row_is_a_confirmed_zero_with_explicit_unit(app, native, bearer, make_user, no_provider):
    user = make_user("water-zero")
    body = read(native, bearer(user))
    assert body["state"] == "empty" and body["amount"] == 0
    assert body["unit"] == "glass" and body["max_amount"] == 8
    assert body["day"] == app_today().isoformat() and body["timezone"] == "Europe/Istanbul"
    assert body["revision"]


def test_read_failure_is_unavailable_not_zero(app, native, bearer, make_user, monkeypatch):
    user = make_user("water-503")
    monkeypatch.setattr(hydration_service, "read_today",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("db")))
    response = native.get(PATH, headers=bearer(user))
    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "HYDRATION_UNAVAILABLE" and error["retryable"] is True
    assert "amount" not in response.get_data(as_text=True)


def test_absolute_set_then_read_converges(app, native, bearer, make_user, no_provider):
    user = make_user("water-set")
    headers = bearer(user)
    revision = read(native, headers)["revision"]
    response = put(native, headers, revision, 3)
    assert response.status_code == 200
    body = response.get_json()["hydration"]
    assert body["amount"] == 3 and body["state"] == "available"
    assert read(native, headers) == body
    assert stored(user.id) == {app_today().isoformat(): 3}


def test_duplicate_tap_and_lost_response_never_double_count(
        app, native, bearer, make_user, no_provider):
    user = make_user("water-dup")
    headers = bearer(user)
    revision = read(native, headers)["revision"]
    assert put(native, headers, revision, 2).status_code == 200
    # The same request again (duplicate tap / blind resend after a lost
    # response): refused, and the persisted absolute value is unchanged.
    again = put(native, headers, revision, 2)
    assert again.status_code == 412
    assert error_of(again)["code"] == "STALE_HYDRATION"
    assert error_of(again)["retryable"] is False
    # Recovery rule: fresh read, compare with the desired state.
    assert read(native, headers)["amount"] == 2
    assert stored(user.id) == {app_today().isoformat(): 2}


def test_two_devices_from_one_state_cannot_both_succeed(app, native, bearer, make_user, no_provider):
    user = make_user("water-two")
    headers = bearer(user)
    set_water(user.id, app_today().isoformat(), 1)
    shared = read(native, headers)["revision"]
    assert put(native, headers, shared, 2).status_code == 200
    assert put(native, headers, shared, 5).status_code == 412
    assert read(native, headers)["amount"] == 2


def test_a_screen_from_yesterday_cannot_write_today(app, native, bearer, make_user, no_provider):
    user = make_user("water-day")
    headers = bearer(user)
    with audit_clock(datetime(2026, 8, 9, 23, 50, tzinfo=APP_TZ)):
        yesterday = read(native, headers)["revision"]
    with audit_clock(datetime(2026, 8, 10, 0, 10, tzinfo=APP_TZ)):
        response = put(native, headers, yesterday, 4)
        assert response.status_code == 412
        assert read(native, headers)["day"] == "2026-08-10"
    assert stored(user.id) == {}


@pytest.mark.parametrize("body", [{"amount": 9}, {"amount": -1}, {"amount": 1.5},
                                  {"amount": True}, {"amount": "3"}, {"count": 3},
                                  {"amount": 3, "user_id": 1}, {"amount": 3, "revision": "x"},
                                  {"delta": 1}, []])
def test_command_is_closed_and_bounded(app, native, bearer, make_user, no_provider, body):
    user = make_user("water-bad")
    headers = bearer(user)
    revision = read(native, headers)["revision"]
    response = native.put(PATH, headers=dict(headers, **{"If-Match": quoted(revision)}),
                          json=body)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_HYDRATION_COMMAND"
    assert stored(user.id) == {}


def test_precondition_is_required(app, native, bearer, make_user, no_provider):
    user = make_user("water-428")
    response = put(native, bearer(user), None, 1)
    assert response.status_code == 428
    assert stored(user.id) == {}


def test_native_write_fires_the_same_daily_funnel_once(app, native, bearer, make_user, no_provider):
    user = make_user("water-quest")
    headers = bearer(user)
    rev = read(native, headers)["revision"]
    rev = put(native, headers, rev, 1).get_json()["hydration"]["revision"]
    rev = put(native, headers, rev, 0).get_json()["hydration"]["revision"]
    put(native, headers, rev, 2)
    row = WaterLog.query.filter_by(user_id=user.id).one()
    assert row.quest_fired is True


def test_web_and_native_share_one_row(app, client, native, bearer, make_user, login, no_provider):
    user = make_user("water-parity")
    login("water-parity")
    assert client.post("/water", json={"count": 4}).get_json()["count"] == 4
    headers = bearer(user)
    body = read(native, headers)
    assert body["amount"] == 4
    put(native, headers, body["revision"], 6)
    assert client.get("/water").get_json()["count"] == 6
    assert WaterLog.query.filter_by(user_id=user.id).count() == 1


def test_owner_isolation_for_hydration(app, native, bearer, make_user, no_provider):
    alice = make_user("water-alice")
    bob = make_user("water-bob")
    set_water(alice.id, app_today().isoformat(), 5)
    alice_rev = read(native, bearer(alice))["revision"]
    assert read(native, bearer(bob))["amount"] == 0
    # Alice's revision never authorises a write to Bob's row (both read 5 vs 0).
    assert put(native, bearer(bob), alice_rev, 7).status_code == 412
    assert stored(alice.id) == {app_today().isoformat(): 5}
    assert stored(bob.id) == {}


def test_hydration_read_is_one_select(app, native, bearer, make_user, no_provider):
    user = make_user("water-q")
    headers = bearer(user)
    with StatementCounter() as counter:
        read(native, headers)
    assert len([s for s in counter.selects() if "water_log" in s]) == 1
    assert counter.writes() == []


def test_write_failure_is_never_reported_as_confirmed(
        app, native, bearer, make_user, monkeypatch, no_provider):
    """N7-25 guard: an ambiguous/failed write is a retryable 503, never a value."""
    user = make_user("water-fail")
    headers = bearer(user)
    revision = read(native, headers)["revision"]
    monkeypatch.setattr(hydration_service, "set_today_count",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("commit lost")))
    response = put(native, headers, revision, 4)
    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "HYDRATION_UNAVAILABLE" and error["retryable"] is True
    assert "hydration" not in response.get_json()
