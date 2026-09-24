"""Explicit model policy. Spend class is a field on the policy, never a guess.

Transport does not select the class. ``provider == "bedrock"`` does not mean
heavy, and a model id is not classified by substring (``"haiku" in id``).
The only admitted ids are the two entries below:

- the configured Sonnet profile → heavy
- the EU Geo Haiku 4.5 profile → light

Anything else, including the global Haiku profile, fails closed before a
provider call and before a spend charge.
"""
from dataclasses import dataclass

# EU Geo only. Global Haiku is a different profile and is not registered.
HAIKU_EU_GEO_PROFILE = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"


@dataclass(frozen=True)
class ModelPolicy:
    logical_model: str
    billing_provider: str
    transport: str
    model_id: str
    spend_class: str
    telemetry_model: str
    vision: bool
    stream: bool


def _configured_ids():
    from app.config import BEDROCK_HAIKU_MODEL, BEDROCK_MODEL
    return BEDROCK_MODEL, BEDROCK_HAIKU_MODEL


def policy_for_model_id(model_id):
    """Return the policy for an exact configured id, or raise UnknownModelPolicy.

    UnknownModelPolicy is a spend refusal: no charge, no fallback, no retry.
    """
    from app.services.ai_spend_guard import UnknownModelPolicy

    sonnet_id, haiku_id = _configured_ids()
    if not isinstance(model_id, str) or sonnet_id == haiku_id:
        raise UnknownModelPolicy()
    if model_id == haiku_id:
        # The EU profile is the only light model. A global or non-Haiku value
        # in BEDROCK_HAIKU_MODEL does not become light by configuration.
        if model_id != HAIKU_EU_GEO_PROFILE:
            raise UnknownModelPolicy()
        return ModelPolicy(
            logical_model="haiku45",
            billing_provider="bedrock",
            transport="anthropic_bedrock",
            model_id=model_id,
            spend_class="light",
            telemetry_model="claude-haiku-4-5",
            vision=True,
            stream=True,
        )
    if model_id == sonnet_id:
        return ModelPolicy(
            logical_model="sonnet45",
            billing_provider="bedrock",
            transport="anthropic_bedrock",
            model_id=model_id,
            spend_class="heavy",
            telemetry_model="claude-sonnet-4-5",
            vision=True,
            stream=True,
        )
    raise UnknownModelPolicy()


def sonnet_policy():
    from app.config import BEDROCK_MODEL
    return policy_for_model_id(BEDROCK_MODEL)


def haiku_policy():
    return policy_for_model_id(HAIKU_EU_GEO_PROFILE)
