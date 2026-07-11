"""Sprint 2 uçtan-uca kimlik doğrulama senaryoları: logout GlobalSignOut,
eşzamanlı oturumlar, oturum süre-dolumu, korumalı route matrisi.

    python -m pytest tests/test_cognito_auth.py -v
"""
from datetime import datetime, timedelta
import pytest

from app.extensions import db
from app.models import User, CognitoSession
from app.services import cognito_service, cognito_jwt, session_store
from app.services.cognito_service import CognitoServiceError
from app.blueprints import auth as auth_bp


@pytest.fixture
def cognito_env(monkeypatch):
    """COGNITO_ENABLED aç; authenticate + JWKS doğrulamayı sahtele."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)

    def fake_authenticate(username, password):
        return {
            "tokens": {"access_token": f"acc-{username}", "id_token": f"id-{username}",
                       "refresh_token": f"ref-{username}", "expires_in": 3600},
            "claims": {"sub": f"sub-{username}", "email": f"{username}@example.com",
                       "email_verified": True, "name": username},
        }

    monkeypatch.setattr(cognito_service, "authenticate", fake_authenticate)
    # S4: login artık DOĞRULANMIŞ claim'lerin sub'ını karşılaştırır — sahte
    # doğrulayıcı, token'daki kullanıcı adından tutarlı sub üretir
    # (id-<username> → sub-<username>).
    monkeypatch.setattr(cognito_jwt, "validate_token",
                        lambda tok, use: {"sub": "sub-" + tok.removeprefix("id-").removeprefix("acc-")})
    return monkeypatch


@pytest.fixture
def cog_account(app):
    u = User(username="e2e", email="e2e@example.com", cognito_sub="sub-e2e")
    db.session.add(u)
    db.session.commit()
    return u


def test_login_compares_sub_from_verified_claims(client, cognito_env, cog_account):
    # S4: validate_token'ın DÖNDÜRDÜĞÜ (doğrulanmış) claim'ler kullanılmalı.
    # Doğrulanmamış decode doğru sub'ı söylese bile, doğrulanmış sub yerel
    # kayıtla eşleşmiyorsa giriş reddedilmeli — aksi hâlde bir refactor
    # doğrulamayı sessizce anlamsızlaştırır.
    cognito_env.setattr(cognito_jwt, "validate_token",
                        lambda tok, use: {"sub": "baskasi"})
    resp = client.post("/login", json={"username": "e2e", "password": "x"})
    assert resp.status_code == 401
    assert CognitoSession.query.count() == 0


def test_logout_calls_global_sign_out_and_deletes_row(client, cognito_env, cog_account):
    calls = {}
    cognito_env.setattr(cognito_service, "global_sign_out", lambda tok: calls.setdefault("tok", tok))
    assert client.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert CognitoSession.query.count() == 1
    resp = client.get("/logout", headers={"Referer": "http://localhost/"})
    assert resp.status_code in (301, 302)
    assert calls.get("tok") == "acc-e2e"          # GlobalSignOut çağrıldı
    assert CognitoSession.query.count() == 0      # satır silindi


def test_concurrent_sessions_are_independent(app, cognito_env, cog_account):
    c1, c2 = app.test_client(), app.test_client()
    assert c1.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert c2.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert CognitoSession.query.count() == 2       # iki bağımsız satır
    # c1 çıkış yapınca kendi satırı silinir; c2 satırı kalır (yerel kayıt bağımsız).
    c1.get("/logout", headers={"Referer": "http://localhost/"})
    assert CognitoSession.query.count() == 1


def test_session_expiration_forces_relogin(client, cognito_env, cog_account, monkeypatch):
    # access süresi geçmiş + refresh başarısız → korumalı route login'e yönlendirir.
    assert client.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    row = CognitoSession.query.first()
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda ref, uname: (_ for _ in ()).throw(CognitoServiceError("x", "NotAuthorizedException")))
    resp = client.get("/supplements")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert CognitoSession.query.count() == 0       # geçersiz oturum temizlendi


def test_protected_route_allows_after_login(client, cognito_env, cog_account):
    assert client.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert client.get("/supplements").status_code == 200
