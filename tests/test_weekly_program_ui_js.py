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
    "XMLHttpRequest", "sendBeacon", "EventSource", "WebSocket",
    "weekly-program?", "method: 'POST'", "method: 'PUT'", "method: 'DELETE'",
])
def test_initializer_uses_no_alternate_or_writing_transport(forbidden):
    assert forbidden not in CODE


@pytest.mark.parametrize("forbidden", [
    "setTimeout", "setInterval", "requestAnimationFrame",
    "MutationObserver", "IntersectionObserver",
])
def test_initializer_registers_no_timer_poll_or_observer(forbidden):
    assert forbidden not in CODE


@pytest.mark.parametrize("forbidden", ["innerHTML", "outerHTML", "insertAdjacentHTML",
                                       "document.write", "eval("])
def test_initializer_injects_no_html(forbidden):
    assert forbidden not in CODE


# ── no second planning authority in the browser ─────────────────────────────────

@pytest.mark.parametrize("forbidden", [
    "AI_ADAPTIVE_PLAN_CONTEXT",           # coach flag — independent rollout
    "1RM", "VOLUME_INCREASE_STEP", "DELOAD_VOLUME_CUT",
])
def test_initializer_contains_no_client_planning_authority(forbidden):
    assert forbidden not in CODE


def test_initializer_contains_no_thresholds_or_target_arithmetic():
    """AdaptivePlan is the single planning authority. No numeric literal beyond the
    '1' initialization marker may live here — a threshold, a 0.05 volume step, a
    7-day window or a `* (1 + delta)` target would be a second planner."""
    assert "0.05" not in CODE and "0.40" not in CODE
    assert not re.search(r"baseline_weekly_volume\s*[*+/%-]", CODE)
    assert "getDay" not in CODE and "setDate" not in CODE
    assert "seven" not in CODE.lower() and "7-day" not in CODE.lower()


def test_initializer_declares_no_placeholder_payload():
    """No mock/stub recommendation object that could drift from the backend contract."""
    assert "JSON.stringify" not in CODE
    assert "mockRecommendation" not in CODE and "fallbackRecommendation" not in CODE


# ── behavior under node ─────────────────────────────────────────────────────────

_HARNESS = r"""
const calls = { fetch: 0, xhr: 0, listeners: 0, request: null };
function makeElement(tag) {
  return {
    tagName: tag.toUpperCase(), children: [], attributes: {}, className: "",
    classList: { add: function () {} },
    setAttribute: function (key, value) { this.attributes[key] = value; },
    append: function (...nodes) { this.children.push(...nodes); },
    replaceChildren: function (...nodes) { this.children = nodes; },
    addEventListener: function () { calls.listeners++; },
    textContent: "",
  };
}
const copyNode = { textContent: JSON.stringify({
  "training.weekly_program": "Weekly Program",
  "weekly_program.loading": "Loading weekly program",
}) };
function makeMount() {
  const mount = makeElement("section");
  mount.dataset = {};
  mount.hiddenRemoved = false;
  mount.querySelector = (sel) => sel === "[data-weekly-program-copy]" ? copyNode : null;
  mount.removeAttribute = () => { mount.hiddenRemoved = true; };
  return mount;
}
const mount = %(with_mount)s ? makeMount() : null;
global.document = {
  querySelector: (sel) => (sel === "[data-weekly-program-mount]" ? mount : null),
  createElement: makeElement,
};
global.window = { LOCALE: "en" };
global.fetch = (path, options) => {
  calls.fetch++; calls.request = { path, options };
  return new Promise(() => {});
};
global.XMLHttpRequest = function () { calls.xhr++; throw new Error("XHR called"); };

require(%(script)s);

const api = global.window.FitXWeeklyProgram;
const result = {
  exposesInit: !!(api && typeof api.init === "function"),
  autoRan: mount ? mount.dataset.weeklyProgramInitialized : null,
  secondCall: api ? api.init(global.document) : null,
  thirdCall: api ? api.init(global.document) : null,
  markerAfterRepeats: mount ? mount.dataset.weeklyProgramInitialized : null,
  datasetKeys: mount ? Object.keys(mount.dataset) : [],
  state: mount ? mount.dataset.weeklyProgramState : null,
  hiddenRemoved: mount ? mount.hiddenRemoved : null,
  childCount: mount ? mount.children.length : null,
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
    assert result["calls"]["fetch"] == result["calls"]["xhr"] == 0
    assert result["calls"]["request"] is None


@requires_node
def test_initializer_runs_exactly_once_when_the_shell_is_present():
    result = _run_node(with_mount=True)
    assert result["autoRan"] == "1"            # ran on load
    assert result["secondCall"] is False       # repeat initialization is a no-op
    assert result["thirdCall"] is False
    assert result["markerAfterRepeats"] == "1"
    assert result["datasetKeys"] == ["weeklyProgramInitialized", "weeklyProgramState"]


@requires_node
def test_initializer_renders_loading_and_requests_exactly_once():
    result = _run_node(with_mount=True)
    assert result["state"] == "loading"
    assert result["hiddenRemoved"] is True
    assert result["childCount"] == 2
    assert result["calls"]["fetch"] == 1
    assert result["calls"]["xhr"] == result["calls"]["listeners"] == 0
    assert result["calls"]["request"]["path"] == "/api/training/weekly-program"
    assert result["calls"]["request"]["options"] == {
        "method": "GET", "headers": {"Accept": "application/json"}}


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


def test_css_is_owned_by_training_stylesheet_and_uses_scoped_classes():
    """PR6.1 adds no stylesheet, and the shell must not be caught by an existing rule
    either — no bare `section` selector, and nothing matching its id or data attribute.
    An unstyled empty `<section>` is a zero-height block, which is what makes "ON adds
    no empty vertical space" true. PR6.2 owns the card's CSS."""
    css_dir = Path(__file__).resolve().parents[1] / "static"
    assert not (css_dir / "weekly_program.css").exists()   # no file "for later"
    for sheet in css_dir.glob("*.css"):
        text = sheet.read_text(encoding="utf-8")
        body = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        if sheet.name == "training.css":
            assert ".weekly-program-" in body
        else:
            assert "weekly-program" not in body, sheet.name
        assert "weekly_program" not in body, sheet.name
        # a bare `section { ... }` / `section,` / `main section` rule would style it
        assert not re.search(r"(?m)(^|[,{}>+~]|\s)section\s*(\{|,)", body), sheet.name


def test_asset_stays_small():
    """PR6.1 is a boundary, not a feature; guard against it quietly growing into one
    before PR6.2 reviews the client contract."""
    assert SCRIPT_PATH.stat().st_size < 16384


# PR6.2 client contract guards. Behavioral scenarios below continue to execute the
# shipped file under Node; these source checks pin security and ownership boundaries.
def test_client_uses_the_single_read_only_endpoint_with_json_accept():
    assert "fetch(ENDPOINT" in CODE
    assert "var ENDPOINT = '/api/training/weekly-program';" in CODE
    assert "method: 'GET'" in CODE
    assert "'Accept': 'application/json'" in CODE
    assert "?" not in re.search(r"var ENDPOINT = ([^;]+)", CODE).group(1)


@pytest.mark.parametrize("state", [
    "idle", "loading", "populated", "insufficient_data",
    "missing_baseline", "error", "malformed",
])
def test_client_declares_every_state(state):
    assert "'%s'" % state in CODE


@pytest.mark.parametrize("value", [
    "insufficient_data", "build_consistency", "deload", "maintenance",
    "overload", "steady", "increase", "hold", "decrease", "progress",
    "insufficient_history", "inconsistent_training", "deload_due",
    "plateau_detected", "progressing", "steady_state",
    "volume_trend_down", "strength_trend_down",
])
def test_client_uses_only_backend_contract_vocabulary(value):
    assert "'%s'" % value in CODE


def test_client_has_retry_concurrency_and_stale_response_guards():
    assert "activeRequest" in CODE
    assert "requestGeneration" in CODE
    assert "generation !== requestGeneration" in CODE
    assert "replaceChildren" in CODE
    assert "addEventListener('click'" in CODE


_CASE_HARNESS = r"""
const config = %(config)s;
const calls = {fetch: 0};
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {};
    this.dataset = {}; this.className = ""; this._text = ""; this.handlers = {};
    this.classList = {add: (...names) => {
      this.className = [this.className, ...names].filter(Boolean).join(" ");
    }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent || "").join("");
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  removeAttribute(key) { delete this.attributes[key]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  click() { if (this.handlers.click) this.handlers.click(); }
  querySelector(selector) {
    if (selector === "[data-weekly-program-copy]") return this.copyNode || null;
    const className = selector.startsWith(".") ? selector.slice(1) : null;
    for (const child of this.children) {
      if (className && (child.className || "").split(" ").includes(className)) return child;
      if (child.querySelector) {
        const found = child.querySelector(selector); if (found) return found;
      }
    }
    return null;
  }
}
const copy = {
  "training.weekly_program": "Weekly Program",
  "weekly_program.loading": "Loading",
  "weekly_program.metric.observed": "Observed",
  "weekly_program.metric.target": "Target",
  "weekly_program.metric.kg": "kg",
  "weekly_program.state.insufficient_data": "Need data",
  "weekly_program.state.missing_baseline": "Missing baseline",
  "weekly_program.state.error": "Load error",
  "weekly_program.state.malformed_response": "Malformed data",
  "weekly_program.state.retry": "Retry"
};
for (const focus of ["insufficient_data","build_consistency","deload","maintenance","overload","steady"]) {
  copy["weekly_program.focus." + focus + ".title"] = "focus:" + focus;
  copy["weekly_program.focus." + focus + ".description"] = "desc:" + focus;
}
for (const action of ["increase","hold","decrease"]) {
  copy["weekly_program.volume_action." + action] = "volume:" + action;
}
for (const action of ["progress","hold","deload"]) {
  copy["weekly_program.intensity_action." + action] = "intensity:" + action;
}
for (const reason of ["insufficient_history","inconsistent_training","deload_due",
  "plateau_detected","progressing","steady_state","volume_trend_down","strength_trend_down"]) {
  copy["weekly_program.reason." + reason] = "reason:" + reason;
}
const mount = new Element("section");
mount.attributes["aria-hidden"] = "true";
mount.copyNode = {textContent: JSON.stringify(copy)};
global.document = {
  querySelector: (selector) => selector === "[data-weekly-program-mount]" ? mount : null,
  createElement: (tag) => new Element(tag)
};
global.window = {LOCALE: config.locale || "en"};
global.fetch = (path, options) => {
  const spec = config.responses[calls.fetch++];
  calls.last = {path, options};
  if (spec.kind === "network") return Promise.reject(new Error("network"));
  return Promise.resolve({
    ok: spec.kind !== "status",
    redirected: spec.kind === "redirect",
    json: () => spec.kind === "nonjson"
      ? Promise.reject(new SyntaxError("html"))
      : Promise.resolve(spec.payload)
  });
};
require(%(script)s);
function settle() {
  return new Promise((resolve) => setImmediate(resolve))
    .then(() => new Promise((resolve) => setImmediate(resolve)));
}
(async () => {
  await settle();
  const first = {
    state: mount.dataset.weeklyProgramState,
    text: mount.textContent,
    role: (mount.children[1] && mount.children[1].attributes.role) || null,
    fetches: calls.fetch
  };
  let retry = null;
  if (config.retry) {
    const button = mount.querySelector(".weekly-program-retry");
    button.click();
    button.click();
    retry = {immediateState: mount.dataset.weeklyProgramState, fetches: calls.fetch,
             text: mount.textContent};
    await settle();
    retry.finalState = mount.dataset.weeklyProgramState;
    retry.finalText = mount.textContent;
  }
  console.log(JSON.stringify({first, retry, fetches: calls.fetch, request: calls.last}));
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _payload(**changes):
    payload = {
        "weeks": 4,
        "has_data": True,
        "week_focus": "overload",
        "volume_action": "increase",
        "intensity_action": "progress",
        "volume_delta_pct": 0.05,
        "overload_ready": True,
        "maintenance_recommended": False,
        "baseline_week_start": "2026-07-13",
        "baseline_weekly_volume": 1234.56,
        "target_weekly_volume": 1296.29,
        "reason_codes": ["progressing", "volume_trend_down"],
        "explanation_keys": [
            "weekly_program.focus.overload",
            "weekly_program.reason.progressing",
            "weekly_program.reason.volume_trend_down",
        ],
        "unsupported": ["session_frequency"],
    }
    payload.update(changes)
    return payload


def _run_case(responses, *, locale="en", retry=False):
    config = json.dumps({"responses": responses, "locale": locale, "retry": retry})
    harness = _CASE_HARNESS % {
        "config": config,
        "script": json.dumps(str(SCRIPT_PATH)),
    }
    completed = subprocess.run(
        [NODE, "-e", harness], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(("focus", "volume", "intensity"), [
    ("insufficient_data", "hold", "hold"),
    ("build_consistency", "hold", "hold"),
    ("deload", "decrease", "deload"),
    ("maintenance", "hold", "hold"),
    ("overload", "increase", "progress"),
    ("steady", "hold", "hold"),
])
@requires_node
def test_populated_renders_every_canonical_focus_and_action(focus, volume, intensity):
    payload = _payload(
        week_focus=focus, volume_action=volume, intensity_action=intensity,
        explanation_keys=[
            "weekly_program.focus." + focus,
            "weekly_program.reason.progressing",
            "weekly_program.reason.volume_trend_down",
        ])
    result = _run_case([{"kind": "json", "payload": payload}])["first"]
    assert result["state"] == "populated"
    assert "focus:" + focus in result["text"]
    assert "volume:" + volume in result["text"]
    assert "intensity:" + intensity in result["text"]


@requires_node
def test_populated_preserves_reason_order_and_formats_tr_en():
    en = _run_case([{"kind": "json", "payload": _payload()}], locale="en")["first"]
    tr = _run_case([{"kind": "json", "payload": _payload()}], locale="tr")["first"]
    assert "1,234.56 kg" in en["text"] and "1,296.29 kg" in en["text"]
    assert "1.234,56 kg" in tr["text"] and "1.296,29 kg" in tr["text"]
    assert en["text"].index("reason:progressing") < en["text"].index(
        "reason:volume_trend_down")
    assert "2026-07-13" not in en["text"] and "0.05" not in en["text"]


@requires_node
def test_insufficient_and_missing_baseline_are_distinct():
    insufficient = _payload(
        has_data=False, week_focus="insufficient_data", volume_action="hold",
        intensity_action="hold", volume_delta_pct=0.0, overload_ready=False,
        baseline_week_start=None, baseline_weekly_volume=None,
        target_weekly_volume=None, reason_codes=["insufficient_history"],
        explanation_keys=["weekly_program.focus.insufficient_data",
                          "weekly_program.reason.insufficient_history"])
    missing = _payload(
        baseline_week_start=None, baseline_weekly_volume=None,
        target_weekly_volume=None)
    a = _run_case([{"kind": "json", "payload": insufficient}])["first"]
    b = _run_case([{"kind": "json", "payload": missing}])["first"]
    assert a["state"] == "insufficient_data" and "Need data" in a["text"]
    assert "volume:" not in a["text"] and "Observed" not in a["text"]
    assert b["state"] == "missing_baseline" and "Missing baseline" in b["text"]
    assert "volume:increase" in b["text"] and "Observed" not in b["text"]


@pytest.mark.parametrize("kind", ["network", "redirect", "status", "nonjson"])
@requires_node
def test_transport_auth_and_non_json_failures_use_error_state(kind):
    result = _run_case([{"kind": kind}])["first"]
    assert result["state"] == "error"
    assert result["role"] == "alert"
    assert "Load error" in result["text"] and "Retry" in result["text"]


@pytest.mark.parametrize("changes", [
    {"has_data": "yes"},
    {"week_focus": "future_focus"},
    {"volume_action": "reduce"},
    {"intensity_action": "reduce"},
    {"volume_delta_pct": float("inf")},
    {"overload_ready": 1},
    {"reason_codes": ["unknown"]},
    {"explanation_keys": ["weekly_program.focus.overload"]},
    {"baseline_week_start": "2026-02-30"},
    {"baseline_weekly_volume": None},
    {"target_weekly_volume": 0},
    {"unsupported": [1]},
])
@requires_node
def test_contract_violations_use_distinct_malformed_state(changes):
    result = _run_case([{"kind": "json", "payload": _payload(**changes)}])["first"]
    assert result["state"] == "malformed"
    assert result["role"] == "alert"
    assert "Malformed data" in result["text"]


@requires_node
def test_additive_fields_and_unknown_unsupported_values_are_accepted():
    payload = _payload(unsupported=["future_capability"], future_field={"safe": True})
    result = _run_case([{"kind": "json", "payload": payload}])["first"]
    assert result["state"] == "populated"


@requires_node
def test_retry_clears_content_and_double_click_cannot_start_concurrent_request():
    result = _run_case([
        {"kind": "status"},
        {"kind": "json", "payload": _payload()},
    ], retry=True)
    assert result["first"]["state"] == "error"
    assert result["retry"]["immediateState"] == "loading"
    assert result["retry"]["finalState"] == "populated"
    assert result["fetches"] == 2
    assert result["retry"]["fetches"] == 2
    assert "Load error" not in result["retry"]["text"]


TRAINING_CSS = (
    Path(__file__).resolve().parents[1] / "static" / "training.css"
).read_text(encoding="utf-8")


def test_weekly_program_styles_are_scoped_and_responsive():
    assert ".weekly-program-card" in TRAINING_CSS
    assert ".weekly-program-metrics" in TRAINING_CSS
    assert ".weekly-program-retry" in TRAINING_CSS
    assert "@media (max-width: 640px)" in TRAINING_CSS
    assert re.search(
        r"\.weekly-program-metrics\s*\{[^}]*grid-template-columns:\s*repeat\(2,",
        TRAINING_CSS, re.DOTALL)
    assert re.search(
        r"@media \(max-width: 640px\).*?\.weekly-program-metrics[^}]*"
        r"grid-template-columns:\s*1fr",
        TRAINING_CSS, re.DOTALL)


def test_weekly_program_styles_pin_overflow_tap_and_shared_skeleton():
    assert re.search(r"\.weekly-program-card\s*\{[^}]*min-width:\s*0",
                     TRAINING_CSS, re.DOTALL)
    assert "overflow-wrap: anywhere" in TRAINING_CSS
    assert re.search(r"\.weekly-program-retry\s*\{[^}]*min-height:\s*44px",
                     TRAINING_CSS, re.DOTALL)
    assert "skeleton skeleton-text weekly-program-skeleton" in CODE



def test_client_formats_only_server_supplied_volumes():
    assert "Intl.NumberFormat" in CODE
    assert "maximumFractionDigits: 2" in CODE
    assert "baseline_weekly_volume" in CODE
    assert "target_weekly_volume" in CODE
    assert "volume_delta_pct" in CODE
    assert not re.search(r"target_weekly_volume\s*=(?!=)", CODE)


@pytest.mark.parametrize("forbidden", [
    "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
    "eval(", "Function(", "localStorage", "sessionStorage", "setTimeout",
    "setInterval", "AbortController", "console.",
])
def test_pr62_security_guards(forbidden):
    assert forbidden not in CODE
