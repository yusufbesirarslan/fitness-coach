from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.Type import Type
from appstoreserverlibrary.models.InAppOwnershipType import InAppOwnershipType
from app.services.apple_subscriptions import AppleAdapter, Evidence


def adult_account(user, **changes):
    """Real canonical signature, explicitly requested by purchase test owners."""
    from app.services import account_eligibility as policy
    user.cognito_sub = user.cognito_sub or f"purchase-fixture-{uuid4()}"
    record = dict(version=policy.VERSION, age_band="18_plus", country="TR",
                  source="self_declared", checked_at=datetime.now(timezone.utc).isoformat(), _age=25)
    record.update(changes)
    policy.attach(user, record)
    return user


def evidence(token, **changes):
    now = datetime.now(timezone.utc)
    values = dict(environment="Sandbox", original_id="100", transaction_id="101",
                  product_id="monthly.fixture", group_id="group.fixture", account_token=token,
                  status=1, expires_at=now + timedelta(days=1), revoked_at=None,
                  grace_expires_at=None, auto_renew=True, transaction_signed_at=now,
                  renewal_signed_at=now, purchased_at=now - timedelta(seconds=1))
    values.update(changes)
    return Evidence(**values)


def adapter(token=None):
    token = token or str(uuid4())
    a = AppleAdapter.__new__(AppleAdapter)
    a.environment, a.bundle, a.app_id = "Sandbox", "bundle.fixture", 123
    a.products, a.group = {"monthly.fixture", "annual.fixture"}, "group.fixture"
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    tx = NS(bundleId=a.bundle, environment=Environment.SANDBOX, productId="monthly.fixture",
            subscriptionGroupIdentifier=a.group, type=Type.AUTO_RENEWABLE_SUBSCRIPTION,
            inAppOwnershipType=InAppOwnershipType.PURCHASED, originalTransactionId="100",
            transactionId="101", appAccountToken=token, signedDate=now,
            expiresDate=now + 86400000, revocationDate=None, purchaseDate=now - 1000)
    renewal = NS(originalTransactionId="100", environment=Environment.SANDBOX,
                 productId=tx.productId, autoRenewProductId=tx.productId,
                 autoRenewStatus=1, signedDate=now, gracePeriodExpiresDate=None,
                 isInBillingRetryPeriod=False)
    item = NS(originalTransactionId="100", signedTransactionInfo="signed-tx", signedRenewalInfo="signed-renewal", status=1)
    response = NS(bundleId=a.bundle, appAppleId=123, environment=Environment.SANDBOX,
                  data=[NS(subscriptionGroupIdentifier=a.group, lastTransactions=[item])])
    a.client = NS(get_transaction_info=lambda _: NS(signedTransactionInfo="signed-tx"),
                  get_all_subscription_statuses=lambda _: response)
    a.verifier = NS(verify_and_decode_signed_transaction=lambda _: tx,
                    verify_and_decode_renewal_info=lambda _: renewal)
    return a, token, tx, renewal, item, response
