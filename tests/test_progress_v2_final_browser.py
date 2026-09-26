"""Progress V2 PR5 — the integrated page in a real browser.

Hermetic Chromium (tests/ux4_gate_support.py): the real template + static
bundle of this commit, served by the Flask test client. All four Progress
reads are fulfilled with payloads produced by the SERVER's own projection
functions (the same fixtures the PR3 and PR4 browser matrices use), so each
state is exactly what production would publish:

  on_track        on track · populated trends · physique comparison · history
  needs_attention needs attention · empty physique · several days incl. same-day
  baseline        building baseline · empty physique · one check-in
  failure         every Progress read fails

For every state × 320/390/430/768/1024/1366/1600 × EN/TR it proves the page as
one system: no horizontal overflow, no section left on "Loading…", no
`[hidden]` node that still takes layout, no enum/placeholder leakage, every
relevant control ≥ 44px and inside its section, no control collides with a
neighbour, trend drawings stay inside their card and carry a text
equivalent, the hierarchy holds (Current State headline is the page's
largest body text after the title), and the page makes exactly its four
existing reads with the PR4 static budget.

Set PR5_QA_SHOTS=<dir> to also save full-page screenshots per cell.

    python -m pytest tests/test_progress_v2_final_browser.py -v
"""
import json
import os

import pytest

from ux4_gate_support import gate, ready  # noqa: F401  (fixture re-export)

from test_progress_axis_insight_browser import _payloads
from test_progress_physique_history_browser import _history, _physique

pytestmark = pytest.mark.filterwarnings("ignore")

SUMMARY = "/api/progress/summary"
AXIS = "/api/progress/axis-insights"
PHYSIQUE = "/api/progress/physique"
HISTORY = "/api/progress/history"
READS = sorted([SUMMARY, AXIS, PHYSIQUE, HISTORY])
WIDTHS = (320, 390, 430, 768, 1024, 1366, 1600)

# PR4 baseline (origin/main 7310d63): the page's own static files, counted
# once per visit (analytics.js included). Fonts and third-party requests are
# aborted by the harness.
STATIC_BUDGET = 14


def _several_with_same_day():
    history = _history("several")
    history["entries"].insert(1, dict(history["entries"][0],
                                      checked_in_at="2026-09-18T08:00:00+03:00"))
    return history


STATES = {
    "on_track": ("on_track", "comparison", "several"),
    "needs_attention": ("needs_attention", "empty", "several_same_day"),
    "baseline": ("baseline", "empty", "one"),
    "failure": (None, None, None),
}


def _stage(gate, state):  # noqa: F811
    summary_kind, phys_kind, hist_kind = STATES[state]
    if state == "failure":
        for path in READS:
            gate.overrides[path] = (500, json.dumps({"error": "x"}))
        return
    summary, axis = _payloads(summary_kind)
    history = (_several_with_same_day() if hist_kind == "several_same_day"
               else _history(hist_kind))
    gate.overrides[SUMMARY] = (200, json.dumps(summary))
    gate.overrides[AXIS] = (200, json.dumps(axis))
    gate.overrides[PHYSIQUE] = (200, json.dumps(_physique(phys_kind)))
    gate.overrides[HISTORY] = (200, json.dumps(history))


SETTLED = """() => {
  const ax = document.getElementById('ax-card');
  return ax && ax.getAttribute('data-status') !== 'loading'
    && !document.querySelector('main .prog-loading')
    && document.getElementById('ps-card').hasAttribute('data-state');
}"""


PROBE = r"""
() => {
  const main = document.querySelector('main');
  const box = el => { const r = el.getBoundingClientRect();
                      return {top: r.top + scrollY, bottom: r.bottom + scrollY,
                              left: r.left, right: r.right,
                              width: r.width, height: r.height}; };
  const visible = el => el.checkVisibility() && el.getBoundingClientRect().height > 0;
  const px = el => parseFloat(getComputedStyle(el).fontSize);
  const controls = [...main.querySelectorAll('a[href], button')].filter(visible);
  const sections = [...main.querySelectorAll('[data-progress-section]')];
  const sectionOf = el => el.closest('[data-progress-section]');
  const vw = document.documentElement.clientWidth;
  const text = main.innerText;
  const bodyText = [...main.querySelectorAll('p, li, span, time, h2, h3, a, button')]
      .filter(visible).filter(el => !el.closest('.ps-state, .wc-value, .page-hdr'));
  return {
    overflow: document.documentElement.scrollWidth > vw,
    loading: main.querySelectorAll('.prog-loading').length,
    hiddenTakesSpace: [...main.querySelectorAll('[hidden]')]
        .filter(el => getComputedStyle(el).display !== 'none')
        .map(el => el.id || el.className),
    leaks: ['undefined', 'null', 'NaN', '{', '}', 'progress.']
        .filter(tok => text.includes(tok)),
    enumLike: (text.match(/\b[a-z]+_[a-z_]+\b/g) || []),
    controls: controls.map(el => ({
      tag: el.tagName, cls: el.className, text: el.textContent.trim(),
      section: sectionOf(el) && sectionOf(el).getAttribute('data-progress-section'),
      box: box(el),
      fits: el.getBoundingClientRect().right <= vw + 0.5 && el.getBoundingClientRect().left >= -0.5,
      marginTop: parseFloat(getComputedStyle(el).marginTop),
      marginBottom: parseFloat(getComputedStyle(el).marginBottom),
    })),
    svgs: [...main.querySelectorAll('.tr-svg')].map(svg => {
      const card = svg.closest('.wc-card').getBoundingClientRect();
      const r = svg.getBoundingClientRect();
      const viz = svg.closest('.tr-viz');
      return {inside: r.left >= card.left - 0.5 && r.right <= card.right + 0.5,
              width: r.width, height: r.height,
              ariaHidden: svg.getAttribute('aria-hidden'),
              textEquivalent: (viz.querySelector('.tr-sr') || {}).textContent || ''};
    }),
    stateText: document.getElementById('ps-state').textContent.trim(),
    statePx: px(document.getElementById('ps-state')),
    h1Px: px(document.querySelector('h1')),
    maxBodyPx: Math.max(0, ...bodyText.map(px)),
    upperCaseLabels: [...main.querySelectorAll('*')].filter(visible)
        .filter(el => getComputedStyle(el).textTransform === 'uppercase')
        .map(el => el.className),
    imgs: [...main.querySelectorAll('img')].map(i => ({
      loading: i.loading, w: i.getBoundingClientRect().width,
      h: i.getBoundingClientRect().height,
      alt: i.getAttribute('alt')})),
    dom: {page: document.querySelectorAll('*').length,
          main: main.querySelectorAll('*').length,
          history: document.querySelectorAll('#history-list *').length},
    sections: sections.map(s => ({id: s.getAttribute('data-progress-section'),
                                  box: box(s)})),
    headings: [...document.querySelectorAll('h1, h2, h3, h4')].filter(visible)
        .map(h => h.tagName),
    pageHeight: document.documentElement.scrollHeight,
    trendTops: [...main.querySelectorAll('.wc-card')].map(c => Math.round(c.getBoundingClientRect().top)),
  };
}
"""


def _overlap(a, b):
    return not (a["right"] <= b["left"] + 0.5 or b["right"] <= a["left"] + 0.5
                or a["bottom"] <= b["top"] + 0.5 or b["bottom"] <= a["top"] + 0.5)


@pytest.mark.parametrize("locale", ["en", "tr"])
@pytest.mark.parametrize("state", sorted(STATES))
def test_progress_v2_integrated_page(app, gate, make_user, login, state, locale):  # noqa: F811
    name = "pr5v2_%s_%s" % (state[:10], locale)
    user = make_user(name)
    ready(app, user.id, language=locale)
    login(name)
    _stage(gate, state)
    shots = os.environ.get("PR5_QA_SHOTS")
    audit = {}
    problems = []

    def check(ok, detail):
        # Every broken invariant of every cell is collected, so one run names
        # the whole defect set instead of stopping at the first.
        if not ok:
            problems.append(repr(detail)[:600])

    for width in WIDTHS:
        gate.visit("/progress-page", width=width)
        gate.page.wait_for_function(SETTLED, timeout=5000)
        f = gate.page.evaluate(PROBE)
        cell = (state, locale, width)
        audit[width] = {k: f[k] for k in ("dom", "pageHeight", "controls",
                                          "upperCaseLabels", "sections")}
        audit[width]["static"] = len(gate.static_reads())

        check(not gate.errors, (cell, gate.errors))
        check(not f["overflow"], cell)
        check(f["loading"] == 0, cell)
        check(f["hiddenTakesSpace"] == [], (cell, f["hiddenTakesSpace"]))
        check(f["leaks"] == [], (cell, f["leaks"]))
        check(f["enumLike"] == [], (cell, f["enumLike"]))

        # Controls: tappable, inside the viewport, never overlapping.
        for c in f["controls"]:
            check(c["fits"], (cell, c))
            check(c["box"]["height"] >= 44, (cell, c))
            # PR4 P2: the hit area is structural, not borrowed via a
            # negative margin that overlaps the neighbouring line.
            check(c["marginTop"] >= 0 and c["marginBottom"] >= 0, (cell, c))
        for i, a in enumerate(f["controls"]):
            for b in f["controls"][i + 1:]:
                check(not _overlap(a["box"], b["box"]), (cell, a, b))

        # Trends drawings: inside their card, aria-hidden, text equivalent.
        for svg in f["svgs"]:
            check(svg["inside"] and svg["width"] > 0, (cell, svg))
            check(svg["ariaHidden"] == "true", (cell, svg))
            check(svg["textEquivalent"].strip(), (cell, svg))

        # Hierarchy: Current State's headline is louder than any body text.
        # A failed read is a quiet notice, not the page's verdict.
        if state == "failure":
            check(f["statePx"] < f["h1Px"] / 2, (cell, f["statePx"]))
        else:
            check(f["statePx"] > f["maxBodyPx"], (cell, f["statePx"], f["maxBodyPx"]))
        # Headings never skip a level.
        levels = [int(h[1]) for h in f["headings"]]
        check(levels[0] == 1, (cell, levels))
        for prev, nxt in zip(levels, levels[1:]):
            check(nxt <= prev + 1, (cell, levels))

        for img in f["imgs"]:
            check(img["loading"] == "lazy" and img["alt"], (cell, img))
            check(img["w"] <= 141, (cell, img))

        # Trends: one row of three from tablet up (no orphaned third card),
        # a single column on phones.
        if width >= 768:
            check(len(set(f["trendTops"])) == 1, (cell, f["trendTops"]))
        elif width <= 430:
            check(len(set(f["trendTops"])) == 3, (cell, f["trendTops"]))

        order = [s["id"] for s in f["sections"]]
        check(order == ["header", "current-state", "trends", "axis-insight",
                         "physique", "recent-checkins"], cell)
        tops = [s["box"]["top"] for s in f["sections"]]
        check(tops == sorted(tops), cell)

        reads = [p for p in gate.app_reads() if p.startswith("/api/")]
        check(sorted(reads) == READS, (cell, reads))
        statics = [p for p in gate.static_reads()
                   if not p.endswith(".png")]   # fixture photo is a /static file
        check(len(statics) <= STATIC_BUDGET, (cell, statics))

        if shots:
            os.makedirs(shots, exist_ok=True)
            gate.page.screenshot(
                path=os.path.join(shots, "%s_%s_%d.png" % (state, locale, width)),
                full_page=True)

    if shots:
        with open(os.path.join(shots, "audit_%s_%s.json" % (state, locale)), "w",
                  encoding="utf-8") as fh:
            json.dump(audit, fh, indent=1, ensure_ascii=False)
    assert problems == [], "\n".join(problems[:40])


DISCLOSURE_PROBE = r"""
() => {
  const hist = document.getElementById('history-list');
  const more = hist.querySelector('.hist-more');
  const r = el => { const b = el.getBoundingClientRect();
                    return {top: b.top, bottom: b.bottom, left: b.left, right: b.right,
                            width: b.width, height: b.height}; };
  const row = more.closest('.hist-item');
  return {
    live: hist.getAttribute('aria-live'),
    busy: hist.getAttribute('aria-busy'),
    expanded: more.getAttribute('aria-expanded'),
    controls: more.getAttribute('aria-controls'),
    updates: hist.querySelectorAll('.hist-updates li').length,
    nodes: hist.querySelectorAll('*').length,
    more: r(more),
    neighbours: [...row.querySelectorAll('.hist-summary, .hist-metric, .hist-date, .hist-updates')]
        .map(r),
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  };
}
"""


@pytest.mark.parametrize("locale, width", [("en", 390), ("tr", 320), ("en", 1366)])
def test_same_day_disclosure_is_announced_by_state_not_by_a_live_region(
        app, gate, make_user, login, locale, width):  # noqa: F811
    """State 4 (same-day expanded) + PR4 P2 A/C.

    The history list is not a live region, so expanding a day announces the
    button's new aria-expanded state instead of reading the appended rows; the
    list's aria-busy clears once its read settles; the 44px control collides
    with nothing before or after expansion; rows exist only while expanded."""
    name = "pr5disc_%s_%d" % (locale, width)
    user = make_user(name)
    ready(app, user.id, language=locale)
    login(name)
    _stage(gate, "needs_attention")
    gate.visit("/progress-page", width=width)
    gate.page.wait_for_function(SETTLED, timeout=5000)

    before = gate.page.evaluate(DISCLOSURE_PROBE)
    assert before["live"] is None and before["busy"] is None
    assert before["expanded"] == "false" and before["updates"] == 0
    assert before["more"]["height"] >= 44

    more = gate.page.locator(".hist-more").first
    more.focus()
    gate.page.keyboard.press("Enter")
    after = gate.page.evaluate(DISCLOSURE_PROBE)
    assert after["expanded"] == "true"
    assert after["controls"] and gate.page.locator("#" + after["controls"]).count() == 1
    assert after["updates"] == 2
    assert not after["overflow"]
    for other in after["neighbours"]:
        assert not _overlap(after["more"], other), (after["more"], other)
    # Bounded: expanding one day adds that day's rows and nothing else.
    assert after["nodes"] - before["nodes"] <= 8, (before["nodes"], after["nodes"])

    shots = os.environ.get("PR5_QA_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
        gate.page.locator("#progress-recent-checkins").screenshot(
            path=os.path.join(shots, "same_day_expanded_%s_%d.png" % (locale, width)))

    gate.page.keyboard.press("Enter")
    collapsed = gate.page.evaluate(DISCLOSURE_PROBE)
    assert collapsed["expanded"] == "false" and collapsed["updates"] == 0
    assert collapsed["nodes"] == before["nodes"]
    assert not gate.errors


FOCUS_PROBE = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const s = getComputedStyle(el);
  return {
    inMain: !!el.closest('main'),
    text: el.textContent.trim().slice(0, 40),
    ring: (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2)
          || (s.boxShadow && s.boxShadow !== 'none'),
  };
}
"""


def test_every_progress_control_is_keyboard_reachable_with_a_visible_focus(
        app, gate, make_user, login):  # noqa: F811
    user = make_user("pr5kbd")
    ready(app, user.id, language="en")
    login("pr5kbd")
    _stage(gate, "on_track")
    gate.visit("/progress-page", width=390)
    gate.page.wait_for_function(SETTLED, timeout=5000)
    expected = gate.page.evaluate(
        "() => [...document.querySelectorAll('main a[href], main button')]"
        ".filter(el => el.checkVisibility()).map(el => el.textContent.trim().slice(0, 40))")
    assert expected

    reached = []
    for _ in range(80):
        gate.page.keyboard.press("Tab")
        f = gate.page.evaluate(FOCUS_PROBE)
        if f and f["inMain"]:
            assert f["ring"], f
            reached.append(f["text"])
        if len(reached) and f and not f["inMain"] and set(expected) <= set(reached):
            break
    assert set(expected) <= set(reached), (expected, reached)


def test_reduced_motion_leaves_nothing_animating(app, gate, make_user, login):  # noqa: F811
    """The page adds no motion of its own: after the reads settle nothing is
    running, and nothing needs a prefers-reduced-motion override."""
    user = make_user("pr5motion")
    ready(app, user.id, language="en")
    login("pr5motion")
    _stage(gate, "on_track")
    gate.page.emulate_media(reduced_motion="reduce")
    gate.visit("/progress-page", width=390)
    gate.page.wait_for_function(SETTLED, timeout=5000)
    running = gate.page.evaluate(
        "() => document.getAnimations().filter(a => a.playState === 'running')"
        ".filter(a => a.effect && a.effect.target && a.effect.target.closest('main'))"
        ".length")
    assert running == 0


@pytest.mark.parametrize("locale,width", [("en", 1366), ("en", 390), ("tr", 320)])
@pytest.mark.parametrize("count,older", [(12, False), (12, True), (13, False)])
def test_same_day_saturation_from_real_endpoint(app, gate, make_user, login, locale, width, count, older):
    from datetime import datetime, timedelta
    from app.extensions import db
    from test_progress_history import END_DAY, _add_checkin

    name = "pr5sat"
    user = make_user(name)
    ready(app, user.id, language=locale)
    login(name)
    for minute in range(count):
        _add_checkin(user.id, END_DAY, 80.0,
                     created_at=datetime(2026, 7, 15, 10, minute))
    if older:
        _add_checkin(user.id, END_DAY - timedelta(days=1), 81.0)
    db.session.commit()
    _stage(gate, "on_track")
    del gate.overrides[HISTORY]  # Exercise the real DB -> API -> browser path.
    gate.visit("/progress-page", width=width)
    gate.page.wait_for_function(SETTLED, timeout=5000)
    expected = ({"en": "12+ updates", "tr": "12+ g\u00fcncelleme"} if count > 12 else
                {"en": "12 check-ins this day", "tr": "Bu g\u00fcn 12 check-in"})[locale]
    more = gate.page.locator(".hist-more")
    assert more.inner_text() == expected
    before = gate.page.locator("#history-list *").count()
    assert gate.page.locator(".hist-update").count() == 0
    more.focus()
    gate.page.keyboard.press("Enter")
    assert gate.page.locator(".hist-update").count() == 12
    assert more.evaluate("el => el === document.activeElement")
    gate.page.keyboard.press("Enter")
    assert gate.page.locator("#history-list *").count() == before
    assert sorted(p for p in gate.app_reads() if p.startswith("/api/")) == READS
    assert not gate.page.evaluate(PROBE)["overflow"]
    assert not gate.errors


@pytest.mark.parametrize("locale,width", [("en", 1366), ("tr", 320)])
def test_physique_region_switch_retains_keyboard_focus(app, gate, make_user, login, locale, width):
    name = "pr5region"
    user = make_user(name)
    ready(app, user.id, language=locale)
    login(name)
    _stage(gate, "on_track")
    payload = _physique("comparison")
    payload["regions"].append(dict(payload["regions"][0], body_region="lower_body"))
    gate.overrides[PHYSIQUE] = (200, json.dumps(payload))
    gate.visit("/progress-page", width=width)
    gate.page.wait_for_function(SETTLED, timeout=5000)
    gate.page.locator('[role="radio"][aria-checked="true"]').focus()
    for selected, key in [("lower_body", "ArrowRight"), ("upper_body", "ArrowLeft")]:
        payload["selected_region"] = selected
        gate.overrides[PHYSIQUE] = (200, json.dumps(payload))
        gate.page.keyboard.press(key)
        gate.page.wait_for_function("region => document.querySelector('#physique-body').dataset.region === region", arg=selected)
        assert gate.page.locator('[role="radio"][aria-checked="true"]').evaluate(
            "el => el === document.activeElement")
        assert gate.page.evaluate(FOCUS_PROBE)["ring"]
        assert gate.page.locator("#physique-body").get_attribute("aria-busy") is None
    assert not gate.errors


@pytest.mark.parametrize("locale", ["en", "tr"])
def test_trimmed_history_archive_copy_does_not_claim_twelve(app, gate, make_user, login, locale):
    from datetime import datetime, timedelta
    from app.extensions import db
    from test_progress_history import END_DAY, _add_checkin

    user = make_user("pr5trimcopy")
    ready(app, user.id, language=locale)
    login("pr5trimcopy")
    _add_checkin(user.id, END_DAY, 80.0)
    for minute in range(13):
        _add_checkin(user.id, END_DAY - timedelta(days=1), 81.0,
                     created_at=datetime(2026, 7, 14, 10, minute))
    db.session.commit()
    _stage(gate, "on_track")
    del gate.overrides[HISTORY]
    gate.visit("/progress-page", width=390)
    gate.page.wait_for_function(SETTLED, timeout=5000)
    assert gate.page.locator(".hist-item").count() == 1
    expected = {"en": "Older check-ins are not shown.",
                "tr": "Daha eski check-in'ler g\u00f6sterilmiyor."}[locale]
    assert gate.page.locator(".hist-archive").inner_text() == expected
    assert sorted(p for p in gate.app_reads() if p.startswith("/api/")) == READS


def test_delayed_physique_response_does_not_steal_focus(app, gate, make_user, login):
    user = make_user("pr5slowfocus")
    ready(app, user.id, language="en")
    login("pr5slowfocus")
    _stage(gate, "on_track")
    payload = _physique("comparison")
    payload["regions"].append(dict(payload["regions"][0], body_region="lower_body"))
    gate.overrides[PHYSIQUE] = (200, json.dumps(payload))
    gate.visit("/progress-page", width=390)
    gate.page.wait_for_function(SETTLED, timeout=5000)
    gate.page.evaluate("""() => {
      const fetch = window.fetch;
      window.fetch = (...args) => String(args[0]).includes('/physique?')
        ? new Promise(resolve => { window.releasePhysique = () => fetch(...args).then(resolve); })
        : fetch(...args);
    }""")
    payload["selected_region"] = "lower_body"
    gate.overrides[PHYSIQUE] = (200, json.dumps(payload))
    gate.page.locator('[role="radio"][aria-checked="true"]').focus()
    gate.page.keyboard.press("ArrowRight")
    gate.page.wait_for_function("() => !!window.releasePhysique")
    gate.page.keyboard.press("Tab")
    gate.page.keyboard.press("Tab")
    assert not gate.page.evaluate("() => !!document.activeElement.closest('#physique-body')")
    gate.page.evaluate("() => { window.focusAfterTab = document.activeElement; }")
    assert not gate.page.evaluate("() => window.focusAfterTab.matches('[role=radio]')")
    gate.page.evaluate("() => window.releasePhysique()")
    gate.page.wait_for_function("() => document.querySelector('#physique-body').dataset.region === 'lower_body'")
    assert gate.page.evaluate("() => document.activeElement === window.focusAfterTab")
