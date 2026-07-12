"""Hosted UI remains disabled after removal of the obsolete mapping service."""

from app.blueprints import auth as auth_bp


def test_callback_404_when_cognito_unavailable(client):
    assert client.get("/auth/cognito/callback").status_code == 404


def test_hosted_ui_login_route_404_even_when_cognito_enabled(client, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    assert client.get("/login/cognito").status_code == 404


def test_callback_404_even_when_cognito_enabled(client, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    assert client.get("/auth/cognito/callback").status_code == 404


def test_login_and_register_do_not_render_hosted_ui_links(client, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    login_html = client.get("/login").get_data(as_text=True)
    register_html = client.get("/register").get_data(as_text=True)
    assert "/login/cognito" not in login_html
    assert "/login/cognito" not in register_html
