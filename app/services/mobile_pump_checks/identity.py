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
    key = _subkey(secret)
    owner = str(int(user_id)).encode("ascii")
    plain = int(row_id).to_bytes(8, "big", signed=False)
    mask = hmac.new(key, b"mask\x00" + owner, hashlib.sha256).digest()[:8]
    cipher = bytes(left ^ right for left, right in zip(plain, mask))
    tag = hmac.new(key, owner + b"\x00" + cipher, hashlib.sha256).digest()[:10]
    return base64.urlsafe_b64encode(cipher + tag).decode("ascii")


def resolve_pump_check_row_id(secret, user_id, token):
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    try:
        payload = base64.urlsafe_b64decode(token.encode("ascii"))
    except ValueError:
        return None
    if len(payload) != _TOKEN_BYTES:
        return None
    cipher, supplied_tag = payload[:8], payload[8:]
    key = _subkey(secret)
    owner = str(int(user_id)).encode("ascii")
    expected_tag = hmac.new(
        key, owner + b"\x00" + cipher, hashlib.sha256).digest()[:10]
    if not hmac.compare_digest(supplied_tag, expected_tag):
        return None
    mask = hmac.new(key, b"mask\x00" + owner, hashlib.sha256).digest()[:8]
    row_id = int.from_bytes(
        bytes(left ^ right for left, right in zip(cipher, mask)), "big")
    return row_id if row_id > 0 else None


def matches_pump_check_id(secret, user_id, row_id, token):
    resolved = resolve_pump_check_row_id(secret, user_id, token)
    return resolved is not None and hmac.compare_digest(
        str(resolved), str(int(row_id)))
