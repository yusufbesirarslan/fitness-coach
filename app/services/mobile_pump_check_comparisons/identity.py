"""Opaque comparison identity and directional create-command fingerprint."""
import base64
import hashlib
import hmac
import json
import re
import secrets


ID_DOMAIN = b"axisai/mobile-pump-check-comparison/id/v1"
FINGERPRINT_DOMAIN = "axisai/mobile-pump-check-comparison-create/v1"
_TOKEN_BYTES = 18
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")


def _subkey(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, ID_DOMAIN, hashlib.sha256).digest()


def new_comparison_id(secret, user_id, nonce=None):
    """Create a random indexed comparison token bound to its owner."""
    nonce = secrets.token_bytes(32) if nonce is None else bytes(nonce)
    owner = str(int(user_id)).encode("ascii")
    digest = hmac.new(
        _subkey(secret), owner + b"\x00" + nonce, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_TOKEN_BYTES]).decode("ascii")


def is_valid_comparison_id(token):
    return isinstance(token, str) and bool(_TOKEN_RE.fullmatch(token))


def fingerprint(baseline_token, current_token, version):
    semantic = {
        "domain": FINGERPRINT_DOMAIN,
        "baseline_pump_check_id": baseline_token,
        "current_pump_check_id": current_token,
        "analysis_version": version,
    }
    encoded = json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
