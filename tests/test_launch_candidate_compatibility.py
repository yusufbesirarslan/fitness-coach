"""Approved launch allocation against synthetic signed Apple evidence."""
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import AppleAccountAssociation, AiAllowanceBucket
from app.services import ai_allowances as ledger, subscriptions
from app.services.ai_allowances import launch_policy
from ai_allowance_support import enable_ledger, enable_subscriptions
from subscription_support import adapter, adult_account
from test_d1_launch_integration import approved_cohort


@pytest.mark.parametrize("trial", [False, True])
@pytest.mark.parametrize("product", ["monthly.fixture", "annual.fixture"])
def test_verified_trial_and_paid_products_select_same_finite_candidate(app, make_user, monkeypatch, approved_cohort, approved_launch_policy, trial, product):
    from app.services.ai_allowances.policy import SUPPORTED_FEATURES
    features = SUPPORTED_FEATURES
    policy = launch_policy.installed_policy()
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=policy)
    user = adult_account(make_user("candidate"))
    a, token, tx, renewal, *_ = adapter()
    tx.productId = renewal.productId = renewal.autoRenewProductId = product
    tx.offerType = 1 if trial else None
    tx.offerDiscountType = "FREE_TRIAL" if trial else None
    db.session.add(AppleAccountAssociation(user_id=user.id, token=token))
    db.session.commit()
    # Real adapter checks signed-object identities; only the SDK is synthetic.
    verified = a.verify("101", token)
    assert subscriptions.persist_verified(user.id, verified, token)["effective_premium"] is True
    monkeypatch.setattr(subscriptions, "AppleAdapter", lambda _: SimpleNamespace(environments=("Sandbox",)))
    for feature in features:
        hold = ledger.reserve(user.id, feature, "candidate-" + feature)
        assert hold.scope == "PREMIUM"
        ledger.finalize(user.id, hold.reference, "success")
    view = ledger.allowance_view(user.id)
    assert view["access"] == "PREMIUM"
    assert {row["feature"]: (row["limit"], row["used"], row["reset"])
            for row in view["features"]} == {
        feature: (limit, 1, "MONTHLY")
        for feature, limit in zip(features, [60, 2, 2, 16, 2])}
    bucket_ids = {bucket.id for bucket in AiAllowanceBucket.query.all()}
    # The signed trial-to-paid transaction is a normal entitlement refresh,
    # never a new allowance identity, period or grant.
    tx.transactionId = "102"
    tx.offerType = tx.offerDiscountType = None
    tx.signedDate += 1
    renewal.signedDate += 1
    assert subscriptions.persist_verified(user.id, a.verify("102", token), token)["effective_premium"] is True
    assert {bucket.id for bucket in AiAllowanceBucket.query.all()} == bucket_ids
    assert all(row["used"] == 1 for row in ledger.allowance_view(user.id)["features"])
