"""Opaque, owner-bound revision for one authoritative ``MealLog`` state."""
import base64
import hashlib
import hmac
import math
import struct
from dataclasses import dataclass
from datetime import datetime, timezone


_SUBKEY_INFO = b"axisai/mobile-nutrition/diary-entry-revision/v1"
_TOKEN_BYTES = 18


@dataclass(frozen=True)
class DiaryEntryRevisionState:
    user_id: int
    entry_id: int
    meal_label: "str | None"
    description: "str | None"
    energy_kcal: "float | None"
    protein_g: "float | None"
    carbohydrate_g: "float | None"
    fat_g: "float | None"
    diary_date: "str | None"
    source: "str | None"
    idempotency_key: "str | None"
    idempotency_fingerprint: "str | None"
    photo_key: "str | None"
    created_at: "datetime | None"


def _subkey(secret):
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()


def _integer(value):
    return b"i" + struct.pack(">q", int(value))


def _text(value):
    if value is None:
        return b"n"
    encoded = str(value).encode("utf-8")
    return b"s" + struct.pack(">I", len(encoded)) + encoded


def _number(value):
    if value is None:
        return b"n"
    number = float(value)
    if math.isnan(number):
        return b"f:NaN"
    if math.isinf(number):
        return b"f:+Inf" if number > 0 else b"f:-Inf"
    if number == 0:
        number = 0.0
    return b"f" + struct.pack(">d", number)


def _timestamp(value):
    if value is None:
        return b"n"
    if not isinstance(value, datetime):
        raise TypeError("created_at must be a datetime or None")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return _text(value.isoformat(timespec="microseconds"))


def _canonical_state(state):
    if not isinstance(state, DiaryEntryRevisionState):
        raise TypeError("state must be a DiaryEntryRevisionState")
    return b"".join((
        b"u" + _integer(state.user_id),
        b"e" + _integer(state.entry_id),
        b"m" + _text(state.meal_label),
        b"d" + _text(state.description),
        b"c" + _number(state.energy_kcal),
        b"p" + _number(state.protein_g),
        b"h" + _number(state.carbohydrate_g),
        b"a" + _number(state.fat_g),
        b"y" + _text(state.diary_date),
        b"s" + _text(state.source),
        b"k" + _text(state.idempotency_key),
        b"g" + _text(state.idempotency_fingerprint),
        b"o" + _text(state.photo_key),
        b"t" + _timestamp(state.created_at),
    ))


def diary_entry_revision(secret, state):
    digest = hmac.new(
        _subkey(secret), _canonical_state(state), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_TOKEN_BYTES]).decode("ascii")


def matches_diary_entry_revision(secret, state, token):
    if not isinstance(token, str) or not token:
        return False
    return hmac.compare_digest(diary_entry_revision(secret, state), token)
