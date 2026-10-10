"""FINOPS-01: telemetry failures never control serving; identities stay finite."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import ai_input_budget, ai_provider_call, ai_spend_guard, ai_usage


@pytest.fixture(autouse=True)
def isolated_guard(monkeypatch):
    ai_spend_guard._reset_local_for_tests()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_provider_call.time, "sleep", lambda _: None)
    yield
    ai_spend_guard._reset_local_for_tests()


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


def payload():
    return dict(model="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                max_tokens=100, messages=[{"role": "user", "content": "prompt-secret"}],
                temperature=0, tools=[{"name": "t", "input_schema": {"type": "object"}}])


def response():
    return SimpleNamespace(content="response-secret", usage=SimpleNamespace(
        input_tokens=100, output_tokens=20, cache_creation_input_tokens=30,
        cache_read_input_tokens=40))


@pytest.mark.parametrize("broken", ["emit", "usage_from_response"])
def test_telemetry_failure_keeps_success_payload_timeout_charge_and_response(monkeypatch, broken):
    def run():
        seen = []
        answer = response()
        def method(**kwargs):
            seen.append(kwargs)
            return answer
        with ai_provider_call.admit(feature="nutrition", provider="bedrock",
                                    payload=payload(), gate=False) as call:
            result = call.create(method, timeout=7)
        assert result is answer
        return seen, dict(ai_spend_guard._local_counts)
    healthy = run()
    ai_spend_guard._reset_local_for_tests()
    monkeypatch.setattr(ai_usage, broken, lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("token-secret")))
    assert run() == healthy


def test_missing_success_usage_is_explicit_partial_estimate(events):
    with ai_provider_call.admit(feature="nutrition", provider="bedrock",
                                payload=payload(), gate=False) as call:
        call.create(lambda **kw: SimpleNamespace(usage=None))
    assert len(events) == 1
    assert events[0]["usage_source"] == "estimated"
    assert events[0]["input_tokens"] == events[0]["input_bound"]
    assert events[0]["output_tokens"] is None


@pytest.mark.parametrize("model,profile,priced", [
    ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", "global", True),
    ("eu.anthropic.claude-sonnet-4-5-20250929-v1:0", "geographic", False),
    ("anthropic.claude-sonnet-4-5-20250929-v1:0", "direct", False),
    ("global.anthropic.claude-sonnet-4-5-future", "unknown", False),
])
def test_billing_profile_never_silently_prices_another_class(events, model, profile, priced):
    ai_usage.emit(feature="coach", provider="bedrock", model=model, outcome="success",
                  usage={"input_tokens": 100, "output_tokens": 20}, usage_source="provider")
    event = events[0]
    assert event["billing_profile"] == profile
    assert ("estimated_cost_usd" in event) == priced
    assert event["schema_version"] == 2


def test_thread_correlation_is_captured_and_restored():
    with ai_usage.request_scope("0123456789abcdef"):
        @ai_usage.bind_request
        def work():
            return ai_usage.current_request_id()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(work).result() == "0123456789abcdef"
        assert pool.submit(ai_usage.current_request_id).result() is None


def test_physical_retry_group_and_explicit_fallback(events):
    class APIConnectionError(Exception):
        pass
    count = 0
    def method(**kw):
        nonlocal count
        count += 1
        if count == 1:
            raise APIConnectionError("email-secret@example.invalid")
        return response()
    with ai_usage.fallback_scope(True):
        with ai_provider_call.admit(feature="nutrition", provider="openai",
                                    payload=payload(), gate=False) as call:
            call.create(method)
    assert len(events) == 2
    assert [e["attempt"] for e in events] == [1, 2]
    assert events[0]["admission_id"] == events[1]["admission_id"]
    assert all(e["fallback"] is True for e in events)
    assert len(events[0]["admission_id"]) == 32
    assert "email-secret" not in json.dumps(events)


@pytest.mark.parametrize("family,retries", [("bedrock", 2), ("openai", 3)])
def test_exact_event_count_matches_every_physical_error_attempt(events, family, retries):
    class StatusError(Exception):
        status_code = 503
    calls = []
    def method(**kwargs):
        calls.append(kwargs)
        raise StatusError("exception-secret sk-secret user@example.invalid")
    with pytest.raises(StatusError):
        with ai_provider_call.admit(feature="nutrition", provider=family,
                                    payload=payload(), gate=False) as call:
            call.create(method, timeout=7)
    assert len(calls) == len(events) == retries
    assert [e["attempt"] for e in events] == list(range(1, retries + 1))
    assert all(e["outcome"] == "provider_error" and e["output_tokens"] is None for e in events)
    assert "exception-secret" not in json.dumps(events)
    assert "sk-secret" not in json.dumps(events)
    assert "user@example.invalid" not in json.dumps(events)


@pytest.mark.parametrize("read_final,expected", [(True, "success"), (False, "client_disconnect")])
def test_stream_has_one_event_with_exact_outcome(events, read_final, expected):
    class Stream:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def get_final_message(self):
            return response()
    with ai_provider_call.admit(feature="coach", provider="bedrock-stream",
                                payload=payload(), gate=False) as call:
        with call.stream(lambda **kw: Stream(), timeout=7) as stream:
            if read_final:
                assert stream.get_final_message().content == "response-secret"
    assert len(events) == 1
    assert events[0]["outcome"] == expected
    assert events[0]["output_tokens"] == (20 if read_final else None)


@pytest.mark.parametrize("failure", [False, True])
def test_sink_failure_preserves_provider_exception_and_retry_charges(monkeypatch, failure):
    class StatusError(Exception):
        status_code = 503
    seen = []
    def method(**kwargs):
        seen.append(kwargs)
        raise StatusError("sensitive-exception")
    if failure:
        monkeypatch.setattr(ai_usage._logger, "info", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("sink")))
    with pytest.raises(StatusError, match="sensitive-exception"):
        with ai_provider_call.admit(feature="nutrition", provider="bedrock",
                                    payload=payload(), gate=False) as call:
            call.create(method, timeout=7)
    assert len(seen) == 2
    assert all(p == {**payload(), "timeout": 7} for p in seen)
    assert sum(v for k, v in ai_spend_guard._local_counts.items() if ":global:" in k and ":heavy:d:" in k) == 2


def test_safe_job_identity_and_arbitrary_job_name_exclusion(monkeypatch, events):
    import rq
    monkeypatch.setattr(rq, "get_current_job", lambda: SimpleNamespace(id="12345678-1234-4234-8234-123456789abc"))
    ai_usage.emit(feature="summary", provider="openai", model="gpt-4o-mini", outcome="success")
    assert events[-1]["job_id"] == "12345678-1234-4234-8234-123456789abc"
    monkeypatch.setattr(rq, "get_current_job", lambda: SimpleNamespace(id="user@example.invalid"))
    ai_usage.emit(feature="summary", provider="openai", model="gpt-4o-mini", outcome="success")
    assert events[-1]["job_id"] is None
    assert "user@example.invalid" not in json.dumps(events)


@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": 100, "output_tokens": 20, "cache_write_tokens": 30, "cache_read_tokens": 40}, .000724),
    ({"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0}, 0),
    ({"input_tokens": None, "output_tokens": None}, None),
])
def test_cost_preserves_disjoint_cache_quantities_and_unknowns(usage, expected):
    cost = ai_usage.estimated_cost_usd("bedrock", "claude-sonnet-4-5", usage)
    if expected is None:
        assert cost is None
    else:
        assert cost == pytest.approx(expected, abs=.0000005)


def test_new_admission_identity_cannot_export_content(events):
    ai_usage.emit(feature="coach", provider="bedrock", model="unknown", outcome="success",
                  admission_id="user@example.invalid")
    assert events[-1]["admission_id"] is None


@pytest.mark.parametrize("reason", ["input", "guard"])
def test_local_refusal_is_one_event_and_zero_physical_attempts(monkeypatch, events, reason):
    sent = []
    if reason == "input":
        monkeypatch.setitem(ai_input_budget.INPUT_BUDGETS, "nutrition", 1)
        exception = ai_provider_call.AIInputBudgetExceeded
    else:
        monkeypatch.setattr(ai_spend_guard, "LIMITS", {("global", "heavy", "d"): 1})
        ai_spend_guard.charge("bedrock", feature="nutrition")
        exception = ai_spend_guard.AISpendLimitExceeded
    with pytest.raises(exception):
        with ai_provider_call.admit(feature="nutrition", provider="bedrock", payload=payload(), gate=False) as call:
            call.create(lambda **kw: sent.append(kw))
    assert sent == []
    assert len(events) == 1
    assert events[0]["outcome"] == ("input_budget_rejected" if reason == "input" else "guard_rejected")
    assert "estimated_cost_usd" not in events[0]


def test_health_probe_is_one_billable_event_without_spend_charge_or_subject(monkeypatch, events):
    from app import extensions
    from app.services import bedrock_health
    sent = []
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self): return response()
    def stream(**kwargs):
        sent.append(kwargs)
        return Stream()
    monkeypatch.setattr(extensions, "bedrock_client", SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    assert bedrock_health._probe_once() == (True, None)
    assert len(sent) == len(events) == 1
    assert sent[0]["max_tokens"] == 1 and sent[0]["timeout"] == 5
    assert events[0]["feature"] == "health_probe" and events[0]["subject_id"] is None
    assert ai_spend_guard._local_counts == {}


def test_second_tool_round_is_a_distinct_admission_of_the_same_action(events):
    with ai_usage.request_scope("0123456789abcdef"), ai_spend_guard.subject_scope(7):
        for round_number in (1, 2):
            with ai_provider_call.admit(feature="coach", provider="bedrock", payload=payload(),
                                        gate=False, tool_round=round_number) as call:
                call.create(lambda **kw: response())
    assert len(events) == 2
    assert [e["tool_round"] for e in events] == [1, 2]
    assert len({e["admission_id"] for e in events}) == 2
    assert {e["request_id"] for e in events} == {"0123456789abcdef"}
    assert {e["subject_id"] for e in events} == {7}


def test_real_nutrition_executor_preserves_request_and_subject(app, monkeypatch, events):
    from app.services import ai_nutrition
    monkeypatch.setattr(ai_nutrition, "_LLM_MACRO_BATCH_SIZE", 1)
    def batch(items, category_map):
        with ai_provider_call.admit(feature="nutrition", provider="bedrock", payload=payload(), gate=False) as call:
            call.create(lambda **kw: response())
        return {items[0]: {"calories": 1}}
    monkeypatch.setattr(ai_nutrition, "_estimate_macros_llm_batch", batch)
    with ai_usage.request_scope("0123456789abcdef"), ai_spend_guard.subject_scope(7):
        result = ai_nutrition._estimate_macros_llm(["a", "b", "c"])
    assert set(result) == {"a", "b", "c"}
    assert len(events) == 3
    assert {e["request_id"] for e in events} == {"0123456789abcdef"}
    assert {e["subject_id"] for e in events} == {7}


def test_worker_uses_database_owner_and_existing_job_id(app, make_user, monkeypatch, events):
    import rq
    from app.extensions import db
    from app.models import CoachConversation
    from app.jobs import tasks
    from app.services import memory_manager
    user = make_user("summaryowner")
    conversation = CoachConversation(user_id=user.id)
    db.session.add(conversation)
    db.session.commit()
    def summarize(conv):
        with ai_provider_call.admit(feature="summary", provider="openai", payload=payload(), gate=False) as call:
            call.create(lambda **kw: SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20)))
        return True
    monkeypatch.setattr(memory_manager, "maybe_summarize", summarize)
    monkeypatch.setattr(rq, "get_current_job", lambda: SimpleNamespace(id="12345678-1234-4234-8234-123456789abc"))
    assert tasks.summarize_conversation(conversation.id) is True
    assert len(events) == 1
    assert events[0]["subject_id"] == user.id
    assert events[0]["request_id"] is None
    assert events[0]["job_id"] == "12345678-1234-4234-8234-123456789abc"


def test_openai_cache_split_is_not_double_counted():
    usage = ai_usage.usage_from_response("openai", SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=1000, completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=400))))
    assert usage == {"input_tokens": 600, "output_tokens": 20, "cache_write_tokens": 0, "cache_read_tokens": 400}
    assert ai_usage.estimated_cost_usd("openai", "gpt-4o-mini", usage) == pytest.approx(.000132)


def test_fallback_flag_does_not_mark_unrelated_tool_capabilities(events):
    with ai_usage.fallback_scope(True, feature="coach"):
        for family, feature in [("openai", "coach"), ("bedrock", "vision"), ("openai", "nutrition")]:
            ai_usage.emit(feature=feature, provider=family, model="gpt-4o-mini", outcome="success")
    assert [e["fallback"] for e in events] == [True, False, False]


@pytest.mark.parametrize("sentinel", ["prompt-sensitive-value", "response-sensitive-value",
    "private.person@example.invalid", "sk-private-access-refresh-token", "tool-user-sensitive-content",
    "exception-user-secret-message"])
def test_sensitive_content_never_reaches_attempt_event(events, sentinel):
    data = payload()
    data["messages"] = [{"role": "user", "content": sentinel},
                        {"role": "assistant", "content": [{"type": "tool_use", "id": "tool1",
                         "name": "t", "input": {"user_content": sentinel}}]}]
    answer = response()
    answer.content = sentinel
    with ai_provider_call.admit(feature="nutrition", provider="bedrock", payload=data) as call:
        assert call.create(lambda **kwargs: answer) is answer
    assert len(events) == 1
    assert sentinel not in json.dumps(events)


def test_admission_correlation_generation_failure_cannot_block_provider(monkeypatch, events):
    monkeypatch.setattr(ai_provider_call.uuid, "uuid4", lambda: (_ for _ in ()).throw(RuntimeError("rng-failure")))
    answer = response()
    with ai_provider_call.admit(feature="nutrition", provider="bedrock", payload=payload()) as call:
        assert call.create(lambda **kwargs: answer) is answer
    assert len(events) == 1
    assert events[0]["admission_id"] is None
