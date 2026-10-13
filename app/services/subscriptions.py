"""Durable ownership and bounded entitlement, independent of legacy quotas."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from flask import current_app

from app.extensions import db
from app.models import User, AppleAccountAssociation, AppleSubscription
from app.services.apple_subscriptions import AppleAdapter, SubscriptionError

MAX_VERIFICATION_AGE = timedelta(hours=24)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _enabled():
    if not current_app.config.get("APPLE_SUBSCRIPTIONS_ENABLED"):
        raise SubscriptionError("SUBSCRIPTIONS_DISABLED", 503)


def _owner(user_id):
    user = db.session.execute(sa.select(User).where(User.id == user_id)
                              .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if user is None:
        raise SubscriptionError("SUBSCRIPTION_ACCOUNT_UNAVAILABLE", 409)
    return user


def _association(user_id):
    _owner(user_id)
    association = db.session.get(AppleAccountAssociation, user_id)
    if association is None:
        association = AppleAccountAssociation(user_id=user_id, token=str(uuid4()))
        db.session.add(association)
        db.session.flush()
    return association


def purchase_context(user_id):
    _enabled()
    try:
        # Config must be complete before a client can start purchasing.
        adapter = AppleAdapter(current_app.config)
        token = _association(user_id).token
        db.session.commit()
        return {"contract_version": 1, "app_account_token": token,
                "environment": adapter.environment, "evidence_type": "transaction_id"}
    except (SubscriptionError, SQLAlchemyError) as error:
        db.session.rollback()
        if isinstance(error, SubscriptionError):
            raise
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None


def entitlement_view(row, now):
    if row is None:
        return {"tier": "FREE", "effective_premium": False, "status": "FREE",
                "expires_at": None, "verification_pending": False}
    status = {1: "ACTIVE", 2: "EXPIRED", 3: "BILLING_RETRY", 4: "GRACE_PERIOD", 5: "REVOKED"}.get(row.status)
    if status is None or not row.last_verified_at or not row.expires_at:
        raise SubscriptionError("SUBSCRIPTION_INVALID_STATE", 503)
    expiry = utc(row.grace_expires_at) if status == "GRACE_PERIOD" else utc(row.expires_at)
    if row.revoked_at:
        status = "REVOKED"
    pending = (utc(row.last_verified_at) > now
               or now - utc(row.last_verified_at) >= MAX_VERIFICATION_AGE)
    effective = (not pending and status in ("ACTIVE", "GRACE_PERIOD")
                 and expiry is not None and expiry > now)
    if status in ("ACTIVE", "GRACE_PERIOD") and (expiry is None or expiry <= now):
        status = "EXPIRED"
    if pending and status not in ("REVOKED", "EXPIRED"):
        status = "VERIFICATION_PENDING"
    return {"tier": "PREMIUM" if effective else "FREE", "effective_premium": effective,
            "status": status, "expires_at": expiry.isoformat().replace("+00:00", "Z") if expiry else None,
            "verification_pending": pending}


def entitlement(user_id):
    _enabled()
    environment = AppleAdapter(current_app.config).environment
    try:
        if db.session.get(User, user_id) is None:
            raise SubscriptionError("SUBSCRIPTION_ACCOUNT_UNAVAILABLE", 409)
        rows = db.session.execute(sa.select(AppleSubscription).where(
            AppleSubscription.user_id == user_id, AppleSubscription.environment == environment)).scalars().all()
        now = datetime.now(timezone.utc)
        views = [entitlement_view(row, now) for row in rows]
        return next((v for v in views if v["effective_premium"]),
                    next((v for v in views if v["verification_pending"]),
                         views[0] if views else entitlement_view(None, now)))
    except SQLAlchemyError:
        db.session.rollback()
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None


def persist_verified(user_id, evidence, expected_token):
    """The user lock serializes same-owner writes; UNIQUE arbitrates all owners.

    Apple I/O happens before these locks. Re-read the association under the
    deletion lock and never reassign an existing original identity.
    """
    try:
        _owner(user_id)
        association = db.session.get(AppleAccountAssociation, user_id)
        if association is None or association.token != expected_token or evidence.account_token != expected_token:
            raise SubscriptionError("SUBSCRIPTION_OWNERSHIP_CONFLICT", 409)
        identity = (AppleSubscription.environment == evidence.environment,
                    AppleSubscription.original_transaction_id == evidence.original_id)
        row = db.session.execute(sa.select(AppleSubscription).where(*identity)
                                 .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:
            # Savepoint keeps uniqueness conflicts from poisoning the transaction.
            from sqlalchemy.exc import IntegrityError
            try:
                with db.session.begin_nested():
                    row = AppleSubscription(user_id=user_id, environment=evidence.environment,
                                            original_transaction_id=evidence.original_id,
                                            **_fields(evidence))
                    db.session.add(row)
                    db.session.flush()
            except IntegrityError:
                row = db.session.execute(sa.select(AppleSubscription).where(*identity)
                                         .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
                if row is None:
                    raise SubscriptionError("SUBSCRIPTION_INVALID_STATE", 503) from None
        if row.user_id != user_id:
            raise SubscriptionError("SUBSCRIPTION_OWNERSHIP_CONFLICT", 409)
        if (utc(row.transaction_signed_at) > evidence.transaction_signed_at
                or utc(row.renewal_signed_at) > evidence.renewal_signed_at):
            raise SubscriptionError("SUBSCRIPTION_VERIFICATION_PENDING", 503)
        # Revocation is terminal in this foundation. Reconciliation may later
        # establish a new paid period; client replay cannot clear it here.
        if row.revoked_at or row.status == 5:
            if not evidence.revoked_at and evidence.status != 5:
                raise SubscriptionError("SUBSCRIPTION_VERIFICATION_PENDING", 503)
        for key, value in _fields(evidence).items():
            setattr(row, key, value)
        db.session.commit()
        return entitlement_view(row, datetime.now(timezone.utc))
    except (SubscriptionError, SQLAlchemyError) as error:
        db.session.rollback()
        if isinstance(error, SubscriptionError):
            raise
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None


def _fields(evidence):
    now = datetime.now(timezone.utc)
    return dict(latest_transaction_id=evidence.transaction_id, product_id=evidence.product_id,
                subscription_group_id=evidence.group_id, status=evidence.status,
                expires_at=evidence.expires_at, revoked_at=evidence.revoked_at,
                grace_expires_at=evidence.grace_expires_at, auto_renew=evidence.auto_renew,
                transaction_signed_at=evidence.transaction_signed_at,
                renewal_signed_at=evidence.renewal_signed_at, last_verified_at=now, updated_at=now)


def verify_purchase(user_id, transaction_id):
    _enabled()
    try:
        association = db.session.get(AppleAccountAssociation, user_id)
        if association is None:
            raise SubscriptionError("SUBSCRIPTION_PURCHASE_CONTEXT_REQUIRED", 409)
        token = association.token
        # Release the read transaction before external I/O; persist re-locks owner.
        db.session.rollback()
        evidence = AppleAdapter(current_app.config).verify(transaction_id, token)
        return persist_verified(user_id, evidence, token)
    except SQLAlchemyError:
        db.session.rollback()
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503) from None
