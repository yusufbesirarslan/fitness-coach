"""Playwright capture runner for resolved frontend-audit plans."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any

from werkzeug.serving import make_server

from . import SCHEMA_VERSION
from .plan import CaptureSpec


def capture_filename(spec: CaptureSpec) -> str:
    return f"{spec.id}.png"


def browser_settings(viewport: dict[str, int], stress_profile: str | None) -> dict[str, Any]:
    settings: dict[str, Any] = {"viewport": dict(viewport)}
    if stress_profile == "landscape":
        settings["viewport"] = {"width": viewport["height"], "height": viewport["width"]}
    elif stress_profile == "keyboard":
        settings["viewport"] = {"width": viewport["width"], "height": 500}
    elif stress_profile == "reduced-motion":
        settings["reduced_motion"] = "reduce"
    elif stress_profile == "dark-mode":
        settings["color_scheme"] = "dark"
    return settings


def browser_clock_script(fixed_datetime: str) -> str:
    milliseconds = int(datetime.fromisoformat(fixed_datetime).timestamp() * 1000)
    return f"""
(() => {{
  const NativeDate = Date;
  const fixedNow = {milliseconds};
  class AuditDate extends NativeDate {{
    constructor(...args) {{ super(...(args.length ? args : [fixedNow])); }}
    static now() {{ return fixedNow; }}
  }}
  AuditDate.parse = NativeDate.parse;
  AuditDate.UTC = NativeDate.UTC;
  window.Date = AuditDate;
  Date.now = AuditDate.now;
}})();
"""


def _page_checks(page) -> dict[str, Any]:
    return page.evaluate(r"""
() => {
  const root = document.documentElement;
  const width = root.clientWidth;
  const overflow = [...document.querySelectorAll('body *')].flatMap(el => {
    const r = el.getBoundingClientRect();
    if (r.right <= width + 1 && r.left >= -1) return [];
    return [{tag: el.tagName, id: el.id || '', className: String(el.className || '').slice(0, 160), left: r.left, right: r.right}];
  }).slice(0, 50);
  const text = document.body ? document.body.innerText : '';
  const placeholderPattern = /(?:\{(?:xp|bonus)\}|\{\{[^}]+\}\}|\b(?:undefined|NaN)\b)/g;
  const placeholders = [...new Set(text.match(placeholderPattern) || [])];
  const fixedBottom = [...document.querySelectorAll('body *')].flatMap(el => {
    const style = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (style.position !== 'fixed' || r.bottom < innerHeight - 2) return [];
    return [{tag: el.tagName, id: el.id || '', className: String(el.className || '').slice(0, 160), top: r.top, height: r.height}];
  }).slice(0, 30);
  const activeNavigation = [...document.querySelectorAll('[aria-current], .active, .is-active')].map(el => ({
    tag: el.tagName, text: (el.textContent || '').trim().slice(0, 80), ariaCurrent: el.getAttribute('aria-current')
  })).slice(0, 20);
  return {viewport_width: width, scroll_width: root.scrollWidth, overflow, placeholders, fixed_bottom: fixedBottom, active_navigation: activeNavigation};
}
""")


class AuditServer:
    def __init__(self, app):
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _capture_one(browser_type, server: AuditServer, inventory: dict, clocks: dict, spec: CaptureSpec, output_dir: Path) -> dict[str, Any]:
    dimensions = inventory["viewports"][spec.viewport]
    context = browser_type.new_context(**browser_settings(dimensions, spec.stress_profile))
    context.add_init_script(browser_clock_script(clocks[spec.scenario_id]["fixed_current_datetime"]))
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[dict[str, str]] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", lambda request: failed_requests.append({"url": request.url, "error": str(request.failure)}))
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot = screenshot_dir / capture_filename(spec)
    try:
        route_record = next(route for route in inventory["routes"] if route["id"] == spec.route_id)
        if route_record["auth_required"]:
            page.goto(f"{server.base_url}/__audit__/login/{spec.scenario_id}", wait_until="domcontentloaded", timeout=20000)
        response = page.goto(f"{server.base_url}{spec.route}", wait_until="domcontentloaded", timeout=20000)
        if spec.stress_profile == "text-200":
            page.evaluate(r"""
() => {
  const nonceSource = document.querySelector('style[nonce], script[nonce]');
  if (!nonceSource || !nonceSource.nonce) throw new Error('page CSP nonce not found');
  const style = document.createElement('style');
  style.nonce = nonceSource.nonce;
  style.textContent = 'html { font-size: 200% !important; }';
  document.head.appendChild(style);
}
""")
        if spec.stress_profile == "keyboard":
            focusable = page.locator("input:not([type=hidden]), textarea").first
            if focusable.count():
                focusable.focus()
        page.wait_for_timeout(400)
        checks = _page_checks(page)
        page.screenshot(path=str(screenshot), full_page=True)
        status_code = response.status if response else None
        expected_error = (
            route_record.get("audit_only") is True
            and route_record["route"] == "/__audit__/500"
            and status_code == 500
        )
        status = "captured" if expected_error or (status_code is not None and status_code < 500) else "failed"
        return {
            "schema_version": SCHEMA_VERSION,
            "capture_id": spec.id,
            "url": page.url,
            "status": status,
            "status_code": status_code,
            "screenshot": str(screenshot.relative_to(output_dir)).replace("\\", "/"),
            "checks": {
                "console_errors": console_errors,
                "page_errors": page_errors,
                "failed_requests": failed_requests,
                "overflow": checks["overflow"],
                "placeholders": checks["placeholders"],
                "occlusion": checks["fixed_bottom"],
                "active_navigation": checks["active_navigation"],
                "viewport_width": checks["viewport_width"],
                "scroll_width": checks["scroll_width"],
            },
        }
    except Exception as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "capture_id": spec.id,
            "url": page.url,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "checks": {"console_errors": console_errors, "page_errors": page_errors, "failed_requests": failed_requests, "overflow": [], "placeholders": [], "occlusion": []},
        }
    finally:
        context.close()


def _manifest_capture_record(spec: CaptureSpec, result: dict[str, Any], result_path: str) -> dict[str, Any]:
    record = {
        "id": spec.id,
        "route_id": spec.route_id,
        "scenario_id": spec.scenario_id,
        "state": spec.state,
        "viewport": spec.viewport,
        "browser": spec.browser,
        "status": "captured" if result["status"] == "captured" else "blocked",
        "result": result_path,
    }
    for key, value in (("screenshot", result.get("screenshot")), ("reason", result.get("error"))):
        if value is not None:
            record[key] = value
    return record


def run_capture_plan(app, inventory: dict, plan: list[CaptureSpec], output_dir: Path) -> dict[str, Any]:
    """Run app and browsers in this process/environment and write a manifest."""
    from playwright.sync_api import sync_playwright

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    captures = []
    environment = {"browser": "multiple", "supported": True}
    with AuditServer(app) as server, sync_playwright() as playwright:
        for browser_name in ("chromium", "webkit", "firefox"):
            specs = [spec for spec in plan if spec.browser == browser_name]
            if not specs:
                continue
            browser_type = getattr(playwright, browser_name)
            try:
                browser = browser_type.launch(headless=True)
            except Exception as exc:
                for spec in specs:
                    captures.append({
                        "id": spec.id, "route_id": spec.route_id, "scenario_id": spec.scenario_id,
                        "state": spec.state, "viewport": spec.viewport, "browser": spec.browser,
                        "status": "blocked", "reason": f"{type(exc).__name__}: {exc}",
                    })
                continue
            try:
                for spec in specs:
                    result = _capture_one(browser, server, inventory, clocks, spec, output_dir)
                    result_path = output_dir / "results" / f"{spec.id}.json"
                    result_path.parent.mkdir(parents=True, exist_ok=True)
                    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    result_ref = str(result_path.relative_to(output_dir)).replace("\\", "/")
                    captures.append(_manifest_capture_record(spec, result, result_ref))
            finally:
                browser.close()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": output_dir.name,
        "environment": environment,
        "captures": captures,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
