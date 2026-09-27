"""Real browser and Flask route coverage for the Check-in submit lifecycle."""
import json
import os
from pathlib import Path

import pytest

from app.blueprints import tracking
from app.extensions import db
from app.models import User, WeeklyCheckIn
from test_training_execution_boundary import training_page  # noqa: F401


def _ready(user_id, locale):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = locale
    db.session.commit()


def _qa_shot(page, name):
    directory = os.environ.get("CHECKIN_QA_DIR")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(directory) / name))


@pytest.mark.parametrize("locale,width", [("en", 390), ("tr", 1280)])
def test_double_click_pending_success_and_intentional_second_form(
        app, auth_user, training_page, monkeypatch, locale, width):
    page, traffic, _, _ = training_page
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *args, **kwargs: "One coach result")
    with app.app_context():
        _ready(auth_user.id, locale)
    copy = json.load(open(f"locales/{locale}.json", encoding="utf-8"))
    page.set_viewport_size({"width": width, "height": 850})
    page.goto("http://localhost/progress-page")
    page.locator('[data-action="openCheckin"]').first.click()
    page.locator("#ci-weight").fill("79")
    page.evaluate("""() => {
      const realFetch = window.fetch;
      window.__checkinCalls = 0;
      window.fetch = (url, options) => {
        if (url !== '/checkin') return realFetch(url, options);
        window.__checkinCalls++;
        return new Promise(resolve => {
          window.__releaseCheckin = () => resolve(realFetch(url, options));
        });
      };
    }""")
    page.locator("#checkin-btn").click()
    assert page.locator("#checkin-btn").is_disabled()
    assert page.locator("#checkin-btn").inner_text() == copy["progress.sending"]
    _qa_shot(page, f"{locale}-{width}-pending.png")
    page.evaluate("submitCheckin()")
    assert page.evaluate("window.__checkinCalls") == 1
    page.evaluate("window.__releaseCheckin()")
    page.wait_for_function("checkinSubmissionState === 'success'")
    assert page.locator("#checkin-btn").is_disabled()
    assert page.locator("#checkin-btn").inner_text() == copy["progress.checkin_submitted"]
    assert "checkin-complete" in page.locator("#checkin-btn").get_attribute("class")
    assert page.locator("#checkin-status").inner_text() == copy["progress.checkin_submitted"]
    assert page.locator("#feedback-card.visible").count() == 1
    _qa_shot(page, f"{locale}-{width}-success.png")
    page.evaluate("submitCheckin()")
    assert page.evaluate("window.__checkinCalls") == 1
    assert len([item for item in traffic if item[0] == "/checkin"]) == 1
    with app.app_context():
        assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == 1

    page.evaluate("closeCheckin()")
    page.locator('[data-action="openCheckin"]').first.click()
    assert page.locator("#checkin-btn").is_enabled()
    assert page.locator("#checkin-btn").inner_text() == copy["progress.submit_checkin"]


def test_lost_response_retries_same_token_and_closes_success(
        app, auth_user, training_page, monkeypatch):
    page, traffic, _, _ = training_page
    calls = []
    monkeypatch.setattr(tracking, "generate_checkin_feedback",
                        lambda *args, **kwargs: calls.append(1) or "One coach result")
    with app.app_context():
        _ready(auth_user.id, "en")
    page.set_viewport_size({"width": 390, "height": 850})
    page.goto("http://localhost/progress-page")
    page.locator('[data-action="openCheckin"]').first.click()
    page.locator("#ci-weight").fill("79")
    page.evaluate("""() => {
      const realFetch = window.fetch;
      window.__keys = [];
      window.fetch = async (url, options) => {
        if (url !== '/checkin') return realFetch(url, options);
        window.__keys.push(options.headers['Idempotency-Key']);
        const response = await realFetch(url, options);
        if (window.__keys.length === 1) throw new Error('response lost');
        return response;
      };
    }""")
    page.locator("#checkin-btn").click()
    page.wait_for_function("checkinSubmissionState === 'error'")
    assert page.locator("#checkin-btn").is_enabled()
    assert page.locator("#checkin-btn").inner_text() == "Retry check-in"
    assert "checkin-error" in page.locator("#checkin-status").get_attribute("class")
    assert page.locator("#feedback-card.visible").count() == 0
    if os.environ.get("CHECKIN_QA_DIR"):
        page.wait_for_timeout(3500)  # capture the persistent state after toast exits
    _qa_shot(page, "en-390-error.png")
    page.locator("#checkin-btn").click()
    page.wait_for_function("checkinSubmissionState === 'success'")
    keys = page.evaluate("window.__keys")
    assert len(keys) == 2 and keys[0] == keys[1]
    assert len([item for item in traffic if item[0] == "/checkin"]) == 2
    assert len(calls) == 1
    with app.app_context():
        assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == 1
