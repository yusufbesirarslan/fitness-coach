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
                if name == "account":
                    pressed = page.eval_on_selector_all(
                        '.hub-lang-opt, .pf-choice[data-action="setLang"]',
                        "els => els.map(e => [JSON.parse(e.dataset.args)[0], e.getAttribute('aria-pressed')])")
                    assert sorted(pressed) == sorted([[language, "true"], [language, "true"],
                                                      ["tr" if language == "en" else "en", "false"],
                                                      ["tr" if language == "en" else "en", "false"]]), (width, pressed)

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
    # The 44px invariant is about settled layout: opt into the product's
    # reduced-motion path so nav.css's page-enter translateY cannot be mid-flight
    # when bounding_box() samples the target.
    page.emulate_media(reduced_motion="reduce")
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


# ── Review-fix regressions: notification failure, pending and a11y states ──

NOTIF_HOOKS = r"""
(() => {
  window.__readCalls = 0;
  window.__dataCalls = [];
  window.__navs = [];
  const orig = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = String(input && input.url ? input.url : input);
    if (url.indexOf('/notifications/read') !== -1) window.__readCalls += 1;
    const m = url.match(/\/notifications\/data\?before_id=(\d+)/);
    if (m) window.__dataCalls.push(Number(m[1]));
    return orig(input, init);
  };
  if (window.navigation) {
    window.navigation.addEventListener('navigate', e => window.__navs.push(new URL(e.destination.url).pathname));
  }
})();
"""

NOT_LIVE = r"""
() => {
  const rows = [...document.querySelectorAll('.notif-row')];
  const live = el => el.hasAttribute('aria-live') ||
      ['status', 'log', 'alert', 'marquee', 'timer'].includes(el.getAttribute('role'));
  return rows.length > 0 && rows.every(row => {
    for (let el = row; el; el = el.parentElement) if (live(el)) return false;
    return true;
  });
}
"""

SCROLL_END = "() => { window.scrollTo(0, document.body.scrollHeight); window.dispatchEvent(new Event('scroll')); }"
EMPTY_PAGE = '{"notifications":[],"hasMore":false,"nextBeforeId":null,"unreadCount":0}'


def _seed_notifications(app, user_id, actor_id, count, read_every=0):
    """Distinct targets -> notify() never dedupes. Returns ids newest first."""
    from app.models import Notification

    with app.app_context():
        for i in range(count):
            n = notify(user_id, "friend_request", actor_id=actor_id,
                       target_type="friendship", target_id=10_000 + i)
            assert n is not None
            if read_every and i % read_every == 0:
                n.is_read = True
        db.session.commit()
        return sorted((n.id for n in Notification.query.filter_by(user_id=user_id)),
                      reverse=True)


def _notif_harness(page):
    """Deterministic controls for /notifications/data and /notifications/read.

    Modes: 'pass' (real Flask client), 'fail' (503), 'empty' (data only),
    'hold' (the request stays pending until the test releases it).
    """
    ctl = {"data": "pass", "data_first_page": "pass", "read": "pass",
           "held_data": [], "held_read": []}

    def data_route(route):
        first = "before_id=0&" in route.request.url
        mode = ctl["data_first_page"] if first else ctl["data"]
        if mode == "fail":
            route.fulfill(status=503, content_type="application/json", body='{"error":"unavailable"}')
        elif mode == "empty":
            route.fulfill(status=200, content_type="application/json", body=EMPTY_PAGE)
        elif mode == "hold":
            ctl["held_data"].append(route)
        else:
            route.fallback()

    def read_route(route):
        if ctl["read"] == "fail":
            route.fulfill(status=503, content_type="application/json", body='{"error":"unavailable"}')
        elif ctl["read"] == "hold":
            ctl["held_read"].append(route)
        else:
            route.fallback()

    page.add_init_script(NOTIF_HOOKS)
    page.route("**/notifications/data*", data_route)
    page.route("**/notifications/read", read_route)
    return ctl


def _wait_held(page, held, count):
    """Bounded poll until a routed request is parked (a condition, not a timed assertion)."""
    for _ in range(250):
        if len(held) >= count:
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"expected {count} held request(s), saw {len(held)}")


def _row_ids(page):
    return [int(x) for x in page.eval_on_selector_all(".notif-row", "els => els.map(e => e.dataset.id)")]


def test_notification_page_n_failure_keeps_rows_and_retries_the_same_boundary(
        app, auth_user, make_user, training_page):
    actor = make_user("paginationactor")
    ids = _seed_notifications(app, auth_user.id, actor.id, 45)
    with app.app_context():
        _ready(auth_user.id, "en")
    page, _, _, _ = training_page
    ctl = _notif_harness(page)
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto("http://localhost/notifications")
    expect(page.locator(".notif-row")).to_have_count(20)
    assert page.evaluate(NOT_LIVE) is True
    assert page.locator("#notif-list").get_attribute("aria-live") is None

    # CASE B: page 2 fails -> page 1 stays, one inline error, no full-page error.
    ctl["data"] = "fail"
    page.evaluate(SCROLL_END)
    more = page.locator('#notif-more[data-notif-more="error"]')
    expect(more).to_be_visible()
    expect(more.locator('[role="alert"]')).to_have_count(1)
    assert _row_ids(page) == ids[:20]
    expect(page.locator('[data-notif-state="error"]')).to_have_count(0)
    expect(page.locator("#notif-retry")).to_have_count(0)
    expect(page.locator("#notif-list")).to_have_attribute("data-notif-state", "ready")

    # CASE D: repeated scroll triggers while failed -> no later page, no second affordance.
    for _ in range(3):
        page.evaluate(SCROLL_END)
    expect(page.locator("#notif-page-retry")).to_have_count(1)
    boundary = ids[19]
    assert page.evaluate("window.__dataCalls") == [0, boundary]

    # CASE C: retry the exact failed boundary; rows append once and the error clears.
    ctl["data"] = "pass"
    page.locator("#notif-page-retry").click()
    expect(page.locator(".notif-row")).to_have_count(40)
    expect(page.locator("#notif-more")).to_be_hidden()
    expect(page.locator("#notif-page-retry")).to_have_count(0)
    assert _row_ids(page) == ids[:40]
    assert page.evaluate("window.__dataCalls") == [0, boundary, boundary]
    assert page.evaluate("document.activeElement.dataset.id") == str(ids[20])
    expect(page.locator("#notif-status")).to_have_text("20 more notifications loaded.")
    assert page.evaluate(NOT_LIVE) is True

    page.evaluate(SCROLL_END)
    expect(page.locator(".notif-row")).to_have_count(45)
    assert _row_ids(page) == ids
    assert page.evaluate("window.__dataCalls") == [0, boundary, boundary, ids[39]]
    expect(page.locator("#notif-status")).to_have_text("5 more notifications loaded.")


def test_notification_first_load_failure_keeps_full_error_and_retries_page_one(
        app, auth_user, make_user, training_page):
    actor = make_user("firstloadactor")
    ids = _seed_notifications(app, auth_user.id, actor.id, 3)
    page, _, _, _ = training_page
    ctl = _notif_harness(page)
    ctl["data_first_page"] = "fail"

    # CASE A
    page.goto("http://localhost/notifications")
    expect(page.locator('[data-notif-state="error"] [role="alert"]')).to_be_visible()
    expect(page.locator("#notif-more")).to_be_hidden()
    page.evaluate(SCROLL_END)
    assert page.evaluate("window.__dataCalls") == [0]
    ctl["data_first_page"] = "pass"
    page.locator("#notif-retry").click()
    expect(page.locator(".notif-row")).to_have_count(3)
    assert _row_ids(page) == ids
    assert page.evaluate("window.__dataCalls") == [0, 0]


def test_notification_pending_mark_read_blocks_repeat_activation_and_false_navigation(
        app, auth_user, make_user, training_page):
    actor = make_user("pendingreadactor")
    _seed_notifications(app, auth_user.id, actor.id, 2)
    page, _, _, _ = training_page
    ctl = _notif_harness(page)
    page.goto("http://localhost/notifications")
    expect(page.locator(".notif-row.unread")).to_have_count(2)
    row = page.locator(".notif-row").first
    other = page.locator(".notif-row").nth(1)

    # CASE E: first activation -> one request, pending state exposed.
    ctl["read"] = "hold"
    row.click()
    expect(row).to_have_attribute("aria-busy", "true")
    assert page.evaluate("window.__readCalls") == 1
    _wait_held(page, ctl["held_read"], 1)

    # CASE F: pointer + keyboard repeat on the SAME row -> no navigation, no duplicate.
    row.click()
    row.focus()
    row.press("Enter")
    assert page.evaluate("window.__readCalls") == 1
    assert page.evaluate("window.__navs") == []
    # the guard is scoped to that notification only
    expect(other).not_to_have_attribute("aria-busy", "true")
    expect(other).to_have_class(re.compile(r"\bunread\b"))

    # CASE H: failure restores unread, stays put, is announced, and allows retry.
    ctl["held_read"].pop().fulfill(status=503, content_type="application/json",
                                   body='{"error":"unavailable"}')
    expect(page.locator("#toast-wrap[aria-live] .toast-error")).to_be_visible()
    expect(row).to_have_class(re.compile(r"\bunread\b"))
    expect(row.locator(".notif-dot")).to_have_count(1)
    expect(row).not_to_have_attribute("aria-busy", "true")
    assert page.evaluate("window.__navs") == []
    assert page.url.endswith("/notifications")

    # CASE G: retry -> navigation only after the original request succeeds.
    row.click()
    assert page.evaluate("window.__readCalls") == 2
    _wait_held(page, ctl["held_read"], 1)
    row.click()
    assert page.evaluate("window.__readCalls") == 2
    assert page.evaluate("window.__navs") == []
    ctl["held_read"].pop().fallback()
    page.wait_for_url("**/friends")


def test_account_goal_and_language_choices_expose_selected_state(
        app, auth_user, training_page):
    for language in ("en", "tr"):
        with app.app_context():
            _ready(auth_user.id, language)
            user = db.session.get(User, auth_user.id)
            user.goal = "kilo verme"
            db.session.commit()
        page, _, _, _ = training_page
        page.goto("http://localhost/edit-profile")
        other = "tr" if language == "en" else "en"
        for sel in (".hub-lang-opt", ".pf-choice"):
            expect(page.locator(f'{sel}[data-args=\'["{language}"]\']')).to_have_attribute("aria-pressed", "true")
            expect(page.locator(f'{sel}[data-args=\'["{other}"]\']')).to_have_attribute("aria-pressed", "false")

        page.locator('[data-action="openEditSheet"]').click()
        loss, gain = page.locator(".pf-goal .pf-choice").nth(0), page.locator(".pf-goal .pf-choice").nth(1)
        expect(loss).to_have_attribute("aria-pressed", "true")
        expect(gain).to_have_attribute("aria-pressed", "false")
        expect(page.locator('.pf-goal [role="group"]')).to_have_attribute("aria-labelledby", "pf-goal-label")
        gain.focus()
        gain.press("Enter")
        expect(gain).to_have_attribute("aria-pressed", "true")
        expect(loss).to_have_attribute("aria-pressed", "false")
        assert page.evaluate("document.activeElement === document.querySelectorAll('.pf-goal .pf-choice')[1]")


NOTIF_MATRIX_PROBE = r"""
() => {
  const shown = el => el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const small = [...document.querySelectorAll('#notif-retry, #notif-page-retry, .notif-markall')]
    .filter(shown).map(el => el.getBoundingClientRect()).filter(r => r.width < 43.5 || r.height < 43.5).length;
  return {
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    small,
    liveList: document.getElementById('notif-list').hasAttribute('aria-live'),
  };
}
"""


def test_notification_state_matrix_across_widths_and_languages(
        app, auth_user, make_user, training_page):
    actor = make_user("matrixnotificationactorwithalongname")
    ids = _seed_notifications(app, auth_user.id, actor.id, 25, read_every=3)
    page, _, _, _ = training_page
    ctl = _notif_harness(page)
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    def check(state, lang, width):
        facts = page.evaluate(NOTIF_MATRIX_PROBE)
        assert facts == {"overflow": False, "small": 0, "liveList": False}, (state, lang, width, facts)

    for language in ("en", "tr"):
        with app.app_context():
            _ready(auth_user.id, language)
            from app.models import Notification
            for n in Notification.query.filter_by(user_id=auth_user.id):
                n.is_read = (ids.index(n.id) % 3 == 0)
            db.session.commit()
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 700})
            for key in ("data", "data_first_page", "read"):
                ctl[key] = "pass"

            # initial loading (page 1 held) -> released populated + mixed read/unread
            ctl["data_first_page"] = "hold"
            page.goto("http://localhost/notifications")
            _wait_held(page, ctl["held_data"], 1)
            expect(page.locator('#notif-list[data-notif-state="loading"] .loading-text')).to_be_visible()
            check("loading", language, width)
            ctl["data_first_page"] = "pass"
            ctl["held_data"].pop().fallback()
            expect(page.locator(".notif-row")).to_have_count(20)
            expect(page.locator(".notif-row.unread")).not_to_have_count(0)
            expect(page.locator(".notif-row:not(.unread)")).not_to_have_count(0)
            check("populated", language, width)

            # pagination failure -> retry -> success
            ctl["data"] = "fail"
            page.evaluate(SCROLL_END)
            expect(page.locator("#notif-page-retry")).to_be_visible()
            assert page.locator(".notif-row").count() == 20
            check("page-failure", language, width)
            ctl["data"] = "pass"
            page.locator("#notif-page-retry").click()
            expect(page.locator(".notif-row")).to_have_count(25)
            expect(page.locator("#notif-more")).to_be_hidden()
            assert _row_ids(page) == ids
            check("page-retry", language, width)

            # mark-read pending/repeat -> failure -> success navigation
            row_id = page.locator(".notif-row.unread").first.get_attribute("data-id")
            row = page.locator(f'.notif-row[data-id="{row_id}"]')
            row.scroll_into_view_if_needed()
            ctl["read"] = "hold"
            row.click()
            expect(row).to_have_attribute("aria-busy", "true")
            _wait_held(page, ctl["held_read"], 1)
            row.click()
            check("read-pending", language, width)
            assert page.evaluate("window.__navs") == []
            ctl["held_read"].pop().fulfill(status=503, content_type="application/json", body="{}")
            expect(page.locator(".toast-error").last).to_be_visible()
            expect(row).to_have_class(re.compile(r"\bunread\b"))
            check("read-failure", language, width)
            ctl["read"] = "pass"
            row.click()
            page.wait_for_url(re.compile(r"/friends$"))
            # restore the read row for the next cell
            with app.app_context():
                from app.models import Notification
                for n in Notification.query.filter_by(user_id=auth_user.id):
                    n.is_read = (ids.index(n.id) % 3 == 0)
                db.session.commit()

            # initial failure, then empty
            ctl["data_first_page"] = "fail"
            page.goto("http://localhost/notifications")
            expect(page.locator('[data-notif-state="error"] #notif-retry')).to_be_visible()
            check("initial-failure", language, width)
            ctl["data_first_page"] = "empty"
            page.locator("#notif-retry").click()
            expect(page.locator('[data-notif-state="empty"]')).to_be_visible()
            expect(page.locator("#notif-status")).not_to_be_empty()
            check("empty", language, width)

    assert errors == []
