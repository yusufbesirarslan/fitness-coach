"""AI spend guard (Phase 2 P2-C) — finite provider-call ceilings.

What is pinned here:
  * admission/rejection per scope (account, global) and class (heavy, light);
  * race safety of the Redis counters: N concurrent callers never admit more
    than the limit, including across two ceilings at once;
  * Redis outage -> bounded process-local enforcement (never fail-open);
  * window rollover and key expiry;
  * THE POINT: a refused call never reaches the provider — in the gate, the
    heavy/light chat helpers, the coach tool loop (blocking and streaming),
    menu OCR and fan-out executors — and a refusal never turns into an OpenAI
    fallback call or a recovery retry;
  * the routes answer with their existing localized error state, never a 500,
    and the free-tier quota is refunded.

    python -m pytest tests/test_ai_spend_guard.py -v
"""
import ast
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import ai, ai_coach, ai_gate, ai_recovery, ai_spend_guard, ai_stream
from app.services.ai_spend_guard import AISpendLimitExceeded


# ── Fakes ───────────────────────────────────────────────────────────────────

class FakeRedis:
    """Enough of redis-py for the guard: MULTI/EXEC pipelines of INCR, DECR
    and EXPIRE NX, executed atomically (one lock = Redis' single thread)."""

    def __init__(self):
        self.data = {}
        self.ttl = {}
        self.fail = False
        self._lock = threading.Lock()

    def pipeline(self, transaction=True):
        assert transaction is True, "the guard must use MULTI/EXEC"
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def incr(self, key):
        self._ops.append(("incr", key))

    def decr(self, key):
        self._ops.append(("decr", key))

    def expire(self, key, ttl, nx=False):
        self._ops.append(("expire", key, ttl, nx))

    def execute(self):
        if self._redis.fail:
            raise ConnectionError("redis down")
        out = []
        with self._redis._lock:
            for op in self._ops:
                if op[0] == "incr":
                    self._redis.data[op[1]] = self._redis.data.get(op[1], 0) + 1
                    out.append(self._redis.data[op[1]])
                elif op[0] == "decr":
                    self._redis.data[op[1]] = self._redis.data.get(op[1], 0) - 1
                    out.append(self._redis.data[op[1]])
                else:
                    _, key, ttl, nx = op
                    if not (nx and key in self._redis.ttl):
                        self._redis.ttl[key] = ttl
                    out.append(True)
        return out


class CountingMessages:
    """bedrock_client.messages stand-in that records every provider call."""

    def __init__(self, responses=None):
        self.calls = 0
        self._responses = list(responses or [])

    def create(self, **kwargs):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="ok")])


class CountingOpenAI:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        message = SimpleNamespace(content="openai-ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


@pytest.fixture(autouse=True)
def _fresh_guard(monkeypatch):
    """Every test starts with empty counters, local mode and known limits."""
    ai_spend_guard._reset_local_for_tests()
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    monkeypatch.setattr(ai_spend_guard, "LIMITS", {
        ("user", "heavy", "d"): 3,
        ("user", "light", "d"): 3,
        ("global", "heavy", "h"): 100,
        ("global", "heavy", "d"): 100,
        ("global", "light", "d"): 100,
    })
    yield
    ai_spend_guard._reset_local_for_tests()


def _limits(monkeypatch, **overrides):
    names = {
        "user_heavy": ("user", "heavy", "d"),
        "user_light": ("user", "light", "d"),
        "global_heavy_hour": ("global", "heavy", "h"),
        "global_heavy_day": ("global", "heavy", "d"),
        "global_light": ("global", "light", "d"),
    }
    limits = dict(ai_spend_guard.LIMITS)
    for name, value in overrides.items():
        limits[names[name]] = value
    monkeypatch.setattr(ai_spend_guard, "LIMITS", limits)


def _use_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: fake)
    return fake


def _charge_as(subject, provider="bedrock"):
    with ai_spend_guard.subject_scope(subject):
        ai_spend_guard.charge(provider)


# ── Admission semantics ─────────────────────────────────────────────────────

def test_account_below_ceiling_is_admitted_and_at_ceiling_is_refused():
    for _ in range(3):
        _charge_as(7)
    with pytest.raises(AISpendLimitExceeded) as exc:
        _charge_as(7)
    assert exc.value.scope == "user"
    assert exc.value.provider_class == "heavy"


def test_one_account_hitting_its_ceiling_does_not_block_another():
    for _ in range(3):
        _charge_as(7)
    _charge_as(8)  # a different account still has its own allowance


def test_heavy_and_light_are_counted_separately():
    for _ in range(3):
        _charge_as(7, "bedrock")
    _charge_as(7, "openai")  # light allowance untouched
    with pytest.raises(AISpendLimitExceeded):
        _charge_as(7, "bedrock-stream")  # streaming is heavy too


def test_global_ceiling_bounds_many_accounts(monkeypatch):
    # Multi-account abuse: every account is under its own ceiling, the sum is not.
    _limits(monkeypatch, user_heavy=1000, global_heavy_hour=5)
    for subject in range(5):
        _charge_as(subject)
    with pytest.raises(AISpendLimitExceeded) as exc:
        _charge_as(99)
    assert exc.value.scope == "global"


def test_unattributed_calls_still_spend_the_global_budget(monkeypatch):
    _limits(monkeypatch, global_heavy_hour=2)
    ai_spend_guard.charge("bedrock")
    ai_spend_guard.charge("bedrock")
    with pytest.raises(AISpendLimitExceeded):
        ai_spend_guard.charge("bedrock")


def test_zero_disables_only_that_ceiling(monkeypatch):
    _limits(monkeypatch, user_heavy=0)
    for _ in range(10):
        _charge_as(7)


def test_disabled_guard_admits_everything(monkeypatch):
    monkeypatch.setattr(ai_spend_guard, "ENABLED", False)
    for _ in range(10):
        _charge_as(7)


def test_a_refused_call_does_not_consume_budget(monkeypatch):
    fake = _use_redis(monkeypatch)
    _limits(monkeypatch, user_heavy=10, global_heavy_hour=2)
    _charge_as(7)
    _charge_as(7)
    for _ in range(5):
        with pytest.raises(AISpendLimitExceeded):
            _charge_as(7)
    user_key = next(k for k in fake.data if ":user:7:heavy:d:" in k)
    assert fake.data[user_key] == 2  # refusals were compensated


def test_refusal_message_reveals_no_threshold_or_scope():
    for _ in range(3):
        _charge_as(7)
    with pytest.raises(AISpendLimitExceeded) as exc:
        _charge_as(7)
    message = str(exc.value)
    assert message == "AI capacity temporarily unavailable"
    assert not any(ch.isdigit() for ch in message)


def test_refusal_is_a_capacity_limit_so_existing_handlers_apply():
    assert issubclass(AISpendLimitExceeded, ai_gate.BlockingConcurrencyLimit)
    assert not issubclass(AISpendLimitExceeded, ai_recovery.TransientAIError)


def test_refusal_emits_log_and_bounded_metric(monkeypatch, caplog):
    recorded = []
    from app.services import runtime_metrics
    monkeypatch.setattr(runtime_metrics, "increment",
                        lambda name, dimensions=None, value=1: recorded.append((name, dimensions)))
    for _ in range(3):
        _charge_as(7)
    with caplog.at_level("WARNING"), pytest.raises(AISpendLimitExceeded):
        _charge_as(7)
    assert recorded == [("AiSpendGuardRejections", {"Scope": "user", "Class": "heavy"})]
    assert "provider call refused scope=user class=heavy" in caplog.text


# ── Redis atomicity, expiry, outage ─────────────────────────────────────────

def _race(n, fn):
    barrier = threading.Barrier(n)
    admitted = []
    refused = []

    def one(i):
        barrier.wait()
        try:
            fn(i)
            admitted.append(i)
        except AISpendLimitExceeded:
            refused.append(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(one, range(n)))
    return admitted, refused


def test_concurrent_callers_never_exceed_a_ceiling(monkeypatch):
    fake = _use_redis(monkeypatch)
    _limits(monkeypatch, user_heavy=0, global_heavy_hour=10, global_heavy_day=1000)
    admitted, refused = _race(64, lambda i: ai_spend_guard.charge("bedrock"))
    assert len(admitted) == 10
    assert len(refused) == 54
    hour_key = next(k for k in fake.data if ":global:heavy:h:" in k)
    assert fake.data[hour_key] == 10  # every refusal compensated


def test_concurrent_callers_never_exceed_either_of_two_ceilings(monkeypatch):
    _use_redis(monkeypatch)
    _limits(monkeypatch, user_heavy=5, global_heavy_hour=8, global_heavy_day=1000)
    per_user = {}
    lock = threading.Lock()

    def call(i):
        subject = i % 2
        _charge_as(subject)
        with lock:
            per_user[subject] = per_user.get(subject, 0) + 1

    admitted, _ = _race(40, call)
    assert len(admitted) <= 8
    assert all(count <= 5 for count in per_user.values())


def test_keys_expire_after_their_window(monkeypatch):
    fake = _use_redis(monkeypatch)
    _charge_as(7)
    assert fake.ttl, "every counter must carry a TTL"
    for key, ttl in fake.ttl.items():
        window = key.split(":")[-2]
        assert ttl == ai_spend_guard._WINDOW_SECONDS[window] + ai_spend_guard._KEY_TTL_GRACE_SECONDS


def test_a_new_window_restores_the_allowance(monkeypatch):
    _use_redis(monkeypatch)
    now = [1_000_000.0]
    monkeypatch.setattr(ai_spend_guard.time, "time", lambda: now[0])
    for _ in range(3):
        _charge_as(7)
    with pytest.raises(AISpendLimitExceeded):
        _charge_as(7)
    now[0] += 86400
    _charge_as(7)


def test_redis_outage_degrades_to_a_local_bound_not_fail_open(monkeypatch, caplog):
    fake = _use_redis(monkeypatch)
    fake.fail = True
    monkeypatch.setattr(ai_spend_guard, "_redis_warned_at", 0.0)
    with caplog.at_level("WARNING"):
        for _ in range(3):
            _charge_as(7)
        with pytest.raises(AISpendLimitExceeded):
            _charge_as(7)
    assert "Redis unavailable" in caplog.text


# ── Subject attribution across threads ──────────────────────────────────────

def test_bind_subject_carries_the_caller_into_an_executor_thread():
    seen = []

    with ai_spend_guard.subject_scope(5):
        job = ai_spend_guard.bind_subject(lambda: seen.append(ai_spend_guard.current_subject()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(job).result()
    assert seen == [5]


def test_request_context_attributes_to_the_logged_in_user(app, auth_user, client):
    with client:
        client.get("/")
        assert ai_spend_guard.current_subject() == auth_user.id


def test_mobile_user_takes_precedence_in_request_context(app):
    from flask import g
    with app.test_request_context("/api/v1/today"):
        g.mobile_user = SimpleNamespace(id=31)
        assert ai_spend_guard.current_subject() == 31


# ── The gate: refusal happens before the provider, permit returned ──────────

def test_gate_refusal_never_runs_the_body_and_returns_the_permit():
    for _ in range(3):
        _charge_as(7)
    ran = []
    with ai_spend_guard.subject_scope(7):
        with pytest.raises(AISpendLimitExceeded):
            with ai_gate.model_concurrency_slot("bedrock"):
                ran.append(True)
    assert ran == []
    assert ai_gate.capacity_snapshot()["model_active"] == 0
    held = [ai_gate._model_slots.acquire(blocking=False)
            for _ in range(ai_gate.AI_MODEL_MAX_CONCURRENCY)]
    try:
        assert all(held), "a refused call leaked a model permit"
    finally:
        for ok in held:
            if ok:
                ai_gate._model_slots.release()


def test_capacity_refusal_does_not_consume_spend_budget(monkeypatch):
    fake = _use_redis(monkeypatch)
    held = [ai_gate._model_slots.acquire(blocking=False)
            for _ in range(ai_gate.AI_MODEL_MAX_CONCURRENCY)]
    try:
        with pytest.raises(ai_gate.BlockingConcurrencyLimit) as exc:
            with ai_gate.model_concurrency_slot("bedrock", wait_seconds=0):
                pass
        assert not isinstance(exc.value, AISpendLimitExceeded)
    finally:
        for ok in held:
            if ok:
                ai_gate._model_slots.release()
    assert fake.data == {}


def test_charge_is_placed_before_the_yield_in_the_gate():
    """Structural guard: moving charge() after the provider call would still
    change the RESPONSE but no longer prevent the SPEND."""
    tree = ast.parse(inspect.getsource(ai_gate.model_concurrency_slot))
    charge_line = yield_line = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "charge"):
            charge_line = node.lineno
        if isinstance(node, ast.Yield):
            yield_line = node.lineno
    assert charge_line is not None and yield_line is not None
    assert charge_line < yield_line


def test_every_provider_sdk_call_site_is_guarded():
    """A provider call is only bounded if it passes model_concurrency_slot or
    charges directly. The deep-health Bedrock probe is the one deliberate
    exception (1 output token, cached, and gating it could fail a deploy)."""
    root = Path(ai_spend_guard.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if not any(sdk in text for sdk in (".messages.create(", ".messages.stream(",
                                            ".chat.completions.create(")):
            continue
        rel = path.relative_to(root.parent).as_posix()
        if rel == "app/services/bedrock_health.py":
            continue
        if "model_concurrency_slot(" not in text and "ai_spend_guard.charge(" not in text:
            offenders.append(rel)
    assert offenders == []


# ── No provider call after refusal: chat helpers ────────────────────────────

@pytest.fixture
def providers(monkeypatch):
    bedrock = CountingMessages()
    openai_fake = CountingOpenAI()
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=bedrock))
    monkeypatch.setattr(ai, "openai_client", openai_fake)
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_recovery, "recall_last_good", lambda key: None)
    monkeypatch.setattr(ai_recovery, "remember_last_good", lambda key, value: None)
    return SimpleNamespace(bedrock=bedrock, openai=openai_fake)


def test_heavy_chat_stops_at_the_ceiling_without_openai_fallback(app, providers):
    with ai_spend_guard.subject_scope(7):
        for _ in range(3):
            assert ai._heavy_chat([{"role": "user", "content": "hi"}]) == "ok"
        with pytest.raises(AISpendLimitExceeded):
            ai._heavy_chat([{"role": "user", "content": "hi"}])
    assert providers.bedrock.calls == 3
    assert providers.openai.calls == 0


def test_light_chat_stops_at_the_ceiling(app, providers):
    with ai_spend_guard.subject_scope(7):
        for _ in range(3):
            ai._openai_chat([{"role": "user", "content": "hi"}])
        with pytest.raises(AISpendLimitExceeded):
            ai._openai_chat([{"role": "user", "content": "hi"}])
    assert providers.openai.calls == 3


def test_vision_helper_stops_at_the_ceiling(app, providers):
    with ai_spend_guard.subject_scope(7):
        for _ in range(3):
            ai._bedrock_validate_image(b"img", "image/jpeg", "p")
        with pytest.raises(AISpendLimitExceeded):
            ai._bedrock_compare_images(b"a", "image/jpeg", b"b", "image/jpeg", "p")
    assert providers.bedrock.calls == 3


def test_recovery_never_retries_a_refusal():
    attempts = []

    def refused():
        attempts.append(1)
        raise AISpendLimitExceeded("user", "heavy")

    with pytest.raises(AISpendLimitExceeded):
        ai_recovery.call_with_recovery(refused, feature="test", attempts=5)
    assert attempts == [1]


def test_premium_normal_usage_is_unaffected_by_default_limits(app, providers, monkeypatch):
    monkeypatch.setattr(ai_spend_guard, "LIMITS", {
        ("user", "heavy", "d"): 200,
        ("user", "light", "d"): 400,
        ("global", "heavy", "h"): 300,
        ("global", "heavy", "d"): 1500,
        ("global", "light", "d"): 5000,
    })
    # A heavy real day: 30 coach turns (the 30-day per-account maximum observed
    # in production) at the worst-case 5 provider rounds each.
    with ai_spend_guard.subject_scope(7):
        for _ in range(150):
            ai._heavy_chat([{"role": "user", "content": "hi"}])
    assert providers.bedrock.calls == 150


# ── Coach tool loop (blocking) ──────────────────────────────────────────────

def _tool_use_response():
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", name="query_fitx_metrics",
                                 id="t1", input={"metric_type": "nutrition"})])


@pytest.fixture
def coach_providers(monkeypatch):
    bedrock = CountingMessages()
    openai_fake = CountingOpenAI()
    monkeypatch.setattr(ai_coach, "bedrock_client", SimpleNamespace(messages=bedrock))
    monkeypatch.setattr(ai_coach, "openai_client", openai_fake)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    return SimpleNamespace(bedrock=bedrock, openai=openai_fake)


def test_coach_turn_at_ceiling_makes_no_provider_call_and_no_openai_fallback(
        app, coach_providers, make_user):
    user = make_user("spender")
    for _ in range(3):
        _charge_as(user.id)
    with app.test_request_context("/ask", method="POST"):
        with ai_spend_guard.subject_scope(user.id):
            reply = ai_coach._run_coach_conversation(user.id, "soru", "", client_history=[])
    assert coach_providers.bedrock.calls == 0
    assert coach_providers.openai.calls == 0
    assert reply == ai_coach._COACH_FALLBACKS["tr"]["error"]


def test_tool_loop_rounds_each_spend_and_the_ceiling_stops_mid_turn(
        app, coach_providers, make_user, monkeypatch):
    user = make_user("looper")
    coach_providers.bedrock._responses = [_tool_use_response()] * 5
    dispatched = []
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool",
                        lambda uid, name, args: dispatched.append(name) or "{}")
    with app.test_request_context("/ask", method="POST"):
        with ai_spend_guard.subject_scope(user.id):
            reply = ai_coach._run_coach_conversation(user.id, "soru", "", client_history=[])
    # user ceiling is 3: three rounds ran, the fourth was refused before the call
    assert coach_providers.bedrock.calls == 3
    assert len(dispatched) == 3
    assert coach_providers.openai.calls == 0
    assert reply in ai_coach._COACH_FALLBACKS["tr"].values()


# ── Coach tool loop (streaming) ─────────────────────────────────────────────

class _FakeStream:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield self._text

    def get_final_message(self):
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=self._text)],
                               usage=None)


class _CountingStreamClient:
    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls += 1
        return _FakeStream("cevap")


@pytest.fixture
def stream_providers(monkeypatch):
    client = _CountingStreamClient()
    monkeypatch.setattr(ai_coach, "bedrock_client", client)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())

    def no_openai(*args, **kwargs):
        raise AssertionError("a spend refusal must not fall back to OpenAI")
        yield  # pragma: no cover

    monkeypatch.setattr(ai_stream, "_stream_openai_fallback", no_openai)
    return client


def test_streaming_turn_is_attributed_to_its_owner_in_the_producer_thread(
        app, stream_providers, monkeypatch):
    fake = _use_redis(monkeypatch)
    events = list(ai_stream.stream_coach_answer(42, "soru", "", []))
    assert events[-1]["type"] == "done"
    assert stream_providers.calls == 1
    assert any(":user:42:heavy:d:" in key and value == 1 for key, value in fake.data.items())


def test_streaming_turn_at_ceiling_yields_error_frame_without_provider_call(
        app, stream_providers):
    for _ in range(3):
        _charge_as(42)
    events = list(ai_stream.stream_coach_answer(42, "soru", "", []))
    assert stream_providers.calls == 0
    assert events[-1]["type"] == "error"
    assert events[-1]["key"] == "coach.reply_failed"
    assert events[-1]["work_performed"] is False


# ── Menu OCR and fan-out ────────────────────────────────────────────────────

def test_menu_ocr_refusal_returns_unreadable_without_a_provider_call(app, monkeypatch):
    from app.services import menu_ocr
    fake = CountingOpenAI()
    monkeypatch.setattr(menu_ocr, "openai_client", fake)
    for _ in range(3):
        _charge_as(7, "openai")
    with ai_spend_guard.subject_scope(7):
        assert menu_ocr._extract_text_from_image(b"tiny", "image/png") == ""
    assert fake.calls == 0


def test_macro_fan_out_batches_spend_the_requesting_accounts_budget(app, monkeypatch):
    from app.services import ai_nutrition
    fake = _use_redis(monkeypatch)
    monkeypatch.setattr(ai_nutrition, "_LLM_MACRO_BATCH_SIZE", 1)

    def batch(items, category_map=None, grams_hint=None):
        ai_spend_guard.charge("bedrock")
        return {}

    monkeypatch.setattr(ai_nutrition, "_estimate_macros_llm_batch", batch)
    with ai_spend_guard.subject_scope(9):
        ai_nutrition._estimate_macros_llm(["a", "b"])
    assert any(":user:9:heavy:d:" in key and value == 2 for key, value in fake.data.items())


# ── Routes: localized failure contract, no 500, quota refunded ──────────────

def test_ask_stream_at_ceiling_returns_localized_error_frame_and_refunds_quota(
        app, auth_user, client, stream_providers):
    from app.i18n import t
    app.config["AI_CHAT_QUOTA_ENABLED"] = True
    for _ in range(3):
        _charge_as(auth_user.id)
    resp = client.post("/ask/stream", json={"question": "merhaba"})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "event: error" in body
    with app.test_request_context():
        expected = t("coach.reply_failed")
    frames = [json.loads(line[len("data: "):]) for line in body.splitlines()
              if line.startswith("data: ")]
    assert frames[-1] == {"message": expected}
    assert stream_providers.calls == 0
    from app.extensions import db
    from app.models import User
    db.session.expire_all()
    quota = (db.session.get(User, auth_user.id).user_metadata or {}).get("ai_plan_quota", {})
    assert quota.get("chat", 0) == 0


def test_ask_at_ceiling_returns_the_existing_soft_error(app, auth_user, client, coach_providers):
    for _ in range(3):
        _charge_as(auth_user.id)
    resp = client.post("/ask", json={"question": "merhaba"})
    assert resp.status_code == 200
    assert resp.get_json()["answer"] == ai_coach._COACH_FALLBACKS["tr"]["error"]
    assert coach_providers.bedrock.calls == 0
    assert coach_providers.openai.calls == 0
