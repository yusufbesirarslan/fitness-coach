"""Opaque, owner-bound identity for canonical Pump Check rows."""
import base64
import hashlib
import hmac
import re


_SUBKEY_INFO = b"axisai/mobile-pump-check/id/v1"
_TOKEN_BYTES = 18
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")


def _subkey(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()


def pump_check_id(secret, user_id, row_id):
    message = f"{int(user_id)}\x00{int(row_id)}".encode("ascii")
    digest = hmac.new(_subkey(secret), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_TOKEN_BYTES]).decode("ascii")


def matches_pump_check_id(secret, user_id, row_id, token):
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return False
    return hmac.compare_digest(pump_check_id(secret, user_id, row_id), token)
