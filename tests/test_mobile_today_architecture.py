"""Architecture guards for the canonical mobile Today surface (Sprint 12 PR3).

PR3's central rule is that `GET /api/v1/today` **exposes** canonical Today state
and must never **become** a second Today authority. That rule is not
self-enforcing: the next person to touch this endpoint can add one `if` and quietly
own rest-day inference for mobile only, and every contract test in
tests/test_mobile_today_api.py would still pass.

These are the guards that would not. They are behavioural and dependency-level
rather than line-number-based:

  * the projection *delegates* - it holds no rest/completion/date decision of its
    own, and it reads no fitness table directly;
  * mobile and web agree, because they are literally fed by the same resolver;
  * the route is inside the existing mobile auth + feature gate, not beside it;
  * no provider/LLM machinery is reachable from a Today read.

    python -m pytest tests/test_mobile_today_architecture.py -v
"""
import ast
import inspect
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from app.blueprints import mobile_today as today_route
from app.extensions import db
from app.models import TrainingPlan
from app.services import mobile_today
from app.services import mobile_auth
from app.services.today_facts import get_active_plan
from app.services.training_generation.response_validator import WEEKDAYS
from app.services.workout_state import resolve_workout_state
from app.timeutil import APP_TZ, audit_clock


SERVICE_PATH = Path("app/services/mobile_today.py")
ROUTE_PATH = Path("app/blueprints/mobile_today.py")

FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
TODAY = date(2026, 7, 23)


def _source(path):
    return path.read_text(encoding="utf-8")


def _code_only(path):
    """The module's executable source, with comments and docstrings removed.

    Guards below assert that certain tokens do NOT appear. Scanning raw text
    would let a prose mention of a forbidden construct ("never call
    `date.today()`") fail the guard, and would tempt the next author to delete the
    explanation instead of the construct. `ast.unparse` keeps real string literals
    - so a genuine `"dinlenme"` comparison still trips - while dropping the prose.
    """
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


def _tree(path):
    return ast.parse(_source(path))


def _imported_modules(path):
    modules = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ---------------------------------------------------------------------------
# The projection delegates; it does not decide
# ---------------------------------------------------------------------------
def test_today_projection_delegates_to_the_canonical_authorities():
    source = _source(SERVICE_PATH)

    # The canonical owners, called by name.
    assert "resolve_workout_state(" in source
    assert "get_active_plan(" in source
    assert "app_today()" in source
    assert "serialize_today_plan(" in source

    modules = _imported_modules(SERVICE_PATH)
    assert "app.services.workout_state" in modules
    assert "app.services.today_facts" in modules
    assert "app.timeutil" in modules


def test_projection_reads_no_fitness_table_of_its_own():
    """A direct model import would be the first step to a second authority.

    Today's facts must arrive through the resolver and the active-plan selector.
    The moment this module queries `PumpCheck`/`WorkoutLog`/`WorkoutSession`
    itself, mobile can disagree with web about the very same day.
    """
    modules = _imported_modules(SERVICE_PATH)
    assert "app.models" not in modules
    assert "app.extensions" not in modules

    source = _code_only(SERVICE_PATH)
    for table in ("PumpCheck", "WorkoutLog", "WorkoutSession", "TrainingPlan"):
        assert f"{table}.query" not in source
        assert f"{table}.query" not in _code_only(ROUTE_PATH)
    assert ".query" not in source
    assert "db.session" not in source


def assert_delegates_rest_and_completion(source):
    """The guard itself, factored out so its non-vacuity can be proven below.

    `is_rest_day` and `completed` must be *read off* the snapshot, never computed
    from a schedule string or a log count here.
    """
    assert "snapshot.is_rest_day" in source
    assert "snapshot.completed_today" in source
    # No local rest/completion vocabulary: no comparison against the plan's own
    # Turkish rest marker, and no locally assigned verdict.
    assert "dinlenme" not in source
    assert "is_rest_day =" not in source
    assert "completed =" not in source


def assert_derives_no_date(source):
    for forbidden in ("date.today()", "datetime.now(", "utcnow(",
                      "ZoneInfo(", "astimezone(APP_TZ)"):
        assert forbidden not in source, f"second clock authority: {forbidden}"
    assert "app_today()" in source and "app_now()" in source


def test_projection_contains_no_rest_day_or_completion_inference():
    """The two semantics PR3 is most likely to be tempted into re-deriving."""
    assert_delegates_rest_and_completion(_code_only(SERVICE_PATH))


def test_projection_derives_no_date_of_its_own():
    """The day must come from the canonical Istanbul clock only.

    `date.today()`, `datetime.now()` and `utcnow()` are all server-local or UTC
    days - each of them silently moves Today by up to a day for a user in
    Istanbul, which is precisely the bug `app/timeutil` exists to prevent.
    """
    assert_derives_no_date(_code_only(SERVICE_PATH))


def test_route_accepts_no_client_supplied_owner_or_day():
    """The route reads the principal and nothing else off the request.

    Any `request.args` / `request.get_json` here would reintroduce exactly the
    parameter the ownership invariant forbids.
    """
    route_source = _code_only(ROUTE_PATH)
    assert "g.mobile_user.id" in route_source
    for forbidden in ("request.args", "request.get_json", "request.form",
                      "request.headers", "request.values", "user_id="):
        assert forbidden not in route_source, f"client input reached the route: {forbidden}"

    # ...and the projection takes the owner positionally, with no other knobs.
    signature = inspect.signature(mobile_today.build_today)
    assert list(signature.parameters) == ["user_id"]


def test_route_is_thin_and_holds_no_domain_branching():
    """One call to the projection; the route never classifies state itself."""
    tree = _tree(ROUTE_PATH)
    view = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "today")
    calls = [node.func.attr for node in ast.walk(view)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert calls.count("build_today") == 1
    # No state vocabulary is spelled anywhere in the transport layer.
    route_source = _code_only(ROUTE_PATH)
    for token in ("rest_day", "no_plan", "completed", "scheduled"):
        assert token not in route_source


# ---------------------------------------------------------------------------
# Non-vacuity: the guards above must be able to fail
# ---------------------------------------------------------------------------
def test_the_delegation_guards_are_not_vacuous():
    """The guards must REJECT the mutations PR3 forbids, not merely pass today.

    Each mutation below is applied to a copy of the real source and fed to the
    same assertion helper the live guard uses. If a helper ever stops raising, it
    has gone blind and the architectural decision is no longer protected.
    """
    source = _code_only(SERVICE_PATH)

    # 1. A mobile-local rest-day inference, derived from the plan text.
    rest_inference = source.replace(
        "'is_rest_day': snapshot.is_rest_day",
        "'is_rest_day': today_plan['tip'] == 'dinlenme'")
    assert rest_inference != source, "mutation did not apply"
    with pytest.raises(AssertionError):
        assert_delegates_rest_and_completion(rest_inference)

    # 2. A locally computed completion verdict.
    completion_inference = source.replace(
        "'completed': snapshot.completed_today",
        "'completed': completed")
    assert completion_inference != source, "mutation did not apply"
    with pytest.raises(AssertionError):
        assert_delegates_rest_and_completion(completion_inference)

    # 3. The server's own naive clock instead of the canonical Istanbul day.
    naive_clock = source.replace("day = app_today()", "day = date.today()")
    assert naive_clock != source, "mutation did not apply"
    with pytest.raises(AssertionError):
        assert_derives_no_date(naive_clock)

    # ...and the unmutated source passes both, so the helpers are discriminating
    # rather than merely always-raising.
    assert_delegates_rest_and_completion(source)
    assert_derives_no_date(source)


# ---------------------------------------------------------------------------
# Zero provider calls
# ---------------------------------------------------------------------------
def test_no_provider_or_ai_module_is_reachable_from_the_today_read():
    for path in (SERVICE_PATH, ROUTE_PATH):
        modules = _imported_modules(path)
        for module in modules:
            assert not module.startswith("app.services.ai"), module
            assert not module.startswith("app.prompts"), module
            assert module not in {"openai", "anthropic", "boto3", "groq"}, module
        source = _code_only(path)
        for token in ("openai_client", "bedrock_client", "ai_gate",
                      "ai_pipeline", "ai_coach", "invoke_model"):
            assert token not in source, f"provider machinery in {path}: {token}"


def test_today_succeeds_while_every_provider_client_would_explode(
        client, make_user, monkeypatch):
    """The strongest available proof: make a provider call impossible, then read.

    If Today touched a provider - directly or through any layer it composes -
    this request would raise rather than return canonical state.
    """
    from app import extensions

    class _Detonator:
        def __getattr__(self, name):
            raise AssertionError(
                f"GET /api/v1/today invoked a provider client ({name})")

    monkeypatch.setattr(extensions, "openai_client", _Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", _Detonator())

    user = make_user("today-arch")
    _save_plan(user)
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))

    with audit_clock(FIXED_NOW):
        response = client.get(
            "/api/v1/today", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json["today"]["status"] == "scheduled_not_started"


# ---------------------------------------------------------------------------
# Cross-surface parity: mobile cannot disagree with web
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tip,complete", [
    ("antrenman", False), ("dinlenme", False), ("antrenman", True),
])
def test_mobile_today_agrees_with_the_web_canonical_facts(
        client, make_user, monkeypatch, tip, complete):
    """Compare shared domain facts, never rendered HTML.

    The web Today/Training surfaces read `resolve_workout_state` + the canonical
    active-plan selector. This asserts the mobile payload reports the same answer
    for the same persisted state, on the same canonical day.
    """
    from app.models import PumpCheck

    user = make_user(f"parity-{tip}-{complete}")
    _save_plan(user, tip)
    if complete:
        db.session.add(PumpCheck(
            user_id=user.id, valid=True, date_key=TODAY.isoformat(),
            created_at=datetime(2026, 7, 23, 9, 0)))
        db.session.commit()

    user_id = user.id
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))

    with audit_clock(FIXED_NOW):
        payload = client.get(
            "/api/v1/today",
            headers={"Authorization": "Bearer token"}).json["today"]
        # The web-side canonical reads, performed independently afterwards.
        web_snapshot = resolve_workout_state(user_id, today=TODAY)
        web_has_plan = get_active_plan(user_id) is not None

    assert payload["status"] == web_snapshot.primary_state
    assert payload["action"] == web_snapshot.action
    assert payload["workout"]["is_rest_day"] == web_snapshot.is_rest_day
    assert payload["workout"]["completed"] == web_snapshot.completed_today
    assert payload["workout"]["schedule_state"] == web_snapshot.schedule_state
    assert payload["plan"]["exists"] == web_has_plan
    assert payload["date"] == web_snapshot.today.isoformat()
    # The whole canonical envelope, not just the fields mobile happens to mirror.
    assert payload["state"] == web_snapshot.to_dict()


# ---------------------------------------------------------------------------
# Route registration lives inside the existing mobile gate
# ---------------------------------------------------------------------------
def test_today_is_registered_only_behind_the_mobile_api_feature_gate(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "0")
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    disabled = create_app()
    assert not any(rule.rule.startswith("/api/v1")
                   for rule in disabled.url_map.iter_rules())


def test_today_route_carries_the_shared_mobile_auth_decorator(app):
    view = app.view_functions["mobile_api.today"]
    assert getattr(view, "_require_mobile_auth", False) is True


def test_today_adds_no_parallel_ungated_route(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/v1/today" in rules
    # Exactly one Today surface on the versioned mobile namespace. Pre-existing
    # web routes that happen to end in /today (meal log, activity) are a separate,
    # cookie-authenticated surface and are deliberately left alone.
    # Exactly the Today surfaces the mobile namespace is supposed to have: the
    # one PR3 adds, plus the pre-existing nutrition diary day. A third would mean
    # someone opened a competing Today read.
    mobile_today_rules = {rule for rule in rules
                          if rule.startswith("/api/v1") and "today" in rule}
    assert mobile_today_rules == {
        "/api/v1/today", "/api/v1/nutrition/diary/today"}


def test_today_endpoint_belongs_to_the_single_mobile_blueprint(app):
    rule = next(r for r in app.url_map.iter_rules() if r.rule == "/api/v1/today")
    assert rule.endpoint.split(".")[0] == "mobile_api"
    assert today_route.bp.name == "mobile_api"


# ---------------------------------------------------------------------------
# Production truth: no fixture, sample or placeholder Today (Sprint 12 PR2A)
# ---------------------------------------------------------------------------
def test_the_production_today_has_no_fixture_or_placeholder_fallback():
    """PR2A removed fabricated mobile fitness state; PR3 must not re-add it.

    A "friendly" default - a sample workout when the plan is missing, a demo day
    when a read fails - is exactly the class of lie PR2A deleted. The failure path
    is a 5xx, and the empty path is an honest empty state, so neither needs a
    literal to fall back to.
    """
    source = _code_only(SERVICE_PATH) + _code_only(ROUTE_PATH)
    for token in ("fixture", "sample", "demo", "placeholder", "dummy",
                  "seed_", "FALLBACK", "fallback", "example_"):
        assert token not in source, f"production Today must not ship {token!r}"
    # No canned plan vocabulary. Reading a key off the canonical projection is
    # fine (`today_plan['egzersizler']` is how the count is taken); *values* are
    # not - a day type or a whole program literal can only be here if someone
    # hard-coded a workout or re-implemented rest-day inference.
    for literal in ("antrenman", "dinlenme", '"program"', "'program'"):
        assert literal not in source, f"hard-coded plan content: {literal!r}"


# ---------------------------------------------------------------------------
# Query budget: Today is the highest-frequency read in the app
# ---------------------------------------------------------------------------
def _statements_for(client, user, monkeypatch):
    from sqlalchemy import event

    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    seen = []
    engine = db.engine

    def _record(conn, cursor, statement, params, context, many):
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        with audit_clock(FIXED_NOW):
            response = client.get(
                "/api/v1/today", headers={"Authorization": "Bearer token"})
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert response.status_code == 200
    return seen


@pytest.mark.parametrize("with_plan", [True, False])
def test_today_costs_a_constant_bounded_number_of_queries(
        client, make_user, monkeypatch, with_plan):
    """One read per canonical authority, and the same count in every state.

    A count that grows with plan content means an N+1 crept into the projection;
    a count that differs between the plan and no-plan paths usually means a second
    load of the same row. Both are regressions this endpoint cannot afford.
    """
    user = make_user(f"budget-{with_plan}")
    if with_plan:
        _save_plan(user)

    statements = _statements_for(client, user, monkeypatch)

    assert len(statements) <= 5, statements
    plan_reads = [s for s in statements if "FROM training_plan" in s]
    assert len(plan_reads) <= 1, f"the active plan is loaded twice: {plan_reads}"


# ---------------------------------------------------------------------------
# PR3 is a read projection: no schema, no persistence
# ---------------------------------------------------------------------------
def test_today_introduces_no_model_and_no_migration():
    source = _code_only(SERVICE_PATH) + _code_only(ROUTE_PATH)
    for write in ("db.Model", "session.add", "session.commit", "session.flush",
                  "op.create_table", "sa.Column"):
        assert write not in source, f"PR3 must stay a read projection: {write}"

    migrations = {p.name for p in Path("migrations/versions").glob("*.py")}
    assert not any("today" in name for name in migrations)


def _save_plan(user, tip="antrenman"):
    program = [{
        "gun": name,
        "tip": tip if name == WEEKDAYS[TODAY.weekday()] else "dinlenme",
        "odak": "Genel", "sure_dk": 40, "tahmini_kalori": 300,
        "egzersizler": [],
    } for name in WEEKDAYS]
    db.session.add(TrainingPlan(
        user_id=user.id, plan_data=json.dumps({"program": program}), score=5))
    db.session.commit()
