"""Hard pre-provider input budget, charged attempts, and AI usage events.

What is pinned here (Phase 2 closeout):
  * the input bound is an UPPER bound on the final payload (UTF-8 bytes +
    fixed overhead + per-image ceilings) and never shrinks for multibyte text;
  * under / exactly at / one over the budget; output (max_tokens) caps;
    image counts; unboundable parts (URL images, documents) refused;
  * deterministic reduction drops only the OLDEST history, re-counts, and
    refuses before the provider when the required content alone does not fit;
  * a refusal makes ZERO provider calls, no fallback, no recovery retry, does
    not consume spend-guard budget and does not leak a capacity permit — in
    every path: heavy/light chat, coach blocking + streaming + tool rounds,
    vision, menu OCR, training/nutrition generation, summaries, fan-out;
  * TOCTOU: the provider receives exactly the checked copy; nothing
    token-bearing can be added after admission;
  * attempts: SDK retries are off, every physical attempt is charged, the
    retry count is bounded, timeouts and spend refusals are never retried;
  * usage events: provider-reported vs estimated, no prompt/response text,
    bounded taxonomy, dated pricing identity.

    python -m pytest tests/test_ai_provider_call.py -v
"""
import ast
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import (
    ai, ai_coach, ai_gate, ai_input_budget, ai_provider_call, ai_recovery,
    ai_spend_guard, ai_stream, ai_usage,
)
from app.services.ai_provider_call import AIInputBudgetExceeded
from app.services.ai_spend_guard import AISpendLimitExceeded

SECRET_PROMPT = "PROMPT-CANARY-7f3a"
SECRET_REPLY = "REPLY-CANARY-91bd"


# ── Fixtures / fakes ────────────────────────────────────────────────────────

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
    monkeypatch.setattr(ai_provider_call.time, "sleep", lambda s: None)
    yield
    ai_spend_guard._reset_local_for_tests()


def _spent():
    """Admitted provider attempts: every charge increments exactly one global
    DAILY counter (heavy or light), whatever else it increments."""
    return sum(v for k, v in ai_spend_guard._local_counts.items()
               if ":global:" in k and (":heavy:d:" in k or ":light:d:" in k))


class Usage(SimpleNamespace):
    pass


def _bedrock_resp(text=SECRET_REPLY, stop="end_turn", content=None, usage=None):
    return SimpleNamespace(
        stop_reason=stop,
        content=content if content is not None else [SimpleNamespace(type="text", text=text)],
        usage=usage or Usage(input_tokens=120, output_tokens=30,
                             cache_creation_input_tokens=0, cache_read_input_tokens=0))


class Messages:
    """bedrock_client.messages stand-in recording every physical attempt."""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return _bedrock_resp()


class OpenAIFake:
    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        message = SimpleNamespace(content=SECRET_REPLY, tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=0)))


class StatusError(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


@pytest.fixture
def providers(monkeypatch):
    bedrock = Messages()
    openai_fake = OpenAIFake()
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=bedrock))
    monkeypatch.setattr(ai, "openai_client", openai_fake)
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_recovery, "recall_last_good", lambda key: None)
    monkeypatch.setattr(ai_recovery, "remember_last_good", lambda key, value: None)
    monkeypatch.setattr(ai_recovery, "_sleep", lambda s: None)
    return SimpleNamespace(bedrock=bedrock, openai=openai_fake)


@pytest.fixture
def usage_events():
    events = []

    class _Capture(logging.Handler):
        def emit(self, record):
            events.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger("fitx.ai_usage")
    logger.addHandler(handler)
    yield events
    logger.removeHandler(handler)


def _parsed(events):
    out = []
    for line in events:
        assert line.startswith("[AI-USAGE] ")
        out.append(json.loads(line[len("[AI-USAGE] "):]))
    return out


def _budget(monkeypatch, feature, value):
    monkeypatch.setitem(ai_input_budget.INPUT_BUDGETS, feature, value)


def _payload(text, max_tokens=100, **extra):
    return dict(model="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": text}], **extra)


def _slots_free():
    # Every permit returned: the gate semaphore can be drained to its size.
    taken = 0
    while ai_gate._model_slots.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        ai_gate._model_slots.release()
    return taken


# ── The bound ───────────────────────────────────────────────────────────────

def test_bound_is_utf8_bytes_plus_fixed_overhead():
    payload = {"messages": [{"role": "user", "content": "a"}]}
    serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    bound, images = ai_input_budget.input_upper_bound(payload)
    assert images == 0
    assert bound == len(serialized) + ai_input_budget.BASE_OVERHEAD_TOKENS


def test_turkish_multibyte_text_is_counted_in_bytes_not_characters():
    turkish = "ığüşöçİĞÜŞÖÇ" * 500          # 6,000 chars, 2 bytes each
    bound, _ = ai_input_budget.input_upper_bound({"messages": [{"content": turkish}]})
    assert bound >= len(turkish.encode("utf-8")) + ai_input_budget.BASE_OVERHEAD_TOKENS
    assert bound > 2 * len(turkish)  # a chars/4 estimate would be ~1,500


def test_images_are_bounded_per_provider_rule():
    anthropic = {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "A" * 400_000}}]}]}
    bound, images = ai_input_budget.input_upper_bound(anthropic)
    assert images == 1
    # image bytes are NOT counted as text; the per-image ceiling is
    assert ai_input_budget.ANTHROPIC_IMAGE_TOKENS < bound < 4_000

    high = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}
    low = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA",
                                            "detail": "low"}}]}]}
    assert ai_input_budget.input_upper_bound(high)[0] > ai_input_budget.OPENAI_IMAGE_TOKENS_HIGH
    assert ai_input_budget.OPENAI_IMAGE_TOKENS_LOW < ai_input_budget.input_upper_bound(low)[0] \
        < ai_input_budget.OPENAI_IMAGE_TOKENS_HIGH


@pytest.mark.parametrize("block", [
    {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}},
    {"type": "document", "source": {"type": "base64", "data": "AAAA"}},
])
def test_unboundable_parts_are_refused_before_the_provider(block, providers):
    payload = _payload("x")
    payload["messages"][0]["content"] = [block]
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="vision", provider="bedrock", payload=payload):
            pytest.fail("admitted")  # pragma: no cover
    assert providers.bedrock.calls == []


def test_sdk_response_objects_in_the_tool_loop_are_counted():
    class Block:
        def model_dump(self, **kwargs):
            return {"type": "tool_use", "id": "t1", "name": "n", "input": {"q": "x" * 5000}}

    bound, _ = ai_input_budget.input_upper_bound(
        {"messages": [{"role": "assistant", "content": [Block()]}]})
    assert bound > 5000


# ── Budget edges ────────────────────────────────────────────────────────────

def test_under_cap_is_admitted_and_sent_once(providers):
    with ai_provider_call.admit(feature="other", provider="bedrock",
                                payload=_payload("hi")) as call:
        call.create(providers.bedrock.create)
    assert len(providers.bedrock.calls) == 1


def test_exactly_at_cap_is_admitted_and_one_over_is_refused(providers, monkeypatch):
    payload = _payload("x" * 1000)
    bound, _ = ai_input_budget.input_upper_bound(payload)
    _budget(monkeypatch, "other", bound)
    with ai_provider_call.admit(feature="other", provider="bedrock", payload=payload) as call:
        call.create(providers.bedrock.create)
    over = _payload("x" * 1001)
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="other", provider="bedrock", payload=over):
            pass  # pragma: no cover
    assert len(providers.bedrock.calls) == 1


@pytest.mark.parametrize("max_tokens", [None, 0, -1, "700", 2001])
def test_missing_or_over_cap_output_is_refused(max_tokens, providers):
    payload = _payload("hi")
    if max_tokens is None:
        payload.pop("max_tokens")
    else:
        payload["max_tokens"] = max_tokens
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="other", provider="bedrock", payload=payload):
            pass  # pragma: no cover
    assert providers.bedrock.calls == []


def test_image_count_is_capped_per_feature(providers):
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}
    payload = _payload("x")
    payload["messages"][0]["content"] = [img, img, img]
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="vision", provider="bedrock", payload=payload):
            pass  # pragma: no cover
    payload["messages"][0]["content"] = [img]
    with pytest.raises(AIInputBudgetExceeded):  # coach carries no images at all
        with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload):
            pass  # pragma: no cover
    assert providers.bedrock.calls == []


def test_unknown_feature_is_the_bounded_other_bucket(providers):
    with ai_provider_call.admit(feature="<script>user text", provider="bedrock",
                                payload=_payload("hi")) as call:
        assert call.feature == "other"


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
def test_budget_env_values_are_validated(raw, monkeypatch):
    monkeypatch.setenv("AI_INPUT_BUDGET_TEST", raw)
    with pytest.raises(ai_input_budget.BudgetConfigError):
        ai_input_budget._env_positive("AI_INPUT_BUDGET_TEST", 10)


def test_budget_env_override_and_default(monkeypatch):
    monkeypatch.setenv("AI_INPUT_BUDGET_TEST", " 1234 ")
    assert ai_input_budget._env_positive("AI_INPUT_BUDGET_TEST", 10) == 1234
    monkeypatch.delenv("AI_INPUT_BUDGET_TEST")
    assert ai_input_budget._env_positive("AI_INPUT_BUDGET_TEST", 10) == 10


def test_every_feature_has_input_output_and_image_policy():
    for feature in ai_input_budget.FEATURES:
        assert ai_input_budget.INPUT_BUDGETS[feature] > 0
        assert ai_input_budget.OUTPUT_BUDGETS[feature] > 0
        assert ai_input_budget.IMAGE_LIMITS[feature] >= 0
    # No budget is provider-context-sized.
    assert max(ai_input_budget.INPUT_BUDGETS.values()) <= 64_000


# ── Deterministic reduction ─────────────────────────────────────────────────

def _coach_payload(history_pairs, question="GÜNCEL SORU", pad=2000):
    messages = []
    for i in range(history_pairs):
        messages.append({"role": "user", "content": f"U{i} " + "x" * pad})
        messages.append({"role": "assistant", "content": f"A{i} " + "y" * pad})
    messages.append({"role": "user", "content": question})
    return dict(model="m", max_tokens=700, system="SİSTEM-GÜVENLİK", tools=[{"name": "t"}],
                messages=messages), len(messages) - 1


def test_oldest_history_is_dropped_first_and_the_request_survives(providers, monkeypatch):
    payload, hist = _coach_payload(5)
    full, _ = ai_input_budget.input_upper_bound(payload)
    _budget(monkeypatch, "coach", full - 3000)       # forces ~2 pairs out
    with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload,
                                reduce=ai_input_budget.history_reducer(0, hist)) as call:
        call.create(providers.bedrock.create)
    sent = providers.bedrock.calls[0]
    texts = [m["content"][:3] for m in sent["messages"]]
    assert texts[0] == "U1 " and texts[-1] == "GÜN"      # oldest pair gone, question kept
    assert sent["messages"][0]["role"] == "user"
    assert sent["system"] == "SİSTEM-GÜVENLİK" and sent["tools"] == [{"name": "t"}]
    # the caller's own list is untouched (reduction works on the checked copy)
    assert payload["messages"][0]["content"].startswith("U0")
    assert ai_input_budget.input_upper_bound(sent)[0] <= full - 3000


def test_openai_history_reduction_keeps_leading_system_messages(providers, monkeypatch):
    messages = [{"role": "system", "content": "SYS"}, {"role": "system", "content": "CTX"}]
    for i in range(3):
        messages += [{"role": "user", "content": f"U{i}" + "x" * 3000},
                     {"role": "assistant", "content": f"A{i}" + "y" * 3000}]
    messages.append({"role": "user", "content": "Q"})
    payload = dict(model="gpt-4o-mini", max_tokens=700, messages=messages)
    _budget(monkeypatch, "coach", ai_input_budget.input_upper_bound(payload)[0] - 5000)
    with ai_provider_call.admit(feature="coach", provider="openai", payload=payload,
                                reduce=ai_input_budget.history_reducer(2, 6)) as call:
        call.create(providers.openai.chat.completions.create)
    sent = providers.openai.calls[0]["messages"]
    assert [m["content"][:3] for m in sent[:2]] == ["SYS", "CTX"]
    assert sent[2]["content"].startswith("U1") and sent[-1]["content"] == "Q"


def test_required_content_that_cannot_fit_is_refused_before_the_provider(
        providers, monkeypatch):
    payload, hist = _coach_payload(3, question="q" * 20_000)
    _budget(monkeypatch, "coach", 15_000)
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload,
                                    reduce=ai_input_budget.history_reducer(0, hist)):
            pass  # pragma: no cover
    assert providers.bedrock.calls == []


def test_system_prompt_is_never_trimmed(providers, monkeypatch):
    payload, hist = _coach_payload(0)
    payload["system"] = "S" * 30_000
    _budget(monkeypatch, "coach", 20_000)
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload,
                                    reduce=ai_input_budget.history_reducer(0, hist)):
            pass  # pragma: no cover
    assert providers.bedrock.calls == []


# ── Refusal: zero calls, zero spend, no permit leak ─────────────────────────

def test_refusal_consumes_no_spend_budget_and_returns_no_permit(providers, monkeypatch):
    free_before = _slots_free()
    _budget(monkeypatch, "other", 2000)
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="other", provider="bedrock",
                                    payload=_payload("x" * 5000)):
            pass  # pragma: no cover
    assert _spent() == 0
    assert _slots_free() == free_before
    assert providers.bedrock.calls == []


def test_refusal_is_a_spend_refusal_so_existing_handlers_apply():
    exc = AIInputBudgetExceeded("coach", "heavy")
    assert isinstance(exc, AISpendLimitExceeded)
    assert isinstance(exc, ai_gate.BlockingConcurrencyLimit)
    assert "64000" not in str(exc) and "budget" not in str(exc).lower()


def test_heavy_chat_refusal_has_no_openai_fallback_and_no_recovery_retry(
        app, providers, monkeypatch):
    _budget(monkeypatch, "training_plan", 3000)
    with pytest.raises(AIInputBudgetExceeded):
        ai._heavy_complete([{"role": "user", "content": "x" * 10_000}],
                           max_tokens=4000, feature="training_plan")
    assert providers.bedrock.calls == [] and providers.openai.calls == []


@pytest.mark.parametrize("fn,feature,kwargs", [
    ("_openai_chat", "summary", {"max_tokens": 500}),
    ("_openai_chat", "nutrition", {"max_tokens": 400}),
    ("_heavy_chat", "nutrition_plan", {"max_tokens": 2000}),
    ("_heavy_chat", "menu_extract", {"max_tokens": 5000}),
    ("_heavy_chat", "coach", {"max_tokens": 700}),
])
def test_every_wrapper_feature_is_budgeted(app, providers, monkeypatch, fn, feature, kwargs):
    _budget(monkeypatch, feature, 3000)
    with pytest.raises(AIInputBudgetExceeded):
        getattr(ai, fn)([{"role": "user", "content": "x" * 10_000}], feature=feature, **kwargs)
    assert providers.bedrock.calls == [] and providers.openai.calls == []


def test_direct_vision_helper_invocation_is_budgeted(app, providers, monkeypatch):
    _budget(monkeypatch, "vision", 3000)
    with pytest.raises(AIInputBudgetExceeded):
        ai._bedrock_validate_image(b"img", "image/jpeg", "p" * 5000, max_tokens=200)
    with pytest.raises(AIInputBudgetExceeded):
        ai._bedrock_compare_images(b"a", "image/jpeg", b"b", "image/jpeg", "p" * 5000)
    assert providers.bedrock.calls == []


def test_parallel_fan_out_batches_are_each_budgeted(app, providers, monkeypatch):
    _budget(monkeypatch, "nutrition", 3000)

    def batch(_):
        with app.app_context():
            try:
                ai._heavy_chat([{"role": "user", "content": "x" * 10_000}],
                               max_tokens=1000, feature="nutrition")
            except AIInputBudgetExceeded:
                return "refused"

    with ThreadPoolExecutor(max_workers=4) as ex:
        assert list(ex.map(batch, range(8))) == ["refused"] * 8
    assert providers.bedrock.calls == [] and providers.openai.calls == []


def test_budget_holds_without_request_context_and_with_redis_down(monkeypatch, providers):
    class DownRedis:
        def pipeline(self, transaction=True):
            raise ConnectionError("down")

    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: DownRedis())
    _budget(monkeypatch, "summary", 2000)
    # no app/request context at all: a background worker call
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="summary", provider="openai",
                                    payload=dict(model="gpt-4o-mini", max_tokens=500,
                                                 messages=[{"role": "user",
                                                            "content": "x" * 5000}])):
            pass  # pragma: no cover
    assert providers.openai.calls == []


# ── Coach: blocking tool loop and streaming ─────────────────────────────────

def _tool_use():
    return _bedrock_resp(stop="tool_use", content=[
        SimpleNamespace(type="tool_use", name="query_fitx_metrics", id="t1",
                        input={"metric_type": "nutrition"})])


@pytest.fixture
def coach_providers(monkeypatch):
    bedrock = Messages()
    openai_fake = OpenAIFake()
    monkeypatch.setattr(ai_coach, "bedrock_client", SimpleNamespace(messages=bedrock))
    monkeypatch.setattr(ai_coach, "openai_client", openai_fake)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    return SimpleNamespace(bedrock=bedrock, openai=openai_fake)


def test_tool_result_growth_is_rechecked_before_every_round(
        app, coach_providers, make_user, monkeypatch):
    """Round 1 fits; a huge tool result makes round 2 exceed the budget: round
    2 is refused before the provider, with no OpenAI fallback."""
    user = make_user("grower")
    coach_providers.bedrock.script = [_tool_use()]
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool",
                        lambda uid, name, args: json.dumps({"rows": "r" * 90_000}))
    with app.test_request_context("/ask", method="POST"):
        with ai_spend_guard.subject_scope(user.id):
            reply = ai_coach._run_coach_conversation(user.id, "soru", "", client_history=[])
    assert len(coach_providers.bedrock.calls) == 1          # round 2 never sent
    assert coach_providers.openai.calls == []
    assert reply in ai_coach._COACH_FALLBACKS["tr"].values()
    assert _spent() == 1                                     # the refused round spent nothing


def test_oversized_coach_question_never_reaches_either_provider(
        app, coach_providers, make_user, monkeypatch):
    user = make_user("bigq")
    _budget(monkeypatch, "coach", 20_000)
    with app.test_request_context("/ask", method="POST"):
        with ai_spend_guard.subject_scope(user.id):
            reply = ai_coach._run_coach_conversation(
                user.id, "ş" * 30_000, "", client_history=[])
    assert coach_providers.bedrock.calls == [] and coach_providers.openai.calls == []
    assert reply == ai_coach._COACH_FALLBACKS["tr"]["error"]


class _Stream:
    def __init__(self, text="cevap"):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield self.text

    def get_final_message(self):
        return _bedrock_resp(text=self.text)


class StreamClient:
    def __init__(self):
        self.calls = []

    @property
    def messages(self):
        return SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream()


@pytest.fixture
def stream_client(monkeypatch):
    client = StreamClient()
    monkeypatch.setattr(ai_coach, "bedrock_client", client)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())

    def no_openai(*args, **kwargs):
        raise AssertionError("an input-budget refusal must not fall back to OpenAI")
        yield  # pragma: no cover

    monkeypatch.setattr(ai_stream, "_stream_openai_fallback", no_openai)
    return client


def test_streaming_path_uses_the_same_budget(app, stream_client, monkeypatch):
    _budget(monkeypatch, "coach", 20_000)
    events = list(ai_stream.stream_coach_answer(42, "ş" * 30_000, "", []))
    assert stream_client.calls == []
    assert events[-1]["type"] == "error" and events[-1]["work_performed"] is False


def test_streaming_success_is_admitted_charged_and_measured(app, stream_client, usage_events):
    events = list(ai_stream.stream_coach_answer(42, "soru", "", []))
    assert events[-1]["type"] == "done"
    assert len(stream_client.calls) == 1 and _spent() >= 1
    (event,) = [e for e in _parsed(usage_events) if e["outcome"] == "success"]
    assert event["feature"] == "coach" and event["usage_source"] == "provider"
    assert event["input_tokens"] == 120 and event["tool_round"] == 1


def test_abandoned_stream_is_recorded_as_estimated_not_zero(usage_events):
    payload = _payload("x")
    with ai_provider_call.admit(feature="coach", provider="bedrock-stream",
                                payload=payload) as call:
        with call.stream(lambda **kw: _Stream()) as stream:
            next(iter(stream.text_stream))   # consumer leaves before the final message
    (event,) = _parsed(usage_events)
    assert event["outcome"] == "client_disconnect"
    assert event["usage_source"] == "estimated"
    assert event["input_tokens"] == event["input_bound"] > 0


# ── Menu OCR / PDF fan-out ──────────────────────────────────────────────────

def test_menu_ocr_is_budgeted_and_refusal_is_unreadable(app, monkeypatch):
    from app.services import menu_ocr
    fake = OpenAIFake()
    monkeypatch.setattr(menu_ocr, "openai_client", fake)
    with app.app_context():
        assert menu_ocr._extract_text_from_image(b"\x89PNG" + b"0" * 100, "image/png") \
            == SECRET_REPLY
        assert len(fake.calls) == 1 and _spent() == 1
        _budget(monkeypatch, "menu_ocr", 10_000)       # below one high-detail image
        assert menu_ocr._extract_text_from_image(b"\x89PNG" + b"0" * 100, "image/png") == ""
    assert len(fake.calls) == 1 and _spent() == 1


def test_scanned_pdf_ocr_fan_out_is_capped_by_policy(app, monkeypatch):
    import io
    from PIL import Image
    from app.services import menu_ocr

    pages = [Image.new("RGB", (60, 60), "white") for _ in range(9)]
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    seen = []
    monkeypatch.setattr(menu_ocr, "_extract_pdf_pages_via_vision",
                        lambda pdf, idx: seen.append(list(idx)) or "")
    monkeypatch.setattr(ai_input_budget, "MENU_OCR_MAX_PAGES", 3)
    with app.app_context():
        menu_ocr._extract_text_from_pdf(buf.getvalue())
    assert seen == [[0, 1, 2]]


# ── TOCTOU ──────────────────────────────────────────────────────────────────

def test_the_provider_receives_exactly_the_checked_copy(providers):
    messages = [{"role": "user", "content": "checked"}]
    payload = dict(model="m", max_tokens=100, messages=messages)
    with ai_provider_call.admit(feature="other", provider="bedrock", payload=payload) as call:
        messages.append({"role": "user", "content": "x" * 900_000})   # after the check
        messages[0]["content"] = "mutated"
        call.create(providers.bedrock.create)
    assert providers.bedrock.calls[0]["messages"] == [{"role": "user", "content": "checked"}]


@pytest.mark.parametrize("extra", [{"messages": []}, {"system": "x"}, {"tools": []},
                                   {"max_tokens": 9000}, {"extra_body": {"a": 1}}])
def test_nothing_token_bearing_can_be_added_after_admission(providers, extra):
    with ai_provider_call.admit(feature="other", provider="bedrock",
                                payload=_payload("hi")) as call:
        with pytest.raises(TypeError):
            call.create(providers.bedrock.create, **extra)
    assert providers.bedrock.calls == []


def test_an_admission_covers_one_call_only(providers):
    with ai_provider_call.admit(feature="other", provider="bedrock",
                                payload=_payload("hi")) as call:
        call.create(providers.bedrock.create)
        with pytest.raises(RuntimeError):
            call.create(providers.bedrock.create)
    assert len(providers.bedrock.calls) == 1


# ── Attempts and retries ────────────────────────────────────────────────────

def test_sdk_clients_are_built_without_internal_retries(monkeypatch):
    from app import extensions
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["openai"] = kwargs

    class FakeBedrock:
        def __init__(self, **kwargs):
            captured["bedrock"] = kwargs

    monkeypatch.setattr(extensions, "OpenAI", FakeOpenAI)
    import anthropic
    monkeypatch.setattr(anthropic, "AnthropicBedrock", FakeBedrock)
    getattr(extensions._LazyOpenAI(), "chat", None)
    getattr(extensions._LazyAnthropicBedrock(), "messages", None)
    assert captured["openai"]["max_retries"] == 0
    assert captured["bedrock"]["max_retries"] == 0


def test_transient_failure_retry_is_a_separately_charged_attempt(providers, usage_events):
    providers.bedrock.script = [APIConnectionError("reset"), _bedrock_resp()]
    with ai_provider_call.admit(feature="other", provider="bedrock",
                                payload=_payload("hi")) as call:
        call.create(providers.bedrock.create)
    assert len(providers.bedrock.calls) == 2
    assert _spent() == 2              # two physical attempts, two charges
    outcomes = [(e["attempt"], e["outcome"]) for e in _parsed(usage_events)]
    assert outcomes == [(1, "provider_error"), (2, "success")]


@pytest.mark.parametrize("provider,expected", [("bedrock", 2), ("openai", 3)])
def test_retry_count_is_bounded(providers, provider, expected):
    fake = providers.bedrock if provider == "bedrock" else providers.openai
    fake.script = [StatusError(503)] * 10
    method = fake.create if provider == "bedrock" else fake.chat.completions.create
    with pytest.raises(StatusError):
        with ai_provider_call.admit(feature="other", provider=provider,
                                    payload=_payload("hi")) as call:
            call.create(method)
    assert len(fake.calls) == expected
    assert expected == 1 + (ai_provider_call.BEDROCK_MAX_RETRIES if provider == "bedrock"
                            else ai_provider_call.OPENAI_MAX_RETRIES)


def _sdk_timeouts():
    import anthropic
    import httpx
    import openai
    request = httpx.Request("POST", "https://example.invalid")
    return [anthropic.APITimeoutError(request=request), openai.APITimeoutError(request=request)]


def test_real_sdk_timeouts_are_connection_errors_but_never_retried():
    for exc in _sdk_timeouts():
        assert any(c.__name__ == "APIConnectionError" for c in type(exc).__mro__)
        assert ai_provider_call._is_retryable(exc) is False


@pytest.mark.parametrize("exc", [APITimeoutError("read"), StatusError(400), StatusError(403)]
                         + _sdk_timeouts())
def test_timeouts_and_permanent_errors_are_not_retried(providers, exc, usage_events):
    providers.bedrock.script = [exc, _bedrock_resp()]
    with pytest.raises(type(exc)):
        with ai_provider_call.admit(feature="other", provider="bedrock",
                                    payload=_payload("hi")) as call:
            call.create(providers.bedrock.create)
    assert len(providers.bedrock.calls) == 1
    (event,) = _parsed(usage_events)
    assert event["usage_source"] == "estimated" and event["input_tokens"] > 0
    assert event["outcome"] == ("timeout" if "Timeout" in type(exc).__name__
                                else "provider_error")


def test_a_spend_refusal_on_retry_ends_the_call(providers, monkeypatch, usage_events):
    monkeypatch.setattr(ai_spend_guard, "LIMITS", {("global", "heavy", "d"): 1})
    providers.bedrock.script = [StatusError(529), _bedrock_resp()]
    with pytest.raises(AISpendLimitExceeded):
        with ai_provider_call.admit(feature="other", provider="bedrock",
                                    payload=_payload("hi")) as call:
            call.create(providers.bedrock.create)
    assert len(providers.bedrock.calls) == 1
    assert [e["outcome"] for e in _parsed(usage_events)] == ["provider_error", "guard_rejected"]


def test_recovery_ladder_attempts_are_each_admitted(app, providers):
    # two recovery attempts x (1 + BEDROCK_MAX_RETRIES) physical attempts,
    # then the OpenAI fallback with its own charged attempts: all bounded.
    import anthropic
    import httpx
    import openai
    request = httpx.Request("POST", "https://example.invalid")
    providers.bedrock.script = [anthropic.APIConnectionError(request=request)] * 10
    providers.openai.script = [openai.APIConnectionError(request=request)] * 10
    with pytest.raises(Exception):
        ai._heavy_complete([{"role": "user", "content": "hi"}], max_tokens=100)
    bedrock_attempts = len(providers.bedrock.calls)
    openai_attempts = len(providers.openai.calls)
    assert bedrock_attempts == 2 * (1 + ai_provider_call.BEDROCK_MAX_RETRIES)
    assert openai_attempts == 2 * (1 + ai_provider_call.OPENAI_MAX_RETRIES)
    assert _spent() == bedrock_attempts + openai_attempts


# ── Usage events ────────────────────────────────────────────────────────────

def test_success_event_carries_provider_usage_and_no_content(app, providers, usage_events):
    with app.test_request_context("/x"):
        from flask import g
        g.request_id = "req-123"
        with ai_spend_guard.subject_scope(77):
            ai._heavy_chat([{"role": "user", "content": SECRET_PROMPT}],
                           system_prompt="sys", max_tokens=700, feature="coach")
    (line,) = usage_events
    assert SECRET_PROMPT not in line and SECRET_REPLY not in line and "sys" not in line
    event = _parsed(usage_events)[0]
    assert event == {**event, "event": "ai_usage", "request_id": "req-123", "subject_id": 77,
                     "provider": "bedrock", "model": "claude-sonnet-4-5", "feature": "coach",
                     "outcome": "success", "attempt": 1, "usage_source": "provider",
                     "input_tokens": 120, "output_tokens": 30, "output_cap": 700,
                     "pricing_version": ai_usage.PRICING_VERSION,
                     "pricing_model": "bedrock:claude-sonnet-4-5"}
    assert event["estimated_cost_usd"] == pytest.approx((120 * 3 + 30 * 15) / 1e6)


def test_openai_cached_tokens_are_split_out():
    resp = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=1000, completion_tokens=5,
        prompt_tokens_details=SimpleNamespace(cached_tokens=400)))
    assert ai_usage.usage_from_response("openai", resp) == {
        "input_tokens": 600, "output_tokens": 5,
        "cache_write_tokens": 0, "cache_read_tokens": 400}


def test_rejection_events_are_bounded_and_never_priced(providers, monkeypatch, usage_events):
    _budget(monkeypatch, "other", 1500)
    with pytest.raises(AIInputBudgetExceeded):
        with ai_provider_call.admit(feature="other", provider="bedrock",
                                    payload=_payload(SECRET_PROMPT * 200)):
            pass  # pragma: no cover
    (line,) = usage_events
    assert SECRET_PROMPT not in line
    event = _parsed(usage_events)[0]
    assert event["outcome"] == "input_budget_rejected"
    assert "estimated_cost_usd" not in event


def test_bound_violation_is_flagged_loudly(providers, usage_events):
    providers.bedrock.script = [_bedrock_resp(usage=Usage(
        input_tokens=10_000_000, output_tokens=1,
        cache_creation_input_tokens=0, cache_read_input_tokens=0))]
    with ai_provider_call.admit(feature="other", provider="bedrock",
                                payload=_payload("hi")) as call:
        call.create(providers.bedrock.create)
    assert _parsed(usage_events)[0]["bound_violation"] is True


def test_event_fields_are_from_fixed_vocabularies(usage_events):
    ai_usage.emit(feature="'; DROP", provider="evil", model="attacker-model",
                  outcome="whatever", subject_id=1)
    event = _parsed(usage_events)[0]
    assert event["feature"] == "other" and event["provider"] == "other"
    assert event["model"] == "other" and event["outcome"] == "provider_error"


def test_rejection_metric_has_no_per_user_or_feature_dimension(providers, monkeypatch):
    from app.services import runtime_metrics
    seen = []
    monkeypatch.setattr(runtime_metrics, "increment",
                        lambda name, dimensions=None: seen.append((name, dimensions)))
    _budget(monkeypatch, "other", 1500)
    with ai_spend_guard.subject_scope(99):
        with pytest.raises(AIInputBudgetExceeded):
            with ai_provider_call.admit(feature="other", provider="bedrock",
                                        payload=_payload("x" * 5000)):
                pass  # pragma: no cover
    assert seen == [("AiInputBudgetRejections", {"Class": "heavy"})]


# ── Structure: the door is the only way in ──────────────────────────────────

_SDK_ENTRY_POINTS = {("messages", "create"), ("messages", "stream"),
                     ("completions", "create"), ("messages", "count_tokens"),
                     ("responses", "create")}


def _attr_tail(node):
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    return tuple(reversed(names))


def test_no_module_calls_a_provider_sdk_entry_point_directly():
    """SDK entry points may only be PASSED to an admission (call.create/stream);
    calling one directly would skip the input check, the charge and telemetry."""
    root = Path(ai.__file__).resolve().parents[2]
    offenders = []
    for base in ("app", "fitx_mcp"):
        for path in (root / base).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if _attr_tail(node.func)[-2:] in _SDK_ENTRY_POINTS:
                        offenders.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    assert offenders == []


def test_every_module_touching_an_sdk_entry_point_uses_the_door():
    root = Path(ai.__file__).resolve().parents[2]
    offenders = []
    for path in (root / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        touches = any(isinstance(n, ast.Attribute) and _attr_tail(n)[-2:] in _SDK_ENTRY_POINTS
                      for n in ast.walk(tree))
        if touches and "ai_provider_call.admit(" not in text:
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []


def test_input_check_runs_before_the_permit_and_the_charge():
    """admit(): check_payload() precedes model_concurrency_slot()/charge()."""
    import inspect
    src = inspect.getsource(ai_provider_call.admit)
    assert src.index("check_payload(") < src.index("model_concurrency_slot(")
    assert src.index("check_payload(") < src.index("ai_spend_guard.charge(")


def test_charge_precedes_every_physical_retry():
    import inspect
    src = inspect.getsource(ai_provider_call.Admission.create)
    loop = src[src.index("for attempt"):]
    assert loop.index("ai_spend_guard.charge(") < loop.index("method(**kwargs")
