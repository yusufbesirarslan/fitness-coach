"""Hermetic Chromium checks for the compact Progress continuation."""
import os
from pathlib import Path

import pytest

from ux4_gate_support import gate, ready  # noqa: F401

from app.blueprints import coach as coach_bp


@pytest.fixture(autouse=True)
def close_adapter_responses(client, monkeypatch):
    """The browser adapter buffers responses; close them to release SSE slots."""
    original = client.open

    def buffered_open(*args, **kwargs):
        response = original(*args, **kwargs)
        response.get_data()
        response.close()
        return response

    monkeypatch.setattr(client, "open", buffered_open)


def test_handoff_preview_is_compact_across_viewports(app, gate, make_user, login):  # noqa: F811
    user = make_user("progress_browser", profile_complete=True)
    login("progress_browser")
    for locale in ("en", "tr"):
        ready(app, user.id, locale)
        for width in (320, 390, 430, 768, 1024, 1366):
            gate.visit("/coach?review=progress-insight", width=width)
            facts = gate.page.evaluate("""() => {
              const c = document.getElementById('coach-progress-context');
              const i = document.getElementById('cw-input');
              return {preview: !!c, height: c && c.getBoundingClientRect().height,
                draft: i && i.value, overflow: document.documentElement.scrollWidth > innerWidth,
                marker: window.CW && window.CW.handoff};
            }""")
            assert facts["preview"] and facts["height"] < 150, (locale, width, facts)
            assert facts["marker"] == "progress-insight"
            assert len(facts["draft"]) < 90
            assert not facts["overflow"] and not gate.errors, (locale, width, gate.errors)
            assert "/api/progress/axis-insights" not in gate.app_reads()
            shots = os.environ.get("HANDOFF_QA_SHOTS")
            if shots and width in (390, 1366):
                Path(shots).mkdir(parents=True, exist_ok=True)
                gate.page.screenshot(path=str(Path(shots) / f"coach-{locale}-{width}.png"))


def test_first_send_uses_marker_then_normal_entry_has_none(
        app, gate, make_user, login, monkeypatch):  # noqa: F811
    user = make_user("progress_send", profile_complete=True)
    ready(app, user.id, "en")
    login("progress_send")
    app.config["AI_CHAT_QUOTA_ENABLED"] = False
    seen = []

    def fake_stream(uid, question, history, language="tr", **kw):
        seen.append((uid, question, kw.get("handoff")))
        yield {"type": "meta", "conversation_id": None}
        yield {"type": "done", "text": "A grounded answer.",
               "is_error_fallback": False, "usage": None}

    monkeypatch.setattr(coach_bp, "stream_answer", fake_stream)
    gate.visit("/coach?review=progress-insight", width=390)
    gate.page.locator("#cw-input").fill("My own edited request.")
    gate.page.locator("#cw-send").click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('A grounded answer.')")
    assert seen == [(user.id, "My own edited request.", "progress-insight")]
    assert gate.page.locator("#cw-msgs").get_by_text("My own edited request.").count() == 1
    assert gate.page.locator("#coach-progress-context").count() == 0
    assert gate.page.evaluate("() => location.pathname + location.search") == "/coach"
    assert gate.page.evaluate("() => window.CW._lastHandoff") is None
    gate.page.locator("#cw-input").fill("Another question.")
    gate.page.locator("#cw-send").click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('Another question.')")
    assert seen[-1] == (user.id, "Another question.", None)
    gate.visit("/coach", width=390)
    assert gate.page.locator("#coach-progress-context").count() == 0
    assert gate.page.evaluate("() => window.CW.handoff") is None


def test_plain_fallback_failure_keeps_context_until_success(
        app, gate, make_user, login, monkeypatch):  # noqa: F811
    user = make_user("progress_plain", profile_complete=True)
    ready(app, user.id, "en")
    login("progress_plain")
    app.config["AI_CHAT_QUOTA_ENABLED"] = False
    seen = []

    def answer(uid, question, history, language="tr", **kw):
        seen.append((question, kw.get("handoff")))
        return {"answer": "Failed reply" if len(seen) == 1 else "Successful reply",
                "is_error_fallback": len(seen) == 1, "conversation_id": None}

    monkeypatch.setattr(coach_bp, "generate_answer", answer)
    gate.visit("/coach?review=progress-insight", width=390)
    gate.page.evaluate("""() => {
      window.CW._ask = function(q, h) {
        this._lastQ = q; this._lastHandoff = h; this._activeHandoff = h;
        this._setLoading(true); return this._plainAsk(q);
      };
    }""")
    gate.page.locator("#cw-send").click()
    gate.page.wait_for_function("() => !window.CW.busy")
    assert gate.page.locator("#coach-progress-context").count() == 1
    gate.page.locator(".cw-regen").click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('Successful reply')")
    assert gate.page.locator("#coach-progress-context").count() == 0
    assert seen == [("Help me plan this week.", "progress-insight")] * 2
    gate.page.locator("#cw-input").fill("Another question.")
    gate.page.locator("#cw-send").click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('Another question.')")
    assert seen[-1] == ("Another question.", None)
    gate.visit("/coach", width=390)
    assert gate.page.locator("#coach-progress-context").count() == 0
    assert gate.page.evaluate("() => window.CW.handoff") is None


def test_failed_send_keeps_handoff_for_retry_and_dismiss_clears_it(
        app, gate, make_user, login, monkeypatch):  # noqa: F811
    user = make_user("progress_retry", profile_complete=True)
    ready(app, user.id, "en")
    login("progress_retry")
    app.config["AI_CHAT_QUOTA_ENABLED"] = False
    seen = []

    def fake_stream(uid, question, history, language="tr", **kw):
        seen.append(kw.get("handoff"))
        yield {"type": "meta", "conversation_id": None}
        if len(seen) in (1, 3):
            yield {"type": "error", "key": "coach.reply_failed"}
        else:
            yield {"type": "done", "text": "Try a smaller goal.",
                   "is_error_fallback": False, "usage": None}

    monkeypatch.setattr(coach_bp, "stream_answer", fake_stream)
    gate.visit("/coach?review=progress-insight", width=390)
    gate.page.locator("#cw-send").click()
    gate.page.wait_for_function("() => !window.CW.busy")
    assert seen == ["progress-insight"]
    assert gate.page.locator("#coach-progress-context").count() == 1
    gate.page.locator(".cw-regen").click()
    gate.page.wait_for_function("() => document.querySelector('#cw-msgs').textContent.includes('Try a smaller goal.')")
    assert seen == ["progress-insight", "progress-insight"]
    assert gate.page.locator("#coach-progress-context").count() == 0
    gate.visit("/coach?review=progress-insight", width=390)
    gate.page.locator("#cw-send").click()
    gate.page.wait_for_function("() => !window.CW.busy")
    gate.page.locator("#coach-progress-dismiss").click()
    assert gate.page.locator("#coach-progress-context").count() == 0
    assert gate.page.evaluate("() => window.CW.handoff") is None
    assert gate.page.evaluate("() => window.CW._lastHandoff") is None
    assert gate.page.evaluate("() => location.pathname + location.search") == "/coach"
    assert gate.page.locator("#cw-input").evaluate("el => el === document.activeElement")
    gate.page.locator(".cw-regen").click()
    gate.page.wait_for_function("() => !window.CW.busy")
    assert seen == ["progress-insight"] * 3 + [None]
