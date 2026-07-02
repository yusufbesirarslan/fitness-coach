import base64
import hashlib
import os

from cryptography.fernet import Fernet
from flask import current_app


def _fallback_key():
    secret = current_app.config.get("SECRET_KEY") or os.environ.get("SECRET_KEY", "")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    key = os.environ.get("WEARABLE_TOKEN_KEY", "").strip()
    if key:
        return Fernet(key.encode("utf-8"))
    is_dev = current_app.config.get("TESTING") or current_app.debug or os.environ.get("FLASK_ENV") == "development"
    if not is_dev:
        raise RuntimeError("WEARABLE_TOKEN_KEY must be set outside debug/test environments")
    return Fernet(_fallback_key())


def encrypt_token(value):
    if value is None:
        return None
    return _fernet().encrypt(str(value).encode("utf-8")).decode("utf-8")


def decrypt_token(value):
    if not value:
        return None
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
