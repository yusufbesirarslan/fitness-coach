"""Amazon Cognito Hosted-UI (OIDC redirect) hesap eşleme testleri.

Kapsam: app/services/cognito.py (`get_or_create_user`, `_coerce_bool`,
`_unique_username`) ve auth blueprint'in /auth/cognito/callback route'u. Bu
katman güvenlik-kritiktir: DOĞRULANMAMIŞ e-postalı bir Cognito kimliğinin mevcut
yerel hesaba bağlanması hesap ele geçirmeye kapı açar — bu reddetme yolu
sabitlenir. Ağ YOK: Authlib/oauth tamamen sahte ile değiştirilir.

    python -m pytest tests/test_cognito.py -v
"""
from types import SimpleNamespace

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

def _patch_callback(monkeypatch, *, token=None, raises=None):
    """auth blueprint'teki Cognito kullanılabilirliğini ve oauth.oidc'yi sahtele."""
    monkeypatch.setattr(auth_bp, "_cognito_available", lambda: True)

    def authorize_access_token():
        if raises is not None:
            raise raises
        return token

    monkeypatch.setattr(
        auth_bp, "oauth",
        SimpleNamespace(oidc=SimpleNamespace(
            authorize_access_token=authorize_access_token)))


def test_callback_404_when_cognito_unavailable(client):
    # Varsayılan: COGNITO_ENABLED False → uç kapalı.
    assert client.get("/auth/cognito/callback").status_code == 404


def test_callback_token_exchange_failure_redirects_with_err_cognito(client, monkeypatch):
    _patch_callback(monkeypatch, raises=RuntimeError("state mismatch"))
    resp = client.get("/auth/cognito/callback")
    assert resp.status_code == 302
    assert "err=cognito" in resp.headers["Location"]


def test_callback_link_error_redirects_with_err_link(client, make_user, monkeypatch):
    # Doğrulanmamış e-posta zaten kayıtlı → get_or_create_user CognitoLinkError atar.
    make_user(username="mevcut", email="mevcut@example.com")
    _patch_callback(monkeypatch, token={"userinfo": {
        "sub": "x", "email": "mevcut@example.com", "email_verified": False}})
    resp = client.get("/auth/cognito/callback")
    assert resp.status_code == 302
    assert "err=link" in resp.headers["Location"]


def test_callback_success_logs_in_and_creates_user(client, monkeypatch):
    _patch_callback(monkeypatch, token={"userinfo": {
        "sub": "cog-ok", "email": "girdi@example.com", "email_verified": True}})
    resp = client.get("/auth/cognito/callback")
    assert resp.status_code == 302
    assert "err=" not in resp.headers["Location"]  # hata yok → home'a yönlendirir
    user = User.query.filter_by(email="girdi@example.com").one()
    assert user.cognito_sub == "cog-ok"
    # Oturum kuruldu: korumalı bir sayfa artık login'e yönlendirmez.
    assert client.get("/leaderboard").status_code == 200
