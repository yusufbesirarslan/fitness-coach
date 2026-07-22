"""Frontend initialization boundary for the Adaptive Weekly Program UI (Sprint 6 PR6.1).

The repository has no JavaScript test runner — no `package.json`, and CI runs pytest
only (`.github/workflows/ci.yml`). Rather than pull in a framework for one 40-line
file, this module uses the two mechanisms the suite already relies on:

* **Source guards** (always run) — the convention in `tests/test_i18n.py` and
  `tests/test_pump_check_sharing.py`: read the shipped asset and assert on it. Scoped
  to code lines (comments are stripped first) so a docstring mentioning `fetch` cannot
  fail the build, and a real `fetch(` call cannot pass it.
* **Behavioral execution** under `node` via `subprocess` — the mechanism
  `tests/test_adaptive_plan_context.py` already uses for out-of-process checks — with a
  ~20-line DOM stub. Skipped when `node` is absent so a bare environment still runs the
  guards above; GitHub's `ubuntu-latest` image ships Node, so CI executes them.

    python -m pytest tests/test_weekly_program_ui_js.py -v
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "static" / "weekly_program.js"
SOURCE = SCRIPT_PATH.read_text(encoding="utf-8")

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _code_only(source):
    """Strip block and line comments so the guards below judge executable code, not
    prose. Deliberately simple: the file contains no regex literal or string that can
    confuse it, and a future one would be caught by the round-trip assertions."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"(?m)^\s*//.*$", "", source)
    return re.sub(r"//.*$", "", source, flags=re.MULTILINE)


CODE = _code_only(SOURCE)


# ── the guard's own sanity ──────────────────────────────────────────────────────

def test_comment_stripper_keeps_code_and_drops_prose():
    stripped = _code_only("/* fetch( in a block */\nvar a = 1; // fetch( trailing\nb();")
    assert "fetch(" not in stripped
    assert "var a = 1;" in stripped and "b();" in stripped


def test_source_guards_are_running_against_real_code():
    """If the file were ever emptied or renamed, the "absence" guards below would all
    pass vacuously. Pin that there is real code to guard."""
    assert "initWeeklyProgram" in CODE
    assert "data-weekly-program-mount" in CODE


# ── no data consumption (PR6.1's central client-side promise) ───────────────────

@pytest.mark.parametrize("forbidden", [
    "fetch(", "XMLHttpRequest", "sendBeacon", "EventSource", "WebSocket",
    "/api/training/weekly-program", "/api/training", "weekly-program?",
])
def test_initializer_makes_no_request(forbidden):
    assert forbidden not in CODE


@pytest.mark.parametrize("forbidden", [
    "addEventListener", "setTimeout", "setInterval", "requestAnimationFrame",
    "MutationObserver", "IntersectionObserver",
])
def test_initializer_registers_no_listener_timer_or_poll(forbidden):
    assert forbidden not in CODE


@pytest.mark.parametrize("forbidden", ["innerHTML", "outerHTML", "insertAdjacentHTML",
                                       "document.write", "eval("])
def test_initializer_injects_no_html(forbidden):
    assert forbidden not in CODE


# ── no second planning authority in the browser ─────────────────────────────────

@pytest.mark.parametrize("forbidden", [
    "AI_ADAPTIVE_PLAN_CONTEXT",           # coach flag — independent rollout
    "week_focus", "volume_action", "intensity_action", "volume_delta_pct",
    "baseline_weekly_volume", "target_weekly_volume", "reason_codes",
    "explanation_keys", "deload", "plateau", "overload", "1RM",
])
def test_initializer_contains_no_planning_vocabulary(forbidden):
    assert forbidden not in CODE


def test_initializer_contains_no_thresholds_or_target_arithmetic():
    """AdaptivePlan is the single planning authority. No numeric literal beyond the
    '1' initialization marker may live here — a threshold, a 0.05 volume step, a
    7-day window or a `* (1 + delta)` target would be a second planner."""
    numbers = set(re.findall(r"(?<![\w.'\"])\d+(?:\.\d+)?", CODE))
    assert numbers <= {"1"}, numbers
    assert not re.search(r"[-+*/%]\s*\d", CODE)
    assert "Date(" not in CODE and "getDay" not in CODE   # no date-window arithmetic


def test_initializer_declares_no_placeholder_payload():
    """No mock/stub recommendation object that could drift from the backend contract."""
    assert "JSON.parse" not in CODE
    assert "JSON.stringify" not in CODE
    assert not re.search(r"=\s*\{\s*\w+\s*:", CODE.replace(
        "window.FitXWeeklyProgram = { init: initWeeklyProgram }", ""))


# ── behavior under node ─────────────────────────────────────────────────────────

_HARNESS = r"""
// Minimal DOM stub. Any network or listener API is a trap that fails loudly.
const calls = { fetch: 0, xhr: 0, listeners: 0 };
function makeMount() {
  return { dataset: {}, tagName: 'SECTION' };
}
const mount = %(with_mount)s ? makeMount() : null;
global.document = {
  querySelector: (sel) => (sel === '[data-weekly-program-mount]' ? mount : null),
  addEventListener: () => { calls.listeners++; },
};
global.window = { addEventListener: () => { calls.listeners++; } };
global.fetch = () => { calls.fetch++; throw new Error('fetch called'); };
global.XMLHttpRequest = function () { calls.xhr++; throw new Error('XHR called'); };

require(%(script)s);

const api = global.window.FitXWeeklyProgram;
const result = {
  exposesInit: !!(api && typeof api.init === 'function'),
  autoRan: mount ? mount.dataset.weeklyProgramInitialized : null,
  secondCall: api ? api.init(global.document) : null,
  thirdCall: api ? api.init(global.document) : null,
  markerAfterRepeats: mount ? mount.dataset.weeklyProgramInitialized : null,
  datasetKeys: mount ? Object.keys(mount.dataset) : [],
  calls,
};
console.log(JSON.stringify(result));
"""


def _run_node(with_mount):
    harness = _HARNESS % {"with_mount": "true" if with_mount else "false",
                          "script": json.dumps(str(SCRIPT_PATH))}
    completed = subprocess.run([NODE, "-e", harness], capture_output=True, text=True,
                               check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@requires_node
def test_initializer_no_ops_when_the_shell_is_absent():
    """Flag OFF: the script is not even served — but if it ever is (cache, a shared
    bundle, a future page), it must do nothing rather than throw."""
    result = _run_node(with_mount=False)
    assert result["exposesInit"] is True
    assert result["secondCall"] is False
    assert result["calls"] == {"fetch": 0, "xhr": 0, "listeners": 0}


@requires_node
def test_initializer_runs_exactly_once_when_the_shell_is_present():
    result = _run_node(with_mount=True)
    assert result["autoRan"] == "1"            # ran on load
    assert result["secondCall"] is False       # repeat initialization is a no-op
    assert result["thirdCall"] is False
    assert result["markerAfterRepeats"] == "1"
    assert result["datasetKeys"] == ["weeklyProgramInitialized"]


@requires_node
def test_initializer_calls_no_network_api_and_adds_no_listener():
    assert _run_node(with_mount=True)["calls"] == {"fetch": 0, "xhr": 0, "listeners": 0}


@requires_node
def test_initializer_exposes_a_stable_extension_point_for_pr62():
    assert _run_node(with_mount=True)["exposesInit"] is True


@requires_node
def test_script_is_syntactically_valid():
    completed = subprocess.run([NODE, "--check", str(SCRIPT_PATH)],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


# ── template wiring ─────────────────────────────────────────────────────────────

def test_script_is_only_referenced_behind_the_flag():
    """The asset must never be loaded unconditionally: OFF ships zero bytes of it."""
    template = (Path(__file__).resolve().parents[1] / "templates" / "training.html"
                ).read_text(encoding="utf-8")
    assert template.count("/static/weekly_program.js") == 1
    lines = template.splitlines()
    at = next(i for i, line in enumerate(lines) if "/static/weekly_program.js" in line)
    assert lines[at - 1].strip() == "{%- if weekly_program_ui_enabled %}"
    assert lines[at + 1].strip() == "{%- endif %}"


def test_script_is_not_referenced_from_any_other_template_or_asset():
    root = Path(__file__).resolve().parents[1]
    referrers = [path for path in list((root / "templates").rglob("*.html"))
                 + list((root / "static").glob("*.js"))
                 if path.name != "weekly_program.js"
                 and "weekly_program.js" in path.read_text(encoding="utf-8")]
    assert referrers == [root / "templates" / "training.html"]


def test_no_css_rule_targets_the_shell_so_it_adds_no_layout():
    """PR6.1 adds no stylesheet, and the shell must not be caught by an existing rule
    either — no bare `section` selector, and nothing matching its id or data attribute.
    An unstyled empty `<section>` is a zero-height block, which is what makes "ON adds
    no empty vertical space" true. PR6.2 owns the card's CSS."""
    css_dir = Path(__file__).resolve().parents[1] / "static"
    assert not (css_dir / "weekly_program.css").exists()   # no file "for later"
    for sheet in css_dir.glob("*.css"):
        text = sheet.read_text(encoding="utf-8")
        body = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        assert "weekly-program" not in body, sheet.name
        assert "weekly_program" not in body, sheet.name
        # a bare `section { ... }` / `section,` / `main section` rule would style it
        assert not re.search(r"(?m)(^|[,{}>+~]|\s)section\s*(\{|,)", body), sheet.name


def test_asset_stays_small():
    """PR6.1 is a boundary, not a feature; guard against it quietly growing into one
    before PR6.2 reviews the client contract."""
    assert SCRIPT_PATH.stat().st_size < 4096
