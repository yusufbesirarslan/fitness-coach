"""The exact vendor wire. Only allowlisted keys can ever leave the server.

PostHog EU ``/batch`` shape. ``distinct_id`` is the per-grant random analytics
pseudonym; no account id, e-mail, device, OS, app, locale, timezone, network,
screen, IP-derived or person property is ever added. Server-side capture also
means the vendor sees AxisAI's egress address, never the user's.
"""
import json
from datetime import timezone

from app.services.product_analytics.catalog import EVENTS, WIRE


def _timestamp(value):
    aware = value.replace(tzinfo=timezone.utc)
    return aware.strftime("%Y-%m-%dT%H:%M:%S.") + f"{aware.microsecond // 1000:03d}Z"


def wire_event(row):
    props = json.loads(row.properties)
    spec = EVENTS[row.name]
    if set(props) != set(spec["properties"]):
        raise ValueError("stored properties drifted from the catalog")
    return {
        "event": row.name,
        "distinct_id": row.subject,
        "uuid": row.event_id,
        "timestamp": _timestamp(row.occurred_at),
        "properties": {**props, **WIRE["fixed_properties"]},
    }


def build_batch(api_key, rows):
    return {"api_key": api_key, "batch": [wire_event(row) for row in rows]}


def encode(batch):
    return json.dumps(batch, sort_keys=True, separators=(",", ":")).encode("utf-8")
