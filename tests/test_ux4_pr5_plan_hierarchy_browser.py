"""WEB-UX4-PR5 — the ACTIVE Plan surface, measured in a real browser.

F-06 / F-30 are layout defects and J3 is an evidence gap, so both are proven
where they happen: Chromium lays out the real ``plan.html`` with the real
scripts against real Flask responses. Every state is produced canonically —
a real ``TrainingPlan`` row, a real ``WorkoutSession`` started through the
session service or its HTTP route, a real previous-day session for the stale
reason — and flows through workout_session → workout_state → Plan facts → Plan
presenter → template. Nothing here edits rendered DOM to fake a state. Only the
provider-backed plan generator is stubbed (a hermetic test may not contact a
model), and only for the session-conflict confirmation.

The card census is computed from RENDERED style (background separation, full
border, shadow), never from class names, so renaming a selector cannot pass it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.i18n import t
from app.models import TrainingPlan, User, WorkoutLog
from app.services.training_generation.response_validator import WEEKDAYS
from app.services.workout_session import start_session
from app.timeutil import app_today
from test_sprint14_workout_execution_contract import (
    BENCH, ROW, SQUAT, checkpoint_over_http, snapshot, start_session_over_http,
)
from test_training_execution_boundary import training_page  # noqa: F401  (fixture)
from test_ux3_pr3_training_management_browser import (  # noqa: F401  (fixtures)
    PLAN_URL, generator, plan_v2, profile_ready,
)

WIDTHS = (320, 390, 768, 1024, 1366)
MOBILE = (320, 390)
TOLERANCE = 1
STATIC_PREFIXES = ("/static/",)
MEASURE_DIR = os.environ.get("PR5_MEASURE_DIR")

EXERCISES = (
    {"isim": "Barbell Back Squat", "exercise_id": SQUAT, "set": 4, "tekrar": "6-8",
     "dinlenme": "120 sn", "not": "Keep the bar over mid-foot"},
    {"isim": "Barbell Bench Press", "exercise_id": BENCH, "set": 3, "tekrar": "8-10",
     "dinlenme": "90 sn"},
    {"isim": "Barbell Row", "exercise_id": ROW, "set": 3, "tekrar": "10",
     "dinlenme": "90 sn", "not": "Brace before every pull"},
)


# ── canonical seeds ─────────────────────────────────────────────────────────

def _week(training_offsets):
    """A realistic week: training on ``today + offset`` days, rest elsewhere.

    Offsets are relative to the canonical Istanbul day, so the fixture stays a
    scheduled day (offset 0) or a rest day (no offset 0) whenever it runs.
    """
    today = app_today().weekday()
    program = []
    for index, name in enumerate(WEEKDAYS):
        if (index - today) % 7 in {o % 7 for o in training_offsets}:
            program.append({"gun": name, "tip": "antrenman", "odak": "Strength",
                            "sure_dk": 50, "tahmini_kalori": 320,
                            "egzersizler": [dict(e) for e in EXERCISES]})
        else:
            program.append({"gun": name, "tip": "dinlenme", "odak": "Recovery",
                            "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []})
    return {"program": program, "haftalik_ozet": {"yogunluk_skoru": 7}}


def _seed_plan(user_id, training_offsets=(0, 2, 4), raw=None):
    data = raw if raw is not None else json.dumps(_week(training_offsets), ensure_ascii=False)
    db.session.add(TrainingPlan(user_id=user_id, score=7.5, plan_data=data))
    db.session.commit()


def _seed_history(user_id):
    """Four earlier weeks of logged work, never today (today's evidence would
    change the canonical workout state), so the weekly program has content."""
    now = datetime.utcnow()
    for days_ago in (3, 5, 10, 12, 17, 19, 24, 26):
        for name, weight in (("Barbell Back Squat", 80 + days_ago % 5),
                             ("Barbell Bench Press", 55)):
            db.session.add(WorkoutLog(user_id=user_id, exercise_name=name, sets=3,
                                      reps=8, weight_kg=weight, volume=3 * 8 * weight,
                                      created_at=now - timedelta(days=days_ago)))
    db.session.commit()


SCENARIOS = ("scheduled_start", "active_rest_day", "active_resume",
             "session_conflict", "stale_session", "partial_active_plan")


def seed(app, client, user_id, scenario):
    sessions = scenario in ("active_resume", "session_conflict", "stale_session")
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = sessions
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
    with app.app_context():
        _seed_history(user_id)
        if scenario == "active_rest_day":
            _seed_plan(user_id, training_offsets=(1, 3, 5))
        elif scenario == "partial_active_plan":
            _seed_plan(user_id, raw="{not json")
        elif scenario == "stale_session":
            _seed_plan(user_id, training_offsets=(-1, 0, 2))
            yesterday = app_today() - timedelta(days=1)
            assert start_session(user_id, today=yesterday).session is not None
        else:
            _seed_plan(user_id)
    if scenario in ("active_resume", "session_conflict"):
        public_id = start_session_over_http(client)
        assert checkpoint_over_http(client, public_id, 0, snapshot()).status_code == 200


@pytest.fixture
def surface(app, plan_v2, profile_ready, generator, training_page, client):
    page, traffic, _, _ = training_page
    page.emulate_media(reduced_motion="reduce")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def arrange(scenario, language="tr"):
        seed(app, client, profile_ready.id, scenario)
        with app.app_context():
            db.session.get(User, profile_ready.id).language = language
            db.session.commit()
    yield page, traffic, arrange, errors
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = False


def open_plan(page, width, height=844):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(PLAN_URL)
    page.evaluate("document.fonts ? document.fonts.ready : null")
    mount = page.locator("[data-weekly-program-mount]")
    if mount.count():
        page.wait_for_function(
            "() => !['idle', 'loading'].includes(document.querySelector('[data-weekly-program-mount]').dataset.weeklyProgramState)",
            timeout=5000)
    page.evaluate("() => scrollTo(0, 0)")


def _api(traffic):
    return [path for path, _, _ in traffic if not path.startswith(STATIC_PREFIXES)]


# ── the probe: computed style + geometry, never class names ─────────────────

PROBE = r"""(opts) => {
  const main = document.querySelector('main');
  const alpha = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return 0;
                       const p = m[1].split(',').map(s => parseFloat(s)); return p.length > 3 ? p[3] : 1; };
  const painted = el => { for (let n = el.parentElement; n; n = n.parentElement) {
      const bg = getComputedStyle(n).backgroundColor; if (alpha(bg) > 0) return bg; }
    return 'rgb(0, 0, 0)'; };
  const CONTROL = new Set(['BUTTON','A','INPUT','SELECT','TEXTAREA','SUMMARY','PROGRESS','IMG','SVG','LABEL','OPTION']);
  const visible = el => el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const isSurface = el => {
    if (CONTROL.has(el.tagName) || !visible(el)) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 80 || r.height < 36) return false;
    const cs = getComputedStyle(el);
    const bg = alpha(cs.backgroundColor) > 0 && cs.backgroundColor !== painted(el);
    const sides = ['Top','Right','Bottom','Left'].filter(s =>
      parseFloat(cs['border' + s + 'Width']) > 0 && cs['border' + s + 'Style'] !== 'none'
      && alpha(cs['border' + s + 'Color']) > 0).length;
    return bg || sides >= 3 || cs.boxShadow !== 'none';
  };
  if (opts.expand) document.querySelectorAll('main details').forEach(d => d.open = true);
  const surfaces = [...main.querySelectorAll('*')].filter(isSurface);
  const depthOf = el => { let d = 0; for (let n = el; n && n !== main.parentElement; n = n.parentElement)
      if (surfaces.includes(n)) d++; return d; };
  const tabbable = root => [...root.querySelectorAll('a[href],button,summary,input,select,textarea,[tabindex]')]
      .filter(el => el.tabIndex >= 0 && visible(el) && !el.closest('[inert]'));
  const box = el => { if (!el || !visible(el)) return null; const r = el.getBoundingClientRect();
      return {x: r.x, y: r.y + scrollY, w: r.width, h: r.height, top: r.top, bottom: r.bottom}; };
  const rows = [...document.querySelectorAll('.plan-day')];
  const rest = rows.filter(r => r.classList.contains('plan-day--rest'));
  const training = rows.filter(r => !r.classList.contains('plan-day--rest'));
  const bar = document.querySelector('.action-bar');
  const barShown = bar && getComputedStyle(bar).display !== 'none';
  const fixedTop = [...document.querySelectorAll('body *')].filter(el => {
      const cs = getComputedStyle(el); if (!['fixed','sticky'].includes(cs.position) || !visible(el)) return false;
      const r = el.getBoundingClientRect(); return r.top <= 0 && r.bottom > 0 && r.bottom < innerHeight / 2 && r.width > innerWidth / 2; })
    .reduce((m, el) => Math.max(m, el.getBoundingClientRect().bottom), 0);
  const align = el => {
    if (!el || !visible(el)) return null;
    const range = document.createRange(); range.selectNodeContents(el);
    const text = range.getBoundingClientRect(); const r = el.getBoundingClientRect();
    const parent = el.parentElement.getBoundingClientRect();
    const pcs = getComputedStyle(el.parentElement);
    const axis = parent.left + parseFloat(pcs.paddingLeft);
    return {textLeftFromAxis: Math.round(text.left - axis), linkWidth: Math.round(r.width),
            linkHeight: Math.round(r.height), textWidth: Math.round(text.width),
            aligned: Math.abs(text.left - axis) <= 2};
  };
  let longest = 0;
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.textContent.trim() || !visible(n.parentElement) || n.parentElement.closest('script')) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    for (const rect of range.getClientRects()) longest = Math.max(longest, rect.width);
  }
  const mcs = getComputedStyle(main);
  const action = document.querySelector('[data-action="startWorkout"]');
  const training_domain = document.querySelector('[data-plan-domain="training"]');
  const nutrition = document.querySelector('[data-plan-domain="nutrition"]');
  const days = document.querySelector('.plan-days');
  return {
    state: main.dataset.planState,
    workout_action: training_domain && training_domain.dataset.workoutAction,
    doc_height: document.documentElement.scrollHeight,
    viewport_h: innerHeight,
    overflow: document.documentElement.scrollWidth > innerWidth,
    content_width: Math.round(main.getBoundingClientRect().width - parseFloat(mcs.paddingLeft) - parseFloat(mcs.paddingRight)),
    training_box: box(training_domain),
    nutrition_box: box(nutrition),
    days_box: box(days),
    surface_count: surfaces.length,
    max_depth: surfaces.reduce((m, el) => Math.max(m, depthOf(el)), 0),
    surfaces: surfaces.map(el => ({cls: el.className, depth: depthOf(el)})),
    rest_days: rest.length,
    training_days: training.length,
    rest_focus_stops: rest.reduce((n, r) => n + (r.matches('details') ? tabbable(r).length : tabbable(r).length), 0),
    training_focus_stops: training.reduce((n, r) => n + tabbable(r).length, 0),
    rest_tags: [...new Set(rest.map(r => r.tagName))],
    training_tags: [...new Set(training.map(r => r.tagName))],
    action: box(action),
    action_count: document.querySelectorAll('[data-action="startWorkout"]').length,
    action_label: action && visible(action) ? action.textContent.trim() : null,
    usable_top: fixedTop,
    usable_bottom: innerHeight - (barShown ? bar.getBoundingClientRect().height : 0),
    stale: (() => { const s = document.querySelector('[data-plan-session-stale]');
                   return s && visible(s) ? {reason: s.dataset.planSessionStale, box: box(s)} : null; })(),
    recovery_visible: (() => { const r = document.querySelector('[data-action="recoverBlockedWorkout"]'); return !!(r && visible(r)); })(),
    partial_note: box(document.querySelector('.plan-partial-note')),
    weekly_mount: document.querySelectorAll('[data-weekly-program-mount]').length,
    weekly_box: box(document.querySelector('[data-weekly-program-mount]')),
    nutrition_link: align(document.querySelector('[data-plan-domain="nutrition"] > .plan-domain-link, [data-plan-domain="nutrition"] .plan-domain-link:not([href="/supplements"])')),
    supplements_link: align(document.querySelector('a.plan-domain-link[href="/supplements"]')),
    longest_line: Math.round(longest),
  };
}"""


def probe(page, expand=False):
    return page.evaluate(PROBE, {"expand": expand})


def _record(name, data, page=None):
    if not MEASURE_DIR:
        return
    out = Path(MEASURE_DIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    if page is not None:
        page.screenshot(path=str(out / f"{name}.png"), full_page=True)


# ══════════════════════════════════════════════════════════════════════════
# Measurement matrix — every state × width × locale, overflow-free, recorded
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_state_matrix_is_canonical_and_never_overflows(surface, scenario, language):
    page, _, arrange, errors = surface
    arrange(scenario, language)
    expected = {
        "scheduled_start": ("active_plan", "start"),
        "active_rest_day": ("active_plan", "none"),
        "active_resume": ("active_plan", "resume"),
        "session_conflict": ("active_plan", "resume"),
        "stale_session": ("active_plan", "none"),
        "partial_active_plan": ("partial_active_plan", "none"),
    }[scenario]
    for width in WIDTHS:
        open_plan(page, width)
        if scenario == "session_conflict":
            _open_conflict_confirmation(page)
        data = probe(page)
        assert page.locator("html").get_attribute("lang") == language
        assert (data["state"], data["workout_action"]) == expected, (scenario, width, data["state"])
        assert data["overflow"] is False, (scenario, language, width)
        if scenario == "stale_session":
            assert data["stale"] and data["stale"]["reason"] == "previous_day"
            assert data["action_label"] is None
            assert data["recovery_visible"] is True
        if scenario == "partial_active_plan":
            assert data["partial_note"] is not None
        _record(f"{scenario}-{language}-{width}", data, page)
        if scenario == "session_conflict":
            page.keyboard.press("Escape")
            expect(page.locator("[data-plan-replace-confirm]")).to_be_hidden()
    assert not errors


def _open_conflict_confirmation(page):
    """Regenerate → a real (stubbed-provider) proposal → Replace → the dialog.

    Stops at the confirmation: nothing is persisted to obtain the evidence."""
    page.locator("[data-plan-manage-open]").click()
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    page.locator("[data-plan-manage-confirm]").click()
    dialog = page.locator("[data-plan-replace-confirm]")
    expect(dialog).to_be_visible()
    expect(dialog.locator(".plan-replace-warning")).to_be_visible()


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("scenario", ["scheduled_start", "active_resume"])
def test_expanded_census_is_recorded(surface, scenario, language):
    page, _, arrange, _ = surface
    arrange(scenario, language)
    for width in (390, 1366):
        open_plan(page, width)
        data = probe(page, expand=True)
        _record(f"expanded-{scenario}-{language}-{width}", data, page)


# ══════════════════════════════════════════════════════════════════════════
# Recorded current-main baseline (1110ecb, this same harness, TR). Thresholds
# derive from it and are not tuned to the implementation: F-06 asks for
# roughly a fifth less height at 390 and nesting <= 2.
# ══════════════════════════════════════════════════════════════════════════
BASELINE_390_HEIGHT = 2718
BASELINE_SURFACES = 15
BASELINE_CONTENT_WIDTH_1366 = 688
ACTIVE = ("scheduled_start", "active_resume", "stale_session", "active_rest_day",
          "partial_active_plan")


@pytest.mark.parametrize("scenario", ACTIVE)
def test_visual_nesting_is_at_most_two_and_surfaces_materially_decline(surface, scenario):
    page, _, arrange, _ = surface
    arrange(scenario)
    for width in WIDTHS:
        open_plan(page, width)
        for expand in (False, True):
            data = probe(page, expand=expand)
            assert data["max_depth"] <= 2, (scenario, width, expand, data["surfaces"])
            assert data["surface_count"] <= BASELINE_SURFACES // 3, (scenario, width, data["surfaces"])


def test_the_conflict_proposal_keeps_nesting_at_most_two(surface):
    page, _, arrange, _ = surface
    arrange("session_conflict")
    for width in (390, 1366):
        open_plan(page, width)
        _open_conflict_confirmation(page)
        page.keyboard.press("Escape")
        expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
        data = probe(page)
        assert data["max_depth"] <= 2, data["surfaces"]


def _canonical_days(app, user_id):
    with app.app_context():
        row = TrainingPlan.query.filter_by(user_id=user_id).one()
        program = json.loads(row.plan_data)["program"]
    return ([d["gun"] for d in program if d["tip"] == "dinlenme"],
            [d["gun"] for d in program if d["tip"] != "dinlenme"])


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("scenario", ["scheduled_start", "active_rest_day"])
def test_rest_days_are_quiet_rows_and_training_days_stay_disclosures(
        app, surface, profile_ready, scenario, language):
    page, _, arrange, _ = surface
    arrange(scenario, language)
    rest_labels, training_labels = _canonical_days(app, profile_ready.id)
    open_plan(page, 390)
    with app.test_request_context():
        rest_word = t("plan.day.rest", locale=language)
        rest_note = t("plan.day.rest_note", locale=language)
        weekday = {d: t(f"plan.weekday.{d}", locale=language)
                   for d in rest_labels + training_labels}
    rows = page.locator(".plan-days .plan-day--rest")
    assert rows.count() == len(rest_labels) >= 2
    texts = rows.all_inner_texts()
    for label, text in zip(rest_labels, texts):
        assert weekday[label] in text and rest_word in text and rest_note in text, text
    data = probe(page)
    assert data["rest_tags"] == ["DIV"], data["rest_tags"]
    assert data["rest_focus_stops"] == 0
    training = page.locator(".plan-days details.plan-day")
    assert training.count() == len(training_labels)
    assert data["training_focus_stops"] == len(training_labels)
    names = [n.strip() for n in page.locator(".plan-days .plan-day .plan-day-name").all_inner_texts()]
    assert len(names) == 7
    for label in rest_labels + training_labels:
        assert names.count(weekday[label]) == 1, (label, names)
    first = training.first
    first.locator("summary").focus()
    page.keyboard.press("Enter")
    expect(first).to_have_attribute("open", "")
    body = first.inner_text()
    for exercise in EXERCISES:
        assert exercise["isim"] in body and exercise["tekrar"] in body and exercise["dinlenme"] in body
        if exercise.get("not"):
            assert exercise["not"] in body
    summary = first.locator("summary").inner_text()
    assert "50" in summary and "320 kcal" in summary and "Strength" in summary


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("size", [(320, 640), (390, 844)])
@pytest.mark.parametrize("scenario,label_key", [
    ("scheduled_start", "plan.action.start_workout"),
    ("active_resume", "plan.action.resume_workout"),
])
def test_start_and_resume_are_the_one_action_above_the_fold(
        app, surface, scenario, label_key, size, language):
    page, _, arrange, _ = surface
    arrange(scenario, language)
    open_plan(page, *size)
    data = probe(page)
    with app.test_request_context():
        assert data["action_label"] == t(label_key, locale=language)
    assert data["action_count"] == 1
    box = data["action"]
    assert box["top"] >= data["usable_top"] - TOLERANCE, (size, box, data["usable_top"])
    assert box["bottom"] <= data["usable_bottom"] + TOLERANCE, (size, box, data["usable_bottom"])
    assert box["h"] >= 44
    assert page.evaluate(
        "() => { const b = document.querySelector('[data-action=\"startWorkout\"]');"
        " const r = b.getBoundingClientRect();"
        " const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
        " return hit === b || b.contains(hit); }")
    order = page.evaluate(
        "() => ['[data-action=\"startWorkout\"]', '[data-weekly-program-mount]', '.plan-days',"
        " '[data-plan-manage-open]', '[data-plan-domain=\"nutrition\"]']"
        ".map(s => document.querySelector(s).getBoundingClientRect().top)")
    assert order[0] < min(order[1:]), order


def test_390_document_height_falls_by_a_fifth(surface):
    page, _, arrange, _ = surface
    arrange("scheduled_start")
    open_plan(page, 390)
    data = probe(page)
    assert data["doc_height"] <= BASELINE_390_HEIGHT * 0.80, data["doc_height"]
    assert data["overflow"] is False


@pytest.mark.parametrize("scenario", ["scheduled_start", "stale_session", "partial_active_plan"])
def test_desktop_active_plan_is_composed_not_a_phone_column(surface, scenario):
    page, _, arrange, _ = surface
    arrange(scenario)
    open_plan(page, 1366, 900)
    data = probe(page)
    training, nutrition = data["training_box"], data["nutrition_box"]
    assert data["content_width"] > BASELINE_CONTENT_WIDTH_1366 * 1.4, data["content_width"]
    assert nutrition["x"] >= training["x"] + training["w"], (training, nutrition)
    assert abs(nutrition["y"] - training["y"]) <= TOLERANCE, (training, nutrition)
    assert training["w"] >= 2 * nutrition["w"], (training, nutrition)
    assert data["content_width"] < 1366 * 0.85
    assert data["longest_line"] <= 760, data["longest_line"]
    open_plan(page, 768)
    tablet = probe(page)
    assert tablet["nutrition_box"]["y"] >= tablet["training_box"]["y"] + tablet["training_box"]["h"] - TOLERANCE


@pytest.mark.parametrize("language", ["tr", "en"])
def test_320_child_domain_links_follow_the_reading_axis(surface, language):
    page, _, arrange, _ = surface
    arrange("scheduled_start", language)
    open_plan(page, 320, 640)
    data = probe(page)
    for name in ("nutrition_link", "supplements_link"):
        link = data[name]
        assert link["aligned"], (name, link)
        assert link["linkHeight"] >= 44


@pytest.mark.parametrize("width", WIDTHS)
def test_stale_session_explains_itself_before_recovery_and_offers_no_resume(app, surface, width):
    page, _, arrange, _ = surface
    arrange("stale_session")
    open_plan(page, width)
    data = probe(page)
    assert data["action"] is None and data["action_count"] == 0
    assert data["stale"]["reason"] == "previous_day"
    with app.test_request_context():
        expect(page.locator("[data-plan-session-stale]")).to_have_text(
            t("plan.session_stale.previous_day", locale="tr"))
        excluded = {t("plan.workout_state.rest_day", locale="tr"),
                    t("plan.workout_state.completed", locale="tr")}
    chip = page.locator('[data-plan-domain="training"] .plan-domain-state').inner_text()
    assert chip not in excluded
    recovery = page.locator('[data-action="recoverBlockedWorkout"]')
    expect(recovery).to_be_visible()
    assert data["stale"]["box"]["top"] < recovery.bounding_box()["y"]
    assert data["stale"]["box"]["bottom"] <= data["usable_bottom"]


@pytest.mark.parametrize("width", WIDTHS)
def test_session_conflict_warns_in_a_real_dialog_and_cancel_keeps_the_plan(
        app, surface, profile_ready, width):
    page, traffic, arrange, _ = surface
    arrange("session_conflict")
    with app.app_context():
        before = TrainingPlan.query.filter_by(user_id=profile_ready.id).one().lineage_id
    open_plan(page, width)
    _open_conflict_confirmation(page)
    dialog = page.locator("[data-plan-replace-confirm]")
    assert dialog.get_attribute("role") == "dialog"
    assert dialog.get_attribute("aria-modal") == "true"
    with app.test_request_context():
        expect(dialog.locator(".plan-replace-warning")).to_have_text(
            t("plan.manage.confirm.session_warning", locale="tr"))
    warning = dialog.locator(".plan-replace-warning").bounding_box()
    assert warning["y"] >= 0 and warning["y"] + warning["height"] <= page.viewport_size["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    assert "/training-plan/save" not in _api(traffic)
    with app.app_context():
        assert TrainingPlan.query.filter_by(user_id=profile_ready.id).one().lineage_id == before


_FOCUS = (
    "() => { const el = document.activeElement; if (!el || el === document.body) return null;"
    " const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);"
    " return {tag: el.tagName, action: el.getAttribute('data-action'), href: el.getAttribute('href'),"
    " open: el.hasAttribute('data-plan-manage-open'), rest: !!el.closest('.plan-day--rest'),"
    " day: el.tagName === 'SUMMARY' && !!el.closest('.plan-days'), main: !!el.closest('main'),"
    " visible: r.width > 0 && r.height > 0 && el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}),"
    " ring: (parseFloat(cs.outlineWidth) >= 2 && cs.outlineStyle !== 'none') || cs.boxShadow !== 'none'}; }"
)


@pytest.mark.parametrize("scenario", ["scheduled_start", "stale_session"])
def test_real_tab_walk_reaches_everything_early_and_skips_rest_days(surface, scenario):
    page, _, arrange, _ = surface
    arrange(scenario)
    open_plan(page, 390)
    page.locator("h1").click()
    stops = []
    for _ in range(80):
        page.keyboard.press("Tab")
        record = page.evaluate(_FOCUS)
        if record is None or not record["main"]:
            if stops:
                break
            continue
        stops.append(record)
    assert stops, "no focus stops inside the Plan"
    assert all(s["visible"] and s["ring"] for s in stops), [
        s for s in stops if not (s["visible"] and s["ring"])]
    assert not any(s["rest"] for s in stops)
    assert sum(s["day"] for s in stops) == 3
    kinds = [s["action"] or s["href"] or ("open" if s["open"] else s["tag"]) for s in stops]
    first_day = next(i for i, s in enumerate(stops) if s["day"])
    if scenario == "scheduled_start":
        assert kinds.index("startWorkout") < first_day, kinds
    else:
        assert "startWorkout" not in kinds
        assert kinds.index("recoverBlockedWorkout") < first_day, kinds
    for target in ("planManageOpen", "/nutrition", "/supplements", "/coach"):
        assert target in kinds, (target, kinds)
    assert page.evaluate(
        "document.querySelectorAll('[tabindex]:not([tabindex=\"-1\"]):not([tabindex=\"0\"])').length") == 0


def test_layout_interactions_issue_no_requests_and_weekly_program_fetches_once(surface):
    page, traffic, arrange, _ = surface
    arrange("active_resume")
    open_plan(page, 390)
    loaded = _api(traffic)
    assert loaded.count("/api/training/weekly-program") == 1, loaded
    assert loaded.count("/training") == 1, loaded
    assert page.locator("[data-weekly-program-mount]").count() == 1
    assert page.locator("#weekly-program[data-weekly-program-mount]").count() == 1
    traffic.clear()
    for summary in page.locator(".plan-days details.plan-day > summary").all():
        summary.click()
        summary.click()
    for width in (768, 1024, 1366, 390):
        page.set_viewport_size({"width": width, "height": 900})
    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(500)
    assert _api(traffic) == [], _api(traffic)


# ══════════════════════════════════════════════════════════════════════════
# Regression states — recorded with the same harness on both commits
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["tr", "en"])
def test_regression_states_record_without_overflow(app, surface, profile_ready, monkeypatch, language):
    page, _, _, errors = surface
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
    with app.app_context():
        db.session.get(User, profile_ready.id).language = language
        db.session.commit()
    for width in (320, 390, 1366):
        open_plan(page, width)
        data = probe(page)
        data["create_action"] = page.locator("[data-plan-manage-generate]").bounding_box()
        assert data["state"] == "no_active_plan" and data["overflow"] is False
        _record(f"no_active_plan-{language}-{width}", data, page)
    with app.app_context():
        _seed_plan(profile_ready.id)
    for width in (390, 1366):
        open_plan(page, width)
        page.locator("[data-plan-manage-open]").click()
        data = probe(page)
        assert data["overflow"] is False
        _record(f"regenerate_open-{language}-{width}", data, page)
    from app.services import plan_facts

    def _raise(uid):
        raise RuntimeError("read failed")
    monkeypatch.setattr(plan_facts, "get_active_plan", _raise)
    for width in (390, 1366):
        open_plan(page, width)
        data = probe(page)
        assert data["state"] == "read_error" and data["overflow"] is False
        assert data["action"] is None
        _record(f"read_error-{language}-{width}", data, page)
    assert not errors
