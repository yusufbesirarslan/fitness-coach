"""NUTR-PR7 — bounded native MealLog history.

    python -m pytest tests/test_nutrition_vnext_pr7_native_history.py -q

Whole Istanbul days, newest first, bounded pages, opaque owner-bound cursor,
empty != failure, no raw ids, today's entries identical to the diary.
"""
from datetime import date, datetime, timedelta

import pytest

from app.services.nutrition_native import history
from app.timeutil import app_today

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    StatementCounter, add_meal, bearer, error_of, native, no_provider,
)

PATH = "/api/v1/nutrition/history"


def page(native, headers, **query):
    return native.get(PATH, headers=headers, query_string=query)


def seed_days(user_id, count, start=None, per_day=2):
    start = start or app_today()
    days = []
    for offset in range(count):
        key = (start - timedelta(days=offset)).isoformat()
        for n in range(per_day):
            add_meal(user_id, key, kcal=100 + n, created_at=datetime(2026, 8, 1, 6, n))
        days.append(key)
    return days


def test_empty_history_is_a_success_not_a_failure(app, native, bearer, make_user, no_provider):
    user = make_user("hist-empty")
    response = page(native, bearer(user))
    assert response.status_code == 200
    body = response.get_json()
    assert body == {"contract_version": 1, "timezone": "Europe/Istanbul",
                    "state": "empty", "days": [], "next_cursor": None}


def test_read_failure_is_a_typed_503(app, native, bearer, make_user, monkeypatch):
    user = make_user("hist-503")
    monkeypatch.setattr(history, "read_page",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db")))
    response = page(native, bearer(user))
    assert response.status_code == 503
    assert error_of(response)["code"] == "NUTRITION_HISTORY_UNAVAILABLE"
    assert error_of(response)["retryable"] is True


def test_default_page_is_seven_whole_days_newest_first(app, native, bearer, make_user, no_provider):
    user = make_user("hist-default")
    days = seed_days(user.id, 9)
    body = page(native, bearer(user)).get_json()
    assert [d["date"] for d in body["days"]] == days[:7]
    assert all(len(d["meals"]) == 2 for d in body["days"])
    assert body["days"][0]["totals"]["energy_kcal"] == 201.0
    assert body["next_cursor"]


def test_cursor_walks_without_gaps_or_overlap(app, native, bearer, make_user, no_provider):
    user = make_user("hist-walk")
    days = seed_days(user.id, 9, per_day=1)
    headers = bearer(user)
    first = page(native, headers, limit="4").get_json()
    second = page(native, headers, limit="4", cursor=first["next_cursor"]).get_json()
    third = page(native, headers, limit="4", cursor=second["next_cursor"]).get_json()
    seen = [d["date"] for p in (first, second, third) for d in p["days"]]
    assert seen == days
    assert third["next_cursor"] is None


@pytest.mark.parametrize("limit", ["0", "15", "-1", "abc", "1e2", "7.0"])
def test_page_size_is_bounded(app, native, bearer, make_user, no_provider, limit):
    user = make_user("hist-limit")
    response = page(native, bearer(user), limit=limit)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_HISTORY_LIMIT"


def test_max_page_is_fourteen_days(app, native, bearer, make_user, no_provider):
    user = make_user("hist-max")
    seed_days(user.id, 20, per_day=1)
    body = page(native, bearer(user), limit="14").get_json()
    assert len(body["days"]) == 14 and body["next_cursor"]


@pytest.mark.parametrize("cursor", ["", "garbage", "a.b", "x" * 300,
                                    "eyJiZWZvcmUiOiIyMDI2LTAxLTAxIn0.AAAA"])
def test_malformed_cursor_is_refused(app, native, bearer, make_user, no_provider, cursor):
    user = make_user("hist-cursor")
    response = page(native, bearer(user), cursor=cursor)
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_HISTORY_CURSOR"


def test_a_cursor_from_another_account_is_refused(app, native, bearer, make_user, no_provider):
    alice = make_user("hist-alice")
    bob = make_user("hist-bob")
    seed_days(alice.id, 9, per_day=1)
    seed_days(bob.id, 3, per_day=1)
    cursor = page(native, bearer(alice), limit="2").get_json()["next_cursor"]
    response = page(native, bearer(bob), cursor=cursor)
    assert response.status_code == 400
    bob_page = page(native, bearer(bob)).get_json()
    assert len(bob_page["days"]) == 3
    alice_texts = {m["id"] for d in page(native, bearer(alice), limit="14").get_json()["days"]
                   for m in d["meals"]}
    bob_texts = {m["id"] for d in bob_page["days"] for m in d["meals"]}
    assert not alice_texts & bob_texts


def test_today_is_identical_to_the_diary(app, native, bearer, make_user, no_provider):
    user = make_user("hist-today")
    today = app_today().isoformat()
    add_meal(user.id, today, kcal=320)
    add_meal(user.id, today, kcal=410, label="Akşam")
    headers = bearer(user)
    diary = native.get("/api/v1/nutrition/diary/today", headers=headers).get_json()
    first = page(native, headers).get_json()["days"][0]
    assert first["date"] == diary["day"]["date"]
    assert first["meals"] == diary["meals"]
    assert first["totals"] == diary["totals"]


def test_no_raw_ids_and_no_n_plus_one(app, native, bearer, make_user, no_provider):
    user = make_user("hist-q")
    rows = []
    for offset in range(5):
        key = (app_today() - timedelta(days=offset)).isoformat()
        rows += [add_meal(user.id, key), add_meal(user.id, key)]
    headers = bearer(user)
    with StatementCounter() as counter:
        body = page(native, headers).get_json()
    meal_selects = [s for s in counter.selects() if "meal_log" in s]
    assert len(meal_selects) == 2
    ids = {m["id"] for d in body["days"] for m in d["meals"]}
    assert not ids & {str(r.id) for r in rows}
    assert all(isinstance(i, str) and len(i) == 24 for i in ids)


def test_cursor_payload_does_not_reveal_the_owner(app):
    token = history.encode_cursor("k", 4242, "2026-08-01")
    import base64
    body = base64.urlsafe_b64decode(token.split(".")[0] + "==").decode()
    assert "4242" not in body and body == '{"before":"2026-08-01"}'
    assert history.decode_cursor("k", 4242, token) == "2026-08-01"
    with pytest.raises(Exception):
        history.decode_cursor("k", 4243, token)


def test_cursor_dates_are_validated(app):
    forged = history.encode_cursor("k", 1, "2026-02-31")
    with pytest.raises(Exception):
        history.decode_cursor("k", 1, forged)
    assert date.fromisoformat(history.decode_cursor(
        "k", 1, history.encode_cursor("k", 1, "2026-02-28")))
