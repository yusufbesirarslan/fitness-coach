from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import requests
from appstoreserverlibrary.api_client import APIException
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus

from app.extensions import db
from app.models import AppleAccountAssociation, AppleSubscription, User
from app.services import subscriptions as s
from app.services.apple_subscriptions import AppleAdapter, SubscriptionError
from subscription_support import adapter, evidence


def test_adapter_authenticates_both_signed_objects():
    a, token, *_ = adapter()
    assert a.verify("101", token).account_token == token
    def reject(_):
        raise VerificationException(VerificationStatus.VERIFICATION_FAILURE)
    a.verifier.verify_and_decode_renewal_info = reject
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        a.verify("101", token)


@pytest.mark.parametrize("target,field,value", [
    ("tx", "bundleId", "wrong"), ("tx", "environment", Environment.PRODUCTION),
    ("tx", "productId", "unknown"), ("tx", "originalTransactionId", None),
    ("tx", "appAccountToken", None), ("tx", "appAccountToken", str(uuid4())),
    ("tx", "transactionId", "999"), ("tx", "subscriptionGroupIdentifier", "wrong"),
    ("tx", "signedDate", 1), ("response", "appAppleId", 999),
    ("response", "bundleId", "wrong"), ("response", "environment", Environment.PRODUCTION),
    ("renewal", "originalTransactionId", "wrong"), ("renewal", "productId", "unknown"),
    ("renewal", "environment", Environment.PRODUCTION), ("item", "status", 99),
    ("item", "status", 4),
])
def test_untrusted_or_inconsistent_evidence_rejected(target, field, value):
    a, token, tx, renewal, item, response = adapter()
    setattr(dict(tx=tx, renewal=renewal, item=item, response=response)[target], field, value)
    with pytest.raises(SubscriptionError):
        a.verify("101", token)


@pytest.mark.parametrize("failure", [requests.Timeout(), APIException(429), APIException(500), APIException(404)])
def test_apple_failures_are_closed(failure):
    a, token, *_ = adapter()
    def fail(_):
        raise failure
    a.client.get_transaction_info = fail
    with pytest.raises(SubscriptionError) as err:
        a.verify("101", token)
    assert err.value.status == (422 if isinstance(failure, APIException) and failure.http_status_code == 404 else 503)


def test_missing_configuration_is_closed():
    with pytest.raises(SubscriptionError, match="NOT_CONFIGURED"):
        AppleAdapter({})


def test_ocsp_outage_is_retryable():
    a, token, *_ = adapter()
    def fail(_):
        raise VerificationException(VerificationStatus.RETRYABLE_VERIFICATION_FAILURE)
    a.verifier.verify_and_decode_signed_transaction = fail
    with pytest.raises(SubscriptionError) as error:
        a.verify("101", token)
    assert error.value.code == "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"
    assert error.value.status == 503


@pytest.mark.parametrize("status,expired,revoked,grace,old,expected", [
    (1, False, False, False, False, "ACTIVE"),
    (1, True, False, False, False, "EXPIRED"),
    (2, True, False, False, False, "EXPIRED"),
    (3, False, False, False, False, "BILLING_RETRY"),
    (4, True, False, True, False, "GRACE_PERIOD"),
    (4, True, False, False, False, "EXPIRED"),
    (5, False, False, False, False, "REVOKED"),
    (1, False, True, False, False, "REVOKED"),
    (1, False, False, False, True, "VERIFICATION_PENDING"),
])
def test_bounded_entitlement(status, expired, revoked, grace, old, expected):
    now = datetime.now(timezone.utc)
    row = NS(status=status, expires_at=now + timedelta(days=-1 if expired else 1),
             revoked_at=now if revoked else None, grace_expires_at=now + timedelta(hours=1) if grace else None,
             last_verified_at=now - timedelta(hours=25 if old else 0), auto_renew=False)
    result = s.entitlement_view(row, now)
    assert result["status"] == expected
    assert result["effective_premium"] == (expected in ("ACTIVE", "GRACE_PERIOD"))


def test_idempotency_ownership_staleness_and_rollback(app):
    with app.app_context():
        user = User(username="apple1", email="apple1@example.invalid")
        other = User(username="apple2", email="apple2@example.invalid")
        db.session.add_all([user, other]); db.session.commit()
        ids = user.id, other.id
        token, other_token = str(uuid4()), str(uuid4())
        db.session.add_all([AppleAccountAssociation(user_id=ids[0], token=token),
                            AppleAccountAssociation(user_id=ids[1], token=other_token)])
        db.session.commit()
        ev = evidence(token)
        assert s.persist_verified(ids[0], ev, token)["effective_premium"]
        assert s.persist_verified(ids[0], ev, token)["effective_premium"]
        assert AppleSubscription.query.count() == 1
        # Even a contradictory trusted-adapter association cannot move a row.
        with pytest.raises(SubscriptionError, match="OWNERSHIP_CONFLICT"):
            s.persist_verified(ids[1], replace(ev, account_token=other_token), other_token)
        with pytest.raises(SubscriptionError, match="VERIFICATION_PENDING"):
            s.persist_verified(ids[0], replace(ev, transaction_signed_at=ev.transaction_signed_at - timedelta(seconds=1)), token)
        assert AppleSubscription.query.one().user_id == ids[0]
        revoked = replace(ev, revoked_at=ev.transaction_signed_at, status=5)
        assert s.persist_verified(ids[0], revoked, token)["status"] == "REVOKED"
        with pytest.raises(SubscriptionError, match="VERIFICATION_PENDING"):
            s.persist_verified(ids[0], ev, token)
        assert AppleSubscription.query.one().status == 5
        assert not db.session.get(User, ids[0]).is_premium


def test_environment_separation_and_missing_user(app):
    with app.app_context():
        user = User(username="apple", email="apple@example.invalid")
        db.session.add(user); db.session.commit(); uid = user.id
        token = str(uuid4())
        db.session.add(AppleAccountAssociation(user_id=uid, token=token)); db.session.commit()
        ev = evidence(token)
        s.persist_verified(uid, ev, token)
        s.persist_verified(uid, replace(ev, environment="Production"), token)
        assert AppleSubscription.query.count() == 2
        with pytest.raises(SubscriptionError, match="ACCOUNT_UNAVAILABLE"):
            s.persist_verified(999999, ev, token)


def test_commit_failure_rolls_back_verified_update(app, monkeypatch):
    import sqlalchemy as sa
    with app.app_context():
        user = User(username="rollback", email="rollback@example.invalid")
        db.session.add(user); db.session.commit(); uid = user.id
        token = str(uuid4())
        db.session.add(AppleAccountAssociation(user_id=uid, token=token)); db.session.commit()
        ev = evidence(token)
        s.persist_verified(uid, ev, token)
        def fail():
            raise sa.exc.SQLAlchemyError("synthetic storage failure")
        monkeypatch.setattr(db.session, "commit", fail)
        with pytest.raises(SubscriptionError, match="TEMPORARILY_UNAVAILABLE"):
            s.persist_verified(uid, replace(ev, status=5, revoked_at=ev.transaction_signed_at), token)
        row = AppleSubscription.query.one()
        assert row.status == 1 and row.revoked_at is None
