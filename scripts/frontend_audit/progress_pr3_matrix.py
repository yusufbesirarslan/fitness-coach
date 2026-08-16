"""Progress Redesign PR3 — AXIS INSIGHTS exact browser matrix.

Reuses the Sprint-0 hermetic audit harness (``create_audit_app`` + ``AuditServer``
+ fixed browser clock + Chromium) to run the EXACT PR3 matrix: every canonical
Axis Insights semantic state x every required viewport, plus the endpoint-failure
state that no state-driven seed can produce. Hermetic: ``create_audit_app`` calls
``configure_audit_environment`` before any ``app.*`` import, so no production or
external dependency is contacted.

The seven states, and what each one exists to prove:

  progressing        WHAT'S WORKING is populated, WATCH THIS is deliberately EMPTY
  steady             the steady positive + a canonical SECONDARY watch reason
  build_consistency  no positive signal at all -> WHAT'S WORKING empty, not faked
  plateau            needs_attention, yet consistency is still praised (invariant)
  deload             the planner's PRIMARY reason outranks its trend nuances
  insufficient_data  both context slots insufficient, NEXT MOVE still canonical
  endpoint_failure   Rule 18: the other four Progress sections still render

Seeds are computed against the harness's FIXED clock (2026-07-20 Istanbul) and
target the canonical thresholds in ``training_progression`` — they are not tuned
to an expected output. Each cell asserts the state the SERVICE reports, so a seed
that drifts fails loudly instead of silently auditing the wrong state.

Run (WSL, Sprint-0 venv + browsers):
  PLAYWRIGHT_BROWSERS_PATH=<cache> \
    python -m scripts.frontend_audit.progress_pr3_matrix \
      --output docs/frontend-readiness/progress-pr3
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .app import ROOT, create_audit_app
from .runner import AuditServer, browser_clock_script
from .seed import seed_all

# The four viewports the PR3 brief requires, verbatim.
VIEWPORTS = {
    "390": {"width": 390, "height": 844},
    "768": {"width": 768, "height": 1024},
    "1280": {"width": 1280, "height": 800},
    "1440": {"width": 1440, "height": 900},
}

# The harness's fixed Istanbul day for every scenario used below.
AUDIT_DAY = date(2026, 7, 20)

# Canonical state -> the audit scenario whose user carries its seeded history.
# Every one of these is an "active" seeded user (profile complete, loginable),
# and none of them is `completed-workout`, whose pre-seeded WorkoutLog would
# contaminate the deliberately shaped histories below.
STATE_SCENARIO = {
    "insufficient_data": "social-empty",
    "build_consistency": "active-rest-day",
    "plateau": "coach-history",
    "deload": "wearable-connected",
    "progressing": "active-workout",
    "steady": "wearable-disconnected",
}
FAILURE_SCENARIO = "progress-history"

# Week offsets (0 = the trailing window ending on AUDIT_DAY) and the weekly
# volume to log in each. Derived from training_progression's documented
# thresholds: CONSISTENCY_MIN_ACTIVE_WEEKS=3 of 4, MIN_PLATEAU_WEEKS=3 flat
# active weeks, MIN_DELOAD_WEEKS=4 active weeks with no rest week.
STATE_HISTORY = {
    # Nothing at all: fewer than MIN_DATA_WEEKS active windows.
    "insufficient_data": {},
    # Two active windows out of four -> enough data, but inconsistent.
    "build_consistency": {0: 900.0, 2: 900.0},
    # Three active windows, all flat -> plateau; the fourth is a rest week, so
    # deload does NOT fire (a rest week is itself a deload).
    "plateau": {0: 1000.0, 1: 1000.0, 2: 1000.0},
    # Four active windows, all flat -> plateau AND an unbroken block -> deload.
    "deload": {0: 1000.0, 1: 1000.0, 2: 1000.0, 3: 1000.0},
    # Rising volume -> progressing (and therefore no plateau, no deload).
    "progressing": {0: 1600.0, 1: 1300.0, 2: 1050.0, 3: 900.0},
    # Falling volume over three active windows -> not flat (no plateau), not
    # rising (not progressing), still consistent -> keep_pushing/steady. The
    # down-trend also gives the planner a SECONDARY reason code to publish.
    "steady": {0: 900.0, 1: 1200.0, 2: 1600.0},
}

# A slot carries no data-status until the client has rendered the single async
# read into it, so this is the settle signal — not a sleep tuned to a machine.
SETTLED_JS = r"""
() => ['ax-working', 'ax-watch', 'ax-next'].every((id) => {
  const el = document.getElementById(id);
  return !!(el && el.getAttribute('data-status'));
})
"""

MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const text = (el) => (el && el.textContent ? el.textContent.trim() : '');
  const section = document.querySelector('[aria-labelledby="ai-h"]');
  const slots = q('.ax-slot').map((el) => {
    const head = el.querySelector('[data-slot="headline"]');
    const detail = el.querySelector('[data-slot="detail"]');
    const label = el.querySelector('.ax-slot-label');
    const cs = getComputedStyle(el);
    return {
      id: el.id,
      status: el.getAttribute('data-status'),
      label: text(label),
      label_tag: label ? label.tagName : null,
      headline: text(head),
      detail: detail && !detail.hidden ? text(detail) : null,
      overflow: el.scrollWidth > el.clientWidth + 1,
      visible: cs.display !== 'none' && cs.visibility !== 'hidden'
               && el.getClientRects().length > 0,
    };
  });
  const bodyText = document.body ? document.body.innerText : '';
  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,
    section_present: !!section,
    section_heading_tag: section && section.querySelector('#ai-h')
      ? section.querySelector('#ai-h').tagName : null,
    section_overflow: section ? section.scrollWidth > section.clientWidth + 1 : false,
    slots: slots,
    slot_count: slots.length,
    // The other four Progress sections. Rule 18: an Axis Insights failure must
    // not take any of them down.
    your_progress_state: text(document.getElementById('ps-state')),
    what_changed_cards: q('.wc-card').length,
    what_changed_values: q('.wc-card [data-slot="value"]').map(text),
    physique_filled: text(document.getElementById('physique-body')).length > 0,
    history_filled: text(document.getElementById('history-list')).length > 0,
    // A raw i18n key on screen means a missing translation, not a state.
    raw_key_leak: [...new Set(bodyText.match(/\bprogress\.[a-z_]+/g) || [])].slice(0, 10),
    // No carousel: nothing inside the section may scroll horizontally.
    horizontal_scrollers: q('[aria-labelledby="ai-h"] *')
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.className || el.tagName).slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""

FOCUS_JS = r"""
() => {
  // Keyboard reachability + visible focus, measured rather than assumed: the
  // check-in opener is the first interactive control after the insight slots.
  const btn = document.querySelector('[data-action="openCheckin"]');
  if (!btn) return {focusable: false, outline: null};
  btn.focus();
  const cs = getComputedStyle(btn);
  return {
    focusable: document.activeElement === btn,
    outline: cs.outlineStyle + ' ' + cs.outlineWidth,
    box_shadow: cs.boxShadow,
  };
}
"""


def _naive_utc_noon(day: date) -> datetime:
    """Noon Istanbul on ``day``, stored naive-UTC like every other seed row."""
    return datetime(day.year, day.month, day.day, 12) - timedelta(hours=3)


def seed_states(app) -> dict:
    """Write the per-state WorkoutLog history onto each scenario's user.

    Runs after ``seed_all`` (which drops and recreates every table), so this
    only adds rows. Volumes are split across two exercises per active window so
    the estimated-1RM series stays flat and volume remains the driving signal.
    """
    from app.extensions import db
    from app.models import User, WorkoutLog

    counts = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            assert user is not None, scenario
            # Any pre-seeded workout row would contaminate the shaped history.
            WorkoutLog.query.filter_by(user_id=user.id).delete()
            rows = 0
            for week_offset, volume in STATE_HISTORY[state].items():
                day = AUDIT_DAY - timedelta(days=week_offset * 7)
                db.session.add(WorkoutLog(
                    user_id=user.id, exercise_name="Squat", sets=4, reps=8,
                    weight_kg=60.0, volume=volume,
                    created_at=_naive_utc_noon(day),
                ))
                rows += 1
            counts[state] = rows
        db.session.commit()
    return counts


def verify_states(app) -> dict:
    """Assert the seeds really produce the canonical states they claim.

    This is the part that keeps the matrix honest. Without it a drifting seed
    would quietly audit six copies of the same state while every cell passed.
    """
    from app.models import User
    from app.services.progress_insights import build_progress_insights
    from app.services.progress_summary import build_progress_summary

    observed = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            summary = build_progress_summary(user.id, end_day=AUDIT_DAY)
            insights = build_progress_insights(user.id, end_day=AUDIT_DAY)
            observed[state] = {
                "scenario": scenario,
                "next_signal": summary.performance.next_signal,
                "trajectory": summary.trajectory.state,
                "working": [insights.working.status, insights.working.code],
                "watch": [insights.watch.status, insights.watch.code],
                "next_move": [insights.next_move.status, insights.next_move.code],
            }
    return observed


# Expected canonical signal per state, and the slot statuses that make each
# state worth auditing. ``None`` means "not asserted here" (the service tests
# own the full table); these are the claims the SCREENSHOTS are evidence for.
EXPECTED = {
    "insufficient_data": {"signal": "insufficient_data",
                          "working": "insufficient_data", "watch": "insufficient_data"},
    "build_consistency": {"signal": "build_consistency",
                          "working": "empty", "watch": "available"},
    "plateau": {"signal": "plateau", "working": "available", "watch": "available"},
    "deload": {"signal": "deload", "working": "available", "watch": "available"},
    "progressing": {"signal": "progressing", "working": "available", "watch": "empty"},
    "steady": {"signal": "keep_pushing", "working": "available", "watch": "available"},
}


def _cells():
    """Every state x every required viewport, plus the failure state."""
    cells = []
    for state in STATE_SCENARIO:
        for viewport in VIEWPORTS:
            cells.append({
                "state": state, "viewport": viewport,
                "scenario": STATE_SCENARIO[state], "fail": False,
                # One curated screenshot per state at a mobile and a desktop width.
                "shot": viewport in ("390", "1440"),
            })
    for viewport in VIEWPORTS:
        cells.append({
            "state": "endpoint_failure", "viewport": viewport,
            "scenario": FAILURE_SCENARIO, "fail": True,
            "shot": viewport in ("390", "1440"),
        })
    return cells


def _cell_id(cell):
    return f"axis-insights__{cell['state']}__{cell['viewport']}"


def _evaluate(cell, m, focus):
    """Deterministic pass/fail for one cell from its measurements."""
    reasons = []

    if m["doc_horizontal_overflow"]:
        reasons.append(
            f"document horizontal overflow ({m['doc_scroll_width']}"
            f" > {m['doc_client_width']})")
    if m["section_overflow"]:
        reasons.append("AXIS INSIGHTS section horizontal overflow")
    if m["horizontal_scrollers"]:
        reasons.append(f"horizontal scroller inside the section: {m['horizontal_scrollers']}")

    # Exactly three slots, in every state including failure.
    if m["slot_count"] != 3:
        reasons.append(f"slot count={m['slot_count']} (want 3)")
    if m["section_heading_tag"] != "H2":
        reasons.append(f"section heading is {m['section_heading_tag']} (want H2)")

    for slot in m["slots"]:
        if not slot["visible"]:
            reasons.append(f"{slot['id']} is not visible")
        if slot["overflow"]:
            reasons.append(f"{slot['id']} overflows horizontally")
        if slot["label_tag"] != "H3":
            reasons.append(f"{slot['id']} label is {slot['label_tag']} (want H3)")
        if not slot["label"]:
            reasons.append(f"{slot['id']} has no visible slot name")
        # Status must never be carried by colour alone: whenever the client has
        # written a status, it has written a headline in the same call.
        if slot["status"] and not slot["headline"]:
            reasons.append(f"{slot['id']} has status={slot['status']} but no text")

    if m["raw_key_leak"]:
        reasons.append(f"raw localization keys leaked: {m['raw_key_leak']}")

    statuses = {s["id"]: s["status"] for s in m["slots"]}

    if cell["fail"]:
        # Rule 7 + Rule 18: a distinct failure state, and the rest of the page
        # keeps rendering.
        for slot_id, status in statuses.items():
            if status != "unavailable":
                reasons.append(f"{slot_id} status={status} (want unavailable on failure)")
        if not m["your_progress_state"]:
            reasons.append("YOUR PROGRESS did not render during an insights failure")
        if m["what_changed_cards"] != 3:
            reasons.append(f"WHAT CHANGED cards={m['what_changed_cards']} (want 3)")
        if not m["physique_filled"]:
            reasons.append("PHYSIQUE PROGRESS did not render during an insights failure")
        if not m["history_filled"]:
            reasons.append("PROGRESS HISTORY did not render during an insights failure")
    else:
        expected = EXPECTED[cell["state"]]
        if statuses.get("ax-working") != expected["working"]:
            reasons.append(
                f"working status={statuses.get('ax-working')} (want {expected['working']})")
        if statuses.get("ax-watch") != expected["watch"]:
            reasons.append(
                f"watch status={statuses.get('ax-watch')} (want {expected['watch']})")
        # NEXT MOVE is canonical in every state, including insufficient history.
        if statuses.get("ax-next") != "available":
            reasons.append(f"next_move status={statuses.get('ax-next')} (want available)")
        if not any(s["id"] == "ax-next" and s["headline"] for s in m["slots"]):
            reasons.append("NEXT MOVE rendered no instruction")

    if not focus.get("focusable"):
        reasons.append("check-in opener is not keyboard focusable")

    return ("pass" if not reasons else "fail"), reasons


# Console lines the analytics tag emits in the offline hermetic env. Matching is
# deliberately narrow (the tag's own hosts) so a real app-side CSP or fetch error
# is never swallowed. Both the raw and the filtered list are kept in the manifest.
_THIRD_PARTY_CONSOLE_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "www.google.com/g/collect",
    "clarity.ms",
)


def _is_third_party_console_noise(message: str) -> bool:
    return any(host in message for host in _THIRD_PARTY_CONSOLE_HOSTS)


def run(output_dir: Path) -> dict:
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright

    from app.blueprints import tracking

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    db_path = ROOT / "artifacts" / "ui-audit" / "progress_pr3_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    seed_summary["axis_insight_rows"] = seed_states(app)
    observed_states = verify_states(app)

    # Fail before capturing anything if a seed no longer produces its state:
    # auditing the wrong state is worse than not auditing it.
    drift = {
        state: observed_states[state]
        for state, want in EXPECTED.items()
        if observed_states[state]["next_signal"] != want["signal"]
    }

    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    real_builder = tracking.build_progress_insights

    def _broken_builder(*_a, **_kw):
        raise RuntimeError("audit-injected Axis Insights failure")

    cells = _cells()
    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for cell in cells:
                # Harness-only monkeypatch of the route's service call. Nothing
                # in the application is flag-gated for this; the failure state
                # is injected here so production keeps exactly one code path.
                tracking.build_progress_insights = (
                    _broken_builder if cell["fail"] else real_builder)

                dims = VIEWPORTS[cell["viewport"]]
                context = browser.new_context(viewport=dict(dims))
                context.add_init_script(
                    browser_clock_script(
                        clocks[cell["scenario"]]["fixed_current_datetime"]))
                page = context.new_page()
                console_errors, page_errors = [], []
                server_errors, failed_requests = [], []
                page.on("console", lambda msg: console_errors.append(msg.text)
                        if msg.type == "error" else None)
                page.on("pageerror", lambda err: page_errors.append(str(err)))
                # Same-origin only: external resources fail by design in the
                # hermetic no-network env. The deliberate 500 on the failure
                # cells is expected and recorded rather than counted as a fault.
                page.on("response", lambda r: server_errors.append(
                    {"url": r.url, "status": r.status})
                    if (r.status >= 500 and r.url.startswith(server.base_url)) else None)
                page.on("requestfailed", lambda req: failed_requests.append(
                    {"url": req.url, "error": str(req.failure)})
                    if req.url.startswith(server.base_url) else None)

                cid = _cell_id(cell)
                try:
                    page.goto(f"{server.base_url}/__audit__/login/{cell['scenario']}",
                              wait_until="domcontentloaded", timeout=20000)
                    resp = page.goto(f"{server.base_url}/progress-page",
                                     wait_until="domcontentloaded", timeout=20000)
                    # The three slots are filled by ONE async read. Measuring on
                    # a fixed timeout raced it (a settled slot carries a
                    # data-status; the server-rendered placeholder does not), so
                    # wait for the client to actually settle all three.
                    try:
                        page.wait_for_function(SETTLED_JS, timeout=10000)
                    except PWTimeoutError:
                        pass  # measured below and reported as the failure it is
                    m = page.evaluate(MEASURE_JS)
                    focus = page.evaluate(FOCUS_JS)
                    status_code = resp.status if resp else None
                    verdict, reasons = _evaluate(cell, m, focus)

                    if status_code is not None and status_code >= 500:
                        verdict = "fail"
                        reasons.append(f"document status {status_code}")
                    # The injected axis-insights 500 is the point of the failure
                    # cells; any OTHER 5xx is a real fault.
                    unexpected = [
                        e for e in server_errors
                        if not (cell["fail"] and e["url"].endswith("/api/progress/axis-insights"))
                    ]
                    if unexpected:
                        verdict = "fail"
                        reasons.append("unexpected 5xx: " + ", ".join(
                            f"{e['status']} {e['url'].rsplit('/', 1)[-1]}" for e in unexpected))
                    # net::ERR_ABORTED is a benign client cancellation from the
                    # harness's login->redirect double navigation.
                    hard = [f for f in failed_requests
                            if "ERR_ABORTED" not in (f.get("error") or "")]
                    if hard:
                        verdict = "fail"
                        reasons.append("failed requests: " + ", ".join(
                            f["url"].rsplit("/", 1)[-1] for f in hard))
                    # An uncaught page error is a defect even when the layout is
                    # fine. Console errors are filtered to OUR code first: the
                    # hermetic env has no network, so the third-party analytics
                    # tag logs CSP/connect failures on every page in the repo —
                    # pre-existing environment noise, not a PR3 signal.
                    own_console = [c for c in console_errors
                                   if not _is_third_party_console_noise(c)]
                    if cell["fail"]:
                        # The browser logs the injected 500 as a resource error.
                        # That log IS the failure cell's premise; the assertion
                        # is that the page degraded, which _evaluate checks.
                        own_console = [c for c in own_console
                                       if "500" not in c]
                    if own_console or page_errors:
                        verdict = "fail"
                        reasons.append("console/page errors present")

                    shot_rel = None
                    if cell["shot"]:
                        shot_path = shots_dir / f"{cid}.png"
                        page.screenshot(path=str(shot_path), full_page=True)
                        shot_rel = f"screenshots/{cid}.png"

                    results.append({
                        "id": cid, "state": cell["state"], "viewport": cell["viewport"],
                        "viewport_px": dims, "scenario": cell["scenario"],
                        "injected_failure": cell["fail"], "status_code": status_code,
                        "verdict": verdict, "reasons": reasons,
                        "console_errors": console_errors,
                        "own_console_errors": own_console,
                        "page_errors": page_errors,
                        "server_errors": server_errors, "failed_requests": failed_requests,
                        "focus": focus, "measurements": m, "screenshot": shot_rel,
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "id": cid, "state": cell["state"], "viewport": cell["viewport"],
                        "verdict": "blocked", "reasons": [f"{type(exc).__name__}: {exc}"],
                    })
                finally:
                    context.close()
        finally:
            tracking.build_progress_insights = real_builder
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "progress-redesign-pr3-axis-insights",
        "engine": "chromium",
        "hermetic": True,
        "audit_day": AUDIT_DAY.isoformat(),
        "seed_summary": seed_summary,
        "observed_states": observed_states,
        "seed_drift": drift,
        "viewports": VIEWPORTS,
        "totals": {
            "cells": len(results), "passed": passed,
            "failed": len(failed), "blocked": len(blocked),
        },
        "failed_ids": failed,
        "blocked_ids": blocked,
        "cells": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "validation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = run(args.output)
    print(json.dumps({
        "totals": manifest["totals"],
        "seed_drift": manifest["seed_drift"],
        "failed_ids": manifest["failed_ids"],
        "blocked_ids": manifest["blocked_ids"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
