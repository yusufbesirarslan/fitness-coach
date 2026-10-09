"""Strict native weekly check-in wire contract (LP16-B). Pure.

Deliberately NOT the web parser in ``app/blueprints/tracking.py``: that one
fills a missing metric with 3, accepts ``true`` as 1 through ``int()``, turns
an unknown overload into ``kismen`` and lets ``1e400`` reach ``int()``. Here
nothing is defaulted, clamped or coerced:

- the body is exactly the six fields, as one JSON object (duplicate keys,
  unknown keys, missing keys, a non-object, ``NaN``/``Infinity`` literals or
  a non-JSON body → ``InvalidRequest``, 400);
- every field must have its JSON type (ratings: integer, never bool, never
  ``3.0`` or ``"3"``; weight: number, never bool or string; overload:
  string) → else ``InvalidRequest``, 400;
- a well-typed value outside the contract → ``InvalidValue`` (field, reason),
  422: weight not finite or outside 20–500 kg (``1e400`` parses to an
  infinite value and lands here), rating outside 1–5, overload not one of the
  wire tokens ``yes``/``partial``/``no``.

Wire tokens are locale-free and never translated; the stored legacy Turkish
token (``evet``/``kismen``/``hayir``) never reaches the wire.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal

from app.services.account_profile import BODY_WEIGHT_MAX_KG, BODY_WEIGHT_MIN_KG
from app.timeutil import app_date_of, to_app_tz

CONTRACT_VERSION = 1

RATING_FIELDS = ("training_intensity", "fatigue", "sleep_quality",
                 "nutrition_adherence")
FIELDS = frozenset(("weight_kg", "progressive_overload") + RATING_FIELDS)
RATING_MIN, RATING_MAX = 1, 5

# Closed wire enum → stored legacy token. The inverse is total on the three
# canonical stored tokens; any other stored value reads back as null.
OVERLOAD_WIRE_TO_STORED = {"yes": "evet", "partial": "kismen", "no": "hayir"}
OVERLOAD_STORED_TO_WIRE = {v: k for k, v in OVERLOAD_WIRE_TO_STORED.items()}

# Separates native intent from the web's legacy fingerprint (sha256 of the
# parsed legacy dict, no domain) on the shared
# ``uq_weekly_checkin_user_key`` column pair: the same key never replays
# across transports, it conflicts.
FINGERPRINT_DOMAIN = "native.v1"

MAX_BODY_BYTES = 4096

# Closed reason vocabulary of ``InvalidValue``.
REASON_OUT_OF_RANGE = "out_of_range"
REASON_UNSUPPORTED_VALUE = "unsupported_value"


class InvalidRequest(Exception):
    """Not the closed six-field JSON object of the right JSON types (400)."""


class InvalidValue(Exception):
    """A well-typed field whose value is outside the contract (422)."""

    def __init__(self, field, reason):
        super().__init__(field, reason)
        self.field = field
        self.reason = reason


class IdempotencyConflict(Exception):
    """The Idempotency-Key already belongs to a different intent (409)."""


@dataclass(frozen=True)
class NativeCheckIn:
    """One parsed native check-in, in wire vocabulary."""

    weight_kg: float
    training_intensity: int
    fatigue: int
    sleep_quality: int
    nutrition_adherence: int
    progressive_overload: str  # wire token: yes | partial | no


def _reject_constant(_name):
    raise InvalidRequest("non-standard JSON constant")


def _unique_object(pairs):
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise InvalidRequest("duplicate key")
    return dict(pairs)


def _decode(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_BODY_BYTES:
        raise InvalidRequest("body")
    try:
        return json.loads(
            bytes(raw).decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except InvalidRequest:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise InvalidRequest("not JSON") from error


def _is_json_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _weight(value):
    if not (_is_json_int(value) or isinstance(value, Decimal)):
        raise InvalidRequest("weight_kg type")
    try:
        weight = float(value)
    except OverflowError:
        raise InvalidValue("weight_kg", REASON_OUT_OF_RANGE) from None
    if not math.isfinite(weight) or not (
            BODY_WEIGHT_MIN_KG <= weight <= BODY_WEIGHT_MAX_KG):
        raise InvalidValue("weight_kg", REASON_OUT_OF_RANGE)
    return weight


def _rating(field, value):
    if not _is_json_int(value):
        raise InvalidRequest(f"{field} type")
    if not RATING_MIN <= value <= RATING_MAX:
        raise InvalidValue(field, REASON_OUT_OF_RANGE)
    return value


def _overload(value):
    if not isinstance(value, str):
        raise InvalidRequest("progressive_overload type")
    if value not in OVERLOAD_WIRE_TO_STORED:
        raise InvalidValue("progressive_overload", REASON_UNSUPPORTED_VALUE)
    return value


def parse_request(raw, *, is_json):
    """Raw request bytes → ``NativeCheckIn``, or ``InvalidRequest``/``InvalidValue``.

    Shape is decided before values, so a body with an unknown key is 400
    whatever its values; values are then checked in a fixed field order.
    """
    if not is_json:
        raise InvalidRequest("content type")
    data = _decode(raw)
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise InvalidRequest("shape")
    weight = _weight(data["weight_kg"])
    ratings = {field: _rating(field, data[field]) for field in RATING_FIELDS}
    overload = _overload(data["progressive_overload"])
    return NativeCheckIn(weight_kg=weight, progressive_overload=overload,
                         **ratings)


def fingerprint(command):
    """sha256 of the canonical semantic fields under the ``native.v1`` domain."""
    semantic = {
        "domain": FINGERPRINT_DOMAIN,
        "weight_kg": command.weight_kg,
        "training_intensity": command.training_intensity,
        "fatigue": command.fatigue,
        "sleep_quality": command.sleep_quality,
        "nutrition_adherence": command.nutrition_adherence,
        "progressive_overload": command.progressive_overload,
    }
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def iso_checked_in_at(value):
    """Naive-UTC instant → offset-aware ISO 8601 in Europe/Istanbul."""
    local = to_app_tz(value)
    return local.isoformat() if local is not None else None


def analysis_day(value):
    day = app_date_of(value)
    return day.isoformat() if day is not None else None


def checkin_payload(checked_in_at, command):
    """The closed POST body; identical for the 201 write and every 200 replay.

    Hand-written field list: no DB id, owner, key, fingerprint, stored Turkish
    token, session id or internal timestamp can leak by accident.
    """
    return {
        "contract_version": CONTRACT_VERSION,
        "checked_in_at": iso_checked_in_at(checked_in_at),
        "analysis_day": analysis_day(checked_in_at),
        "weight_kg": command.weight_kg,
        "training_intensity": command.training_intensity,
        "fatigue": command.fatigue,
        "sleep_quality": command.sleep_quality,
        "nutrition_adherence": command.nutrition_adherence,
        "progressive_overload": command.progressive_overload,
    }
