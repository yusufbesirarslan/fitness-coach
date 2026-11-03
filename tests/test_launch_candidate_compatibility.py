"""Allowance/Apple layer compatibility, never an installed launch policy.

Synthetic approval inputs below are test choices, not commercial decisions.
Real native admission and PostgreSQL fences have their own integration suites.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import AppleAccountAssociation
from app.services import ai_allowances as ledger, subscriptions
from app.services.ai_allowances import launch_policy
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy
from ai_allowance_support import enable_ledger, enable_subscriptions
from subscription_support import adapter, adult_account


@pytest.mark.parametrize("trial", [False, True])
@pytest.mark.parametrize("product", ["monthly.fixture", "annual.fixture"])
def test_verified_trial_and_paid_products_select_same_finite_candidate(app, make_user, monkeypatch, trial, product):
    candidate = json.loads((Path(__file__).resolve().parents[1] / "docs" /
        "mon-d1-premium-launch-closure-30" / "POLICY_CANDIDATE.json").read_text())
    assert candidate["status"] == "UNAPPROVED_REVIEW_INPUT_ONLY"
    assert launch_policy.APPROVED_POLICY is None
    assert candidate["starter_eligible_from"] is None
    assert candidate["max_billed_releases_premium"] is None
    features = candidate["feature_order"]
    policy = AllowancePolicy("synthetic-candidate",
        ScopePolicy("NEVER", dict(zip(features, candidate["starter"]["allowances"])), 0),
        datetime(1970, 1, 1, tzinfo=timezone.utc),
        ScopePolicy("MONTHLY", dict(zip(features, candidate["premium"]["allowances"])), 0))
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
