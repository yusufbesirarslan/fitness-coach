from pathlib import Path

import pytest


def test_audit_settings_refuse_unsafe_host_and_database(tmp_path):
    from scripts.frontend_audit.app import AuditSafetyError, assert_safe_settings

    safe_url = f"sqlite:///{(tmp_path / 'audit.db').as_posix()}"
    assert_safe_settings("127.0.0.1", safe_url)
    with pytest.raises(AuditSafetyError, match="loopback"):
        assert_safe_settings("0.0.0.0", safe_url)
    with pytest.raises(AuditSafetyError, match="SQLite"):
        assert_safe_settings("127.0.0.1", "postgresql://prod.example/db")


def test_audit_routes_are_local_only(app, tmp_path):
    from scripts.frontend_audit.app import create_audit_app

    production_rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert not any(rule.startswith("/__audit__/") for rule in production_rules)

    audit_app = create_audit_app(tmp_path / "audit.db")
    audit_rules = {rule.rule for rule in audit_app.url_map.iter_rules()}
    assert "/__audit__/login/<scenario_id>" in audit_rules
    assert "/__audit__/404" in audit_rules
    assert "/__audit__/500" in audit_rules


def test_seed_is_idempotent_and_uses_only_synthetic_users(tmp_path):
    from app.extensions import db
    from app.models import User
    from scripts.frontend_audit.app import create_audit_app
    from scripts.frontend_audit.seed import seed_all

    audit_app = create_audit_app(tmp_path / "audit.db")
    first = seed_all(audit_app)
    second = seed_all(audit_app)
    assert first == second
    assert first["users"] == 12
    with audit_app.app_context():
        users = User.query.order_by(User.username).all()
        assert all(user.email.endswith("@example.invalid") for user in users)
        assert {user.username for user in users} >= {
            "audit-active-workout", "audit-active-rest-day", "audit-friend"
        }


def test_seeded_scenario_login_satisfies_real_auth_middleware(tmp_path):
    from scripts.frontend_audit.app import create_audit_app
    from scripts.frontend_audit.seed import seed_all

    audit_app = create_audit_app(tmp_path / "audit.db")
    seed_all(audit_app)
    client = audit_app.test_client()
    response = client.get("/__audit__/login/active-workout")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    page = client.get("/")
    assert page.status_code == 200
    assert b"audit-active-workout" in page.data
