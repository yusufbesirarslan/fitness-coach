"""WEB-UX4-PR7 browser evidence for Progress, Today, Account and Notifications.

The browser runs the real templates and static assets while every HTTP request
is served by the authenticated Flask test client.  No external service or live
server participates.
"""
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect

from app.extensions import db
from app.models import PumpCheck, Supplement, User, WeeklyCheckIn
from app.services.notifications import notify
from app.today_presenter import (STATE_ERROR, STATE_IN_PROGRESS,
                                 STATE_SCHEDULED, TodayFacts,
                                 TodayPlanSummary)
from test_training_execution_boundary import training_page  # noqa: F401
from test_today_v2 import _seed_plan, _seed_pumpcheck_today, _week


WIDTHS = (320, 390, 768, 1024, 1366)
CAPTURE_DIR = os.environ.get("UX4_PR7_CAPTURE_DIR")
SURFACES = {
    "today": "/",
    "progress": "/progress-page",
    "account": "/edit-profile",
    "notifications": "/notifications",
}
EXPECTED_READS = {
    "today": {"/", "/notifications/unread-count", "/meal-log/today", "/water",
              "/checkin-history", "/leaderboard/reward-check"},
    "progress": {"/progress-page", "/notifications/unread-count",
                 "/api/progress/summary", "/api/progress/history",
                 "/api/progress/axis-insights", "/api/progress/physique"},
    "account": {"/edit-profile", "/notifications/unread-count"},
    "notifications": {"/notifications", "/notifications/unread-count",
                      "/notifications/data"},
}

PROBE = r"""
() => {
  const shown = el => {
    if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const main = document.querySelector('main');
  const priority = [...main.querySelectorAll('.btn-volt, [data-today-next], #notif-retry, .notif-markall')]
    .filter(shown).map(el => {
      const r = el.getBoundingClientRect();
      return {name: el.id || el.getAttribute('data-action') || el.textContent.trim(), w: r.width, h: r.height};
    });
  return {
    lang: document.documentElement.lang,
    h1: main.querySelectorAll('h1').length,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    emoji: (main.innerText.match(/\p{Extended_Pictographic}/gu) || []),
    shortPriority: priority.filter(x => x.w < 43.5 || x.h < 43.5),
    primary: main.querySelectorAll('.btn-volt').length,
  };
}
"""


def _ready(user_id, language):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = language
    db.session.commit()


def _app_paths(traffic):
    return [path for path, _, _ in traffic if not path.startswith("/static/")]


def test_pr7_surface_matrix_has_no_overflow_or_hierarchy_regression(
        app, auth_user, make_user, training_page):
    actor = make_user("averylongnotificationactorname")
    with app.app_context():
        db.session.add(Supplement(
            user_id=auth_user.id, product_name="Long-form recovery protein",
            brand="AxisAI", category="Protein", status="Active", rating_effect=4,
        ))
        notify(auth_user.id, "pump_check_comment", actor_id=actor.id,
               target_type="pump_check", target_id=44)
        db.session.commit()

    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    for language in ("en", "tr"):
        with app.app_context():
            _ready(auth_user.id, language)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for name, path in SURFACES.items():
                traffic.clear()
                page.goto("http://localhost" + path)
                page.wait_for_load_state("networkidle")
                if name == "notifications":
                    page.wait_for_selector('.notif-row, [data-notif-state="empty"]')
                facts = page.evaluate(PROBE)
                assert facts["lang"] == language, (name, width, facts)
                assert facts["h1"] == 1, (name, width, facts)
                assert facts["overflow"] is False, (name, width, facts)
                assert facts["emoji"] == [], (name, width, facts)
                assert facts["shortPriority"] == [], (name, width, facts)
                if name in {"today", "progress", "account"}:
                    assert facts["primary"] == 1, (name, width, facts)

                if CAPTURE_DIR and language == "en" and width in (390, 1366):
                    capture = Path(CAPTURE_DIR)
                    capture.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(capture / f"{name}-{width}.png"), full_page=True)

                paths = _app_paths(traffic)
                assert set(paths) == EXPECTED_READS[name], (name, width, paths)
                assert len(paths) == len(set(paths)), (name, width, paths)

    assert errors == []


def test_notification_load_error_retries_and_read_failure_is_visible(
        app, auth_user, make_user, training_page):
    actor = make_user("notificationactor")
    with app.app_context():
        notify(auth_user.id, "friend_request", actor_id=actor.id,
               target_type="friendship", target_id=actor.id)
        db.session.commit()

    page, _, _, _ = training_page
    fail_load = {"value": True}

    def data_route(route):
        if fail_load["value"]:
            route.fulfill(status=503, content_type="application/json", body='{"error":"unavailable"}')
        else:
            route.fallback()

    page.route("**/notifications/data*", data_route)
    page.goto("http://localhost/notifications")
    error = page.locator('[data-notif-state="error"]')
    expect(error).to_be_visible()
    expect(page.locator("#notif-retry")).to_be_visible()
    assert page.locator(".loading-text").count() == 0

    fail_load["value"] = False
    page.locator("#notif-retry").click()
    expect(page.locator(".notif-row")).to_have_count(1)
    expect(page.locator('[data-notif-state="ready"]')).to_be_visible()

    def read_route(route):
        route.fulfill(status=503, content_type="application/json", body='{"error":"unavailable"}')

    page.route("**/notifications/read", read_route)
    row = page.locator(".notif-row")
    row.click()
    expect(page.locator(".toast-error")).to_be_visible()
    expect(row).to_have_class(re.compile(r"\bunread\b"))
    expect(row.locator(".notif-dot")).to_have_count(1)


def test_today_settled_states_keep_a_reachable_next_step(
        app, auth_user, training_page):
    from app.models import TrainingPlan

    with app.app_context():
        _ready(auth_user.id, "en")
        _seed_plan(auth_user.id, _week("dinlenme"))

    page, _, _, _ = training_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://localhost/")
    rest = page.locator('[data-today-state="rest_day"] [data-today-next]')
    expect(rest).to_be_visible()
    expect(rest).to_have_attribute("href", "/training")
    box = rest.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44

    with app.app_context():
        plan = TrainingPlan.query.filter_by(user_id=auth_user.id).one()
        plan.plan_data = _week("antrenman")
        db.session.commit()
        _seed_pumpcheck_today(auth_user.id)

    page.goto("http://localhost/")
    completed = page.locator('[data-today-state="completed"] [data-today-next]')
    expect(completed).to_be_visible()
    expect(completed).to_have_attribute("href", "/progress-page")
    box = completed.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44


def test_today_browser_covers_scheduled_in_progress_and_degraded(
        app, auth_user, training_page, monkeypatch):
    import app.blueprints.tracking as tracking_routes

    plan = TodayPlanSummary(focus="Upper body", duration_min=45,
                            exercise_count=3)
    with app.app_context():
        _ready(auth_user.id, "en")
    page, _, _, _ = training_page
    page.set_viewport_size({"width": 390, "height": 844})

    cases = (
        (TodayFacts(True, True, False, STATE_SCHEDULED, "start", plan),
         STATE_SCHEDULED, True),
        (TodayFacts(True, True, False, STATE_IN_PROGRESS, "resume", plan),
         STATE_IN_PROGRESS, True),
        (TodayFacts(False, False, False), STATE_ERROR, False),
    )
    for facts, state, has_primary in cases:
        monkeypatch.setattr(tracking_routes, "gather_today_facts",
                            lambda _user_id, value=facts: value)
        page.goto("http://localhost/")
        root = page.locator("#today-page")
        expect(root).to_have_attribute("data-today-state", state)
        expect(root.locator("[data-today-primary]")).to_have_count(
            1 if has_primary else 0)


def test_progress_browser_covers_sparse_populated_and_pump_check_history(
        app, auth_user, training_page):
    page, _, _, _ = training_page
    page.set_viewport_size({"width": 390, "height": 844})
    now = datetime.utcnow()

    with app.app_context():
        user = db.session.get(User, auth_user.id)
        user.weight = 81.5
        db.session.add(WeeklyCheckIn(
            user_id=auth_user.id, weight=81.5, yogunluk=3, fatigue=2,
            uyku_kalitesi=4, beslenme_uyumu=4, created_at=now,
        ))
        db.session.add(PumpCheck(
            user_id=auth_user.id, captured_at=now, body_region="full_body",
            public_id="pr7-check-one", analysis_status="completed",
            analysis={"quality": "good"},
        ))
        db.session.commit()

    page.goto("http://localhost/progress-page")
    expect(page.locator("#wc-body [data-slot=value]")).to_contain_text("81.5")
    expect(page.locator("#physique-body .pp-status")).to_have_count(1)
    expect(page.locator("#physique-body .pp-strip figure")).to_have_count(1)

    with app.app_context():
        db.session.add(WeeklyCheckIn(
            user_id=auth_user.id, weight=82.0, yogunluk=4, fatigue=2,
            uyku_kalitesi=4, beslenme_uyumu=3,
            created_at=now - timedelta(days=7),
        ))
        db.session.add(PumpCheck(
            user_id=auth_user.id, captured_at=now - timedelta(days=7),
            body_region="full_body", public_id="pr7-check-two",
            analysis_status="completed", analysis={"quality": "good"},
        ))
        db.session.commit()

    page.goto("http://localhost/progress-page")
    expect(page.locator("#history-list .hist-item")).to_have_count(2)
    expect(page.locator("#physique-body .pp-strip figure")).to_have_count(2)


def test_account_browser_covers_avatar_premium_and_keyboard_choices(
        app, auth_user, training_page):
    with app.app_context():
        user = db.session.get(User, auth_user.id)
        user.profile_picture = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
        user.is_premium = True
        db.session.commit()

    page, _, _, _ = training_page
    page.goto("http://localhost/edit-profile")
    expect(page.locator("#avatar-display img")).to_have_count(1)
    expect(page.locator(".pf-membership .badge-success")).to_be_visible()
    expect(page.locator(".pf-upgrade")).to_have_count(0)

    page.locator('[data-action="openEditSheet"]').click()
    goal = page.locator('.pf-goal .pf-choice').nth(1)
    goal.focus()
    goal.press("Enter")
    expect(goal).to_have_class(re.compile(r"\bselected\b"))


def test_notifications_browser_covers_empty_and_mixed_read_state(
        app, auth_user, make_user, training_page):
    page, _, _, _ = training_page
    page.goto("http://localhost/notifications")
    expect(page.locator('[data-notif-state="empty"]')).to_be_visible()

    actor = make_user("mixednotificationactor")
    with app.app_context():
        read = notify(auth_user.id, "friend_request", actor_id=actor.id,
                      target_type="friendship", target_id=actor.id)
        unread = notify(auth_user.id, "friend_accept", actor_id=actor.id,
                        target_type="friendship", target_id=actor.id + 1)
        assert read is not None and unread is not None
        read.is_read = True
        db.session.commit()

    page.goto("http://localhost/notifications")
    expect(page.locator(".notif-row")).to_have_count(2)
    expect(page.locator(".notif-row.unread")).to_have_count(1)
    expect(page.locator(".notif-row:not(.unread)")).to_have_count(1)
