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

from app.config import COGNITO_REFRESH_SKEW_SECONDS, COGNITO_TOKEN_ENC_KEY
from app.extensions import db
from app.models import CognitoSession
from app.services import cognito_service

_logger = logging.getLogger(__name__)
_fernet = None


class SessionInvalid(Exception):
    pass


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


def _enc(value):
    return _get_fernet().encrypt((value or "").encode()).decode()


def _dec(value):
    return _get_fernet().decrypt(value.encode()).decode()


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


def get_valid_access_token(session_id):
    row = get(session_id)
    if not row:
        raise SessionInvalid("no_session")
    skew = timedelta(seconds=COGNITO_REFRESH_SKEW_SECONDS)
    if row.access_token_exp and (row.access_token_exp - datetime.utcnow()) > skew:
        return _dec(row.access_token)
    # süresi dolmuş / dolmak üzere → yenile
    try:
        refreshed = cognito_service.refresh_tokens(_dec(row.refresh_token), row.cognito_username)
    except cognito_service.CognitoServiceError:
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
