"""Strict client observation ingress.

Envelope (exact keys): ``{"schema_version": 2, "consent_grant": <grant uuid>,
"events": [{"event_id", "name", "occurred_at", "properties"}, ...]}``.

* Account fence: the owner is the authenticated principal only; the batch is
  accepted only if ``consent_grant`` equals that owner's CURRENT valid grant.
  Events buffered under a withdrawn grant, an expired grant, or another
  account on the same device all fail this check and are never stored.
* Authority: only ``client`` catalog events; a client can never post a
  server success event.
* Validation: exact keys, UUIDv4 ids, closed enum properties, occurrence time
  within [received-24h, received+5min], size/count bounds. Invalid events are
  dropped and counted, never logged.
* Deduplication: client ``event_id`` is created once per logical observation
  and kept across retries; ``(subject, event_id)`` drops repeats.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.services.product_analytics import consent, emit
from app.services.product_analytics.catalog import CLIENT_EVENTS, ENVELOPE, valid_properties

MAX_BODY = ENVELOPE["max_body_bytes"]
MAX_EVENTS = ENVELOPE["max_events"]
PAST = timedelta(seconds=ENVELOPE["occurred_at_window_seconds"]["past"])
FUTURE = timedelta(seconds=ENVELOPE["occurred_at_window_seconds"]["future"])


class InvalidEnvelope(ValueError):
    pass


class StaleGrant(ValueError):
    pass


def _uuid4(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4
    except (ValueError, AttributeError):
        return False


def _occurred(value, received):
    if type(value) is not str or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    naive = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if not received - PAST <= naive <= received + FUTURE:
        return None
    return naive


def _event(item, received):
    if type(item) is not dict or set(item) != set(ENVELOPE["event_keys"]):
        return None
    name = item["name"]
    if type(name) is not str or name not in CLIENT_EVENTS:
        return None
    if not _uuid4(item["event_id"]) or not valid_properties(name, item["properties"], "client"):
        return None
    occurred = _occurred(item["occurred_at"], received)
    if occurred is None:
        return None
    return name, item["event_id"], item["properties"], occurred


def parse(raw):
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_BODY:
        raise InvalidEnvelope()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise InvalidEnvelope() from None
    if (type(data) is not dict or set(data) != set(ENVELOPE["keys"])
            or type(data["schema_version"]) is not int or data["schema_version"] != 2
            or not _uuid4(data["consent_grant"])
            or type(data["events"]) is not list or not 1 <= len(data["events"]) <= MAX_EVENTS):
        raise InvalidEnvelope()
    return data


def accept(user_id, raw, *, now=None):
    """Validate and stage one batch. Commits. Returns (accepted, dropped)."""
    data = parse(raw)
    received = now or datetime.utcnow()
    try:
        row = consent.current(user_id)
        if not consent.is_valid(row, received) or row.grant_id != data["consent_grant"]:
            db.session.rollback()
            raise StaleGrant()
        accepted = dropped = 0
        for item in data["events"]:
            parsed = _event(item, received)
            if parsed is None:
                dropped += 1
                continue
            name, event_id, properties, occurred = parsed
            if emit.stage(row, name, "", properties, occurred_at=occurred,
                          authority="client", event_id=event_id):
                accepted += 1
            else:
                dropped += 1
        db.session.commit()
        return accepted, dropped
    except StaleGrant:
        raise
    except Exception:
        db.session.rollback()
        raise
