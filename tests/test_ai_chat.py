"""Tests for the central LLM wrapper (app/services/ai.py).

System prompt yerleştirme, parametre geçişi, max_tokens kesilme uyarısı ve
OpenAI hata sınıflarının kullanıcı-dostu RuntimeError'lara çevrilmesi.
İstemci mock'lu — ağ yok.

    python -m pytest tests/test_ai_chat.py -v
"""
import logging
from types import SimpleNamespace

import pytest

from app.services import ai
from app.services.ai import _openai_chat


def _resp(content="yanıt", stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=content or "")])


class _FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response or _resp()


def _install(monkeypatch, fake):
    monkeypatch.setattr(ai, "light_client", SimpleNamespace(messages=fake))


def test_chat_hoists_system_prompt_and_passes_params(monkeypatch):
    fake = _FakeClient(_resp("merhaba"))
    _install(monkeypatch, fake)

    out = _openai_chat([{"role": "user", "content": "selam"}],
                       system_prompt="Sen bir koçsun.", max_tokens=99, temperature=0.1)
    assert out == "merhaba"
    call = fake.calls[0]
    assert call["system"] == "Sen bir koçsun."
    assert call["messages"][0]["content"] == "selam"
    assert call["model"] == "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert call["max_tokens"] == 99
    assert call["temperature"] == 0.1


def test_chat_without_system_prompt(monkeypatch):
    fake = _FakeClient(_resp("ok"))
    _install(monkeypatch, fake)
    _openai_chat([{"role": "user", "content": "selam"}])
    assert fake.calls[0]["messages"][0]["role"] == "user"
    assert "system" not in fake.calls[0]


def test_chat_warns_when_response_truncated(monkeypatch, caplog):
    _install(monkeypatch, _FakeClient(_resp("yarım", "max_tokens")))
    with caplog.at_level(logging.WARNING):
        out = _openai_chat([{"role": "user", "content": "x"}], max_tokens=10)
    assert out == "yarım"
    assert any("kesildi" in r.message for r in caplog.records)


@pytest.mark.parametrize("exc_name,expected", [
    ("RateLimitError", "yoğun"),
    ("APITimeoutError", "ulaşılamadı"),
    ("APIConnectionError", "ulaşılamadı"),
    ("APIError", "AI servisi hatası"),
])
def test_chat_maps_provider_errors_to_friendly_runtime_errors(monkeypatch, exc_name, expected):
    class _Boom(Exception):
        pass
    monkeypatch.setattr(ai.anthropic, exc_name, _Boom)
    _install(monkeypatch, _FakeClient(error=_Boom("kaboom")))

    with pytest.raises(RuntimeError, match=expected):
        _openai_chat([{"role": "user", "content": "x"}])


def test_chat_empty_text_returns_empty_string(monkeypatch):
    _install(monkeypatch, _FakeClient(_resp(content="")))
    assert _openai_chat([{"role": "user", "content": "x"}]) == ""


def test_chat_empty_content_returns_empty_string(monkeypatch):
    _install(monkeypatch, _FakeClient(SimpleNamespace(stop_reason="end_turn", content=[])))
    assert _openai_chat([{"role": "user", "content": "x"}]) == ""
