"""Strict parsing, bounding and replay identity for native workout checkpoints.

A checkpoint is a **bounded full snapshot** of the client's durable execution
progress, never a patch (PR5 section 16): the whole snapshot plus the caller's
declared base revision is one self-describing request, which makes replay and
conflict detection a pure function of the request and the stored revision. A
sequence of micro-patches would need per-patch ordering state to be replay-safe;
a snapshot needs none.

Nothing here touches the database, Flask or a provider. Membership is validated
against the canonical workout projection the SESSION was started for, so Flutter
can never checkpoint an exercise that is not part of that workout, and can never
supply a replacement workout definition.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Optional

from .errors import InvalidIdempotencyKey, InvalidRevision, InvalidSessionRequest


# Bounds. Deliberately explicit and small: a workout is a bounded human activity,
# so every dimension has a product-plausible ceiling that a hostile client cannot
# exceed. MAX_EXERCISES mirrors the native read contract's own per-day bound.
MAX_EXERCISES = 32
MAX_SETS_PER_EXERCISE = 20
MAX_REPS = 1_000
MAX_WEIGHT_KG = 1_000.0
MAX_ELAPSED_SECONDS = 86_400
# A BACKSTOP, not a second limit. The per-dimension bounds above are the real
# contract, so this must sit comfortably above the largest snapshot they allow
# (32 exercises x 20 sets ~ 39 KB) -- otherwise a legitimate maximal workout
# would be unpersistable and the client would get a 400 it could not act on.
# It exists only to stop a pathological encoding, never a real workout.
MAX_SNAPSHOT_BYTES = 65_536

_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_REVISION_RE = re.compile(r"^(0|[1-9][0-9]{0,8})$")
_REASON_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_FINGERPRINT_DOMAIN = b"axisai:training-workout-checkpoint:v1\x00"

_SNAPSHOT_FIELDS = frozenset({"current_exercise_index", "elapsed_seconds", "exercises"})
_EXERCISE_FIELDS = frozenset({"exercise_id", "sets"})
_SET_FIELDS = frozenset({"index", "completed", "reps", "weight_kg"})


@dataclass(frozen=True)
class Checkpoint:
    """One validated snapshot plus its deterministic semantic fingerprint."""

    snapshot: dict
    fingerprint: str

    def to_json(self) -> str:
        return _canonical_json(self.snapshot)


def parse_idempotency_key(raw: object) -> str:
    """Validate an opaque key without normalizing or echoing its contents."""
    if not isinstance(raw, str) or _KEY_RE.fullmatch(raw) is None:
        raise InvalidIdempotencyKey("idempotency key is malformed")
    return raw


def parse_revision(raw: object) -> int:
    """Parse an ``If-Match`` base revision. Client timestamps are never accepted
    as concurrency authority - only this server-issued integer is."""
    if isinstance(raw, str):
        candidate = raw.strip().strip('"')
        if _REVISION_RE.fullmatch(candidate):
            return int(candidate)
    raise InvalidRevision("a valid If-Match revision is required")


def parse_optional_revision(raw: object) -> Optional[int]:
    """``If-Match`` where the header is optional (resume/abandon). Present but
    malformed is still an error - a client must not silently lose its guard."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return parse_revision(raw)


def parse_reason(raw: object) -> Optional[str]:
    """A bounded, opaque abandon reason code (never free-form user prose)."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidSessionRequest("reason must be a string")
    cleaned = raw.strip()
    if not cleaned:
        return None
    if _REASON_RE.fullmatch(cleaned) is None:
        raise InvalidSessionRequest("reason is not a bounded reason code")
    return cleaned


def parse_checkpoint(raw: object, allowed_exercise_ids) -> Checkpoint:
    """Validate one full snapshot against the session's canonical workout.

    ``allowed_exercise_ids`` is the ordered canonical exercise identity list of
    the workout the session was started for. Every entry must name one of them;
    unknown, duplicated or impossible entries are REJECTED, never dropped, so a
    client is never silently told its progress was saved when it was not.
    """
    allowed = tuple(allowed_exercise_ids)
    if not allowed:
        raise InvalidSessionRequest("the canonical workout has no exercises")
    if not isinstance(raw, dict) or isinstance(raw, bool):
        raise InvalidSessionRequest("checkpoint must be an object")
    if set(raw) != _SNAPSHOT_FIELDS:
        raise InvalidSessionRequest("checkpoint fields do not match the contract")

    exercises = _parse_exercises(raw["exercises"], allowed)
    elapsed = _parse_int(
        raw["elapsed_seconds"], 0, MAX_ELAPSED_SECONDS, "elapsed_seconds")
    index = _parse_int(
        raw["current_exercise_index"], 0, len(allowed) - 1,
        "current_exercise_index")

    snapshot = {
        "current_exercise_index": index,
        "elapsed_seconds": elapsed,
        "exercises": exercises,
    }
    encoded = _canonical_json(snapshot).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise InvalidSessionRequest("checkpoint exceeds its size bound")
    digest = hashlib.sha256(_FINGERPRINT_DOMAIN + encoded).hexdigest()
    return Checkpoint(snapshot=snapshot, fingerprint=digest)


def load_snapshot(raw: object) -> Optional[dict]:
    """Read a stored snapshot back for projection. A row that cannot be parsed
    projects as ``None`` rather than raising: a corrupt cache must never make an
    owned session unreadable."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_exercises(raw, allowed) -> list:
    if not isinstance(raw, list):
        raise InvalidSessionRequest("exercises must be a list")
    if len(raw) > MAX_EXERCISES:
        raise InvalidSessionRequest("exercise count exceeds its bound")
    seen = set()
    parsed = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != _EXERCISE_FIELDS:
            raise InvalidSessionRequest("exercise entry does not match the contract")
        exercise_id = item["exercise_id"]
        if not isinstance(exercise_id, str) or exercise_id not in allowed:
            raise InvalidSessionRequest("exercise is not part of the canonical workout")
        if exercise_id in seen:
            raise InvalidSessionRequest("duplicate exercise identity")
        seen.add(exercise_id)
        parsed.append({"exercise_id": exercise_id, "sets": _parse_sets(item["sets"])})
    # Canonical ordering: the stored snapshot follows the workout's own exercise
    # order, so two requests carrying the same progress in a different order
    # produce the SAME fingerprint and replay instead of conflicting.
    parsed.sort(key=lambda entry: allowed.index(entry["exercise_id"]))
    return parsed


def _parse_sets(raw) -> list:
    if not isinstance(raw, list):
        raise InvalidSessionRequest("sets must be a list")
    if len(raw) > MAX_SETS_PER_EXERCISE:
        raise InvalidSessionRequest("set count exceeds its bound")
    seen = set()
    parsed = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != _SET_FIELDS:
            raise InvalidSessionRequest("set entry does not match the contract")
        index = _parse_int(item["index"], 0, MAX_SETS_PER_EXERCISE - 1, "set index")
        if index in seen:
            raise InvalidSessionRequest("duplicate set identity")
        seen.add(index)
        completed = item["completed"]
        if type(completed) is not bool:
            raise InvalidSessionRequest("completed must be a boolean")
        parsed.append({
            "index": index,
            "completed": completed,
            "reps": _parse_optional_int(item["reps"], 0, MAX_REPS, "reps"),
            "weight_kg": _parse_optional_weight(item["weight_kg"]),
        })
    parsed.sort(key=lambda entry: entry["index"])
    return parsed


def _parse_int(value, low: int, high: int, label: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InvalidSessionRequest(f"{label} is out of range")
    return value


def _parse_optional_int(value, low: int, high: int, label: str):
    return None if value is None else _parse_int(value, low, high, label)


def _parse_optional_weight(value):
    """Weight is the only float in the contract. Bounded, finite, and quantized
    to one decimal so a semantically identical retry fingerprints identically
    regardless of client float formatting."""
    if value is None:
        return None
    if type(value) is bool or type(value) not in (int, float):
        raise InvalidSessionRequest("weight_kg is out of range")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= MAX_WEIGHT_KG:
        raise InvalidSessionRequest("weight_kg is out of range")
    return round(numeric, 1)


def _canonical_json(document: dict) -> str:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
