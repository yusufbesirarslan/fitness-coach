"""Hermetic Flask application wrapper used only by the frontend audit."""

from __future__ import annotations

import ipaddress
import json
import os
import threading
from pathlib import Path


# The hermetic audit installs per-request GLOBAL state — a fixed audit clock and
# a monkeypatched Cognito token validator (see _activate_audit_context). That is
# only consistent while ONE request runs at a time, but a single Today/legacy page
# fires several XHRs in parallel and the server is threaded, so concurrent requests
# would interleave their global patch/restore and corrupt each other's auth/clock
# — surfacing as spurious 5xx (a transient 503 "identity unavailable" or an
# unhandled 500) on a random subset of the parallel reads. This lock serializes
# the request BODIES (the threaded server still owns the sockets, so the browser's
# concurrent connections never head-of-line block) so each request holds a
# coherent clock+validator for its whole lifetime. It is a harness-only fix: no
# application code is involved and production is unaffected.
_AUDIT_REQUEST_LOCK = threading.Lock()

AUDIT_ACCESS_TOKEN_PREFIX = "audit-access:"


def audit_validate_token(token, expected_use, leeway_seconds=0):
    """Stand-in for cognito_jwt.validate_token while the audit harness runs.

    Module level, and its signature MUST stay identical to the production
    validator's. It is installed by monkeypatching, so the real caller decides
    how it is invoked: when the auth contract started passing `leeway_seconds`,
    a two-argument stub here turned every seeded audit page into a 500 that
    looked like an application bug. `leeway_seconds` is accepted and ignored on
    purpose — audit tokens are opaque strings, not signed JWTs, so there is no
    expiry to be lenient about. tests/test_frontend_audit_app.py compares the
    two signatures directly so the next change fails loudly instead.
    """
    # Deferred like every other app import in this module: the harness sets its
    # hermetic environment before anything under app/ is imported.
    from app.services import cognito_jwt
    if (expected_use != "access"
            or not token.startswith(AUDIT_ACCESS_TOKEN_PREFIX)):
        raise cognito_jwt.TokenValidationError("invalid audit token")
    return {
        "sub": token.removeprefix(AUDIT_ACCESS_TOKEN_PREFIX),
        "token_use": "access",
    }


ROOT = Path(__file__).resolve().parents[2]
SPRINT_DIR = ROOT / "docs" / "frontend-readiness" / "sprint-0"


class AuditSafetyError(RuntimeError):
    """The requested audit configuration could affect a non-audit system."""


def sqlite_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.resolve().as_posix()}"


def assert_safe_settings(host: str, database_url: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise AuditSafetyError("audit host must be a numeric loopback address") from exc
    if not address.is_loopback:
        raise AuditSafetyError("audit server must bind to loopback")
    if not database_url.startswith("sqlite:///"):
        raise AuditSafetyError("audit database must be SQLite")


def load_scenario_clocks() -> dict[str, dict]:
    document = json.loads((SPRINT_DIR / "scenario-clocks.json").read_text(encoding="utf-8"))
    return {scenario["id"]: scenario for scenario in document["scenarios"]}


def configure_audit_environment(database_url: str) -> None:
    assert_safe_settings("127.0.0.1", database_url)
    os.environ.update({
        "OPENAI_API_KEY": "audit-no-network",
        "SECRET_KEY": "axis-audit-local-only-secret",
        "DATABASE_URL": database_url,
        "FATSECRET_BASE_URL": "https://fatsecret.invalid",
        "FITX_SKIP_DB_INIT": "1",
        "REDIS_URL": "",
        "BEDROCK_ENABLED": "0",
        "S3_BUCKET_NAME": "",
        "COGNITO_USER_POOL_ID": "audit-local-pool",
        "COGNITO_APP_CLIENT_ID": "audit-local-client",
        "RESEND_API_KEY": "",
        "FLASK_DEBUG": "1",
        "SENTRY_DSN": "",
        "AI_METRICS_ENABLED": "0",
    })


def create_audit_app(database_path: Path):
    database_path = Path(database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_url = sqlite_url(database_path)
    previous_database_url = os.environ.get("DATABASE_URL")
    configure_audit_environment(database_url)

    try:
        from datetime import datetime

        from flask import abort, g, jsonify, redirect, session
        from flask_login import login_user

        from app import create_app
        from app.extensions import db, limiter
        from app.models import User
        from app.services import cognito_jwt, session_store
        from app.timeutil import audit_clock

        app = create_app()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url

    app.config.update(
        TESTING=True,
        PROPAGATE_EXCEPTIONS=False,
        SESSION_COOKIE_SECURE=False,
        AI_PLAN_QUOTA_ENABLED=False,
        AI_CHAT_QUOTA_ENABLED=False,
    )
    limiter.enabled = False
    app.extensions["frontend_audit"] = {
        "database_path": str(database_path),
        "scenario_clocks": load_scenario_clocks(),
    }

    with app.app_context():
        db.create_all()

    def _activate_audit_context():
        # Hold the serialization lock for the whole request so the global clock +
        # validator patch below stays coherent under the threaded server's
        # concurrent requests (released in _restore_audit_context / teardown).
        _AUDIT_REQUEST_LOCK.acquire()
        g._audit_lock_held = True
        clocks = app.extensions["frontend_audit"]["scenario_clocks"]
        scenario_id = session.get("audit_scenario", "anonymous")
        scenario = clocks.get(scenario_id, clocks["anonymous"])
        manager = audit_clock(datetime.fromisoformat(scenario["fixed_current_datetime"]))
        manager.__enter__()
        g._audit_clock_manager = manager
        g._audit_original_validator = cognito_jwt.validate_token
        cognito_jwt.validate_token = audit_validate_token

    app.before_request(_activate_audit_context)
    handlers = app.before_request_funcs[None]
    handlers.remove(_activate_audit_context)
    handlers.insert(0, _activate_audit_context)

    @app.teardown_request
    def _restore_audit_context(_error=None):
        try:
            original = getattr(g, "_audit_original_validator", None)
            if original is not None:
                cognito_jwt.validate_token = original
            manager = getattr(g, "_audit_clock_manager", None)
            if manager is not None:
                manager.__exit__(None, None, None)
        finally:
            if getattr(g, "_audit_lock_held", False):
                g._audit_lock_held = False
                _AUDIT_REQUEST_LOCK.release()

    @app.get("/__audit__/login/<scenario_id>")
    def audit_login(scenario_id):
        if scenario_id not in app.extensions["frontend_audit"]["scenario_clocks"]:
            abort(404)
        user = User.query.filter_by(username=f"audit-{scenario_id}").first_or_404()
        login_user(user)
        session["audit_scenario"] = scenario_id
        session["cognito_sid"] = session_store.create(
            user,
            {
                "access_token": (
                    f"{AUDIT_ACCESS_TOKEN_PREFIX}{user.cognito_sub}"),
                "refresh_token": "audit-refresh-never-used",
                "expires_in": 86400,
            },
            user.username,
        )
        return redirect("/")

    @app.get("/__audit__/reset-password")
    def audit_reset_password():
        session["password_reset_username"] = "audit-anonymous"
        session["password_reset_started_at"] = datetime.now().timestamp()
        return redirect("/reset-password")

    @app.get("/__audit__/scenario")
    def audit_scenario():
        scenario_id = session.get("audit_scenario", "anonymous")
        return jsonify(app.extensions["frontend_audit"]["scenario_clocks"][scenario_id])

    @app.get("/__audit__/404")
    def audit_404():
        abort(404)

    @app.get("/__audit__/500")
    def audit_500():
        raise RuntimeError("intentional local audit error")

    return app
