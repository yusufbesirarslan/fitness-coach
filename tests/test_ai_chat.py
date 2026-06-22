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


def _resp(content="yanıt", finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content))])


class _FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if error:
                    raise error
                return response or _resp()

        self.chat = SimpleNamespace(completions=_Completions())


def test_chat_prepends_system_prompt_and_passes_params(monkeypatch):
    fake = _FakeClient(_resp("merhaba"))
    monkeypatch.setattr(ai, "openai_client", fake)

    out = _openai_chat([{"role": "user", "content": "selam"}],
                       system_prompt="Sen bir koçsun.", max_tokens=99, temperature=0.1)
    assert out == "merhaba"
    call = fake.calls[0]
    assert call["messages"][0] == {"role": "system", "content": "Sen bir koçsun."}
    assert call["messages"][1]["content"] == "selam"
    assert call["max_tokens"] == 99
    assert call["temperature"] == 0.1


def test_chat_without_system_prompt(monkeypatch):
    fake = _FakeClient(_resp("ok"))
    monkeypatch.setattr(ai, "openai_client", fake)
    _openai_chat([{"role": "user", "content": "selam"}])
    assert fake.calls[0]["messages"][0]["role"] == "user"


def test_chat_warns_when_response_truncated(monkeypatch, caplog):
    monkeypatch.setattr(ai, "openai_client", _FakeClient(_resp("yarım", "length")))
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
def test_chat_maps_openai_errors_to_friendly_runtime_errors(monkeypatch, exc_name, expected):
    class _Boom(Exception):
        pass
    # ai modülü hata sınıflarını global adla yakalar → sahte sınıfı aynı ada koy.
    monkeypatch.setattr(ai, exc_name, _Boom)
    monkeypatch.setattr(ai, "openai_client", _FakeClient(error=_Boom("kaboom")))

    with pytest.raises(RuntimeError, match=expected):
        _openai_chat([{"role": "user", "content": "x"}])


def test_chat_none_content_returns_empty_string(monkeypatch):
    # OpenAI refuse/length durumunda message.content None olabilir; çağıranların
    # çoğu dönüşe .strip() uyguluyor → None değil "" dönmeli (AttributeError yok).
    monkeypatch.setattr(ai, "openai_client", _FakeClient(_resp(content=None)))
    assert _openai_chat([{"role": "user", "content": "x"}]) == ""


def test_chat_empty_choices_returns_empty_string(monkeypatch):
    # İçerik filtresi boş choices döndürebilir → IndexError yerine "" dönmeli.
    monkeypatch.setattr(ai, "openai_client", _FakeClient(SimpleNamespace(choices=[])))
    assert _openai_chat([{"role": "user", "content": "x"}]) == ""
