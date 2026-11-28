"""The exact vendor wire. Only allowlisted keys can ever leave the server.

PostHog EU ``/batch`` shape. ``distinct_id`` is the per-grant random analytics
pseudonym; no account id, e-mail, device, OS, app, locale, timezone, network,
screen, IP-derived or person property is ever added. Server-side capture also
means the vendor sees AxisAI's egress address, never the user's.
"""
import json
import uuid
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


def erasure_profile_batch(api_key, subjects, now):
    """The documented PostHog deletion prerequisite for personless events.

    Events captured with ``$process_person_profile: false`` "have no person to
    delete" (posthog.com/docs/privacy/data-storage). For a RETIRED pseudonym
    only, one ``$set`` event creates a profile so the bulk delete can remove
    the person and its events. The only person property is a fixed marker;
    it is never sent for a live grant and never carries account data.
    """
    spec = WIRE["erasure_profile_event"]
    return {"api_key": api_key, "batch": [{
        "event": spec["event"],
        "distinct_id": subject,
        "uuid": str(uuid.uuid4()),
        "timestamp": _timestamp(now),
        "properties": dict(spec["properties"]),
    } for subject in subjects]}
