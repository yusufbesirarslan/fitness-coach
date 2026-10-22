"""MON-AI-ROUTING-5.5: Sonnet 5.5 + Haiku 5.5 routing on Bedrock.

Mocked SDK only: these prove the payload, routing, accounting and refusal
contracts. They are not live Bedrock qualification.
"""
import base64
import io
import json
import logging
from types import SimpleNamespace

import pytest
from PIL import Image

from app import config as app_config
from app.services import (ai, ai_consent, ai_input_budget, ai_model_policy,
                          ai_provider_call, ai_spend_guard, ai_usage)

SONNET45 = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
SONNET55 = "eu.anthropic.claude-sonnet-5-5"
HAIKU55 = "eu.anthropic.claude-haiku-5-5"


@pytest.fixture(autouse=True)
def isolated_guard(monkeypatch):
    ai_spend_guard._reset_local_for_tests()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_provider_call.time, "sleep", lambda _: None)
    yield
    ai_spend_guard._reset_local_for_tests()


@pytest.fixture
def claude55(monkeypatch):
    monkeypatch.setattr(app_config, "AI_MODEL_ROUTING", "claude55")
    monkeypatch.setattr(app_config, "BEDROCK_SONNET55_MODEL", SONNET55)
    monkeypatch.setattr(app_config, "BEDROCK_HAIKU55_MODEL", HAIKU55)
    monkeypatch.setattr(app_config, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", _FakeAnthropic)


@pytest.fixture
def events():
    captured = []

    class Sink(logging.Handler):
        def emit(self, record):
            captured.append(json.loads(record.getMessage().split("[AI-USAGE] ", 1)[1]))
    sink = Sink()
    ai_usage._logger.addHandler(sink)
    yield captured
    ai_usage._logger.removeHandler(sink)


@pytest.fixture
def charged_classes(monkeypatch):
    seen = []
    real = ai_spend_guard._planned_keys

    def spy(cls, subject, now, feature=None):
        seen.append(cls)
        return real(cls, subject, now, feature)
    monkeypatch.setattr(ai_spend_guard, "_planned_keys", spy)
    return seen


class _FakeAPIError(Exception):
    pass


class _FakeAnthropic:
    APIError = _FakeAPIError
    RateLimitError = type("RateLimitError", (_FakeAPIError,), {})
    APITimeoutError = type("APITimeoutError", (_FakeAPIError,), {})
    APIConnectionError = type("APIConnectionError", (_FakeAPIError,), {})


def _reply(text="ok", stop="end_turn", category=None, blocks=None):
    return SimpleNamespace(
        content=blocks if blocks is not None else [SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if category else None,
        usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0))


def _bedrock(monkeypatch, *replies, raises=None):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if raises is not None and kwargs["model"] in raises:
            raise raises[kwargs["model"]]
        return replies[min(len(calls), len(replies)) - 1]
    monkeypatch.setattr(ai, "bedrock_client",
                        SimpleNamespace(messages=SimpleNamespace(create=create)))
    return calls


def _no_openai(monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("OpenAI must not be called under claude55 routing")
    monkeypatch.setattr(ai, "openai_client", SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden))))


def _png(width, height):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# ── configuration ────────────────────────────────────────────────────────────

def test_legacy_is_the_default_and_needs_no_new_setting():
    assert app_config.AI_MODEL_ROUTING == "legacy"
    ai_model_policy.validate_routing_config()
    assert ai_model_policy.heavy_model() == app_config.BEDROCK_MODEL
    assert ai_model_policy.light_route() == ("openai", app_config.OPENAI_MODEL)


@pytest.mark.parametrize("routing,sonnet,haiku,enabled,message", [
    ("Claude55", SONNET55, HAIKU55, True, "AI_MODEL_ROUTING"),
    ("claude55", "", HAIKU55, True, "BEDROCK_SONNET55_MODEL"),
    ("claude55", SONNET55, "", True, "BEDROCK_HAIKU55_MODEL"),
    ("claude55", "anthropic.claude-sonnet-5-5", HAIKU55, True, "BEDROCK_SONNET55_MODEL"),
    ("claude55", "global.anthropic.claude-sonnet-5-5-v1:0", HAIKU55, True,
     "BEDROCK_SONNET55_MODEL"),
    ("claude55", HAIKU55, HAIKU55, True, "BEDROCK_SONNET55_MODEL"),
    ("claude55", SONNET55, SONNET55, True, "BEDROCK_HAIKU55_MODEL"),
    ("claude55", SONNET55, HAIKU55, False, "BEDROCK_ENABLED"),
])
def test_invalid_routing_config_stops_the_boot(monkeypatch, routing, sonnet, haiku,
                                               enabled, message):
    monkeypatch.setattr(app_config, "AI_MODEL_ROUTING", routing)
    monkeypatch.setattr(app_config, "BEDROCK_SONNET55_MODEL", sonnet)
    monkeypatch.setattr(app_config, "BEDROCK_HAIKU55_MODEL", haiku)
    monkeypatch.setattr(app_config, "BEDROCK_ENABLED", enabled)
    with pytest.raises(ai_model_policy.RoutingConfigError, match=message):
        ai_model_policy.validate_routing_config()


@pytest.mark.parametrize("sonnet,haiku", [
    ("eu.anthropic.claude-sonnet-5-5", "eu.anthropic.claude-haiku-5-5"),
    ("global.anthropic.claude-sonnet-5-5", "global.anthropic.claude-haiku-5-5"),
])
def test_registered_profiles_validate(monkeypatch, claude55, sonnet, haiku):
    monkeypatch.setattr(app_config, "BEDROCK_SONNET55_MODEL", sonnet)
    monkeypatch.setattr(app_config, "BEDROCK_HAIKU55_MODEL", haiku)
    ai_model_policy.validate_routing_config()
    assert ai_model_policy.heavy_model() == sonnet
    assert ai_model_policy.light_route() == ("bedrock", haiku)


def test_effort_matrix_covers_every_feature_at_or_below_high():
    assert set(ai_model_policy.EFFORT) == set(ai_input_budget.FEATURES)
    assert set(ai_model_policy.EFFORT.values()) <= {"low", "medium", "high"}


# ── legacy stays byte-identical ──────────────────────────────────────────────

@pytest.mark.usefixtures("synthetic_ai_layer")
def test_legacy_payload_reaches_the_sdk_unchanged(monkeypatch):
    sent = []
    payload = dict(model=SONNET45, max_tokens=100, temperature=0.3,
                   messages=[{"role": "user", "content": "x"}])
    with ai_provider_call.admit(feature="nutrition", provider="bedrock",
                                payload=payload, gate=False) as call:
        call.create(lambda **kw: sent.append(kw) or _reply())
    sent[0].pop("timeout")
    assert sent == [payload]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_legacy_heavy_fallback_still_goes_to_openai(monkeypatch):
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", _FakeAnthropic)
    _bedrock(monkeypatch, raises={SONNET45: _FakeAPIError("down")})
    monkeypatch.setattr(ai, "_openai_chat", lambda *a, **k: "OPENAI")
    assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "OPENAI"


# ── claude55 request surface ─────────────────────────────────────────────────

@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_heavy_call_is_sonnet55_without_sampling_and_with_thinking_off(
        monkeypatch, charged_classes, events):
    calls = _bedrock(monkeypatch, _reply("plan"))
    _no_openai(monkeypatch)
    out = ai._heavy_chat([{"role": "user", "content": "x"}], system_prompt="S",
                         max_tokens=500, temperature=0.35, feature="nutrition_plan")
    assert out == "plan"
    sent = calls[0]
    assert sent["model"] == SONNET55
    assert not {"temperature", "top_p", "top_k"} & set(sent)
    assert sent["thinking"] == {"type": "between_tools"}
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["system"] == "S" and sent["messages"][-1]["role"] == "user"
    assert charged_classes == ["heavy"]
    assert events[-1]["model"] == "claude-sonnet-5-5"
    assert events[-1]["billing_profile"] == "geographic"
    assert events[-1]["routing"] == "claude55"
    assert events[-1]["stop_reason"] == "end_turn"


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_light_path_is_haiku55_on_bedrock_and_charged_light(monkeypatch, charged_classes,
                                                            events):
    calls = _bedrock(monkeypatch, _reply("rice"))
    _no_openai(monkeypatch)
    out = ai._openai_chat([{"role": "user", "content": "pirinç"}], system_prompt="EN",
                          max_tokens=24, temperature=0.0, feature="nutrition")
    assert out == "rice"
    sent = calls[0]
    assert sent["model"] == HAIKU55
    assert "temperature" not in sent
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["output_config"] == {"effort": "low"}
    assert charged_classes == ["light"]
    assert events[-1]["model"] == "claude-haiku-5-5"
    assert "estimated_cost_usd" in events[-1]


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_openai_is_not_a_recipient_under_claude55(monkeypatch, charged_classes):
    sdk = []
    with pytest.raises(ai_model_policy.UnknownModelPolicy):
        with ai_provider_call.admit(feature="nutrition", provider="openai",
                                    payload=dict(model="gpt-4o-mini", max_tokens=10,
                                                 messages=[{"role": "user", "content": "x"}]),
                                    gate=False) as call:
            call.create(lambda **kw: sdk.append(kw))
    assert sdk == [] and charged_classes == []


@pytest.mark.parametrize("model", [
    SONNET45, "global.anthropic.claude-sonnet-5-5", "anthropic.claude-sonnet-5-5",
    "eu.anthropic.claude-sonnet-5-5-v1:0", "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    None,
])
@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_only_the_configured_registered_ids_are_admitted(model, charged_classes):
    sdk = []
    with pytest.raises(ai_model_policy.UnknownModelPolicy):
        with ai_provider_call.admit(feature="coach", provider="bedrock",
                                    payload=dict(model=model, max_tokens=10,
                                                 messages=[{"role": "user", "content": "x"}]),
                                    gate=False) as call:
            call.create(lambda **kw: sdk.append(kw))
    assert sdk == [] and charged_classes == []


@pytest.mark.parametrize("extra,reason", [
    ({"messages": [{"role": "user", "content": "x"},
                   {"role": "assistant", "content": "{"}]}, "assistant_prefill"),
    ({"tool_choice": {"type": "any"}}, "forced_tool_choice"),
    ({"tool_choice": {"type": "tool", "name": "t"}}, "forced_tool_choice"),
])
@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_shapes_the_55_models_reject_are_refused_before_spend(extra, reason, charged_classes):
    payload = dict(model=SONNET55, max_tokens=10,
                   messages=[{"role": "user", "content": "x"}])
    payload.update(extra)
    with pytest.raises(ai_model_policy.UnsupportedRequestShape) as exc:
        with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload,
                                    gate=False) as call:
            call.create(lambda **kw: pytest.fail("sent"))
    assert exc.value.reason == reason
    assert charged_classes == []


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_caller_payload_is_never_mutated_by_the_adaptation():
    payload = dict(model=SONNET55, max_tokens=10, temperature=0.5,
                   messages=[{"role": "user", "content": "x"}])
    before = json.dumps(payload, sort_keys=True)
    with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload,
                                gate=False) as call:
        call.create(lambda **kw: _reply())
    assert json.dumps(payload, sort_keys=True) == before


# ── fallback and refusal ─────────────────────────────────────────────────────

@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_heavy_failure_falls_back_to_haiku_not_openai(monkeypatch, events, charged_classes):
    calls = _bedrock(monkeypatch, _reply("light answer"),
                     raises={SONNET55: _FakeAPIError("down")})
    _no_openai(monkeypatch)
    assert ai._heavy_chat([{"role": "user", "content": "x"}], max_tokens=500,
                          feature="coach") == "light answer"
    assert [c["model"] for c in calls] == [SONNET55, HAIKU55]
    assert charged_classes == ["heavy", "light"]
    haiku = [e for e in events if e["model"] == "claude-haiku-5-5"]
    assert haiku and all(e["fallback"] is True for e in haiku)
    assert all(e["fallback"] is False for e in events if e["model"] == "claude-sonnet-5-5")


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_refusal_is_an_answer_never_a_fallback(monkeypatch, events):
    calls = _bedrock(monkeypatch, _reply("", stop="refusal", category="general_harms"))
    _no_openai(monkeypatch)
    with pytest.raises(ai.ModelRefusal):
        ai._heavy_chat([{"role": "user", "content": "x"}], max_tokens=500, feature="coach")
    assert [c["model"] for c in calls] == [SONNET55]
    assert events[-1]["stop_reason"] == "refusal"
    assert events[-1]["refusal_category"] == "general_harms"


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_haiku_fallback_refusal_never_returns_populated_last_good(monkeypatch, events):
    recovery = ai.ai_recovery
    messages = [{"role": "user", "content": "x"}]
    key = recovery.lastgood_key("heavy_chat", SONNET55, "", messages, 500)
    stored = {}
    monkeypatch.setattr(recovery, "_get_redis", lambda: SimpleNamespace(
        setex=lambda k, ttl, value: stored.__setitem__(k, value),
        get=lambda k: stored.get(k)))
    recovery.remember_last_good(key, "previous answer")
    assert recovery.recall_last_good(key) == "previous answer"
    reads = []
    real_recall = recovery.recall_last_good

    def recall(k):
        reads.append(k)
        return real_recall(k)

    monkeypatch.setattr(recovery, "recall_last_good", recall)
    calls = _bedrock(monkeypatch, _reply("", stop="refusal", category="general_harms"),
                     raises={SONNET55: _FakeAPIError("down")})
    _no_openai(monkeypatch)
    with pytest.raises(ai.ModelRefusal):
        ai._heavy_complete(messages, max_tokens=500, feature="coach")
    assert [c["model"] for c in calls] == [SONNET55, HAIKU55]
    assert reads == []
    assert stored[key] == "previous answer"
    assert events[-1]["stop_reason"] == "refusal"
    assert events[-1]["fallback"] is True


@pytest.mark.parametrize("cache_hit", [False, True])
@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_transient_fallback_failure_keeps_retry_and_last_good_recovery(monkeypatch, cache_hit):
    recovery = ai.ai_recovery
    monkeypatch.setattr(recovery, "AI_RECOVERY_ENABLED", True)
    monkeypatch.setattr(recovery, "AI_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(recovery, "_sleep", lambda _: None)
    reads = []

    def recall(key):
        reads.append(key)
        return "previous answer" if cache_hit else None

    monkeypatch.setattr(recovery, "recall_last_good", recall)
    calls = _bedrock(monkeypatch, raises={
        SONNET55: _FakeAPIError("down"),
        HAIKU55: _FakeAnthropic.APITimeoutError("timeout")})
    _no_openai(monkeypatch)
    if cache_hit:
        result = ai._heavy_complete([{"role": "user", "content": "x"}],
                                    max_tokens=500, feature="coach")
        assert result.text == "previous answer"
        assert result.provider == "cache"
        assert result.finish_reason == "last_good"
        assert result.fallback_used is True
    else:
        with pytest.raises(ai.TransientAIError):
            ai._heavy_complete([{"role": "user", "content": "x"}],
                               max_tokens=500, feature="coach")
    assert [c["model"] for c in calls] == [SONNET55, HAIKU55, HAIKU55]
    assert len(reads) == 1


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_unknown_refusal_category_is_not_exported_verbatim(monkeypatch, events):
    _bedrock(monkeypatch, _reply("", stop="refusal", category="user@example.invalid"))
    with pytest.raises(ai.ModelRefusal):
        ai._claude_chat([{"role": "user", "content": "x"}])
    assert events[-1]["refusal_category"] == "other"


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_spend_refusal_on_sonnet_never_reaches_haiku(monkeypatch):
    calls = _bedrock(monkeypatch, _reply())
    monkeypatch.setitem(ai_spend_guard.LIMITS, ("global", "heavy", "h"), 1)
    ai_spend_guard.charge("bedrock")  # the hourly heavy ceiling is now used up
    with pytest.raises(ai_spend_guard.AISpendLimitExceeded):
        ai._heavy_chat([{"role": "user", "content": "x"}], max_tokens=500, feature="coach")
    assert calls == []


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_vision_uses_sonnet55_and_thinking_blocks_do_not_hide_the_text(monkeypatch):
    blocks = [SimpleNamespace(type="thinking", thinking="", signature="sig"),
              SimpleNamespace(type="text", text='{"valid": true}')]
    calls = _bedrock(monkeypatch, _reply(blocks=blocks))
    out = ai._bedrock_validate_image(_png(64, 64), "image/png", "check", max_tokens=200)
    assert out == '{"valid": true}'
    assert calls[0]["model"] == SONNET55
    assert calls[0]["output_config"] == {"effort": "medium"}


# ── images ───────────────────────────────────────────────────────────────────

def _image_payload(model, data):
    return {"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": data}}]}]}


@pytest.mark.parametrize("model,width,height,tokens", [
    (SONNET45, 3000, 3000, ai_input_budget.ANTHROPIC_IMAGE_TOKENS),
    (SONNET55, 1568, 700, ai_input_budget.ANTHROPIC_IMAGE_TOKENS),
    (SONNET55, 1569, 600, ai_input_budget.ANTHROPIC_HIGH_RES_IMAGE_TOKENS),
    (SONNET55, 1100, 1100, ai_input_budget.ANTHROPIC_HIGH_RES_IMAGE_TOKENS),
    (HAIKU55, 2000, 2000, ai_input_budget.ANTHROPIC_HIGH_RES_IMAGE_TOKENS),
])
def test_image_bound_is_model_aware(model, width, height, tokens):
    data = base64.b64encode(_png(width, height)).decode()
    bound, images = ai_input_budget.input_upper_bound(_image_payload(model, data))
    assert images == 1
    assert bound == _stripped_bytes(model) + ai_input_budget.BASE_OVERHEAD_TOKENS + tokens


def _stripped_bytes(model):
    stripped = {"model": model, "max_tokens": 10,
                "messages": [{"role": "user", "content": [{"type": "image"}]}]}
    return len(json.dumps(stripped, ensure_ascii=False).encode("utf-8"))


def test_undecodable_image_for_a_55_model_counts_at_the_maximum():
    bound, _ = ai_input_budget.input_upper_bound(_image_payload(SONNET55, "not-base64!"))
    assert bound == (_stripped_bytes(SONNET55) + ai_input_budget.BASE_OVERHEAD_TOKENS
                     + ai_input_budget.ANTHROPIC_HIGH_RES_IMAGE_TOKENS)


def test_fit_standard_envelope_downscales_and_keeps_small_images(app):
    from app.services.vision_images import fit_standard_envelope
    with app.app_context():
        small = _png(800, 600)
        assert fit_standard_envelope(small, "image/png") == (small, "image/png")
        big, media_type = fit_standard_envelope(_png(4000, 3000), "image/png")
        width, height = Image.open(io.BytesIO(big)).size
    assert media_type == "image/jpeg"
    assert ai_input_budget.within_standard_envelope(width, height)


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_55_image_blocks_are_sent_inside_the_envelope(monkeypatch):
    calls = _bedrock(monkeypatch, _reply("{}"))
    ai._bedrock_compare_images(_png(3000, 2000), "image/png", _png(500, 500), "image/png",
                               "compare")
    images = [b for b in calls[0]["messages"][0]["content"] if b["type"] == "image"]
    sizes = [Image.open(io.BytesIO(base64.b64decode(b["source"]["data"]))).size
             for b in images]
    assert all(ai_input_budget.within_standard_envelope(w, h) for w, h in sizes)
    assert sizes[1] == (500, 500)


# ── menu OCR ─────────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_menu_ocr_runs_on_haiku_with_an_anthropic_image_block(app, monkeypatch):
    from app.services import menu_ocr
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return _reply("Çorba 50 TL")
    import app.extensions as extensions
    monkeypatch.setattr(extensions, "bedrock_client",
                        SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(menu_ocr, "openai_client", None)
    with app.app_context():
        assert menu_ocr._extract_text_from_image(_png(200, 200), "image/png") == "Çorba 50 TL"
    sent = calls[0]
    assert sent["model"] == HAIKU55
    assert "temperature" not in sent
    assert sent["system"].startswith("Sen bir restoran menüsü OCR")
    kinds = [b["type"] for b in sent["messages"][0]["content"]]
    assert kinds == ["text", "image"]


# ── coach tool loop ──────────────────────────────────────────────────────────

@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_coach_loop_replays_thinking_blocks_unchanged(auth_user, monkeypatch):
    from app.services import ai_coach
    thinking = SimpleNamespace(type="thinking", thinking="looking up", signature="s1")
    tool = SimpleNamespace(type="tool_use", name="cancel_pending_log", input={}, id="t1")
    first = _reply(stop="tool_use", blocks=[thinking, tool])
    second = _reply("done")
    calls = []

    class _Msgs:
        def create(self, **kw):
            calls.append(kw)
            return first if len(calls) == 1 else second
    monkeypatch.setattr(ai_coach, "bedrock_client", SimpleNamespace(messages=_Msgs()))
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", lambda uid, name, args: "{}")
    answer = ai_coach._run_coach_conversation_bedrock(auth_user.id, "q", "", [], language="en")
    assert answer == "done"
    assert [c["model"] for c in calls] == [SONNET55, SONNET55]
    assistant = calls[1]["messages"][-2]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0].type == "thinking"
    assert assistant["content"][0].signature == "s1"
    assert all(c["thinking"] == {"type": "between_tools"} for c in calls)


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_coach_first_call_failure_falls_back_to_the_haiku_loop(auth_user, monkeypatch):
    from app.services import ai_coach
    calls = []

    class _Msgs:
        def create(self, **kw):
            calls.append(kw["model"])
            if kw["model"] == SONNET55:
                raise RuntimeError("bedrock unavailable")
            return _reply("from haiku")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client", SimpleNamespace(messages=_Msgs()))
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: pytest.fail("OpenAI loop must not run"))
    answer = ai_coach._run_coach_conversation(auth_user.id, "q", "", client_history=[],
                                              language="en")
    assert answer == "from haiku"
    assert calls[0] == SONNET55 and calls[-1] == HAIKU55


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_coach_refusal_ends_the_turn_without_fallback(auth_user, monkeypatch):
    from app.services import ai_coach
    calls = []

    class _Msgs:
        def create(self, **kw):
            calls.append(kw["model"])
            return _reply("", stop="refusal", category="bio")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client", SimpleNamespace(messages=_Msgs()))
    answer = ai_coach._run_coach_conversation(auth_user.id, "q", "", client_history=[],
                                              language="en")
    assert calls == [SONNET55]
    assert answer == ai_coach._COACH_FALLBACKS["en"]["error"]


# ── consent, probe, stream ───────────────────────────────────────────────────

def test_consent_grants_are_bound_to_the_exact_model(monkeypatch):
    # A grant whose disclosure names Sonnet 4.5 does not authorize a 5.5 model:
    # switching models needs a new reviewed disclosure, never an inherited one.
    policy = ai_consent.Disclosure(
        version="v-test", purpose="training.plan",
        categories=("fitness_preferences", "body_metrics", "health_constraints"),
        recipients=(("bedrock", SONNET45),), texts=(("en", "d"),))
    monkeypatch.setattr(ai_consent, "POLICIES", {"v-test": policy})
    monkeypatch.setattr(ai_consent, "_record_valid", lambda purpose, record: True)
    state = {"grants": {"training.plan": {"version": "v-test"}}}
    binding = ai_consent.BINDINGS["training_plan"]
    ai_consent._authorize(state, binding, "bedrock", SONNET45)
    for model in (SONNET55, HAIKU55):
        with pytest.raises(ai_consent.ConsentDenied):
            ai_consent._authorize(state, binding, "bedrock", model)


@pytest.mark.usefixtures("claude55")
def test_real_d1_authority_still_denies_under_claude55(app, make_user):
    owner = make_user("routing55_owner")
    sdk = []
    with app.app_context(), ai_spend_guard.subject_scope(owner.id):
        with pytest.raises(ai_consent.ConsentDenied):
            with ai_provider_call.admit(
                    feature="training_plan", provider="bedrock",
                    payload={"model": SONNET55, "max_tokens": 10,
                             "messages": [{"role": "user", "content": "OWN"}]},
                    gate=False, charge=False) as admission:
                admission.create(lambda **kw: sdk.append(kw))
    assert sdk == []


@pytest.mark.usefixtures("claude55")
def test_consent_preflight_route_names_sonnet55():
    assert ai.heavy_primary_route() == ("bedrock", SONNET55)


@pytest.mark.usefixtures("claude55")
def test_health_probe_targets_sonnet55_with_the_55_surface(monkeypatch, events):
    sent = []

    class _Stream:
        def __init__(self, **kw):
            sent.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return _reply("p")
    import app.extensions as extensions
    monkeypatch.setattr(extensions, "bedrock_client",
                        SimpleNamespace(messages=SimpleNamespace(stream=_Stream)))
    ai_provider_call.probe_bedrock_readiness()
    assert sent[0]["model"] == SONNET55
    assert sent[0]["max_tokens"] == 1
    assert sent[0]["thinking"] == {"type": "between_tools"}
    assert events[-1]["model"] == "claude-sonnet-5-5"


@pytest.mark.usefixtures("synthetic_ai_layer", "claude55")
def test_stream_turn_requests_sonnet55(auth_user, monkeypatch, app):
    from app.services import ai_coach, ai_stream
    seen = []

    def fake_turn(client, call_kwargs, **kw):
        seen.append(call_kwargs["model"])
        yield {"kind": "delta", "text": "streamed"}
        yield {"kind": "final", "message": _reply("streamed")}
    monkeypatch.setattr(ai_stream, "_stream_bedrock_turn", fake_turn)
    with app.test_request_context():
        list(ai_stream._stream_bedrock(auth_user.id, "q", "", [], "en"))
    assert seen == [SONNET55]


# ── accounting ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,profile,expected", [
    ("global.anthropic.claude-sonnet-5-5", "global", 100 * 2.00e-6 + 20 * 10.00e-6),
    ("eu.anthropic.claude-sonnet-5-5", "geographic", 100 * 2.20e-6 + 20 * 11.00e-6),
    ("global.anthropic.claude-haiku-5-5", "global", 100 * 0.10e-6 + 20 * 0.50e-6),
    ("eu.anthropic.claude-haiku-5-5", "geographic", 100 * 0.11e-6 + 20 * 0.55e-6),
    ("eu.anthropic.claude-sonnet-4-5-20250929-v1:0", "geographic", None),
    ("anthropic.claude-sonnet-5-5", "direct", None),
    ("global.anthropic.claude-sonnet-5-5-v2", "unknown", None),
])
def test_55_prices_are_per_profile_and_unknowns_stay_unpriced(events, model, profile,
                                                              expected):
    ai_usage.emit(feature="coach", provider="bedrock", model=model, outcome="success",
                  usage={"input_tokens": 100, "output_tokens": 20}, usage_source="provider")
    event = events[-1]
    assert event["billing_profile"] == profile
    if expected is None:
        assert "estimated_cost_usd" not in event
    else:
        assert event["estimated_cost_usd"] == pytest.approx(expected, abs=5e-7)
        assert event["pricing_version"] == ai_usage.PRICING_VERSION


def test_haiku_long_context_prompt_stays_unpriced():
    usage = {"input_tokens": 90_000, "output_tokens": 10, "cache_read_tokens": 10_001,
             "cache_write_tokens": 0}
    assert ai_usage.estimated_cost_usd("bedrock", "claude-haiku-5-5", usage, "global") is None
    usage["cache_read_tokens"] = 10_000
    assert ai_usage.estimated_cost_usd("bedrock", "claude-haiku-5-5", usage, "global") \
        == pytest.approx(90_000 * 0.10e-6 + 10 * 0.50e-6 + 10_000 * 0.01e-6, abs=5e-7)


def test_cache_tokens_are_priced_disjointly_for_sonnet55():
    usage = {"input_tokens": 100, "output_tokens": 20, "cache_write_tokens": 30,
             "cache_read_tokens": 40}
    assert ai_usage.estimated_cost_usd("bedrock", "claude-sonnet-5-5", usage, "global") \
        == pytest.approx(100 * 2e-6 + 20 * 10e-6 + 30 * 2.5e-6 + 40 * 0.1e-6, abs=5e-7)

