"""Phase 6 auth/onboarding frontend contract tests."""


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
