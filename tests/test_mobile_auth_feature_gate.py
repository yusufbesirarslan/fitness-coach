import pytest

from app.services import mobile_credentials


ROOT_WIRE = "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s"


def _create_app():
    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


def _mobile_rules(app):
    return {
        (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("mobile_api.")
    }


@pytest.mark.parametrize("configured", [None, "0"])
def test_disabled_startup_succeeds_without_mobile_keyring(
        monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("MOBILE_AUTH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MOBILE_AUTH_ENABLED", configured)
    monkeypatch.delenv("MOBILE_AUTH_DERIVATION_KEYRING", raising=False)
    monkeypatch.delenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", raising=False)
    monkeypatch.setenv("MOBILE_AUTH_ACCESS_TTL_SECONDS", "not-an-integer")

    flask_app = _create_app()

    assert flask_app.config["MOBILE_AUTH_ENABLED"] is False


def test_disabled_mobile_routes_are_unavailable(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "0")
    monkeypatch.delenv("MOBILE_AUTH_DERIVATION_KEYRING", raising=False)
    monkeypatch.delenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", raising=False)
    flask_app = _create_app()
    client = flask_app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = "feature-gate-csrf"
    post_headers = {
        "Origin": "http://localhost",
        "X-CSRFToken": "feature-gate-csrf",
    }

    assert _mobile_rules(flask_app) == set()
    for method, path in (
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/refresh"),
        ("post", "/api/v1/auth/logout"),
        ("get", "/api/v1/account/me"),
        ("get", "/api/v1/nutrition/diary/today"),
    ):
        headers = post_headers if method == "post" else None
        assert getattr(client, method)(path, headers=headers).status_code == 404


@pytest.mark.parametrize(("keyring", "active_version"), [
    (None, None),
    ("not-json", "v1"),
    ('{"v1":"a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s"}', "v2"),
])
def test_enabled_startup_fails_without_valid_keyring(
        monkeypatch, keyring, active_version):
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "1")
    if keyring is None:
        monkeypatch.delenv("MOBILE_AUTH_DERIVATION_KEYRING", raising=False)
    else:
        monkeypatch.setenv("MOBILE_AUTH_DERIVATION_KEYRING", keyring)
    if active_version is None:
        monkeypatch.delenv(
            "MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", raising=False)
    else:
        monkeypatch.setenv(
            "MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", active_version)

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        _create_app()


def test_malformed_mobile_auth_enabled_value_fails_closed(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "true")

    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        _create_app()


def test_enabled_startup_exposes_only_approved_mobile_routes(monkeypatch):
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "1")
    monkeypatch.setenv(
        "MOBILE_AUTH_DERIVATION_KEYRING", f'{{"v1":"{ROOT_WIRE}"}}')
    monkeypatch.setenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", "v1")

    flask_app = _create_app()

    assert flask_app.config["MOBILE_AUTH_ENABLED"] is True
    assert _mobile_rules(flask_app) == {
        ("/api/v1/auth/login", ("POST",)),
        ("/api/v1/auth/refresh", ("POST",)),
        ("/api/v1/auth/logout", ("POST",)),
        ("/api/v1/account/me", ("GET",)),
            # Sprint 9 backend prerequisite. Product routes live on this blueprint
            # too, so the allow-list keeps covering every /api/v1 route there is.
            ("/api/v1/nutrition/diary/today", ("GET",)),
            ("/api/v1/nutrition/foods/search", ("GET",)),
            ("/api/v1/nutrition/foods/fatsecret/<food_id>/servings", ("GET",)),
            ("/api/v1/nutrition/foods/barcode", ("GET",)),
            ("/api/v1/nutrition/logs", ("POST",)),
        }


def test_derivation_readiness_runs_only_when_mobile_auth_enabled(monkeypatch):
    import app as app_package
    from app.services import mobile_auth

    calls = []
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setattr(app_package, "init_database", lambda app: None)
    monkeypatch.setattr(
        mobile_auth, "validate_derivation_key_readiness",
        lambda: calls.append("readiness"))
    monkeypatch.setenv("MOBILE_AUTH_DERIVATION_KEYRING", "not-json")
    monkeypatch.delenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", raising=False)
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "0")

    _create_app()
    assert calls == []

    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "1")
    monkeypatch.setenv(
        "MOBILE_AUTH_DERIVATION_KEYRING", f'{{"v1":"{ROOT_WIRE}"}}')
    monkeypatch.setenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", "v1")
    _create_app()
    assert calls == ["readiness"]


def test_disabled_weekly_maintenance_does_not_query_mobile_tables(monkeypatch):
    from app import cli
    from app.services import mobile_auth, session_store

    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "0")
    monkeypatch.delenv("MOBILE_AUTH_DERIVATION_KEYRING", raising=False)
    monkeypatch.delenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", raising=False)
    monkeypatch.setattr(cli, "run_weekly_rollover", lambda: "weekly-ok")
    monkeypatch.setattr(session_store, "purge_expired", lambda: 0)
    monkeypatch.setattr(
        mobile_auth, "purge_expired",
        lambda: (_ for _ in ()).throw(AssertionError("mobile tables unavailable")))
    flask_app = _create_app()

    result = flask_app.test_cli_runner().invoke(args=["weekly-reset"])

    assert result.exit_code == 0
    assert "cognito-sessions purged: 0" in result.output
    assert "mobile-auth-sessions purged: 0" in result.output


def test_web_auth_routes_cookies_and_csrf_match_in_both_gate_states(monkeypatch):
    apps = []
    for enabled in ("0", "1"):
        monkeypatch.setenv("MOBILE_AUTH_ENABLED", enabled)
        monkeypatch.setenv(
            "MOBILE_AUTH_DERIVATION_KEYRING", f'{{"v1":"{ROOT_WIRE}"}}')
        monkeypatch.setenv("MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION", "v1")
        apps.append(_create_app())

    disabled, enabled = apps
    disabled_web_rules = {
        (rule.rule, rule.endpoint, tuple(sorted(rule.methods)))
        for rule in disabled.url_map.iter_rules()
    }
    enabled_web_rules = {
        (rule.rule, rule.endpoint, tuple(sorted(rule.methods)))
        for rule in enabled.url_map.iter_rules()
        if not rule.endpoint.startswith("mobile_api.")
    }
    assert disabled_web_rules == enabled_web_rules
    for name in (
        "SESSION_COOKIE_HTTPONLY", "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_SAMESITE",
    ):
        assert disabled.config[name] == enabled.config[name]
    for flask_app in apps:
        client = flask_app.test_client()
        assert client.get("/login").status_code == 200
        assert client.post("/login", data={}).status_code == 403
