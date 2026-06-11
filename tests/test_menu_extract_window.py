"""Tests for the menu-extraction input window (_extract_categorized_items).

This PR widened the extractor input from 10000 to _MENU_EXTRACT_MAX_CHARS (40000)
so long menus (e.g. the ~32k-char BigChefs page) no longer lose their later
categories (Burgerler, Pizzalar, Tatlılar) before they ever reach the LLM.

Two paths exist and they truncate differently — pinned here so the documented
"toplam giriş bütçesini koru" tradeoff stays intentional:
  - no framework_state -> raw_text[:40000]
  - with framework_state -> fw_state[:6000] + raw_text[:34000]  (fw costs 6k of raw)

No real network: _openai_chat is monkeypatched to capture the prompt it receives.

    python -m pytest tests/test_menu_extract_window.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flask  # noqa: E402

import app.services.ai_nutrition as ai_nutrition  # noqa: E402
from app.services.ai_nutrition import (  # noqa: E402
    _MENU_EXTRACT_MAX_CHARS,
    _extract_categorized_items,
)

_FW_RESERVE = 6000  # fw_state prefix budget reserved from the raw-text window


def _build_text(markers):
    """50k-char filler with unique markers placed at exact byte offsets."""
    buf = ["."] * 50000
    for off, text in markers.items():
        for i, ch in enumerate(text):
            buf[off + i] = ch
    return "".join(buf)


def _capture_prompt(monkeypatch):
    """Patch _openai_chat to record the user prompt and return canned JSON."""
    captured = {}

    def _fake(messages=None, system_prompt=None, temperature=0.0, max_tokens=0, **_):
        captured["prompt"] = messages[0]["content"]
        captured["max_tokens"] = max_tokens
        return '{"categories": {"Genel": ["x"]}}'

    monkeypatch.setattr(ai_nutrition, "_openai_chat", _fake)
    return captured


_MARKERS = {
    120: "MARK_EARLY",
    30000: "MARK_AT_30K",
    35000: "MARK_AT_35K",   # between the 34k (fw path) and 40k (plain) cutoffs
    45000: "MARK_AT_45K",   # beyond every window
}


def test_constant_is_aligned_value():
    # Must match the scraper's body_text cap (menu.py _BODY_TEXT_MAX) — see review.
    assert _MENU_EXTRACT_MAX_CHARS == 40000


def test_plain_path_includes_up_to_40k(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    raw = _build_text(_MARKERS)
    with flask.Flask(__name__).app_context():
        _extract_categorized_items(raw, fw_state=None)
    prompt = captured["prompt"]
    assert "MARK_EARLY" in prompt
    assert "MARK_AT_30K" in prompt
    assert "MARK_AT_35K" in prompt          # 35000 < 40000 -> reaches the LLM
    assert "MARK_AT_45K" not in prompt      # 45000 >= 40000 -> dropped


def test_framework_state_path_reserves_budget_for_fw(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    raw = _build_text(_MARKERS)
    fw = "FWSTATE_UNIQUE " + ("f" * 200)
    with flask.Flask(__name__).app_context():
        _extract_categorized_items(raw, fw_state=fw)
    prompt = captured["prompt"]
    assert "FWSTATE_UNIQUE" in prompt        # framework_state is included
    assert "MARK_EARLY" in prompt
    assert "MARK_AT_30K" in prompt           # 30000 < 34000 -> kept
    # fw_state costs 6000 chars of the raw window, so the 34k..40k slice is dropped
    # on this path (documented "toplam giriş bütçesini koru" tradeoff).
    assert "MARK_AT_35K" not in prompt       # 35000 >= 34000 -> dropped here
    assert "MARK_AT_45K" not in prompt


def test_total_input_stays_within_budget(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    raw = _build_text(_MARKERS)
    fw = "z" * 9000  # oversized fw_state must still be clamped to 6000
    with flask.Flask(__name__).app_context():
        _extract_categorized_items(raw, fw_state=fw)
    # fw_state is clamped to exactly _FW_RESERVE chars regardless of its size, so
    # the menu payload (fw[:6000] + "\n\n" + raw[:34000]) stays within budget.
    prompt = captured["prompt"]
    assert "z" * _FW_RESERVE in prompt           # 6000 chars passed through
    assert "z" * (_FW_RESERVE + 1) not in prompt  # not one char more
