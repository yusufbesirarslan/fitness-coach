"""Playwright environment classification and mandatory Chromium smoke test."""

from __future__ import annotations

import platform
import sys
from importlib import metadata
from pathlib import Path


def classify_environment(system: str | None = None, release: str | None = None, version: str | None = None) -> dict:
    system = system or platform.system()
    release = release or platform.release()
    version = version or platform.version()
    if system == "Windows" and not str(release).startswith("11"):
        return {
            "system": system,
            "release": release,
            "version": version,
            "supported": False,
            "reason": "Playwright documents native Windows 11+ support",
        }
    if system in {"Linux", "Windows", "Darwin"}:
        return {
            "system": system,
            "release": release,
            "version": version,
            "supported": True,
            "reason": "supported operating-system family",
        }
    return {
        "system": system,
        "release": release,
        "version": version,
        "supported": False,
        "reason": "unsupported operating-system family",
    }


def environment_record() -> dict:
    record = classify_environment()
    record["python"] = platform.python_version()
    record["executable"] = sys.executable
    return record


def base_preflight_record() -> dict:
    return {
        "schema_version": "1.0.0",
        "python": {"version": platform.python_version(), "executable": sys.executable, "ok": False},
        "operating_system": classify_environment(),
        "playwright": {"version": None, "installed": False},
        "chromium": {"installed": False, "launched": False},
        "loopback": {"url": None, "navigated": False},
        "screenshot": {"path": None, "created": False},
        "shutdown": {"clean": False},
        "success": False,
        "errors": [],
    }


def run_preflight(output_dir: Path) -> dict:
    """Launch Chromium against a temporary loopback page and capture a PNG."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    record = base_preflight_record()
    record["python"]["ok"] = sys.version_info >= (3, 10)
    try:
        record["playwright"]["version"] = metadata.version("playwright")
        record["playwright"]["installed"] = True
    except metadata.PackageNotFoundError:
        record["errors"].append("playwright package is not installed")
        return record

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><title>AxisAI audit preflight</title><h1>ok</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    record["loopback"]["url"] = url
    browser = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            record["chromium"]["installed"] = executable.is_file()
            browser = playwright.chromium.launch(headless=True)
            record["chromium"]["launched"] = True
            page = browser.new_page(viewport={"width": 390, "height": 844})
            response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
            record["loopback"]["navigated"] = bool(response and response.ok)
            screenshot = output_dir / "chromium-loopback-390.png"
            page.screenshot(path=str(screenshot), full_page=True)
            record["screenshot"] = {"path": str(screenshot), "created": screenshot.is_file() and screenshot.stat().st_size > 0}
            browser.close()
            browser = None
            record["shutdown"]["clean"] = True
    except Exception as exc:
        record["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if browser is not None:
            browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    record["success"] = (
        record["python"]["ok"]
        and record["operating_system"]["supported"]
        and record["playwright"]["installed"]
        and record["chromium"]["installed"]
        and record["chromium"]["launched"]
        and record["loopback"]["navigated"]
        and record["screenshot"]["created"]
        and record["shutdown"]["clean"]
    )
    return record
