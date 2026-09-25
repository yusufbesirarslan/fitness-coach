"""WEB-UX4-PR8 - cross-surface regression gate, measured in a real browser.

PR2-PR7 each proved one surface. This gate asserts the invariants every UX4
surface must keep at once, in every matrix cell, so a later change to any one
surface - or to the shared shell under all of them - cannot drift silently:

  * semantic DOM ......... one h1, no skipped heading level, every field and
                           every icon-only control named, no invisible Tab stop
  * responsive / i18n .... no page overflow, one navigation, no clipped primary
                           copy, a real dominant action, no raw key, no other-
                           locale copy, no raw enum - at 320/390/768/1024/1366,
                           TR and EN
  * states ............... the same invariants in the representative states
                           PR2-PR7 implemented, plus each state's own authority
  * keyboard ............. reading-order Tab walk, desktop focus visibility,
                           the Account / Notifications / sheet controls
  * partial failure ...... a failing secondary read degrades only its section
  * request topology ..... app reads, script dependencies, external hosts

Every failure names ``surface / viewport / locale / state / invariant``.

The harness is ``ux4_gate_support``: HTTP is served by the authenticated Flask
test client, every non-localhost request is aborted, nothing leaves the process.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import User
from test_ux4_pr2_foundations_browser import (
    _FOCUS_JS, _assert_focus_is_perceptible, _keyboard_walk,
)
from ux4_gate_support import (  # noqa: F401  (gate is a fixture)
    AUTH_SURFACES, FAILURE_OVERRIDES, LOCALES, STATES, SURFACES, WIDTHS,
    foreign_copy, gate, load_locale, raw_enums, raw_keys, ready, seed_state,
    tab_walk,
)

CATALOGUES = {code: load_locale(code) for code in LOCALES}
KEYS = set(CATALOGUES["en"]) | set(CATALOGUES["tr"])

# Stored Turkish enum values that must reach an English screen translated.
STORED_ENUMS = {"kilo verme", "kas kazanma", "antrenman", "dinlenme"}


def cell_violations(facts, where, language):
    """Every invariant, each reported separately and located."""
    out = []

    def fail(invariant, detail):
        out.append("%s invariant=%s: %s" % (where, invariant, detail))

    if facts["lang"] != language:
        fail("document-language", facts["lang"])
    if len(facts["h1"]) != 1:
        fail("one-h1", facts["h1"])
    if not facts["firstIsH1"]:
        fail("h1-leads-the-outline", "first exposed heading is not the h1")
    if facts["skips"]:
        fail("no-skipped-heading-level", facts["skips"])
    if facts["unlabeled"]:
        fail("every-field-labelled", facts["unlabeled"])
    if facts["unnamed"]:
        fail("every-control-named", facts["unnamed"])
    if facts["ghosts"]:
        fail("no-invisible-tab-stop", facts["ghosts"])
    if len(facts["navs"]) != 1:
        fail("one-navigation", facts["navs"])
    if facts["overflow"]:
        fail("no-horizontal-overflow", "scrollWidth=%s" % facts["scrollWidth"])
    if facts["clipped"]:
        fail("primary-copy-not-clipped", facts["clipped"])
    if len(facts["primary"]) > 1:
        fail("one-dominant-action", [p["name"] for p in facts["primary"]])
    for p in facts["primary"]:
        if p["w"] < 43.5 or p["h"] < 43.5:
            fail("dominant-action-size", "%s %.0fx%.0f" % (p["name"], p["w"], p["h"]))
        if p["clipped"] or p["outside"]:
            fail("dominant-action-inside-viewport", p["name"])
        if p["bg"] in ("rgba(0, 0, 0, 0)", "transparent"):
            fail("dominant-action-filled", p["name"])
        same = [s["name"] for s in facts["secondaries"]
                if s["bg"] == p["bg"] and s["color"] == p["color"]]
        if same:
            fail("dominant-action-distinguishable", "%s paints like %s" % (p["name"], same))
    strings = list(facts["texts"]) + list(facts["attrs"])
    keys = raw_keys(strings, KEYS)
    if keys:
        fail("no-raw-localization-key", keys)
    enums = raw_enums(strings)
    if enums:
        fail("no-raw-enum", enums)
    other = "en" if language == "tr" else "tr"
    foreign = foreign_copy(strings, CATALOGUES[language], CATALOGUES[other])
    if foreign:
        fail("no-%s-copy-on-a-%s-screen" % (other, language), foreign)
    if language == "en":
        leaked = sorted(s for s in strings if s.strip().lower() in STORED_ENUMS)
        if leaked:
            fail("no-stored-enum-label", leaked)
    return out


def _assert_clean(violations, errors=()):
    problems = list(violations) + ["page error: %s" % e for e in errors]
    assert not problems, "%d invariant violation(s):\n  %s" % (
        len(problems), "\n  ".join(problems))


# ── default-state matrix: 8 surfaces x 5 widths x TR/EN ─────────────────────

# App reads a surface issues in its default state, measured on d6d8675 (the
# PR7 set for Today / Progress / Account / Notifications is the same one
# test_ux4_pr7_browser pins; it is repeated here so all eight live in one table).
DEFAULT_READS = {
    "today": {"/", "/notifications/unread-count", "/meal-log/today", "/water",
              "/checkin-history", "/leaderboard/reward-check"},
    "plan": {"/training", "/notifications/unread-count"},
    "coach": {"/coach", "/notifications/unread-count", "/coach/history"},
    "nutrition": {"/nutrition", "/notifications/unread-count", "/meal-log/today",
                  "/nutrition-plan/active", "/water", "/coach/history"},
    "supplements": {"/supplements", "/notifications/unread-count"},
    "progress": {"/progress-page", "/notifications/unread-count",
                 "/api/progress/summary", "/api/progress/history",
                 "/api/progress/axis-insights", "/api/progress/physique"},
    "account": {"/edit-profile", "/notifications/unread-count"},
    "notifications": {"/notifications", "/notifications/unread-count",
                      "/notifications/data"},
}

# Script dependencies (dependency presence, not bytes) and the ceiling on the
# number of first-party static files, measured on d6d8675.
_SHELL = {"csrf.js", "i18n.js", "modal.js", "analytics.js"}
SCRIPTS = {
    "today": _SHELL | {"today.js"},
    "plan": _SHELL | {"actions.js", "training_plan_management.js",
                      "plan_training_manage.js"},
    "coach": _SHELL | {"actions.js", "coach_widget.js"},
    "nutrition": _SHELL | {"actions.js", "coach_widget.js", "nutrition.js"},
    "supplements": _SHELL | {"actions.js"},
    "progress": _SHELL | {"actions.js", "progress.js",
                          "progress_insights.js", "progress_physique.js",
                          "progress_presentation.js"},
    "account": _SHELL | {"actions.js", "profile.js"},
    "notifications": _SHELL | {"actions.js"},
}
# Progress V2 PR1 added progress_presentation.js (the pure state → copy module)
# WITHOUT raising the Progress ceiling: the history consumer moved into
# progress.js, so the separate progress_history.js request is gone.
STATIC_CEILING = {"today": 11, "plan": 13, "coach": 12, "nutrition": 14,
                  "supplements": 11, "progress": 15, "account": 12,
                  "notifications": 11}
# Third-party origins each surface may ask for. Coach (and Nutrition, which
# hosts the Menu Scan widget) pin marked + DOMPurify from jsDelivr with SRI.
EXTERNAL = {"fonts.googleapis.com stylesheet", "www.googletagmanager.com script"}
EXTERNAL_WIDGET = EXTERNAL | {"cdn.jsdelivr.net script"}


def _topology_violations(gate, surface, where):
    out = []
    reads = gate.app_reads()
    if set(reads) != DEFAULT_READS[surface]:
        out.append("%s invariant=request-topology: extra %s missing %s" % (
            where, sorted(set(reads) - DEFAULT_READS[surface]),
            sorted(DEFAULT_READS[surface] - set(reads))))
    duplicates = sorted({r for r in reads if reads.count(r) > 1})
    if duplicates:
        out.append("%s invariant=no-duplicate-read: %s" % (where, duplicates))
    statics = set(gate.static_reads())
    scripts = {p.rsplit("/", 1)[1] for p in statics if p.endswith(".js")}
    if scripts != SCRIPTS[surface]:
        out.append("%s invariant=script-dependencies: extra %s missing %s" % (
            where, sorted(scripts - SCRIPTS[surface]), sorted(SCRIPTS[surface] - scripts)))
    if len(statics) > STATIC_CEILING[surface]:
        out.append("%s invariant=static-request-budget: %d > %d %s" % (
            where, len(statics), STATIC_CEILING[surface], sorted(statics)))
    allowed = EXTERNAL_WIDGET if surface in ("coach", "nutrition") else EXTERNAL
    external = set(gate.external)
    if not external <= allowed:
        out.append("%s invariant=external-origins: %s" % (where, sorted(external - allowed)))
    fonts = [e for e in gate.external_all if e.startswith("fonts.googleapis.com")]
    if len(fonts) != 1:
        out.append("%s invariant=one-font-stylesheet: %s" % (where, fonts))
    return out


@pytest.mark.parametrize("surface", list(SURFACES))
def test_surface_matrix_keeps_every_invariant(app, auth_user, gate, surface):
    """The standing UX4 matrix: 5 widths x TR/EN for one surface.

    Request topology is asserted on the same loads (EN, every width), so the
    budget costs no extra navigation.
    """
    violations = []
    for language in LOCALES:
        ready(app, auth_user.id, language)
        for width in WIDTHS:
            gate.visit(SURFACES[surface], width)
            where = "surface=%s viewport=%d locale=%s state=default" % (
                surface, width, language)
            violations += cell_violations(gate.probe(), where, language)
            if language == "en":
                violations += _topology_violations(gate, surface, where)
    _assert_clean(violations, gate.errors)


@pytest.mark.parametrize("surface", list(AUTH_SURFACES))
def test_auth_entry_matrix_keeps_the_foundation_invariants(app, client, gate, surface):
    """The acquisition screens carry the same foundation guarantees (PR2 F-01,
    F-10) - they are the product's first impression. No product navigation
    exists before sign-in, so the one-navigation invariant does not apply."""
    violations = []
    for language in LOCALES:
        client.post("/set-language", json={"lang": language})
        for width in (320, 768, 1366):     # PR2 owns the 5-width auth overflow matrix
            gate.visit(AUTH_SURFACES[surface], width)
            where = "surface=%s viewport=%d locale=%s state=anonymous" % (
                surface, width, language)
            violations += [v for v in cell_violations(gate.probe(), where, language)
                           if "invariant=one-navigation" not in v]
    _assert_clean(violations, gate.errors)


# ── state matrix ────────────────────────────────────────────────────────────

# Reads a state may add on top of its surface's default topology.
STATE_READS = {
    ("plan", "scheduled_start"): {"/api/training/weekly-program"},
    ("plan", "active_rest_day"): {"/api/training/weekly-program"},
    ("plan", "active_resume"): {"/api/training/weekly-program"},
}

# Today's authority: Resume > Start > Create Plan. Exactly one of them - or,
# in a settled / degraded state, none - is the dominant action.
TODAY_AUTHORITY = {
    "no_plan": ("no_plan", "today.action.create_plan"),
    "scheduled": ("scheduled_not_started", "today.action.start_workout"),
    "in_progress": ("in_progress", "today.action.resume_workout"),
    "completed": ("completed", None),
    "rest_day": ("rest_day", None),
    "degraded": ("error", None),
}

STATE_FACTS = r"""
() => {
  const q = s => document.querySelector(s);
  const shown = el => !!el && el.checkVisibility({checkOpacity: true, visibilityProperty: true});
  return {
    todayState: q('#today-page') ? q('#today-page').dataset.todayState : null,
    todayPrimary: [...document.querySelectorAll('[data-today-primary]')].filter(shown)
                    .map(e => e.textContent.trim()),
    todayNext: [...document.querySelectorAll('[data-today-next]')].filter(shown).length,
    nutTarget: q('.nut-hero') ? q('.nut-hero').dataset.targetState : null,
    nutTargetText: q('.nut-hero-target') ? q('.nut-hero-target').innerText.trim() : null,
    notifState: q('#notif-list') ? q('#notif-list').dataset.notifState : null,
    composer: shown(q('#cw-input')),
    coachHistory: document.querySelectorAll('#cw-msgs .cw-row').length,
    planV2: !!q('main#plan-page[data-plan-v2]'),
    planState: q('#plan-page') ? q('#plan-page').dataset.planState : null,
    legacyScript: [...document.scripts].some(x => /\/static\/training\.js/.test(x.src)),
    cabinet: document.querySelectorAll('[data-action="deleteSupplement"]').length,
    ppStrip: document.querySelectorAll('#physique-body .pp-strip figure').length,
    history: document.querySelectorAll('#history-list .hist-item').length,
    loading: [...document.querySelectorAll('.loading-text')].filter(shown).length,
    pressedGoal: [...document.querySelectorAll('.pf-choice[aria-pressed="true"]')].length,
  };
}
"""


def _state_violations(surface, state, s, where, language, seeded):
    out = []

    def fail(invariant, detail):
        out.append("%s invariant=%s: %s" % (where, invariant, detail))

    if surface == "today":
        expected_state, label_key = TODAY_AUTHORITY[state]
        if s["todayState"] != expected_state:
            fail("today-state", s["todayState"])
        want = [CATALOGUES[language][label_key]] if label_key else []
        if s["todayPrimary"] != want:
            fail("resume>start>create-authority", s["todayPrimary"])
        if not label_key and s["todayNext"] != 1:
            fail("settled-state-keeps-a-next-step", s["todayNext"])
    elif surface == "plan":
        if not s["planV2"] or s["legacyScript"]:
            fail("canonical-plan-renderer", "planV2=%s legacyScript=%s"
                 % (s["planV2"], s["legacyScript"]))
        want = "no_active_plan" if state == "no_plan" else "active_plan"
        if s["planState"] != want:
            fail("plan-state", s["planState"])
    elif surface == "coach":
        if not s["composer"]:
            fail("composer-ready", "the composer is not painted")
        if state == "populated" and s["coachHistory"] < 8:
            fail("history-hydrated", s["coachHistory"])
    elif surface == "nutrition":
        want = "known" if state == "target_known" else "absent"
        if s["nutTarget"] != want:
            fail("target-state", s["nutTarget"])
        if want == "absent" and any(ch.isdigit() for ch in (s["nutTargetText"] or "")):
            fail("no-fabricated-target", s["nutTargetText"])
    elif surface == "supplements":
        want = 0 if state == "empty_cabinet" else seeded
        if s["cabinet"] != want:
            fail("cabinet-rows", s["cabinet"])
    elif surface == "progress" and state == "populated":
        if s["ppStrip"] != 2 or s["history"] != 2:
            fail("pump-check-and-history-continuity", (s["ppStrip"], s["history"]))
    elif surface == "account":
        if s["pressedGoal"] < 1:
            fail("selected-preference-exposed", s["pressedGoal"])
    elif surface == "notifications":
        want = {"empty": "empty", "populated": "ready",
                "first_load_failure": "error"}[state]
        if s["notifState"] != want:
            fail("notification-state", s["notifState"])
    if s["loading"]:
        fail("no-permanent-loading", s["loading"])
    return out


STATE_CELLS = (("tr", 320), ("en", 1366))
STATE_CASES = [(surface, state) for surface, states in STATES.items() for state in states]


@pytest.mark.parametrize("surface,state", STATE_CASES,
                         ids=["%s-%s" % c for c in STATE_CASES])
def test_state_matrix_keeps_every_invariant(app, client, auth_user, gate, monkeypatch,
                                            surface, state):
    """Representative states PR2-PR7 implemented: TR at 320, EN at 1366."""
    path = seed_state(app, client, monkeypatch, auth_user.id, surface, state)
    gate.overrides.update(FAILURE_OVERRIDES.get((surface, state), {}))
    allowed = DEFAULT_READS[surface] | STATE_READS.get((surface, state), set())
    with app.app_context():
        from app.models import Supplement
        seeded = Supplement.query.filter_by(user_id=auth_user.id).count()
    violations = []
    # Each state is measured once per locale, at opposite ends of the width
    # range; the full 5x2 matrix is the default-state test's job.
    for language, width in STATE_CELLS:
        ready(app, auth_user.id, language)
        gate.visit(path, width)
        where = "surface=%s viewport=%d locale=%s state=%s" % (
            surface, width, language, state)
        violations += cell_violations(gate.probe(), where, language)
        violations += _state_violations(surface, state, gate.probe(STATE_FACTS),
                                        where, language, seeded)
        reads = gate.app_reads()
        if not set(reads) <= allowed:
            violations.append("%s invariant=request-topology: %s" % (
                where, sorted(set(reads) - allowed)))
        if len(reads) != len(set(reads)):
            violations.append("%s invariant=no-duplicate-read: %s" % (where, reads))
    _assert_clean(violations, gate.errors)


# ── keyboard ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("surface", list(SURFACES))
def test_tab_order_follows_the_reading_order(app, auth_user, gate, surface):
    """At a single-column width, Tab must move down the page, never jump back
    above where it was; and every stop must paint. Fixed chrome (header, bottom
    navigation, floating actions) sits outside the flow and is exempt from the
    order check, not from the visibility check."""
    ready(app, auth_user.id, "en")
    gate.visit(SURFACES[surface], 390)
    stops = tab_walk(gate.page)
    where = "surface=%s viewport=390 locale=en state=default" % surface
    assert len(stops) >= 5, "%s invariant=keyboard-reachable: only %d stops" % (where, len(stops))
    problems = ["%s invariant=tab-stop-painted: %s" % (where, s["label"])
                for s in stops if not s["visible"]]
    flow = [s for s in stops if not s["fixed"]]
    for before, after in zip(flow, flow[1:]):
        if after["top"] < before["top"] - 8:
            problems.append("%s invariant=tab-follows-reading-order: %s@%d -> %s@%d" % (
                where, before["label"], before["top"], after["label"], after["top"]))
    _assert_clean(problems, gate.errors)


@pytest.mark.parametrize("surface", list(SURFACES))
def test_desktop_keyboard_stops_show_a_visible_focus_indicator(app, auth_user, gate, surface):
    """PR2 proved focus visibility at 390, where the bottom action bar is the
    navigation. At 1366 the header navigation and the desktop compositions
    (Plan's two columns, the Coach destination) are different controls."""
    ready(app, auth_user.id, "en")
    gate.page.add_init_script(_FOCUS_JS)
    gate.visit(SURFACES[surface], 1366)
    gate.page.wait_for_timeout(200)       # the ring transitions in over 150ms
    _assert_focus_is_perceptible(_keyboard_walk(gate.page, limit=20),
                                 "surface=%s viewport=1366" % surface)


def test_account_language_choice_is_keyboard_operable(app, auth_user, gate):
    """PR7 exposes aria-pressed on the language choices and proves the goal
    choice by keyboard; the language switch itself had no keyboard proof."""
    ready(app, auth_user.id, "tr")
    gate.visit("/edit-profile", 390)
    option = gate.page.locator('.hub-lang-opt[data-args=\'["en"]\']')
    option.focus()
    assert gate.page.evaluate("() => document.activeElement.matches('.hub-lang-opt')")
    with gate.page.expect_navigation():
        gate.page.keyboard.press("Enter")
    settle_reads = [p for p, m, _s in gate.traffic if p == "/set-language" and m == "POST"]
    assert settle_reads, "Enter on the EN option issued no language change"
    with app.app_context():
        assert db.session.get(User, auth_user.id).language == "en"
    assert gate.page.evaluate("() => document.documentElement.lang") == "en"


def test_notification_retry_controls_are_keyboard_operable(app, auth_user, make_user, gate):
    """PR7's retries are proven with pointer clicks; a keyboard user must be
    able to reach and fire both the first-load and the page-N retry."""
    from test_ux4_pr7_browser import SCROLL_END, _seed_notifications
    actor = make_user("pr8keyboardactor")
    _seed_notifications(app, auth_user.id, actor.id, 25)
    ready(app, auth_user.id, "en")

    gate.overrides["/notifications/data"] = (503, '{"error":"unavailable"}')
    gate.visit("/notifications", 390)
    retry = gate.page.locator("#notif-retry")
    retry.focus()
    assert gate.page.evaluate("() => document.activeElement.id") == "notif-retry"
    del gate.overrides["/notifications/data"]
    gate.page.keyboard.press("Enter")
    gate.page.wait_for_selector(".notif-row")
    assert gate.page.locator(".notif-row").count() == 20

    gate.overrides["/notifications/data"] = (503, '{"error":"unavailable"}')
    gate.page.evaluate(SCROLL_END)
    gate.page.wait_for_selector("#notif-page-retry")
    gate.page.locator("#notif-page-retry").focus()
    del gate.overrides["/notifications/data"]
    gate.page.keyboard.press(" ")
    gate.page.wait_for_function("() => document.querySelectorAll('.notif-row').length === 25")


@pytest.mark.parametrize("path,opener,first_inside", [
    ("/edit-profile", '[data-action="openEditSheet"]', "#edit-sheet"),
    ("/progress-page", '[data-action="openCheckin"]', "#checkin-sheet"),
])
def test_sheets_take_focus_and_return_it_to_the_opener(app, auth_user, gate,
                                                       path, opener, first_inside):
    """No product dialog has migrated to the shared AxisModal primitive yet
    (PR2 proves the primitive itself). These two sheets implement focus-on-open
    and focus-return and Escape by hand; the gate holds them to what they
    promise and invents no containment they do not claim."""
    ready(app, auth_user.id, "en")
    gate.visit(path, 390)
    button = gate.page.locator(opener).first
    button.focus()
    gate.page.keyboard.press("Enter")
    gate.page.wait_for_function(
        "sel => document.querySelector(sel).contains(document.activeElement)", arg=first_inside)
    gate.page.keyboard.press("Escape")
    gate.page.wait_for_function(
        "sel => !document.querySelector(sel).classList.contains('open')", arg=first_inside)
    assert gate.page.evaluate(
        "sel => document.activeElement === document.querySelector(sel)", opener), (
        "%s: focus did not return to the opener" % path)


# ── partial-failure isolation ───────────────────────────────────────────────

SECTIONS = r"""
() => Object.fromEntries([...document.querySelectorAll('main section')].map((s, i) =>
  [s.getAttribute('aria-labelledby') || s.className || String(i),
   s.innerText.replace(/\s+/g, ' ').trim()]))
"""

# failing read -> the section(s) allowed to change. Everything else on the page
# must render exactly as it does when every read succeeds.
PARTIAL = [
    ("today", "scheduled", "/meal-log/today", {"today-status-label"}),
    ("today", "scheduled", "/water", {"today-status-label"}),
    ("today", "scheduled", "/checkin-history", {"today-progress-label"}),
    ("today", "scheduled", "/leaderboard/reward-check", set()),
    ("today", "scheduled", "/notifications/unread-count", set()),
    ("progress", "populated", "/api/progress/summary", {"ps-h", "tr-h"}),
    ("progress", "populated", "/api/progress/history", {"ph-h"}),
    ("progress", "populated", "/api/progress/axis-insights", {"ai-h"}),
    ("progress", "populated", "/api/progress/physique", {"pp-h"}),
]


@pytest.mark.parametrize("surface,state,read,owners", PARTIAL,
                         ids=["%s-%s" % (s, r.strip("/").replace("/", "_"))
                              for s, _st, r, _o in PARTIAL])
def test_a_failing_secondary_read_degrades_only_its_own_section(
        app, client, auth_user, gate, monkeypatch, surface, state, read, owners):
    ready(app, auth_user.id, "en")
    path = seed_state(app, client, monkeypatch, auth_user.id, surface, state)
    gate.visit(path, 390)
    healthy = gate.probe(SECTIONS)
    healthy_primary = gate.probe(
        "() => [...document.querySelectorAll('main .btn-volt')].map(e => e.textContent.trim())")

    gate.overrides[read] = (500, '{"error":"unavailable"}')
    gate.visit(path, 390)
    degraded = gate.probe(SECTIONS)
    where = "surface=%s viewport=390 locale=en state=%s failing=%s" % (surface, state, read)
    problems = []
    if set(degraded) != set(healthy):
        problems.append("%s invariant=sections-survive: %s -> %s" % (
            where, sorted(healthy), sorted(degraded)))
    for name, text in healthy.items():
        if name in owners:
            if not degraded.get(name) or degraded.get(name) == text:
                problems.append("%s invariant=owner-shows-its-failure: %s" % (where, name))
        elif degraded.get(name) != text:
            problems.append("%s invariant=isolation: %s changed to %r" % (
                where, name, (degraded.get(name) or "")[:80]))
    facts = gate.probe()
    if len(facts["h1"]) != 1:
        problems.append("%s invariant=one-h1: %s" % (where, facts["h1"]))
    primary = gate.probe(
        "() => [...document.querySelectorAll('main .btn-volt')].map(e => e.textContent.trim())")
    if primary != healthy_primary:
        problems.append("%s invariant=dominant-action-survives: %s -> %s" % (
            where, healthy_primary, primary))
    if gate.probe("() => [...document.querySelectorAll('.loading-text, .skeleton')]"
                  ".filter(e => e.checkVisibility()).length"):
        problems.append("%s invariant=no-permanent-loading" % where)
    _assert_clean(problems, gate.errors)


# ── F-40 - error documents ──────────────────────────────────────────────────

ERROR_PROBE = r"""
() => {
  const h1 = [...document.querySelectorAll('h1')];
  const links = [...document.querySelectorAll('a[href]')];
  const r = links.length ? links[0].getBoundingClientRect() : null;
  return {h1: h1.map(h => h.textContent.trim()),
          size: h1.length ? parseFloat(getComputedStyle(h1[0]).fontSize) : null,
          face: h1.length ? getComputedStyle(h1[0]).fontFamily : null,
          bodyFace: getComputedStyle(document.body).fontFamily,
          links: links.map(a => [a.getAttribute('href'), a.textContent.trim()]),
          linkBox: r ? {w: r.width, h: r.height} : null,
          lang: document.documentElement.lang,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1};
}
"""


@pytest.mark.parametrize("code", ["404", "500"])
def test_error_documents_are_on_scale_responsive_and_keyboard_visible(
        app, client, gate, monkeypatch, code):
    """F-40 in the browser: the numeral is obvious but on the product scale
    (38-52px, never 120px), one route home that is a real 44px target with a
    visible focus ring, no overflow from 320 to 1366, in TR and EN."""
    if code == "404":
        path = "/pr8-missing-page"
    else:
        path = "/login"
        endpoint = app.url_map.bind("localhost").match(path)[0]
        app.config["PROPAGATE_EXCEPTIONS"] = False

        def boom(*_a, **_kw):
            raise RuntimeError("pr8 induced failure")
        monkeypatch.setitem(app.view_functions, endpoint, boom)
    gate.page.add_init_script(_FOCUS_JS)
    problems = []
    for language in LOCALES:
        client.post("/set-language", json={"lang": language})
        home = CATALOGUES[language]["error.back_home"]
        for width in WIDTHS:
            gate.visit(path, width)
            where = "surface=error-%s viewport=%d locale=%s" % (code, width, language)
            f = gate.probe(ERROR_PROBE)
            if f["h1"] != [code]:
                problems.append("%s invariant=one-h1: %s" % (where, f["h1"]))
            if not 38 <= f["size"] <= 52:
                problems.append("%s invariant=numeral-on-display-scale: %.1fpx" % (where, f["size"]))
            if "Bebas Neue" not in f["face"] or "Inter" not in f["bodyFace"]:
                problems.append("%s invariant=type-pairing: %s / %s" % (where, f["face"], f["bodyFace"]))
            if f["links"] != [["/", home]]:
                problems.append("%s invariant=one-route-home: %s" % (where, f["links"]))
            if f["linkBox"]["h"] < 44:
                problems.append("%s invariant=home-target-size: %s" % (where, f["linkBox"]))
            if f["overflow"]:
                problems.append("%s invariant=no-horizontal-overflow" % where)
            if f["lang"] != language:
                problems.append("%s invariant=document-language: %s" % (where, f["lang"]))
        stops = _keyboard_walk(gate.page, limit=3)
        _assert_focus_is_perceptible(stops, "error-%s %s" % (code, language))
    _assert_clean(problems, gate.errors)
