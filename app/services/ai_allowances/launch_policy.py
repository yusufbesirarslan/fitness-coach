"""Source-only installation seam; no approved launch allocation exists yet.

Changing this module requires a reviewed, versioned source policy and founder
approval. Runtime flags, environment JSON and client claims cannot select one.
Eligibility for Starter allocation is not signed service/age eligibility.
"""
from .policy import AllowancePolicy, PERIOD_MONTHLY, SUPPORTED_FEATURES, PolicyError

# Intentionally inactive. Do not substitute candidate or test quantities.
APPROVED_POLICY: AllowancePolicy | None = None


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
