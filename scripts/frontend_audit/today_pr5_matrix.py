"""UX-2 PR5 Today guidance browser matrix.

Runs against the hermetic frontend-audit app and its synthetic SQLite data.
The matrix covers every requested viewport and locale for the three primary
guidance choices and two representative no-primary states.  The 390px
scheduled cells additionally fail one supporting nutrition read to prove that
late, partial data cannot replace or duplicate the server-rendered decision.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
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

STATE_SCENARIOS = {
    "in_progress": "progress-history",
    "scheduled_not_started": "active-workout",
    "no_plan": "social-empty",
    "rest_day": "active-rest-day",
    "completed": "completed-workout",
}
PRIMARY_STATES = {"in_progress", "scheduled_not_started", "no_plan"}


MEASURE_JS = r"""
() => {
  const root = document.documentElement;
  const mount = document.getElementById('today-page');
  const primary = [...document.querySelectorAll('[data-today-primary]')];
  const secondary = [...document.querySelectorAll('.today-secondary-link')];
  const bodyText = document.body ? document.body.innerText : '';
  const ids = primary.map((el) => el.id).filter(Boolean);
  return {
    doc_horizontal_overflow: root.scrollWidth > root.clientWidth + 1,
    today_mount_overflow: !!mount && mount.scrollWidth > mount.clientWidth + 1,
    today_state: mount ? mount.getAttribute('data-today-state') : null,
    primary_action_count: primary.length,
    primary_action_label: primary[0] ? primary[0].textContent.trim() : null,
    primary_action_clipped: primary.some((el) => el.scrollWidth > el.clientWidth + 1),
    secondary_action_count: secondary.length,
    raw_key_leak: [...new Set(bodyText.match(/\b(?:today|nav|progress)\.[a-z0-9_.]+/g) || [])],
    duplicate_primary_ids: ids.length !== new Set(ids).size,
    summary_error_visible: !!document.querySelector('#today-status-error:not([hidden])'),
    html_lang: root.getAttribute('lang'),
  };
}
"""


def _cells() -> list[dict]:
    return [
        {
            "state": state,
            "scenario": scenario,
            "viewport": viewport,
            "locale": locale,
            "want_primary": state in PRIMARY_STATES,
            "partial_data": (
                state == "scheduled_not_started" and viewport == "390"
            ),
        }
        for state, scenario in STATE_SCENARIOS.items()
        for viewport in VIEWPORTS
        for locale in ("en", "tr")
    ]


def _evaluate(cell: dict, measured: dict) -> tuple[str, list[str]]:
    reasons = []
    if measured["doc_horizontal_overflow"]:
        reasons.append("document horizontal overflow")
    if measured["today_mount_overflow"]:
        reasons.append("today-shell horizontal overflow")
    if measured["today_state"] != cell["state"]:
        reasons.append(
            f"state={measured['today_state']} (want {cell['state']})")
    wanted = 1 if cell["want_primary"] else 0
    if measured["primary_action_count"] != wanted:
        reasons.append(
            f"primary-action count={measured['primary_action_count']} (want {wanted})")
    if measured["primary_action_clipped"]:
        reasons.append("primary action label clipped")
    if measured["raw_key_leak"]:
        reasons.append(
            f"raw localization keys leaked: {measured['raw_key_leak']}")
    if measured["duplicate_primary_ids"]:
        reasons.append("duplicate primary action ids")
    if not cell["want_primary"] and measured["secondary_action_count"] < 1:
        reasons.append("no-primary state is a dead end")
    if measured["html_lang"] != cell["locale"]:
        reasons.append(
            f"html lang={measured['html_lang']} (want {cell['locale']})")
    if cell.get("partial_data") and not measured.get("summary_error_visible"):
        reasons.append("partial nutrition failure was not surfaced")
    return ("pass" if not reasons else "fail"), reasons


def _unexpected_console_errors(cell: dict, messages: list[str]) -> list[str]:
    """Exclude only Chromium's report of our deliberate partial-read 503."""
    if not cell.get("partial_data"):
        return messages
    return [message for message in messages if not (
        "Failed to load resource" in message and "status of 503" in message
    )]


def _prepare_resume_state(app, clocks) -> None:
    from app.extensions import db
    from app.models import User
    from app.services.workout_session.service import start_session
    from app.timeutil import audit_clock

    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    with app.app_context(), audit_clock(datetime.fromisoformat(
            clocks["progress-history"]["fixed_current_datetime"])):
        user = User.query.filter_by(username="audit-progress-history").one()
        result = start_session(user.id)
        if result.session is None:
            raise RuntimeError(f"could not seed active session: {result.outcome}")
        db.session.expire_all()


def _set_language(app, scenario: str, locale: str) -> None:
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = User.query.filter_by(username=f"audit-{scenario}").one()
        user.language = locale
        db.session.commit()


def _block_external(route, request, base: str) -> None:
    if request.url.startswith(base):
        route.continue_()
    else:
        route.fulfill(status=204, body="", content_type="text/plain")


def run(output_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    db_path = ROOT / "artifacts" / "ui-audit" / "today_pr5_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    _prepare_resume_state(app, clocks)

    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for cell in _cells():
                _set_language(app, cell["scenario"], cell["locale"])
                dims = VIEWPORTS[cell["viewport"]]
                context = browser.new_context(viewport=dict(dims))
                context.add_init_script(browser_clock_script(
                    clocks[cell["scenario"]]["fixed_current_datetime"]))
                context.route("**/*", lambda route, request: _block_external(
                    route, request, server.base_url))
                if cell["partial_data"]:
                    context.route("**/meal-log/today", lambda route: route.fulfill(
                        status=503, body='{"error":"audit partial read"}',
                        content_type="application/json"))
                page = context.new_page()
                console_errors, page_errors, server_errors = [], [], []
                page.on("console", lambda message: console_errors.append(message.text)
                        if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on("response", lambda response: server_errors.append({
                    "path": urlparse(response.url).path, "status": response.status,
                }) if (response.url.startswith(server.base_url)
                       and response.status >= 500
                       and not (cell["partial_data"]
                                and urlparse(response.url).path == "/meal-log/today"))
                        else None)
                cell_id = (
                    f"{cell['state']}__{cell['viewport']}__{cell['locale']}"
                    f"{'__partial' if cell['partial_data'] else ''}")
                try:
                    response = page.goto(
                        f"{server.base_url}/__audit__/login/{cell['scenario']}",
                        wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(700)
                    measured = page.evaluate(MEASURE_JS)
                    verdict, reasons = _evaluate(cell, measured)
                    if response and response.status >= 500:
                        reasons.append(f"document status {response.status}")
                    unexpected_console = _unexpected_console_errors(
                        cell, console_errors)
                    if unexpected_console:
                        reasons.append(f"console errors: {unexpected_console}")
                    if page_errors:
                        reasons.append(f"page errors: {page_errors}")
                    if server_errors:
                        reasons.append(f"unexpected server errors: {server_errors}")
                    verdict = "pass" if not reasons else "fail"
                    shot = shots_dir / f"{cell_id}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    results.append({
                        "id": cell_id, **cell, "viewport_px": dims,
                        "verdict": verdict, "reasons": reasons,
                        "measurements": measured,
                        "console_errors": console_errors,
                        "page_errors": page_errors,
                        "server_errors": server_errors,
                        "screenshot": f"screenshots/{cell_id}.png",
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "id": cell_id, **cell, "verdict": "blocked",
                        "reasons": [f"{type(exc).__name__}: {exc}"],
                    })
                finally:
                    context.close()
        finally:
            browser.close()

    failed = [row["id"] for row in results if row["verdict"] == "fail"]
    blocked = [row["id"] for row in results if row["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "ux2-pr5-today-guidance-orchestration",
        "engine": "chromium",
        "hermetic": True,
        "seed_summary": seed_summary,
        "totals": {
            "cells": len(results),
            "passed": sum(row["verdict"] == "pass" for row in results),
            "failed": len(failed), "blocked": len(blocked),
        },
        "failed_ids": failed, "blocked_ids": blocked, "cells": results,
    }
    (output_dir / "validation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.frontend_audit.today_pr5_matrix")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "docs" / "frontend-readiness" / "ux2-pr5")
    args = parser.parse_args()
    manifest = run(args.output)
    totals = manifest["totals"]
    print(
        f"cells={totals['cells']} passed={totals['passed']} "
        f"failed={totals['failed']} blocked={totals['blocked']}")
    for row in manifest["cells"]:
        if row["verdict"] != "pass":
            print("  !", row["id"], row["reasons"])
    print("manifest:", args.output / "validation-manifest.json")


if __name__ == "__main__":
    main()
