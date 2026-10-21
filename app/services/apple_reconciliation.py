"""MON-02-BE-02: notification intake and bounded authoritative reconciliation.

A notification never changes access. Intake authenticates it, records its
verified identity once, and requests reconciliation of the subscription it
names. The worker then performs a FRESH authenticated status lookup and applies
it through ``subscriptions.persist_verified``, the same path as client verify:
same ownership rules, monotonic signed timestamps, terminal revocation and
five-minute snapshot freshness. Duplicate, delayed and out-of-order deliveries
therefore all converge on Apple's current state.

Requests are coalesced per (environment, original transaction). A claim takes a
lease and spends an attempt before any Apple I/O, so a crash is retried after
the lease and bounded by ``MAX_ATTEMPTS``. Completion compares the request
generation, so a notification arriving mid-lookup is never dropped.

Only success removes a request. A terminal or exhausted outcome QUARANTINES it
(next lookup after ``QUARANTINE``): the sweep skips subscriptions that already
have a request, so a failing row costs at most one lookup per quarantine period
and can never crowd healthy refreshes out of the bounded sweep. A request older
than ``RECOVERY_WINDOW`` is dropped instead.
"""
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import current_app
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import (AppleAccountAssociation, AppleNotificationReceipt, AppleSubscription,
                        AppleSubscriptionReconciliation as Request)
from app.services import subscriptions
from app.services.ai_gate import BlockingConcurrencyLimit, blocking_concurrency_slot
from app.services.apple_subscriptions import AppleAdapter, SubscriptionError

MAX_ATTEMPTS = 8
BACKOFF_BASE = timedelta(minutes=1)
BACKOFF_CAP = timedelta(hours=1)
LEASE = timedelta(minutes=5)
# Refresh well inside the 24 h entitlement bound so active access stays verified.
REFRESH_AFTER = timedelta(hours=12)
# Apple's billing retry lasts up to 60 days; older lapsed rows are not polled.
RECOVERY_WINDOW = timedelta(days=60)
QUARANTINE = timedelta(hours=24)
DEFAULT_LIMIT = 25
DEFAULT_SWEEP_LIMIT = 50

# Closed outcome vocabulary (logs, last_outcome, CLI summary).
APPLIED = "APPLIED"
SUPERSEDED = "SUPERSEDED"
RETRY = "RETRY"
EXHAUSTED = "RETRY_EXHAUSTED"
UNMATCHED = "UNMATCHED_ACCOUNT"
_TERMINAL = {"SUBSCRIPTION_INVALID_EVIDENCE", "SUBSCRIPTION_OWNERSHIP_CONFLICT",
             "SUBSCRIPTION_ACCOUNT_UNAVAILABLE", "SUBSCRIPTION_INVALID_STATE"}


class ReconciliationDisabled(Exception):
    pass


class ReconciliationUnavailable(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def s_utc(value):
    return subscriptions.utc(value)


def _log(event, outcome):
    # Closed codes only: no transaction, token, account or payload identifiers.
    current_app.logger.info("apple_reconciliation event=%s outcome=%s", event, outcome)


def _adapter():
    if not current_app.config.get("APPLE_SUBSCRIPTIONS_ENABLED"):
        raise SubscriptionError("SUBSCRIPTIONS_DISABLED", 503)
    return AppleAdapter(current_app.config)


def _locked_request(environment, original_id):
    return db.session.execute(sa.select(Request).where(
        Request.environment == environment, Request.original_transaction_id == original_id)
        .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()


def request_reconciliation(environment, original_id, token, now):
    """Coalesce into one pending request; caller owns the transaction.

    A retry backoff is kept so new hints cannot bypass it; a request that is
    not backing off (fresh or quarantined) is due now: each new authentic,
    deduplicated notification buys at most one lookup.
    """
    row = _locked_request(environment, original_id)
    if row is None:
        try:
            with db.session.begin_nested():
                db.session.add(Request(environment=environment, original_transaction_id=original_id,
                                       account_token=token, generation=1, attempts=0,
                                       next_attempt_at=now, created_at=now, updated_at=now))
                db.session.flush()
            return
        except IntegrityError:
            row = _locked_request(environment, original_id)
            if row is None:
                raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
    row.generation += 1
    if token:
        row.account_token = token
    if row.attempts == 0 and s_utc(row.next_attempt_at) > now:
        row.next_attempt_at = now
    row.updated_at = now


def accept_notification(signed_payload):
    """Authenticate, deduplicate and enqueue. Returns the receipt disposition.

    Apple sees success only after the receipt and request commit together.
    """
    adapter = _adapter()
    db.session.rollback()  # never hold a transaction across signature/OCSP I/O
    try:
        with blocking_concurrency_slot():
            note = adapter.notification(signed_payload)
    except BlockingConcurrencyLimit:
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
    now = _now()
    try:
        try:
            with db.session.begin_nested():
                db.session.add(AppleNotificationReceipt(
                    notification_uuid=note.uuid, environment=note.environment,
                    notification_type=note.type, subtype=note.subtype,
                    original_transaction_id=note.original_id, disposition=note.disposition,
                    signed_at=note.signed_at, received_at=now))
                db.session.flush()
        except IntegrityError:
            db.session.rollback()
            _log("notification", "DUPLICATE")
            return "DUPLICATE"
        if note.original_id:
            request_reconciliation(note.environment, note.original_id, note.account_token, now)
        db.session.commit()
    except (SubscriptionError, SQLAlchemyError) as error:
        db.session.rollback()
        if isinstance(error, SubscriptionError):
            raise
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
    _log("notification", note.disposition)
    return note.disposition


def enqueue_stale(environment, now, limit):
    """Missed-notification recovery: request lookups for rows verified too long ago."""
    rows = db.session.execute(sa.select(AppleSubscription.original_transaction_id).where(
        AppleSubscription.environment == environment,
        AppleSubscription.last_verified_at < now - REFRESH_AFTER,
        AppleSubscription.revoked_at.is_(None), AppleSubscription.status != 5,
        AppleSubscription.expires_at > now - RECOVERY_WINDOW,
        ~sa.exists().where(Request.environment == AppleSubscription.environment,
                           Request.original_transaction_id == AppleSubscription.original_transaction_id))
        .order_by(AppleSubscription.last_verified_at, AppleSubscription.id).limit(limit)).scalars().all()
    for original_id in rows:
        request_reconciliation(environment, original_id, None, now)
    db.session.commit()
    return len(rows)


def _claim(environment, now):
    row = db.session.execute(sa.select(Request).where(
        Request.environment == environment, Request.next_attempt_at <= now,
        sa.or_(Request.lease_until.is_(None), Request.lease_until <= now))
        .order_by(Request.next_attempt_at, Request.original_transaction_id).limit(1)
        .with_for_update(skip_locked=True).execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        db.session.rollback()
        return None
    row.attempts += 1
    row.lease_until = now + LEASE
    row.updated_at = now
    claim = (row.original_transaction_id, row.generation, row.attempts, row.account_token, row.lease_until)
    db.session.commit()
    return claim


def _owner(environment, original_id, hint):
    """Our ownership row decides the owner; the signed hint only finds a new one."""
    owned = db.session.execute(sa.select(AppleSubscription.user_id).where(
        AppleSubscription.environment == environment,
        AppleSubscription.original_transaction_id == original_id)).scalar_one_or_none()
    if owned is not None:
        association = db.session.get(AppleAccountAssociation, owned)
        owner = (owned, association.token) if association else None
    else:
        association = hint and db.session.execute(sa.select(AppleAccountAssociation).where(
            AppleAccountAssociation.token == hint)).scalar_one_or_none()
        owner = (association.user_id, association.token) if association else None
    db.session.rollback()  # never hold a read transaction across Apple I/O
    return owner


def _finish(environment, claim, outcome, retry_at=None):
    original_id, generation, _attempts, _hint, lease = claim
    now = _now()
    row = _locked_request(environment, original_id)
    # Fencing: a worker whose lease expired and was re-claimed changes nothing.
    if row is not None and s_utc(row.lease_until) == s_utc(lease):
        row.lease_until, row.last_outcome, row.updated_at = None, outcome, now
        if retry_at is not None:
            row.next_attempt_at = retry_at
        elif row.generation != generation:
            # A newer hint arrived during the lookup: look again, fresh budget.
            row.attempts, row.next_attempt_at = 0, now
        elif outcome in (APPLIED, SUPERSEDED) or now - s_utc(row.created_at) > RECOVERY_WINDOW:
            db.session.delete(row)
        else:
            row.attempts, row.next_attempt_at = 0, now + QUARANTINE
    db.session.commit()


def _process(adapter, environment, claim):
    original_id, _generation, attempts, hint, _lease = claim
    owner = _owner(environment, original_id, hint)
    if owner is None:
        return UNMATCHED, None, False
    user_id, token = owner
    try:
        try:
            with blocking_concurrency_slot():
                evidence = adapter.status(original_id, token)
        except BlockingConcurrencyLimit:
            raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
        subscriptions.persist_verified(user_id, evidence, token)
        return APPLIED, None, False
    except SubscriptionError as error:
        if error.reason == "SUPERSEDED":
            return SUPERSEDED, None, False
        if error.code in _TERMINAL or error.reason == "REVOCATION_TERMINAL":
            return error.reason or error.code, None, False
        outage = error.code == "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"
        if attempts >= MAX_ATTEMPTS:
            return EXHAUSTED, None, outage
        delay = min(BACKOFF_BASE * (2 ** (attempts - 1)), BACKOFF_CAP)
        return error.reason or error.code, _now() + delay, outage


def run(limit=DEFAULT_LIMIT, sweep_limit=DEFAULT_SWEEP_LIMIT):
    """One bounded pass. An Apple/OCSP outage stops the pass (no hammering)."""
    try:
        adapter = _adapter()
    except SubscriptionError as error:
        raise ReconciliationDisabled(error.code) from None
    environment = adapter.environment
    try:
        counts = {"enqueued": enqueue_stale(environment, _now(), sweep_limit), "processed": 0}
    except (SQLAlchemyError, SubscriptionError):
        db.session.rollback()
        raise ReconciliationUnavailable("STORAGE_UNAVAILABLE") from None
    for _ in range(limit):
        try:
            claim = _claim(environment, _now())
        except SQLAlchemyError:
            db.session.rollback()
            raise ReconciliationUnavailable("STORAGE_UNAVAILABLE") from None
        if claim is None:
            break
        try:
            outcome, retry_at, outage = _process(adapter, environment, claim)
        except Exception as error:  # noqa: BLE001 - storage fault or defect: bounded retry, stop the pass
            db.session.rollback()
            # Exception class name only: never its message or arguments.
            current_app.logger.warning("apple_reconciliation event=process_failed error=%s",
                                       type(error).__name__)
            outcome, retry_at, outage = RETRY, _now() + BACKOFF_BASE, True
            if claim[2] >= MAX_ATTEMPTS:
                outcome, retry_at = EXHAUSTED, None
        try:
            _finish(environment, claim, outcome, retry_at)
        except SQLAlchemyError:
            db.session.rollback()  # the lease expires; the attempt was already counted
            raise ReconciliationUnavailable("STORAGE_UNAVAILABLE") from None
        _log("reconcile", outcome)
        counts["processed"] += 1
        counts[outcome] = counts.get(outcome, 0) + 1
        if outage:
            break
    return counts
