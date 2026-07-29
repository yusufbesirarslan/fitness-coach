"""Executable authorization inventory and legacy-auth regression guards."""

from pathlib import Path


PUBLIC_ENDPOINTS = {
    "static",
    "health",
    "pages.landing",
    "pages.invite",
    "auth.set_language",
    "auth.register",
    "auth.login",
    "auth.verify_page",
    "auth.verify_confirm",
    "auth.verify_resend",
    "auth.forgot_password",
    "auth.reset_password",
    "auth.cognito_login",
    "auth.cognito_callback",
}

TEARDOWN_ENDPOINTS = {"auth.logout"}
MOBILE_PUBLIC_ENDPOINTS = {
    "mobile_api.login", "mobile_api.refresh", "mobile_api.logout"}


def test_every_non_public_route_uses_require_auth(app):
    missing = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in (
                PUBLIC_ENDPOINTS | TEARDOWN_ENDPOINTS | MOBILE_PUBLIC_ENDPOINTS):
            continue
        view = app.view_functions[rule.endpoint]
        required = (getattr(view, "_require_mobile_auth", False)
                    if rule.endpoint.startswith("mobile_api.")
                    else getattr(view, "_require_auth", False))
        if not required:
            missing.append((rule.endpoint, rule.rule))
    assert missing == []


def test_runtime_auth_does_not_use_password_hash():
    auth_source = Path("app/blueprints/auth.py").read_text(encoding="utf-8")
    middleware = Path("app/auth_middleware.py").read_text(encoding="utf-8")
    assert "password_hash" not in auth_source + middleware
    assert "check_password" not in auth_source + middleware
    assert "generate_password_hash" not in auth_source + middleware


def test_no_duplicate_cognito_implementations():
    assert not Path("app/services/cognito.py").exists()
    assert not Path("app/services/cognito_idp.py").exists()


def test_mobile_auth_boundary_has_no_cookie_or_flask_login_fallback():
    sources = "\n".join(Path(path).read_text(encoding="utf-8") for path in (
        "app/blueprints/mobile_api.py",
        "app/mobile_auth_middleware.py",
        "app/services/mobile_auth.py",
    ))
    assert "flask_login" not in sources
    assert "current_user" not in sources
    assert "session[" not in sources
    assert "flask_cors" not in sources.lower()
