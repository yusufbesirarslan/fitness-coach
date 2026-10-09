"""LP16-A architecture gate: one WeeklyCheckIn persistence authority.

Source-level guards (AST) plus a runtime delegation spy. They fail if a
transport regains check-in persistence, if the service grows a Flask/request,
AI/provider, Coach/Progress/plan dependency, if a helper hides a commit, or if
the service starts writing authorities LP16-A must never touch.

    python -m pytest tests/test_lp16a_weekly_checkin_architecture.py -q
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "app/services/weekly_checkin"
SERVICE_FILES = sorted(p.relative_to(ROOT).as_posix() for p in PACKAGE.glob("*.py"))
TRACKING = "app/blueprints/tracking.py"
WRITER_VIEWS = ("checkin", "update_weight")

# Everything the package may import. Anything else (flask, flask_login,
# app.blueprints, ai_*, coach*, mobile_*, progress_*, bedrock/openai/anthropic,
# training/nutrition plans) is a boundary break.
ALLOWED_IMPORTS = {
    "__future__", "enum", "dataclasses", "typing", "json",
    "sqlalchemy.exc",
    "app.extensions", "app.models", "app.services", "app.timeutil",
    "app.services.calculations",
    ".", ".models", ".queries", ".service",
}
ALLOWED_APP_SERVICES = {"account_profile"}
ALLOWED_MODELS = {"User", "UserSession", "WeeklyCheckIn"}

FORBIDDEN_NAMES = {
    "request", "current_user", "g", "session_transaction", "current_app",
    "jsonify", "t", "generate_checkin_feedback", "_claim_quest",
    "complete_quest_for_user",
}
FORBIDDEN_ATTRS = {"target_weight", "plan_data", "training_plan", "nutrition_plan"}

# The only functions allowed to end a transaction, by file.
TRANSACTION_OWNERS = {
    "app/services/weekly_checkin/service.py": {
        "claim_submission", "commit_full_checkin", "record_legacy_weight_update",
    },
}


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _module_name(node):
    if isinstance(node, ast.ImportFrom):
        return "." * node.level + (node.module or "")
    return None


def _python_sources():
    for base in ("app", "fitx_mcp"):
        for path in sorted((ROOT / base).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix()


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_package_imports_only_canonical_helpers():
    for path in SERVICE_FILES:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in ALLOWED_IMPORTS, (path, alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = _module_name(node)
                assert module in ALLOWED_IMPORTS, (path, module)
                names = {alias.name for alias in node.names}
                if module == "app.services":
                    assert names <= ALLOWED_APP_SERVICES, (path, names)
                if module == "app.models":
                    assert names <= ALLOWED_MODELS, (path, names)


def test_package_references_no_request_globals_ai_or_web_orchestration():
    for path in SERVICE_FILES:
        tree = _tree(path)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & FORBIDDEN_NAMES, (path, names & FORBIDDEN_NAMES)
        assert not attrs & FORBIDDEN_ATTRS, (path, attrs & FORBIDDEN_ATTRS)
        assert "_get_current_object" not in attrs, path


def test_only_the_named_transaction_owners_commit_or_roll_back():
    for path in SERVICE_FILES:
        tree = _tree(path)
        owners = TRANSACTION_OWNERS.get(path, set())
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            calls = {n.func.attr for n in ast.walk(func)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            ends = calls & {"commit", "rollback"}
            if ends:
                assert func.name in owners, (path, func.name, ends)
        if not owners:
            calls = {n.func.attr for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            assert not calls & {"commit", "rollback"}, path


def test_weekly_checkin_rows_are_constructed_only_by_the_service():
    constructing = set()
    for path in _python_sources():
        for node in ast.walk(_tree(path)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "WeeklyCheckIn"):
                constructing.add(path)
    assert constructing == {"app/services/weekly_checkin/service.py"}, constructing


def test_body_weight_and_derived_targets_are_assigned_only_by_the_primitive():
    tree = _tree("app/services/weekly_checkin/service.py")
    assigning = set()
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(func):
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                    and node.attr in {"bmr", "tdee", "target_calories"}):
                assigning.add(func.name)
    assert assigning == {"apply_body_weight"}, assigning


@pytest.mark.parametrize("view", WRITER_VIEWS)
def test_writer_views_hold_no_persistence_logic(view):
    func = _function(_tree(TRACKING), view)
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"WeeklyCheckIn", "UserSession"}, view
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"with_for_update", "commit", "rollback",
                                     "add", "query"}, (view, node.attr)
            if isinstance(node.ctx, ast.Store):
                assert node.attr not in {"weight", "bmr", "tdee", "target_calories",
                                         "target_weight", "response_snapshot"}, view
        if isinstance(node, ast.Name):
            assert node.id not in {"db", "WeeklyCheckIn", "UserSession"}, (view, node.id)


def test_tracking_has_no_private_weight_helper_left():
    tree = _tree(TRACKING)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_apply_weight_to_profile" not in defined
    imported = {alias.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for alias in n.names}
    assert not imported & {"calculate_bmr", "calculate_tdee", "calculate_target"}


def test_legacy_weight_update_has_exactly_one_caller():
    callers = set()
    for path in _python_sources():
        if path.startswith("app/services/weekly_checkin/"):
            continue
        for node in ast.walk(_tree(path)):
            if (isinstance(node, ast.Attribute)
                    and node.attr == "record_legacy_weight_update"):
                callers.add(path)
    assert callers == {TRACKING}, callers
    func = _function(_tree(TRACKING), "update_weight")
    assert any(isinstance(n, ast.Attribute) and n.attr == "record_legacy_weight_update"
               for n in ast.walk(func))


def test_web_feedback_stays_in_the_route_between_context_and_staging():
    """AI call position: after the claim + context reads, before staging."""
    func = _function(_tree(TRACKING), "checkin")
    order = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", None))
            if name in {"claim_submission", "load_context",
                        "generate_checkin_feedback", "stage_full_checkin",
                        "commit_full_checkin"}:
                order.append((node.lineno, name))
    names = [name for _line, name in sorted(order)]
    assert names[:4] == ["claim_submission", "load_context",
                         "generate_checkin_feedback", "stage_full_checkin"]
    assert names.count("commit_full_checkin") == 2


# -- runtime delegation spy --------------------------------------------------

def test_web_routes_delegate_to_the_canonical_service(client, auth_user, monkeypatch):
    from app.blueprints import tracking
    from app.services import weekly_checkin

    calls = []
    for name in ("claim_submission", "load_context", "stage_full_checkin",
                 "commit_full_checkin", "record_legacy_weight_update"):
        original = getattr(weekly_checkin, name)

        def _spy(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(weekly_checkin, name, _spy)
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *a, **k: "fb")

    assert client.post("/checkin", json={"weight": 79},
                       headers={"Idempotency-Key": "lp16a-spy-0001"}).status_code == 200
    assert calls == ["claim_submission", "load_context", "stage_full_checkin",
                     "commit_full_checkin"]
    calls.clear()
    assert client.post("/checkin", json={"weight": 79}).status_code == 200
    assert calls == ["load_context", "stage_full_checkin", "commit_full_checkin"]
    calls.clear()
    assert client.post("/update-weight", json={"weight": 78}).status_code == 200
    assert calls == ["record_legacy_weight_update"]
