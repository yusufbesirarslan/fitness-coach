"""Consent-fenced outbox dispatch and the vendor-erasure receipt lifecycle.

Runs only in the worker's analytics pass (``pipeline``), never in a request.

Dispatch, per pass, in two short transactions:

A. Exposure marker. For the owners of due rows, ``vendor_exposed_at`` is
   committed on the CURRENT granted subject before anything is sent, so a
   later withdrawal/account deletion knows whether vendor erasure is needed
   (an unacknowledged send counts as exposure).
B. Fenced send. Lock order is the same as withdrawal and account purge:
   consent rows (``FOR UPDATE SKIP LOCKED``, ordered), then the owners' due
   outbox rows (``FOR UPDATE SKIP LOCKED``). The consent locks are held across
   the bounded send, so a withdrawal either commits first (its rows are gone
   or fenced) or waits for at most the transport deadline. A row is sent only
   if its owner's grant is valid NOW and generation AND subject match (never
   re-bound to a newer grant; fixes #541 A6), and the subject's exposure is
   already committed. A concurrent pass skips locked owners/rows (duplicate
   wakeups never double-send the same row while a send is in flight).

Bounded retries: failure increments ``attempts`` with exponential backoff;
after ``MAX_ATTEMPTS`` the row is dropped. Rows older than the configured
outbox age (never more than the 48 h hard ceiling) are dropped. A lost
acknowledgement replays the same ``uuid`` (stable event identity); vendor-side
de-duplication by ``uuid`` is not documented by PostHog, so a replay may count
twice at the vendor (documented bound, verified at gate G5).

Erasure: ``pending`` -> profile marker sent -> ``materialized`` -> person
found, bulk delete accepted -> ``requested`` -> vendor reports verified ->
``verified``. Never terminal-failed; overdue jobs are counted for alarms.
"""
import time
from datetime import datetime, timedelta

from sqlalchemy import text, update

from app.extensions import db
from app.models import ProductAnalyticsConsent, ProductAnalyticsErasure, ProductAnalyticsOutbox
from app.services.product_analytics import consent, wire
from app.services.product_analytics.settings import OUTBOX_HARD_CEILING
from app.services.product_analytics.transport import TransportFailure

MAX_ATTEMPTS = 5
BASE_BACKOFF = timedelta(seconds=30)
MAX_AGE = OUTBOX_HARD_CEILING
ERASURE_SETTLE = timedelta(minutes=15)      # let the vendor ingest the profile marker
ERASURE_VERIFY_INTERVAL = timedelta(hours=24)
ERASURE_MAX_BACKOFF = timedelta(hours=24)
ERASURE_BATCH = 20
#: After this many "profile not ingested" checks the marker is sent again
#: (an accepted-but-dropped marker must not strand a job; review B-4).
REMATERIALIZE_AFTER = 6
#: Wall-clock budget for vendor calls in one erasure pass (each call is also
#: bounded by the transport deadline), so the job-row transaction stays short.
ERASURE_PASS_BUDGET_SECONDS = 60.0


def _due_owners(now, limit):
    rows = (db.session.query(ProductAnalyticsOutbox.user_id)
            .filter(ProductAnalyticsOutbox.next_attempt_at <= now)
            .order_by(ProductAnalyticsOutbox.id).limit(limit).all())
    return sorted({user_id for (user_id,) in rows})


def _mark_exposure(owners, now):
    """Transaction A: commit exposure for each owner's current granted subject.

    Locks with ``SKIP LOCKED`` first: an owner whose consent row is held (a
    withdrawal, purge or another pass) is skipped, never waited on, so one
    busy account cannot stall dispatch for everyone else (PG-qualified)."""
    if owners:
        sendable = (db.session.query(ProductAnalyticsOutbox.id)
                    .filter(ProductAnalyticsOutbox.user_id == ProductAnalyticsConsent.user_id,
                            ProductAnalyticsOutbox.generation == ProductAnalyticsConsent.generation,
                            ProductAnalyticsOutbox.subject == ProductAnalyticsConsent.subject,
                            ProductAnalyticsOutbox.next_attempt_at <= now)
                    .exists())
        # Only a subject that has a sendable row is marked (review B-7): a
        # subject whose rows will all be fenced never gets a vendor profile.
        ids = [row_id for (row_id,) in (
            db.session.query(ProductAnalyticsConsent.id)
            .filter(ProductAnalyticsConsent.user_id.in_(owners),
                    ProductAnalyticsConsent.status == consent.GRANTED,
                    ProductAnalyticsConsent.subject.isnot(None),
                    ProductAnalyticsConsent.vendor_exposed_at.is_(None),
                    sendable)
            .order_by(ProductAnalyticsConsent.id)
            .with_for_update(skip_locked=True).all())]
        if ids:
            db.session.execute(
                update(ProductAnalyticsConsent)
                .where(ProductAnalyticsConsent.id.in_(ids))
                .values(vendor_exposed_at=now)
                .execution_options(synchronize_session=False))
    db.session.commit()


#: Hard wall-clock bound on how long one dispatch transaction may sit idle
#: holding consent/outbox locks around a send (review B-3). ``requests``
#: timeouts bound each socket operation, not DNS resolution, attempts over
#: several resolved addresses or a slow-drip peer. PostgreSQL ends the session
#: once it has been idle in this transaction for longer than the cap, which
#: releases the locks at that instant (a waiting withdrawal or purge proceeds)
#: and rolls back; the pass then stops dispatching (``pipeline``).
LOCK_HOLD_CAP = timedelta(seconds=30)


def _cap_lock_hold():
    if db.session.get_bind().dialect.name == "postgresql":
        db.session.execute(
            text("SELECT set_config('idle_in_transaction_session_timeout', :ms, true)"),
            {"ms": str(int(LOCK_HOLD_CAP.total_seconds() * 1000))})


def dispatch_once(transport, *, limit=50, now=None, max_age=MAX_AGE):
    now = now or datetime.utcnow()
    max_age = min(max_age, MAX_AGE)
    stats = {"sent": 0, "fenced": 0, "retry": 0, "dropped": 0, "skipped": 0}
    try:
        owners = _due_owners(now, limit)
        if not owners:
            db.session.rollback()
            return stats
        _mark_exposure(owners, now)
        # Transaction B: consent first, then outbox (same order as withdrawal/purge).
        _cap_lock_hold()
        grants = {row.user_id: row for row in (
            ProductAnalyticsConsent.query.filter(ProductAnalyticsConsent.user_id.in_(owners))
            .order_by(ProductAnalyticsConsent.user_id).populate_existing()
            .with_for_update(skip_locked=True).all())}
        rows = (ProductAnalyticsOutbox.query
                .filter(ProductAnalyticsOutbox.user_id.in_(owners),
                        ProductAnalyticsOutbox.next_attempt_at <= now)
                .order_by(ProductAnalyticsOutbox.id).limit(limit)
                .with_for_update(skip_locked=True).all())
        missing = {user_id for user_id in owners if user_id not in grants}
        present = set()
        if missing:
            # Locked by a concurrent withdrawal/purge/pass, or no consent row
            # at all. Rows of an owner without any consent row are orphans.
            present = {user_id for (user_id,) in db.session.query(ProductAnalyticsConsent.user_id)
                       .filter(ProductAnalyticsConsent.user_id.in_(missing)).all()}
        deliver = []
        for row in rows:
            grant = grants.get(row.user_id)
            if grant is None and row.user_id in present:
                stats["skipped"] += 1          # owner busy elsewhere: next pass
                continue
            if (not consent.is_valid(grant, now) or grant.generation != row.generation
                    or grant.subject != row.subject):
                db.session.delete(row)
                stats["fenced"] += 1
            elif grant.vendor_exposed_at is None:
                stats["skipped"] += 1          # exposure not yet committed: next pass
            elif now - row.occurred_at > max_age:
                db.session.delete(row)
                stats["dropped"] += 1
            else:
                deliver.append(row)
        if deliver:
            try:
                transport.send(wire.encode(wire.build_batch(transport.api_key, deliver)))
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


def _backoff(job, now, outcome):
    job.attempts += 1
    job.last_outcome = outcome
    job.next_attempt_at = now + min(BASE_BACKOFF * (2 ** min(job.attempts, 16)),
                                    ERASURE_MAX_BACKOFF)


def process_erasures_once(transport, *, limit=ERASURE_BATCH, now=None, overdue=None,
                          budget=ERASURE_PASS_BUDGET_SECONDS, clock=time.monotonic):
    """Advance due erasure jobs one lifecycle step. Returns counts.

    Only the processed job rows are locked (``SKIP LOCKED``); no product
    table, consent or outbox row is touched. Vendor calls stop once the pass
    budget is spent; untouched jobs stay due for the next pass."""
    now = now or datetime.utcnow()
    stop_at = clock() + budget

    def in_budget():
        return clock() < stop_at

    stats = {"materialized": 0, "requested": 0, "verified": 0, "waiting": 0, "failed": 0,
             "overdue": 0}
    try:
        jobs = (ProductAnalyticsErasure.query
                .filter(ProductAnalyticsErasure.status.in_(("pending", "materialized", "requested")),
                        ProductAnalyticsErasure.next_attempt_at <= now)
                .order_by(ProductAnalyticsErasure.id).limit(min(limit, ERASURE_BATCH))
                .with_for_update(skip_locked=True).all())
        pending = [job for job in jobs if job.status == "pending"]
        if pending:
            try:
                transport.send(wire.encode(wire.erasure_profile_batch(
                    transport.api_key, [job.subject for job in pending], now)))
            except TransportFailure as failure:
                for job in pending:
                    _backoff(job, now, failure.outcome)
                stats["failed"] += len(pending)
            else:
                for job in pending:
                    job.status, job.last_outcome = "materialized", "profile_sent"
                    job.next_attempt_at = now + ERASURE_SETTLE
                stats["materialized"] += len(pending)
        found = []
        for job in (job for job in jobs if job.status == "materialized" and job not in pending):
            if not in_budget():
                break
            try:
                ref = transport.find_person(job.subject)
            except TransportFailure as failure:
                _backoff(job, now, failure.outcome)
                stats["failed"] += 1
                continue
            if ref is None:
                _backoff(job, now, "profile_not_ingested")
                if job.attempts % REMATERIALIZE_AFTER == 0:
                    job.status = "pending"          # re-send the profile marker
                stats["waiting"] += 1
            else:
                job.vendor_person_ref = ref
                found.append(job)
        if found and in_budget():
            try:
                result = transport.bulk_delete([job.subject for job in found])
            except TransportFailure as failure:
                for job in found:
                    _backoff(job, now, failure.outcome)
                stats["failed"] += len(found)
            else:
                accepted = (not result["deletion_errors"]
                            and result["persons_queued_for_deletion"] + result["persons_deleted"]
                            >= len(found))
                for job in found:
                    if accepted:
                        job.status, job.last_outcome = "requested", "deletion_queued"
                        job.vendor_requested_at = now
                        job.next_attempt_at = now + ERASURE_VERIFY_INTERVAL
                    else:
                        _backoff(job, now, "deletion_incomplete")
                stats["requested" if accepted else "failed"] += len(found)
        for job in (job for job in jobs if job.status == "requested" and job not in found):
            if not in_budget():
                break
            try:
                verified = transport.deletion_verified(job.vendor_person_ref)
            except TransportFailure as failure:
                _backoff(job, now, failure.outcome)
                stats["failed"] += 1
                continue
            if verified:
                job.status, job.last_outcome, job.completed_at = "verified", "vendor_verified", now
                stats["verified"] += 1
            else:
                job.last_outcome = "awaiting_vendor"
                job.next_attempt_at = now + ERASURE_VERIFY_INTERVAL
                stats["waiting"] += 1
        if overdue is not None:
            stats["overdue"] = (ProductAnalyticsErasure.query
                                .filter(ProductAnalyticsErasure.status.in_(
                                    ("pending", "materialized", "requested")),
                                    ProductAnalyticsErasure.requested_at < now - overdue)
                                .count())
        db.session.commit()
        return stats
    except Exception:
        db.session.rollback()
        raise
