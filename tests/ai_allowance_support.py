"""TEST-ONLY AI allowance fixtures (MON-02-BE-03A).

These quantities are NOT approved for production and must never be installed
as defaults. The Balanced Starter numbers are the owner's test hypothesis only;
the Premium numbers are arbitrary fixtures, not a proposal.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.extensions import db
from app.models import AppleSubscription
from app.services import subscriptions
from app.services.ai_allowances import AllowancePolicy, ScopePolicy

BALANCED_STARTER_TEST_FIXTURE = {
    "coach": 10, "training_generation": 1, "nutrition_generation": 1,
    "pump_check": 1, "qr_menu": 1,
}
PREMIUM_TEST_FIXTURE = {"coach": 3, "qr_menu": 2}
ELIGIBLE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
LEGACY_CREATED_AT = datetime(2025, 6, 1)  # naive UTC, like User.created_at


def fixture_policy(*, starter=True, premium=False, period="WEEKLY", max_billed=2,
                   version="test-balanced-v1", starter_units=None):
    return AllowancePolicy(
        version=version,
        starter=ScopePolicy("NEVER", dict(starter_units or BALANCED_STARTER_TEST_FIXTURE), max_billed)
        if starter else None,
        starter_eligible_from=ELIGIBLE_FROM if starter else None,
        premium=ScopePolicy(period, dict(PREMIUM_TEST_FIXTURE), max_billed) if premium else None,
    )


def enable_ledger(app, policy=None):
    app.config.update(AI_ALLOWANCE_LEDGER_ENABLED=True,
                      AI_ALLOWANCE_POLICY=fixture_policy() if policy is None else policy)


def enable_subscriptions(app, monkeypatch):
    """#436 entitlement on, with no Apple client (DB rows only)."""
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Sandbox")
    monkeypatch.setattr(subscriptions, "AppleAdapter", lambda _: NS(environment="Sandbox", environments=("Sandbox",)))


def premium_row(user_id, *, expires_in=timedelta(days=20), verified_ago=timedelta(minutes=1),
                status=1, original_id="900"):
    now = datetime.now(timezone.utc)
    row = AppleSubscription(
        user_id=user_id, environment="Sandbox", original_transaction_id=original_id,
        latest_transaction_id=original_id, product_id="monthly.fixture",
        subscription_group_id="group.fixture", status=status, expires_at=now + expires_in,
        revoked_at=None, grace_expires_at=None, auto_renew=True, transaction_signed_at=now,
        renewal_signed_at=now, last_verified_at=now - verified_ago, updated_at=now)
    db.session.add(row)
    db.session.commit()
    return row
