"""Founder-approved Launch V1 allocation; source merge does not activate AI.

Approval: AXISAI-D1-LAUNCH-V1-FULL-FUNCTIONAL-INTEGRATION, including the
account-scoped Starter clarification. All valid existing account timestamps
qualify; NULL timestamps do not. Re-registration is a new account, with no
cross-account tracking. D1, signed eligibility, Apple and runtime gates remain
independent. Runtime/environment/client input cannot select these values.

LAUNCH_FREE (AXISAI-LAUNCH-FREE-BE-01, founder-approved 30/2/2/20/5, three
campaign-anchored 30-day windows from 2026-11-25 00:00 Europe/Istanbul):
LF1 11-25..12-25, LF2 12-25..01-24, LF3 01-24..2027-02-23, start-inclusive,
end-exclusive. It is an AI allowance scope, never a subscription entitlement.
A revised start date is a reviewed source change; window keys are relative
(``LF<n>``), so a revision never re-grants a window an account already opened.
"""
from datetime import datetime, timezone

from app.timeutil import APP_TZ

from .policy import (AllowancePolicy, CampaignPolicy, ScopePolicy, PERIOD_CAMPAIGN,
                     PERIOD_MONTHLY, SUPPORTED_FEATURES, PolicyError)

LAUNCH_FREE_CAMPAIGN = CampaignPolicy(
    version="launch-free-v1",
    starts_at=datetime(2026, 11, 25, tzinfo=APP_TZ),
    windows=3,
    allowances=ScopePolicy(PERIOD_CAMPAIGN, dict(zip(SUPPORTED_FEATURES, (30, 2, 2, 20, 5))), 0),
)

LAUNCH_V1_POLICY = AllowancePolicy(
    version="launch-v1-usable-result",
    starter=ScopePolicy("NEVER", dict(zip(SUPPORTED_FEATURES, (10, 1, 1, 1, 1))), 0),
    starter_eligible_from=datetime.min.replace(tzinfo=timezone.utc),
    premium=ScopePolicy("MONTHLY", dict(zip(SUPPORTED_FEATURES, (60, 2, 2, 16, 2))), 0),
    refund_unusable_results=True,
    launch_free=LAUNCH_FREE_CAMPAIGN,
)
APPROVED_POLICY: AllowancePolicy | None = LAUNCH_V1_POLICY


def installed_policy():
    policy = APPROVED_POLICY
    if policy is None:
        return None
    if (not isinstance(policy, AllowancePolicy) or policy.starter is None
            or policy.premium is None
            or policy.premium.period != PERIOD_MONTHLY
            or set(policy.starter.allowances) != set(SUPPORTED_FEATURES)
            or set(policy.premium.allowances) != set(SUPPORTED_FEATURES)
            or (policy.launch_free is not None
                and set(policy.launch_free.allowances.allowances) != set(SUPPORTED_FEATURES))):
        raise PolicyError("launch requires finite five-family Starter, monthly Premium "
                          "and complete campaign scopes")
    return policy
