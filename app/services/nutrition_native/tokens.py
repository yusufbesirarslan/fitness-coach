"""Owner-bound, domain-separated opaque tokens for the NUTR-PR7 native contract.

The same construction ``mobile_nutrition/identity.py`` and ``revision.py``
established for ledger rows: a keyed digest under a subkey of ``SECRET_KEY``
whose derivation label names exactly one token class. Nothing here is stored —
every token is re-derivable from canonical state, so no schema exists to hold
it — and every class carries its own label, so a diary entry token can never be
replayed as a plan revision, a supplement id, a planned-meal id or a history
cursor (and vice versa).

Pure: stdlib only, no Flask, no ORM. Callers pass the key.
"""
import base64
import binascii
import hashlib
import hmac
import json
import math
import struct
from datetime import datetime, timezone


# One label per token class. The trailing version lets a future format be a new
# label instead of a silent reinterpretation of tokens already in clients.
PLAN_REVISION = b"axisai/nutrition-native/plan-revision/v1"
PLANNED_MEAL_ID = b"axisai/nutrition-native/planned-meal-id/v1"
PLAN_PROPOSAL = b"axisai/nutrition-native/plan-proposal/v1"
HYDRATION_REVISION = b"axisai/nutrition-native/hydration-revision/v1"
SUPPLEMENT_ID = b"axisai/nutrition-native/supplement-id/v1"
SUPPLEMENT_REVISION = b"axisai/nutrition-native/supplement-revision/v1"
CABINET_REVISION = b"axisai/nutrition-native/supplement-cabinet-revision/v1"
HISTORY_CURSOR = b"axisai/nutrition-native/history-cursor/v1"
PLANNED_MEAL_COMMAND = "axisai/nutrition-native/planned-meal-log/v1"
# LP15-C: signed confirmation proof of one analyzed menu candidate (issued by
# `app/services/mobile_menu.py`; verified and consumed by LP15-D).
MENU_ITEM_PROOF = b"axisai/mobile-menu-item-proof/v1"

DOMAINS = (
    PLAN_REVISION, PLANNED_MEAL_ID, PLAN_PROPOSAL, HYDRATION_REVISION,
    SUPPLEMENT_ID, SUPPLEMENT_REVISION, CABINET_REVISION, HISTORY_CURSOR,
    MENU_ITEM_PROOF,
)

# 144 bits, base64url without padding — the width of every sibling identity.
TOKEN_BYTES = 18


def _subkey(secret, domain):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, domain, hashlib.sha256).digest()


def integer(value):
    return b"i" + struct.pack(">q", int(value))


def text(value):
    if value is None:
        return b"n"
    encoded = str(value).encode("utf-8")
    return b"s" + struct.pack(">I", len(encoded)) + encoded


def number(value):
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"b" + (b"1" if value else b"0")
    numeric = float(value)
    if math.isnan(numeric):
        return b"f:NaN"
    if math.isinf(numeric):
        return b"f:+Inf" if numeric > 0 else b"f:-Inf"
    if numeric == 0:
        numeric = 0.0
    return b"f" + struct.pack(">d", numeric)


def timestamp(value):
    if value is None:
        return b"n"
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime or None")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return text(value.isoformat(timespec="microseconds"))


def digest_token(secret, domain, *parts):
    """The opaque token of one canonical state under one token class."""
    if domain not in DOMAINS:
        raise ValueError("unknown token domain")
    message = b"".join(parts)
    digest = hmac.new(_subkey(secret, domain), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:TOKEN_BYTES]).decode("ascii")


def matches(expected, presented):
    """Constant-time equality; anything that is not a non-empty str fails."""
    if not isinstance(presented, str) or not presented:
        return False
    return hmac.compare_digest(expected, presented)


# ── signed payload tokens (history cursor, plan proposal) ───────────────────


class InvalidSignedToken(ValueError):
    pass


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value):
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def sign_payload(secret, domain, user_id, payload):
    """``<payload>.<mac>``: an integrity-protected, owner-bound value.

    The owner is bound through the MAC only — it is never written into the
    payload, so a token reveals nothing about whose it is. A token minted for
    user A therefore fails verification for user B exactly like a forgery.
    """
    if domain not in DOMAINS:
        raise ValueError("unknown token domain")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")
    mac = hmac.new(_subkey(secret, domain), integer(user_id) + body,
                   hashlib.sha256).digest()
    return _b64(body) + "." + _b64(mac)


def verify_payload(secret, domain, user_id, token, max_length=1024):
    """The payload dict, or ``InvalidSignedToken`` for anything else."""
    if domain not in DOMAINS:
        raise ValueError("unknown token domain")
    if not isinstance(token, str) or not token or len(token) > max_length:
        raise InvalidSignedToken("malformed")
    parts = token.split(".")
    if len(parts) != 2 or not all(parts):
        raise InvalidSignedToken("malformed")
    try:
        body = _unb64(parts[0])
        mac = _unb64(parts[1])
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise InvalidSignedToken("malformed") from None
    expected = hmac.new(_subkey(secret, domain), integer(user_id) + body,
                        hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise InvalidSignedToken("signature")
    try:
        payload = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidSignedToken("malformed") from None
    if not isinstance(payload, dict):
        raise InvalidSignedToken("malformed")
    return payload
