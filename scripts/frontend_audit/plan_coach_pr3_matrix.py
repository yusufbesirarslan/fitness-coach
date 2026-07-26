"""AxisAI UIUX Sprint 1 PR3 — Plan V2 + Coach Page V2 exact browser matrix.

Reuses the Sprint-0 hermetic audit harness (``create_audit_app`` + ``AuditServer``
+ fixed browser clock + Chromium) to run the EXACT PR3 matrices with the Plan and
Coach Page feature flags toggled per cell — a dimension the inventory-driven
capture tiers do not model. Hermetic: ``create_audit_app`` calls
``configure_audit_environment`` before any ``app.*`` import, so no production or
external dependency is contacted (FatSecret/Bedrock/Resend/S3/Redis pinned to
inert values; SQLite audit DB on loopback only).

Both flags are read from ``current_app.config`` at request time, so the driver
toggles ``UIUX_PLAN_V2_ENABLED`` / ``UIUX_COACH_PAGE_V2_ENABLED`` (and, for the
weekly-section combinations, ``WEEKLY_PROGRAM_UI_ENABLED``) on the shared app
between sequential captures. Locale is forced through the signed-in user's
``language`` column (the canonical first-priority locale source).

Matrices (Chromium; the repository's minimum required engine):
  A. Plan — 20 cells:  5 vp x 2 loc x Plan{off,on}; Nav ON, Today ON, Coach OFF
  B. Coach — 20 cells: 5 vp x 2 loc x Coach{off,on}; Nav ON, Today ON, Plan ON
  C. Plan x Coach — 16 cells: {390,1366} x {en,tr} x 4 flag combos (Plan surface)
  D. Upstream — 8 cells: {390,1366} x 4 Nav/Today combos; Plan ON + Coach ON (Coach)
  E. Plan states — 16 cells: 4 canonical page states x {390,1366} x {en,tr}, Plan ON
  W. Weekly section — 6 cells: {disabled, enabled, enabled-error} x {390,1366}, Plan ON

Plus ``run_interactions`` (200% zoom / reduced-motion / increased-text) and
``run_behavior`` (the answer.txt §9 Coach lifecycle idempotency combinations,
Plan day expand/collapse, retry, back/forward).

Run (WSL, Sprint-0 venv + browsers):
  PLAYWRIGHT_BROWSERS_PATH=<cache> \
    python -m scripts.frontend_audit.plan_coach_pr3_matrix \
      --output docs/frontend-readiness/sprint-1-pr3
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from .app import ROOT, create_audit_app
from .runner import AuditServer, browser_clock_script
from .seed import seed_all

VIEWPORTS = {
    "320": {"width": 320, "height": 720},
    "390": {"width": 390, "height": 844},
    "768": {"width": 768, "height": 1024},
    "1024": {"width": 1024, "height": 768},
    "1366": {"width": 1366, "height": 768},
}

# Canonical Plan page state -> the seeded scenario (or runtime mutation) producing
# it. active_plan uses the valid 7-day seed; no_active_plan a profile-complete user
# with NO TrainingPlan; partial_active_plan mutates a carrier plan's plan_data to
# malformed JSON; read_error patches the canonical selector to raise.
PLAN_STATE_SCENARIO = {
    "no_active_plan": "social-empty",       # profile complete, no TrainingPlan
    "active_plan": "active-workout",         # valid 7-day program
    "partial_active_plan": "wearable-disconnected",  # plan row, plan_data mutated malformed
    "read_error": "active-workout",          # selector patched to raise
}
COACH_SCENARIO = "coach-history"             # profile complete + conversation history
PLAN_DEFAULT_SCENARIO = "active-workout"
WEEKLY_PATH = "/api/training/weekly-program"
HISTORY_PATH = "/coach/history"


PLAN_MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const mount = document.getElementById('plan-page');
  const bodyText = document.body ? document.body.innerText : '';
  const rawKeys = bodyText.match(/\bplan\.[a-z_]+(?:\.[a-z_]+)*/g) || [];
  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,
    plan_present: !!mount,
    plan_state: mount ? mount.getAttribute('data-plan-state') : null,
    plan_mount_overflow: mount ? mount.scrollWidth > mount.clientWidth + 1 : false,
    h1_count: q('h1').length,
    legacy_training_js: q('script[src*="/static/training.js"]').length,
    coach_root_count: q('#cw-root').length,
    self_link_to_training: mount ? [...mount.querySelectorAll('a[href="/training"]')].length : 0,
    status_text_present: !!(mount && mount.querySelector('.plan-status-txt') &&
        mount.querySelector('.plan-status-txt').textContent.trim()),
    create_form_present: q('[data-plan-create]').length,
    retry_present: q('[data-action="fxReload"]').length,
    weekly_mount_count: q('#weekly-program[data-weekly-program-mount]').length,
    weekly_js_count: q('script[src*="/static/weekly_program.js"]').length,
    plan_day_count: q('details.plan-day').length,
    secondary_links: q('.plan-secondary-link').map((a) => a.getAttribute('href')),
    raw_key_leak: [...new Set(rawKeys)].slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""

COACH_MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const win = document.getElementById('cw-window');
  const bodyText = document.body ? document.body.innerText : '';
  const rawKeys = bodyText.match(/\bcoach\.[a-z_]+(?:\.[a-z_]+)*/g) || [];
  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,
    coach_v2_present: q('[data-coach-v2]').length,
    h1_count: q('h1').length,
    cw_root_count: q('#cw-root').length,
    cw_window_count: q('#cw-window').length,
    cw_input_count: q('#cw-input').length,          // the ONE composer
    cw_send_count: q('#cw-send').length,            // the ONE submit
    cw_fab_count: q('#cw-fab').length,
    coach_open: !!(win && win.classList.contains('cw-open')),
    ax_coach_open_defined: typeof window.axCoachOpen === 'function',
    raw_key_leak: [...new Set(rawKeys)].slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""


# ── flag / data helpers ────────────────────────────────────────────────────

def _set_flags(app, *, nav=True, today=True, plan=False, coach=False, weekly=False):
    app.config["UIUX_NAV_V2_ENABLED"] = nav
    app.config["UIUX_TODAY_V2_ENABLED"] = today
    app.config["UIUX_PLAN_V2_ENABLED"] = plan
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = coach
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = weekly


def _set_user_language(app, scenario, locale):
    from app.extensions import db
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(username=f"audit-{scenario}").first()
        user.language = locale
        db.session.commit()


def _mutate_plan_malformed(app, scenario):
    """Force a carrier scenario's active plan_data to malformed JSON → partial."""
    from app.extensions import db
    from app.models import TrainingPlan, User
    with app.app_context():
        user = User.query.filter_by(username=f"audit-{scenario}").first()
        plan = (TrainingPlan.query.filter_by(user_id=user.id)
                .order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc()).first())
        plan.plan_data = "[ this is not valid json"
        db.session.commit()


@contextmanager
def _patched_read_error():
    """Patch the canonical selector so gather_plan_facts hits read_ok=False."""
    from app.services import plan_facts
    original = plan_facts.get_active_plan

    def _raise(_uid):
        raise RuntimeError("audit-injected read failure")

    plan_facts.get_active_plan = _raise
    try:
        yield
    finally:
        plan_facts.get_active_plan = original


@contextmanager
def _patched_weekly_error():
    """Patch the weekly planner so the section endpoint returns a structured 500."""
    from app.blueprints import training
    original = training.build_weekly_program

    def _raise(*_a, **_k):
        raise RuntimeError("audit-injected weekly failure")

    training.build_weekly_program = _raise
    try:
        yield
    finally:
        training.build_weekly_program = original


# ── cell definitions ───────────────────────────────────────────────────────

def _cells():
    cells = []
    # A. Plan — 20 (Coach OFF, weekly OFF)
    for vp in ("320", "390", "768", "1024", "1366"):
        for loc in ("en", "tr"):
            for plan in (False, True):
                shot = vp in ("390", "1366") and (loc == "en" or plan)
                cells.append({
                    "matrix": "A-plan-20", "target": "plan", "route": "/training",
                    "viewport": vp, "locale": loc, "scenario": PLAN_DEFAULT_SCENARIO,
                    "flags": {"plan": plan, "coach": False, "weekly": False},
                    "expect_state": "active_plan", "shot": shot,
                })
    # B. Coach — 20 (Plan ON)
    for vp in ("320", "390", "768", "1024", "1366"):
        for loc in ("en", "tr"):
            for coach in (False, True):
                shot = vp in ("390", "1366") and (loc == "en" or coach)
                cells.append({
                    "matrix": "B-coach-20", "target": "coach", "route": "/coach",
                    "viewport": vp, "locale": loc, "scenario": COACH_SCENARIO,
                    "flags": {"plan": True, "coach": coach, "weekly": False},
                    "shot": shot,
                })
    # C. Plan x Coach — 16 (Plan surface, all 4 flag combos)
    for vp in ("390", "1366"):
        for loc in ("en", "tr"):
            for plan in (False, True):
                for coach in (False, True):
                    cells.append({
                        "matrix": "C-crossflag-16", "target": "plan", "route": "/training",
                        "viewport": vp, "locale": loc, "scenario": PLAN_DEFAULT_SCENARIO,
                        "flags": {"plan": plan, "coach": coach, "weekly": False},
                        "expect_state": "active_plan",
                        "shot": vp == "390" and loc == "en" and plan,
                    })
    # D. Upstream compatibility — 8 (Plan ON + Coach ON, vary Nav/Today; Coach surface)
    for vp in ("390", "1366"):
        for nav in (False, True):
            for today in (False, True):
                cells.append({
                    "matrix": "D-upstream-8", "target": "coach", "route": "/coach",
                    "viewport": vp, "locale": "en", "scenario": COACH_SCENARIO,
                    "flags": {"plan": True, "coach": True, "weekly": False,
                              "nav": nav, "today": today},
                    "shot": False,
                })
    # E. Plan states — 16 (Plan ON)
    for state in ("no_active_plan", "active_plan", "partial_active_plan", "read_error"):
        for vp in ("390", "1366"):
            for loc in ("en", "tr"):
                cells.append({
                    "matrix": "E-states-16", "target": "plan", "route": "/training",
                    "viewport": vp, "locale": loc, "scenario": PLAN_STATE_SCENARIO[state],
                    "flags": {"plan": True, "coach": False, "weekly": False},
                    "expect_state": state, "shot": vp == "390" and loc == "en",
                })
    # W. Weekly section — 6 (Plan ON + active plan)
    for weekly_mode in ("disabled", "enabled", "enabled-error"):
        for vp in ("390", "1366"):
            cells.append({
                "matrix": "W-weekly-6", "target": "plan", "route": "/training",
                "viewport": vp, "locale": "en", "scenario": PLAN_DEFAULT_SCENARIO,
                "flags": {"plan": True, "coach": False,
                          "weekly": weekly_mode != "disabled"},
                "expect_state": "active_plan", "weekly_mode": weekly_mode,
                "shot": vp == "390",
            })
    return cells


def _cell_id(cell):
    f = cell["flags"]
    flagstr = (f"plan-{'on' if f.get('plan') else 'off'}"
               f"_coach-{'on' if f.get('coach') else 'off'}"
               f"_weekly-{'on' if f.get('weekly') else 'off'}")
    extra = cell.get("expect_state") or cell.get("weekly_mode") or cell["target"]
    return f"{cell['matrix']}__{extra}__{cell['viewport']}__{cell['locale']}__{flagstr}"


# ── evaluation ─────────────────────────────────────────────────────────────

def _evaluate_plan(cell, m, weekly_reqs):
    reasons = []
    f = cell["flags"]
    if m["doc_horizontal_overflow"]:
        reasons.append("document horizontal overflow")
    if m["h1_count"] != 1:
        reasons.append(f"h1 count={m['h1_count']} (want 1)")
    if m["coach_root_count"] != 1:
        reasons.append(f"coach instances={m['coach_root_count']} (want exactly 1)")
    if m["raw_key_leak"]:
        reasons.append(f"raw localization keys leaked: {m['raw_key_leak']}")
    if f["plan"]:
        if not m["plan_present"]:
            reasons.append("Plan v2 flag ON but plan tree absent")
        if m["legacy_training_js"]:
            reasons.append("legacy training.js loaded under Plan ON")
        if m["plan_mount_overflow"]:
            reasons.append("plan-shell horizontal overflow")
        if not m["status_text_present"]:
            reasons.append("status not conveyed as text")
        if m["self_link_to_training"]:
            reasons.append("plan self-links to /training")
        if cell.get("expect_state") and m["plan_state"] != cell["expect_state"]:
            reasons.append(f"state={m['plan_state']} (want {cell['expect_state']})")
        # No dominant/self CTA in any state; create form only in no_active_plan;
        # retry only in read_error.
        want_create = 1 if m["plan_state"] == "no_active_plan" else 0
        if m["create_form_present"] != want_create:
            reasons.append(f"create form={m['create_form_present']} (want {want_create})")
        want_retry = 1 if m["plan_state"] == "read_error" else 0
        if m["retry_present"] != want_retry:
            reasons.append(f"retry={m['retry_present']} (want {want_retry})")
        # Weekly section: disabled → no mount/js/request, plan still visible;
        # enabled → exactly one mount + at most one weekly request; a weekly
        # failure must NOT collapse the page (plan_state stays active_plan).
        if f["weekly"] and m["plan_state"] in ("active_plan", "partial_active_plan"):
            if m["weekly_mount_count"] != 1:
                reasons.append(f"weekly mount={m['weekly_mount_count']} (want 1 when enabled)")
            if weekly_reqs > 1:
                reasons.append(f"weekly requests={weekly_reqs} (want <=1)")
        else:
            if m["weekly_mount_count"]:
                reasons.append("weekly mount present while disabled")
            if m["weekly_js_count"]:
                reasons.append("weekly_program.js loaded while disabled")
            if weekly_reqs:
                reasons.append(f"weekly request fired while disabled ({weekly_reqs})")
    else:
        if m["plan_present"]:
            reasons.append("Plan v2 tree present under Plan OFF")
    return ("pass" if not reasons else "fail"), reasons


def _evaluate_coach(cell, m):
    reasons = []
    f = cell["flags"]
    if m["doc_horizontal_overflow"]:
        reasons.append("document horizontal overflow")
    if m["h1_count"] != 1:
        reasons.append(f"h1 count={m['h1_count']} (want 1)")
    if m["raw_key_leak"]:
        reasons.append(f"raw localization keys leaked: {m['raw_key_leak']}")
    # Exactly one interactive Coach instance / composer / submit — every combo.
    for field, label in (("cw_root_count", "root"), ("cw_window_count", "window"),
                         ("cw_input_count", "composer"), ("cw_send_count", "submit")):
        if m[field] != 1:
            reasons.append(f"coach {label} count={m[field]} (want exactly 1)")
    if f["coach"]:
        if not m["coach_v2_present"]:
            reasons.append("Coach Page v2 flag ON but v2 tree absent")
        if not m["ax_coach_open_defined"]:
            reasons.append("route opener axCoachOpen not defined under Coach ON")
        if not m["coach_open"]:
            reasons.append("Coach V2 route did not auto-open the single widget")
    else:
        if m["coach_v2_present"]:
            reasons.append("Coach v2 tree present under Coach OFF")
        if m["ax_coach_open_defined"]:
            reasons.append("route opener leaked into legacy floating widget (Coach OFF)")
    return ("pass" if not reasons else "fail"), reasons


# ── driver ─────────────────────────────────────────────────────────────────

def _new_page(browser, dims, clock, reduced_motion=None):
    kwargs = {"viewport": dict(dims)}
    if reduced_motion:
        kwargs["reduced_motion"] = "reduce"
    context = browser.new_context(**kwargs)
    context.add_init_script(browser_clock_script(clock))
    page = context.new_page()
    trackers = {"console": [], "page": [], "server_5xx": [], "failed": [], "reqs": []}
    page.on("console", lambda m: trackers["console"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: trackers["page"].append(str(e)))
    page.on("request", lambda r: trackers["reqs"].append((r.method, r.url)))
    return context, page, trackers


def _net_failures(trackers, base_url, allow_5xx_paths=()):
    """Return (hard_5xx, hard_failed) filtered to same-origin, minus allowances."""
    hard_5xx = [e for e in trackers["server_5xx"]
                if urlparse(e["url"]).path not in allow_5xx_paths]
    hard_failed = [fr for fr in trackers["failed"]
                   if "ERR_ABORTED" not in (fr.get("error") or "")]
    return hard_5xx, hard_failed


def run(output_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    db_path = ROOT / "artifacts" / "ui-audit" / "plan_coach_pr3_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    _mutate_plan_malformed(app, PLAN_STATE_SCENARIO["partial_active_plan"])
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]

    cells = _cells()
    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for cell in cells:
                f = cell["flags"]
                _set_flags(app, nav=f.get("nav", True), today=f.get("today", True),
                           plan=f.get("plan", False), coach=f.get("coach", False),
                           weekly=f.get("weekly", False))
                _set_user_language(app, cell["scenario"], cell["locale"])

                dims = VIEWPORTS[cell["viewport"]]
                context, page, trk = _new_page(
                    browser, dims, clocks[cell["scenario"]]["fixed_current_datetime"])
                base = server.base_url
                page.on("response", lambda r, b=base: trk["server_5xx"].append(
                    {"url": r.url, "status": r.status})
                    if (r.status >= 500 and r.url.startswith(b)) else None)
                page.on("requestfailed", lambda req, b=base: trk["failed"].append(
                    {"url": req.url, "error": str(req.failure)})
                    if req.url.startswith(b) else None)
                cid = _cell_id(cell)
                weekly_error = cell.get("weekly_mode") == "enabled-error"
                read_error = cell.get("expect_state") == "read_error"
                try:
                    page.goto(f"{base}/__audit__/login/{cell['scenario']}",
                              wait_until="domcontentloaded", timeout=20000)
                    ctx_read = _patched_read_error() if read_error else _nullctx()
                    ctx_weekly = _patched_weekly_error() if weekly_error else _nullctx()
                    with ctx_read, ctx_weekly:
                        resp = page.goto(f"{base}{cell['route']}",
                                         wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(800)  # allow widget boot + weekly/section fetch
                        if cell["target"] == "plan":
                            m = page.evaluate(PLAN_MEASURE_JS)
                        else:
                            m = page.evaluate(COACH_MEASURE_JS)
                    status_code = resp.status if resp else None
                    weekly_reqs = sum(1 for (mth, u) in trk["reqs"]
                                      if urlparse(u).path == WEEKLY_PATH)
                    if cell["target"] == "plan":
                        verdict, reasons = _evaluate_plan(cell, m, weekly_reqs)
                    else:
                        verdict, reasons = _evaluate_coach(cell, m)
                    if status_code is not None and status_code >= 500:
                        verdict = "fail"; reasons.append(f"document status {status_code}")
                    # The weekly-error cell INTENTIONALLY makes the section endpoint
                    # 500; that one same-origin path is allowed for that cell only.
                    allow = (WEEKLY_PATH,) if weekly_error else ()
                    hard_5xx, hard_failed = _net_failures(trk, base, allow)
                    if hard_5xx:
                        verdict = "fail"
                        reasons.append("unexpected 5xx: " + ", ".join(
                            f"{e['status']} {urlparse(e['url']).path}" for e in hard_5xx))
                    if hard_failed:
                        verdict = "fail"
                        reasons.append("failed requests: " + ", ".join(
                            urlparse(fr["url"]).path for fr in hard_failed))
                    shot_rel = None
                    if cell["shot"]:
                        page.screenshot(path=str(shots_dir / f"{cid}.png"), full_page=True)
                        shot_rel = f"screenshots/{cid}.png"
                    results.append({
                        "id": cid, "matrix": cell["matrix"], "target": cell["target"],
                        "viewport": cell["viewport"], "viewport_px": dims,
                        "locale": cell["locale"], "flags": f,
                        "scenario": cell["scenario"],
                        "expected_state": cell.get("expect_state"),
                        "weekly_mode": cell.get("weekly_mode"),
                        "status_code": status_code, "verdict": verdict, "reasons": reasons,
                        "weekly_requests": weekly_reqs,
                        "console_errors": trk["console"], "page_errors": trk["page"],
                        "server_errors": trk["server_5xx"], "failed_requests": trk["failed"],
                        "measurements": m, "screenshot": shot_rel,
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({"id": cid, "matrix": cell["matrix"],
                                    "verdict": "blocked",
                                    "reasons": [f"{type(exc).__name__}: {exc}"]})
                finally:
                    context.close()
        finally:
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "uiux-sprint1-pr3-plan-coach-separation",
        "engine": "chromium", "hermetic": True, "seed_summary": seed_summary,
        "totals": {"cells": len(results), "passed": passed,
                   "failed": len(failed), "blocked": len(blocked)},
        "matrices": {name: sum(1 for c in cells if c["matrix"] == name)
                     for name in sorted({c["matrix"] for c in cells})},
        "failed_ids": failed, "blocked_ids": blocked, "cells": results,
    }
    (output_dir / "validation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


@contextmanager
def _nullctx():
    yield


# ── interactions: 200% zoom / reduced-motion / increased-text ──────────────

INTERACTIONS = [
    {"cond": "reduced-motion", "target": "plan",  "state": "active_plan", "vp": "390",  "loc": "en"},
    {"cond": "reduced-motion", "target": "coach", "vp": "390",  "loc": "tr"},
    {"cond": "zoom-200",       "target": "plan",  "state": "active_plan", "vp": "1366", "loc": "en"},
    {"cond": "zoom-200",       "target": "plan",  "state": "no_active_plan", "vp": "768", "loc": "tr"},
    {"cond": "text-150",       "target": "plan",  "state": "active_plan", "vp": "390",  "loc": "tr"},
    {"cond": "text-150",       "target": "coach", "vp": "390",  "loc": "en"},
]

_TEXT_ZOOM_JS = """
() => {
  const src = document.querySelector('style[nonce], script[nonce]');
  if (!src || !src.nonce) throw new Error('no CSP nonce');
  const s = document.createElement('style');
  s.nonce = src.nonce; s.textContent = 'html { font-size: %d%% !important; }';
  document.head.appendChild(s);
}
"""


def run_interactions(output_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    db_path = ROOT / "artifacts" / "ui-audit" / "plan_coach_pr3_interactions.db"
    app = create_audit_app(db_path)
    seed_all(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]

    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        base = server.base_url
        browser = pw.chromium.launch(headless=True)
        try:
            for it in INTERACTIONS:
                if it["target"] == "plan":
                    scenario = PLAN_STATE_SCENARIO[it["state"]]
                    route = "/training"
                    _set_flags(app, plan=True, coach=False, weekly=False)
                else:
                    scenario = COACH_SCENARIO
                    route = "/coach"
                    _set_flags(app, plan=True, coach=True, weekly=False)
                _set_user_language(app, scenario, it["loc"])
                dims = VIEWPORTS[it["vp"]]
                context, page, trk = _new_page(
                    browser, dims, clocks[scenario]["fixed_current_datetime"],
                    reduced_motion=(it["cond"] == "reduced-motion"))
                page.on("response", lambda r, b=base: trk["server_5xx"].append(
                    {"url": r.url, "status": r.status})
                    if (r.status >= 500 and r.url.startswith(b)) else None)
                cid = f"I-{it['cond']}__{it['target']}__{it.get('state','')}__{it['vp']}__{it['loc']}"
                try:
                    page.goto(f"{base}/__audit__/login/{scenario}",
                              wait_until="domcontentloaded", timeout=20000)
                    page.goto(f"{base}{route}", wait_until="domcontentloaded", timeout=20000)
                    if it["cond"] == "zoom-200":
                        page.evaluate("() => { document.documentElement.style.zoom = '2'; }")
                    elif it["cond"] == "text-150":
                        page.evaluate(_TEXT_ZOOM_JS % 150)
                    page.wait_for_timeout(700)
                    m = page.evaluate(PLAN_MEASURE_JS if it["target"] == "plan" else COACH_MEASURE_JS)
                    reasons = []
                    if m["doc_horizontal_overflow"]:
                        reasons.append("document horizontal overflow")
                    if it["target"] == "plan":
                        if not m["plan_present"]:
                            reasons.append("plan tree absent")
                        if m["plan_mount_overflow"]:
                            reasons.append("plan-shell overflow")
                        if m["coach_root_count"] != 1:
                            reasons.append(f"coach instances={m['coach_root_count']}")
                    else:
                        if m["cw_root_count"] != 1 or m["cw_input_count"] != 1:
                            reasons.append(f"coach root={m['cw_root_count']} composer={m['cw_input_count']}")
                    reasons += [f"unexpected 5xx {urlparse(e['url']).path}" for e in trk["server_5xx"]]
                    page.screenshot(path=str(shots_dir / f"{cid}.png"), full_page=True)
                    results.append({
                        "id": cid, "condition": it["cond"], "target": it["target"],
                        "state": it.get("state"), "viewport": it["vp"], "locale": it["loc"],
                        "verdict": "pass" if not reasons else "fail", "reasons": reasons,
                        "measurements": m, "screenshot": f"screenshots/{cid}.png",
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({"id": cid, "verdict": "blocked",
                                    "reasons": [f"{type(exc).__name__}: {exc}"]})
                finally:
                    context.close()
        finally:
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    out = {
        "schema_version": "1.0.0",
        "pr": "uiux-sprint1-pr3-plan-coach-separation", "kind": "interaction",
        "totals": {"cells": len(results), "passed": passed,
                   "failed": sum(1 for r in results if r["verdict"] == "fail"),
                   "blocked": sum(1 for r in results if r["verdict"] == "blocked")},
        "cells": results,
    }
    (output_dir / "interaction-results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ── behavior: answer.txt §9 Coach lifecycle + Plan interactions ────────────

def run_behavior(output_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    db_path = ROOT / "artifacts" / "ui-audit" / "plan_coach_pr3_behavior.db"
    app = create_audit_app(db_path)
    seed_all(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]

    results = []

    def record(cid, verdict, reasons=None, evidence=None):
        results.append({"id": cid, "verdict": verdict,
                        "reasons": reasons or [], "evidence": evidence or {}})

    with AuditServer(app) as server, sync_playwright() as pw:
        base = server.base_url
        browser = pw.chromium.launch(headless=True)

        def open_route(scenario, route, size=(390, 800), locale="en"):
            _set_user_language(app, scenario, locale)
            context, page, trk = _new_page(
                browser, {"width": size[0], "height": size[1]},
                clocks[scenario]["fixed_current_datetime"])
            page.on("response", lambda r: trk["server_5xx"].append(
                {"url": r.url, "status": r.status})
                if (r.status >= 500 and r.url.startswith(base)) else None)
            # audit login always redirects to "/", which itself boots the widget
            # (one /coach/history). Let that settle, then CLEAR the request log so
            # per-page counts below reflect ONLY the target route's single boot —
            # otherwise the login-redirect page inflates the count to 2.
            page.goto(f"{base}/__audit__/login/{scenario}",
                      wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(500)
            trk["reqs"].clear()
            page.goto(f"{base}{route}", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(800)
            return context, page, trk

        def history_count(trk):
            return sum(1 for (mth, u) in trk["reqs"] if urlparse(u).path == HISTORY_PATH)

        # BH-1 Coach OFF: legacy floating widget unchanged (one root, opens/closes)
        cid = "BH-coach-off-floating-unchanged"
        try:
            _set_flags(app, plan=True, coach=False)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            if m["coach_v2_present"]:
                reasons.append("v2 tree present under Coach OFF")
            if m["cw_root_count"] != 1:
                reasons.append(f"root={m['cw_root_count']}")
            # Floating widget toggle is unchanged: each toggle FLIPS state. (Legacy
            # /coach auto-opens on load, so assert the flip, not a fixed direction.)
            win = "() => document.getElementById('cw-window').classList.contains('cw-open')"
            s0 = page.evaluate(win)
            page.evaluate("() => window.CW && window.CW.toggle()")
            page.wait_for_timeout(200)
            s1 = page.evaluate(win)
            page.evaluate("() => window.CW && window.CW.toggle()")
            page.wait_for_timeout(200)
            s2 = page.evaluate(win)
            if s1 == s0:
                reasons.append("floating widget toggle did not flip state")
            if s2 != s0:
                reasons.append("floating widget second toggle did not restore state")
            if history_count(trk) != 1:
                reasons.append(f"history fetch count={history_count(trk)} (want exactly 1)")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"roots": m["cw_root_count"], "history_fetches": history_count(trk),
                    "toggle_states": [s0, s1, s2]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-2 Coach ON route: one instance, one composer, auto-opened
        cid = "BH-coach-on-route-single-instance"
        try:
            _set_flags(app, plan=True, coach=True)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            for field, label in (("cw_root_count", "root"), ("cw_input_count", "composer"),
                                 ("cw_send_count", "submit")):
                if m[field] != 1:
                    reasons.append(f"{label}={m[field]} (want 1)")
            if not m["coach_open"]:
                reasons.append("did not auto-open")
            if history_count(trk) > 1:
                reasons.append(f"duplicate history fetch ({history_count(trk)})")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"measurements": m, "history_fetches": history_count(trk)})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-3 script evaluated twice → still one root/composer, one history fetch
        cid = "BH-script-twice-no-duplicate"
        try:
            _set_flags(app, plan=True, coach=True)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")
            page.evaluate("""
() => new Promise((res) => {
  const s = document.createElement('script');
  s.src = '/static/coach_widget.js?dup=1';
  s.onload = () => res(true); s.onerror = () => res(false);
  document.body.appendChild(s);
})""")
            page.wait_for_timeout(500)
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            if m["cw_root_count"] != 1:
                reasons.append(f"root={m['cw_root_count']} after 2nd script eval")
            if m["cw_input_count"] != 1:
                reasons.append(f"composer={m['cw_input_count']} after 2nd script eval")
            if history_count(trk) > 1:
                reasons.append(f"duplicate history fetch ({history_count(trk)})")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"roots": m["cw_root_count"], "composers": m["cw_input_count"],
                    "history_fetches": history_count(trk)})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-4 init() invoked twice → still one root/composer
        cid = "BH-init-twice-no-duplicate"
        try:
            _set_flags(app, plan=True, coach=True)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")
            page.evaluate("() => window.CW && window.CW.init()")
            page.wait_for_timeout(300)
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            if m["cw_root_count"] != 1:
                reasons.append(f"root={m['cw_root_count']} after init() twice")
            if m["cw_input_count"] != 1:
                reasons.append(f"composer={m['cw_input_count']} after init() twice")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"roots": m["cw_root_count"], "composers": m["cw_input_count"]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-5 route mode AFTER floating: navigate coach-OFF page → coach-ON route
        cid = "BH-route-after-floating"
        try:
            _set_flags(app, plan=True, coach=False)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")   # floating first
            floating_roots = page.evaluate("() => document.querySelectorAll('#cw-root').length")
            app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
            page.goto(f"{base}/coach", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(700)
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            if floating_roots != 1:
                reasons.append(f"floating roots={floating_roots}")
            if m["cw_root_count"] != 1:
                reasons.append(f"route root={m['cw_root_count']}")
            if not m["coach_v2_present"]:
                reasons.append("route did not render v2 after floating")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"floating_roots": floating_roots, "route_roots": m["cw_root_count"]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-6 floating AFTER route: coach-ON route → coach-OFF floating page
        cid = "BH-floating-after-route"
        try:
            _set_flags(app, plan=True, coach=True)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")   # route first
            route_roots = page.evaluate("() => document.querySelectorAll('#cw-root').length")
            app.config["UIUX_COACH_PAGE_V2_ENABLED"] = False
            page.goto(f"{base}/coach", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(500)
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            if route_roots != 1:
                reasons.append(f"route roots={route_roots}")
            if m["cw_root_count"] != 1:
                reasons.append(f"floating root={m['cw_root_count']}")
            if m["coach_v2_present"]:
                reasons.append("v2 tree present after rollback to floating")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"route_roots": route_roots, "floating_roots": m["cw_root_count"]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-7 close → reopen keeps one instance
        cid = "BH-coach-close-reopen"
        try:
            _set_flags(app, plan=True, coach=True)
            ctx, page, trk = open_route(COACH_SCENARIO, "/coach")
            page.evaluate("() => window.CW && window.CW.toggle()")   # close (was auto-open)
            page.wait_for_timeout(200)
            page.evaluate("() => window.axCoachOpen && window.axCoachOpen()")  # reopen via fallback
            page.wait_for_timeout(200)
            m = page.evaluate(COACH_MEASURE_JS)
            reasons = []
            if m["cw_root_count"] != 1:
                reasons.append(f"root={m['cw_root_count']} after close/reopen")
            if not m["coach_open"]:
                reasons.append("did not reopen via axCoachOpen")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"root": m["cw_root_count"], "open": m["coach_open"]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-8 Plan day expand/collapse (pointer + keyboard) via native <details>
        cid = "BH-plan-day-expand-collapse"
        try:
            _set_flags(app, plan=True, coach=False)
            ctx, page, trk = open_route(PLAN_DEFAULT_SCENARIO, "/training")
            details = page.locator("details.plan-day").first
            reasons = []
            if not details.count():
                reasons.append("no plan-day details present")
            else:
                summary = details.locator("summary")
                summary.click()
                page.wait_for_timeout(120)
                open1 = details.evaluate("e => e.open")
                summary.click()
                page.wait_for_timeout(120)
                open2 = details.evaluate("e => e.open")
                if not open1:
                    reasons.append("summary click did not expand")
                if open2:
                    reasons.append("summary click did not collapse")
            reasons += [f"unexpected 5xx {urlparse(e['url']).path}" for e in trk["server_5xx"]]
            record(cid, "pass" if not reasons else "fail", reasons)
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-9 read_error retry re-fetches the same page (safe retry, no self-link)
        cid = "BH-plan-read-error-retry"
        try:
            _set_flags(app, plan=True, coach=False)
            _set_user_language(app, PLAN_DEFAULT_SCENARIO, "en")
            context, page, trk = _new_page(
                browser, {"width": 390, "height": 800},
                clocks[PLAN_DEFAULT_SCENARIO]["fixed_current_datetime"])
            page.goto(f"{base}/__audit__/login/{PLAN_DEFAULT_SCENARIO}",
                      wait_until="domcontentloaded", timeout=20000)
            with _patched_read_error():
                page.goto(f"{base}/training", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(300)
                state1 = page.evaluate("() => document.getElementById('plan-page').getAttribute('data-plan-state')")
                has_retry = page.evaluate("() => document.querySelectorAll('[data-action=\"fxReload\"]').length")
            # Retry OUTSIDE the patch → healthy read → active_plan.
            page.click("[data-action='fxReload']")
            page.wait_for_timeout(600)
            state2 = page.evaluate("() => { const p=document.getElementById('plan-page'); return p? p.getAttribute('data-plan-state'):null; }")
            reasons = []
            if state1 != "read_error":
                reasons.append(f"state under patch={state1} (want read_error)")
            if not has_retry:
                reasons.append("no retry control in read_error")
            if state2 != "active_plan":
                reasons.append(f"state after retry={state2} (want active_plan)")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"state_under_error": state1, "state_after_retry": state2})
            context.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # BH-10 Plan back/forward keeps the plan surface intact
        cid = "BH-plan-back-forward"
        try:
            _set_flags(app, plan=True, coach=False)
            ctx, page, trk = open_route(PLAN_DEFAULT_SCENARIO, "/training")
            page.click(".plan-secondary-link")   # first secondary → /coach
            page.wait_for_timeout(400)
            page.go_back()
            page.wait_for_timeout(500)
            m = page.evaluate(PLAN_MEASURE_JS)
            reasons = []
            if not m["plan_present"]:
                reasons.append("plan absent after back")
            if m["plan_state"] != "active_plan":
                reasons.append(f"state after back={m['plan_state']}")
            if m["coach_root_count"] != 1:
                reasons.append(f"coach instances after back={m['coach_root_count']}")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"state_after_back": m["plan_state"]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    out = {
        "schema_version": "1.0.0",
        "pr": "uiux-sprint1-pr3-plan-coach-separation", "kind": "behavior-interaction",
        "totals": {"cells": len(results), "passed": passed,
                   "failed": sum(1 for r in results if r["verdict"] == "fail"),
                   "blocked": sum(1 for r in results if r["verdict"] == "blocked")},
        "cells": results,
    }
    (output_dir / "behavior-results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(prog="python -m scripts.frontend_audit.plan_coach_pr3_matrix")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "docs" / "frontend-readiness" / "sprint-1-pr3")
    parser.add_argument("--interactions", action="store_true")
    parser.add_argument("--behavior", action="store_true")
    args = parser.parse_args()
    if args.behavior:
        out = run_behavior(args.output)
        t = out["totals"]
        print(f"behavior cells={t['cells']} passed={t['passed']} failed={t['failed']} blocked={t['blocked']}")
        for r in out["cells"]:
            if r["verdict"] != "pass":
                print("  !", r["id"], r.get("reasons"))
        return
    if args.interactions:
        out = run_interactions(args.output)
        t = out["totals"]
        print(f"interactions cells={t['cells']} passed={t['passed']} failed={t['failed']} blocked={t['blocked']}")
        for r in out["cells"]:
            if r["verdict"] != "pass":
                print("  !", r["id"], r.get("reasons"))
        return
    manifest = run(args.output)
    t = manifest["totals"]
    print(f"cells={t['cells']} passed={t['passed']} failed={t['failed']} blocked={t['blocked']}")
    if manifest["failed_ids"]:
        print("FAILED:", *manifest["failed_ids"], sep="\n  ")
    if manifest["blocked_ids"]:
        print("BLOCKED:", *manifest["blocked_ids"], sep="\n  ")
    print("manifest:", args.output / "validation-manifest.json")


if __name__ == "__main__":
    main()
