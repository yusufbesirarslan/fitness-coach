"""Architecture guards for the native Progress summary (LP-04, spec J1).

LP-04's central rule is that `GET /api/v1/progress/summary` **transports** the
canonical `app/services/progress_summary` read model and must never **become** a
second Progress authority. The contract tests in
tests/test_mobile_progress_summary_api.py would keep passing if someone copied
the builder into the route and let it drift later; these would not:

  * the route delegates to the one builder and the one projection, by behaviour
    (a spy) and by dependency (its imports);
  * the route owns no threshold, window, date, state literal or fallback;
  * web and mobile are fed by the same builder, so they agree byte for byte;
  * the route sits inside the shared mobile auth + feature gate;
  * no provider/LLM machinery is reachable from a Progress summary read.

    python -m pytest tests/test_mobile_progress_summary_architecture.py -v
"""
import ast
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import create_app
from app.blueprints import mobile_progress as progress_route
from app.extensions import db
from app.models import WeeklyCheckIn, WorkoutLog
from app.services import mobile_auth
from app.services.progress_summary import (
    BodySummary,
    ConsistencySummary,
    PerformanceSummary,
    ProgressSummary,
    ProgressWindow,
    Trajectory,
    WeekSummary,
    progress_summary_payload,
)
from app.timeutil import APP_TZ, audit_clock


ROUTE_PATH = Path("app/blueprints/mobile_progress.py")
PATH = "/api/v1/progress/summary"
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
TODAY = date(2026, 7, 23)


def _code_only(path):
    """Executable source with docstrings removed (see test_mobile_today_architecture)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


def _imports(path):
    imported = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.setdefault(alias.name, set())
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.setdefault(node.module, set()).update(
                alias.name for alias in node.names)
    return imported


def _as(monkeypatch, user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    return {"Authorization": "Bearer opaque-progress-access"}


def _seed(user):
    for offset, weight in ((14, 79.4), (7, 79.0), (0, 78.4)):
        day = TODAY - timedelta(days=offset)
        db.session.add(WeeklyCheckIn(
            user_id=user.id, weight=weight, yogunluk=3,
            created_at=datetime(day.year, day.month, day.day, 9)))
    for offset in range(0, 28, 3):
        day = TODAY - timedelta(days=offset)
        db.session.add(WorkoutLog(
            user_id=user.id, exercise_name="Squat", sets=3, reps=5,
            weight_kg=60.0 - offset * 0.2, volume=900.0 - offset * 5,
            created_at=datetime(day.year, day.month, day.day, 9)))
    db.session.commit()


# ---------------------------------------------------------------------------
# The route delegates to the one canonical builder and projection
# ---------------------------------------------------------------------------
def test_route_serializes_whatever_the_canonical_builder_returns(
        client, make_user, monkeypatch):
    """Behavioural proof of delegation.

    The builder is replaced by a spy returning a summary no real history could
    produce. If the route computed anything itself - or called a copy of the
    builder - the response would not be this summary's canonical projection.
    """
    user = make_user("progress-spy")
    sentinel = ProgressSummary(
        window=ProgressWindow(weeks=4, start=date(2031, 1, 3),
                              end=date(2031, 1, 30), timezone="Europe/Istanbul"),
        trajectory=Trajectory(state="needs_attention", reason="deload"),
        body=BodySummary(status="partial", current_weight_kg=64.2),
        performance=PerformanceSummary(state="deload", volume_trend="down",
                                       strength_trend="down", next_signal="deload"),
        consistency=ConsistencySummary(state="inconsistent", active_weeks=1,
                                       analyzed_weeks=4, sessions=7),
        weekly=(WeekSummary(start=date(2031, 1, 3), sessions=7, active=True,
                            volume_kg=12.5),),
    )
    calls = []

    def _spy(user_id, **kwargs):
        calls.append((user_id, kwargs))
        return sentinel

    monkeypatch.setattr(progress_route, "build_progress_summary", _spy)
    response = client.get(PATH, headers=_as(monkeypatch, user))

    assert response.status_code == 200
    assert response.get_json() == progress_summary_payload(sentinel)
    # Exactly one call, for the principal, with no caller-chosen window or day.
    assert calls == [(user.id, {})]


def test_route_uses_the_builder_and_projection_the_web_route_uses():
    from app.blueprints import tracking
    from app.services import progress_summary

    assert progress_route.build_progress_summary is progress_summary.build_progress_summary
    assert progress_route.progress_summary_payload is progress_summary.progress_summary_payload
    assert tracking.build_progress_summary is progress_route.build_progress_summary
    assert tracking.progress_summary_payload is progress_route.progress_summary_payload


def test_route_imports_only_the_transport_and_the_canonical_service():
    """J1: "the blueprint imports only the service and payload"."""
    imports = _imports(ROUTE_PATH)
    assert imports == {
        "flask": {"current_app", "g", "jsonify"},
        "app.blueprints.mobile_api": {"bp", "mobile_error"},
        "app.extensions": {"db"},
        "app.mobile_auth_middleware": {"require_mobile_auth"},
        "app.observability": {"current_request_id"},
        "app.services.progress_summary": {
            "build_progress_summary", "progress_summary_payload"},
    }


def test_route_owns_no_progress_rule_of_its_own():
    """No threshold, window, date, state vocabulary, query or fallback.

    Any of these appearing in the route is the first line of a second Progress
    authority for mobile only.
    """
    source = _code_only(ROUTE_PATH)
    for token in (
            # Reads and dates the service owns.
            ".query", "select(", "app_today", "date.today", "datetime",
            "timedelta", "APP_TZ", "end_day", "weeks", "SUMMARY_WEEKS",
            "CONTRACT_VERSION", "contract_version",
            # Fabrication.
            "fixture", "sample", "placeholder", "fallback", "default",
            # Request input.
            "request.", ".args", "get_json"):
        assert token not in source, f"mobile Progress route owns {token!r}"

    # The bounded vocabularies the service owns never appear as a value here.
    from app.services import progress_summary as svc
    vocabulary = set(svc.TRAJECTORY_STATES + svc.PERFORMANCE_STATES
                     + svc.BODY_STATUSES + svc.CONSISTENCY_STATES
                     + ("up", "flat", "down"))
    literals = {node.value for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert literals & vocabulary == set()
    numbers = {node.value for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Constant)
               and type(node.value) in (int, float)}
    assert numbers == {503}, "the only number in the route is its status code"
    # The single delegation, serialized through the canonical projection.
    assert source.count("build_progress_summary(g.mobile_user.id)") == 1
    assert "progress_summary_payload(summary)" in source


def test_no_second_progress_summary_builder_exists_for_mobile():
    """The canonical builder is called from exactly the two transports."""
    callers = sorted(
        str(path) for path in Path("app").rglob("*.py")
        if "build_progress_summary(" in _code_only(path)
        and not str(path).startswith("app/services/progress_summary"))
    assert callers == ["app/blueprints/mobile_progress.py",
                       "app/blueprints/tracking.py"]
    for path in Path("app").rglob("*mobile*progress*.py"):
        assert path == ROUTE_PATH, f"parallel mobile Progress module: {path}"


# ---------------------------------------------------------------------------
# Web parity: mobile cannot disagree with web
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seeded", [False, True])
def test_mobile_and_web_serve_the_same_summary(
        client, make_user, login, monkeypatch, seeded):
    user = make_user(f"progress-parity-{seeded}", weight=78.4, target_weight=75.0)
    if seeded:
        _seed(user)
    login(user.username)

    with audit_clock(FIXED_NOW):
        web = client.get("/api/progress/summary")
        mobile = client.get(PATH, headers=_as(monkeypatch, user))

    assert web.status_code == mobile.status_code == 200
    assert mobile.get_json() == web.get_json()
    # Same data, each surface keeps its own documented cache header.
    assert web.headers["Cache-Control"] == "private, no-store"
    assert mobile.headers["Cache-Control"] == "no-store"


# ---------------------------------------------------------------------------
# Zero provider calls
# ---------------------------------------------------------------------------
def test_summary_succeeds_while_every_provider_client_would_explode(
        client, make_user, monkeypatch):
    from app import extensions

    class _Detonator:
        def __getattr__(self, name):
            raise AssertionError(
                f"GET {PATH} invoked a provider client ({name})")

    monkeypatch.setattr(extensions, "openai_client", _Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", _Detonator())

    user = make_user("progress-noai", weight=78.4, target_weight=75.0)
    _seed(user)
    with audit_clock(FIXED_NOW):
        response = client.get(PATH, headers=_as(monkeypatch, user))
    assert response.status_code == 200
    assert response.get_json()["contract_version"] == 1


def test_no_provider_module_is_imported_by_the_route():
    for module in _imports(ROUTE_PATH):
        assert not module.startswith("app.services.ai"), module
        assert not module.startswith("app.prompts"), module
        assert module not in {"openai", "anthropic", "boto3", "groq"}, module


# ---------------------------------------------------------------------------
# Registration: inside the shared mobile auth + feature gate
# ---------------------------------------------------------------------------
def test_route_is_registered_only_behind_the_mobile_feature_gate(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "0")
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    disabled = create_app()
    assert PATH not in {rule.rule for rule in disabled.url_map.iter_rules()}


def test_route_is_an_authenticated_get_on_the_single_mobile_blueprint(app):
    rules = [r for r in app.url_map.iter_rules() if r.rule == PATH]
    assert len(rules) == 1
    rule = rules[0]
    assert rule.endpoint == "mobile_api.progress_summary"
    assert rule.methods - {"HEAD", "OPTIONS"} == {"GET"}
    assert progress_route.bp.name == "mobile_api"
    view = app.view_functions[rule.endpoint]
    assert getattr(view, "_require_mobile_auth", False) is True

    from app.mobile_auth_middleware import _MOBILE_PREAUTH_ENDPOINTS
    assert rule.endpoint not in _MOBILE_PREAUTH_ENDPOINTS


def test_exactly_one_progress_surface_on_the_mobile_namespace(app):
    mobile_progress_rules = {
        rule.rule for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/v1") and "progress" in rule.rule}
    # LP16-B adds the weekly check-in pair (its own transport module, over the
    # LP16-A write authority) - still exactly one Progress SUMMARY surface.
    assert mobile_progress_rules == {PATH, "/api/v1/progress/check-ins"}
