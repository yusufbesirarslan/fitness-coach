"""CognitoSession token deposu — şifreleme round-trip, süre-dolumunda yenileme,
yenileme başarısızlığında geçersiz kılma. boto3 yok; refresh monkeypatch'lenir.

    python -m pytest tests/test_session_store.py -v
"""
from datetime import datetime, timedelta
import pytest

from app.extensions import db
from app.models import User, CognitoSession
from app.services import session_store, cognito_service
from app.services.session_store import SessionInvalid


@pytest.fixture
def cog_user(app):
    u = User(username="cg", email="cg@example.com", cognito_sub="sub-cg")
    db.session.add(u)
    db.session.commit()
    return u


def _tokens(exp=3600):
    return {"access_token": "acc-1", "id_token": "id-1", "refresh_token": "ref-1", "expires_in": exp}


def test_create_persists_encrypted_row(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    row = session_store.get(sid)
    assert row.user_id == cog_user.id
    assert row.cognito_username == "cg"
    # ham token DB'de düz metin OLMAMALI (şifreli saklanır).
    assert row.access_token != "acc-1"
    assert session_store.current_access_token(sid) == "acc-1"


def test_valid_token_returned_without_refresh(app, cog_user, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(cognito_service, "refresh_tokens", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    assert session_store.get_valid_access_token(sid) == "acc-1"
    assert called["n"] == 0  # yenileme yok


def test_expired_token_triggers_refresh(app, cog_user, monkeypatch):
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda ref, uname: {"access_token": "acc-2", "id_token": "", "expires_in": 3600})
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    # süreyi geçmişe çek
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert session_store.get_valid_access_token(sid) == "acc-2"


def test_refresh_failure_invalidates(app, cog_user, monkeypatch):
    def boom(ref, uname):
        raise cognito_service.CognitoServiceError("Oturum yenilenemedi.", "NotAuthorizedException")
    monkeypatch.setattr(cognito_service, "refresh_tokens", boom)
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    with pytest.raises(SessionInvalid):
        session_store.get_valid_access_token(sid)
    assert session_store.get(sid) is None  # satır silindi


def test_delete_removes_row(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    session_store.delete(sid)
    assert session_store.get(sid) is None
