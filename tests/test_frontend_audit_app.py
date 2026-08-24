import os
from pathlib import Path

import pytest


def test_audit_app_restores_database_environment_for_subsequent_apps(tmp_path):
    from scripts.frontend_audit.app import create_audit_app

    original_database_url = os.environ["DATABASE_URL"]

    create_audit_app(tmp_path / "audit.db")

    from app import create_app

    assert os.environ["DATABASE_URL"] == original_database_url
    assert create_app().config["SQLALCHEMY_DATABASE_URI"] == original_database_url


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
    """End-to-end: a seeded login must reach `/` through the REAL @require_auth.

    The harness monkeypatches the token validator, so every change to how
    production calls that validator lands here first. This test is the reason
    the audit harness cannot quietly rot into a stub that only satisfies
    itself — it exercises the actual middleware, not a mock of it.
    """
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


def test_audit_validator_stub_matches_the_production_validator_signature():
    """The stub is installed by monkeypatching, so its signature is a contract.

    When the auth contract began passing `leeway_seconds`, the two-argument
    stub raised TypeError inside @require_auth and every seeded audit page came
    back as a 500 — a harness defect wearing the costume of an application bug.
    Comparing the signatures directly turns the next such change into an
    immediate, self-explaining failure.
    """
    import inspect

    from app.services import cognito_jwt
    from scripts.frontend_audit.app import audit_validate_token

    assert (inspect.signature(audit_validate_token)
            == inspect.signature(cognito_jwt.validate_token))


def test_audit_validator_stub_rejects_anything_but_a_seeded_access_token():
    """It stands in for a validator; it must still refuse what the real one
    refuses, or the audit would pass with credentials production rejects."""
    from app.services import cognito_jwt
    from scripts.frontend_audit.app import (
        AUDIT_ACCESS_TOKEN_PREFIX, audit_validate_token,
    )

    claims = audit_validate_token(f"{AUDIT_ACCESS_TOKEN_PREFIX}sub-1", "access")
    assert claims == {"sub": "sub-1", "token_use": "access"}
    # Leeway is accepted and ignored — audit tokens are opaque, not signed JWTs.
    assert audit_validate_token(
        f"{AUDIT_ACCESS_TOKEN_PREFIX}sub-1", "access", leeway_seconds=30) == claims

    for token, use in ((f"{AUDIT_ACCESS_TOKEN_PREFIX}sub-1", "id"),
                       ("not-an-audit-token", "access")):
        with pytest.raises(cognito_jwt.TokenValidationError):
            audit_validate_token(token, use)


def test_audit_seed_training_plans_match_the_canonical_contract(tmp_path):
    """The audit fixture must be valid by construction — production validation
    is not relaxed to accept stale tip/exercise vocabulary."""
    import json

    from app.models import TrainingPlan
    from app.services.training_generation.response_validator import (
        VALID_TIPS, validate_plan_structure,
    )
    from app.services.workout_state.serialization import serialize_plan
    from scripts.frontend_audit.app import create_audit_app
    from scripts.frontend_audit.seed import seed_all

    audit_app = create_audit_app(tmp_path / "audit.db")
    seed_all(audit_app)
    with audit_app.app_context():
        plans = TrainingPlan.query.all()
        assert plans
        for plan in plans:
            payload = json.loads(plan.plan_data)
            validated = validate_plan_structure(payload)
            serialize_plan(payload)
            for day in validated["program"]:
                assert day["tip"] in VALID_TIPS
                for exercise in day["egzersizler"]:
                    assert "dinlenme" in exercise
                    assert "not" in exercise


def test_seeded_training_bootstrap_succeeds_without_harness_repair(tmp_path):
    """/training/bootstrap used to 500 on the stale seed unless the capture
    harness rewrote kuvvet→antrenman and filled dinlenme/not."""
    from scripts.frontend_audit.app import create_audit_app
    from scripts.frontend_audit.seed import seed_all

    audit_app = create_audit_app(tmp_path / "audit.db")
    seed_all(audit_app)
    client = audit_app.test_client()
    for scenario in ("active-workout", "active-rest-day"):
        login = client.get(f"/__audit__/login/{scenario}")
        assert login.status_code == 302
        response = client.get("/training/bootstrap")
        assert response.status_code == 200, scenario
        body = response.get_json()
        assert body.get("code") != "bootstrap_unavailable", scenario
        assert "workout" in body, scenario


def test_workout_audit_harness_does_not_repair_the_seed():
    from pathlib import Path

    audit_dir = Path(__file__).resolve().parents[1] / "scripts" / "frontend_audit"
    tree = "\n".join(
        path.read_text(encoding="utf-8")
        for path in audit_dir.glob("*.py")
        if path.name != "seed.py"
    )
    assert "_normalize_seed_plans" not in tree
    assert 'tip"] = "antrenman"' not in tree
    assert 'tip": "kuvvet"' not in tree
    assert 'setdefault("dinlenme"' not in tree
