"""Authoritative per-provider-attempt AI usage events (`[AI-USAGE]` log lines).

One structured line per physical provider attempt (and per local refusal),
written to stdout by a dedicated logger, so it reaches the durable
`/axisai/app` CloudWatch log group through the same json-file -> CloudWatch
Agent path as every other web/worker line (PR #336). Logs Insights answers the
unit-economics questions (usage per provider/feature/account, COGS per user)
over that group; see docs/RATE_LIMITING.md for the queries.

Deliberately NOT a CloudWatch custom metric: `subject_id` is per-account, and a
per-account metric dimension is exactly the cardinality mistake Phase 1 paid
for. Every field here is either a number, an id, or a value from a fixed
allowlist — never prompt text, response text, email, IP, or a credential.

Token counts are provider-reported whenever the provider returns them
(`usage_source="provider"`). Where it does not — a timed-out or failed attempt,
a stream the client abandoned — the event carries the local upper bound for
input and no output figure, marked `usage_source="estimated"`. The two are
never mixed in one event. `estimated_cost_usd` is a convenience derived from a
dated price table; it is not a bill and not a boundary.
"""
import json
import logging
import sys
import threading
import time

from app.services.ai_input_budget import normalize_feature

PRICING_VERSION = "2026-09-23"

# USD per 1M tokens: (input, output, cache_write, cache_read).
# Bedrock: Sonnet 4.5 via the GLOBAL cross-region profile (the production
# model id); the regional profile costs 10% more. OpenAI: gpt-4o-mini list.
_PRICES = {
    "bedrock:claude-sonnet-4-5": (3.00, 15.00, 3.75, 0.30),
    "openai:gpt-4o-mini": (0.15, 0.60, 0.15, 0.075),
}

OUTCOMES = (
    "success",
    "provider_error",
    "timeout",
    "client_disconnect",
    "guard_rejected",
    "input_budget_rejected",
)
PROVIDER_ATTEMPT_OUTCOMES = ("success", "provider_error", "timeout", "client_disconnect")

_logger = logging.getLogger("fitx.ai_usage")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)


def normalize_model(provider, model):
    name = str(model or "").lower()
    if provider == "bedrock" and "claude-sonnet-4-5" in name:
        return "claude-sonnet-4-5"
    if provider == "openai" and name.startswith("gpt-4o-mini"):
        return "gpt-4o-mini"
    return "other"


# ── Request correlation (threads without a request context) ─────────────────

_tls = threading.local()


def current_request_id():
    rid = getattr(_tls, "request_id", None)
    if rid:
        return rid
    try:
        from flask import g, has_request_context
        if has_request_context():
            return getattr(g, "request_id", None)
    except Exception:
        return None
    return None


class request_scope:
    """Carry the caller's request id onto a worker/producer thread."""

    def __init__(self, request_id):
        self.request_id = request_id

    def __enter__(self):
        self._previous = getattr(_tls, "request_id", None)
        _tls.request_id = self.request_id
        return self

    def __exit__(self, *exc):
        _tls.request_id = self._previous
        return False


# ── Usage extraction ─────────────────────────────────────────────────────────

def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def usage_from_response(provider, resp):
    """Provider-reported token counts from a response/final message, or None."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    if provider == "bedrock":
        return {
            "input_tokens": _int_or_none(getattr(usage, "input_tokens", None)),
            "output_tokens": _int_or_none(getattr(usage, "output_tokens", None)),
            "cache_write_tokens": _int_or_none(
                getattr(usage, "cache_creation_input_tokens", None)) or 0,
            "cache_read_tokens": _int_or_none(
                getattr(usage, "cache_read_input_tokens", None)) or 0,
        }
    details = getattr(usage, "prompt_tokens_details", None)
    cached = _int_or_none(getattr(details, "cached_tokens", None)) or 0
    prompt = _int_or_none(getattr(usage, "prompt_tokens", None))
    return {
        # OpenAI's prompt_tokens INCLUDES the cached part; split it so the
        # fields mean the same thing for both providers.
        "input_tokens": None if prompt is None else prompt - cached,
        "output_tokens": _int_or_none(getattr(usage, "completion_tokens", None)),
        "cache_write_tokens": 0,
        "cache_read_tokens": cached,
    }


def estimated_cost_usd(provider, model_norm, usage):
    prices = _PRICES.get(f"{provider}:{model_norm}")
    if prices is None or not usage:
        return None
    parts = (usage.get("input_tokens"), usage.get("output_tokens"),
             usage.get("cache_write_tokens"), usage.get("cache_read_tokens"))
    total = 0.0
    for count, price in zip(parts, prices):
        if count:
            total += count * price / 1_000_000
    return round(total, 6)


def emit(*, feature, provider, model, outcome, attempt=None, tool_round=None,
         subject_id=None, usage=None, usage_source=None, input_bound=None,
         image_units=0, output_cap=None):
    """Write one `[AI-USAGE]` event. Never raises."""
    try:
        feature = normalize_feature(feature)
        provider = provider if provider in ("bedrock", "openai") else "other"
        outcome = outcome if outcome in OUTCOMES else "provider_error"
        model_norm = normalize_model(provider, model)
        usage = usage or {}
        event = {
            "event": "ai_usage",
            "ts": round(time.time(), 3),
            "request_id": current_request_id(),
            "provider": provider,
            "model": model_norm,
            "feature": feature,
            "subject_id": subject_id,
            "outcome": outcome,
            "attempt": attempt,
            "tool_round": tool_round,
            "usage_source": usage_source,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_write_tokens": usage.get("cache_write_tokens"),
            "cache_read_tokens": usage.get("cache_read_tokens"),
            "image_units": image_units,
            "input_bound": input_bound,
            "output_cap": output_cap,
        }
        if outcome in PROVIDER_ATTEMPT_OUTCOMES:
            cost = estimated_cost_usd(provider, model_norm, usage)
            if cost is not None:
                event["estimated_cost_usd"] = cost
                event["pricing_version"] = PRICING_VERSION
                event["pricing_model"] = f"{provider}:{model_norm}"
            if (usage_source == "provider" and input_bound is not None
                    and usage.get("input_tokens") is not None
                    and (usage["input_tokens"] + (usage.get("cache_write_tokens") or 0)
                         + (usage.get("cache_read_tokens") or 0)) > input_bound):
                # The upper bound was beaten: the boundary undercounted. Loud,
                # searchable, and never silently absorbed.
                event["bound_violation"] = True
        _logger.info("[AI-USAGE] %s", json.dumps(event, separators=(",", ":")))
    except Exception:
        pass

