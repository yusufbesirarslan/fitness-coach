"""Sprint 4 WS5 — AI sonuç önbelleği (app/services/ai_cache.py).

Hermetik: gerçek Redis yok. `_FakeRedis` (get/setex) ile davranış doğrulanır;
Redis yokken (redis_client=None) veya kapatma anahtarıyla no-op olduğu ve tüm
Redis hatalarının yutulduğu (asıl akış düşmez) sınanır.

    python -m pytest tests/test_ai_cache.py -v
"""
import json

import pytest

from app.services import ai_cache


class _FakeRedis:
    """get + setex taşıyan minimal sahte Redis (foodcache/menu-cache deseni)."""
    def __init__(self, fail=False):
        self.store = {}
        self.ttls = {}
        self.fail = fail

    def get(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = value
        self.ttls[key] = ttl


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr("app.extensions.redis_client", r)
    return r


# ── Temel round-trip ───────────────────────────────────────────────────────

def test_miss_returns_none(fake_redis):
    assert ai_cache.cache_get("food_normalize", {"q": "tavuk"}) is None


def test_set_then_get_roundtrips_string(fake_redis):
    ai_cache.cache_set("food_normalize", {"q": "tavuk göğsü"}, "chicken breast")
    assert ai_cache.cache_get("food_normalize", {"q": "tavuk göğsü"}) == "chicken breast"


def test_set_then_get_roundtrips_dict(fake_redis):
    value = {"tavuk": "chicken", "pilav": "rice"}
    ai_cache.cache_set("food_normalize_batch", {"names": ["a"]}, value)
    assert ai_cache.cache_get("food_normalize_batch", {"names": ["a"]}) == value


def test_stored_value_is_json(fake_redis):
    ai_cache.cache_set("food_normalize", {"q": "x"}, "y")
    raw = next(iter(fake_redis.store.values()))
    assert json.loads(raw) == "y"


def test_turkish_chars_not_escaped(fake_redis):
    # ensure_ascii=False → Türkçe karakterler \uXXXX'e şişmez.
    ai_cache.cache_set("food_normalize", {"q": "çğş"}, " öçş")
    raw = next(iter(fake_redis.store.values()))
    assert "\\u" not in raw


# ── Anahtar kararlılığı ─────────────────────────────────────────────────────

def test_key_is_order_independent(fake_redis):
    # sort_keys=True → payload sözlük sırası anahtarı değiştirmez.
    k1 = ai_cache._key("macro", {"a": 1, "b": 2})
    k2 = ai_cache._key("macro", {"b": 2, "a": 1})
    assert k1 == k2


def test_key_namespaced_and_versioned(fake_redis):
    key = ai_cache._key("food_normalize", {"q": "x"})
    assert key.startswith("ai:cache:food_normalize:v1:")


def test_different_feature_different_key(fake_redis):
    assert ai_cache._key("macro", {"q": "x"}) != ai_cache._key("recs", {"q": "x"})


# ── TTL ─────────────────────────────────────────────────────────────────────

def test_feature_default_ttl_applied(fake_redis):
    from app.config import AI_CACHE_TTL_FOOD
    ai_cache.cache_set("food_normalize", {"q": "x"}, "y")
    assert next(iter(fake_redis.ttls.values())) == AI_CACHE_TTL_FOOD


def test_explicit_ttl_overrides_default(fake_redis):
    ai_cache.cache_set("food_normalize", {"q": "x"}, "y", ttl=42)
    assert next(iter(fake_redis.ttls.values())) == 42


def test_unknown_feature_uses_default_ttl(fake_redis):
    from app.config import AI_CACHE_TTL_PROMPT
    ai_cache.cache_set("totally_unknown", {"q": "x"}, "y")
    assert next(iter(fake_redis.ttls.values())) == AI_CACHE_TTL_PROMPT


# ── Degrade (no-op) davranışı ──────────────────────────────────────────────

def test_no_redis_get_is_noop(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", None)
    assert ai_cache.cache_get("food_normalize", {"q": "x"}) is None


def test_no_redis_set_is_noop(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", None)
    ai_cache.cache_set("food_normalize", {"q": "x"}, "y")  # patlamamalı


def test_disabled_flag_short_circuits(fake_redis, monkeypatch):
    monkeypatch.setattr(ai_cache, "AI_CACHE_ENABLED", False)
    ai_cache.cache_set("food_normalize", {"q": "x"}, "y")
    assert fake_redis.store == {}  # yazılmadı
    assert ai_cache.cache_get("food_normalize", {"q": "x"}) is None


# ── Hata yutma (asıl akış asla düşmez) ─────────────────────────────────────

def test_get_swallows_redis_error(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", _FakeRedis(fail=True))
    assert ai_cache.cache_get("food_normalize", {"q": "x"}) is None


def test_set_swallows_redis_error(monkeypatch):
    monkeypatch.setattr("app.extensions.redis_client", _FakeRedis(fail=True))
    ai_cache.cache_set("food_normalize", {"q": "x"}, "y")  # patlamamalı


def test_corrupt_cached_value_treated_as_miss(fake_redis):
    # Redis'te geçersiz JSON → miss gibi (çağıran yeniden hesaplar).
    key = ai_cache._key("food_normalize", {"q": "x"})
    fake_redis.store[key] = "{not-json"
    assert ai_cache.cache_get("food_normalize", {"q": "x"}) is None
