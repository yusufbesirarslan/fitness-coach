"""NUTR-PR7 — global native contract + native NutritionDayView transport.

    python -m pytest tests/test_nutrition_vnext_pr7_native_contract.py -q

Proves the cross-capability rules every PR7 route shares (Bearer-only, typed
envelope, no-store, no cookie auth, owner from the principal only, typed 503
instead of the auth catch-all, domain-separated tokens) and that
``GET /api/v1/nutrition/day-view`` is the PR6 service verbatim.
"""
import ast
from datetime import datetime
from pathlib import Path

import pytest

from app.extensions import db
from app.models import MealLog, UserSession
from app.services import nutrition_day_view as dv
from app.services.nutrition_native import errors, tokens
from app.timeutil import APP_TZ, app_today, audit_clock

from nutrition_pr7_support import (  # noqa: F401  (pytest fixtures)
    StatementCounter, add_meal, bearer, error_of, native, no_provider,
    save_plan_row, set_target, set_water,
)

ROOT = Path(__file__).resolve().parent.parent
ROUTES = (ROOT / "app" / "blueprints" / "mobile_nutrition_closure.py").read_text(encoding="utf-8")
DAY_VIEW = "/api/v1/nutrition/day-view"

PR7_ROUTES = [
    ("get", "/api/v1/nutrition/day-view"),
    ("get", "/api/v1/nutrition/plan"),
    ("put", "/api/v1/nutrition/plan"),
    ("post", "/api/v1/nutrition/plan/generate"),
    ("post", "/api/v1/nutrition/plan/meals/AAAAAAAAAAAAAAAAAAAAAAAA/log"),
    ("get", "/api/v1/nutrition/hydration"),
    ("put", "/api/v1/nutrition/hydration"),
    ("get", "/api/v1/nutrition/history"),
    ("get", "/api/v1/nutrition/supplements"),
    ("post", "/api/v1/nutrition/supplements"),
    ("patch", "/api/v1/nutrition/supplements/AAAAAAAAAAAAAAAAAAAAAAAA"),
    ("delete", "/api/v1/nutrition/supplements/AAAAAAAAAAAAAAAAAAAAAAAA"),
]


# ── global auth boundary ───────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path"), PR7_ROUTES)
def test_every_pr7_route_requires_bearer_and_answers_json(native, method, path):
    response = getattr(native, method)(path, json={})
    assert response.status_code == 401
    assert error_of(response)["code"] == "AUTH_SESSION_EXPIRED"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Location" not in response.headers
    assert "Set-Cookie" not in response.headers


@pytest.mark.parametrize(("method", "path"), PR7_ROUTES)
def test_a_browser_cookie_session_is_not_native_auth(
        app, client, make_user, login, method, path):
    """N7-19: a logged-in browser session reaches NO native Nutrition route."""
    make_user("cookie-user")
    login("cookie-user")
    response = getattr(client, method)(path, json={})
    assert response.status_code == 401
    assert error_of(response)["code"] == "AUTH_SESSION_EXPIRED"


def test_every_pr7_view_is_wrapped_by_require_mobile_auth(app):
    from app.blueprints import mobile_nutrition_closure as module
    pr7 = [(rule.rule, app.view_functions[rule.endpoint])
           for rule in app.url_map.iter_rules()
           if app.view_functions[rule.endpoint].__module__ == module.__name__]
    assert len(pr7) == 12
    for path, view in pr7:
        assert path.startswith("/api/v1/nutrition/"), path
        assert getattr(view, "_require_mobile_auth", False), path


def test_routes_never_read_an_owner_from_the_request():
    tree = ast.parse(ROUTES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in ("user_id", "email", "sub", "owner", "username")
        if isinstance(node, ast.Attribute) and node.attr in ("form", "cookies"):
            raise AssertionError(f"request.{node.attr} read in native routes")
    assert "current_user" not in ROUTES and "session[" not in ROUTES


def test_error_vocabulary_is_closed_and_semantically_retryable():
    classes = errors.all_error_classes()
    codes = [cls.code for cls in classes]
    assert len(codes) == len(set(codes)), "duplicate public code"
    by_code = {cls.code: cls for cls in classes}
    for code in ("STALE_NUTRITION_PLAN", "STALE_HYDRATION", "STALE_SUPPLEMENT",
                 "STALE_SUPPLEMENT_CABINET", "IDEMPOTENCY_CONFLICT",
                 "INVALID_PLAN_PROPOSAL"):
        assert by_code[code].retryable is False, code
    for code in ("NUTRITION_PLAN_UNAVAILABLE", "HYDRATION_UNAVAILABLE",
                 "NUTRITION_HISTORY_UNAVAILABLE", "SUPPLEMENTS_UNAVAILABLE",
                 "NUTRITION_DAY_VIEW_UNAVAILABLE",
                 "NUTRITION_PLAN_GENERATION_FAILED"):
        assert by_code[code].retryable is True and by_code[code].status == 503, code
    assert by_code["NUTRITION_PRECONDITION_REQUIRED"].status == 428


def test_token_classes_are_domain_separated():
    secret = "s"
    parts = (tokens.integer(1), tokens.integer(2))
    produced = {domain: tokens.digest_token(secret, domain, *parts)
                for domain in tokens.DOMAINS}
    assert len(set(produced.values())) == len(tokens.DOMAINS)
    from app.services.mobile_nutrition.identity import diary_entry_id
    assert diary_entry_id(secret, 1, 2) not in produced.values()


@pytest.mark.parametrize("header", [
    "W/\"AAAAAAAAAAAAAAAAAAAAAAAA\"", "*", "\"a\", \"b\"", "AAAAAAAAAAAAAAAA",
    "\"\"", "\"short\"",
])
def test_if_match_refuses_weak_star_list_unquoted_blank(
        app, native, bearer, make_user, header):
    user = make_user("ifmatch")
    response = native.put("/api/v1/nutrition/hydration",
                          headers=bearer(user, **{"If-Match": header}),
                          json={"amount": 1})
    assert response.status_code == 400
    assert error_of(response)["code"] == "INVALID_NUTRITION_PRECONDITION"


# ── A. native day view ─────────────────────────────────────────────────────


def test_native_day_view_is_the_pr6_payload_for_the_bearer_owner(
        app, native, bearer, make_user, no_provider):
    user = make_user("dv-native")
    today = app_today().isoformat()
    set_target(user.id, 2100)
    add_meal(user.id, today, kcal=525)
    set_water(user.id, today, 3)
    save_plan_row(user.id)
    response = native.get(DAY_VIEW, headers=bearer(user))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    expected = dv.nutrition_day_view_payload(dv.build_nutrition_day_view(user.id))
    assert response.get_json() == expected
    body = response.get_json()
    assert body["intake"]["state"] == "available"
    assert body["hydration"] == {"state": "available", "amount": 3, "unit": "glass"}
    assert body["next_action"]["kind"] == "log_food"


def test_web_and_native_day_view_are_semantically_identical(
        app, client, native, bearer, make_user, login, no_provider):
    """Same user + same committed state → same business truth on both wires."""
    user = make_user("dv-parity")
    login("dv-parity")
    today = app_today().isoformat()
    set_target(user.id, 1800, goal="kilo verme")
    add_meal(user.id, today, kcal=410)
    set_water(user.id, today, 0)
    web = client.get("/nutrition-day-view")
    mobile = native.get(DAY_VIEW, headers=bearer(user))
    assert web.status_code == mobile.status_code == 200
    assert web.get_json() == mobile.get_json()


def test_native_day_view_states_unknown_is_not_zero(
        app, native, bearer, make_user, no_provider):
    user = make_user("dv-empty")
    body = native.get(DAY_VIEW, headers=bearer(user)).get_json()
    assert body["target"] == {"state": "empty", "value": None, "unit": "kcal"}
    assert body["intake"]["state"] == "empty"
    assert body["plan"] == {"state": "empty", "summary": None}
    assert body["next_action"]["kind"] == "set_target"
    assert "loading" not in str(body)


def test_native_day_view_unavailable_is_not_empty(
        app, native, bearer, make_user, monkeypatch):
    user = make_user("dv-fail")

    def broken(_user_id, _day):
        raise RuntimeError("storage")
    monkeypatch.setattr(dv, "_read_intake", broken)
    body = native.get(DAY_VIEW, headers=bearer(user)).get_json()
    assert body["intake"] == {"state": "unavailable", "totals": None, "meal_count": None}
    assert body["next_action"]["kind"] == "retry"


def test_native_day_view_total_failure_is_a_typed_retryable_503(
        app, native, bearer, make_user, monkeypatch):
    user = make_user("dv-503")
    from app.blueprints import mobile_nutrition_closure as module
    monkeypatch.setattr(module, "build_nutrition_day_view",
                        lambda _uid: (_ for _ in ()).throw(RuntimeError("boom")))
    response = native.get(DAY_VIEW, headers=bearer(user))
    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "NUTRITION_DAY_VIEW_UNAVAILABLE"
    assert error["retryable"] is True


def test_native_day_view_reads_four_bounded_selects_and_writes_nothing(
        app, native, bearer, make_user, no_provider):
    user = make_user("dv-queries")
    today = app_today().isoformat()
    set_target(user.id)
    add_meal(user.id, today)
    headers = bearer(user)
    with StatementCounter() as counter:
        assert native.get(DAY_VIEW, headers=headers).status_code == 200
    domain = [s for s in counter.selects()
              if any(t in s for t in ("meal_log", "user_session", "water_log",
                                      "nutrition_plan"))]
    assert len(domain) == 4
    assert not [w for w in counter.writes() if "savepoint" not in w.lower()]


def test_native_day_view_uses_the_server_istanbul_day(
        app, native, bearer, make_user, no_provider):
    """01:30 in Istanbul is already the next day; client hints are ignored."""
    user = make_user("dv-day")
    with audit_clock(datetime(2026, 8, 10, 1, 30, tzinfo=APP_TZ)):
        add_meal(user.id, "2026-08-10", kcal=300)
        add_meal(user.id, "2026-08-09", kcal=999)
        body = native.get(DAY_VIEW, headers=bearer(user, **{"X-Timezone": "UTC"}),
                          query_string={"day": "2026-08-09"}).get_json()
    assert body["day"] == "2026-08-10"
    assert body["intake"]["totals"]["calories"] == 300.0


def test_native_day_view_owner_isolation(app, native, bearer, make_user, no_provider):
    alice = make_user("dv-alice")
    bob = make_user("dv-bob")
    today = app_today().isoformat()
    add_meal(alice.id, today, kcal=700)
    set_target(alice.id)
    body = native.get(DAY_VIEW, headers=bearer(bob),
                      query_string={"user_id": alice.id}).get_json()
    assert body["intake"]["state"] == "empty"
    assert body["target"]["state"] == "empty"


def test_native_route_holds_no_day_view_rule_of_its_own():
    """N7-01 guard: the transport calls the PR6 builder + payload, nothing else."""
    tree = ast.parse(ROUTES)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "nutrition_day_view_native")
    calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert {"build_nutrition_day_view", "nutrition_day_view_payload"} <= calls
    assert not any(name in ROUTES for name in ("MealLog", "WaterLog", "derive_next_action"))


# ── existing native Nutrition semantics PR7 must not regress ───────────────

MANUAL = {"kind": "manual", "description": "Apple", "slot": "ara_ogun",
          "nutrition": {"energy_kcal": 52, "protein_g": 0, "carbohydrate_g": 14,
                        "fat_g": 0}}


def test_existing_logfood_key_semantics_are_preserved(app, native, bearer, make_user, no_provider):
    """N7-21 guard: same key + same command replays; different command → 409."""
    user = make_user("logfood-guard")
    headers = bearer(user, **{"Idempotency-Key": "logfood-guard-1"})
    first = native.post("/api/v1/nutrition/logs", headers=headers, json=MANUAL)
    replay = native.post("/api/v1/nutrition/logs", headers=headers, json=MANUAL)
    other = native.post("/api/v1/nutrition/logs", headers=headers,
                        json=dict(MANUAL, description="Pear"))
    assert (first.status_code, replay.status_code, other.status_code) == (201, 200, 409)
    assert replay.get_json() == first.get_json()
    assert MealLog.query.filter_by(user_id=user.id).count() == 1


def test_existing_diary_if_match_semantics_are_preserved(app, native, bearer, make_user, no_provider):
    """N7-22 guard: a stale entry revision never moves or deletes a meal."""
    user = make_user("diary-guard")
    add_meal(user.id, app_today().isoformat(), label="Kahvaltı")
    headers = bearer(user)
    meal = native.get("/api/v1/nutrition/diary/today", headers=headers).get_json()["meals"][0]
    path = f"/api/v1/nutrition/logs/{meal['id']}"
    stale = '"' + "A" * 24 + '"'
    moved = native.patch(path, headers=dict(headers, **{"If-Match": stale}),
                         json={"operation": "set_slot", "slot": "ogle"})
    deleted = native.delete(path, headers=dict(headers, **{"If-Match": stale}))
    assert moved.status_code == deleted.status_code == 412
    assert MealLog.query.filter_by(user_id=user.id).one().ogun == "Kahvaltı"
