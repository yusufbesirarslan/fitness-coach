"""Consent-fenced outbox dispatch and vendor erasure (synthetic transport only).

Withdrawal fence (fixes the #541 cross-grant retry defect): each due row is
re-checked against its owner's CURRENT consent, read under a share lock held
across the send. A withdrawal takes the same row's exclusive lock, so it
either waits for the in-flight send to finish or commits first and every
staged/retrying row of the old generation is deleted unsent. A row is never
re-bound to a newer subject or grant: generation AND subject must match.

Bounded retries: failure increments ``attempts`` with exponential backoff;
after ``MAX_ATTEMPTS`` the row is dropped (counted loss, never infinite queue).
Rows older than the occurrence window plus the retry budget are dropped too.
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import ProductAnalyticsErasure, ProductAnalyticsOutbox
from app.services.product_analytics import consent, wire
from app.services.product_analytics.transport import TransportFailure

MAX_ATTEMPTS = 5
BASE_BACKOFF = timedelta(seconds=30)
MAX_AGE = timedelta(hours=48)
SYNTHETIC_API_KEY = "phc_SYNTHETIC_NOT_A_PROJECT"


def dispatch_once(transport, *, api_key=SYNTHETIC_API_KEY, limit=50, now=None):
    now = now or datetime.utcnow()
    stats = {"sent": 0, "fenced": 0, "retry": 0, "dropped": 0}
    try:
        rows = (ProductAnalyticsOutbox.query
                .filter(ProductAnalyticsOutbox.next_attempt_at <= now)
                .order_by(ProductAnalyticsOutbox.id).limit(limit).all())
        grants = {}
        for user_id in sorted({row.user_id for row in rows}):
            grants[user_id] = consent.current(user_id, read_lock=True)
        deliver = []
        for row in rows:
            grant = grants.get(row.user_id)
            if (not consent.is_valid(grant, now) or grant.generation != row.generation
                    or grant.subject != row.subject):
                db.session.delete(row)
                stats["fenced"] += 1
            elif now - row.occurred_at > MAX_AGE:
                db.session.delete(row)
                stats["dropped"] += 1
            else:
                deliver.append(row)
        if deliver:
            try:
                transport.send(wire.encode(wire.build_batch(api_key, deliver)))
            except TransportFailure:
                for row in deliver:
                    row.attempts += 1
                    if row.attempts >= MAX_ATTEMPTS:
                        db.session.delete(row)
                        stats["dropped"] += 1
                    else:
                        row.next_attempt_at = now + BASE_BACKOFF * (2 ** (row.attempts - 1))
                        stats["retry"] += 1
            else:
                for row in deliver:
                    db.session.delete(row)
                stats["sent"] = len(deliver)
        db.session.commit()
        return stats
    except Exception:
        db.session.rollback()
        raise


def process_erasures_once(transport, *, limit=100, now=None):
    """Request vendor deletion for retired pseudonyms; mark done on ack."""
    now = now or datetime.utcnow()
    try:
        jobs = (ProductAnalyticsErasure.query.filter_by(status="pending")
                .order_by(ProductAnalyticsErasure.id).limit(limit).all())
        if not jobs:
            return 0
        try:
            transport.delete_subjects([job.subject for job in jobs])
        except TransportFailure:
            for job in jobs:
                job.attempts += 1
            db.session.commit()
            return 0
        for job in jobs:
            job.status = "done"
            job.completed_at = now
        db.session.commit()
        return len(jobs)
    except Exception:
        db.session.rollback()
        raise
