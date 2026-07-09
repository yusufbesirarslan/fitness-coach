"""Amazon Cognito local-account mapping and Sprint 1 route tests.

Covers app/services/cognito.py account mapping helpers and verifies Sprint 1 keeps
Hosted UI/OIDC redirect routes disabled while native backend Cognito registration
is handled by app/services/cognito_service.py.

    python -m pytest tests/test_cognito.py -v
"""
import pytest
from app.blueprints import auth as auth_bp
from app.extensions import db
from app.models import User
from app.services.cognito import (
    CognitoLinkError,
    _coerce_bool,
    _unique_username,
    get_or_create_user,
)


# ---------------------------------------------------------------------------
# _coerce_bool — Cognito email_verified bool VEYA "true"/"false" string gelebilir
# ---------------------------------------------------------------------------

def test_coerce_bool_handles_bool_and_string_forms():
    assert _coerce_bool(True) is True
    assert _coerce_bool(False) is False
    assert _coerce_bool("true") is True
    assert _coerce_bool("True") is True
    assert _coerce_bool(" TRUE ") is True
    assert _coerce_bool("false") is False
    assert _coerce_bool("") is False
    assert _coerce_bool(None) is False


# ---------------------------------------------------------------------------
# _unique_username — e-postadan türetir, kurallara uyar, çakışmayı çözer
# ---------------------------------------------------------------------------

def test_unique_username_strips_invalid_chars(app):
    assert _unique_username("ali+spam@example.com") == "alispam"


def test_unique_username_pads_short_local_part(app):
    # 'ab' < 3 karakter → 'abfitx' ile doldurulur (validate_username min uzunluğu).
    assert _unique_username("ab@example.com") == "abfitx"


def test_unique_username_suffixes_on_collision(app, make_user):
    make_user(username="taken", email="taken@example.com")
    out = _unique_username("taken@other.com")
    assert out != "taken"
    assert out.startswith("taken_")  # base[:57] + "_" + token


# ---------------------------------------------------------------------------
# get_or_create_user — eşleme öncelikleri
# ---------------------------------------------------------------------------

def test_get_or_create_creates_new_account(app):
    user, is_new = get_or_create_user(
        {"sub": "cog-sub-1", "email": "yeni@example.com", "email_verified": True})
    assert is_new is True
    assert user.cognito_sub == "cog-sub-1"
    assert user.email == "yeni@example.com"
    assert user.referral_code  # ensure_referral_code çağrıldı
    # Yerel parola KULLANILAMAZ (rastgele) olmalı — klasik /login'den giriş yapamaz.
    assert user.password_hash is None
    assert not user.check_password("")


def test_get_or_create_links_by_cognito_sub_first(app, make_user):
    existing = make_user(username="subuser", email="sub@example.com",
                         cognito_sub="cog-sub-2")
    user, is_new = get_or_create_user(
        # e-posta farklı olsa bile sub eşleşmesi en güçlü bağ → aynı hesap
        {"sub": "cog-sub-2", "email": "degisti@example.com", "email_verified": True})
    assert is_new is False
    assert user.id == existing.id


def test_get_or_create_links_existing_account_when_email_verified(app, make_user):
    existing = make_user(username="mevcut", email="mevcut@example.com")
    assert existing.cognito_sub is None
    user, is_new = get_or_create_user(
        {"sub": "cog-sub-3", "email": "mevcut@example.com", "email_verified": True})
    assert is_new is False
    assert user.id == existing.id
    assert user.cognito_sub == "cog-sub-3"  # mevcut hesaba Cognito kimliği bağlandı


def test_get_or_create_rejects_unverified_email_link(app, make_user):
    """GÜVENLİK: doğrulanmamış e-postalı Cognito kimliği mevcut bir hesaba
    BAĞLANMAMALI (hesap ele geçirme). CognitoLinkError yükseltilir ve mevcut
    hesabın cognito_sub'u DEĞİŞMEZ."""
    existing = make_user(username="kurban", email="kurban@example.com")
    with pytest.raises(CognitoLinkError):
        get_or_create_user(
            {"sub": "saldirgan-sub", "email": "kurban@example.com",
             "email_verified": False})
    db.session.rollback()
    assert db.session.get(User, existing.id).cognito_sub is None


def test_get_or_create_requires_email(app):
    with pytest.raises(CognitoLinkError, match="e-posta"):
        get_or_create_user({"sub": "cog-sub-x", "email": "", "email_verified": True})


def test_get_or_create_consumes_referral_for_new_user(app, make_user, monkeypatch):
    import app.services.cognito as cognito_svc
    seen = {}
    monkeypatch.setattr(cognito_svc, "consume_referral",
                        lambda user, code: seen.update(uid=user.id, code=code))
    get_or_create_user(
        {"sub": "cog-ref", "email": "davetli@example.com", "email_verified": True},
        ref_code="ABC123")
    assert seen["code"] == "ABC123"


# ---------------------------------------------------------------------------
# /auth/cognito/callback route — token alımı, hesap bağlama, hata eşlemesi
# ---------------------------------------------------------------------------

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