"""Coach turn admission in the AI spend guard (2026-11-24 real-provider E2E P2).

Real Sonnet 5.5 Coach turns are tool loops: one user message = 1-3 physical
heavy attempts (R1 tool_use -> R2 -> Nutrition estimator). With per-attempt
admission only, a turn was admitted at R1 and refused at R2/R3 after the
earlier rounds were already paid for (503, no reply). The fix: the turn's FIRST
attempt is admitted only while every ceiling has room for COACH_TURN_RESERVE
attempts. Pinned here:

  * every attempt is still charged exactly once to every window — the minute,
    hour, day and global ceilings are never exceeded (no uncharged call);
  * a turn of <= reserve attempts is never refused mid-turn (zero paid-but-
    unanswered attempts), and is refused before any provider call instead;
  * only the opener uses the reserve: retries and later rounds are ordinary
    per-attempt charges; the reserve never leaks to another call or thread;
  * Redis outage still fails closed; concurrent openers never over-admit;
  * the blocking and streaming Coach loops arm it on tool round 1 only;
  * the known trade-off (cheap one-attempt turns near a ceiling) is pinned;
  * RESERVE=1 restores per-attempt behaviour (non-vacuity: the old failure).

    python -m pytest tests/test_ai_spend_coach_turn_admission.py -v
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import ai_coach, ai_spend_guard, ai_stream
from app.services.ai_spend_guard import AISpendLimitExceeded
from tests.test_ai_spend_guard import (  # noqa: F401 -- shared fakes + fixtures
    FakeRedis,
    _tool_use_response,
    coach_providers,
    stream_providers,
)

MINUTE = ("user", "heavy", "m")


@pytest.fixture(autouse=True)
def _guard(monkeypatch):
    ai_spend_guard._reset_local_for_tests()
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    monkeypatch.setattr(ai_spend_guard, "COACH_TURN_RESERVE", 3)
    monkeypatch.setattr(ai_spend_guard.time, "time", lambda: 1_000_020.0)
    monkeypatch.setattr(ai_spend_guard, "LIMITS", {
        ("user", "heavy", "d"): 1000,
        ("user", "heavy", "h"): 1000,
        MINUTE: 5,
        ("user", "light", "d"): 1000,
        ("global", "heavy", "h"): 100000,
        ("global", "heavy", "d"): 100000,
        ("global", "light", "d"): 100000,
    })
    yield
    ai_spend_guard._reset_local_for_tests()


@pytest.fixture(params=["local", "redis"])
def backend(request, monkeypatch):
    if request.param == "redis":
        fake = FakeRedis()
        monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: fake)
        return fake
    return None


def _set(monkeypatch, key, value):
    limits = dict(ai_spend_guard.LIMITS)
    limits[key] = value
    monkeypatch.setattr(ai_spend_guard, "LIMITS", limits)


def _charge(subject, opener=False):
    with ai_spend_guard.subject_scope(subject), ai_spend_guard.coach_turn_opener(opener):
        ai_spend_guard.charge("bedrock")


def _minute_count(subject, backend):
    store = backend.data if backend is not None else ai_spend_guard._local_counts
    return sum(v for k, v in store.items() if f":user:{subject}:heavy:m:" in k)


def _turn(subject, attempts):
    """One Coach turn of `attempts` physical heavy calls, as the loop charges them."""
    for i in range(attempts):
        try:
            _charge(subject, opener=(i == 0))
        except AISpendLimitExceeded:
            return ("refused_before_spend" if i == 0 else "refused_mid_turn"), i
    return "ok", attempts


def _minute_of_turns(subject, attempts, n=6):
    outcomes = [_turn(subject, attempts) for _ in range(n)]
    return ([o for o, _ in outcomes],
            sum(paid for o, paid in outcomes if o == "refused_mid_turn"))


# ── Admission semantics ─────────────────────────────────────────────────────

def test_opener_needs_room_for_the_whole_turn_and_refusal_costs_nothing(backend):
    for _ in range(3):
        _charge(1)
    with pytest.raises(AISpendLimitExceeded):
        _charge(1, opener=True)          # 3 used + 3 needed > 5
    assert _minute_count(1, backend) == 3  # compensated: refusal spends nothing
    _charge(1)                             # a non-opener attempt still fits (4 <= 5)
    _charge(1)
    with pytest.raises(AISpendLimitExceeded):
        _charge(1)                         # the ceiling itself is unchanged
    assert _minute_count(1, backend) == 5


def test_empty_window_always_admits_a_turn_even_if_reserve_exceeds_ceiling(monkeypatch, backend):
    _set(monkeypatch, MINUTE, 2)
    _charge(1, opener=True)                # max(1, 2 - 2) = 1 -> admitted on empty
    _charge(1)
    with pytest.raises(AISpendLimitExceeded):
        _charge(1)
    assert _minute_count(1, backend) == 2


@pytest.mark.parametrize("ceiling", [5, 8])
@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_turns_within_reserve_are_never_refused_mid_turn(monkeypatch, backend, ceiling, attempts):
    _set(monkeypatch, MINUTE, ceiling)
    outcomes, wasted = _minute_of_turns(1, attempts)
    assert "refused_mid_turn" not in outcomes
    assert wasted == 0
    assert _minute_count(1, backend) <= ceiling
    assert outcomes[0] == "ok"


def test_reserve_one_restores_per_attempt_admission_and_its_mid_turn_failure(monkeypatch, backend):
    """Non-vacuity: the observed E2E failure (8/min, 3-attempt Nutrition turns)."""
    monkeypatch.setattr(ai_spend_guard, "COACH_TURN_RESERVE", 1)
    _set(monkeypatch, MINUTE, 8)
    outcomes, wasted = _minute_of_turns(1, 3)
    assert outcomes[:3] == ["ok", "ok", "refused_mid_turn"]
    assert wasted == 2                     # R1+R2 paid, turn answered 503


def test_reserve_three_fixes_the_observed_sequence(monkeypatch, backend):
    _set(monkeypatch, MINUTE, 8)
    outcomes, wasted = _minute_of_turns(1, 3)
    assert outcomes[:3] == ["ok", "ok", "refused_before_spend"]
    assert wasted == 0


def test_known_tradeoff_cheap_single_attempt_turns_near_the_ceiling(monkeypatch, backend):
    """Pinned on purpose: the opener cannot know a turn will need one attempt.
    At 5/min, one-attempt turns get 3 per minute (was 5); the 4th is refused
    before any spend. The minute ceiling itself is unchanged."""
    outcomes, _ = _minute_of_turns(1, 1)
    assert outcomes == ["ok"] * 3 + ["refused_before_spend"] * 3


def test_opener_threshold_applies_to_every_window_including_global(monkeypatch, backend):
    # 20/h global keeps each account's share at 20 // 5 = 4 (the guard's
    # global-share rule), so the GLOBAL window is the one under test.
    _set(monkeypatch, ("global", "heavy", "h"), 20)
    for subject in range(100, 118):
        _charge(subject)                   # 18 of 20 used by other accounts
    with pytest.raises(AISpendLimitExceeded) as refused:
        _charge(1, opener=True)            # 18 + 3 > 20
    assert refused.value.scope == "global"
    _charge(1)                             # plain attempts still reach the ceiling
    _charge(1)
    with pytest.raises(AISpendLimitExceeded):
        _charge(1)


# ── One-shot: only the opener's own first charge ────────────────────────────

def test_only_the_first_charge_in_the_opener_scope_uses_the_reserve(backend):
    _charge(1)
    _charge(1)
    with ai_spend_guard.subject_scope(1), ai_spend_guard.coach_turn_opener(True):
        ai_spend_guard.charge("bedrock")   # opener: 3 <= 5 - 2
        ai_spend_guard.charge("bedrock")   # its retry: ordinary, 4 <= 5
        ai_spend_guard.charge("bedrock")   # 5 <= 5
    assert _minute_count(1, backend) == 5


def test_reserve_does_not_leak_when_the_gate_refuses_before_charging(backend):
    for _ in range(3):
        _charge(1)

    class Capacity(Exception):
        pass

    with pytest.raises(Capacity):
        with ai_spend_guard.coach_turn_opener(True):
            raise Capacity()               # e.g. model slot refused: no charge ran
    assert not hasattr(ai_spend_guard._tls, "turn_reserve")
    _charge(1)                             # ordinary: 4 <= 5, not opener-limited


def test_a_consumed_reserve_is_never_rearmed_by_a_nested_scope(backend):
    for _ in range(2):
        _charge(1)
    with ai_spend_guard.subject_scope(1), ai_spend_guard.coach_turn_opener(True):
        ai_spend_guard.charge("bedrock")           # opener consumes the reserve (3)
        with ai_spend_guard.coach_turn_opener(False):
            ai_spend_guard.charge("bedrock")       # 4
        ai_spend_guard.charge("bedrock")           # still ordinary after exit: 5
    assert _minute_count(1, backend) == 5


def test_disabled_guard_still_consumes_the_one_shot(monkeypatch):
    monkeypatch.setattr(ai_spend_guard, "ENABLED", False)
    with ai_spend_guard.coach_turn_opener(True):
        ai_spend_guard.charge("bedrock")
        assert not hasattr(ai_spend_guard._tls, "turn_reserve")


def test_reserve_is_per_thread(backend):
    for _ in range(3):
        _charge(2)
    armed, done = threading.Event(), threading.Event()
    errors = []

    def holder():
        with ai_spend_guard.coach_turn_opener(True):
            armed.set()
            done.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    armed.wait(5)
    try:
        _charge(2)                         # other thread's reserve does not apply here
    except AISpendLimitExceeded as exc:
        errors.append(exc)
    finally:
        done.set()
        t.join(5)
    assert errors == []


# ── Concurrency and outage ──────────────────────────────────────────────────

def test_concurrent_openers_across_accounts_never_over_admit(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: fake)
    _set(monkeypatch, ("global", "heavy", "h"), 12)

    def run(subject):
        return _turn(subject, 3)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(run, range(100, 116)))
    paid = sum(n for _, n in results)
    global_count = sum(v for k, v in fake.data.items() if ":global:heavy:h:" in k)
    assert global_count == paid <= 12
    # A turn refused at the opener never paid; only a turn that lost a race
    # between its own opener and a continuation can stop mid-turn.
    assert all(n == 0 for o, n in results if o == "refused_before_spend")


def test_concurrent_turns_of_one_owner_never_exceed_the_minute_ceiling(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: fake)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: _turn(7, 3), range(8)))
    assert _minute_count(7, fake) <= 5


def test_redis_outage_refuses_the_opener_fail_closed(monkeypatch):
    fake = FakeRedis()
    fake.fail = True
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: fake)
    with pytest.raises(AISpendLimitExceeded) as refused:
        _charge(1, opener=True)
    assert refused.value.scope == "backend"


@pytest.mark.parametrize("raw", ["0", "11", "-1", "x", ""])
def test_invalid_reserve_configuration_stops_boot(monkeypatch, raw):
    monkeypatch.setenv("AI_SPEND_COACH_TURN_RESERVE", raw)
    with pytest.raises(ValueError):
        ai_spend_guard._env_turn_reserve("AI_SPEND_COACH_TURN_RESERVE", 3)


def test_reserve_default_is_three(monkeypatch):
    monkeypatch.delenv("AI_SPEND_COACH_TURN_RESERVE", raising=False)
    assert ai_spend_guard._env_turn_reserve("AI_SPEND_COACH_TURN_RESERVE", 3) == 3


# ── Wiring: the Coach loops arm it on tool round 1 only ─────────────────────

def _door(feature, tool_round, gate=True, charge=True):
    from app.services import ai_provider_call
    payload = dict(model="global.anthropic.claude-sonnet-4-5-20250929-v1:0", max_tokens=100,
                   messages=[{"role": "user", "content": "hi"}])
    with ai_spend_guard.subject_scope(1):
        with ai_provider_call.admit(feature=feature, provider="bedrock", payload=payload,
                                    tool_round=tool_round, gate=gate, charge=charge):
            pass


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("gate", [True, False])
def test_provider_door_arms_the_opener_only_for_coach_round_one(app, gate):
    for _ in range(3):
        _charge(1)
    with app.test_request_context("/ask", method="POST"):
        with pytest.raises(AISpendLimitExceeded):
            _door("coach", 1, gate=gate)           # opener: needs 3 free, has 2
        assert _minute_count(1, None) == 3
        _door("coach", 2, gate=gate)               # later round: per attempt (4)
        _door("nutrition_plan", 1, gate=gate)      # not a Coach turn opener (5)
        with pytest.raises(AISpendLimitExceeded):
            _door("coach", 2, gate=gate)           # ceiling unchanged
    assert not hasattr(ai_spend_guard._tls, "turn_reserve")


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_blocking_coach_turn_refused_before_any_provider_call_instead_of_mid_turn(
        app, coach_providers, make_user, monkeypatch):
    user = make_user("turner")
    coach_providers.bedrock._responses = [_tool_use_response(), _tool_use_response()]
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", lambda uid, name, args: "{}")
    for _ in range(3):
        _charge(user.id)
    with app.test_request_context("/ask", method="POST"):
        with ai_spend_guard.subject_scope(user.id):
            reply = ai_coach._run_coach_conversation(user.id, "soru", "", client_history=[])
    assert coach_providers.bedrock.calls == 0       # was 2 paid calls, then refusal
    assert coach_providers.openai.calls == 0
    assert reply == ai_coach._COACH_FALLBACKS["tr"]["error"]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_blocking_three_round_turn_completes_from_an_empty_window(
        app, coach_providers, make_user, monkeypatch):
    user = make_user("finisher")
    coach_providers.bedrock._responses = [_tool_use_response(), _tool_use_response()]
    monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", lambda uid, name, args: "{}")
    with app.test_request_context("/ask", method="POST"):
        with ai_spend_guard.subject_scope(user.id):
            reply = ai_coach._run_coach_conversation(user.id, "soru", "", client_history=[])
    assert coach_providers.bedrock.calls == 3
    assert reply not in ai_coach._COACH_FALLBACKS["tr"].values()
    assert _minute_count(user.id, None) == 3        # each round charged exactly once


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_streaming_turn_opener_is_checked_in_the_producer_thread(app, stream_providers):
    for _ in range(3):
        _charge(42)
    events = list(ai_stream.stream_coach_answer(42, "soru", "", []))
    assert stream_providers.calls == 0
    assert events[-1]["type"] == "error"
    assert events[-1]["work_performed"] is False


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_streaming_turn_admitted_when_the_whole_turn_fits(app, stream_providers):
    for _ in range(2):
        _charge(42)
    events = list(ai_stream.stream_coach_answer(42, "soru", "", []))
    assert stream_providers.calls == 1
    assert events[-1]["type"] == "done"
    assert _minute_count(42, None) == 3
