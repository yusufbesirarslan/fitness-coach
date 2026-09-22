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


def test_fernet_requires_dedicated_key_outside_dev(app, monkeypatch):
    # S2: COGNITO_TOKEN_ENC_KEY yokken SECRET_KEY'den sessiz türetme yalnız
    # dev/test'te kabul edilir; prod'da wearable anahtarı gibi (crypto.py)
    # özel anahtar zorunlu — SECRET_KEY sızarsa DB'deki tüm OAuth token'lar
    # çözülebilir olmamalı.
    monkeypatch.setattr(session_store, "_fernet", None)
    monkeypatch.setattr(session_store, "COGNITO_TOKEN_ENC_KEY", "")
    monkeypatch.setitem(app.config, "TESTING", False)
    monkeypatch.setattr(app, "debug", False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    with pytest.raises(RuntimeError):
        session_store._get_fernet()


def test_fernet_falls_back_to_secret_key_in_dev(app, monkeypatch):
    monkeypatch.setattr(session_store, "_fernet", None)
    monkeypatch.setattr(session_store, "COGNITO_TOKEN_ENC_KEY", "")
    f = session_store._get_fernet()  # TESTING=True → türetme çalışır
    assert f.decrypt(f.encrypt(b"x")) == b"x"


def test_public_cipher_helpers_are_legacy_compatible(app):
    ciphertext = session_store.encrypt_token("provider-token")
    assert ciphertext != "provider-token"
    assert session_store.decrypt_token(ciphertext) == "provider-token"
    assert session_store._dec(session_store._enc("legacy-token")) == "legacy-token"


def test_boot_enforces_dedicated_key_when_cognito_enabled(monkeypatch):
    # S2 (boot yarısı): _get_fernet tembeldir (ilk login'de çalışır); yalnız
    # kullanım anında patlasaydı deploy gate yeşil geçip login 500'lenirdi.
    # Boot'ta fail-fast → health gate yakalar ve rollback eder.
    import app.config as config
    monkeypatch.setattr(config, "COGNITO_ENABLED", True)
    monkeypatch.setattr(config, "COGNITO_TOKEN_ENC_KEY", "")
    with pytest.raises(RuntimeError):
        config._enforce_cognito_token_key(is_dev=False)
    config._enforce_cognito_token_key(is_dev=True)  # dev serbest
    monkeypatch.setattr(config, "COGNITO_TOKEN_ENC_KEY", "k")
    config._enforce_cognito_token_key(is_dev=False)  # anahtar varsa geçer
    monkeypatch.setattr(config, "COGNITO_ENABLED", False)
    monkeypatch.setattr(config, "COGNITO_TOKEN_ENC_KEY", "")
    config._enforce_cognito_token_key(is_dev=False)  # Cognito kapalıysa gerek yok


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
    """KESİN ret (refresh token iptal/süresi dolmuş) → satır SİLİNİR."""
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


# H1: GEÇİCİ hata ≠ KESİN ret. cognito_service._wrap HER şeyi (throttle, Cognito
# iç hatası, botocore connect/read timeout) CognitoServiceError'a çevirir; eskiden
# hepsi satırı SİLİYORDU. Access token'lar ~saatte bir yenilendiği için bu, tek bir
# Cognito throttle olayında korele bir toplu logout üretebiliyordu.
@pytest.mark.parametrize("code", [
    "TooManyRequestsException", "InternalErrorException",
    "LimitExceededException", "ServiceUnavailableException",
    "",  # kodsuz = ağ/timeout (_wrap'in "beklenmeyen hata" dalı)
])
def test_transient_refresh_failure_preserves_row(app, cog_user, monkeypatch, code):
    def transient(ref, uname):
        raise cognito_service.CognitoServiceError("gecici", code)
    monkeypatch.setattr(cognito_service, "refresh_tokens", transient)
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()

    with pytest.raises(session_store.SessionTransient):
        session_store.get_valid_access_token(sid)
    assert session_store.get(sid) is not None  # oturum KORUNDU


def test_transient_is_not_a_session_invalid_subclass():
    """Çağıranlar ikisini AYIRT edebilmeli; SessionTransient bir SessionInvalid
    olsaydı `except SessionInvalid` onu da yakalar ve yıkıcı dala sokardı."""
    assert not issubclass(session_store.SessionTransient, SessionInvalid)


def test_purge_expired_removes_only_stale_sessions(app, cog_user):
    # I5: satırlar yalnız logout/refresh-hatasında siliniyordu → tablo sınırsız
    # büyüyüp süresi geçmiş şifreli token tutuyordu. 30+ gün dokunulmamış
    # oturumlar (Cognito refresh penceresi de dolmuştur) süpürülmeli.
    from datetime import datetime, timedelta
    stale_sid = session_store.create(cog_user, _tokens(), "cg")
    fresh_sid = session_store.create(cog_user, _tokens(), "cg")
    stale = session_store.get(stale_sid)
    stale.last_used_at = datetime.utcnow() - timedelta(days=31)
    db.session.commit()

    removed = session_store.purge_expired()
    assert removed == 1
    assert session_store.get(stale_sid) is None
    assert session_store.get(fresh_sid) is not None


def test_delete_removes_row(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    session_store.delete(sid)
    assert session_store.get(sid) is None


def test_idle_timeout_deletes_session(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    row = session_store.get(sid)
    row.last_used_at = datetime.utcnow() - timedelta(hours=25)
    db.session.commit()

    with pytest.raises(SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cog_user.id)
    assert exc.value.args[0] == "idle_timeout"
    assert session_store.get(sid) is None


def test_absolute_timeout_deletes_session(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    row = session_store.get(sid)
    row.created_at = datetime.utcnow() - timedelta(days=8)
    db.session.commit()

    with pytest.raises(SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cog_user.id)
    assert exc.value.args[0] == "absolute_timeout"
    assert session_store.get(sid) is None


def test_expected_user_mismatch_deletes_session(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")

    with pytest.raises(SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cog_user.id + 1)
    assert exc.value.args[0] == "user_mismatch"
    assert session_store.get(sid) is None


# --- F12: refresh runs snapshot -> provider (no transaction) -> re-lock -------
# The provider stubs below act as "another request" by committing through the
# same store API while the refresh is between phase 1 and phase 3.

def _expired(cog_user):
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    return sid


def _material(sid):
    row = session_store.get(sid)
    return (row.access_token, row.refresh_token, row.access_token_exp)


def _renewed(access, refresh=None):
    tokens = {"access_token": access, "id_token": "", "expires_in": 3600}
    if refresh is not None:
        tokens["refresh_token"] = refresh
    return tokens


def _commit_winner(sid, access, expires_at):
    (CognitoSession.query.filter_by(session_id=sid).update({
        CognitoSession.access_token: session_store.encrypt_token(access),
        CognitoSession.access_token_exp: expires_at,
    }, synchronize_session=False))
    db.session.commit()


def test_fresh_token_makes_no_provider_call_and_no_write(app, cog_user, monkeypatch):
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    before = _material(sid)
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda *a: pytest.fail("fresh token must not refresh"))
    assert session_store.get_valid_access_token(sid, cog_user.id) == "acc-1"
    assert _material(sid) == before


def test_refresh_calls_provider_once_outside_any_transaction(app, cog_user, monkeypatch):
    sid = _expired(cog_user)
    refresh_ciphertext = session_store.get(sid).refresh_token
    calls = []

    def renew(refresh, username):
        calls.append((refresh, username, db.session().in_transaction()))
        return _renewed("acc-2", refresh)

    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)
    assert session_store.get_valid_access_token(sid, cog_user.id) == "acc-2"
    assert calls == [("ref-1", "cg", False)]
    row = session_store.get(sid)
    assert session_store.decrypt_token(row.access_token) == "acc-2"
    assert row.access_token_exp > datetime.utcnow() + timedelta(minutes=55)
    # An unrotated refresh token keeps its ciphertext byte-for-byte.
    assert row.refresh_token == refresh_ciphertext


def test_rotated_refresh_token_is_persisted_with_its_access_token(app, cog_user, monkeypatch):
    sid = _expired(cog_user)
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda refresh, username: _renewed("acc-2", "ref-2"))
    assert session_store.get_valid_access_token(sid, cog_user.id) == "acc-2"
    row = session_store.get(sid)
    assert session_store.decrypt_token(row.access_token) == "acc-2"
    assert session_store.decrypt_token(row.refresh_token) == "ref-2"


def test_corrupted_refresh_ciphertext_fails_before_the_provider(app, cog_user, monkeypatch):
    from cryptography.fernet import InvalidToken
    sid = _expired(cog_user)
    row = session_store.get(sid)
    row.refresh_token = "not-a-fernet-token"
    db.session.commit()
    before = _material(sid)
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda *a: pytest.fail("undecryptable token reached Cognito"))
    with pytest.raises(InvalidToken):
        session_store.get_valid_access_token(sid, cog_user.id)
    assert _material(sid) == before


def test_row_deleted_during_provider_call_is_never_recreated(app, cog_user, monkeypatch):
    sid = _expired(cog_user)

    def renew(refresh, username):
        assert session_store.delete_for_user(cog_user.id) == 1
        return _renewed("acc-2", refresh)

    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)
    with pytest.raises(SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cog_user.id)
    assert exc.value.args[0] == "no_session"
    assert CognitoSession.query.filter_by(user_id=cog_user.id).count() == 0


def test_winner_committed_during_provider_call_is_returned_not_overwritten(
        app, cog_user, monkeypatch):
    sid = _expired(cog_user)

    def renew(refresh, username):
        _commit_winner(sid, "acc-winner", datetime.utcnow() + timedelta(hours=1))
        return _renewed("acc-loser", refresh)

    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)
    assert session_store.get_valid_access_token(sid, cog_user.id) == "acc-winner"
    assert session_store.current_access_token(sid) == "acc-winner"


def test_definitive_rejection_after_a_winner_keeps_the_winner(app, cog_user, monkeypatch):
    sid = _expired(cog_user)

    def rejected(refresh, username):
        _commit_winner(sid, "acc-winner", datetime.utcnow() + timedelta(hours=1))
        raise cognito_service.CognitoServiceError("x", "NotAuthorizedException")

    monkeypatch.setattr(cognito_service, "refresh_tokens", rejected)
    assert session_store.get_valid_access_token(sid, cog_user.id) == "acc-winner"
    assert session_store.current_access_token(sid) == "acc-winner"


def test_changed_but_unusable_winner_state_is_a_transient_conflict(
        app, cog_user, monkeypatch):
    sid = _expired(cog_user)

    def rejected(refresh, username):
        _commit_winner(sid, "acc-short", datetime.utcnow())
        raise cognito_service.CognitoServiceError("x", "NotAuthorizedException")

    monkeypatch.setattr(cognito_service, "refresh_tokens", rejected)
    with pytest.raises(session_store.SessionTransient) as exc:
        session_store.get_valid_access_token(sid, cog_user.id)
    assert exc.value.args[0] == "refresh_conflict"
    assert session_store.current_access_token(sid) == "acc-short"


def test_touch_during_provider_call_is_not_a_refresh_conflict(app, cog_user, monkeypatch):
    sid = _expired(cog_user)

    def renew(refresh, username):
        session_store.touch(sid)
        return _renewed("acc-2", refresh)

    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)
    assert session_store.get_valid_access_token(sid, cog_user.id) == "acc-2"
    assert session_store.current_access_token(sid) == "acc-2"


def test_idle_deadline_is_rechecked_after_the_provider_answers(app, cog_user, monkeypatch):
    sid = _expired(cog_user)

    def renew(refresh, username):
        # Stands in for the clock crossing the idle deadline mid-call.
        (CognitoSession.query.filter_by(session_id=sid).update({
            CognitoSession.last_used_at: datetime.utcnow() - timedelta(hours=25),
        }, synchronize_session=False))
        db.session.commit()
        return _renewed("acc-2", refresh)

    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)
    with pytest.raises(SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cog_user.id)
    assert exc.value.args[0] == "idle_timeout"
    assert session_store.get(sid) is None


def test_touch_after_delete_neither_raises_nor_resurrects(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    session_store.get(sid)  # the request already holds the row
    session_store.delete_for_user(cog_user.id)
    session_store.touch(sid)
    assert session_store.get(sid) is None


def test_touch_updates_activity_only(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    row = session_store.get(sid)
    row.last_used_at = datetime.utcnow() - timedelta(hours=1)
    db.session.commit()
    before = _material(sid)
    session_store.touch(sid)
    assert _material(sid) == before
    assert session_store.get(sid).last_used_at > datetime.utcnow() - timedelta(minutes=1)
