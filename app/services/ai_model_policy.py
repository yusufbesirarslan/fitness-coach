"""Explicit model routing policy (MON-AI-ROUTING-5.5-MIGRATION-01).

Two routings, chosen by the server only (``AI_MODEL_ROUTING``):

- ``legacy`` (default): Sonnet 4.5 on Bedrock for heavy work, OpenAI
  gpt-4o-mini for light work. Nothing in this module touches a legacy payload,
  so legacy behaviour is the pre-migration behaviour.
- ``claude55``: Sonnet 5.5 heavy, Haiku 5.5 light, both on Bedrock. OpenAI is
  not a recipient. Every payload is checked against an exact model-id registry
  and adapted to the 5.5 request surface before it is admitted.

Spend class comes from the registered model id, never from the transport label
("bedrock" is both Sonnet and Haiku) and never from a substring of the id.
An unregistered id is a refusal before any charge or provider attempt.

Model ids are the Bedrock InvokeModel inference profiles published on the
Sonnet 5.5 / Haiku 5.5 model cards (re-read 2026-10-21). eu-central-1 has no
in-Region option for either model: ``eu.`` (EU geo, six EU destination
Regions) or ``global.`` (any commercial Region). Which one is used is an
operator decision recorded in BEDROCK_SONNET55_MODEL / BEDROCK_HAIKU55_MODEL;
there is deliberately no default.
"""
from dataclasses import dataclass
from types import MappingProxyType

from app.services.ai_spend_guard import AISpendLimitExceeded

ROUTING_LEGACY = "legacy"
ROUTING_CLAUDE55 = "claude55"
ROUTINGS = (ROUTING_LEGACY, ROUTING_CLAUDE55)

# Thinking off on both models: the per-call output budgets (coach 700, vision
# 1200, ...) were sized for Sonnet 4.5, which ran without thinking, and thinking
# tokens count against max_tokens. Sonnet 5.5 rejects {"type": "disabled"};
# "between_tools" is its lowest setting (no extended thinking, short progress
# notes between tool calls). Haiku 5.5 accepts "disabled" at effort <= high.
# Turning adaptive thinking on per route is a separate, qualified change.
_SONNET55_THINKING = MappingProxyType({"type": "between_tools"})
_HAIKU55_THINKING = MappingProxyType({"type": "disabled"})


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    telemetry_model: str
    spend_class: str
    thinking: MappingProxyType
    profile: str


def _spec(model_id, telemetry_model, spend_class, thinking):
    profile = "global" if model_id.startswith("global.") else "geographic"
    return ModelSpec(model_id, telemetry_model, spend_class, thinking, profile)


SPECS = MappingProxyType({
    spec.model_id: spec for spec in (
        _spec("global.anthropic.claude-sonnet-5-5", "claude-sonnet-5-5", "heavy",
              _SONNET55_THINKING),
        _spec("eu.anthropic.claude-sonnet-5-5", "claude-sonnet-5-5", "heavy",
              _SONNET55_THINKING),
        _spec("global.anthropic.claude-haiku-5-5", "claude-haiku-5-5", "light",
              _HAIKU55_THINKING),
        _spec("eu.anthropic.claude-haiku-5-5", "claude-haiku-5-5", "light",
              _HAIKU55_THINKING),
    )
})
SONNET55_IDS = frozenset(k for k, v in SPECS.items() if v.spend_class == "heavy")
HAIKU55_IDS = frozenset(k for k, v in SPECS.items() if v.spend_class == "light")

# Effort per task class (the routing matrix). Starting points, not tuned
# values: staging qualification compares them against the Sonnet 4.5 baseline
# before any of them is raised. Every level here is <= "high", which both
# thinking-off modes above require.
EFFORT = MappingProxyType({
    "coach": "low",
    "training_plan": "medium",
    "nutrition_plan": "medium",
    "nutrition": "low",
    "menu_extract": "low",
    "menu_ocr": "low",
    "vision": "medium",
    "summary": "low",
    "health_probe": "low",
    "other": "low",
})

_SAMPLING_KEYS = ("temperature", "top_p", "top_k")
_FORCED_TOOL_CHOICES = frozenset({"any", "tool"})


class RoutingConfigError(ValueError):
    """AI_MODEL_ROUTING or a 5.5 profile id is not one this module registers."""


class UnknownModelPolicy(AISpendLimitExceeded):
    """The payload names a model the active routing does not admit.

    A spend refusal: no charge, no provider attempt, no fallback, no retry.
    """

    def __init__(self):
        super().__init__("model_policy", "heavy")


class UnsupportedRequestShape(AISpendLimitExceeded):
    """A 5.5 payload carries a shape the model rejects (prefill, forced tool).

    A code defect, refused before anything is spent rather than sent as a
    request that can only return 400.
    """

    def __init__(self, reason):
        super().__init__("request_shape", "heavy")
        self.reason = reason


def _cfg():
    from app import config
    return config


def routing():
    return _cfg().AI_MODEL_ROUTING


def claude55():
    return routing() == ROUTING_CLAUDE55


def validate_routing_config():
    """Raise RoutingConfigError for a routing the server cannot honour exactly."""
    cfg = _cfg()
    if cfg.AI_MODEL_ROUTING not in ROUTINGS:
        raise RoutingConfigError(
            f"AI_MODEL_ROUTING must be one of {', '.join(ROUTINGS)}")
    if cfg.AI_MODEL_ROUTING != ROUTING_CLAUDE55:
        return
    if not cfg.BEDROCK_ENABLED:
        raise RoutingConfigError("AI_MODEL_ROUTING=claude55 requires BEDROCK_ENABLED=1")
    if cfg.BEDROCK_SONNET55_MODEL not in SONNET55_IDS:
        raise RoutingConfigError(
            "BEDROCK_SONNET55_MODEL must be one of " + ", ".join(sorted(SONNET55_IDS)))
    if cfg.BEDROCK_HAIKU55_MODEL not in HAIKU55_IDS:
        raise RoutingConfigError(
            "BEDROCK_HAIKU55_MODEL must be one of " + ", ".join(sorted(HAIKU55_IDS)))


def heavy_model():
    """The Bedrock model id for heavy (Sonnet-class) work under the active routing."""
    cfg = _cfg()
    return cfg.BEDROCK_SONNET55_MODEL if claude55() else cfg.BEDROCK_MODEL


def light_route():
    """``(family, model)`` for light work under the active routing."""
    cfg = _cfg()
    if claude55():
        return "bedrock", cfg.BEDROCK_HAIKU55_MODEL
    return "openai", cfg.OPENAI_MODEL


def is_light_model(model):
    return model in HAIKU55_IDS


def prepare(feature, family, payload):
    """Return ``(payload, spend_class)`` for admission.

    Legacy routing: the payload unchanged and ``None`` (the gate label keeps
    deciding the class). claude55: the payload adapted to the 5.5 request
    surface, and the class of its exact model id. ``payload`` is the caller's
    already-frozen copy and is modified in place.
    """
    if not claude55():
        return payload, None
    model = payload.get("model")
    spec = SPECS.get(model) if isinstance(model, str) else None
    cfg = _cfg()
    if (family != "bedrock" or spec is None
            or model not in (cfg.BEDROCK_SONNET55_MODEL, cfg.BEDROCK_HAIKU55_MODEL)):
        raise UnknownModelPolicy()
    messages = payload.get("messages") or []
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
        raise UnsupportedRequestShape("assistant_prefill")
    tool_choice = payload.get("tool_choice")
    if isinstance(tool_choice, dict) and tool_choice.get("type") in _FORCED_TOOL_CHOICES:
        raise UnsupportedRequestShape("forced_tool_choice")
    for key in _SAMPLING_KEYS:
        payload.pop(key, None)
    payload["thinking"] = dict(spec.thinking)
    output_config = dict(payload.get("output_config") or {})
    output_config.setdefault("effort", EFFORT.get(feature, EFFORT["other"]))
    payload["output_config"] = output_config
    return payload, spec.spend_class
