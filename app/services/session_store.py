# app/services/session_store.py
"""CognitoSession token deposu — Fernet şifreleme + süre-dolumunda yenileme.

Ham access/refresh token'lar ASLA düz metin saklanmaz/loglanmaz. Anahtar
COGNITO_TOKEN_ENC_KEY (geçerli Fernet anahtarı) olmalı; yalnız dev/test'te
SECRET_KEY'den deterministik türetmeye düşülür (S2 — wearable anahtarıyla
aynı kural: SECRET_KEY oturumları da imzalar, sızarsa DB'deki OAuth
token'ları da çözmemeli).
"""
import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from app.config import (COGNITO_REFRESH_SKEW_SECONDS,
                        COGNITO_SESSION_ABSOLUTE_DAYS,
                        COGNITO_SESSION_IDLE_HOURS,
                        COGNITO_TOKEN_ENC_KEY)
from app.extensions import db
from app.models import CognitoSession
from app.services import cognito_service

_logger = logging.getLogger(__name__)
_fernet = None


class SessionInvalid(Exception):
    """Oturum KESİN olarak geçersiz — satır silinir, kullanıcı çıkışa düşer."""
    pass


class SessionTransient(Exception):
    """Cognito GEÇİCİ olarak ulaşılamadı — oturum SAĞLAM, satır KORUNUR.

    H1: refresh yolundaki her hata "bu oturum ölü" demek DEĞİLDİR. Throttle
    (TooManyRequests), Cognito iç hatası veya bir ağ/timeout kesintisi geçicidir;
    bunlarda satırı silmek kullanıcıyı geri dönüşsüz olarak dışarı atar. Access
    token'lar ~1 saatte bir yenilendiği için bu, tek bir Cognito throttle
    olayında KORELE bir toplu logout üretir. Çağıran (require_auth) bunu 503 +
    Retry-After'a çevirir; kullanıcı oturumunu KAYBETMEZ.
    """
    pass


# Cognito hata kodu → GEÇİCİ mi? Boş kod (_wrap'in "beklenmeyen hata" dalı) tam
# olarak botocore connect/read timeout ve ağ kesintilerinin düştüğü yerdir —
# geçici sayılır. Listede olmayan her kod (NotAuthorizedException,
# UserNotFoundException, RefreshFailed, ...) KESİN reddir: satır silinir.
_TRANSIENT_COGNITO_CODES = frozenset({
    "",  # kodsuz: ağ/timeout/beklenmeyen — bkz. cognito_service._wrap
    "InternalErrorException",
    "LimitExceededException",
    "ServiceUnavailableException",
    "TooManyRequestsException",
})


def _is_transient(exc):
    return (getattr(exc, "code", "") or "") in _TRANSIENT_COGNITO_CODES


def _get_fernet():
    global _fernet
    if _fernet is None:
        if COGNITO_TOKEN_ENC_KEY:
            key = COGNITO_TOKEN_ENC_KEY.encode()
        else:
            from flask import current_app
            is_dev = (current_app.config.get("TESTING") or current_app.debug
                      or os.environ.get("FLASK_ENV") == "development")
            if not is_dev:
                # Asıl kapı boot'tadır (config._enforce_cognito_token_key);
                # bu, o kapı atlanırsa prod'da sessiz SECRET_KEY türetmesini
                # kesen ikinci savunma hattı.
                raise RuntimeError(
                    "COGNITO_TOKEN_ENC_KEY must be set outside debug/test "
                    "environments (see wearables/crypto.py precedent)")
            secret = current_app.config["SECRET_KEY"].encode()
            key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        _fernet = Fernet(key)
    return _fernet


def encrypt_token(value):
    return _get_fernet().encrypt((value or "").encode()).decode()


def decrypt_token(value):
    return _get_fernet().decrypt(value.encode()).decode()


def _enc(value):
    return encrypt_token(value)


def _dec(value):
    return decrypt_token(value)


def create(user, tokens, cognito_username):
    sid = secrets.token_urlsafe(32)
    exp = datetime.utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    row = CognitoSession(
        session_id=sid, user_id=user.id, cognito_username=cognito_username,
        access_token=_enc(tokens["access_token"]),
        refresh_token=_enc(tokens["refresh_token"]),
        access_token_exp=exp,
    )
    db.session.add(row)
    db.session.commit()
    return sid


def get(session_id):
    if not session_id:
        return None
    return CognitoSession.query.filter_by(session_id=session_id).first()


def current_access_token(session_id):
    row = get(session_id)
    return _dec(row.access_token) if row else None


def get_valid_access_token(session_id, expected_user_id=None):
    row = get(session_id)
    if not row:
        raise SessionInvalid("no_session")
    if expected_user_id is not None and row.user_id != expected_user_id:
        delete(session_id)
        raise SessionInvalid("user_mismatch")
    now = datetime.utcnow()
    absolute_deadline = timedelta(days=COGNITO_SESSION_ABSOLUTE_DAYS)
    if row.created_at and now - row.created_at > absolute_deadline:
        delete(session_id)
        raise SessionInvalid("absolute_timeout")
    idle_deadline = timedelta(hours=COGNITO_SESSION_IDLE_HOURS)
    if row.last_used_at and now - row.last_used_at > idle_deadline:
        delete(session_id)
        raise SessionInvalid("idle_timeout")
    skew = timedelta(seconds=COGNITO_REFRESH_SKEW_SECONDS)
    if row.access_token_exp and (row.access_token_exp - now) > skew:
        return _dec(row.access_token)
    # süresi dolmuş / dolmak üzere → yenile
    try:
        refreshed = cognito_service.refresh_tokens(_dec(row.refresh_token), row.cognito_username)
    except cognito_service.CognitoServiceError as e:
        # H1: geçici Cognito kesintisi oturumu ÖLDÜRMEZ. Yalnızca KESİN ret
        # (NotAuthorized = refresh token iptal/süresi dolmuş) satırı siler.
        if _is_transient(e):
            _logger.warning(
                "[SESSION] Cognito geçici olarak ulaşılamadı (%s) — oturum korunuyor",
                e.code or type(e).__name__)
            raise SessionTransient("cognito_unavailable")
        delete(session_id)
        raise SessionInvalid("refresh_failed")
    row.access_token = _enc(refreshed["access_token"])
    row.access_token_exp = datetime.utcnow() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
    db.session.commit()
    return refreshed["access_token"]


def touch(session_id):
    row = get(session_id)
    if row:
        row.last_used_at = datetime.utcnow()
        db.session.commit()


def delete(session_id):
    row = get(session_id)
    if row:
        db.session.delete(row)
        db.session.commit()


def delete_for_user(user_id):
    """Delete every application-managed Cognito session for one local user."""
    removed = (CognitoSession.query
               .filter_by(user_id=user_id)
               .delete(synchronize_session=False))
    db.session.commit()
    return removed


# Cognito refresh token'ının varsayılan geçerliliği 30 gündür; bu kadar süre
# dokunulmamış bir oturum zaten yenilenemez — satırı tutmanın tek etkisi
# tablonun sınırsız büyümesi ve süresi geçmiş şifreli token saklamaktır (I5).
PURGE_AFTER_DAYS = 30


def purge_expired(older_than_days=PURGE_AFTER_DAYS):
    """last_used_at'i eşikten eski oturum satırlarını sil; silinen sayıyı döndür.

    Satırlar normalde yalnız logout/refresh-hatasında silinir; sessiz terk
    edilen oturumlar için periyodik süpürme gerekir (weekly-reset CLI çağırır).
    """
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    removed = (CognitoSession.query
               .filter(CognitoSession.last_used_at < cutoff)
               .delete(synchronize_session=False))
    db.session.commit()
    if removed:
        _logger.info("[SESSION] %d süresi geçmiş Cognito oturumu silindi", removed)
    return removed
