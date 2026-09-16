"""WEB-UX4-PR4 — the Plan first run, measured in a real browser.

F-07 is a layout and journey defect, so it is proven where it happens: Chromium
lays out the real ``plan.html`` with the real scripts, the real dispatcher and
real Flask responses. Only the provider-backed generator is stubbed (a hermetic
test may not contact a model). Browser HTTP goes through the authenticated test
client, so cookies, CSRF and canonical state are genuine, and every network
assertion counts what the page actually sent.

The persistence chain is NOT PR4's: generate → proposal → canonical freshness
read → save → canonical refresh is owned by ``training_plan_management.js``.
These tests prove the new presentation leaves that chain, its payload and its
defaults exactly as they were.
"""
from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.i18n import t
from app.models import TrainingPlan, User
from test_training_execution_boundary import training_page  # noqa: F401  (fixture)
from test_ux3_pr3_training_management_browser import (  # noqa: F401  (fixtures)
    PLAN_URL, generator, plan_v2, profile_ready,
)

# The payload an untouched first-run form submits on the pre-PR4 baseline
# (2e5ebe5), captured from the real browser request. `sure` is 30, not the 45
# that `plan_training_manage.js` DEFAULTS, legacy Training and the server-side
# preference fallback use: the rendered duration <select> has no preselected
# option, so its first option wins and the JS fallback never applies. That
# mismatch is recorded as a separate contract-consistency finding; PR4 is
# presentation-only and must keep sending exactly what users send today.
UNTOUCHED_PAYLOAD = {
    "gun_sayisi": 3, "ekipman": "spor_salonu", "odak": "tum_vucut", "sure": 30,
    "kardiyo_tipi": "yok", "kardiyo_gun": 0, "kardiyo_sure": 20,
    "kardiyo_yogunluk": "orta", "antrenman_tarzi": "genel", "odak_hedef": "genel",
    "injuries": "",
}
PAYLOAD_KEYS = set(UNTOUCHED_PAYLOAD)
ESSENTIAL = ["odak_hedef", "ekipman", "gun_sayisi", "sure"]
OPTIONAL = ["antrenman_tarzi", "odak", "kardiyo_tipi"]
# One non-default choice per visible preference, to prove each control moves
# its own canonical key and nothing else.
CHANGE = {
    "odak_hedef": ("guc", "guc"), "gun_sayisi": ("5", 5), "ekipman": ("ev", "ev"),
    "sure": ("60", 60), "antrenman_tarzi": ("crossfit", "crossfit"),
    "odak": ("core", "core"), "kardiyo_tipi": ("kosu", "kosu"),
}
MOBILE = (320, 390)
MATRIX = (320, 390, 768, 1024, 1366)
OVERFLOW_TOLERANCE = 1
STATIC_PREFIXES = ("/static/",)


# ── harness ────────────────────────────────────────────────────────────────

@pytest.fixture
def first_run(app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    page.emulate_media(reduced_motion="reduce")
    return page, traffic


def _set_language(app, user_id, language):
    with app.app_context():
        db.session.get(User, user_id).language = language
        db.session.commit()


def _open(page, width=390, height=844):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(PLAN_URL)
    expect(page.locator('[data-manage-state="create"]')).to_be_visible()
    page.evaluate("document.fonts ? document.fonts.ready : null")


def _api(traffic):
    return [(path, body) for path, body, _ in traffic
            if not path.startswith(STATIC_PREFIXES)]


def _capture_generate_body(page):
    """Record the POST /training-plan body without contacting the server."""
    bodies = []

    def handler(route):
        if route.request.method == "POST":
            bodies.append(json.loads(route.request.post_data))
            route.fulfill(status=503, content_type="application/json",
                          body='{"error": "unavailable"}')
        else:
            route.fallback()

    page.route("**/training-plan", handler)
    return bodies


def _box(page, selector):
    return page.locator(selector).first.bounding_box()


def _usable_bottom(page):
    """The bottom edge a control may reach before the bottom navigation covers it."""
    return page.evaluate("""() => {
        const bar = document.querySelector('.action-bar');
        const shown = bar && getComputedStyle(bar).display !== 'none';
        return innerHeight - (shown ? bar.getBoundingClientRect().height : 0);
    }""")


def _topmost_is(page, selector):
    """True when no part of the element is painted over by anything else.

    Probed per line box (`getClientRects`), not at the bounding-box centre: an
    inline element that wraps (the setup link does at 320px, and on CI's wider
    Linux fonts at 390px) has a bounding box whose centre lies BETWEEN its line
    fragments, on the surrounding paragraph — a false "covered" reading."""
    return page.evaluate("""(sel) => {
        const el = document.querySelector(sel);
        const rects = [...el.getClientRects()].filter(r => r.width > 0 && r.height > 0);
        return rects.length > 0 && rects.every(r => {
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return !!hit && (hit === el || el.contains(hit));
        });
    }""", selector)


_WALK = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
  return {field: el.getAttribute('data-plan-field'), gen: el.hasAttribute('data-plan-manage-generate'),
          summary: el.tagName === 'SUMMARY', manage: !!el.closest('[data-plan-manage]'),
          label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40),
          top: r.top, bottom: r.bottom,
          actionTop: (() => { const a = document.querySelector('.plan-manage-action');
                              return a ? a.getBoundingClientRect().top : null; })(),
          visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.opacity !== '0',
          ring: (parseFloat(cs.outlineWidth) >= 2 && cs.outlineStyle !== 'none') || cs.boxShadow !== 'none'};
}"""


def _tab_to(page, predicate, limit=40):
    stops = []
    # `blur()` does not move Chromium's sequential-focus starting point (a prior
    # click on the summary would silently start the walk there). Clicking the
    # non-interactive page heading resets it to the top of the content.
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); scrollTo(0, 0); }")
    page.locator("h1").click()
    for _ in range(limit):
        page.keyboard.press("Tab")
        record = page.evaluate(_WALK)
        if record is None:
            continue
        stops.append(record)
        if predicate(record):
            return stops
    raise AssertionError(f"target never reached; walked {[s['field'] or s['label'] for s in stops]}")


# ══════════════════════════════════════════════════════════════════════════
# Payload — same keys, same defaults, disclosure is presentation only
# ══════════════════════════════════════════════════════════════════════════

def test_an_untouched_first_run_submits_the_exact_existing_payload(first_run):
    page, traffic = first_run
    _open(page)
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    bodies = [json.loads(body) for path, body in _api(traffic) if path == "/training-plan"]
    assert bodies == [UNTOUCHED_PAYLOAD]
    assert set(bodies[0]) == PAYLOAD_KEYS


def test_opening_and_closing_the_refinement_changes_no_payload_value(first_run):
    page, _ = first_run
    _open(page)
    bodies = _capture_generate_body(page)
    summary = page.locator('[data-manage-state="create"] details > summary')
    summary.click()
    summary.focus()
    page.keyboard.press("Enter")          # close by keyboard
    page.keyboard.press(" ")              # open by keyboard
    summary.click()                       # close by pointer
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-msg]")).to_be_visible()
    assert bodies == [UNTOUCHED_PAYLOAD]


@pytest.mark.parametrize("field", ESSENTIAL + OPTIONAL + ["injuries"])
def test_each_preference_moves_only_its_own_key(first_run, field):
    page, _ = first_run
    _open(page)
    bodies = _capture_generate_body(page)
    if field in OPTIONAL:
        page.locator('[data-manage-state="create"] details > summary').click()
    control = page.locator(f'[data-plan-field="{field}"]')
    if field == "injuries":
        control.fill("  sol diz  ")
        expected = "sol diz"
    else:
        raw, expected = CHANGE[field]
        control.select_option(raw)
    if field in OPTIONAL:
        # Collapsing again must keep the choice the user made.
        page.locator('[data-manage-state="create"] details > summary').click()
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-msg]")).to_be_visible()
    assert len(bodies) == 1
    changed = {k for k in PAYLOAD_KEYS if bodies[0][k] != UNTOUCHED_PAYLOAD[k]}
    assert set(bodies[0]) == PAYLOAD_KEYS
    assert changed == {field}, changed
    assert bodies[0][field] == expected


# ══════════════════════════════════════════════════════════════════════════
# First screen — where am I, what happens, what matters, where is the action
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("width", MOBILE)
def test_the_first_screen_orients_and_offers_the_action_without_scrolling(
        app, first_run, profile_ready, width, language):
    page, _ = first_run
    _set_language(app, profile_ready.id, language)
    _open(page, width)
    usable = _usable_bottom(page)
    with app.test_request_context():
        outcome = t("plan.create.outcome", locale=language)
        destination = t("nav.plan", locale=language)
    expect(page.locator("h1")).to_have_text(destination)
    outcome_box = page.get_by_text(outcome, exact=True).bounding_box()
    assert outcome_box and outcome_box["y"] + outcome_box["height"] <= usable, outcome_box
    action = _box(page, "[data-plan-manage-generate]")
    assert action["y"] >= 0 and action["y"] + action["height"] <= usable + OVERFLOW_TOLERANCE, (
        f"{language}@{width}: the only action is below the fold ({action}, usable {usable})")
    assert _topmost_is(page, "[data-plan-manage-generate]")
    assert action["height"] >= 44
    # Optional refinement is not on the first screen as configuration.
    for name in OPTIONAL:
        expect(page.locator(f'[data-plan-field="{name}"]')).to_be_hidden()
    for name in ESSENTIAL + ["injuries"]:
        expect(page.locator(f'[data-plan-field="{name}"]')).to_be_visible()


@pytest.mark.parametrize("width", MOBILE)
def test_the_action_stays_reachable_while_the_form_scrolls(first_run, width):
    page, _ = first_run
    _open(page, width, height=640)
    page.locator('[data-manage-state="create"] details > summary').click()
    usable = _usable_bottom(page)
    for y in (0, 150, 300, 450):
        page.evaluate(f"scrollTo(0, {y})")
        box = _box(page, "[data-plan-manage-generate]")
        assert box["y"] >= 0 and box["y"] + box["height"] <= usable + OVERFLOW_TOLERANCE, (y, box)
        assert _topmost_is(page, "[data-plan-manage-generate]"), y


@pytest.mark.parametrize("width", MOBILE)
def test_no_focused_field_is_hidden_under_the_action(first_run, width):
    """Each stop is measured the moment it receives focus, i.e. after the
    browser scrolled it into view — the geometry a keyboard user actually gets."""
    page, _ = first_run
    _open(page, width, height=640)
    page.locator('[data-manage-state="create"] details > summary').click()
    stops = _tab_to(page, lambda s: s["gen"])
    measured = [s for s in stops if s["manage"] and not s["gen"]]
    assert {s["field"] for s in measured if s["field"]} == set(ESSENTIAL + OPTIONAL + ["injuries"])
    for stop in measured:
        assert stop["bottom"] <= stop["actionTop"] + OVERFLOW_TOLERANCE, (
            f"@{width}: {stop['field'] or stop['label']} is covered by the action: {stop}")


# ══════════════════════════════════════════════════════════════════════════
# Keyboard — logical order, native disclosure, no hidden stops, visible focus
# ══════════════════════════════════════════════════════════════════════════

def test_collapsed_refinement_is_not_in_the_tab_order_and_the_action_comes_early(first_run):
    page, _ = first_run
    _open(page)
    stops = _tab_to(page, lambda s: s["gen"])
    fields = [s["field"] for s in stops if s["field"]]
    assert fields == ESSENTIAL + ["injuries"], fields
    assert sum(1 for s in stops if s["summary"]) == 1
    assert all(s["visible"] for s in stops), [s for s in stops if not s["visible"]]
    manage = [s for s in stops if s["manage"]]
    assert all(s["ring"] for s in manage), [s for s in manage if not s["ring"]]
    assert page.evaluate("document.querySelectorAll('[tabindex]:not([tabindex=\"-1\"]):not([tabindex=\"0\"])').length") == 0


def test_the_refinement_opens_by_keyboard_and_every_field_is_reachable(first_run):
    page, _ = first_run
    _open(page)
    _tab_to(page, lambda s: s["summary"])
    page.keyboard.press("Enter")
    expect(page.locator('[data-manage-state="create"] details')).to_have_attribute("open", "")
    walked = [s["field"] for s in _tab_to(page, lambda s: s["gen"]) if s["field"]]
    assert walked == ESSENTIAL + OPTIONAL + ["injuries"], walked
    page.keyboard.press("Enter")          # Enter on the action generates
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    # Focus lands on the proposal, and its controls follow logically.
    assert page.evaluate("document.activeElement.hasAttribute('data-plan-manage-proposal-title')")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.hasAttribute('data-plan-manage-confirm')")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.getAttribute('data-action')") == "planManageClose"


# ══════════════════════════════════════════════════════════════════════════
# Truthful states — pending, failure, setup, proposal — never under the action
# ══════════════════════════════════════════════════════════════════════════

def test_the_action_cannot_be_pressed_twice_while_generating(first_run):
    page, traffic = first_run
    _open(page)
    held = []
    page.route("**/training-plan", lambda route: held.append(route)
               if route.request.method == "POST" else route.fallback())
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-generate]")).to_be_disabled()
    expect(page.locator("[data-plan-manage-msg]")).to_be_visible()
    assert _topmost_is(page, "[data-plan-manage-msg]")
    page.locator("[data-plan-manage-generate]").click(force=True)
    page.wait_for_timeout(100)
    assert len(held) == 1
    held[0].fulfill(status=500, content_type="application/json", body='{"error": "x"}')
    expect(page.locator("[data-plan-manage-generate]")).to_be_enabled()
    msg = page.locator("[data-plan-manage-msg]")
    expect(msg).to_have_class("plan-manage-msg plan-manage-msg--error")
    assert _topmost_is(page, "[data-plan-manage-msg]")
    box = msg.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= _usable_bottom(page)
    expect(page.locator("[data-plan-manage-proposal]")).to_be_hidden()
    expect(page.locator('[data-plan-state="active_plan"]')).to_have_count(0)
    assert not any(path == "/training-plan/save" for path, _ in _api(traffic))


@pytest.mark.parametrize("width", MOBILE)
def test_profile_setup_required_is_shown_with_its_link_beside_the_action(
        app, plan_v2, auth_user, generator, training_page, width):
    page, traffic, _, _ = training_page
    page.emulate_media(reduced_motion="reduce")
    with app.app_context():
        db.session.get(User, auth_user.id).profile_complete = True
        db.session.commit()
    _open(page, width)
    page.locator("[data-plan-manage-generate]").click()
    link = page.locator('[data-plan-manage-msg] a[href="/setup"]')
    expect(link).to_be_visible()
    with app.test_request_context():
        expect(link).to_have_text(t("plan.create.go_setup", locale="tr"))
    assert _topmost_is(page, '[data-plan-manage-msg] a[href="/setup"]')
    for fragment in link.evaluate("a => [...a.getClientRects()].map(r => r.bottom)"):
        assert fragment <= _usable_bottom(page) + OVERFLOW_TOLERANCE, (width, fragment)
    assert generator["calls"] == 0
    assert [p for p, _ in _api(traffic) if p.startswith("/training-plan")] == ["/training-plan"]


@pytest.mark.parametrize("width", (390, 1366))
def test_the_action_is_not_pinned_over_the_proposal(first_run, width):
    page, _ = first_run
    _open(page, width)
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    page.locator("[data-plan-manage-confirm]").scroll_into_view_if_needed()
    assert _topmost_is(page, "[data-plan-manage-confirm]")
    assert _topmost_is(page, '[data-action="planManageClose"]')
    action = _box(page, ".plan-manage-action")
    proposal = _box(page, "[data-plan-manage-proposal]")
    overlap_top = max(action["y"], proposal["y"])
    overlap_bottom = min(action["y"] + action["height"], proposal["y"] + proposal["height"])
    assert overlap_bottom <= overlap_top + OVERFLOW_TOLERANCE, (action, proposal)


# ══════════════════════════════════════════════════════════════════════════
# Network — the same requests in the same order, nothing added
# ══════════════════════════════════════════════════════════════════════════

def test_first_run_network_contract_is_unchanged(app, first_run, profile_ready):
    page, traffic = first_run
    _open(page)
    loaded = [path for path, _ in _api(traffic)]
    assert loaded == ["/training", "/notifications/unread-count"], loaded

    traffic.clear()
    summary = page.locator('[data-manage-state="create"] details > summary')
    summary.click()
    page.locator('[data-plan-field="kardiyo_tipi"]').select_option("kosu")
    page.locator('[data-plan-field="kardiyo_tipi"]').select_option("yok")
    page.locator('[data-plan-field="sure"]').focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Escape")
    summary.click()
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(300)
    assert _api(traffic) == [], "presentation interactions issued requests"

    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    page.wait_for_timeout(300)
    assert [path for path, _ in _api(traffic)] == ["/training-plan"]
    with app.app_context():
        assert TrainingPlan.query.filter_by(user_id=profile_ready.id).count() == 0

    traffic.clear()
    page.locator("[data-plan-manage-confirm]").click()
    expect(page.locator('[data-plan-state="active_plan"]')).to_be_visible()
    sequence = [path for path, _ in _api(traffic)]
    assert sequence[:4] == ["/training/bootstrap", "/training-plan/save",
                            "/training/bootstrap", "/training"], sequence
    assert sequence.count("/training-plan/save") == 1
    save_body = json.loads(next(body for path, body in _api(traffic)
                                if path == "/training-plan/save"))
    assert save_body["expected_plan"] is None


# ══════════════════════════════════════════════════════════════════════════
# Responsive matrix, F-14 in the painted UI, desktop composition
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("width", MATRIX)
def test_first_run_never_overflows_and_paints_no_raw_key(
        app, first_run, profile_ready, width, language):
    page, _ = first_run
    _set_language(app, profile_ready.id, language)
    _open(page, width)
    for opened in (False, True):
        if opened:
            page.locator('[data-manage-state="create"] details > summary').click()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), (
            language, width, opened)
        leaked = page.evaluate(r"""() => [...document.querySelectorAll('main *')]
            .filter(el => el.children.length === 0)
            .map(el => (el.textContent || '').trim())
            .filter(t => /^(plan|nav|training)\.[a-z0-9_.]+$/.test(t))""")
        assert not leaked, (language, width, leaked)


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("width", (390, 1366))
def test_the_painted_heading_matches_the_navigation_the_user_clicked(
        app, first_run, profile_ready, width, language):
    page, _ = first_run
    _set_language(app, profile_ready.id, language)
    _open(page, width)
    nav = page.locator('a[href="/training"]:visible')
    expect(nav).to_have_count(1)
    assert nav.inner_text().strip().casefold() == page.locator("h1").inner_text().strip().casefold()
    assert page.title().endswith(page.locator("h1").inner_text().strip())


@pytest.mark.parametrize("width", (1024, 1366))
def test_desktop_first_run_is_composed_not_stretched(first_run, width):
    page, _ = first_run
    _open(page, width)
    main = _box(page, "main")
    assert main["width"] <= 720 + OVERFLOW_TOLERANCE
    boxes = {name: _box(page, f'[data-plan-field="{name}"]') for name in ESSENTIAL}
    # Essentials share rows on a desktop column instead of four full-width bars.
    for left, right in (("odak_hedef", "ekipman"), ("gun_sayisi", "sure")):
        assert abs(boxes[left]["y"] - boxes[right]["y"]) <= OVERFLOW_TOLERANCE, boxes
        assert boxes[left]["x"] < boxes[right]["x"], boxes
    assert all(box["width"] < main["width"] / 2 for box in boxes.values()), boxes


# ══════════════════════════════════════════════════════════════════════════
# Regeneration is shared by the macro — prove it did not move
# ══════════════════════════════════════════════════════════════════════════

def _seed_plan(app, user_id):
    from test_sprint11_training_generation_output import _week
    with app.app_context():
        db.session.add(TrainingPlan(user_id=user_id, score=6.0,
                                    plan_data=json.dumps(_week(), ensure_ascii=False)))
        db.session.commit()


@pytest.mark.parametrize("width", (390, 1366))
def test_regeneration_keeps_its_deliberate_entry_and_flat_form(
        app, first_run, profile_ready, width):
    page, traffic = first_run
    _seed_plan(app, profile_ready.id)
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(PLAN_URL)
    expect(page.locator('[data-plan-state="active_plan"] .plan-days')).to_be_visible()
    panel = page.locator("[data-plan-manage-panel]")
    expect(panel).to_be_hidden()
    page.locator("[data-plan-manage-open]").click()
    expect(panel).to_be_visible()
    assert page.locator('[data-manage-state="regenerate"] details').count() == 0
    for name in ESSENTIAL + OPTIONAL + ["injuries"]:
        expect(page.locator(f'[data-plan-field="{name}"]')).to_be_visible()
    assert page.evaluate(
        "getComputedStyle(document.querySelector('[data-plan-manage-generate]').parentElement)"
        ".position") == "static"
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    expect(page.locator('[data-plan-state="active_plan"] .plan-days')).to_be_visible()
    page.locator("[data-plan-manage-confirm]").click()
    expect(page.locator("[data-plan-replace-confirm]")).to_be_visible()
    page.keyboard.press("Escape")
    page.locator('[data-action="planManageClose"]').click()
    expect(panel).to_be_hidden()
    assert not any(path == "/training-plan/save" for path, _ in _api(traffic))
