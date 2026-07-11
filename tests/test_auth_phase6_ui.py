"""Phase 6 auth/onboarding frontend contract tests."""

import time
from pathlib import Path


def _html(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_auth_pages_use_shared_static_assets(client):
    for path in ("/welcome", "/login", "/register"):
        html = _html(client, path)
        assert "/static/auth.css" in html
        assert "/static/auth.js" in html
        assert "<style" not in html


def test_login_has_secure_accessible_form_controls(client):
    html = _html(client, "/login")
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html
    assert 'data-action="togglePassword"' in html
    assert 'aria-live="polite"' in html
    assert 'data-action="login"' in html


def test_register_has_inline_validation_and_password_strength(client):
    html = _html(client, "/register")
    assert 'autocomplete="new-password"' in html
    assert 'data-action-input="updatePasswordStrength"' in html
    assert 'id="password-strength"' in html
    assert 'aria-describedby="password-help password-strength-text"' in html


def test_setup_collects_target_weight_and_uses_shared_assets(client, auth_user):
    html = _html(client, "/setup")
    assert "/static/auth.css" in html
    assert "/static/auth.js" in html
    assert 'id="s-target-weight"' in html
    assert 'data-action="finishSetup"' in html


def test_auth_frontend_does_not_store_passwords(client):
    response = client.get("/static/auth.js")
    assert response.status_code == 200
    js = response.get_data(as_text=True)
    assert "localStorage.setItem('password'" not in js
    assert 'localStorage.setItem("password"' not in js
    assert "sessionStorage.setItem('password'" not in js
    assert 'sessionStorage.setItem("password"' not in js


def test_password_recovery_pages_reuse_auth_design(client):
    forgot = _html(client, "/forgot-password")
    assert 'class="auth-card"' in forgot
    assert "/static/auth.css" in forgot and "/static/auth.js" in forgot
    assert 'data-action="forgotPassword"' in forgot

    with client.session_transaction() as state:
        state["password_reset_username"] = "alice"
        state["password_reset_started_at"] = time.time()
    reset = _html(client, "/reset-password")
    assert 'class="auth-card"' in reset
    assert 'autocomplete="one-time-code"' in reset
    assert reset.count('autocomplete="new-password"') == 2
    assert 'data-action="resetPassword"' in reset
    assert 'data-action-input="updatePasswordStrength"' in reset

    for name in ("forgot_password.html", "reset_password.html"):
        source = Path("templates", name).read_text(encoding="utf-8")
        assert '{% include "_head.html" %}' in source
        assert "<style" not in source and "<script nonce=" not in source


def test_login_links_to_forgot_password(client):
    assert 'href="/forgot-password"' in _html(client, "/login")


def test_login_renders_categorized_flashes(client):
    with client.session_transaction() as state:
        state["_flashes"] = [
            ("success", "Password changed"),
            ("error", "Try again"),
        ]
    html = _html(client, "/login")
    assert 'class="auth-alert auth-alert-success show"' in html
    assert 'class="auth-alert auth-alert-error show"' in html
    assert "Password changed" in html and "Try again" in html
