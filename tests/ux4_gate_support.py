"""WEB-UX4-PR8 - shared machinery for the cross-surface consistency gate.

Not a test module (no ``test_`` prefix): the PR8 gate files import from here
so the browser harness, the surface inventory and the in-page probes exist
exactly once. Earlier UX4 PRs each grew a private copy of the router; PR8 does
not add a ninth.

Everything is hermetic. Chromium's HTTP is served by the authenticated Flask
test client through a Playwright route handler; every non-localhost request
(Google Fonts, analytics, CDNs) is aborted. The runtime under test is the real
template + static bundle of the checked-out commit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.extensions import db
from app.models import User

ROOT = Path(__file__).resolve().parents[1]

# The UX4-covered product surfaces. Today / Plan / Coach / Progress are the
# primary IA; Nutrition and Supplements are Plan children; Account and
# Notifications are the utility surfaces. Community (feed, friends, quests,
# challenges, leaderboard) was explicitly deferred by UX4 discovery and is NOT
# scanned here - a gate that fails on out-of-scope pages protects nothing.
SURFACES = {
    "today": "/",
    "plan": "/training",
    "coach": "/coach",
    "nutrition": "/nutrition",
    "supplements": "/supplements",
    "progress": "/progress-page",
    "account": "/edit-profile",
    "notifications": "/notifications",
}
AUTH_SURFACES = {
    "login": "/login",
    "register": "/register",
    "forgot_password": "/forgot-password",
}
WIDTHS = (320, 390, 768, 1024, 1366)
LOCALES = ("tr", "en")

STATIC_PREFIX = "/static/"


def load_locale(code: str) -> dict[str, str]:
    with open(ROOT / "locales" / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def ready(app, user_id, language="en"):
    """Profile-complete user in ``language`` with the production UI flags.

    Production runs Coach V2 ON (activated 2026-09-16); Plan V2 is
    unconditional since WEB-UX3-PR6B. The weekly program section is enabled
    the same way the PR5 hierarchy harness enables it.
    """
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
    with app.app_context():
        user = db.session.get(User, user_id)
        user.profile_complete = True
        user.language = language
        db.session.commit()


class GatePage:
    """A Chromium page, the app-request log it produced, and page errors."""

    def __init__(self, page, traffic, errors, overrides):
        self.page = page
        self.traffic = traffic
        self.errors = errors
        # path -> (status, body) served instead of the Flask response, for
        # deterministic partial-failure states.
        self.overrides = overrides
        # "<host> <resource type>" for every third-party request the page
        # attempted in the current visit (all of them are aborted).
        self.external_all = []

    @property
    def external(self):
        return sorted(set(self.external_all))

    def visit(self, path, width=390, height=900):
        self.page.set_viewport_size({"width": width, "height": height})
        # The Coach widget persists its transcript (greeting included) in
        # sessionStorage, so a locale switch inside one tab would repaint the
        # previous language's greeting. Every cell starts from a clean tab
        # state, as a fresh visit in that locale would.
        if self.page.url.startswith("http://localhost"):
            self.page.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
        self.traffic.clear()
        self.external_all.clear()
        self.page.goto("http://localhost" + path, wait_until="domcontentloaded")
        settle(self.page)
        return self

    def app_reads(self):
        return [p for p, _m, _s in self.traffic if not p.startswith(STATIC_PREFIX)]

    def static_reads(self):
        return [p for p, _m, _s in self.traffic if p.startswith(STATIC_PREFIX)]

    def probe(self, js=None):
        return self.page.evaluate(js or SEMANTIC_PROBE)


def settle(page):
    """Wait for the document's async slots to reach a settled state.

    ``networkidle`` is Playwright's own "no request for 500ms" signal - it is a
    condition, not a sleep. The Coach widget mounts from a module and hydrates
    history once; wait for its composer when it exists, bounded.
    """
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_function(
            "() => { const w = document.getElementById('cw-window');"
            " if (!w) return true;"
            " const i = document.getElementById('cw-input');"
            " return !!i && i.getBoundingClientRect().height > 0; }",
            timeout=4000)
    except Exception:
        pass


def _router(client, traffic, overrides):
    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "localhost":
            route.abort()
            return
        if url.path in overrides:
            status, body = overrides[url.path]
            traffic.append((url.path, request.method, status))
            route.fulfill(status=status, content_type="application/json", body=body)
            return
        response = client.open(
            url.path + ("?" + url.query if url.query else ""),
            method=request.method, data=request.post_data,
            headers={k: v for k, v in request.headers.items()
                     if k.lower() in {"content-type", "origin", "x-csrftoken",
                                      "accept", "if-match", "idempotency-key"}})
        traffic.append((url.path, request.method, response.status_code))
        route.fulfill(status=response.status_code, body=response.get_data(),
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in {"content-length", "set-cookie"}})
    return route_request


@pytest.fixture
def gate(client):
    """One Chromium per test; no xdist, no shared browser across tests."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(bypass_csp=True,
                                      viewport={"width": 390, "height": 900})
        traffic, errors, overrides = [], [], {}
        context.route("**/*", _router(client, traffic, overrides))
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        gate_page = GatePage(page, traffic, errors, overrides)

        def external(request):
            host = urlsplit(request.url).netloc
            if host != "localhost":
                gate_page.external_all.append("%s %s" % (host, request.resource_type))

        page.on("request", external)
        yield gate_page
        browser.close()


# ── in-page probes ──────────────────────────────────────────────────────────
#
# One probe returns every per-cell fact. Each invariant is a separate key so a
# failure names exactly which invariant regressed, never "something differs".

SEMANTIC_PROBE = r"""
() => {
  const vw = document.documentElement.clientWidth;
  // Exposed to assistive tech: not display:none / visibility:hidden / [hidden],
  // not inside aria-hidden or inert. Visually-hidden (clipped) text counts -
  // it is a legitimate accessible-name / heading technique.
  const exposed = el => {
    if (!el.checkVisibility({visibilityProperty: true})) return false;
    return !el.closest('[aria-hidden="true"], [inert]');
  };
  // Painted for a sighted user.
  const painted = el => {
    if (!el.checkVisibility({checkOpacity: true, visibilityProperty: true})) return false;
    if (el.closest('[aria-hidden="true"], [inert]')) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const label = el => {
    const id = el.id ? '#' + el.id : '';
    const cls = String(el.className && el.className.baseVal !== undefined
                       ? el.className.baseVal : el.className || '')
      .split(/\s+/).filter(Boolean).slice(0, 2).map(c => '.' + c).join('');
    return el.tagName.toLowerCase() + id + cls;
  };
  const textOf = node => {
    // Accessible text of the subtree, ignoring aria-hidden descendants.
    let out = '';
    for (const c of node.childNodes) {
      if (c.nodeType === 3) out += c.textContent;
      else if (c.nodeType === 1) {
        // Hidden descendants name nothing: a `hidden` "0" badge inside an
        // icon-only link must not pass as that link's accessible name.
        if (c.getAttribute('aria-hidden') === 'true') continue;
        if (c.hidden || !c.checkVisibility({visibilityProperty: true})) continue;
        if (c.tagName === 'IMG') out += ' ' + (c.getAttribute('alt') || '');
        else if (c.tagName === 'svg' || c.tagName === 'SVG') {
          const t = c.querySelector('title');
          out += ' ' + (c.getAttribute('aria-label') || (t ? t.textContent : ''));
        } else out += ' ' + textOf(c);
      }
    }
    return out;
  };
  const byIds = ids => (ids || '').split(/\s+/).filter(Boolean)
    .map(i => document.getElementById(i)).filter(Boolean)
    .map(n => n.textContent).join(' ');
  const accName = el => {
    const parts = [el.getAttribute('aria-label'), byIds(el.getAttribute('aria-labelledby'))];
    if (el.labels) for (const l of el.labels) parts.push(textOf(l));
    parts.push(textOf(el), el.getAttribute('title'));
    if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type)) parts.push(el.value);
    return parts.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
  };
  const fieldName = el => {
    const parts = [el.getAttribute('aria-label'), byIds(el.getAttribute('aria-labelledby')),
                   el.getAttribute('title')];
    if (el.labels) for (const l of el.labels) parts.push(textOf(l));
    return parts.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
  };

  // --- headings ---------------------------------------------------------
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')]
    .filter(exposed)
    .map(h => ({level: h.getAttribute('role') === 'heading'
                        ? Number(h.getAttribute('aria-level') || 2)
                        : Number(h.tagName[1]),
                label: label(h), text: h.textContent.trim().slice(0, 40)}));
  const h1 = headings.filter(h => h.level === 1);
  const skips = [];
  for (let i = 1; i < headings.length; i++) {
    if (headings[i].level > headings[i - 1].level + 1) {
      skips.push(headings[i - 1].label + '(h' + headings[i - 1].level + ') -> '
                 + headings[i].label + '(h' + headings[i].level + ') "' + headings[i].text + '"');
    }
  }
  const firstIsH1 = headings.length === 0 || headings[0].level === 1;

  // --- form fields --------------------------------------------------------
  const unlabeled = [...document.querySelectorAll('input, select, textarea')]
    .filter(el => !['hidden', 'submit', 'button', 'reset', 'image'].includes(el.type))
    .filter(exposed)
    .filter(el => !fieldName(el))
    .map(el => label(el) + '[type=' + (el.type || el.tagName.toLowerCase()) + ']');

  // --- interactive names --------------------------------------------------
  const unnamed = [...document.querySelectorAll(
      'button, a[href], [role="button"], [role="link"], [role="tab"], [role="switch"], summary')]
    .filter(exposed)
    .filter(el => !accName(el))
    .map(label);

  // --- invisible focusable ------------------------------------------------
  // A control that takes Tab focus but paints nothing. A visually-hidden
  // native input is fine WHEN its painted <label> carries the affordance
  // (the canonical custom radio/checkbox pattern).
  const tabbable = [...document.querySelectorAll(
      'a[href], button, input, select, textarea, summary, [tabindex]')]
    .filter(el => el.tabIndex >= 0 && !el.disabled && !el.closest('[inert]'))
    .filter(el => el.checkVisibility({visibilityProperty: true}));
  const ghosts = tabbable.filter(el => {
    const shows = el.checkVisibility({checkOpacity: true, visibilityProperty: true});
    const r = el.getBoundingClientRect();
    if (shows && r.width > 0 && r.height > 0) return false;
    if (el.labels && [...el.labels].some(painted)) return false;
    // Skip-link pattern: off-screen until focused, then painted.
    const before = document.activeElement;
    el.focus({preventScroll: true});
    const afterShows = el.checkVisibility({checkOpacity: true, visibilityProperty: true});
    const ar = el.getBoundingClientRect();
    const visibleOnFocus = afterShows && ar.width > 1 && ar.height > 1
      && ar.right > 0 && ar.left < vw;
    el.blur();
    if (before && before.focus) before.focus({preventScroll: true});
    return !visibleOnFocus;
  }).map(label);

  // --- navigation identity --------------------------------------------------
  const navs = [...document.querySelectorAll('nav.header-nav, nav.action-bar')]
    .filter(painted).map(n => n.className.split(' ')[0]);

  // --- dominant action ------------------------------------------------------
  const main = document.querySelector('main') || document.body;
  const primaries = [...main.querySelectorAll('.btn-volt')].filter(painted);
  const primary = primaries.map(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {name: label(el), w: r.width, h: r.height, bg: cs.backgroundColor,
            color: cs.color,
            clipped: el.scrollWidth > el.clientWidth + 1 && cs.overflowX !== 'visible',
            outside: r.right > vw + 1 || r.left < -1};
  });
  const secondaries = [...main.querySelectorAll('.btn-ghost, .btn-danger')]
    .filter(painted).map(el => {
      const cs = getComputedStyle(el);
      return {name: label(el), bg: cs.backgroundColor, color: cs.color};
    });

  // --- clipped primary copy -------------------------------------------------
  const clipped = [...main.querySelectorAll('h1, h2')].filter(painted).filter(el => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const hiddenOverflow = cs.overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1;
    return hiddenOverflow || r.right > vw + 1 || r.left < -1;
  }).map(el => label(el) + ' "' + el.textContent.trim().slice(0, 30) + '"');

  // Painted text nodes, for the i18n checks that run in Python against the
  // locale catalogues (so this probe never needs to ship the catalogue).
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const s = n.textContent.replace(/\s+/g, ' ').trim();
    if (!s || !n.parentElement) continue;
    const p = n.parentElement;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(p.tagName)) continue;
    if (!painted(p)) continue;
    texts.push(s);
  }
  const attrs = [...document.querySelectorAll('[aria-label], [placeholder], [title]')]
    .filter(exposed)
    .flatMap(el => ['aria-label', 'placeholder', 'title']
      .map(a => el.getAttribute(a)).filter(Boolean));

  return {
    lang: document.documentElement.lang,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    scrollWidth: document.documentElement.scrollWidth,
    h1: h1.map(h => h.label + ' "' + h.text + '"'),
    firstIsH1, skips, unlabeled, unnamed, ghosts, navs, primary, secondaries,
    clipped, texts, attrs,
  };
}
"""


# Visual reading order of Tab stops at a single-column width. Fixed / sticky
# chrome (header, bottom action bar, FABs) is recorded but excluded from the
# monotonic check - it is outside the document flow by design.
def tab_walk(page, limit=60):
    """Real ``Tab`` presses; one record per focused element."""
    page.evaluate("() => { window.scrollTo(0, 0);"
                  " if (document.activeElement) document.activeElement.blur(); }")
    stops, seen = [], set()
    for _ in range(limit):
        page.keyboard.press("Tab")
        rec = page.evaluate(r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  let fixed = false;
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const p = getComputedStyle(n).position;
    if (p === 'fixed' || p === 'sticky') { fixed = true; break; }
  }
  const r = el.getBoundingClientRect();
  const id = el.id ? '#' + el.id : '';
  const cls = String(el.className || '').split(/\s+/).filter(Boolean).slice(0, 2)
    .map(c => '.' + c).join('');
  const shows = el.checkVisibility({checkOpacity: true, visibilityProperty: true})
    && r.width > 0 && r.height > 0;
  const labelPainted = el.labels && [...el.labels].some(l => {
    const lr = l.getBoundingClientRect();
    return l.checkVisibility({checkOpacity: true, visibilityProperty: true})
      && lr.width > 0 && lr.height > 0;
  });
  return {label: el.tagName.toLowerCase() + id + cls
                 + ' "' + (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 24) + '"',
          top: r.top + window.scrollY, left: r.left + window.scrollX,
          bottom: r.bottom + window.scrollY, fixed,
          visible: shows || !!labelPainted,
          inMain: !!el.closest('main')};
}""")
        if rec is None:
            continue
        key = (rec["label"], round(rec["top"]), round(rec["left"]))
        if key in seen:
            break
        seen.add(key)
        stops.append(rec)
    return stops


def locale_texts(probe_result):
    return set(probe_result["texts"]) | set(probe_result["attrs"])


_KEY_TOKEN = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b")
# A snake_case identifier painted as copy (``friend_request``, ``full_body``,
# ``rest_day``) is a raw enum / database value, never product language.
_SNAKE = re.compile(r"(?<![\w./@-])[a-z]+_[a-z_]+(?![\w./@-])")


def raw_keys(strings, catalogue_keys):
    """Painted strings that are literally catalogue KEYS (``plan.day.rest``)."""
    found = set()
    for s in strings:
        for tok in _KEY_TOKEN.findall(s):
            if tok in catalogue_keys:
                found.add(tok)
    return sorted(found)


def raw_enums(strings):
    found = set()
    for s in strings:
        found.update(_SNAKE.findall(s))
    return sorted(found)


def foreign_copy(strings, own, other):
    """Painted strings that are catalogue copy of the OTHER locale only.

    Precise by construction: a string only counts if it is exactly a value in
    the other catalogue and exactly no value in the active one. User content,
    numbers and brand words can never match.
    """
    own_values = {v.strip() for v in own.values()}
    shared_keys_differ = {v.strip() for k, v in other.items()
                          if own.get(k, "").strip() != v.strip()}
    # Multi-word catalogue copy only. A single word ("Strength", "Protein")
    # is just as likely to be user / plan content that happens to equal a
    # label, and would make the gate fail on data rather than on product copy.
    candidates = {v for v in shared_keys_differ
                  if v not in own_values and " " in v and len(v) >= 8
                  and "{" not in v and "<" not in v}
    return sorted(s for s in strings if s in candidates)


# ── canonical state seeds ───────────────────────────────────────────────────
#
# Every state is produced the way PR2-PR7 produce it: real rows through the
# real services / HTTP routes, reusing the seed functions those PRs already
# own. Only Today's degraded read (``read_ok=False``) is injected at the facts
# boundary, exactly as the PR7 harness does - there is no row that produces it.
# Nothing edits rendered DOM to fake a state.

STATES = {
    "today": ("no_plan", "scheduled", "in_progress", "completed", "rest_day",
              "degraded"),
    "plan": ("no_plan", "scheduled_start", "active_rest_day", "active_resume"),
    "coach": ("empty_history", "populated", "history_failure"),
    "nutrition": ("target_known", "target_absent"),
    "supplements": ("empty_cabinet", "saved_cabinet"),
    "progress": ("insufficient", "populated"),
    "account": ("goal_selected",),
    "notifications": ("empty", "populated", "first_load_failure"),
}

# (surface, state) -> the failing read that produces a failure shell.
FAILURE_OVERRIDES = {
    ("coach", "history_failure"): {
        "/coach/history": (503, '{"error":"unavailable"}')},
    ("notifications", "first_load_failure"): {
        "/notifications/data": (503, '{"error":"unavailable"}')},
}


def _profile_session(user_id, **fields):
    from app.models import UserSession
    values = dict(goal="kas kazanma", fitness_level="intermediate",
                  current_activity="active", tdee=2600)
    values.update(fields)
    db.session.add(UserSession(user_id=user_id, **values))
    db.session.commit()


def seed_state(app, client, monkeypatch, user_id, surface, state):
    """Arrange ``state`` for ``surface``; return the path to visit."""
    from test_today_v2 import _seed_pumpcheck_today, _week
    from test_today_v2 import _seed_plan as today_plan

    with app.app_context():
        if surface == "today" and state in ("scheduled", "completed"):
            today_plan(user_id, _week("antrenman"))
            if state == "completed":
                _seed_pumpcheck_today(user_id)
        elif surface == "today" and state == "rest_day":
            today_plan(user_id, _week("dinlenme"))
        elif surface == "today" and state == "degraded":
            import app.blueprints.tracking as tracking_routes
            from app.today_presenter import TodayFacts
            facts = TodayFacts(False, False, False)
            monkeypatch.setattr(tracking_routes, "gather_today_facts",
                                lambda _user_id, value=facts: value)
        elif surface in ("plan", "today") and state in (
                "no_plan", "scheduled_start", "active_rest_day",
                "active_resume", "in_progress"):
            _profile_session(user_id)
        elif surface == "coach" and state == "populated":
            from app.services import memory_manager
            conversation = memory_manager.get_or_create_active_conversation(user_id)
            for i in range(4):
                memory_manager.record_turn(
                    conversation, "What should I train today? (%d)" % i,
                    "Keep the session as planned: three working sets, a "
                    "controlled tempo and full range. (%d)" % i)
            db.session.commit()
        elif surface == "nutrition":
            _profile_session(user_id, weight=80, height=180, age=30, bmr=1800,
                             tdee=2500,
                             target_calories=2100 if state == "target_known" else None)
        elif surface == "supplements" and state == "saved_cabinet":
            from test_ux4_pr6_nutrition_supplements_contract import _rated_cabinet
            _rated_cabinet(user_id)
        elif surface == "progress" and state == "populated":
            from datetime import datetime, timedelta
            from app.models import PumpCheck, WeeklyCheckIn
            now = datetime.utcnow()
            db.session.get(User, user_id).weight = 81.5
            for i, (weight, public_id) in enumerate(((81.5, "pr8-one"),
                                                     (82.0, "pr8-two"))):
                db.session.add(WeeklyCheckIn(
                    user_id=user_id, weight=weight, yogunluk=3, fatigue=2,
                    uyku_kalitesi=4, beslenme_uyumu=4,
                    created_at=now - timedelta(days=7 * i)))
                db.session.add(PumpCheck(
                    user_id=user_id, captured_at=now - timedelta(days=7 * i),
                    body_region="full_body", public_id=public_id,
                    analysis_status="completed", analysis={"quality": "good"}))
            db.session.commit()
        elif surface == "account":
            db.session.get(User, user_id).goal = "kas kazanma"
            db.session.commit()
        elif surface == "notifications" and state == "populated":
            from app.services.notifications import notify
            actor = User(username="pr8actor", email="pr8actor@example.com",
                         cognito_sub="sub-pr8actor")
            db.session.add(actor)
            db.session.commit()
            for i in range(3):
                row = notify(user_id, "friend_request", actor_id=actor.id,
                             target_type="friendship", target_id=20_000 + i)
                if i == 0:
                    row.is_read = True
            db.session.commit()

    if surface == "plan" and state != "no_plan":
        from test_ux4_pr5_plan_hierarchy_browser import seed as plan_seed
        plan_seed(app, client, user_id, state)
    elif surface == "today" and state == "in_progress":
        # A REAL resumable session started over HTTP and checkpointed by the
        # PR5 seed - not a monkeypatched fact.
        from test_ux4_pr5_plan_hierarchy_browser import seed as plan_seed
        plan_seed(app, client, user_id, "active_resume")
    return SURFACES[surface]
