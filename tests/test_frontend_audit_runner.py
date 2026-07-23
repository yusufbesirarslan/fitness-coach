from datetime import datetime
from pathlib import Path
import warnings


class _FakePage:
    def __init__(self, status=200):
        self.status = status
        self.url = "about:blank"
        self.zoom_uses_nonce = False

    def on(self, *_args):
        pass

    def goto(self, url, **_kwargs):
        self.url = url
        return type("Response", (), {"status": self.status})()

    def add_style_tag(self, **_kwargs):
        raise AssertionError("text zoom must not inject an un-nonced style tag")

    def evaluate(self, script):
        if "font-size: 200%" in script:
            self.zoom_uses_nonce = "querySelector" in script and ".nonce" in script
            return None
        return {"viewport_width": 390, "scroll_width": 390, "overflow": [],
                "placeholders": [], "fixed_bottom": [], "active_navigation": []}

    def wait_for_timeout(self, _milliseconds):
        pass

    def screenshot(self, path, **_kwargs):
        Path(path).touch()


class _FakeContext:
    def __init__(self, page):
        self.page = page

    def add_init_script(self, _script):
        pass

    def new_page(self):
        return self.page

    def close(self):
        pass


class _FakeBrowserType:
    def __init__(self, page):
        self.page = page

    def new_context(self, **_settings):
        return _FakeContext(self.page)


class _FakeServer:
    base_url = "http://127.0.0.1:5055"


def _capture_inputs(route_id="landing", route="/welcome", status=200, stress_profile=None):
    from scripts.frontend_audit.plan import CaptureSpec

    page = _FakePage(status)
    inventory = {
        "viewports": {"mobile-390": {"width": 390, "height": 844}},
        "routes": [{"id": route_id, "route": route, "auth_required": False,
                    "audit_only": route_id.startswith("error-")}],
    }
    clocks = {"anonymous": {"fixed_current_datetime": "2026-07-20T09:30:00+03:00"}}
    spec = CaptureSpec(route_id, route, "anonymous", "default", "mobile-390", "chromium", stress_profile)
    return page, inventory, clocks, spec


def test_capture_filename_is_deterministic_and_safe():
    from scripts.frontend_audit.plan import CaptureSpec
    from scripts.frontend_audit.runner import capture_filename

    spec = CaptureSpec("nutrition", "/nutrition", "active-workout", "food-search", "mobile-390", "chromium", "keyboard")
    assert capture_filename(spec) == "nutrition--active-workout--food-search--mobile-390--chromium--keyboard.png"


def test_stress_profiles_adjust_only_relevant_browser_settings():
    from scripts.frontend_audit.runner import browser_settings

    viewport = {"width": 390, "height": 844}
    assert browser_settings(viewport, None)["viewport"] == viewport
    assert browser_settings(viewport, "landscape")["viewport"] == {"width": 844, "height": 390}
    assert browser_settings(viewport, "keyboard")["viewport"] == {"width": 390, "height": 500}
    assert browser_settings(viewport, "reduced-motion")["reduced_motion"] == "reduce"
    assert browser_settings(viewport, "dark-mode")["color_scheme"] == "dark"


def test_text_200_zoom_uses_the_page_csp_nonce(tmp_path):
    from scripts.frontend_audit.runner import _capture_one

    page, inventory, clocks, spec = _capture_inputs(stress_profile="text-200")
    result = _capture_one(_FakeBrowserType(page), _FakeServer(), inventory, clocks, spec, tmp_path)

    assert result["status"] == "captured"
    assert page.zoom_uses_nonce is True


def test_only_intentional_audit_500_is_captured(tmp_path):
    from scripts.frontend_audit.runner import _capture_one

    page, inventory, clocks, spec = _capture_inputs("error-500", "/__audit__/500", 500)
    expected = _capture_one(_FakeBrowserType(page), _FakeServer(), inventory, clocks, spec, tmp_path / "expected")

    page, inventory, clocks, spec = _capture_inputs("landing", "/welcome", 500)
    unexpected = _capture_one(_FakeBrowserType(page), _FakeServer(), inventory, clocks, spec, tmp_path / "unexpected")

    assert expected["status"] == "captured"
    assert unexpected["status"] == "failed"


def test_captured_manifest_record_omits_absent_reason_and_validates(tmp_path):
    from scripts.frontend_audit import runner
    from scripts.frontend_audit.plan import CaptureSpec
    from scripts.frontend_audit.validation import validate_manifest

    build_record = getattr(runner, "_manifest_capture_record", None)
    assert callable(build_record), "runner must expose manifest record serialization"

    screenshot = tmp_path / "screenshots" / "landing.png"
    screenshot.parent.mkdir()
    screenshot.touch()
    spec = CaptureSpec("landing", "/welcome", "anonymous", "default", "mobile-390", "chromium")
    record = build_record(
        spec,
        {"status": "captured", "screenshot": "screenshots/landing.png"},
        "results/landing.json",
    )
    manifest = {
        "schema_version": "1.0.0",
        "run_id": "test-run",
        "environment": {"browser": "multiple", "supported": True},
        "captures": [record],
    }

    assert "reason" not in record
    validate_manifest(manifest, evidence_root=tmp_path)


def test_browser_clock_script_freezes_date_and_timezone():
    from scripts.frontend_audit.runner import browser_clock_script

    script = browser_clock_script("2026-07-20T09:30:00+03:00")
    millis = int(datetime.fromisoformat("2026-07-20T09:30:00+03:00").timestamp() * 1000)
    assert str(millis) in script
    assert "class AuditDate" in script
    assert "Date.now" in script


def test_cli_dry_run_prints_plan_without_importing_playwright(capsys, tmp_path):
    from scripts.frontend_audit.__main__ import main

    result = main(["capture", "--tier", "smoke", "--dry-run", "--output", str(tmp_path)])
    assert result == 0
    output = capsys.readouterr().out
    assert "resolved captures: 2" in output
    assert "landing" in output
    assert "dashboard" in output


def test_preflight_record_has_required_machine_readable_fields():
    from scripts.frontend_audit.preflight import base_preflight_record

    record = base_preflight_record()
    assert record["schema_version"] == "1.0.0"
    assert {"python", "operating_system", "playwright", "chromium", "loopback", "screenshot", "shutdown"} <= set(record)


def test_runner_source_introduces_no_syntax_warnings():
    source_path = Path(__file__).resolve().parents[1] / "scripts" / "frontend_audit" / "runner.py"
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
