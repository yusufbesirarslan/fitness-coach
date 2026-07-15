"""Sprint 4 WS9 — AI hata kurtarma + WS7 arıza soğuması (app/services/ai_recovery.py).

Hermetik: gerçek Redis/uyku yok. `_sleep` monkeypatch'lenir (test hızlı),
`_FakeRedis` last-good + soğuma sayaçlarını taşır. Geçici (TransientAIError) vs
kalıcı hata ayrımı, yeniden-deneme sınırı, last-good round-trip ve soğuma
merdiveni sınanır.

    python -m pytest tests/test_ai_recovery.py -v
"""
import pytest

from app.services import ai_recovery
from app.services.ai_recovery import TransientAIError


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Yeniden-deneme uykusunu no-op yap — testler saniyeler sürmesin."""
    calls = []
    monkeypatch.setattr(ai_recovery, "_sleep", lambda s: calls.append(s))
    return calls


class _FakeRedis:
    """last-good (get/setex) + soğuma (incr/expire/set-nx/delete/ttl) sahtesi."""
    def __init__(self, fail=False):
        self.store = {}
        self.ttls = {}
        self.fail = fail

    def _guard(self):
        if self.fail:
            raise RuntimeError("redis down")

    def get(self, key):
        self._guard()
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self._guard()
        self.store[key] = value
        self.ttls[key] = ttl

    def incr(self, key):
        self._guard()
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, ttl):
        self._guard()
        if key in self.store:
            self.ttls[key] = ttl

    def set(self, key, value, nx=False, ex=None):
        self._guard()
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def delete(self, *keys):
        self._guard()
        for k in keys:
            self.store.pop(k, None)
            self.ttls.pop(k, None)

    def ttl(self, key):
        self._guard()
        if key not in self.store:
            return -2
        return self.ttls.get(key, -1)


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr("app.extensions.redis_client", r)
    return r


# ── call_with_recovery ─────────────────────────────────────────────────────

def test_success_first_call_no_retry(no_sleep):
    out = ai_recovery.call_with_recovery(lambda: "ok", feature="t")
    assert out == "ok"
    assert no_sleep == []  # hiç uyunmadı


def test_retries_transient_then_succeeds(no_sleep):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientAIError("429")
        return "recovered"

    out = ai_recovery.call_with_recovery(flaky, feature="t", attempts=3)
    assert out == "recovered"
    assert calls["n"] == 2
    assert len(no_sleep) == 1  # tek geri çekilme


def test_exhausts_transient_then_raises(no_sleep):
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise TransientAIError("still down")

    with pytest.raises(TransientAIError):
        ai_recovery.call_with_recovery(always, feature="t", attempts=2)
    assert calls["n"] == 2               # 2 deneme yapıldı
    assert len(no_sleep) == 1            # denemeler arası 1 uyku


def test_non_transient_raises_immediately(no_sleep):
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("bad request")  # TransientAIError DEĞİL

    with pytest.raises(RuntimeError):
        ai_recovery.call_with_recovery(boom, feature="t", attempts=3)
    assert calls["n"] == 1   # retry YOK
    assert no_sleep == []


def test_recovery_disabled_single_attempt(no_sleep, monkeypatch):
    monkeypatch.setattr(ai_recovery, "AI_RECOVERY_ENABLED", False)
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise TransientAIError("x")

    with pytest.raises(TransientAIError):
        ai_recovery.call_with_recovery(always, feature="t", attempts=5)
    assert calls["n"] == 1   # AI_RECOVERY_ENABLED=0 → tek deneme


def test_backoff_delay_within_bounds():
    from app.config import AI_RETRY_MAX_DELAY
    for attempt in range(5):
        d = ai_recovery._backoff_delay(attempt)
        assert 0 < d <= AI_RETRY_MAX_DELAY


# ── last-good ──────────────────────────────────────────────────────────────

def test_lastgood_key_stable_and_namespaced():
    k1 = ai_recovery.lastgood_key("heavy_chat", "model", "sys", [{"role": "user"}], 100)
    k2 = ai_recovery.lastgood_key("heavy_chat", "model", "sys", [{"role": "user"}], 100)
    assert k1 == k2
    assert k1.startswith("ai:lastgood:heavy_chat:v1:")


def test_lastgood_key_differs_on_input():
    a = ai_recovery.lastgood_key("f", "x")
    b = ai_recovery.lastgood_key("f", "y")
    assert a != b


def test_remember_recall_roundtrip(fake_redis):
    key = ai_recovery.lastgood_key("f", "input")
    ai_recovery.remember_last_good(key, "the answer")
    assert ai_recovery.recall_last_good(key) == "the answer"


def test_recall_miss_returns_none(fake_redis):
    assert ai_recovery.recall_last_good(ai_recovery.lastgood_key("f", "nope")) is None


def test_remember_skips_empty_and_non_str(fake_redis):
    key = ai_recovery.lastgood_key("f", "x")
    ai_recovery.remember_last_good(key, "")        # boş
    ai_recovery.remember_last_good(key, "   ")     # yalnız boşluk
    ai_recovery.remember_last_good(key, {"a": 1})  # str değil
    assert ai_recovery.recall_last_good(key) is None
    assert fake_redis.store == {}


def test_lastgood_noop_without_redis(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", None)
    key = ai_recovery.lastgood_key("f", "x")
    ai_recovery.remember_last_good(key, "y")  # patlamamalı
    assert ai_recovery.recall_last_good(key) is None


def test_lastgood_swallows_redis_error(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", _FakeRedis(fail=True))
    key = ai_recovery.lastgood_key("f", "x")
    ai_recovery.remember_last_good(key, "y")            # patlamamalı
    assert ai_recovery.recall_last_good(key) is None    # hata → None


# ── WS7 arıza soğuması ─────────────────────────────────────────────────────

def test_failures_below_threshold_no_cooldown(fake_redis, monkeypatch):
    monkeypatch.setattr(ai_recovery, "AI_FAILURE_THRESHOLD", 3)
    ai_recovery.record_ai_failure(7)
    ai_recovery.record_ai_failure(7)
    assert ai_recovery.ai_cooldown_remaining(7) == 0  # 2 < 3 → soğuma yok


def test_threshold_arms_cooldown(fake_redis, monkeypatch):
    monkeypatch.setattr(ai_recovery, "AI_FAILURE_THRESHOLD", 3)
    for _ in range(3):
        ai_recovery.record_ai_failure(7)
    remaining = ai_recovery.ai_cooldown_remaining(7)
    assert remaining > 0


def test_clear_resets_streak(fake_redis, monkeypatch):
    monkeypatch.setattr(ai_recovery, "AI_FAILURE_THRESHOLD", 3)
    ai_recovery.record_ai_failure(7)
    ai_recovery.record_ai_failure(7)
    ai_recovery.clear_ai_failures(7)          # sayaç sıfırlanır
    ai_recovery.record_ai_failure(7)          # tekrar 1'den başlar
    assert ai_recovery.ai_cooldown_remaining(7) == 0


def test_cooldown_isolated_per_user(fake_redis, monkeypatch):
    monkeypatch.setattr(ai_recovery, "AI_FAILURE_THRESHOLD", 2)
    ai_recovery.record_ai_failure(1)
    ai_recovery.record_ai_failure(1)
    assert ai_recovery.ai_cooldown_remaining(1) > 0
    assert ai_recovery.ai_cooldown_remaining(2) == 0  # başka kullanıcı etkilenmez


def test_cooldown_noop_without_redis(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", None)
    ai_recovery.record_ai_failure(7)                    # patlamamalı
    assert ai_recovery.ai_cooldown_remaining(7) == 0    # fail-open
    ai_recovery.clear_ai_failures(7)                    # patlamamalı


def test_cooldown_swallows_redis_error(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", _FakeRedis(fail=True))
    ai_recovery.record_ai_failure(7)                    # patlamamalı
    assert ai_recovery.ai_cooldown_remaining(7) == 0    # hata → 0 (fail-open)
