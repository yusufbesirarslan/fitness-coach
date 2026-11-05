"""Founder-approved Launch V1 allocation; source merge does not activate AI.

Approval: AXISAI-D1-LAUNCH-V1-FULL-FUNCTIONAL-INTEGRATION, including the
account-scoped Starter clarification. All valid existing account timestamps
qualify; NULL timestamps do not. Re-registration is a new account, with no
cross-account tracking. D1, signed eligibility, Apple and runtime gates remain
independent. Runtime/environment/client input cannot select these values.
"""
from datetime import datetime, timezone

from .policy import AllowancePolicy, ScopePolicy, PERIOD_MONTHLY, SUPPORTED_FEATURES, PolicyError

LAUNCH_V1_POLICY = AllowancePolicy(
    version="launch-v1-usable-result",
    starter=ScopePolicy("NEVER", dict(zip(SUPPORTED_FEATURES, (10, 1, 1, 1, 1))), 0),
    starter_eligible_from=datetime.min.replace(tzinfo=timezone.utc),
    premium=ScopePolicy("MONTHLY", dict(zip(SUPPORTED_FEATURES, (60, 2, 2, 16, 2))), 0),
    refund_unusable_results=True,
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
            or set(policy.premium.allowances) != set(SUPPORTED_FEATURES)):
        raise PolicyError("launch requires finite five-family Starter and monthly Premium scopes")
    return policy
