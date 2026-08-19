"""Contract tests for GET /api/progress/history (Progress Redesign PR5).

    python -m pytest tests/test_progress_history_api.py -v
"""
from datetime import date, datetime, timedelta

from app.blueprints import tracking
from app.extensions import db
from app.models import WeeklyCheckIn, WorkoutLog
from app.services.progress_history import (
    CONTRACT_VERSION,
    HISTORY_LIMIT,
    UnknownProgressionSignal,
)
from app.timeutil import app_today

HISTORY_URL = "/api/progress/history"
SUMMARY_URL = "/api/progress/summary"
INSIGHTS_URL = "/api/progress/axis-insights"
PHYSIQUE_URL = "/api/progress/physique"


def _login(make_user, login, name, **fields):
    user = make_user(name, profile_complete=True, **fields)
    login(name)
    return user


def _add_workout(user_id, day, volume=100.0, weight=50.0):
    db.session.add(WorkoutLog(
        user_id=user_id, exercise_name="Squat",
        sets=3, reps=5, weight_kg=weight, volume=volume,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def _add_checkin(user_id, day, weight, qualifying=True):
    db.session.add(WeeklyCheckIn(
        user_id=user_id,
        weight=weight,
        yogunluk=3 if qualifying else None,
        created_at=datetime(day.year, day.month, day.day, 12),
    ))


def test_history_requires_authentication(app, client):
    r = client.get(HISTORY_URL)
    assert r.status_code in (302, 401)
    assert b"entries" not in r.data


def test_user_id_input_cannot_name_another_owner(app, client, make_user, login):
    other = _login(make_user, login, "phapiother")
    _add_checkin(other.id, app_today(), 99.0)
    db.session.commit()
    _login(make_user, login, "phapimine")
    mine = client.get(HISTORY_URL).get_json()
    ignored = client.get(f"{HISTORY_URL}?user_id={other.id}").get_json()
    assert mine == ignored
    assert mine["state"] == "empty"
    assert mine["entries"] == []


def test_range_query_is_ignored(app, client, make_user, login):
    user = _login(make_user, login, "phapirange")
    _add_checkin(user.id, app_today(), 78.4)
    db.session.commit()
    plain = client.get(HISTORY_URL).get_json()
    windowed = client.get(
        f"{HISTORY_URL}?weeks=1&start=2020-01-01&end=2020-01-02&range=year"
    ).get_json()
    assert plain == windowed
    assert windowed["entries"][0]["window"]["weeks"] == 4


def test_private_no_store_header(app, client, make_user, login):
    _login(make_user, login, "phapicache")
    r = client.get(HISTORY_URL)
    assert r.headers["Cache-Control"] == "private, no-store"


def test_empty_contract(app, client, make_user, login):
    _login(make_user, login, "phapiempty")
    r = client.get(HISTORY_URL)
    assert r.status_code == 200
    d = r.get_json()
    assert d["contract_version"] == CONTRACT_VERSION == 1
    assert d["state"] == "empty"
    assert set(d) == {"contract_version", "state", "entries", "has_more"}
    assert d["entries"] == []
    assert d["has_more"] is False


def test_available_contract_and_iso_dates(app, client, make_user, login):
    user = _login(make_user, login, "phapiavail")
    _add_checkin(user.id, date(2026, 7, 15), 78.4)
    db.session.commit()
    r = client.get(HISTORY_URL)
    assert r.status_code == 200
    d = r.get_json()
    assert d["state"] == "available"
    assert len(d["entries"]) == 1
    entry = d["entries"][0]
    assert set(entry) == {
        "checked_in_at", "analysis_day", "window", "trajectory",
        "performance", "consistency", "body",
    }
    assert entry["analysis_day"] == "2026-07-15"
    assert "T" in entry["checked_in_at"]
    assert "+" in entry["checked_in_at"] or entry["checked_in_at"].endswith("Z")
    assert "." not in entry["analysis_day"]
    assert "Aug" not in entry["checked_in_at"]
    assert set(entry["body"]) == {"weight_kg", "weight_delta_kg"}
    assert "coach_feedback" not in str(d)
    assert "feedback" not in str(d)
    assert "id" not in entry


def test_failure_is_generic_500_not_empty(app, client, make_user, login, monkeypatch):
    _login(make_user, login, "phapifail")

    def _boom(*a, **kw):
        raise UnknownProgressionSignal("unmapped training progression signal")

    monkeypatch.setattr(tracking, "build_progress_history", _boom)
    r = client.get(HISTORY_URL)
    assert r.status_code == 500
    payload = r.get_json()
    assert payload.get("error")
    assert "state" not in payload
    assert "empty" not in r.get_data(as_text=True)
    assert "entries" not in payload
    assert tracking._progress_summary_error_class(
        UnknownProgressionSignal("x")) == "contract_drift"


def test_history_failure_does_not_break_the_other_progress_endpoints(
        app, client, make_user, login, monkeypatch):
    user = _login(make_user, login, "phapiiso")
    _add_checkin(user.id, app_today(), 78.0)
    db.session.commit()

    monkeypatch.setattr(
        tracking, "build_progress_history",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))

    assert client.get(HISTORY_URL).status_code == 500
    assert client.get(SUMMARY_URL).status_code == 200
    assert client.get(INSIGHTS_URL).status_code == 200
    assert client.get(PHYSIQUE_URL).status_code == 200
    assert client.get("/checkin-history").status_code == 200


def test_legacy_checkin_history_contract_is_unchanged(
        app, client, make_user, login):
    user = _login(make_user, login, "phapilegacy")
    db.session.add(WeeklyCheckIn(
        user_id=user.id, weight=78.4, yogunluk=4, fatigue=2,
        progressive_overload="evet", uyku_kalitesi=5, beslenme_uyumu=4,
        coach_feedback="Keep going.",
        created_at=datetime(2026, 7, 15, 12),
    ))
    db.session.add(WeeklyCheckIn(
        user_id=user.id, weight=77.0, yogunluk=None,
        created_at=datetime(2026, 7, 16, 12),
    ))
    db.session.commit()

    history = client.get("/checkin-history").get_json()
    assert isinstance(history, list)
    assert len(history) == 1
    assert set(history[0]) == {
        "tarih", "kilo", "yogunluk", "fatigue", "overload",
        "uyku", "beslenme", "feedback",
    }
    assert history[0]["feedback"] == "Keep going."
    assert history[0]["tarih"] == "15.07"
    assert history[0]["kilo"] == 78.4

    canonical = client.get(HISTORY_URL).get_json()
    assert canonical["state"] == "available"
    assert "Keep going." not in str(canonical)
    assert canonical["entries"][0]["analysis_day"] == "2026-07-15"


def test_checkin_and_update_weight_unchanged(app, client, make_user, login, monkeypatch):
    monkeypatch.setattr(
        tracking, "generate_checkin_feedback",
        lambda *a, **kw: "ok")
    user = _login(make_user, login, "phapiwrite", weight=80.0, height=180,
                  age=30, gender="male", activity_level="moderate")
    r = client.post("/update-weight", json={"weight": 79.5})
    assert r.status_code == 200
    r = client.post("/checkin", json={
        "weight": 79.2, "yogunluk": 3, "fatigue": 2, "uyku_kalitesi": 4,
        "beslenme_uyumu": 4, "progressive_overload": "evet", "note": "",
    })
    assert r.status_code == 200
    assert r.get_json().get("coach_feedback") == "ok"


def test_endpoint_read_performs_no_write(app, client, make_user, login):
    user = _login(make_user, login, "phapinowrite", weight=78.0, streak_count=5,
                  rank_points=99)
    assert client.get("/api/progress/achievements").status_code == 200
    db.session.expire_all()
    before = db.session.get(type(user), user.id)
    before_streak, before_xp = before.streak_count, before.rank_points
    before_checkins = WeeklyCheckIn.query.filter_by(user_id=user.id).count()

    assert client.get(HISTORY_URL).status_code == 200
    db.session.expire_all()
    after = db.session.get(type(user), user.id)
    assert after.streak_count == before_streak
    assert after.rank_points == before_xp
    assert WeeklyCheckIn.query.filter_by(user_id=user.id).count() == before_checkins
