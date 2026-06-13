"""Tests for the menu-extraction OUTPUT budget and truncated-JSON salvage.

docs/menu-extraction-truncation-risk.md: PR #32 widened the extractor input
(40k chars) and item capacity (80) but left the LLM output cap at
max_tokens=2500, so big menus could come back truncated mid-array and the
whole extraction collapsed to {} -> OUTPUT_PARSING_FAILED.

Pinned here:
  - _MENU_EXTRACT_MAX_TOKENS is derived from MAX_MENU_ITEMS (single source)
    and actually reaches _openai_chat.
  - Truncated JSON is salvaged up to the last complete item/category
    boundary instead of dropping the entire menu.
  - finish_reason == "length" produces an explicit warning log.

No real network: _openai_chat / openai_client are monkeypatched.

    python -m pytest tests/test_menu_extract_truncation.py -v
"""
import os
import sys

os.environ.setdefault("OPENAI_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flask  # noqa: E402

import app.services.ai_nutrition as ai_nutrition  # noqa: E402
from app.services.ai_nutrition import (  # noqa: E402
    MAX_MENU_ITEMS,
    _MENU_EXTRACT_MAX_TOKENS,
    _extract_categorized_items,
    _salvage_truncated_categories,
)


def _fake_llm(monkeypatch, response):
    captured = {}

    def _fake(messages=None, system_prompt=None, temperature=0.0, max_tokens=0, **_):
        captured["prompt"] = messages[0]["content"]
        captured["max_tokens"] = max_tokens
        return response

    monkeypatch.setattr(ai_nutrition, "_openai_chat", _fake)
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", _fake)  # menü çıkarımı artık ağır yol
    return captured


# --- capacity <-> output budget alignment (öneri #1) ---

def test_output_budget_derived_from_capacity():
    # The budget must move together with the item capacity: at least ~50
    # tokens per item (Turkish names tokenize long) plus structure overhead.
    assert _MENU_EXTRACT_MAX_TOKENS >= MAX_MENU_ITEMS * 50
    # And well above the old fixed 2500 that truncated 80-item responses.
    assert _MENU_EXTRACT_MAX_TOKENS >= 4000


def test_extractor_passes_derived_budget_to_llm(monkeypatch):
    captured = _fake_llm(monkeypatch, '{"categories": {"Genel": ["x"]}}')
    with flask.Flask(__name__).app_context():
        _extract_categorized_items("menü " * 10)
    assert captured["max_tokens"] == _MENU_EXTRACT_MAX_TOKENS


def test_prompt_quota_uses_capacity_constant(monkeypatch):
    captured = _fake_llm(monkeypatch, '{"categories": {"Genel": ["x"]}}')
    with flask.Flask(__name__).app_context():
        _extract_categorized_items("menü " * 10)
    assert f"toplam en fazla {MAX_MENU_ITEMS} yemek" in captured["prompt"]


# --- truncated-JSON salvage (öneri #2) ---

_TRUNCATED_MID_ITEM = ('{"categories": {"Ana Yemekler": ["Kebap", "Köfte"], '
                       '"Tatlılar": ["Sufle", "Profit')


def test_salvage_recovers_complete_items_from_mid_string_cut():
    out = _salvage_truncated_categories(_TRUNCATED_MID_ITEM)
    assert out == {"Ana Yemekler": ["Kebap", "Köfte"], "Tatlılar": ["Sufle"]}


def test_salvage_drops_category_cut_before_first_item():
    raw = '{"categories": {"Ana Yemekler": ["Kebap"], "Tatlılar": ['
    out = _salvage_truncated_categories(raw)
    assert out == {"Ana Yemekler": ["Kebap"]}


def test_salvage_handles_cut_after_category_key():
    raw = '{"categories": {"Ana Yemekler": ["Kebap", "Köfte"], "Tatl'
    out = _salvage_truncated_categories(raw)
    assert out == {"Ana Yemekler": ["Kebap", "Köfte"]}


def test_salvage_returns_none_when_nothing_recoverable():
    assert _salvage_truncated_categories("hiç JSON yok") is None
    assert _salvage_truncated_categories('{"categories": {"Tatl') is None


def test_extractor_returns_partial_result_instead_of_empty(monkeypatch):
    # End-to-end: a truncated LLM response no longer collapses to {} (which
    # analyze_menu turns into OUTPUT_PARSING_FAILED) — partial coverage wins.
    _fake_llm(monkeypatch, _TRUNCATED_MID_ITEM)
    with flask.Flask(__name__).app_context():
        out = _extract_categorized_items("menü " * 10)
    assert out == {"Ana Yemekler": ["Kebap", "Köfte"], "Tatlılar": ["Sufle"]}


def test_extractor_intact_json_still_parses(monkeypatch):
    _fake_llm(monkeypatch, '{"categories": {"Genel": ["Çorba"]}}')
    with flask.Flask(__name__).app_context():
        out = _extract_categorized_items("menü " * 10)
    assert out == {"Genel": ["Çorba"]}


# --- finish_reason == "length" warning (öneri #3) ---

def test_finish_reason_length_is_logged(monkeypatch, caplog):
    import app.services.ai as ai_mod

    class _Msg:
        content = '{"categories": {}}'

    class _Choice:
        finish_reason = "length"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(ai_mod, "openai_client", _Client())
    with caplog.at_level("WARNING", logger="app.services.ai"):
        out = ai_mod._openai_chat([{"role": "user", "content": "x"}], max_tokens=10)
    assert out == '{"categories": {}}'
    assert any("finish_reason=length" in r.getMessage() for r in caplog.records)
