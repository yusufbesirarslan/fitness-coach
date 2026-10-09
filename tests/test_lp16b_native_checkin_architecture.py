"""LP16-B architecture gates: transport only, one write authority, no AI.

    python -m pytest tests/test_lp16b_native_checkin_architecture.py -q
"""
import ast
import json
from pathlib import Path

import pytest

TRANSPORT = Path("app/blueprints/mobile_weekly_checkin.py")
PACKAGE = Path("app/services/mobile_weekly_checkin")
CONTRACT = PACKAGE / "contract.py"
SUBMISSION = PACKAGE / "submission.py"
HISTORY = PACKAGE / "history.py"
PATH = "/api/v1/progress/check-ins"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _code_only(path):
    """Executable source with docstrings removed."""
    tree = _tree(path)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


def _names(path):
    return {node.id for node in ast.walk(_tree(path))
            if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(_tree(path))
        if isinstance(node, ast.Attribute)}


def _imports(path):
    imported = {}
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.setdefault(alias.name, set())
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            imported.setdefault(module, set()).update(a.name for a in node.names)
    return imported


def _package_files():
    return sorted(PACKAGE.glob("*.py"))


def _calls(path):
    names = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


# -- Transport: HTTP only ------------------------------------------------------
def test_transport_imports_only_http_plumbing_and_the_native_package():
    assert _imports(TRANSPORT) == {
        "flask": {"current_app", "g", "jsonify", "request"},
        "flask_limiter.errors": {"RateLimitExceeded"},
        "app.blueprints.mobile_api": {"bp", "mobile_error"},
        "app.config": {"CHECKIN_WRITE_RATELIMIT"},
        "app.extensions": {"db", "limiter"},
        "app.mobile_auth_middleware": {"require_mobile_auth"},
        "app.observability": {"current_request_id"},
        "app.services": {"meal_idempotency", "mobile_weekly_checkin"},
    }


def test_transport_owns_no_persistence_parsing_or_owner_input():
    source = _code_only(TRANSPORT)
    for token in ("WeeklyCheckIn", "UserSession", ".query", "with_for_update",
                  "commit(", ".add(", "get_json", ".json", ".args", ".form",
                  "user_id", "weight", "yogunluk", "evet", "fingerprint(",
                  "app_today", "timedelta"):
        assert token not in source, f"transport owns {token!r}"
    # The only owner expressions are the Bearer principal.
    assert source.count("g.mobile_user") == 3


def test_routes_are_bearer_gets_and_posts_on_the_single_mobile_blueprint(app):
    from app.mobile_auth_middleware import _MOBILE_PREAUTH_ENDPOINTS

    rules = {tuple(sorted(r.methods - {"HEAD", "OPTIONS"})): r
             for r in app.url_map.iter_rules() if r.rule == PATH}
    assert set(rules) == {("GET",), ("POST",)}
    for rule in rules.values():
        assert rule.endpoint.startswith("mobile_api.")
        assert getattr(app.view_functions[rule.endpoint],
                       "_require_mobile_auth", False) is True
        assert rule.endpoint not in _MOBILE_PREAUTH_ENDPOINTS


# -- Native package: framework-free, provider-free, no legacy parser ----------
@pytest.mark.parametrize("path", _package_files(), ids=lambda p: p.name)
def test_native_package_has_no_flask_request_ai_or_legacy_parser(path):
    for module in _imports(path):
        assert not module.startswith("flask"), (path, module)
        assert not module.startswith(("app.services.ai", "app.prompts",
                                      "app.blueprints")), (path, module)
        assert module not in {"openai", "anthropic", "boto3",
                              "app.services.validators"}, (path, module)
    assert _names(path) & {"request", "current_user", "g", "logger",
                           "current_app", "print", "_to_int", "_parse_weight",
                           "generate_checkin_feedback", "tracking"} == set(), path


def test_history_is_read_only():
    calls = set(_calls(HISTORY))
    assert calls & {"add", "add_all", "delete", "flush", "commit", "rollback",
                    "merge", "with_for_update", "stage_full_checkin",
                    "commit_full_checkin", "claim_submission"} == set()


def test_submission_composes_the_lp16a_primitives_in_order():
    """claim → reload owner → context → (clock) → stage → commit."""
    order = [name for name in _calls(SUBMISSION) if name in {
        "claim_submission", "reload_locked_owner", "load_context",
        "server_checked_in_at", "stage_full_checkin", "commit_full_checkin"}]
    assert order == ["claim_submission", "reload_locked_owner", "load_context",
                     "server_checked_in_at", "stage_full_checkin",
                     "commit_full_checkin"]
    source = _code_only(SUBMISSION)
    for token in ("WeeklyCheckIn", "db.", ".weight =", "apply_body_weight",
                  "record_legacy_weight_update", "target_weight"):
        assert token not in source, token
    assert "coach_feedback=None" in source and "note=None" in source


def test_the_wire_vocabulary_and_mapping_are_closed():
    from app.services import mobile_weekly_checkin as native

    assert native.FIELDS == {"weight_kg", "training_intensity", "fatigue",
                             "sleep_quality", "nutrition_adherence",
                             "progressive_overload"}
    assert native.OVERLOAD_WIRE_TO_STORED == {
        "yes": "evet", "partial": "kismen", "no": "hayir"}
    assert native.OVERLOAD_STORED_TO_WIRE == {
        "evet": "yes", "kismen": "partial", "hayir": "no"}
    assert native.FINGERPRINT_DOMAIN == "native.v1"


def test_parser_never_defaults_or_coerces_like_the_web_parser():
    from app.services import mobile_weekly_checkin as native

    full = {"weight_kg": 70, "training_intensity": 3, "fatigue": 3,
            "sleep_quality": 3, "nutrition_adherence": 3,
            "progressive_overload": "no"}
    for field in full:
        partial = {k: v for k, v in full.items() if k != field}
        with pytest.raises(native.InvalidRequest):
            native.parse_request(json.dumps(partial).encode(), is_json=True)
    with pytest.raises(native.InvalidRequest):
        native.parse_request(json.dumps({**full, "fatigue": True}).encode(),
                             is_json=True)
    with pytest.raises(native.InvalidValue):
        native.parse_request(json.dumps({**full, "fatigue": 9}).encode(),
                             is_json=True)
    parsed = native.parse_request(json.dumps(full).encode(), is_json=True)
    assert type(parsed.weight_kg) is float and parsed.weight_kg == 70.0


def test_weekly_checkin_service_stays_provider_and_progress_free():
    """LP16-A's boundary still holds after the additive ``checked_in_at``."""
    for path in Path("app/services/weekly_checkin").glob("*.py"):
        for module in _imports(path):
            assert not module.startswith(("flask", "app.services.ai",
                                          "app.services.progress",
                                          "app.services.mobile")), (path, module)


def test_only_the_lp16a_service_constructs_weekly_check_in_rows():
    offenders = []
    for root in (Path("app"), Path("fitx_mcp")):
        for path in root.rglob("*.py"):
            for node in ast.walk(_tree(path)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "WeeklyCheckIn"):
                    offenders.append(str(path))
    assert sorted(set(offenders)) == ["app/services/weekly_checkin/service.py"]


def test_transport_logs_only_fixed_events_request_ids_and_types():
    for node in ast.walk(_tree(TRANSPORT)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"info", "warning", "error", "exception",
                                       "debug"}):
            fmt, *args = node.args
            assert isinstance(fmt, ast.Constant)
            assert fmt.value.startswith("mobile_checkin event=")
            for arg in args:
                text = ast.unparse(arg)
                assert text in {"current_request_id()", "event",
                                "type(error).__name__",
                                "'created' if created else 'replayed'"}, text
