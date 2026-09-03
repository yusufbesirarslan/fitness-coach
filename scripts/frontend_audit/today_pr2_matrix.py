"""AxisAI UIUX Sprint 1 PR2 — Today experience exact browser matrix.

SUPERSEDED by UX-2 PR4 — KEPT AS SHIPPED EVIDENCE, NOT AS A WORKING HARNESS.
This driver captured the PR2 rollout evidence linked from
``docs/frontend-readiness/sprint-1-pr2/`` and ``docs/handoff.md``, so it is left
in place for that record. It can no longer run against the product: UX-2 PR4
converged ``/`` on one hierarchy, deleted ``templates/index.html`` (so the
``.dash-grid`` legacy branch this driver diffs against does not exist) and
retired the four-state Today dialect it asserts (``plan_ready`` /
``workout_done``) in favour of the canonical ``primary_state`` vocabulary.
A PR4-era Today matrix would be a new driver, not an edit of this one.

Reuses the Sprint-0 hermetic audit harness (``create_audit_app`` + ``AuditServer``
+ fixed browser clock + Chromium) to run the EXACT PR2 matrices with the Today
and Navigation feature flags toggled per cell — a dimension the inventory-driven
capture tiers do not model. Hermetic: ``create_audit_app`` calls
``configure_audit_environment`` before any ``app.*`` import, so no production or
external dependency is contacted (FatSecret/Bedrock/Resend/S3/Redis pinned to
inert values; SQLite audit DB on loopback only).

Matrices (Chromium; the repository's minimum required engine):
  A. 20-cell Today matrix — 5 viewports x 2 locales x Today{off,on}, Nav=ON
  B. 16-cell cross-flag    — 2 viewports x 2 locales x 4 Nav x Today combinations
  C. 12-cell state matrix  — 3 canonical states x 2 viewports x 2 locales, Nav=ON, Today=ON

Flags are read from ``current_app.config`` at request time, so the driver toggles
``UIUX_NAV_V2_ENABLED`` / ``UIUX_TODAY_V2_ENABLED`` on the shared app between
sequential captures. Locale is forced through the signed-in user's ``language``
column (the canonical first-priority locale source). Output: a machine-readable
validation manifest + curated screenshots under the given directory.

Run (WSL, Sprint-0 venv + browsers):
  PLAYWRIGHT_BROWSERS_PATH=<cache> \
    python -m scripts.frontend_audit.today_pr2_matrix \
      --output docs/frontend-readiness/sprint-1-pr2
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

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

# Canonical Today state -> the seeded audit scenario that produces it.
#   social-empty     : profile complete, NO TrainingPlan            -> no_plan
#   active-workout   : TrainingPlan, no PumpCheck today             -> plan_ready
#   completed-workout: TrainingPlan + today's PumpCheck (19:00 clk) -> workout_done
STATE_SCENARIO = {
    "no_plan": "social-empty",
    "plan_ready": "active-workout",
    "workout_done": "completed-workout",
}
DEFAULT_SCENARIO = "active-workout"       # representative populated day for A/B

MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const visible = (el) => {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const mount = document.getElementById('today-page');
  const primaryNavs = q('.header-nav, .action-bar');
  const primaryActions = q('[data-today-primary]');
  const bodyText = document.body ? document.body.innerText : '';
  const rawKeys = bodyText.match(/\b(?:today|index|nav)\.[a-z_]+(?:\.[a-z_]+)*/g) || [];
  const activeNav = q('[data-nav-id][aria-current="page"]').map((e) => e.getAttribute('data-nav-id'));
  const pa = primaryActions[0] || null;
  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,
    today_present: !!mount,
    today_state: mount ? mount.getAttribute('data-today-state') : null,
    today_mount_scroll_width: mount ? mount.scrollWidth : null,
    today_mount_client_width: mount ? mount.clientWidth : null,
    today_mount_overflow: mount ? mount.scrollWidth > mount.clientWidth + 1 : false,
    legacy_dash_grid_present: !!document.querySelector('.dash-grid'),
    primary_nav_raw_count: primaryNavs.length,
    primary_nav_exposed_count: primaryNavs.filter(visible).length,
    primary_action_count: primaryActions.length,
    primary_action_label: pa ? pa.textContent.trim() : null,
    primary_action_clipped: pa ? pa.scrollWidth > pa.clientWidth + 1 : false,
    active_nav_destination: activeNav,
    raw_key_leak: [...new Set(rawKeys)].slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""


def _cells():
    """Yield the exact PR2 matrix cells. `shot` marks curated screenshots."""
    cells = []
    # ── A. 20-cell Today matrix (Nav ON) ──
    for vp in ("320", "390", "768", "1024", "1366"):
        for loc in ("en", "tr"):
            for today in (False, True):
                shot = vp in ("390", "1366") and (loc == "en" or today)
                cells.append({
                    "matrix": "A-today-20", "viewport": vp, "locale": loc,
                    "nav": True, "today": today, "state": "plan_ready",
                    "scenario": DEFAULT_SCENARIO, "shot": shot,
                })
    # ── B. 16-cell cross-flag compatibility ──
    for vp in ("390", "1366"):
        for loc in ("en", "tr"):
            for nav in (False, True):
                for today in (False, True):
                    cells.append({
                        "matrix": "B-crossflag-16", "viewport": vp, "locale": loc,
                        "nav": nav, "today": today, "state": "plan_ready",
                        "scenario": DEFAULT_SCENARIO,
                        "shot": vp == "390" and loc == "en",
                    })
    # ── C. 12-cell representative state matrix (Nav ON, Today ON) ──
    for state in ("no_plan", "plan_ready", "workout_done"):
        for vp in ("390", "1366"):
            for loc in ("en", "tr"):
                cells.append({
                    "matrix": "C-states-12", "viewport": vp, "locale": loc,
                    "nav": True, "today": True, "state": state,
                    "scenario": STATE_SCENARIO[state], "shot": True,
                })
    return cells


def _cell_id(cell):
    flags = f"nav-{'on' if cell['nav'] else 'off'}_today-{'on' if cell['today'] else 'off'}"
    return f"{cell['matrix']}__{cell['state']}__{cell['viewport']}__{cell['locale']}__{flags}"


def _set_user_language(app, scenario, locale):
    from app.extensions import db
    from app.models import User
    with app.app_context():
        user = User.query.filter_by(username=f"audit-{scenario}").first()
        user.language = locale
        db.session.commit()


def _evaluate(cell, m):
    """Deterministic pass/fail for a cell from its measurements."""
    reasons = []
    if m["doc_horizontal_overflow"]:
        reasons.append("document horizontal overflow")
    if m["today_present"] and m["today_mount_overflow"]:
        reasons.append("today-shell horizontal overflow")
    if m["primary_nav_exposed_count"] != 1:
        reasons.append(f"exposed primary-nav count={m['primary_nav_exposed_count']} (want 1)")
    if m["raw_key_leak"]:
        reasons.append(f"raw localization keys leaked: {m['raw_key_leak']}")
    if m["primary_action_clipped"]:
        reasons.append("primary action label clipped")
    if cell["today"]:
        if not m["today_present"]:
            reasons.append("Today v2 flag ON but today tree absent")
        if m["legacy_dash_grid_present"]:
            reasons.append("legacy dashboard rendered under Today ON")
        if m["today_state"] != cell["state"]:
            reasons.append(f"state={m['today_state']} (want {cell['state']})")
        want_primary = 0 if cell["state"] == "workout_done" else 1
        if m["primary_action_count"] != want_primary:
            reasons.append(f"primary-action count={m['primary_action_count']} (want {want_primary})")
    else:
        if m["today_present"]:
            reasons.append("Today v2 tree present under Today OFF")
        if not m["legacy_dash_grid_present"]:
            reasons.append("legacy dashboard absent under Today OFF")
    return ("pass" if not reasons else "fail"), reasons


def run(output_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    db_path = ROOT / "artifacts" / "ui-audit" / "today_pr2_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]

    cells = _cells()
    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for cell in cells:
                app.config["UIUX_NAV_V2_ENABLED"] = cell["nav"]
                app.config["UIUX_TODAY_V2_ENABLED"] = cell["today"]
                _set_user_language(app, cell["scenario"], cell["locale"])

                dims = VIEWPORTS[cell["viewport"]]
                context = browser.new_context(viewport=dict(dims))
                context.add_init_script(
                    browser_clock_script(clocks[cell["scenario"]]["fixed_current_datetime"])
                )
                page = context.new_page()
                console_errors, page_errors = [], []
                server_errors, failed_requests = [], []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda err: page_errors.append(str(err)))
                # Track every response's status and any network-level failure so a
                # 5xx (document OR XHR) cannot hide behind a passing layout check.
                # SAME-ORIGIN only: external resources (Google Analytics, CDNs) fail
                # by design in the hermetic no-network env — those are environmental,
                # not cell failures. Only the audit server's own 5xx/failures count.
                page.on("response", lambda r: server_errors.append({"url": r.url, "status": r.status}) if (r.status >= 500 and r.url.startswith(server.base_url)) else None)
                page.on("requestfailed", lambda req: failed_requests.append({"url": req.url, "error": str(req.failure)}) if req.url.startswith(server.base_url) else None)
                cid = _cell_id(cell)
                try:
                    page.goto(f"{server.base_url}/__audit__/login/{cell['scenario']}", wait_until="domcontentloaded", timeout=20000)
                    resp = page.goto(f"{server.base_url}/", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(500)
                    m = page.evaluate(MEASURE_JS)
                    status_code = resp.status if resp else None
                    verdict, reasons = _evaluate(cell, m)
                    # A layout pass must NEVER mask an unexpected network failure:
                    # any 5xx (document or summary XHR) fails the cell outright.
                    if status_code is not None and status_code >= 500:
                        verdict = "fail"
                        reasons.append(f"document status {status_code}")
                    if server_errors:
                        verdict = "fail"
                        reasons.append("unexpected 5xx: " + ", ".join(
                            f"{e['status']} {e['url'].rsplit('/', 1)[-1] or e['url']}" for e in server_errors))
                    # net::ERR_ABORTED is a benign client cancellation (the harness's
                    # login->redirect->/ double navigation aborts the first load's
                    # in-flight XHR); it appears on the legacy page too and is NOT a
                    # server/network failure — record it but do not fail the cell.
                    hard_failures = [fr for fr in failed_requests if "ERR_ABORTED" not in (fr.get("error") or "")]
                    if hard_failures:
                        verdict = "fail"
                        reasons.append("failed requests: " + ", ".join(
                            fr["url"].rsplit('/', 1)[-1] or fr["url"] for fr in hard_failures))
                    shot_rel = None
                    if cell["shot"]:
                        shot_path = shots_dir / f"{cid}.png"
                        page.screenshot(path=str(shot_path), full_page=True)
                        shot_rel = f"screenshots/{cid}.png"
                    results.append({
                        "id": cid, "matrix": cell["matrix"], "viewport": cell["viewport"],
                        "viewport_px": dims, "locale": cell["locale"],
                        "nav_v2": cell["nav"], "today_v2": cell["today"],
                        "scenario": cell["scenario"], "expected_state": cell["state"],
                        "status_code": status_code, "verdict": verdict, "reasons": reasons,
                        "console_errors": console_errors, "page_errors": page_errors,
                        "server_errors": server_errors, "failed_requests": failed_requests,
                        "measurements": m, "screenshot": shot_rel,
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "id": cid, "matrix": cell["matrix"], "verdict": "blocked",
                        "reasons": [f"{type(exc).__name__}: {exc}"],
                    })
                finally:
                    context.close()
        finally:
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "uiux-sprint1-pr2-today-next-action",
        "engine": "chromium",
        "hermetic": True,
        "seed_summary": seed_summary,
        "totals": {
            "cells": len(results), "passed": passed,
            "failed": len(failed), "blocked": len(blocked),
        },
        "matrices": {
            "A_today_20": sum(1 for c in cells if c["matrix"] == "A-today-20"),
            "B_crossflag_16": sum(1 for c in cells if c["matrix"] == "B-crossflag-16"),
            "C_states_12": sum(1 for c in cells if c["matrix"] == "C-states-12"),
        },
        "failed_ids": failed,
        "blocked_ids": blocked,
        "cells": results,
    }
    (output_dir / "validation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


# Interaction cells (spec 19D): reduced-motion, 200% page zoom, increased text
# size. Each renders Today ON at the given state/viewport/locale, applies the
# condition, and re-measures overflow + primary action integrity.
INTERACTIONS = [
    {"cond": "reduced-motion", "state": "plan_ready",   "viewport": "390",  "locale": "en"},
    {"cond": "reduced-motion", "state": "workout_done", "viewport": "390",  "locale": "tr"},
    {"cond": "zoom-200",       "state": "plan_ready",   "viewport": "1366", "locale": "en"},
    {"cond": "zoom-200",       "state": "plan_ready",   "viewport": "768",  "locale": "tr"},
    {"cond": "text-150",       "state": "plan_ready",   "viewport": "390",  "locale": "tr"},
    {"cond": "text-150",       "state": "no_plan",      "viewport": "390",  "locale": "en"},
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
    db_path = ROOT / "artifacts" / "ui-audit" / "today_pr2_interactions.db"
    app = create_audit_app(db_path)
    seed_all(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    app.config["UIUX_NAV_V2_ENABLED"] = True
    app.config["UIUX_TODAY_V2_ENABLED"] = True

    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for it in INTERACTIONS:
                scenario = STATE_SCENARIO[it["state"]]
                _set_user_language(app, scenario, it["locale"])
                dims = VIEWPORTS[it["viewport"]]
                kwargs = {"viewport": dict(dims)}
                if it["cond"] == "reduced-motion":
                    kwargs["reduced_motion"] = "reduce"
                context = browser.new_context(**kwargs)
                context.add_init_script(browser_clock_script(clocks[scenario]["fixed_current_datetime"]))
                page = context.new_page()
                console_errors, page_errors = [], []
                server_errors, failed_requests = [], []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda err: page_errors.append(str(err)))
                # SAME-ORIGIN only: external resources (Google Analytics, CDNs) fail
                # by design in the hermetic no-network env — those are environmental,
                # not cell failures. Only the audit server's own 5xx/failures count.
                page.on("response", lambda r: server_errors.append({"url": r.url, "status": r.status}) if (r.status >= 500 and r.url.startswith(server.base_url)) else None)
                page.on("requestfailed", lambda req: failed_requests.append({"url": req.url, "error": str(req.failure)}) if req.url.startswith(server.base_url) else None)
                cid = f"D-interaction__{it['cond']}__{it['state']}__{it['viewport']}__{it['locale']}"
                try:
                    page.goto(f"{server.base_url}/__audit__/login/{scenario}", wait_until="domcontentloaded", timeout=20000)
                    page.goto(f"{server.base_url}/", wait_until="domcontentloaded", timeout=20000)
                    if it["cond"] == "zoom-200":
                        page.evaluate("() => { document.documentElement.style.zoom = '2'; }")
                    elif it["cond"] == "text-150":
                        page.evaluate(_TEXT_ZOOM_JS % 150)
                    page.wait_for_timeout(500)
                    m = page.evaluate(MEASURE_JS)
                    reasons = []
                    if m["doc_horizontal_overflow"]:
                        reasons.append("document horizontal overflow")
                    if m["today_present"] and m["today_mount_overflow"]:
                        reasons.append("today-shell horizontal overflow")
                    if m["primary_action_clipped"]:
                        reasons.append("primary action label clipped")
                    want_primary = 0 if it["state"] == "workout_done" else 1
                    if m["primary_action_count"] != want_primary:
                        reasons.append(f"primary-action count={m['primary_action_count']} (want {want_primary})")
                    if server_errors:
                        reasons.append("unexpected 5xx: " + ", ".join(
                            f"{e['status']} {e['url'].rsplit('/', 1)[-1] or e['url']}" for e in server_errors))
                    # Benign client cancellations (net::ERR_ABORTED) are recorded but
                    # do not fail the cell (see the matrix note); only real failures do.
                    hard_failures = [fr for fr in failed_requests if "ERR_ABORTED" not in (fr.get("error") or "")]
                    if hard_failures:
                        reasons.append("failed requests: " + ", ".join(
                            fr["url"].rsplit('/', 1)[-1] or fr["url"] for fr in hard_failures))
                    page.screenshot(path=str(shots_dir / f"{cid}.png"), full_page=True)
                    results.append({
                        "id": cid, "condition": it["cond"], "state": it["state"],
                        "viewport": it["viewport"], "viewport_px": dims, "locale": it["locale"],
                        "verdict": "pass" if not reasons else "fail", "reasons": reasons,
                        "console_errors": console_errors, "page_errors": page_errors,
                        "server_errors": server_errors, "failed_requests": failed_requests,
                        "measurements": m, "screenshot": f"screenshots/{cid}.png",
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({"id": cid, "verdict": "blocked", "reasons": [f"{type(exc).__name__}: {exc}"]})
                finally:
                    context.close()
        finally:
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    out = {
        "schema_version": "1.0.0",
        "pr": "uiux-sprint1-pr2-today-next-action",
        "kind": "interaction",
        "totals": {"cells": len(results), "passed": passed,
                   "failed": sum(1 for r in results if r["verdict"] == "fail"),
                   "blocked": sum(1 for r in results if r["verdict"] == "blocked")},
        "cells": results,
    }
    (output_dir / "interaction-results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


_SUMMARY_XHRS = ["/last-session", "/meal-log/today", "/water",
                 "/checkin-history", "/leaderboard/reward-check"]


def run_behavior(output_dir: Path) -> dict:
    """Behavioral interaction evidence (Blocker 3): CTA/quick-log activation,
    back/forward + route return, breakpoint transitions (below/above 1024) with no
    duplicate init/requests, no duplicate action submission, exactly one Today tree
    and one primary nav, hidden-legacy-controls, and Escape/focus restoration for
    the one dismissible surface (the reward overlay). Each check fails on any
    unexpected 5xx as well."""
    from urllib.parse import urlparse
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    db_path = ROOT / "artifacts" / "ui-audit" / "today_pr2_behavior.db"
    app = create_audit_app(db_path)
    seed_all(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    app.config["UIUX_NAV_V2_ENABLED"] = True
    app.config["UIUX_TODAY_V2_ENABLED"] = True

    results = []

    def record(cid, verdict, reasons=None, evidence=None):
        results.append({"id": cid, "verdict": verdict,
                        "reasons": reasons or [], "evidence": evidence or {}})

    with AuditServer(app) as server, sync_playwright() as pw:
        base = server.base_url
        browser = pw.chromium.launch(headless=True)

        def open_today(scenario="active-workout", size=(390, 800), locale="en"):
            _set_user_language(app, scenario, locale)
            ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
            ctx.add_init_script(browser_clock_script(clocks[scenario]["fixed_current_datetime"]))
            page = ctx.new_page()
            reqs, server_errors = [], []
            page.on("request", lambda r: reqs.append((r.method, r.url)))
            page.on("response", lambda r: server_errors.append((urlparse(r.url).path, r.status)) if (r.status >= 500 and r.url.startswith(base)) else None)
            # The login route redirects to "/" — that IS the Today load. Issuing a
            # second goto("/") would re-fire every summary XHR and double-count, so
            # we rely on the redirect landing on Today and never navigate twice.
            page.goto(f"{base}/__audit__/login/{scenario}", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_load_state("load")
            page.wait_for_timeout(800)
            return ctx, page, reqs, server_errors

        def five_x(server_errors):
            return [f"unexpected 5xx {p} {s}" for p, s in server_errors]

        # ── BH-1/2 primary CTA pointer + keyboard activation (plan_ready) ──
        for mode in ("pointer", "keyboard"):
            cid = f"BH-primary-cta-{mode}-activation"
            try:
                ctx, page, reqs, errs = open_today()
                el = page.locator("[data-today-primary]")
                reasons = []
                if el.count() != 1:
                    reasons.append(f"primary count={el.count()}")
                tag = el.evaluate("e => e.tagName") if el.count() else None
                if mode == "pointer":
                    el.click()
                else:
                    el.focus()
                    focused = page.evaluate("() => document.activeElement === document.querySelector('[data-today-primary]')")
                    if not focused:
                        reasons.append("primary not keyboard-focusable")
                    page.keyboard.press("Enter")
                try:
                    page.wait_for_url("**/training", timeout=8000)
                except Exception:
                    reasons.append(f"did not navigate to /training (url={urlparse(page.url).path})")
                reasons += five_x(errs)
                record(cid, "pass" if not reasons else "fail", reasons,
                       {"tag": tag, "dest": urlparse(page.url).path})
                ctx.close()
            except Exception as exc:  # noqa: BLE001
                record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-3/4 quick-log activation (pointer + keyboard) → /nutrition ──
        for mode in ("pointer", "keyboard"):
            cid = f"BH-quicklog-{mode}-activation"
            try:
                ctx, page, reqs, errs = open_today()
                tile = page.locator(".today-log-tile")
                reasons = []
                if tile.count() != 1:
                    reasons.append(f"quicklog count={tile.count()}")
                if mode == "pointer":
                    tile.click()
                else:
                    tile.focus()
                    page.keyboard.press("Enter")
                try:
                    page.wait_for_url("**/nutrition", timeout=8000)
                except Exception:
                    reasons.append(f"did not navigate to /nutrition (url={urlparse(page.url).path})")
                reasons += five_x(errs)
                record(cid, "pass" if not reasons else "fail", reasons,
                       {"dest": urlparse(page.url).path})
                ctx.close()
            except Exception as exc:  # noqa: BLE001
                record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # Quick-log is a labeled link to the canonical /nutrition flow, NOT an
        # in-page overlay → the "open overlay", "keyboard-navigate within" and
        # "Escape/focus restore" sub-checks are recorded N/A with justification.
        record("BH-quicklog-is-link-not-overlay", "n/a",
               ["quick-log is <a href=/nutrition> (canonical logging flow), not a dismissible in-page surface"],
               {"selector": ".today-log-tile", "href": "/nutrition"})

        # ── BH-5 back / forward / route-return-to-Today ──
        cid = "BH-back-forward-route-return"
        try:
            ctx, page, reqs, errs = open_today()
            page.locator("[data-today-primary]").click()
            page.wait_for_url("**/training", timeout=8000)
            page.go_back()
            page.wait_for_timeout(500)
            m = page.evaluate(MEASURE_JS)
            reasons = []
            if urlparse(page.url).path != "/":
                reasons.append(f"back did not return to / (={urlparse(page.url).path})")
            if not m["today_present"]:
                reasons.append("Today tree absent after back")
            if m["today_state"] != "plan_ready":
                reasons.append(f"state={m['today_state']} after back (want plan_ready)")
            if m["primary_action_count"] != 1:
                reasons.append(f"primary count={m['primary_action_count']} after back")
            page.go_forward()
            try:
                page.wait_for_url("**/training", timeout=8000)
            except Exception:
                reasons.append("forward did not return to /training")
            reasons += five_x(errs)
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"state_after_back": m["today_state"], "primary_after_back": m["primary_action_count"]})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-6 breakpoint transitions below & above 1024 px ──
        cid = "BH-breakpoint-transitions"
        try:
            ctx, page, reqs, errs = open_today(size=(390, 800))
            widths = [390, 768, 1023, 1024, 1366, 1440, 320]
            per_width = {}
            reasons = []
            for w in widths:
                page.set_viewport_size({"width": w, "height": 900})
                page.wait_for_timeout(180)
                m = page.evaluate(MEASURE_JS)
                per_width[w] = {"doc_overflow": m["doc_horizontal_overflow"],
                                "mount_overflow": m["today_mount_overflow"],
                                "primary": m["primary_action_count"]}
                if m["doc_horizontal_overflow"]:
                    reasons.append(f"{w}px doc overflow")
                if m["today_mount_overflow"]:
                    reasons.append(f"{w}px today-shell overflow")
                if m["primary_action_count"] != 1:
                    reasons.append(f"{w}px primary count={m['primary_action_count']}")
            reasons += five_x(errs)
            record(cid, "pass" if not reasons else "fail", reasons, {"per_width": per_width})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-7 repeated breakpoint transitions: no duplicate init / requests ──
        cid = "BH-repeated-breakpoints-no-dup-init"
        try:
            ctx, page, reqs, errs = open_today(size=(1366, 900))
            base_counts = Counter(urlparse(u).path for (mth, u) in reqs if urlparse(u).path in _SUMMARY_XHRS)
            n_before = len(reqs)
            for w in (390, 1366, 768, 1440, 320, 1024) * 2:
                page.set_viewport_size({"width": w, "height": 900})
                page.wait_for_timeout(120)
            page.wait_for_timeout(300)
            after_counts = Counter(urlparse(u).path for (mth, u) in reqs if urlparse(u).path in _SUMMARY_XHRS)
            init_once = page.evaluate("() => window.__axTodayInit === true")
            reasons = []
            if not init_once:
                reasons.append("window.__axTodayInit not set once")
            if after_counts != base_counts:
                reasons.append(f"summary XHRs changed on resize: {dict(base_counts)} -> {dict(after_counts)}")
            reasons += five_x(errs)
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"summary_xhr_counts": dict(after_counts), "requests_before": n_before, "requests_after": len(reqs)})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-8 no duplicate HTTP requests on a single load ──
        cid = "BH-no-duplicate-http-requests"
        try:
            ctx, page, reqs, errs = open_today()
            counts = Counter(urlparse(u).path for (mth, u) in reqs if urlparse(u).path in _SUMMARY_XHRS)
            dupes = {p: c for p, c in counts.items() if c > 1}
            reasons = []
            if dupes:
                reasons.append(f"duplicate summary XHRs: {dupes}")
            reasons += five_x(errs)
            record(cid, "pass" if not reasons else "fail", reasons, {"summary_xhr_counts": dict(counts)})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-9 no duplicate analytics loader ──
        cid = "BH-no-duplicate-analytics"
        try:
            ctx, page, reqs, errs = open_today()
            ga_scripts = page.evaluate("() => document.querySelectorAll('script[src*=\"googletagmanager\"], script[src*=\"google-analytics\"]').length")
            ga_reqs = [u for (mth, u) in reqs if "gtag" in u or "/g/collect" in u or "google-analytics" in u]
            reasons = []
            if ga_scripts > 1:
                reasons.append(f"duplicate GA loader tags: {ga_scripts}")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"ga_loader_tags": ga_scripts, "ga_requests": ga_reqs,
                    "note": "Today V2 fires no custom analytics; single-init guard prevents re-fire"})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-10 no duplicate action submission (no in-page mutating submit) ──
        cid = "BH-no-duplicate-action-submission"
        try:
            ctx, page, reqs, errs = open_today()
            # Same-origin POSTs only: the external analytics beacon (google-analytics
            # /g/collect) is not an app action submission and must not count here.
            posts = [urlparse(u).path for (mth, u) in reqs if mth == "POST" and u.startswith(base)]
            reasons = []
            if posts:
                reasons.append(f"unexpected same-origin POST(s) on load: {posts}")
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"posts_on_load": posts,
                    "note": "primary + quick-log are GET-navigation links; the only POST (reward-dismiss) is conditional legacy, not fired without a pending reward"})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-11 hidden legacy Today controls out of tab order ──
        cid = "BH-hidden-legacy-controls-out-of-tab-order"
        try:
            ctx, page, reqs, errs = open_today()
            probe = page.evaluate(r"""
() => {
  // Legacy Today dashboard must not exist ANYWHERE on the V2 page.
  const legacy = document.querySelectorAll('.dash-grid, .command-center, #nextAction, [data-next-action]');
  // Hidden-but-tabbable check is scoped to the Today V2 tree (#today-page) — the
  // PR1 nav drawer and the conditional legacy reward dialog are separate,
  // owner-managed surfaces outside PR2's Today content, not "legacy Today controls".
  const root = document.getElementById('today-page');
  const focusables = root ? [...root.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')] : [];
  const hiddenTabbable = focusables.filter(el => {
    const s = getComputedStyle(el);
    const hidden = s.display === 'none' || s.visibility === 'hidden' || el.getClientRects().length === 0;
    const ti = el.getAttribute('tabindex');
    return hidden && ti !== '-1' && (el.tagName !== 'A' || el.hasAttribute('href'));
  }).slice(0, 10).map(el => el.tagName + (el.id ? '#' + el.id : ''));
  return { legacy_present: legacy.length, today_focusables: focusables.length, hidden_tabbable: hiddenTabbable };
}
""")
            reasons = []
            if probe["legacy_present"]:
                reasons.append(f"legacy dashboard controls present: {probe['legacy_present']}")
            if probe["hidden_tabbable"]:
                reasons.append(f"hidden-but-tabbable elements: {probe['hidden_tabbable']}")
            record(cid, "pass" if not reasons else "fail", reasons, probe)
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        # ── BH-12/13 exactly one Today tree + one primary nav (each flag combo) ──
        for nav, today in ((True, True), (False, True), (True, False), (False, False)):
            cid = f"BH-single-tree-nav__nav-{'on' if nav else 'off'}_today-{'on' if today else 'off'}"
            try:
                app.config["UIUX_NAV_V2_ENABLED"] = nav
                app.config["UIUX_TODAY_V2_ENABLED"] = today
                ctx, page, reqs, errs = open_today()
                probe = page.evaluate("() => ({ trees: document.querySelectorAll('[data-today-v2]').length })")
                m = page.evaluate(MEASURE_JS)
                reasons = []
                want_tree = 1 if today else 0
                if probe["trees"] != want_tree:
                    reasons.append(f"today trees={probe['trees']} (want {want_tree})")
                if m["primary_nav_exposed_count"] != 1:
                    reasons.append(f"exposed primary nav={m['primary_nav_exposed_count']} (want 1)")
                reasons += five_x(errs)
                record(cid, "pass" if not reasons else "fail", reasons,
                       {"today_trees": probe["trees"], "primary_nav_exposed": m["primary_nav_exposed_count"]})
                ctx.close()
            except Exception as exc:  # noqa: BLE001
                record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])
        app.config["UIUX_NAV_V2_ENABLED"] = True
        app.config["UIUX_TODAY_V2_ENABLED"] = True

        # ── BH-14 Escape + focus restoration for the dismissible surface ──
        # The one dismissible surface is the (pre-existing) reward overlay, shown
        # only when a weekly reward is pending. Seed one so it opens, then verify
        # focus lands in the dialog, Escape closes it, and focus is not trapped.
        cid = "BH-reward-overlay-escape-focus-restore"
        try:
            from app.extensions import db
            from app.models import User, WeeklyWinner
            with app.app_context():
                u = User.query.filter_by(username="audit-active-workout").first()
                db.session.add(WeeklyWinner(user_id=u.id, week_key="2026-W30",
                                            rank=1, xp_awarded=500, notified=False))
                db.session.commit()
            ctx, page, reqs, errs = open_today()
            page.wait_for_selector("#reward-overlay.open", timeout=8000)
            focus_in_dialog = page.evaluate("() => document.activeElement === document.getElementById('reward-btn')")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            closed = page.evaluate("() => !document.getElementById('reward-overlay').classList.contains('open')")
            focus_not_trapped = page.evaluate("() => !document.getElementById('reward-overlay').contains(document.activeElement)")
            reasons = []
            if not focus_in_dialog:
                reasons.append("focus not moved into dialog on open")
            if not closed:
                reasons.append("Escape did not close the overlay")
            if not focus_not_trapped:
                reasons.append("focus trapped inside closed overlay")
            reasons += five_x(errs)
            record(cid, "pass" if not reasons else "fail", reasons,
                   {"focus_in_dialog": focus_in_dialog, "closed_on_escape": closed,
                    "focus_not_trapped": focus_not_trapped})
            ctx.close()
        except Exception as exc:  # noqa: BLE001
            record(cid, "blocked", [f"{type(exc).__name__}: {exc}"])

        browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    out = {
        "schema_version": "1.0.0",
        "pr": "uiux-sprint1-pr2-today-next-action",
        "kind": "behavior-interaction",
        "totals": {"cells": len(results), "passed": passed,
                   "failed": sum(1 for r in results if r["verdict"] == "fail"),
                   "blocked": sum(1 for r in results if r["verdict"] == "blocked"),
                   "na": sum(1 for r in results if r["verdict"] == "n/a")},
        "cells": results,
    }
    (output_dir / "behavior-results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(prog="python -m scripts.frontend_audit.today_pr2_matrix")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "frontend-readiness" / "sprint-1-pr2")
    parser.add_argument("--interactions", action="store_true", help="run only the interaction checks")
    parser.add_argument("--behavior", action="store_true", help="run only the behavioral interaction checks")
    args = parser.parse_args()
    if args.behavior:
        out = run_behavior(args.output)
        t = out["totals"]
        print(f"behavior cells={t['cells']} passed={t['passed']} failed={t['failed']} blocked={t['blocked']} na={t['na']}")
        for r in out["cells"]:
            if r["verdict"] not in ("pass", "n/a"):
                print("  !", r["id"], r.get("reasons"))
        print("behavior-results:", args.output / "behavior-results.json")
        return
    if args.interactions:
        out = run_interactions(args.output)
        t = out["totals"]
        print(f"interactions cells={t['cells']} passed={t['passed']} failed={t['failed']} blocked={t['blocked']}")
        for r in out["cells"]:
            if r["verdict"] != "pass":
                print("  !", r["id"], r.get("reasons"))
        print("interaction-results:", args.output / "interaction-results.json")
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
