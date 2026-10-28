"""Structural gates for MON-02-BE-03A noninterference."""
import ast
from pathlib import Path

from app.feature_flags import FLAGS_BY_KEY

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "app/services/ai_allowances"
# Financial writes still belong only to service.py; approved domain seams use
# execution.py. Native Training keeps exactly its original allowance adapter.
ALLOWED_IMPORTERS = {
    "app/__init__.py", "app/config.py", "app/blueprints/mobile_ai_allowances.py", "app/blueprints/mobile_menu.py",
    "app/blueprints/coach.py", "app/cli.py", "app/services/mobile_training_generation/allowance.py",
    "app/services/ai_coach.py", "app/services/ai_nutrition.py", "app/services/ai_pipeline.py",
    "app/services/ai_provider_call.py", "app/services/menu_analysis.py", "app/services/menu_extract.py",
    "app/services/memory_manager.py", "app/services/mobile_pump_checks/service.py",
    "app/services/mobile_pump_check_comparisons/service.py", "app/services/nutrition_native/plan.py",
    "app/services/nutrition_plan_generation.py", "app/services/premium.py",
    "app/services/training_generation/service.py", "app/services/training_plan_replacement/generation.py",
    "app/services/training_plan_replacement/service.py", "app/services/plan_mutation/service.py",
}


def _python(root):
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, []


def test_only_qualified_domain_seams_import_allowance_execution():
    importers = set()
    for path in _python(ROOT / "app") + _python(ROOT / "fitx_mcp"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("app/services/ai_allowances/"):
            continue
        for module, names in _imports(ast.parse(path.read_text(encoding="utf-8"))):
            if module.startswith("app.services.ai_allowances") or (
                    module == "app.services" and "ai_allowances" in names):
                importers.add(rel)
    assert importers == ALLOWED_IMPORTERS


def test_legacy_quota_shim_uses_execution_readiness_and_never_writes_ledger():
    source = (ROOT / "app/services/premium.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "from app.services.ai_allowances import execution" in source
    assert not any(module.endswith("ai_allowances.service") for module, _ in _imports(tree))
    assert "AiAllowanceBucket" not in source and "AiUsageReservation" not in source


def test_ledger_never_reads_legacy_premium_env_or_logs():
    banned_names = {"is_premium", "user_metadata", "environ", "getenv", "logger", "logging", "print"}
    for path in _python(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        banned = banned_names - ({"logger"} if path.name == "execution.py" else set())
        assert not (banned & (names | attrs)), path.name
        assert not any(m in ("os", "logging") for m, _ in _imports(tree)), path.name


def test_no_policy_or_quantity_is_installed_by_application_code():
    """Only tests construct policies; the source installer stays empty and dark."""
    constructors = []
    for path in _python(ROOT / "app"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
                    and getattr(node.func, "id", getattr(node.func, "attr", None)) in (
                        "AllowancePolicy", "ScopePolicy")):
                constructors.append(path.relative_to(ROOT).as_posix())
    assert constructors == []
    config = ast.parse((ROOT / "app/config.py").read_text(encoding="utf-8"))
    writes = [node for node in ast.walk(config) if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                      and t.slice.value == "AI_ALLOWANCE_POLICY" for t in node.targets)]
    assert len(writes) == 1
    value = writes[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
    assert value.func.id == "installed_policy" and not value.args and not value.keywords
    # The only source installer has no installed values or external channel.
    launch = ast.parse((PACKAGE / "launch_policy.py").read_text(encoding="utf-8"))
    approved = [n for n in launch.body if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name) and n.target.id == "APPROVED_POLICY"]
    assert len(approved) == 1 and isinstance(approved[0].value, ast.Constant)
    assert approved[0].value.value is None
    from app.services.ai_allowances.launch_policy import installed_policy
    assert installed_policy() is None


def test_rollout_flag_is_registered_dark():
    flag = FLAGS_BY_KEY["AI_ALLOWANCE_LEDGER_ENABLED"]
    assert flag.default is False and flag.lifecycle == "shipped_dark"
    assert set(flag.depends_on) == {"MOBILE_AUTH_ENABLED", "APPLE_SUBSCRIPTIONS_ENABLED"}


def test_route_is_flag_gated_outside_authentication():
    tree = ast.parse((ROOT / "app/blueprints/mobile_ai_allowances.py").read_text(encoding="utf-8"))
    view = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "ai_allowances_read")
    decorators = [ast.unparse(d) for d in view.decorator_list]
    assert decorators == ["bp.get('/ai/allowances')", "_rollout_gated", "require_mobile_auth"]
    reads = {n.attr for n in ast.walk(view) if isinstance(n, ast.Attribute)}
    assert not reads & {"args", "json", "form", "headers", "values", "get_json", "cookies"}
