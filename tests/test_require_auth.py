"""@require_auth davranış testleri: anonim reddi, cognito kimliği olmayan
kullanıcının reddi, süre-dolumunda yenileme, KESİN yenileme hatasında geçersiz
kılma ve (H1) GEÇİCİ altyapı hatasında oturumun KORUNMASI.

    python -m pytest tests/test_require_auth.py -v
"""
from datetime import datetime, timedelta
import pytest

from app.extensions import db
from app.models import User
from app.services import session_store, cognito_jwt
from app.auth_middleware import require_auth


@pytest.fixture
def probe_route(app):
    """require_auth ile korunan geçici bir route ekle."""
    # Bu testler dekoratörün kendisini sınar; el-yordamıyla kurulan oturum
    # login_manager'ın "strong" session-protection'ıyla (_id eşleşmesi) çakışır
    # ve kullanıcıyı düşürür. Session protection ayrı test kapsamı → burada kapat.
    app.config["SESSION_PROTECTION"] = None

    @app.route("/__probe")
    @require_auth
    def _probe():
        return "ok", 200
    app.url_map.update()  # route kaydını uygula
    return "/__probe"


@pytest.fixture
def legacy_user(app):
    u = User(username="leg", email="leg@example.com")
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def cognito_user(app):
    u = User(username="cog", email="cog@example.com", cognito_sub="sub-cog")
    db.session.add(u)
    db.session.commit()
    return u


def _login_session(client, user, sid=None):
    """Flask-Login oturumunu ve (varsa) cognito_sid'i doğrudan kur."""
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id)
        if sid:
            s["cognito_sid"] = sid


def test_anonymous_redirected(client, probe_route):
    resp = client.get(probe_route)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_user_without_cognito_identity_invalidated(client, probe_route, legacy_user):
    _login_session(client, legacy_user)
    assert client.get(probe_route).status_code == 302


def test_cognito_valid_token_allowed(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use, **kw: {"sub": "sub-cog"})
    sid = session_store.create(cognito_user, {"access_token": "a", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 200


def test_cognito_missing_session_row_invalidated(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use, **kw: {"sub": "sub-cog"})
    _login_session(client, cognito_user, "no-such-sid")
    resp = client.get(probe_route)
    assert resp.status_code == 302  # geçersiz → login'e


def test_cognito_expired_access_refreshed(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use, **kw: {"sub": "sub-cog"})
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda ref, uname: {"access_token": "fresh", "id_token": "", "expires_in": 3600})
    sid = session_store.create(cognito_user, {"access_token": "old", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 200


def test_cognito_dead_refresh_invalidated(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use, **kw: {"sub": "sub-cog"})
    from app.services import cognito_service
    def boom(ref, uname):
        raise cognito_service.CognitoServiceError("x", "NotAuthorizedException")
    monkeypatch.setattr(cognito_service, "refresh_tokens", boom)
    sid = session_store.create(cognito_user, {"access_token": "old", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    _login_session(client, cognito_user, sid)
    resp = client.get(probe_route)
    assert resp.status_code == 302
    assert session_store.get(sid) is None  # satır silindi


def test_session_user_mismatch_invalidated(
        client, probe_route, cognito_user, make_user, monkeypatch):
    monkeypatch.setattr(
        cognito_jwt, "validate_token", lambda token, use, **kw: {"sub": "sub-cog"})
    other = make_user("other")
    sid = session_store.create(other, {
        "access_token": "a", "refresh_token": "r", "id_token": "i", "expires_in": 3600,
    }, "other")
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 302
    assert session_store.get(sid) is None


def test_verified_sub_must_resolve_same_user(
        client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(
        cognito_jwt, "validate_token",
        lambda token, use, **kw: {"sub": "sub-other"})
    sid = session_store.create(cognito_user, {
        "access_token": "a", "refresh_token": "r", "id_token": "i", "expires_in": 3600,
    }, "cog")
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 302
    assert session_store.get(sid) is None


# ---------------------------------------------------------------------------
# H1 — GEÇİCİ altyapı hatası oturumu ÖLDÜRMEZ.
#
# Eskiden require_auth her hatayı tek bir yıkıcı dala (_invalidate → satırı SİL)
# indiriyordu. Cognito throttle'ı ya da bir ağ timeout'u, access token'ın süresi
# dolduğu anda (kullanıcı başına ~saatte bir) oturumu GERİ DÖNÜŞSÜZ siliyordu.
# Token süreleri kullanıcı popülasyonunda kabaca eşzamanlı dolduğu için bu,
# tek bir Cognito olayında KORELE toplu logout üretebiliyordu.
# ---------------------------------------------------------------------------

def _expired_session(cognito_user):
    sid = session_store.create(cognito_user, {"access_token": "old", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    return sid


@pytest.mark.parametrize("code", [
    "TooManyRequestsException",   # Cognito throttle
    "InternalErrorException",     # Cognito iç hatası
    "LimitExceededException",
    "ServiceUnavailableException",
    "",                           # kodsuz = botocore connect/read timeout, ağ kesintisi
])
def test_transient_refresh_failure_preserves_session(
        client, probe_route, cognito_user, monkeypatch, code):
    """Yenileme GEÇİCİ olarak başarısız → 503 + Retry-After, oturum satırı DURUR."""
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use, **kw: {"sub": "sub-cog"})
    from app.services import cognito_service

    def transient(ref, uname):
        raise cognito_service.CognitoServiceError("gecici", code)

    monkeypatch.setattr(cognito_service, "refresh_tokens", transient)
    sid = _expired_session(cognito_user)
    _login_session(client, cognito_user, sid)

    resp = client.get(probe_route)
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")
    # KRİTİK: oturum satırı korunmalı — kesinti geçince kullanıcı devam etsin.
    assert session_store.get(sid) is not None


def test_jwks_unavailable_preserves_session(
        client, probe_route, cognito_user, monkeypatch):
    """JWKS çekilemedi = imza DOĞRULANAMADI, 'imza GEÇERSİZ' DEĞİL.

    cognito_jwt bu nedeni özellikle ayrı tutuyor (test_cognito_jwt.py); burada
    da 503'e çevrilmeli, oturum silinmemeli. Soğuk JWKS önbelleği her taze
    konteynerde (yani her deploy'dan sonra) gerçeğe dönüşen bir durumdur.
    """
    def unavailable(tok, use, **kw):
        raise cognito_jwt.TokenValidationError("jwks_unavailable")

    monkeypatch.setattr(cognito_jwt, "validate_token", unavailable)
    sid = session_store.create(cognito_user, {"access_token": "a", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    _login_session(client, cognito_user, sid)

    resp = client.get(probe_route)
    assert resp.status_code == 503
    assert session_store.get(sid) is not None


def test_invalid_signature_still_invalidates(
        client, probe_route, cognito_user, monkeypatch):
    """Karşı-test: KESİN ret hâlâ yıkıcı olmalı — geçici/kesin ayrımı, kesin
    reddi yumuşatarak yapılmadı."""
    def bad(tok, use, **kw):
        raise cognito_jwt.TokenValidationError("invalid_signature")

    monkeypatch.setattr(cognito_jwt, "validate_token", bad)
    sid = session_store.create(cognito_user, {"access_token": "a", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    _login_session(client, cognito_user, sid)

    assert client.get(probe_route).status_code == 302
    assert session_store.get(sid) is None


def test_require_auth_marks_wrapped_view_for_audit(app):
    @require_auth
    def protected():
        return "ok"

    assert protected._require_auth is True
