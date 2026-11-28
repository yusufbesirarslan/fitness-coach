"""The ONE independent, optional product-analytics consent authority.

Never inferred from terms, health, AI or any other consent, and never from a
shared checkbox. The client sees only ``status``, the opaque ``grant_id``
fence token, the notice version and expiry; the analytics pseudonym
(``subject``) never leaves the server except as the vendor ``distinct_id``.

Generation semantics: every grant after a withdrawal mints a NEW random
subject and grant id (old pseudonym unlinkable from the new one on the vendor
side), and every withdrawal bumps ``generation`` so any event staged or
in-flight under the old grant is fenced. A renewal of a live grant (new notice
version or expiry) keeps the subject: it is the same person's same choice.
"""
import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (ProductAnalyticsConsent, ProductAnalyticsErasure,
                        ProductAnalyticsOutbox)
from app.services.product_analytics.catalog import NOTICE_VERSION

# PROPOSAL pending privacy review: re-ask yearly.
GRANT_TTL = timedelta(days=365)
GRANTED = "granted"
WITHDRAWN = "withdrawn"


class NoticeOutdated(ValueError):
    """The client asked to grant against a notice that is not current."""


def _now():
    return datetime.utcnow()


def is_valid(row, now=None):
    now = now or _now()
    return (row is not None and row.status == GRANTED and row.subject is not None
            and row.grant_id is not None and row.expires_at is not None
            and row.expires_at > now)


def current(user_id, *, lock=False, read_lock=False):
    query = ProductAnalyticsConsent.query.filter_by(user_id=user_id).populate_existing()
    if lock:
        query = query.with_for_update()
    elif read_lock:
        query = query.with_for_update(read=True)
    return query.one_or_none()


def view(row, now=None):
    now = now or _now()
    if row is None:
        status = "not_granted"
    elif is_valid(row, now):
        status = GRANTED
    elif row.status == GRANTED:
        status = "expired"
    else:
        status = WITHDRAWN
    live = status == GRANTED
    return {
        "contract_version": 1,
        "status": status,
        "grant_id": row.grant_id if live else None,
        "notice_version": NOTICE_VERSION,
        "granted_notice_version": row.notice_version if live else None,
        "expires_at": (row.expires_at.replace(microsecond=0).isoformat() + "Z") if live else None,
    }


def _locked_row(user_id):
    row = current(user_id, lock=True)
    if row is not None:
        return row
    row = ProductAnalyticsConsent(user_id=user_id, status=WITHDRAWN, generation=0,
                                  updated_at=_now())
    try:
        with db.session.begin_nested():
            db.session.add(row)
    except IntegrityError:
        pass  # a concurrent first write won; lock theirs
    return current(user_id, lock=True)


def grant(user_id, notice_version, *, now=None):
    """Grant (or renew) for ``user_id``. Commits. Returns the client view."""
    if notice_version != NOTICE_VERSION:
        raise NoticeOutdated()
    now = now or _now()
    try:
        row = _locked_row(user_id)
        fresh_subject = row.status != GRANTED or row.subject is None
        if fresh_subject:
            row.generation += 1
            row.subject = str(uuid.uuid4())
            row.grant_id = str(uuid.uuid4())
            row.vendor_exposed_at = None
        row.status = GRANTED
        row.notice_version = notice_version
        row.granted_at = now
        row.expires_at = now + GRANT_TTL
        row.withdrawn_at = None
        row.updated_at = now
        if fresh_subject:
            # Server authority for enrollment, atomic with the grant itself.
            from app.services.product_analytics import emit
            emit.stage(row, "axis_v2_analytics_enabled", f"grant:{row.grant_id}", {},
                       occurred_at=now)
        db.session.commit()
        return view(row, now)
    except Exception:
        db.session.rollback()
        raise


def _retire_subject(row, reason, now):
    """Record the durable vendor-erasure job for the subject being retired.

    The caller holds the consent row lock. Dispatch commits
    ``vendor_exposed_at`` BEFORE any send under that subject, so a subject
    that was never exposed provably has nothing at the vendor and its job is
    settled as ``not_required`` without any vendor call (no profile is ever
    created for it)."""
    if row.subject and ProductAnalyticsErasure.query.filter_by(
            subject=row.subject).one_or_none() is None:
        exposed = row.vendor_exposed_at is not None
        db.session.add(ProductAnalyticsErasure(
            subject=row.subject, reason=reason,
            status="pending" if exposed else "not_required", attempts=0,
            requested_at=now, next_attempt_at=now,
            last_outcome=None if exposed else "never_exposed",
            completed_at=None if exposed else now))


def withdraw(user_id, *, now=None):
    """Withdraw. Idempotent. Commits. Purges undispatched events, fences
    in-flight dispatch (generation bump) and queues vendor erasure."""
    now = now or _now()
    try:
        row = _locked_row(user_id)
        if row.status == GRANTED or row.subject is not None:
            _retire_subject(row, "withdrawn", now)
            row.generation += 1
        ProductAnalyticsOutbox.query.filter_by(user_id=user_id).delete(
            synchronize_session=False)
        row.status = WITHDRAWN
        row.subject = None
        row.grant_id = None
        row.withdrawn_at = now
        row.granted_at = None
        row.expires_at = None
        row.vendor_exposed_at = None
        row.updated_at = now
        db.session.commit()
        return view(row, now)
    except Exception:
        db.session.rollback()
        raise


def enqueue_account_erasure(user_id, *, now=None):
    """Inside the account purge transaction (no commit): queue vendor erasure
    for the live subject BEFORE the mapping row is deleted with the account.

    Takes the consent row lock first, the same order as withdrawal and
    dispatch (consent, then outbox), so an in-flight send finishes or is fenced
    and the purge never deadlocks against the dispatcher."""
    row = current(user_id, lock=True)
    if row is not None and row.subject:
        _retire_subject(row, "account_deleted", now or _now())
