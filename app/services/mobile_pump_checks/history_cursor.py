"""Opaque, owner-bound pagination cursor for canonical Pump Check history.

The cursor carries the seek position of a keyset page: the `captured_at` and the
internal row id of the last item already delivered. Both are server concerns, so
the cursor is ENCRYPTED rather than merely signed. A signed-but-readable cursor
would publish sequential database ids as public API semantics — a client could
decode one and learn how many Pump Check rows exist, and how many were written
between two of its own.

Owner binding lives inside the sealed payload. It is a second line of defence,
never the first: the history query filters on the authenticated user, so a
foreign cursor could not widen a result set even if it verified.

Pure module — no Flask, no database, no logging.
"""
import base64
import hashlib
import hmac
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken


_SUBKEY_INFO = b"axisai/mobile-pump-check/history-cursor/v1"
CURSOR_VERSION = "v1"
MAX_CURSOR_LENGTH = 512
_FIELD_SEPARATOR = "|"


class InvalidCursor(ValueError):
    """Raised for every rejected cursor, whatever the reason."""


def _cipher(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    subkey = hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(subkey))


def _seal(secret, payload):
    return _cipher(secret).encrypt(payload).decode("ascii")


def encode_cursor(secret, user_id, captured_at, row_id):
    payload = _FIELD_SEPARATOR.join((
        CURSOR_VERSION,
        str(int(user_id)),
        captured_at.isoformat(timespec="microseconds"),
        str(int(row_id)),
    ))
    return _seal(secret, payload.encode("utf-8"))


def decode_cursor(secret, user_id, cursor):
    """Return `(captured_at, row_id)` or raise `InvalidCursor`.

    Every failure mode collapses to one exception so the boundary can answer
    with one deterministic error and never a 500.
    """
    if not isinstance(cursor, str) or not cursor:
        raise InvalidCursor("cursor must be a non-empty string")
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidCursor("cursor is too long")
    try:
        raw = _cipher(secret).decrypt(cursor.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError, ValueError, TypeError) as exc:
        raise InvalidCursor("cursor is not authentic") from exc
    try:
        payload = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidCursor("cursor payload is not text") from exc

    fields = payload.split(_FIELD_SEPARATOR)
    if len(fields) != 4:
        raise InvalidCursor("cursor payload is malformed")
    version, owner, captured_at, row_id = fields
    if version != CURSOR_VERSION:
        raise InvalidCursor("cursor version is not supported")
    if not _is_integer(owner) or int(owner) != int(user_id):
        raise InvalidCursor("cursor belongs to another owner")
    if not _is_integer(row_id):
        raise InvalidCursor("cursor tie-break is not an integer")
    try:
        parsed = datetime.fromisoformat(captured_at)
    except ValueError as exc:
        raise InvalidCursor("cursor position is not a timestamp") from exc
    if parsed.tzinfo is not None:
        raise InvalidCursor("cursor position must be naive UTC")
    return parsed, int(row_id)


def _is_integer(value):
    return bool(value) and (value[1:] if value[0] == "-" else value).isdigit()
