"""Sağlayıcı yönlendirme testleri (app/services/ai.py).

_heavy_chat: BEDROCK_ENABLED'a göre Claude Sonnet (Bedrock) ya da OpenAI'ya gider;
herhangi bir Bedrock hatasında şeffafça OpenAI'ya düşer.
_claude_chat: OpenAI-stili argümanların Anthropic Messages API'ye çevirisi
(system hoisting, max_tokens clamp) + hata eşleme.
Ağ yok — bedrock_client/openai_client/anthropic monkeypatch'li.

    python -m pytest tests/test_ai_routing.py -v
"""
from types import SimpleNamespace

import pytest

from app.services import ai, ai_recovery
from app.services.ai_recovery import TransientAIError


class _LastGoodRedis:
    """get/setex taşıyan minimal sahte Redis (last-good round-trip testleri)."""
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(ai_recovery, "_sleep", lambda s: None)


# ---------------------------------------------------------------------------
# _heavy_chat yönlendirme
# ---------------------------------------------------------------------------

def test_heavy_chat_disabled_uses_openai(monkeypatch):
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai, "_claude_chat",
                        lambda *a, **k: pytest.fail("Bedrock kapalıyken Claude çağrılmamalı"))
    monkeypatch.setattr(ai, "_openai_chat", lambda *a, **k: "OPENAI")
    assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "OPENAI"


def test_heavy_chat_enabled_uses_claude(monkeypatch):
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())  # paket var (non-None)
    monkeypatch.setattr(ai, "_claude_chat", lambda *a, **k: "CLAUDE")
    monkeypatch.setattr(ai, "_openai_chat",
                        lambda *a, **k: pytest.fail("Claude başarılıyken OpenAI çağrılmamalı"))
    assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "CLAUDE"


def test_heavy_chat_disabled_when_anthropic_missing(monkeypatch):
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", None)  # paket kurulu değil
    monkeypatch.setattr(ai, "_claude_chat",
                        lambda *a, **k: pytest.fail("anthropic yokken Claude çağrılmamalı"))
    monkeypatch.setattr(ai, "_openai_chat", lambda *a, **k: "OPENAI")
    assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "OPENAI"


def test_heavy_chat_falls_back_on_claude_error(monkeypatch, caplog):
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())

    def boom(*a, **k):
        raise RuntimeError("bedrock down")
    monkeypatch.setattr(ai, "_claude_chat", boom)
    monkeypatch.setattr(ai, "_openai_chat", lambda *a, **k: "FALLBACK")

    with caplog.at_level("WARNING"):
        assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "FALLBACK"
    assert any("OpenAI'ya düşülüyor" in r.getMessage() for r in caplog.records)


def test_heavy_chat_passes_kwargs_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())

    def fake_claude(messages, system_prompt=None, max_tokens=1024, temperature=0.7):
        captured.update(messages=messages, system_prompt=system_prompt,
                        max_tokens=max_tokens, temperature=temperature)
        return "ok"
    monkeypatch.setattr(ai, "_claude_chat", fake_claude)

    ai._heavy_chat([{"role": "user", "content": "x"}],
                   system_prompt="S", max_tokens=4000, temperature=0.4)
    assert captured == {"messages": [{"role": "user", "content": "x"}],
                        "system_prompt": "S", "max_tokens": 4000, "temperature": 0.4}


# ---------------------------------------------------------------------------
# _heavy_chat WS9 kurtarma: geçici retry + last-good
# ---------------------------------------------------------------------------

def test_heavy_chat_retries_transient_bedrock_then_succeeds(monkeypatch, no_sleep):
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientAIError("429")  # geçici → retry
        return "CLAUDE-OK"
    monkeypatch.setattr(ai, "_claude_chat", flaky)
    monkeypatch.setattr(ai, "_openai_chat",
                        lambda *a, **k: pytest.fail("retry başarınca OpenAI'ya düşülmemeli"))
    assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "CLAUDE-OK"
    assert calls["n"] == 2  # bir kez daha denendi


def test_heavy_chat_non_transient_bedrock_no_retry_falls_back(monkeypatch, no_sleep):
    # Kalıcı (TransientAIError DEĞİL) Bedrock hatası retry EDİLMEZ; anında OpenAI'ya düşer.
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai, "anthropic", object())
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("AI servisi hatası")
    monkeypatch.setattr(ai, "_claude_chat", boom)
    monkeypatch.setattr(ai, "_openai_chat", lambda *a, **k: "FALLBACK")
    assert ai._heavy_chat([{"role": "user", "content": "x"}]) == "FALLBACK"
    assert calls["n"] == 1  # tek Bedrock denemesi (retry yok)


def test_heavy_chat_serves_last_good_when_both_providers_fail(monkeypatch, no_sleep):
    monkeypatch.setattr("app.extensions.redis_client", _LastGoodRedis())
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", False)
    msgs = [{"role": "user", "content": "aynı girdi"}]

    # 1) Başarılı OpenAI çağrısı last-good yazar.
    monkeypatch.setattr(ai, "_openai_chat", lambda *a, **k: "GOOD")
    assert ai._heavy_chat(msgs) == "GOOD"

    # 2) Aynı girdi, OpenAI düşer → bayat-ama-gerçek last-good sunulur.
    def down(*a, **k):
        raise RuntimeError("AI servisi hatası")
    monkeypatch.setattr(ai, "_openai_chat", down)
    assert ai._heavy_chat(msgs) == "GOOD"


def test_heavy_chat_raises_when_both_fail_and_no_last_good(monkeypatch, no_sleep):
    monkeypatch.setattr("app.extensions.redis_client", None)  # last-good yok
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", False)

    def down(*a, **k):
        raise RuntimeError("AI servisi hatası")
    monkeypatch.setattr(ai, "_openai_chat", down)
    with pytest.raises(RuntimeError):
        ai._heavy_chat([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# _claude_chat çeviri + hata eşleme
# ---------------------------------------------------------------------------

class _FakeRateLimit(Exception):
    pass


class _FakeTimeout(Exception):
    pass


class _FakeConn(Exception):
    pass


class _FakeAPIError(Exception):
    pass


_FAKE_ANTHROPIC = SimpleNamespace(
    RateLimitError=_FakeRateLimit,
    APITimeoutError=_FakeTimeout,
    APIConnectionError=_FakeConn,
    APIError=_FakeAPIError,
)


def _fake_bedrock(monkeypatch, *, reply=None, raises=None, capture=None):
    """ai.bedrock_client.messages.create'i ve ai.anthropic'i sahte ile değiştir."""
    def create(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        if raises is not None:
            raise raises
        return reply
    monkeypatch.setattr(ai, "anthropic", _FAKE_ANTHROPIC)
    monkeypatch.setattr(ai, "bedrock_client",
                        SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_claude_chat_hoists_system_and_clamps_tokens(monkeypatch):
    cap = {}
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="merhaba")],
                           stop_reason="end_turn")
    _fake_bedrock(monkeypatch, reply=resp, capture=cap)
    monkeypatch.setattr(ai, "BEDROCK_MODEL", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
    monkeypatch.setattr(ai, "BEDROCK_MAX_TOKENS", 8000)

    out = ai._claude_chat(
        messages=[{"role": "system", "content": "EK SİSTEM"},
                  {"role": "user", "content": "selam"}],
        system_prompt="ANA SİSTEM",
        max_tokens=999999,   # clamp testi
        temperature=0.3,
    )
    assert out == "merhaba"
    # system_prompt + messages içindeki stray system birleşip üst düzey system= oldu:
    assert cap["system"] == "ANA SİSTEM\n\nEK SİSTEM"
    # messages içinde artık system rolü YOK:
    assert cap["messages"] == [{"role": "user", "content": "selam"}]
    assert cap["max_tokens"] == 8000  # BEDROCK_MAX_TOKENS'a clamp'lendi
    assert cap["model"] == "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    assert cap["temperature"] == 0.3


def test_claude_chat_omits_system_when_none(monkeypatch):
    cap = {}
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                           stop_reason="end_turn")
    _fake_bedrock(monkeypatch, reply=resp, capture=cap)
    ai._claude_chat([{"role": "user", "content": "x"}])
    assert "system" not in cap  # boş system gönderilmez


def test_claude_chat_no_text_block_returns_empty(monkeypatch):
    resp = SimpleNamespace(content=[SimpleNamespace(type="tool_use")], stop_reason="end_turn")
    _fake_bedrock(monkeypatch, reply=resp)
    assert ai._claude_chat([{"role": "user", "content": "x"}]) == ""


def test_claude_chat_maps_rate_limit(monkeypatch):
    _fake_bedrock(monkeypatch, raises=_FakeRateLimit("429"))
    with pytest.raises(RuntimeError, match="yoğun"):
        ai._claude_chat([{"role": "user", "content": "x"}])


def test_claude_chat_maps_timeout(monkeypatch):
    _fake_bedrock(monkeypatch, raises=_FakeTimeout("t/o"))
    with pytest.raises(RuntimeError, match="zaman aşımı"):
        ai._claude_chat([{"role": "user", "content": "x"}])


def test_claude_chat_maps_api_error(monkeypatch):
    _fake_bedrock(monkeypatch, raises=_FakeAPIError("boom"))
    with pytest.raises(RuntimeError, match="AI servisi hatası"):
        ai._claude_chat([{"role": "user", "content": "x"}])


def test_claude_chat_rate_limit_is_transient(monkeypatch):
    # WS9: rate-limit/timeout artık TransientAIError (retry sinyali) — ama hâlâ
    # RuntimeError alt sınıfı, dostça metin sözleşmesi korunur.
    _fake_bedrock(monkeypatch, raises=_FakeRateLimit("429"))
    with pytest.raises(TransientAIError):
        ai._claude_chat([{"role": "user", "content": "x"}])


def test_claude_chat_api_error_is_not_transient(monkeypatch):
    # Kalıcı hata retry edilmemeli → TransientAIError DEĞİL.
    _fake_bedrock(monkeypatch, raises=_FakeAPIError("boom"))
    with pytest.raises(RuntimeError) as exc:
        ai._claude_chat([{"role": "user", "content": "x"}])
    assert not isinstance(exc.value, TransientAIError)


# ---------------------------------------------------------------------------
# Lazy istemci: import sırasında AWS/anthropic'e dokunmaz
# ---------------------------------------------------------------------------

def test_bedrock_client_is_lazy_not_constructed(monkeypatch):
    # Süreç-global singleton'ın _client'ı, önceki herhangi bir Bedrock kullanımıyla
    # kalıcı olarak kirlenebilir (sınıf değil instance attribute'una yazılır ve
    # singleton tüm süreç boyunca paylaşılır). Bu yüzden lazy davranışı TAZE bir
    # instance üzerinde doğrula — sıra/kirlilikten bağımsız ve daha kesin.
    from app.extensions import _LazyAnthropicBedrock
    fresh = _LazyAnthropicBedrock()
    assert fresh._client is None  # __getattr__ tetiklenene dek istemci kurulmaz

    # İlk attribute erişimi istemciyi lazy kurar. anthropic import'unu sahteleyip
    # ağ/AWS anahtarı OLMADAN kurulumun doğru argümanlarla tetiklendiğini doğrula.
    import sys
    import types

    constructed = {}

    class _FakeBedrock:
        def __init__(self, **kwargs):
            constructed.update(kwargs)
            self.messages = "sentinel"

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AnthropicBedrock = _FakeBedrock
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    assert fresh.messages == "sentinel"             # erişim __getattr__'i tetikledi
    assert isinstance(fresh._client, _FakeBedrock)  # artık kuruldu ve cache'lendi
    assert "aws_region" in constructed              # bölge ile kuruldu
    assert "api_key" not in constructed             # token asla argüman olarak geçmez
