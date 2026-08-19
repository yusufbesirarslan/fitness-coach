"""Progress Redesign PR5 — PROGRESS HISTORY exact browser matrix.

Reuses the Sprint-0 hermetic audit harness to run the EXACT PR5 matrix:
every required History semantic state x every required viewport, plus the
endpoint-failure state. Hermetic: ``create_audit_app`` calls
``configure_audit_environment`` before any ``app.*`` import.

The nine states:

  empty                    no check-ins
  sparse_only              weight-only rows stay excluded
  one_baseline             one qualifying check-in, building baseline
  mixed                    multiple mixed reconstructed states
  on_track_expanded        on-track row expanded
  needs_attention_expanded needs-attention row expanded
  missing_weight           qualifying row with no usable weight
  bounded_has_more         12 visible rows, has_more true
  endpoint_failure         HISTORY alone unavailable; other four stay

Run (WSL, Sprint-0 venv + browsers):
  PLAYWRIGHT_BROWSERS_PATH=<cache> \\
    python -m scripts.frontend_audit.progress_pr5_matrix \\
      --output docs/frontend-readiness/progress-pr5
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .app import ROOT, create_audit_app
from .runner import AuditServer, browser_clock_script
from .seed import seed_all


VIEWPORTS = {
    "390": {"width": 390, "height": 844},
    "768": {"width": 768, "height": 1024},
    "1280": {"width": 1280, "height": 800},
    "1440": {"width": 1440, "height": 900},
}

AUDIT_DAY = date(2026, 7, 20)

STATE_SCENARIO = {
    "empty": "social-empty",
    "sparse_only": "active-rest-day",
    "one_baseline": "coach-history",
    "mixed": "wearable-connected",
    "on_track_expanded": "active-workout",
    "needs_attention_expanded": "wearable-disconnected",
    "missing_weight": "progress-history",
    "bounded_has_more": "completed-workout",
}
FAILURE_SCENARIO = "social-empty"


SETTLED_JS = r"""
() => {
  const history = document.getElementById('history-list');
  const historyReady = !!(history && (
    history.getAttribute('data-state') ||
    history.getAttribute('data-status') === 'unavailable'
  ));
  const your = document.getElementById('ps-state');
  const working = document.getElementById('ax-working');
  const physique = document.getElementById('physique-body');
  return !!(
    historyReady &&
    your && your.textContent && your.textContent.trim() &&
    working && working.getAttribute('data-status') &&
    physique && physique.textContent && physique.textContent.trim()
  );
}
"""

MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const text = (el) => (el && el.textContent ? el.textContent.trim() : '');
  const section = document.querySelector('[aria-labelledby="ph-h"]');
  const box = document.getElementById('history-list');
  const items = q('.hist-item').map((el) => {
    const btn = el.querySelector('.hist-trigger');
    const detail = el.querySelector('.hist-detail');
    const cs = getComputedStyle(el);
    return {
      state: el.getAttribute('data-state') || '',
      traj: text(el.querySelector('.hist-traj')),
      date: text(el.querySelector('.hist-date')),
      meta: text(el.querySelector('.hist-meta')),
      weight: text(el.querySelector('.hist-weight')),
      tag: btn ? btn.tagName : null,
      type: btn ? btn.getAttribute('type') : null,
      expanded: btn ? btn.getAttribute('aria-expanded') : null,
      controls: btn ? btn.getAttribute('aria-controls') : null,
      detail_hidden: detail ? detail.hasAttribute('hidden') : null,
      overflow: el.scrollWidth > el.clientWidth + 1,
      visible: cs.display !== 'none' && cs.visibility !== 'hidden'
               && el.getClientRects().length > 0,
    };
  });
  const longOverflow = q('#history-list p, #history-list span, #history-list dd, #history-list button')
    .filter((el) => el.scrollWidth > el.clientWidth + 1)
    .map((el) => el.className || el.tagName)
    .slice(0, 10);
  const bodyText = document.body ? document.body.innerText : '';
  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,
    section_present: !!section,
    section_heading_tag: section && section.querySelector('#ph-h')
      ? section.querySelector('#ph-h').tagName : null,
    section_overflow: section ? section.scrollWidth > section.clientWidth + 1 : false,
    history_state: box ? box.getAttribute('data-state') : null,
    history_status: box ? box.getAttribute('data-status') : null,
    history_text: text(box),
    history_text_len: text(box).length,
    item_count: items.length,
    items: items,
    has_more: !!(box && /older check-ins exist|daha eski check-in/i.test(text(box))),
    empty: !!(box && /no check-ins yet|henüz check-in yok/i.test(text(box))),
    unavailable: !!(box && (
      box.getAttribute('data-status') === 'unavailable' ||
      /couldn't be loaded|yüklenemedi/i.test(text(box))
    )),
    long_text_overflow: longOverflow,
    your_progress_state: text(document.getElementById('ps-state')),
    what_changed_cards: q('.wc-card').length,
    axis_working_status: document.getElementById('ax-working')
      ? document.getElementById('ax-working').getAttribute('data-status') : null,
    physique_filled: text(document.getElementById('physique-body')).length > 0,
    raw_key_leak: [...new Set(bodyText.match(/\bprogress\.[a-z_]+/g) || [])].slice(0, 10),
    horizontal_scrollers: q('[aria-labelledby="ph-h"] *')
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.className || el.tagName).slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""

FOCUS_JS = r"""
() => {
  const btn = document.querySelector('.hist-trigger');
  if (!btn) return {present: false, focusable: false, outline: null};
  btn.focus();
  const cs = getComputedStyle(btn);
  return {
    present: true,
    tag: btn.tagName,
    expanded: btn.getAttribute('aria-expanded'),
    focusable: document.activeElement === btn,
    outline: cs.outlineStyle + ' ' + cs.outlineWidth,
    box_shadow: cs.boxShadow,
  };
}
"""


def _naive_utc_noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12) - timedelta(hours=3)


def _add_workout(db, WorkoutLog, user, day, volume):
    db.session.add(WorkoutLog(
        user_id=user.id, exercise_name="Squat", sets=4, reps=8,
        weight_kg=60.0, volume=volume,
        created_at=_naive_utc_noon(day),
    ))


def _add_checkin(db, WeeklyCheckIn, user, day, weight, qualifying=True):
    db.session.add(WeeklyCheckIn(
        user_id=user.id,
        weight=weight,
        yogunluk=3 if qualifying else None,
        created_at=_naive_utc_noon(day),
    ))


def _progressing_weeks(db, WorkoutLog, user, end_day):
    volumes = (900.0, 1050.0, 1300.0, 1600.0)
    for week_offset, volume in enumerate(reversed(volumes)):
        _add_workout(db, WorkoutLog, user, end_day - timedelta(days=week_offset * 7), volume)


def _inconsistent_weeks(db, WorkoutLog, user, end_day):
    _add_workout(db, WorkoutLog, user, end_day, 900.0)
    _add_workout(db, WorkoutLog, user, end_day - timedelta(days=21), 900.0)


def seed_states(app) -> dict:
    from app.extensions import db
    from app.models import User, WeeklyCheckIn, WorkoutLog

    counts = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            assert user is not None, scenario
            WeeklyCheckIn.query.filter_by(user_id=user.id).delete()
            WorkoutLog.query.filter_by(user_id=user.id).delete()
            db.session.flush()
            if state == "empty":
                counts[state] = 0
                continue
            if state == "sparse_only":
                _add_checkin(db, WeeklyCheckIn, user, AUDIT_DAY, 80.0, qualifying=False)
                _add_checkin(
                    db, WeeklyCheckIn, user, AUDIT_DAY - timedelta(days=7),
                    79.0, qualifying=False)
                counts[state] = 2
                continue
            if state == "one_baseline":
                _add_checkin(db, WeeklyCheckIn, user, AUDIT_DAY, 78.4)
                counts[state] = 1
                continue
            if state == "mixed":
                _add_checkin(
                    db, WeeklyCheckIn, user, AUDIT_DAY - timedelta(days=28), 82.0)
                _progressing_weeks(db, WorkoutLog, user, AUDIT_DAY)
                _add_checkin(db, WeeklyCheckIn, user, AUDIT_DAY, 78.4)
                counts[state] = 2
                continue
            if state == "on_track_expanded":
                _progressing_weeks(db, WorkoutLog, user, AUDIT_DAY)
                _add_checkin(db, WeeklyCheckIn, user, AUDIT_DAY, 78.4)
                counts[state] = 1
                continue
            if state == "needs_attention_expanded":
                _inconsistent_weeks(db, WorkoutLog, user, AUDIT_DAY)
                _add_checkin(db, WeeklyCheckIn, user, AUDIT_DAY, 80.0)
                counts[state] = 1
                continue
            if state == "missing_weight":
                _add_checkin(db, WeeklyCheckIn, user, AUDIT_DAY, 0.0)
                counts[state] = 1
                continue
            if state == "bounded_has_more":
                for i in range(13):
                    _add_checkin(
                        db, WeeklyCheckIn, user,
                        AUDIT_DAY - timedelta(days=(12 - i) * 7),
                        70.0 + i)
                counts[state] = 13
                continue
        db.session.commit()
    return counts


def verify_states(app) -> dict:
    from app.models import User
    from app.services.progress_history import build_progress_history

    observed = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            history = build_progress_history(user.id)
            trajectories = [e.trajectory.state for e in history.entries]
            observed[state] = {
                "scenario": scenario,
                "state": history.state,
                "entry_count": len(history.entries),
                "has_more": history.has_more,
                "trajectories": trajectories,
                "newest_weight": (
                    history.entries[0].weight_kg if history.entries else None),
            }
    return observed


EXPECTED = {
    "empty": {
        "state": "empty", "entry_count": 0, "has_more": False,
        "trajectories": [],
    },
    "sparse_only": {
        "state": "empty", "entry_count": 0, "has_more": False,
        "trajectories": [],
    },
    "one_baseline": {
        "state": "available", "entry_count": 1, "has_more": False,
        "trajectories": ["building_baseline"],
    },
    "mixed": {
        "state": "available", "entry_count": 2, "has_more": False,
        "trajectories": ["on_track", "building_baseline"],
    },
    "on_track_expanded": {
        "state": "available", "entry_count": 1, "has_more": False,
        "trajectories": ["on_track"],
    },
    "needs_attention_expanded": {
        "state": "available", "entry_count": 1, "has_more": False,
        "trajectories": ["needs_attention"],
    },
    "missing_weight": {
        "state": "available", "entry_count": 1, "has_more": False,
        "trajectories": ["building_baseline"],
        "newest_weight": None,
    },
    "bounded_has_more": {
        "state": "available", "entry_count": 12, "has_more": True,
    },
}


def _cells(only=None):
    cells = []
    for state in STATE_SCENARIO:
        for viewport in VIEWPORTS:
            cells.append({
                "state": state, "viewport": viewport,
                "scenario": STATE_SCENARIO[state], "fail": False,
                "shot": viewport in ("390", "1440"),
            })
    for viewport in VIEWPORTS:
        cells.append({
            "state": "endpoint_failure", "viewport": viewport,
            "scenario": FAILURE_SCENARIO, "fail": True,
            "shot": viewport in ("390", "1440"),
        })
    if only:
        wanted = set(only)
        cells = [
            cell for cell in cells
            if f"{cell['state']}:{cell['viewport']}" in wanted
            or cell["state"] in wanted
        ]
    return cells


def _cell_id(cell):
    return f"progress-history__{cell['state']}__{cell['viewport']}"


def _has_visible_focus(focus):
    if not focus.get("focusable"):
        return False
    outline = focus.get("outline") or ""
    shadow = focus.get("box_shadow") or ""
    if outline and not outline.startswith("none") and "0px" not in outline.split():
        return True
    return shadow not in ("", "none")


def _evaluate(cell, m, focus, keyboard=None):
    reasons = []

    if m["doc_horizontal_overflow"]:
        reasons.append(
            f"document horizontal overflow ({m['doc_scroll_width']}"
            f" > {m['doc_client_width']})")
    if m["section_overflow"]:
        reasons.append("PROGRESS HISTORY section horizontal overflow")
    if m["horizontal_scrollers"]:
        reasons.append(f"horizontal scroller inside the section: {m['horizontal_scrollers']}")
    if m["long_text_overflow"]:
        reasons.append(f"long text overflow: {m['long_text_overflow']}")
    if m["section_heading_tag"] != "H2":
        reasons.append(f"section heading is {m['section_heading_tag']} (want H2)")
    if m["raw_key_leak"]:
        reasons.append(f"raw localization keys leaked: {m['raw_key_leak']}")

    if not m["your_progress_state"]:
        reasons.append("YOUR PROGRESS did not render")
    if m["what_changed_cards"] != 3:
        reasons.append(f"WHAT CHANGED cards={m['what_changed_cards']} (want 3)")
    if not m["axis_working_status"]:
        reasons.append("AXIS INSIGHTS did not settle")
    if not m["physique_filled"]:
        reasons.append("PHYSIQUE PROGRESS did not render")

    if cell["fail"]:
        if not m["unavailable"]:
            reasons.append("failure cell did not render the unavailable state")
        if m["item_count"]:
            reasons.append("failure cell rendered history rows")
        if m["history_state"] == "empty":
            reasons.append("failure cell published empty")
        return ("pass" if not reasons else "fail"), reasons

    expected = EXPECTED[cell["state"]]
    if m["history_state"] != expected["state"]:
        reasons.append(
            f"history state={m['history_state']} (want {expected['state']})")
    if expected["state"] == "empty":
        if not m["empty"]:
            reasons.append("empty cell missing empty copy")
        if m["item_count"]:
            reasons.append(f"empty cell rendered {m['item_count']} rows")
    else:
        if m["item_count"] != expected["entry_count"]:
            reasons.append(
                f"item_count={m['item_count']} (want {expected['entry_count']})")
        want_traj = expected.get("trajectories")
        if want_traj:
            got = [item["state"] for item in m["items"]]
            if got != want_traj:
                reasons.append(f"row states={got} (want {want_traj})")
        for item in m["items"]:
            if not item["traj"]:
                reasons.append("trajectory is colour-only (no text)")
            if item["tag"] != "BUTTON":
                reasons.append(f"row trigger is {item['tag']} (want BUTTON)")
            if item["expanded"] is None:
                reasons.append("missing aria-expanded")
            if not item["controls"]:
                reasons.append("missing aria-controls")
            if not item["date"]:
                reasons.append("row missing date text")

    if cell["state"] == "bounded_has_more" and not m["has_more"]:
        reasons.append("bounded cell missing has_more note")
    if cell["state"] == "missing_weight":
        joined = " ".join(item.get("weight") or "" for item in m["items"])
        if m["items"] and m["items"][0].get("weight"):
            reasons.append("missing-weight row showed a weight")

    interactive = cell["state"] in {
        "on_track_expanded", "needs_attention_expanded", "mixed",
        "bounded_has_more", "one_baseline", "missing_weight",
    }
    if interactive and m["item_count"]:
        if not focus.get("present"):
            reasons.append("history trigger is missing")
        elif not focus.get("focusable"):
            reasons.append("history trigger is not keyboard focusable")
        elif not _has_visible_focus(focus):
            reasons.append(f"history trigger focus is not visible ({focus})")
        if keyboard:
            if keyboard.get("expanded_after") != "true":
                reasons.append("Enter did not expand the row")
            if keyboard.get("errors"):
                reasons.append(f"row keyboard errors: {keyboard['errors']}")

    return ("pass" if not reasons else "fail"), reasons


_THIRD_PARTY_CONSOLE_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "www.google.com/g/collect",
    "clarity.ms",
)


def _is_third_party_console_noise(message: str) -> bool:
    return any(host in message for host in _THIRD_PARTY_CONSOLE_HOSTS)


def run(output_dir: Path, only=None) -> dict:
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright

    from app.blueprints import tracking

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    db_path = ROOT / "artifacts" / "ui-audit" / "progress_pr5_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    seed_summary["history_rows"] = seed_states(app)
    observed_states = verify_states(app)

    drift = {}
    for state, want in EXPECTED.items():
        got = observed_states[state]
        mismatched = (
            got["state"] != want["state"]
            or got["entry_count"] != want["entry_count"]
            or got["has_more"] != want["has_more"]
        )
        if "trajectories" in want and got["trajectories"] != want["trajectories"]:
            mismatched = True
        if "newest_weight" in want and got["newest_weight"] != want["newest_weight"]:
            mismatched = True
        if mismatched:
            drift[state] = got

    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    real_builder = tracking.build_progress_history

    def _broken_builder(*_a, **_kw):
        raise RuntimeError("audit-injected Progress History failure")

    cells = _cells(only)
    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for cell in cells:
                tracking.build_progress_history = (
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
                page.on("response", lambda r: server_errors.append(
                    {"url": r.url, "status": r.status})
                    if (r.status >= 500 and r.url.startswith(server.base_url)) else None)
                page.on("requestfailed", lambda req: failed_requests.append(
                    {"url": req.url, "error": str(req.failure)})
                    if req.url.startswith(server.base_url) else None)

                cid = _cell_id(cell)
                try:
                    page.goto(f"{server.base_url}/__audit__/login/{cell['scenario']}",
                              wait_until="domcontentloaded", timeout=30000)
                    try:
                        resp = page.goto(f"{server.base_url}/progress-page",
                                         wait_until="domcontentloaded", timeout=30000)
                    except PWTimeoutError:
                        resp = page.goto(f"{server.base_url}/progress-page",
                                         wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_function(SETTLED_JS, timeout=12000)
                    except PWTimeoutError:
                        pass
                    m = page.evaluate(MEASURE_JS)
                    focus = page.evaluate(FOCUS_JS)
                    keyboard = None
                    interactive = (
                        not cell["fail"]
                        and cell["state"] in {
                            "on_track_expanded", "needs_attention_expanded",
                            "mixed", "bounded_has_more", "one_baseline",
                            "missing_weight",
                        }
                        and m.get("item_count")
                    )
                    if interactive:
                        keyboard = {"expanded_after": None, "errors": []}
                        page.evaluate(
                            "() => { const el = document.querySelector('.hist-trigger');"
                            " if (el) el.focus(); }")
                        page.keyboard.press("Enter")
                        try:
                            page.wait_for_function(
                                """() => {
                                  const el = document.querySelector('.hist-trigger');
                                  return !!(el && el.getAttribute('aria-expanded') === 'true');
                                }""",
                                timeout=4000)
                        except PWTimeoutError:
                            keyboard["errors"].append("row did not expand")
                        keyboard["expanded_after"] = page.evaluate(
                            "() => document.querySelector('.hist-trigger')"
                            "?.getAttribute('aria-expanded') || null")
                        m = page.evaluate(MEASURE_JS)

                    status_code = resp.status if resp else None
                    verdict, reasons = _evaluate(cell, m, focus, keyboard)

                    if status_code is not None and status_code >= 500:
                        verdict = "fail"
                        reasons.append(f"document status {status_code}")
                    unexpected = [
                        e for e in server_errors
                        if not (cell["fail"] and e["url"].endswith("/api/progress/history"))
                    ]
                    if unexpected:
                        verdict = "fail"
                        reasons.append("unexpected 5xx: " + ", ".join(
                            f"{e['status']} {e['url'].rsplit('/', 1)[-1]}" for e in unexpected))
                    hard = [f for f in failed_requests
                            if "ERR_ABORTED" not in (f.get("error") or "")]
                    if hard:
                        verdict = "fail"
                        reasons.append("failed requests: " + ", ".join(
                            f["url"].rsplit("/", 1)[-1] for f in hard))
                    own_console = [c for c in console_errors
                                   if not _is_third_party_console_noise(c)]
                    if cell["fail"]:
                        own_console = [c for c in own_console if "500" not in c]
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
                        "focus": focus, "keyboard": keyboard,
                        "measurements": m, "screenshot": shot_rel,
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "id": cid, "state": cell["state"], "viewport": cell["viewport"],
                        "verdict": "blocked", "reasons": [f"{type(exc).__name__}: {exc}"],
                    })
                finally:
                    context.close()
        finally:
            tracking.build_progress_history = real_builder
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "progress-redesign-pr5-history-convergence",
        "engine": "chromium",
        "hermetic": True,
        "audit_day": "2026-07-20",
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
    parser.add_argument(
        "--only", action="append", default=None,
        help="Restrict to state or state:viewport (repeatable)")
    args = parser.parse_args()
    manifest = run(args.output, only=args.only)
    print(json.dumps({
        "totals": manifest["totals"],
        "seed_drift": manifest["seed_drift"],
        "failed_ids": manifest["failed_ids"],
        "blocked_ids": manifest["blocked_ids"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
