"""Server-authoritative success events.

``emit_authoritative`` is called by a canonical writer IMMEDIATELY AFTER its
own successful commit, so an event exists only for a committed transition and
a client projection can never become "success". It runs in its own short
transaction on the (then clean) request session and never raises into the
writer: a failure loses one analytics event, never a product write.

Canonical action identity: ``event_id`` is a keyed hash of
``(subject, event name, canonical action reference)``. A replay, retry or
duplicate call for the same committed transition maps to the SAME id and the
``(subject, event_id)`` unique key drops it; the reference itself (internal
ids) never leaves the server and cannot be recovered from the id. The id is
UUID-shaped with version nibble 8 (custom), distinguishing server-derived ids
from client random v4 ids.

Known, documented bound: a crash between the writer's commit and this insert
loses that one event (undercount, never overcount).
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import ProductAnalyticsOutbox
from app.services import product_analytics
from app.services.product_analytics import consent
from app.services.product_analytics.catalog import valid_properties

_KEY_LABEL = b"axisai/product-analytics/event-id/v1"


def _key():
    secret = current_app.config.get("SECRET_KEY") or ""
    return hmac.new(secret.encode("utf-8") if isinstance(secret, str) else secret,
                    _KEY_LABEL, hashlib.sha256).digest()


def derived_event_id(subject, name, canonical_ref):
    digest = bytearray(hmac.new(_key(), "\0".join((subject, name, canonical_ref)).encode(),
                                hashlib.sha256).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80   # version 8 (custom)
    digest[8] = (digest[8] & 0x3F) | 0x80   # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(digest)))


def _exists(subject, event_id):
    return db.session.query(ProductAnalyticsOutbox.id).filter_by(
        subject=subject, event_id=event_id).first() is not None


def stage(row, name, canonical_ref, properties, *, occurred_at=None, authority="server",
          event_id=None):
    """Add one validated outbox row for the locked/valid consent ``row``.

    No commit. Returns True when a new row was staged."""
    if not valid_properties(name, properties, authority):
        raise ValueError("event outside the closed catalog")
    event_id = event_id or derived_event_id(row.subject, name, canonical_ref)
    if _exists(row.subject, event_id):
        return False
    now = datetime.utcnow()
    db.session.add(ProductAnalyticsOutbox(
        user_id=row.user_id, generation=row.generation, subject=row.subject,
        event_id=event_id, name=name, authority=authority,
        occurred_at=occurred_at or now, received_at=now,
        properties=json.dumps(properties, sort_keys=True), attempts=0, next_attempt_at=now))
    return True


def emit_authoritative(user_id, name, canonical_ref, properties=None):
    """Post-commit, best-effort, never raises. No-op (no DB access) when OFF."""
    if not product_analytics.enabled():
        return
    try:
        row = consent.current(user_id)
        if not consent.is_valid(row):
            db.session.rollback()
            return
        stage(row, name, canonical_ref, properties or {})
        db.session.commit()
    except IntegrityError:
        db.session.rollback()   # concurrent duplicate of the same transition
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.warning(
            "product_analytics event=emit_failed name=%s error_type=%s",
            name, type(error).__name__)
