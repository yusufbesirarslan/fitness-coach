"""Contract tests for the canonical mobile Today read surface (Sprint 12 PR3).

``GET /api/v1/today`` is a *projection*, never an authority: every fitness fact it
publishes is resolved by ``app/services/workout_state`` and the canonical
active-plan selector in ``app/services/today_facts``. These tests pin the answers
Flutter PR4 is allowed to depend on, and each one fails if the semantic regresses:

  * no-plan is NOT a rest day, and neither is an unreadable schedule;
  * completion comes from the canonical completion authority, never from the clock;
  * the day is the server's Istanbul day, never the caller's;
  * lineage/version are never fabricated when no plan exists;
  * an infrastructure failure is an error, never a fabricated empty Today.

    python -m pytest tests/test_mobile_today_api.py -v
"""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.extensions import db
from app.models import (
    PumpCheck, TrainingPlan, User, WORKOUT_COMPLETION_MARKER, WorkoutLog)
from app.services import mobile_auth
from app.services.training_generation.response_validator import WEEKDAYS
from app.services.workout_state import models as ws_models
from app.timeutil import APP_TZ, audit_clock


TODAY_PATH = "/api/v1/today"

# A fixed Thursday, 15:00 Istanbul - safely inside the day, so a suite run near
# midnight can never make these tests disagree with themselves.
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
TODAY = date(2026, 7, 23)
YESTERDAY = TODAY - timedelta(days=1)
NOON_TODAY = datetime(2026, 7, 23, 9, 0)   # naive UTC == 12:00 Istanbul


@pytest.fixture
def mobile_user(make_user):
    return make_user("today-mobile")


@pytest.fixture
def other_user(make_user):
    return make_user("today-other")


@pytest.fixture
def as_mobile(monkeypatch):
    """Authenticate as one user through the real Bearer boundary.

    The credential store itself is covered by tests/test_mobile_auth_api.py; what
    matters here is that this route resolves its user from ``require_mobile_auth``
    and from nowhere else, so the principal is stubbed and the boundary is not.
    """
    def _headers(user):
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
        return {"Authorization": "Bearer opaque-access-credential"}
    return _headers


# -- Canonical fixtures (real persistence, never a stubbed authority) --------
def _day(name, tip):
    rest = tip == "dinlenme"
    return {
        "gun": name,
        "tip": tip,
        "odak": "-" if rest else "Tum vucut",
        "sure_dk": 0 if rest else 45,
        "tahmini_kalori": 0 if rest else 320,
        "egzersizler": [] if rest else [
            {"isim": "Squat", "set": 3, "tekrar": "10", "dinlenme": "90 sn",
             "not": ""},
            {"isim": "Bench Press", "set": 4, "tekrar": "8", "dinlenme": "120 sn",
             "not": ""},
        ],
    }


def _program(today=TODAY, today_tip="antrenman"):
    """A valid 7-day program: every day is rest except today's weekday."""
    today_name = WEEKDAYS[today.weekday()]
    return {"program": [_day(name, today_tip if name == today_name else "dinlenme")
                        for name in WEEKDAYS],
            "haftalik_ozet": {}}


def save_plan(user, today_tip="antrenman", today=TODAY, raw=None, **columns):
    plan = TrainingPlan(
        user_id=user.id,
        plan_data=raw if raw is not None else json.dumps(_program(today, today_tip)),
        score=5, created_at=datetime(2026, 7, 1, 8, 0), **columns)
    db.session.add(plan)
    db.session.commit()
    return plan


def complete_workout(user, when=NOON_TODAY):
    """Write the canonical completion proof (that day's PumpCheck) + its marker."""
    db.session.add(PumpCheck(user_id=user.id, valid=True,
                             date_key=when.date().isoformat(), created_at=when))
    db.session.add(WorkoutLog(
        user_id=user.id, exercise_name=WORKOUT_COMPLETION_MARKER,
        sets=0, reps=0, weight_kg=0, volume=0, created_at=when))
    db.session.commit()


def read_today(client, headers, now=FIXED_NOW):
    with audit_clock(now):
        return client.get(TODAY_PATH, headers=headers)


# -- Authentication boundary ------------------------------------------------
def test_missing_bearer_returns_mobile_json_and_never_an_html_redirect(raw_client):
    response = raw_client.get(TODAY_PATH)

    assert response.status_code == 401
    assert response.is_json
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert set(response.json["error"]) == {
        "code", "message", "retryable", "request_id"}
    assert "Location" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"


def test_malformed_authorization_header_is_rejected(raw_client):
    response = raw_client.get(TODAY_PATH, headers={"Authorization": "Basic nope"})

    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_rejected_credential_is_rejected_with_the_shared_envelope(
        raw_client, monkeypatch):
    def _reject(raw):
        raise mobile_auth.MobileAuthFailure(
            "AUTH_SESSION_EXPIRED", 401, False, "revoked")

    monkeypatch.setattr(mobile_auth, "authenticate_access", _reject)

    response = raw_client.get(
        TODAY_PATH, headers={"Authorization": "Bearer revoked"})

    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_response_is_never_publicly_cacheable(client, mobile_user, as_mobile):
    save_plan(mobile_user)

    response = read_today(client, as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


# -- Ownership: identity comes from the credential and from nowhere else ----
def test_authenticated_user_receives_only_their_own_today(
        client, mobile_user, other_user, as_mobile):
    save_plan(mobile_user)                       # caller: a scheduled workout day
    save_plan(other_user, today_tip="dinlenme")  # other user: a rest day

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_SCHEDULED_NOT_STARTED
    assert payload["workout"]["is_rest_day"] is False


@pytest.mark.parametrize("query", [
    "?user_id={other}", "?user={other}", "?username=today-other",
    "?owner_id={other}", "?date=2026-01-01", "?today=2026-01-01",
])
def test_no_query_parameter_can_select_another_user_or_another_day(
        client, mobile_user, other_user, as_mobile, query):
    save_plan(mobile_user)
    save_plan(other_user, today_tip="dinlenme")
    headers = as_mobile(mobile_user)
    other_id = other_user.id
    baseline = read_today(client, headers).json["today"]

    with audit_clock(FIXED_NOW):
        tampered = client.get(
            TODAY_PATH + query.format(other=other_id), headers=headers)

    assert tampered.status_code == 200
    assert tampered.json["today"] == baseline


def test_headers_naming_another_user_or_day_are_ignored(
        client, mobile_user, other_user, as_mobile):
    save_plan(mobile_user)
    save_plan(other_user, today_tip="dinlenme")
    headers = dict(as_mobile(mobile_user))
    other_id = other_user.id
    baseline = read_today(client, dict(headers)).json["today"]

    headers.update({
        "X-User-Id": str(other_id),
        "X-Client-Date": "2026-01-01",
        "X-Timezone": "Pacific/Auckland",
    })
    with audit_clock(FIXED_NOW):
        tampered = client.get(TODAY_PATH, headers=headers)

    assert tampered.json["today"] == baseline


# -- Canonical state: scheduled workout -------------------------------------
def test_scheduled_workout_day_is_canonical_and_actionable(
        client, mobile_user, as_mobile):
    plan = save_plan(mobile_user)
    lineage, version = plan.lineage_id, plan.mutation_version

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_SCHEDULED_NOT_STARTED
    assert payload["action"] == ws_models.ACTION_START
    assert payload["workout"]["schedule_state"] == ws_models.SCHEDULE_SCHEDULED
    assert payload["workout"]["is_rest_day"] is False
    assert payload["workout"]["completed"] is False
    assert payload["plan"]["exists"] is True
    assert payload["daily_context"] == {
        "plan_lineage": lineage,
        "mutation_version": version,
        "canonical_local_date": TODAY.isoformat(),
    }


def test_scheduled_day_publishes_a_bounded_summary_not_the_exercise_payload(
        client, mobile_user, as_mobile):
    save_plan(mobile_user)

    summary = read_today(
        client, as_mobile(mobile_user)).json["today"]["workout"]["summary"]

    assert summary == {
        "day": WEEKDAYS[TODAY.weekday()],
        "kind": "antrenman",
        "focus": "Tum vucut",
        "duration_min": 45,
        "estimated_calories": 320,
        "exercise_count": 2,
    }
    # PR3 is a Today read, not a workout-detail duplicate: no exercise payload.
    assert "egzersizler" not in json.dumps(summary)
    assert "Squat" not in json.dumps(summary)


# -- Canonical state: rest day (only ever from the schedule) ----------------
def test_rest_day_comes_from_the_canonical_schedule(
        client, mobile_user, as_mobile):
    save_plan(mobile_user, today_tip="dinlenme")

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_REST_DAY
    assert payload["workout"]["is_rest_day"] is True
    assert payload["workout"]["schedule_state"] == ws_models.SCHEDULE_REST_DAY
    assert payload["action"] == ws_models.ACTION_NONE
    assert payload["plan"]["exists"] is True


# -- Canonical state: no plan is NOT a rest day -----------------------------
def test_no_plan_is_truthful_and_never_a_rest_day(
        client, mobile_user, as_mobile):
    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_NO_PLAN
    assert payload["workout"]["schedule_state"] == ws_models.SCHEDULE_NO_PLAN
    assert payload["workout"]["is_rest_day"] is False
    assert payload["workout"]["completed"] is False
    assert payload["workout"]["summary"] is None
    assert payload["plan"] == {"exists": False, "created_at": None}


def test_no_plan_never_fabricates_lineage_or_mutation_version(
        client, mobile_user, as_mobile):
    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["daily_context"] == {
        "plan_lineage": None,
        "mutation_version": None,
        "canonical_local_date": TODAY.isoformat(),
    }


# -- Canonical state: completion comes from the completion authority --------
def test_completed_state_comes_from_the_canonical_completion_authority(
        client, mobile_user, as_mobile):
    save_plan(mobile_user)
    complete_workout(mobile_user)

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_COMPLETED
    assert payload["workout"]["completed"] is True
    assert payload["action"] == ws_models.ACTION_NONE
    assert payload["state"]["execution_state"] == ws_models.EXEC_COMPLETED


def test_a_past_completion_does_not_make_today_completed(
        client, mobile_user, as_mobile):
    save_plan(mobile_user)
    complete_workout(mobile_user, when=datetime(2026, 7, 22, 9, 0))  # yesterday

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["workout"]["completed"] is False
    assert payload["status"] == ws_models.PRIMARY_SCHEDULED_NOT_STARTED


def test_execution_evidence_is_not_reported_as_completion(
        client, mobile_user, as_mobile):
    save_plan(mobile_user)
    db.session.add(WorkoutLog(
        user_id=mobile_user.id, exercise_name="Squat", sets=3, reps=10,
        weight_kg=50, volume=1500, created_at=NOON_TODAY))
    db.session.commit()

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["workout"]["completed"] is False
    assert payload["status"] == ws_models.PRIMARY_EXECUTION_RECORDED


# -- An unreadable schedule is neither a rest day nor "no plan" -------------
def test_unreadable_schedule_is_needs_attention_not_rest_and_not_no_plan(
        client, mobile_user, as_mobile):
    save_plan(mobile_user, raw="{not json at all")

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_NEEDS_ATTENTION
    assert payload["workout"]["is_rest_day"] is False
    assert payload["workout"]["schedule_state"] == ws_models.SCHEDULE_UNAVAILABLE
    assert payload["workout"]["summary"] is None
    # The plan row genuinely exists - an unreadable schedule is not "no plan".
    assert payload["plan"]["exists"] is True
    assert payload["daily_context"]["plan_lineage"] is not None


# -- Canonical date: the server's Istanbul day, never the caller's ----------
def test_late_utc_evening_reports_the_next_istanbul_day(
        client, mobile_user, as_mobile):
    """22:30 UTC on 22 Jul is already 01:30 on 23 Jul in Istanbul."""
    just_after_midnight = datetime(2026, 7, 23, 1, 30, tzinfo=APP_TZ)
    assert just_after_midnight.astimezone(ZoneInfo("UTC")).date() == YESTERDAY

    save_plan(mobile_user)  # today's weekday (23 Jul) is the scheduled one

    payload = read_today(
        client, as_mobile(mobile_user), now=just_after_midnight).json["today"]

    assert payload["date"] == TODAY.isoformat()
    assert payload["daily_context"]["canonical_local_date"] == TODAY.isoformat()
    # ...and the Istanbul day drives the schedule lookup, not the UTC day.
    assert payload["status"] == ws_models.PRIMARY_SCHEDULED_NOT_STARTED


def test_the_reported_date_matches_the_canonical_workout_state_day(
        client, mobile_user, as_mobile):
    save_plan(mobile_user)

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["date"] == payload["state"]["today"]
    assert payload["date"] == payload["daily_context"]["canonical_local_date"]


def test_server_time_is_utc_and_not_client_supplied(
        client, mobile_user, as_mobile):
    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["server_time"].endswith("Z")
    assert "+" not in payload["server_time"]


# -- Daily context identity (PR4's cache-invalidation key) ------------------
def test_mutation_version_moves_with_the_canonical_plan_version(
        client, mobile_user, as_mobile):
    plan_id = save_plan(mobile_user).id
    before = read_today(client, as_mobile(mobile_user)).json["today"]

    plan = db.session.get(TrainingPlan, plan_id)
    plan.mutation_version = plan.mutation_version + 1
    db.session.commit()
    after = read_today(client, as_mobile(mobile_user)).json["today"]

    assert after["daily_context"]["mutation_version"] == \
        before["daily_context"]["mutation_version"] + 1
    # Lineage names the plan across its versions and must NOT move with them.
    assert after["daily_context"]["plan_lineage"] == \
        before["daily_context"]["plan_lineage"]


def test_daily_context_carries_no_internal_database_identifier(
        client, mobile_user, as_mobile):
    plan = save_plan(mobile_user)
    lineage, primary_key = plan.lineage_id, plan.id

    payload = read_today(client, as_mobile(mobile_user)).json

    assert lineage in json.dumps(payload)
    assert "user_id" not in json.dumps(payload)
    assert payload["today"]["daily_context"]["plan_lineage"] != primary_key


# -- Stable, typed contract -------------------------------------------------
def test_contract_keys_are_identical_across_every_domain_state(
        client, mobile_user, other_user, as_mobile, make_user):
    third = make_user("today-third")
    save_plan(other_user, today_tip="dinlenme")
    save_plan(third)
    complete_workout(third)

    shapes = []
    # Re-fetch each principal: the coherent-read boundary resets the scoped
    # session, so an instance held across a request would be detached.
    for user_id in (mobile_user.id, other_user.id, third.id):
        payload = read_today(
            client, as_mobile(db.session.get(User, user_id))).json["today"]
        shapes.append((
            tuple(sorted(payload)),
            tuple(sorted(payload["workout"])),
            tuple(sorted(payload["plan"])),
            tuple(sorted(payload["daily_context"])),
        ))

    assert shapes[0] == shapes[1] == shapes[2]
    assert shapes[0][0] == (
        "action", "daily_context", "date", "plan", "server_time", "state",
        "status", "workout")


def test_no_speculative_capability_is_published(client, mobile_user, as_mobile):
    save_plan(mobile_user)

    body = json.dumps(read_today(client, as_mobile(mobile_user)).json).lower()

    for absent in ("readiness", "recovery", "strain", "check_in", "checkin",
                   "pending_proposal", "pending_plan_change", "why_plan_changed",
                   "recommendation", "next_best_action", "nutrition",
                   "calorie_goal"):
        assert absent not in body, f"speculative concept published: {absent}"


# -- Degraded dependency: an error is an error, never an empty Today --------
def test_a_canonical_read_failure_is_an_error_not_a_fabricated_empty_today(
        client, mobile_user, as_mobile, monkeypatch):
    from app.services import mobile_today

    save_plan(mobile_user)

    def _explode(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(mobile_today, "resolve_workout_state", _explode)

    response = read_today(client, as_mobile(mobile_user))

    assert response.status_code == 503
    assert response.json["error"]["code"] == "TODAY_TEMPORARILY_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert set(response.json["error"]) == {
        "code", "message", "retryable", "request_id"}
    body = json.dumps(response.json)
    assert "database is gone" not in body
    assert "Traceback" not in body and "RuntimeError" not in body
    assert "today" not in response.json


def test_a_plan_read_failure_is_not_reported_as_no_plan(
        client, mobile_user, as_mobile, monkeypatch):
    from app.services import mobile_today

    save_plan(mobile_user)

    def _explode(*args, **kwargs):
        raise RuntimeError("plan read failed")

    monkeypatch.setattr(mobile_today, "get_active_plan", _explode)

    response = read_today(client, as_mobile(mobile_user))

    assert response.status_code == 503
    assert "no_plan" not in json.dumps(response.json)


# -- Adaptive Coaching independence ----------------------------------------
def test_today_is_correct_with_every_adaptive_coaching_flag_off(
        app, client, mobile_user, as_mobile):
    save_plan(mobile_user)
    app.config["AI_ADAPTIVE_PLAN_CONTEXT"] = False
    app.config["AI_COACH_PLAN_MUTATION_TOOLS_ENABLED"] = False

    payload = read_today(client, as_mobile(mobile_user)).json["today"]

    assert payload["status"] == ws_models.PRIMARY_SCHEDULED_NOT_STARTED
    assert payload["daily_context"]["plan_lineage"] is not None
