"""Persisted random, owner-bound mobile identity for Pump Check rows."""
import base64
import hashlib
import hmac
import re
import secrets


_SUBKEY_INFO = b"axisai/mobile-pump-check/id/v1"
_TOKEN_BYTES = 18
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")


def _subkey(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()


def new_pump_check_id(secret, user_id, nonce=None):
    """Create a random indexed token cryptographically bound to its owner."""
    nonce = secrets.token_bytes(32) if nonce is None else bytes(nonce)
    owner = str(int(user_id)).encode("ascii")
    digest = hmac.new(
        _subkey(secret), owner + b"\x00" + nonce, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_TOKEN_BYTES]).decode("ascii")


def is_valid_pump_check_id(token):
    return isinstance(token, str) and bool(_TOKEN_RE.fullmatch(token))
