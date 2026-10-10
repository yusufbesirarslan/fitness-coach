"""LP17-B1 native Today Guidance HTTP contract (GET /api/v1/today/guidance).

Real Flask boundary, real canonical persistence. The Bearer principal is stubbed
(`as_mobile`) except where the credential pipeline itself is the claim
(`real_bearer`: invalid/revoked tokens, cross-account isolation, no writes).

    python -m pytest tests/test_lp17_b1_today_guidance_api.py -q
"""
import ast
import json
import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask.testing import FlaskClient
from sqlalchemy import event

from app.extensions import db
from app.feature_flags import (FLAGS_BY_KEY, FeatureFlagConfigurationError,
                               PARSED_BY_REGISTRY, resolve_rollout_flags)
from app.models import (MealLog, NutritionPlan, User, UserSession, WaterLog,
                        WeeklyCheckIn)
from app.services import mobile_auth, mobile_today, nutrition_day_view
from app.services import today_guidance_projection as projection
from app.services import today_guidance_read_model as read_model
from app.timeutil import audit_clock
from test_default_rate_limit_identity import tight_default_limiter  # noqa: F401
from test_lp16b_native_checkin_api import athlete, real_bearer  # noqa: F401
from test_mobile_today_api import FIXED_NOW, complete_workout, save_plan

PATH = "/api/v1/today/guidance"
TODAY_PATH = "/api/v1/today"
FLAG = "FITX_MOBILE_TODAY_GUIDANCE_ENABLED"
ROUTE_PATH = Path(__file__).resolve().parents[1] / "app/blueprints/mobile_today_guidance.py"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}
ROOT_KEYS = {"contract_version", "day", "timezone", "training", "nutrition",
             "hydration", "checkin", "recovery", "action_priority", "freshness"}
DAY = FIXED_NOW.date().isoformat()          # Thursday 2026-07-23, Istanbul
PLAN = {"isim": "Lean plan",
        "kahvalti": {"kalori": 450, "protein": 30, "karb": 50, "yag": 12, "yemekler": ["Oats"]}}


@pytest.fixture
def guidance_on(app):
    app.config[FLAG] = True


@pytest.fixture
def owner(make_user):
    return make_user("lp17b1-owner")


@pytest.fixture
def as_mobile(monkeypatch):
    """Stub only the credential lookup; the Bearer boundary itself stays real.

    The user is reloaded per request because the read model's canonical
    snapshots release the scoped session, detaching any instance held here.
    """
    def _headers(user):
        user_id, sub = user.id, user.cognito_sub
        monkeypatch.setattr(
            mobile_auth, "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                db.session.get(User, user_id), SimpleNamespace(id=1), {"sub": sub}))
        return {"Authorization": "Bearer opaque-guidance-access"}
    return _headers


def get(client, headers=None, path=PATH, now=FIXED_NOW, **kwargs):
    with audit_clock(now):
        return client.get(path, headers=headers, **kwargs)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"} and set(body["error"]) == ENVELOPE_KEYS
    assert body["error"]["request_id"]
    return body["error"]


def _nutrition(user_id, target=2100, meals=((525, 30, 60, 12),), water=3, plan=PLAN):
    if target is not None:
        db.session.add(UserSession(user_id=user_id, target_calories=target,
                                   goal="kas kazanma"))
    for kcal, p, c, f in meals:
        db.session.add(MealLog(user_id=user_id, ogun="Öğle", yemekler="x", kalori=kcal,
                               protein=p, karb=c, yag=f, tarih=DAY))
    if water is not None:
        db.session.add(WaterLog(user_id=user_id, date_key=DAY, count=water))
    if plan is not None:
        db.session.add(NutritionPlan(user_id=user_id, score=8, plan_data=json.dumps(plan)))
    db.session.commit()


def _checkins(user_id, count, *, sleep=4):
    # Newest first is Thursday 09:00 Istanbul; one row per earlier day.
    for index in range(count):
        db.session.add(WeeklyCheckIn(
            user_id=user_id, weight=80 - index * 0.1, yogunluk=3, fatigue=2,
            uyku_kalitesi=sleep if index == 0 else 3, beslenme_uyumu=4,
            progressive_overload="kismen",
            created_at=datetime(2026, 7, 23 - index, 6, 0) if index < 23
            else datetime(2026, 6, 1, 6, 0)))
    db.session.commit()


# =============================================================================
# Rollout gate: registry record, strict parsing, default OFF
# =============================================================================
def test_flag_is_a_default_off_registry_record_depending_on_native_auth():
    flag = FLAGS_BY_KEY[FLAG]
    assert flag.default is False
    assert flag.parsed_by == PARSED_BY_REGISTRY
    assert "MOBILE_AUTH_ENABLED" in flag.depends_on
    assert resolve_rollout_flags({})[FLAG] is False
    assert resolve_rollout_flags({FLAG: ""})[FLAG] is False
    assert resolve_rollout_flags({FLAG: "1"})[FLAG] is True
    for raw in ("true", "yes", "1 ", "on"):
        with pytest.raises(FeatureFlagConfigurationError):
            resolve_rollout_flags({FLAG: raw})


def test_app_reads_the_flag_only_through_the_registry(monkeypatch):
    from app import create_app
    monkeypatch.delenv(FLAG, raising=False)
    assert create_app().config[FLAG] is False
    monkeypatch.setenv(FLAG, "1")
    assert create_app().config[FLAG] is True
    monkeypatch.setenv(FLAG, "true")
    with pytest.raises(FeatureFlagConfigurationError):
        create_app()


def test_native_auth_off_keeps_every_native_route_absent_even_with_flag_on(monkeypatch):
    from app import create_app
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "0")
    monkeypatch.setenv(FLAG, "1")
    disabled = create_app()
    assert not any(rule.rule.startswith("/api/v1") for rule in disabled.url_map.iter_rules())
    assert disabled.test_client().get(PATH).status_code == 404


def _normalized(response):
    # The CSP nonce is per request; everything else must match byte for byte.
    return re.sub(rb'nonce="[^"]*"', b'nonce=""', response.data)


@pytest.mark.parametrize("headers", [
    None, {"Authorization": "Basic nope"}, {"Authorization": "Bearer valid"}])
def test_flag_off_get_is_404_before_authentication(
        app, owner, monkeypatch, headers):
    """OFF answers GET before `require_mobile_auth` and never runs the read model.

    With the limiter disabled (the suite default) the GET answer is the app's
    own not-found page, byte-identical to an unregistered path except the
    blueprint-wide `Cache-Control: no-store`. The route stays registered; the
    residuals that follow from that are pinned separately below.
    """
    calls = []
    monkeypatch.setattr(mobile_auth, "authenticate_access",
                        lambda raw: calls.append("auth"))
    monkeypatch.setattr(read_model, "build_today_guidance",
                        lambda user_id: calls.append("read"))
    response = get(FlaskClient(app, app.response_class), headers)
    unregistered = get(FlaskClient(app, app.response_class), headers,
                       path="/api/v1/today/guidance-unregistered-probe")

    assert calls == []
    assert response.status_code == unregistered.status_code == 404
    assert response.mimetype == unregistered.mimetype
    assert _normalized(response) == _normalized(unregistered)
    assert set(response.headers.keys()) - set(unregistered.headers.keys()) == {"Cache-Control"}
    assert set(unregistered.headers.keys()) <= set(response.headers.keys())
    assert response.headers["Cache-Control"] == "no-store"
    assert b"contract_version" not in response.data


def test_flag_off_residuals_under_the_production_limiter(app, owner, monkeypatch):
    """What OFF does NOT hide once the limiter is on (as in production).

    A request-time gate on a registered rule shares the residuals of every
    such `mobile_api` route: the pre-limiter principal binder still resolves a
    presented Bearer (and so the 600/hour default limit can answer 429), and
    OPTIONS reveals the rule. Guidance data is still never produced.
    """
    from app.extensions import limiter
    calls = []
    monkeypatch.setattr(mobile_auth, "authenticate_access",
                        lambda raw: calls.append("auth") or (_ for _ in ()).throw(
                            mobile_auth.MobileAuthFailure(
                                "AUTH_SESSION_EXPIRED", 401, False, "access_invalid")))
    monkeypatch.setattr(read_model, "build_today_guidance",
                        lambda user_id: calls.append("read"))
    monkeypatch.setattr(limiter, "enabled", True)
    bearer = {"Authorization": "Bearer valid"}
    response = get(FlaskClient(app, app.response_class), bearer)
    unregistered = get(FlaskClient(app, app.response_class), bearer,
                       path="/api/v1/today/guidance-unregistered-probe")
    assert response.status_code == unregistered.status_code == 404
    assert _normalized(response) == _normalized(unregistered)
    assert calls == ["auth"]                      # binder only; no read model
    options = FlaskClient(app, app.response_class).open(PATH, method="OPTIONS")
    assert options.status_code == 200 and "GET" in options.headers["Allow"]
    assert FlaskClient(app, app.response_class).open(
        "/api/v1/today/guidance-unregistered-probe", method="OPTIONS").status_code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_flag_off_wrong_method_is_405_from_the_registered_rule(
        app, client, owner, monkeypatch, method):
    """The registered GET rule answers other methods 405 whatever the flag says.

    Routing rejects the method before any view or gate runs, so neither the
    credential lookup nor the read model executes; an unregistered path
    answers the same method 404. `client` sends same-origin CSRF headers: on a
    routing failure the request has no blueprint, so without them the shared
    CSRF hook answers 403 first - for the unregistered path too.
    """
    calls = []
    monkeypatch.setattr(mobile_auth, "authenticate_access",
                        lambda raw: calls.append("auth"))
    monkeypatch.setattr(read_model, "build_today_guidance",
                        lambda user_id: calls.append("read"))
    bearer = {"Authorization": "Bearer valid"}
    probe_path = "/api/v1/today/guidance-unregistered-probe"
    response = client.open(PATH, method=method, headers=bearer)

    assert response.status_code == 405
    assert {"GET", "HEAD", "OPTIONS"} <= {
        allowed.strip() for allowed in response.headers["Allow"].split(",")}
    assert client.open(probe_path, method=method, headers=bearer).status_code == 404
    raw = FlaskClient(app, app.response_class)
    assert raw.open(PATH, method=method, headers=bearer).status_code == 403
    assert raw.open(probe_path, method=method, headers=bearer).status_code == 403
    assert calls == []
    assert b"contract_version" not in response.data


def test_flag_off_default_limit_answers_429_from_the_limiter(
        app, owner, as_mobile, tight_default_limiter, monkeypatch):  # noqa: F811
    """OFF does not exempt the route from the shared default rate limit.

    `tight_default_limiter` shrinks only the default quota (2 per hour, real
    Flask-Limiter, real storage) so the third GET trips it; the production
    600/hour value is pinned by `test_default_rate_limit_identity`. The
    before-request binder resolves the Bearer for the limiter key on every
    request, the gate keeps the read model out, and the 429 is the blueprint's
    `AUTH_RATE_LIMITED` envelope.
    """
    headers = as_mobile(owner)
    stubbed = mobile_auth.authenticate_access
    calls = []
    monkeypatch.setattr(mobile_auth, "authenticate_access",
                        lambda raw: calls.append("auth") or stubbed(raw))
    monkeypatch.setattr(read_model, "build_today_guidance",
                        lambda user_id: calls.append("read"))
    client = FlaskClient(app, app.response_class)
    responses = [get(client, headers) for _ in range(3)]

    assert [r.status_code for r in responses] == [404, 404, 429]
    error = _error(responses[2])
    assert error["code"] == "AUTH_RATE_LIMITED" and error["retryable"] is True
    assert int(responses[2].headers["Retry-After"]) > 0
    assert responses[2].headers["Cache-Control"] == "no-store"
    assert calls == ["auth", "auth", "auth"]      # binder only; no read model


# =============================================================================
# Authentication boundary (flag ON)
# =============================================================================
@pytest.mark.parametrize("value", [
    None, "Basic nope", "Bearer ", "Bearer two parts", "bearer lower"])
def test_missing_or_malformed_bearer_is_401_mobile_envelope(raw_client, guidance_on, value):
    headers = None if value is None else {"Authorization": value}
    response = get(raw_client, headers)
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Location" not in response.headers


def test_unknown_and_revoked_credentials_are_401(client, guidance_on, athlete, real_bearer):
    user = athlete("lp17b1-revoked")
    assert _error(get(client, {"Authorization": "Bearer not-a-real-credential"}))[
        "code"] == "AUTH_SESSION_EXPIRED"
    headers = real_bearer(user)
    assert get(client, headers).status_code == 200
    mobile_auth.revoke_all_for_user(user.id)
    response = get(client, headers)
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"


def test_browser_cookie_session_alone_is_refused(client, guidance_on, auth_user):
    assert client.get("/workout/status").status_code == 200   # cookie session is live
    response = get(client)
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert b"contract_version" not in response.data


def test_cross_account_isolation_through_the_real_credential_pipeline(
        client, guidance_on, athlete, real_bearer):
    alice, bob = athlete("lp17b1-alice"), athlete("lp17b1-bob")
    save_plan(alice)
    db.session.add_all([WaterLog(user_id=alice.id, date_key=DAY, count=6)])
    db.session.commit()
    _checkins(alice.id, 1)
    alice_headers, bob_headers = real_bearer(alice), real_bearer(bob)
    a = get(client, alice_headers).get_json()
    b = get(client, bob_headers).get_json()

    assert a["training"]["status"] == "scheduled_not_started"
    assert a["hydration"]["amount"] == 6 and a["checkin"]["state"] == "available"
    assert b["training"]["status"] == "no_plan"
    assert b["hydration"] == {"state": "empty", "amount": 0, "unit": "glass"}
    assert b["checkin"] == {"state": "empty", "current_week": b["checkin"]["current_week"],
                            "latest": None}
    assert b["checkin"]["current_week"]["submitted"] is False


def test_spoofed_owner_date_and_timezone_inputs_are_ignored(
        client, guidance_on, owner, make_user, as_mobile):
    other = make_user("lp17b1-spoof-other")
    save_plan(other)
    db.session.add(WaterLog(user_id=other.id, date_key="2026-07-01", count=8))
    db.session.commit()
    other_id = other.id
    headers = as_mobile(owner)
    plain = get(client, headers).get_json()
    spoofed = get(
        client, {**headers, "X-User-Id": str(other_id), "X-Date": "2026-07-01",
                 "X-Timezone": "UTC", "Date": "Wed, 01 Jul 2026 00:00:00 GMT"},
        query_string={"user_id": other_id, "owner_id": other_id, "day": "2026-07-01",
                      "date": "2026-07-01", "timezone": "UTC"}).get_json()
    assert spoofed == plain
    assert plain["day"] == DAY and plain["training"]["status"] == "no_plan"


# =============================================================================
# Canonical states: the transport republishes the canonical readers verbatim
# =============================================================================
@pytest.mark.parametrize("kind,expected", [
    ("none", "no_plan"), ("dinlenme", "rest_day"),
    ("antrenman", "scheduled_not_started"), ("broken", "needs_attention"),
    ("complete", "completed"),
])
def test_training_states_match_the_existing_today_endpoint(
        client, guidance_on, owner, as_mobile, kind, expected):
    if kind != "none":
        save_plan(owner, today_tip="dinlenme" if kind == "dinlenme" else "antrenman",
                  raw="broken" if kind == "broken" else None)
    if kind == "complete":
        complete_workout(owner)
    headers = as_mobile(owner)
    response = get(client, headers)
    today = get(client, headers, path=TODAY_PATH).get_json()["today"]

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert set(body) == ROOT_KEYS and body["contract_version"] == 1
    assert body["day"] == today["date"] == DAY
    training = body["training"]
    assert training["state"] == "available"
    assert training["status"] == expected == today["status"]
    for key in ("action", "workout", "daily_context"):
        assert training[key] == today[key]
    assert body["action_priority"] == {"state": "not_established", "primary": None}
    assert body["recovery"] == {"state": "unsupported", "facts": None}
    assert body["freshness"] == {"cacheable": False, "revision": None,
                                 "consistency": "independent_source_snapshots"}


def test_in_progress_session_matches_the_existing_today_endpoint(
        app, client, guidance_on, owner, as_mobile):
    from app.services.workout_session import start_session
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    save_plan(owner)
    with audit_clock(FIXED_NOW):
        start_session(owner.id)
    headers = as_mobile(owner)
    body = get(client, headers).get_json()
    today = get(client, headers, path=TODAY_PATH).get_json()["today"]
    assert body["training"]["status"] == today["status"] == "in_progress"
    assert body["training"]["workout"] == today["workout"]
    assert body["training"]["guidance"] == {"state": "in_progress", "kind": "resume_workout"}
    assert body["action_priority"]["primary"] is None


@pytest.mark.parametrize("seeded", [True, False])
def test_nutrition_and_hydration_match_the_native_day_view(
        client, guidance_on, owner, as_mobile, seeded):
    if seeded:
        _nutrition(owner.id)
    headers = as_mobile(owner)
    body = get(client, headers).get_json()
    day_view = get(client, headers, path="/api/v1/nutrition/day-view").get_json()

    assert body["nutrition"]["state"] == "available"
    assert body["nutrition"]["facts"] == {
        key: day_view[key] for key in ("target", "intake", "plan", "next_action")}
    assert body["hydration"] == day_view["hydration"]
    if seeded:
        assert body["hydration"] == {"state": "available", "amount": 3, "unit": "glass"}
        assert body["nutrition"]["facts"]["target"]["state"] == "available"
    else:
        # Measured zero / honest empty, never a fabricated target.
        assert body["hydration"] == {"state": "empty", "amount": 0, "unit": "glass"}
        assert body["nutrition"]["facts"]["target"]["value"] is None


def test_hydration_zero_row_is_measured_zero(client, guidance_on, owner, as_mobile):
    _nutrition(owner.id, water=0)
    assert get(client, as_mobile(owner)).get_json()["hydration"] == {
        "state": "empty", "amount": 0, "unit": "glass"}


def test_hydration_section_failure_is_unavailable_never_zero(
        client, guidance_on, owner, as_mobile, monkeypatch):
    _nutrition(owner.id)
    monkeypatch.setattr(nutrition_day_view, "_read_hydration",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("water-sentinel")))
    response = get(client, as_mobile(owner))
    body = response.get_json()
    assert response.status_code == 200
    assert body["hydration"]["state"] == "unavailable" and body["hydration"]["amount"] is None
    assert body["nutrition"]["state"] == "available"
    assert body["nutrition"]["facts"]["intake"]["state"] == "available"
    assert b"water-sentinel" not in response.data


@pytest.mark.parametrize("count", [1, 12, 14])
def test_checkin_history_matches_the_native_history_reader(
        client, guidance_on, owner, as_mobile, count):
    _checkins(owner.id, count)
    headers = as_mobile(owner)
    body = get(client, headers).get_json()["checkin"]
    history = get(client, headers, path="/api/v1/progress/check-ins").get_json()
    assert body["state"] == "available"
    assert body["latest"] == history["check_ins"][0]
    assert body["current_week"] == history["current_week"]
    assert body["current_week"]["submitted"] is True
    assert body["latest"]["sleep_quality"] == 4


def test_nullable_historical_checkin_values_stay_null(client, guidance_on, owner, as_mobile):
    db.session.add(WeeklyCheckIn(user_id=owner.id, weight=80.0, yogunluk=3, fatigue=None,
                                 uyku_kalitesi=None, beslenme_uyumu=None,
                                 progressive_overload=None,
                                 created_at=datetime(2026, 7, 22, 6, 0)))
    db.session.commit()
    latest = get(client, as_mobile(owner)).get_json()["checkin"]["latest"]
    assert latest["weight_kg"] == 80.0
    for key in ("fatigue", "sleep_quality", "nutrition_adherence",
                "progressive_overload", "weight_delta_kg"):
        assert latest[key] is None, key


def test_secondary_reader_failures_are_explicit_unavailable_sources(
        client, guidance_on, owner, as_mobile, monkeypatch):
    save_plan(owner)
    fail = lambda *a: (_ for _ in ()).throw(RuntimeError("secondary-sentinel"))  # noqa: E731
    monkeypatch.setattr(nutrition_day_view, "build_nutrition_day_view", fail)
    monkeypatch.setattr(read_model.history, "build_history", fail)
    response = get(client, as_mobile(owner))
    body = response.get_json()
    assert response.status_code == 200
    assert body["training"]["status"] == "scheduled_not_started"
    assert body["nutrition"] == {"state": "unavailable", "facts": None}
    assert body["hydration"] == {"state": "unavailable", "amount": None, "unit": "glass"}
    assert body["checkin"] == {"state": "unavailable", "current_week": None, "latest": None}
    assert b"secondary-sentinel" not in response.data


# =============================================================================
# Strict failures: typed retryable 503, no detail leakage
# =============================================================================
def _strict_training_failure(monkeypatch):
    monkeypatch.setattr(mobile_today, "build_today", lambda user_id: (_ for _ in ()).throw(
        mobile_today.TodayUnavailable("strict-sentinel")))


def _mixed_server_dates(monkeypatch):
    monkeypatch.setattr(read_model, "app_today", lambda: datetime(2026, 7, 24).date())


def _oversized(monkeypatch):
    monkeypatch.setattr(projection, "MAX_PAYLOAD_BYTES", 64)


def _non_finite(monkeypatch):
    original = mobile_today.build_today

    def poisoned(user_id):
        result = original(user_id)
        result["today"]["workout"] = {"summary": {"duration_minutes": math.nan}}
        return result
    monkeypatch.setattr(mobile_today, "build_today", poisoned)


@pytest.mark.parametrize("inject,cause", [
    (_strict_training_failure, "TodayUnavailable"),
    (_mixed_server_dates, "GuidanceUnavailable"),
    (_oversized, "ValueError"),
    (_non_finite, "ValueError"),
])
def test_strict_failures_are_a_typed_retryable_503_without_detail(
        client, guidance_on, owner, as_mobile, monkeypatch, caplog, inject, cause):
    save_plan(owner)
    owner_id, username = owner.id, owner.username
    headers = as_mobile(owner)
    inject(monkeypatch)
    with caplog.at_level(logging.ERROR):
        response = get(client, headers)

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    error = _error(response)
    assert error["code"] == "TODAY_GUIDANCE_TEMPORARILY_UNAVAILABLE"
    assert error["retryable"] is True
    lines = [r.getMessage() for r in caplog.records if "mobile_today_guidance" in r.getMessage()]
    assert lines == [f"mobile_today_guidance event=guidance_read_failed error_type={cause} "
                     f"request_id={error['request_id']}"]
    for leaked in (b"strict-sentinel", b"server day changed", b"exceeds bound", b"NaN",
                   b'"user_id"', username.encode()):
        assert leaked not in response.data
    assert "strict-sentinel" not in caplog.text and username not in caplog.text
    assert f"user_id={owner_id}" not in caplog.text


# =============================================================================
# Architecture: read-only, provider-free, thin, owner from the principal only
# =============================================================================
def test_request_issues_only_selects_and_never_flushes(
        client, guidance_on, athlete, real_bearer):
    user = athlete("lp17b1-readonly")
    save_plan(user)
    _nutrition(user.id, target=None)
    _checkins(user.id, 3)
    headers = real_bearer(user)          # issuance writes happen before capture
    seen, flushes = [], []
    engine, session_type = db.engine, type(db.session())

    def capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement.strip().split()[0].upper())

    def fail_flush(*args):
        flushes.append(True)
    event.listen(engine, "before_cursor_execute", capture)
    event.listen(session_type, "before_flush", fail_flush)
    try:
        response = get(client, headers)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(session_type, "before_flush", fail_flush)
    assert response.status_code == 200
    assert flushes == []
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(seen), seen


def test_request_succeeds_while_every_provider_client_would_explode(
        client, guidance_on, owner, as_mobile, monkeypatch):
    from app import extensions

    class _Detonator:
        def __getattr__(self, name):
            raise AssertionError(f"provider accessed: {name}")
    monkeypatch.setattr(extensions, "openai_client", _Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", _Detonator())
    save_plan(owner)
    _nutrition(owner.id)
    assert get(client, as_mobile(owner)).status_code == 200


def test_route_delegates_exactly_once_with_the_principal_id(
        client, guidance_on, owner, as_mobile, monkeypatch):
    calls = []
    real = read_model.build_today_guidance
    monkeypatch.setattr(read_model, "build_today_guidance",
                        lambda user_id: calls.append(user_id) or real(user_id))
    owner_id = owner.id
    assert get(client, as_mobile(owner), query_string={"user_id": 999}).status_code == 200
    assert calls == [owner_id]


def test_route_module_is_a_thin_transport():
    tree = ast.parse(ROUTE_PATH.read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported == {"functools", "flask", "app.blueprints.mobile_api", "app.extensions",
                        "app.mobile_auth_middleware", "app.observability", "app.services"}
    names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
             for alias in node.names}
    assert "request" not in names, "the transport must not read request input"
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    source = ROUTE_PATH.read_text(encoding="utf-8")
    for token in ("os.environ", "getenv", ".query", "text(", "execute(", ".commit(",
                  ".flush(", ".add(", "session.get(", "app_today", "priority",
                  "sorted(", "fallback", "openai", "bedrock"):
        assert token not in source, token


def test_route_registration_is_the_single_gated_mobile_endpoint(app):
    rules = [r for r in app.url_map.iter_rules() if r.rule == PATH]
    assert [(r.endpoint, sorted(r.methods - {"HEAD", "OPTIONS"})) for r in rules] == [
        ("mobile_api.today_guidance", ["GET"])]
    assert getattr(app.view_functions["mobile_api.today_guidance"],
                   "_require_mobile_auth", False) is True


@pytest.mark.parametrize("kind", ["none", "antrenman", "complete"])
def test_existing_today_response_is_identical_in_both_flag_states(
        app, client, owner, as_mobile, kind):
    if kind != "none":
        save_plan(owner)
    if kind == "complete":
        complete_workout(owner)
    headers = as_mobile(owner)
    app.config[FLAG] = False
    off = get(client, headers, path=TODAY_PATH)
    app.config[FLAG] = True
    on = get(client, headers, path=TODAY_PATH)
    assert off.status_code == on.status_code == 200
    assert off.data == on.data
    assert set(on.get_json()) == {"today"}


# =============================================================================
# Query budget and payload bound
# =============================================================================
def _measure(client, headers):
    seen = []
    engine = db.engine

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().split()[0].upper() == "SELECT":
            seen.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = get(client, headers)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    return len(seen), len(response.data)


def _legacy_weightless_history(user_id, count):
    """Worst case for the history reader: no in-window prior positive weight,
    so every published item needs its own (memoized, bounded) prior-day read."""
    for index in range(count):
        db.session.add(WeeklyCheckIn(
            user_id=user_id, weight=0, yogunluk=3, fatigue=2, uyku_kalitesi=3,
            beslenme_uyumu=3, progressive_overload="kismen",
            created_at=datetime(2026, 7, 23, 6, 0) - timedelta(days=index)))
    db.session.commit()


# Measured SELECTs with a stubbed credential lookup (the real Bearer pipeline adds
# its own 3 fixed reads): Today 3 + Nutrition 4 + check-in 2 = 9 in every normal
# state, independent of history size; a failed secondary source issues none;
# the adversarial history adds at most HISTORY_LIMIT (12) bounded lookups.
_BUDGETS = {"empty": 9, "plan": 9, "nutrition": 9, "checkins_30": 9,
            "secondary_failure": 3, "adversarial_history": 21}


@pytest.mark.parametrize("scenario", sorted(_BUDGETS))
def test_query_budget_and_output_size_are_bounded(
        client, guidance_on, owner, as_mobile, monkeypatch, scenario):
    if scenario in ("plan", "adversarial_history", "secondary_failure"):
        save_plan(owner)
    if scenario in ("nutrition", "adversarial_history"):
        _nutrition(owner.id)
    if scenario == "checkins_30":
        _checkins(owner.id, 30)
    if scenario == "adversarial_history":
        _legacy_weightless_history(owner.id, 30)
    if scenario == "secondary_failure":
        fail = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))  # noqa: E731
        monkeypatch.setattr(nutrition_day_view, "build_nutrition_day_view", fail)
        monkeypatch.setattr(read_model.history, "build_history", fail)
    selects, size = _measure(client, as_mobile(owner))
    assert selects <= _BUDGETS[scenario], (scenario, selects)
    assert size <= 4096, size     # observed ~1.6 KiB; the hard ceiling is 16 KiB


@pytest.mark.parametrize("rows", [13, 40])
def test_adversarial_history_cost_does_not_grow_with_rows(
        client, guidance_on, owner, as_mobile, rows):
    _legacy_weightless_history(owner.id, rows)
    selects, _size = _measure(client, as_mobile(owner))
    assert selects == _BUDGETS["adversarial_history"]


def test_real_bearer_worst_case_budget(client, guidance_on, athlete, real_bearer):
    user = athlete("lp17b1-budget")
    save_plan(user)
    _nutrition(user.id, target=None)
    _legacy_weightless_history(user.id, 20)
    selects, _size = _measure(client, real_bearer(user))
    assert selects <= _BUDGETS["adversarial_history"] + 3, selects
