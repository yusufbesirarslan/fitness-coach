"""Coach capability authority registry (#533 / #547 F3). DEFAULT CLOSED.

Ordinary Coach chat runs under its own ``coach.conversation`` grant (r2) and
is untouched by this module. Plan editing and saved Nutrition Plan access are
NEW capabilities: each needs its OWN affirmative D1 grant of a separately
disclosed capability purpose, captured inside the Coach operation at the same
consent generation and rechecked at every canonical write. An r2 grant, a
Training grant or a ``nutrition.plan`` grant never implies one (no historical
grant expansion).

Nothing here is registered, approved or published. A capability opens only
when ALL of these hold for the owner's current grant record:

* the capability purpose has an exact immutable ``ai_consent.POLICIES`` entry
  with byte-pinned EN/TR texts (``localized_texts``) — counsel-approved
  wording, registered by a reviewed source change;
* ``LAUNCH_VERSIONS`` selects exactly that version for the purpose;
* the version is listed in ``APPROVED_VERSIONS`` for the capability below;
* the contract carries exactly this capability's category tuple, no external
  scope, no nested helper, and exactly the recipients of the launched Coach
  conversation contract (the only route Coach data actually travels on).

``APPROVED_VERSIONS`` and ``DIRECT_APPLY_VERSIONS`` are code constants with no
environment, request, config or flag channel. Both are EMPTY in this source.
Capability purposes are deliberately absent from ``ai_consent.BINDINGS``: a
capability grant never opens a provider dispatch of its own.
"""
from dataclasses import dataclass
from types import MappingProxyType


TRAINING_PLAN_WRITE = "training_plan_write"
NUTRITION_PLAN_ACCESS = "nutrition_plan_access"


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    purpose: str
    categories: tuple[str, ...]
    # The Nutrition capability is additionally conditional on the owner's own
    # separate ``nutrition.plan`` grant (F-1b); never inferred from this one.
    requires_nutrition_plan_grant: bool


SPECS = MappingProxyType({
    TRAINING_PLAN_WRITE: CapabilitySpec(
        name=TRAINING_PLAN_WRITE,
        purpose="coach.training_plan_edit",
        categories=("training_plan_content", "tool_results"),
        requires_nutrition_plan_grant=False,
    ),
    NUTRITION_PLAN_ACCESS: CapabilitySpec(
        name=NUTRITION_PLAN_ACCESS,
        purpose="coach.nutrition_plan_access",
        categories=("nutrition_plan_content", "food_descriptions",
                    "nutrition_targets", "food_preferences", "tool_results"),
        requires_nutrition_plan_grant=True,
    ),
})

CAPABILITY_PURPOSES = frozenset(spec.purpose for spec in SPECS.values())

# capability -> frozenset of exact approved disclosure versions. EMPTY.
APPROVED_VERSIONS = MappingProxyType({})

# Approved capability versions whose disclosure says explicit low-impact
# reversible Training edits may be saved immediately (F3). EMPTY: every Coach
# Training change keeps the existing preview + explicit confirmation.
DIRECT_APPLY_VERSIONS = frozenset()

# Versioned server-owned low-impact rule (see coach_plan_policy.decisions).
RISK_POLICY_VERSION = "coach-risk-v1"


def spec(capability):
    return SPECS.get(capability) if isinstance(capability, str) else None


def source_open(capability):
    """Cheap, account-independent pre-check: anything approved at all?"""
    return bool(spec(capability) and APPROVED_VERSIONS.get(capability))


def version_open(capability, policy):
    """Whether ``policy`` is an approved, launched contract of ``capability``.

    Pure source predicate over the immutable registry; the caller supplies
    the policy of a grant record it has already validated with
    ``ai_consent._record_valid``.
    """
    from app.services import ai_consent
    from app.services.ai_consent_disclosures import LAUNCH_VERSIONS, localized_texts
    capability_spec = spec(capability)
    if capability_spec is None or policy is None:
        return False
    coach_version = LAUNCH_VERSIONS.get(ai_consent.BINDINGS["coach"][0])
    coach_policy = ai_consent.POLICIES.get(coach_version)
    return bool(
        policy.version in APPROVED_VERSIONS.get(capability, frozenset())
        and ai_consent.POLICIES.get(policy.version) == policy
        and policy.purpose == capability_spec.purpose
        and policy.purpose not in {purpose for purpose, _ in ai_consent.BINDINGS.values()}
        and policy.categories == capability_spec.categories
        and not policy.external_scopes and not policy.nested_features
        and LAUNCH_VERSIONS.get(policy.purpose) == policy.version
        and localized_texts(policy) is not None
        and coach_policy is not None and policy.recipients == coach_policy.recipients)


def grant_open(policy):
    """``mutate`` hook: a capability purpose is grantable only when open."""
    for capability, capability_spec in SPECS.items():
        if policy is not None and policy.purpose == capability_spec.purpose:
            return version_open(capability, policy)
    return True


def direct_apply_allowed(authority):
    """Low-impact direct apply needs an approved disclosure that says so."""
    from app.services import ai_consent
    return bool(isinstance(authority, ai_consent.CapabilityAuthority)
                and authority.capability == TRAINING_PLAN_WRITE
                and authority.version in DIRECT_APPLY_VERSIONS
                and authority.version in APPROVED_VERSIONS.get(TRAINING_PLAN_WRITE, ()))
