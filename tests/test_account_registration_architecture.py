"""One registration authority, two transports (LP-01 architecture gate).

Source-level guards that fail if registration/verification provider logic is
duplicated again, if a transport starts calling the provider primitives
directly, or if the canonical service grows a presentation dependency.

    python -m pytest tests/test_account_registration_architecture.py -v
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "app/services/account_registration.py"
WEB = "app/blueprints/auth.py"
MOBILE = "app/blueprints/mobile_registration.py"

# The registration primitives of the provider adapter. Only the canonical
# service may call them.
PRIMITIVES = frozenset({"sign_up", "confirm_sign_up", "resend_code"})

PRESENTATION_NAMES = frozenset({
    "request", "session", "flash", "redirect", "jsonify", "render_template",
    "url_for", "make_response", "abort", "g",
})


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _primitive_calls(tree):
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in PRIMITIVES
                and isinstance(node.value, ast.Name)
                and node.value.id == "cognito_service"):
            found.append(node.attr)
        if isinstance(node, ast.ImportFrom) and node.module and (
                node.module.endswith("cognito_service")):
            found.extend(a.name for a in node.names if a.name in PRIMITIVES)
    return found


def _python_sources():
    for base in ("app", "fitx_mcp"):
        for path in sorted((ROOT / base).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix()


def test_only_the_canonical_service_calls_registration_primitives():
    callers = {path for path in _python_sources()
               if path != "app/services/cognito_service.py"
               and _primitive_calls(_tree(path))}
    assert callers == {SERVICE}


def test_the_service_uses_every_primitive():
    assert set(_primitive_calls(_tree(SERVICE))) == PRIMITIVES


def test_both_transports_delegate_to_the_canonical_service():
    for relative in (WEB, MOBILE):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for function in ("register_account", "confirm_account",
                         "resend_confirmation"):
            assert f"account_registration.{function}(" in source, (
                relative, function)


def test_the_service_has_no_presentation_or_transport_dependency():
    tree = _tree(SERVICE)
    flask_names, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            if node.module == "flask":
                flask_names.update(alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    # Logging only: no request, session, response or template machinery.
    assert flask_names == {"current_app"}
    assert flask_names & PRESENTATION_NAMES == set()
    assert not any(module.startswith(("app.blueprints", "app.mobile",
                                      "flask_login"))
                   for module in modules)


def _referenced_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


SESSION_AUTHORITIES = frozenset({
    "login_user", "session_store", "mobile_auth", "mobile_credentials",
    "MobileAuthSession", "MobileAccessCredential", "MobileRefreshCredential",
    "authenticate", "initiate_auth", "refresh_tokens", "generate_credential",
})


def test_the_service_issues_no_session():
    """No import of, or reference to, any session/credential authority."""
    assert _referenced_names(_tree(SERVICE)) & SESSION_AUTHORITIES == set()


def test_mobile_routes_live_on_the_canonical_blueprint(app):
    rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()
             if rule.rule.startswith("/api/v1/auth/")}
    for path, endpoint in (
            ("/api/v1/auth/register", "mobile_api.register"),
            ("/api/v1/auth/verify", "mobile_api.verify"),
            ("/api/v1/auth/verify/resend", "mobile_api.verify_resend")):
        assert rules[path] == endpoint
    assert not any(rule.rule.startswith(("/api/v2", "/api/mobile", "/native"))
                   for rule in app.url_map.iter_rules())


def test_mobile_transport_never_reads_provider_text():
    source = (ROOT / MOBILE).read_text(encoding="utf-8")
    assert "provider_message" not in source
    assert ".detail" not in source
    assert "cognito_service" not in source
