"""One recovery authority, two transports (LP-02 architecture gate).

Source-level guards that fail if password-recovery provider logic is
duplicated again, if a transport starts calling the provider primitives or the
session-revocation sweep directly, or if the canonical service grows a
presentation or session-issuing dependency.

    python -m pytest tests/test_account_recovery_architecture.py -v
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "app/services/account_recovery.py"
WEB = "app/blueprints/auth.py"
MOBILE = "app/blueprints/mobile_password_recovery.py"

# The recovery primitives of the provider adapter. Only the canonical service
# may call them.
PRIMITIVES = frozenset({"forgot_password", "confirm_forgot_password"})

PRESENTATION_NAMES = frozenset({
    "request", "session", "flash", "redirect", "jsonify", "render_template",
    "url_for", "make_response", "abort", "g",
})


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _attribute_calls(tree, owner, names):
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in names
                and isinstance(node.value, ast.Name)
                and node.value.id == owner):
            found.append(node.attr)
        if isinstance(node, ast.ImportFrom) and node.module and (
                node.module.endswith(owner)):
            found.extend(a.name for a in node.names if a.name in names)
    return found


def _python_sources():
    for base in ("app", "fitx_mcp"):
        for path in sorted((ROOT / base).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix()


def test_only_the_canonical_service_calls_recovery_primitives():
    callers = {path for path in _python_sources()
               if path != "app/services/cognito_service.py"
               and _attribute_calls(_tree(path), "cognito_service", PRIMITIVES)}
    assert callers == {SERVICE}


def test_the_service_uses_every_primitive():
    assert set(_attribute_calls(
        _tree(SERVICE), "cognito_service", PRIMITIVES)) == PRIMITIVES


def test_only_the_canonical_service_runs_the_credential_change_sweep():
    """`revoke_all_for_user` bumps the credential fence: it is a password-change
    effect, so exactly one module may trigger it."""
    callers = {path for path in _python_sources()
               if path != "app/services/mobile_auth.py"
               and _attribute_calls(_tree(path), "mobile_auth",
                                    {"revoke_all_for_user"})}
    assert callers == {SERVICE}


def test_both_transports_delegate_to_the_canonical_service():
    for relative in (WEB, MOBILE):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for function in ("request_password_reset", "reset_password"):
            assert f"account_recovery.{function}(" in source, (
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


# Session and credential authorities the service may reference, and exactly
# which of their members: revocation primitives and the failure type. Nothing
# that mints, rotates, refreshes or looks up a credential.
ALLOWED_AUTHORITY_MEMBERS = {
    "mobile_auth": {"revoke_all_for_user", "MobileAuthFailure"},
    "session_store": {"provider_refresh_tokens_for_user", "delete_for_user"},
    "cognito_service": {"forgot_password", "confirm_forgot_password",
                        "revoke_token", "CognitoServiceError"},
}

ISSUING_NAMES = frozenset({
    "login_user", "mobile_credentials", "MobileAccessCredential",
    "MobileRefreshCredential", "authenticate", "initiate_auth",
    "refresh_tokens", "generate_credential", "login", "refresh",
    "create_session", "save_session", "store_tokens", "issue",
})


def test_the_service_references_only_revocation_members_of_session_authorities():
    tree = _tree(SERVICE)
    used = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in ALLOWED_AUTHORITY_MEMBERS):
            used.setdefault(node.value.id, set()).add(node.attr)
    for owner, members in used.items():
        assert members <= ALLOWED_AUTHORITY_MEMBERS[owner], (owner, members)


def test_the_service_issues_no_session():
    names = set()
    for node in ast.walk(_tree(SERVICE)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.name for alias in node.names)
    assert names & ISSUING_NAMES == set()


def test_mobile_routes_live_on_the_canonical_blueprint(app):
    rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()
             if rule.rule.startswith("/api/v1/auth/password/")}
    assert rules == {
        "/api/v1/auth/password/forgot": "mobile_api.password_forgot",
        "/api/v1/auth/password/reset": "mobile_api.password_reset",
    }
    assert not any(rule.rule.startswith(("/api/v2", "/api/mobile", "/native"))
                   for rule in app.url_map.iter_rules())


def test_mobile_routes_are_classified_pre_auth():
    from app.mobile_auth_middleware import _MOBILE_PREAUTH_ENDPOINTS

    assert {"mobile_api.password_forgot",
            "mobile_api.password_reset"} <= _MOBILE_PREAUTH_ENDPOINTS


def test_mobile_transport_never_reads_provider_or_validator_text():
    source = (ROOT / MOBILE).read_text(encoding="utf-8")
    assert ".detail" not in source
    assert "cognito_service" not in source
    assert "mobile_auth." not in source
    assert "session_store" not in source


def test_web_transport_no_longer_owns_recovery_or_revocation_logic():
    source = (ROOT / WEB).read_text(encoding="utf-8")
    for retired in ("_revoke_all_sessions_after_credential_change",
                    "_best_effort_provider_revoke_all",
                    "_send_password_changed_email",
                    "validate_password", "revoke_all_for_user"):
        assert retired not in source, retired
