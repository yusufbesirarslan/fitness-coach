"""Spend class is explicit policy. Transport cannot change it.

These tests fail if Haiku is classified heavy, Sonnet is classified light,
an unknown id is admitted, or production code grows a direct OpenAI call.
"""
import ast
from pathlib import Path

import pytest

from app.services import ai_provider_call, ai_spend_guard
from app.services.ai_model_policy import (
    HAIKU_EU_GEO_PROFILE,
    haiku_policy,
    policy_for_model_id,
    sonnet_policy,
)
from app.services.ai_spend_guard import UnknownModelPolicy

SONNET_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


def _counts():
    """Global daily counters only. Heavy also has an hourly bucket."""
    heavy = sum(v for k, v in ai_spend_guard._local_counts.items()
                if ":global:" in k and ":heavy:d:" in k)
    light = sum(v for k, v in ai_spend_guard._local_counts.items()
                if ":global:" in k and ":light:d:" in k)
    return heavy, light


@pytest.fixture(autouse=True)
def _guard(monkeypatch):
    ai_spend_guard._reset_local_for_tests()
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    monkeypatch.setattr(ai_spend_guard, "LIMITS", {
        ("user", "heavy", "d"): 50, ("user", "light", "d"): 50,
        ("global", "heavy", "h"): 50, ("global", "heavy", "d"): 50,
        ("global", "light", "d"): 50,
    })
    yield
    ai_spend_guard._reset_local_for_tests()


def test_sonnet_policy_is_heavy_bedrock():
    policy = sonnet_policy()
    assert policy.logical_model == "sonnet45"
    assert policy.billing_provider == "bedrock"
    assert policy.transport == "anthropic_bedrock"
    assert policy.spend_class == "heavy"
    assert policy.telemetry_model == "claude-sonnet-4-5"
    assert policy.model_id == SONNET_ID


def test_haiku_policy_is_light_bedrock_eu_geo():
    policy = haiku_policy()
    assert policy.logical_model == "haiku45"
    assert policy.billing_provider == "bedrock"
    assert policy.transport == "anthropic_bedrock"
    assert policy.spend_class == "light"
    assert policy.telemetry_model == "claude-haiku-4-5"
    assert policy.model_id == HAIKU_EU_GEO_PROFILE
    assert policy.model_id.startswith("eu.")
    assert not policy.model_id.startswith("global.")


def test_global_haiku_and_unknown_ids_fail_closed():
    for model_id in (
            "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "anthropic.claude-haiku-4-5-20251001-v1:0",
            "gpt-4o-mini",
            "m",
            "",
            None):
        with pytest.raises(UnknownModelPolicy):
            policy_for_model_id(model_id)
    assert _counts() == (0, 0)


def test_gate_label_does_not_change_spend_class():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return object()

    with ai_provider_call.admit(
            feature="other", provider="bedrock",
            payload=dict(model=HAIKU_EU_GEO_PROFILE, max_tokens=8,
                         messages=[{"role": "user", "content": "hi"}])) as call:
        call.create(create)
    with ai_provider_call.admit(
            feature="other", provider="openai",
            payload=dict(model=SONNET_ID, max_tokens=8,
                         messages=[{"role": "user", "content": "hi"}])) as call:
        call.create(create)
    assert _counts() == (1, 1)
    assert calls[0]["model"] == HAIKU_EU_GEO_PROFILE
    assert calls[1]["model"] == SONNET_ID


def test_unknown_model_makes_zero_provider_calls():
    calls = []
    with pytest.raises(UnknownModelPolicy):
        with ai_provider_call.admit(
                feature="other", provider="bedrock",
                payload=dict(model="gpt-4o-mini", max_tokens=8,
                             messages=[{"role": "user", "content": "hi"}])) as call:
            call.create(lambda **k: calls.append(k))
    assert calls == []
    assert _counts() == (0, 0)


def test_production_tree_has_no_direct_openai_call():
    """A reintroduced api.openai.com / Chat Completions call fails this test."""
    root = Path(ai_spend_guard.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = ast.unparse(node.func)
                if func.endswith("chat.completions.create") or func == "OpenAI":
                    offenders.append(f"{path.name}:{node.lineno}:{func}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "api.openai.com" in node.value:
                    offenders.append(f"{path.name}:{node.lineno}:api.openai.com")
    assert offenders == []
