"""Render/regression tests for the Phase 5 workout redesign (Task 9):
canonical markup presence, external asset wiring, no legacy --volt token
leakage. Mirrors the fixture pattern used across the suite
(`app, client, make_user, login` — see tests/conftest.py / tests/test_i18n.py);
there is no `auth_client` fixture in this project."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
TRAINING_SCRIPT = STATIC / "training.js"
WORKOUT_STATE_CLIENT = STATIC / "workout_state_client.js"
PLAN_MANAGEMENT = STATIC / "training_plan_management.js"
NODE = shutil.which("node")


def test_training_renders_hero_and_session(app, client, make_user, login):
    make_user("wkuiuser", profile_complete=True)
    login("wkuiuser")
    r = client.get("/training")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # new canonical markup present
    assert 'id="workout-hero"' in html
    assert 'id="session-view"' in html
    assert 'id="celebration"' in html
    assert 'id="rest-timer"' in html
    assert 'data-action="startWorkout"' in html
    assert 'id="sv-abandon"' in html
    assert 'id="sv-abandon" data-action="abandonWorkout" hidden' in html
    # no legacy volt styling leaked into the page
    assert '--volt' not in html


def test_training_loads_external_assets(app, client, make_user, login):
    make_user("wkuiuser2", profile_complete=True)
    login("wkuiuser2")
    html = client.get("/training").get_data(as_text=True)
    assert '/static/training.js' in html
    assert '/static/workout_state_client.js' in html
    assert '/static/training.css' in html


def test_wstats_collapses_to_fewer_columns_on_narrow_screens():
    """Regression (UIUX Sprint 1 PR3 legacy compatibility fix): the weekly-stats
    row `.wstats` is a fixed 3-column grid whose stat cards cannot shrink below
    their (locale-dependent) text min-content, so at 320px the row overflowed the
    viewport by 24px (EN) / 36px (TR) — a defect pre-existing on base 9400641 (see
    docs/frontend-readiness/sprint-1-pr3/legacy-overflow/). A narrow-width media
    query (<=389px, so the passing 390px+ layout is untouched) must collapse
    `.wstats` to two shrinkable columns so the row fits. Legacy-only: training.css
    is loaded solely by templates/training.html and `.wstats` appears nowhere
    else, so this cannot affect Plan V2 (plan.html/plan.css) or Nav/Today."""
    css = (STATIC / "training.css").read_text(encoding="utf-8")
    # Base row is still the fixed 3-column grid at >=390px (unchanged).
    assert re.search(r"\.wstats\s*\{[^}]*grid-template-columns:\s*repeat\(3", css), \
        "base .wstats should remain a 3-column grid"
    # A <=389px media query reduces .wstats to two columns.
    blocks = re.findall(
        r"@media\s*\(max-width:\s*(\d+)px\)\s*(\{(?:[^{}]|\{[^{}]*\})*\})", css)
    narrow = [
        int(px) for px, body in blocks
        if int(px) <= 389
        and re.search(r"\.wstats\s*\{[^}]*grid-template-columns:\s*repeat\(2", body)
    ]
    assert narrow, "expected a <=389px media query collapsing .wstats to 2 columns"


def test_training_abandon_label_follows_authenticated_locale(
        app, client, make_user, login):
    make_user("wkuien", profile_complete=True, language="en")
    login("wkuien")
    html = client.get("/training").get_data(as_text=True)
    assert ">Abandon workout</button>" in html


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_blocked_training_state_never_falls_back_to_the_setup_form():
    source = TRAINING_SCRIPT.read_text(encoding="utf-8")
    start = source.index("    function renderTrainingBlocked() {")
    end = source.index("\n    }\n\n    // Plan text", start) + len("\n    }")
    function_source = source[start:end]
    harness = f"""
const elements = {{
  'active-plan-view': {{ style: {{ display: 'none' }} }},
  'setup-form': {{ style: {{ display: 'block' }} }},
  'wh-cta': {{ innerHTML: 'stale workout action' }},
}};
const document = {{ getElementById: (id) => elements[id] || null }};
const _EN = true;
let activePlan = [{{ day: 'Monday' }}];
let activeTodayPlan = {{ day: 'Monday' }};
let currentWorkoutState = {{ status: 'active' }};
let activePlanIdentity = {{ lineage_id: 'LIN-1', mutation_version: 3 }};
{function_source}
renderTrainingBlocked();
console.log(JSON.stringify({{
  activePlan,
  activeTodayPlan,
  currentWorkoutState,
  identityIsUnknown: activePlanIdentity === undefined,
  identityIsNull: activePlanIdentity === null,
  activePlanDisplay: elements['active-plan-view'].style.display,
  setupFormDisplay: elements['setup-form'].style.display,
  warning: elements['wh-cta'].innerHTML,
}}));
"""
    completed = subprocess.run(
        [NODE, "-e", harness], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["activePlan"] is None
    assert result["activeTodayPlan"] is None
    assert result["currentWorkoutState"] is None
    # A blocked read leaves the save precondition UNKNOWN, never "no plan":
    # null would be a positive claim that the user has nothing to overwrite,
    # which is exactly how a failed read becomes a destructive save.
    assert result["identityIsUnknown"] is True
    assert result["identityIsNull"] is False
    assert result["activePlanDisplay"] == "block"
    assert result["setupFormDisplay"] == "none"
    assert result["warning"] == (
        '<span class="badge badge-warning">Workout state unavailable</span>'
    )

    save_start = source.index("    async function savePlan() {")
    save_end = source.index("\n\n    // ", save_start)
    save_source = source[save_start:save_end]
    management_path = json.dumps(str(PLAN_MANAGEMENT))
    save_harness = f"""
const assert = require('node:assert/strict');
const {{ createWorkoutStateClient }} = require({json.dumps(str(WORKOUT_STATE_CLIENT))});
function deferred() {{
  let resolve;
  const promise = new Promise(r => {{ resolve = r; }});
  return {{ promise, resolve }};
}}
function response(body, ok = true, status = ok ? 200 : 500) {{
  return {{ ok, status, json: async () => body }};
}}
function makeButton() {{
  const classes = new Set();
  return {{
    textContent: 'Save',
    classList: {{ add: value => classes.add(value) }},
    hasClass: value => classes.has(value),
  }};
}}

(async () => {{
  let button = makeButton();
  const document = {{ getElementById: id => id === 'save-btn' ? button : null }};
  const __t = key => key;
  const toasts = [];
  const showToast = (message, type) => toasts.push({{ message, type }});
  let currentPlan = [{{ gun: 'Pazartesi', egzersizler: [] }}];
  let currentScore = 8;
  let currentContextToken = '1.PAYLOAD.SIGNATURE';
  let activePlanIdentity = {{ lineage_id: 'LIN-1', mutation_version: 3 }};
  let workoutStateClient;
  // UX-3 PR3: savePlan routes the destructive write through the SHARED
  // Training-management contract (the same one Plan v2 uses), which proves the
  // plan is still the one this page was rendered against before writing, and
  // requires a real canonical refresh afterwards. The REAL module is loaded
  // here; a stub would make every assertion below an assertion about the stub.
  const planManagement = require({management_path}).createPlanManagement({{
    fetchImpl: (url, init) => global.fetch(url, init),
    persist: (url, init) => workoutStateClient.mutate(url, init),
    refresh: async () => planRefreshedAfterSave,
    baseline: {{ present: true, plan_lineage: 'lineage-1', mutation_version: 3 }},
  }});
  let planRefreshedAfterSave = false;
{save_source}

  const write = deferred();
  const calls = [];
  const canonicalPlan = {{
    exists: true, plan_lineage: 'lineage-1', mutation_version: 3,
  }};
  const generated = {{
    program: [{{ gun: 'Pazartesi', egzersizler: [] }}], overall_score: 8,
    exercise_context_token: '1.PAYLOAD.SIGNATURE',
  }};
  const successFetch = (url, init = {{}}) => {{
    calls.push({{ url, init }});
    if (url === '/training-plan') return Promise.resolve(response(generated));
    if (url === '/training-plan/save') return write.promise;
    if (url === '/training/bootstrap') return Promise.resolve(response({{
      plan: canonicalPlan,
      workout: {{ state: {{ contract_version: 2, session_state: 'none', session: null }} }},
      today_plan: null,
    }}));
    throw new Error('unexpected URL: ' + url);
  }};
  global.fetch = successFetch;
  assert.equal((await planManagement.generate({{}})).ok, true);
  calls.length = 0;
  workoutStateClient = createWorkoutStateClient({{
    fetchImpl: successFetch,
    onSnapshot: () => {{ planRefreshedAfterSave = true; }},
    addEventListener: () => {{}}, removeEventListener: () => {{}},
    documentRef: {{ hidden: false, addEventListener() {{}}, removeEventListener() {{}} }},
  }});
  const first = savePlan();
  const repeated = savePlan();
  await Promise.resolve();
  write.resolve(response({{ saved: true }}, true, 200));
  await Promise.all([first, repeated]);

  // Freshness is proven BEFORE the one destructive write and the canonical
  // refresh follows it; a second concurrent click adds no second write.
  assert.deepEqual(calls.map(call => call.url), [
    '/training/bootstrap', '/training-plan/save', '/training/bootstrap'
  ]);
  const saved = calls.find(call => call.url === '/training-plan/save');
  assert.equal(saved.init.method, 'POST');
  assert.deepEqual(saved.init.headers, {{ 'Content-Type': 'application/json' }});
  assert.deepEqual(JSON.parse(saved.init.body), {{
    plan: [{{ gun: 'Pazartesi', egzersizler: [] }}], score: 8,
    exercise_context_token: '1.PAYLOAD.SIGNATURE',
    expected_plan: {{ lineage_id: 'LIN-1', mutation_version: 3 }}
  }});
  assert.equal(button.textContent, 'training.saved');
  assert.equal(button.hasClass('saved'), true);
  assert.ok(toasts.some(toast => toast.type === 'success'));
  workoutStateClient.destroy();

  button = makeButton();
  toasts.length = 0;
  currentPlan = [{{ gun: 'Pazartesi', egzersizler: [] }}];
  const failedCalls = [];
  const failedFetch = url => {{
    failedCalls.push(url);
    if (url === '/training-plan') return Promise.resolve(response(generated));
    if (url === '/training-plan/save') {{
      return Promise.resolve(response({{ error: 'conflict' }}, false, 409));
    }}
    return Promise.resolve(response({{
      plan: canonicalPlan,
      workout: {{ state: {{ contract_version: 2, session_state: 'none', session: null }} }},
      today_plan: null,
    }}));
  }};
  global.fetch = failedFetch;
  assert.equal((await planManagement.generate({{}})).ok, true);
  failedCalls.length = 0;
  workoutStateClient = createWorkoutStateClient({{
    fetchImpl: failedFetch,
    onSnapshot: () => {{}},
    addEventListener: () => {{}}, removeEventListener: () => {{}},
    documentRef: {{ hidden: false, addEventListener() {{}}, removeEventListener() {{}} }},
  }});
  await savePlan();
  assert.deepEqual(failedCalls, [
    '/training/bootstrap', '/training-plan/save', '/training/bootstrap'
  ]);
  assert.equal(button.textContent, 'Save');
  assert.equal(button.hasClass('saved'), false);
  assert.equal(toasts.some(toast => toast.type === 'success'), false);
  assert.equal(toasts.some(toast => toast.type === 'error'), true);
  workoutStateClient.destroy();

  // A stale precondition: the server refused and destroyed nothing, so the
  // page must say so and must NOT show the saved state.
  button = makeButton();
  toasts.length = 0;
  workoutStateClient = {{
    mutate: async () => ({{ ok: false, status: 409,
                           body: {{ code: 'TRAINING_PLAN_SAVE_PLAN_CHANGED' }} }}),
  }};
  await savePlan();
  assert.equal(button.textContent, 'Save');
  assert.equal(button.hasClass('saved'), false);
  assert.equal(toasts.some(toast => toast.type === 'success'), false);
  assert.equal(toasts.some(toast => toast.type === 'error'), true);
  assert.ok(toasts.some(toast => toast.message.indexOf('training.plan_changed') >= 0));

  // Canonical state was never read (a blocked bootstrap). There is no honest
  // precondition to send, so no destructive request may leave the page.
  button = makeButton();
  toasts.length = 0;
  const unreadCalls = [];
  activePlanIdentity = undefined;
  workoutStateClient = {{ mutate: async url => {{ unreadCalls.push(url); return null; }} }};
  await savePlan();
  assert.deepEqual(unreadCalls, []);
  assert.equal(button.textContent, 'Save');
  assert.equal(toasts.some(toast => toast.type === 'error'), true);
  activePlanIdentity = {{ lineage_id: 'LIN-1', mutation_version: 3 }};

  button = makeButton();
  toasts.length = 0;
  currentPlan = [{{ gun: 'Pazartesi', egzersizler: [] }}];
  assert.equal((await planManagement.generate({{}})).ok, true);
  workoutStateClient = {{ mutate: async () => null }};
  await savePlan();
  assert.equal(button.textContent, 'Save');
  assert.equal(button.hasClass('saved'), false);
  assert.equal(toasts.some(toast => toast.type === 'success'), false);
  assert.equal(toasts.some(toast => toast.type === 'error'), true);

  console.log(JSON.stringify({{ ok: true }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        [NODE, "-e", save_harness], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr

    lifecycle_start = source.index("    populateOptions();")
    lifecycle_source = source[lifecycle_start:]
    lifecycle_harness = f"""
const assert = require('node:assert/strict');
const clientApi = require({json.dumps(str(WORKOUT_STATE_CLIENT))});
const windowListeners = new Map();
function listenerMap(name) {{
  if (!windowListeners.has(name)) windowListeners.set(name, new Map());
  return windowListeners.get(name);
}}
const requests = [];
const activeSnapshot = {{
  plan: {{ exists: true }}, today_plan: {{ gun: 'Pazartesi' }},
  workout: {{ state: {{
    contract_version: 2,
    session_state: 'active_resumable',
    session: {{ status: 'active', public_id: 'session-1' }},
  }} }},
}};
const window = {{
  fetch: async url => {{
    requests.push(url);
    return {{ ok: true, status: 200, json: async () => activeSnapshot }};
  }},
  FitXWorkoutStateClient: clientApi,
  addEventListener(name, fn, options) {{ listenerMap(name).set(fn, options || {{}}); }},
  removeEventListener(name, fn) {{ listenerMap(name).delete(fn); }},
}};
function dispatch(name, event) {{
  for (const [fn, options] of [...listenerMap(name)]) {{
    fn(event);
    if (options.once) listenerMap(name).delete(fn);
  }}
}}
const documentListeners = new Map();
const document = {{
  hidden: false,
  addEventListener(name, fn) {{ documentListeners.set(fn, name); }},
  removeEventListener(name, fn) {{ documentListeners.delete(fn); }},
}};
let nextTimer = 0;
const timers = new Set();
global.setInterval = () => {{ const id = ++nextTimer; timers.add(id); return id; }};
global.clearInterval = id => timers.delete(id);
function populateOptions() {{}}
function setupInjuryPicker() {{}}
function loadInfo() {{}}
function applyTrainingSnapshot() {{}}
function renderTrainingBlocked() {{}}
let workoutStateClient = null;
{lifecycle_source}

(async () => {{
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(requests, ['/training/bootstrap']);
  assert.equal(timers.size, 0);
  assert.equal(listenerMap('pagehide').size, 1);
  assert.equal(listenerMap('pageshow').size, 1);

  dispatch('pagehide', {{ persisted: true }});
  assert.equal(timers.size, 0);
  dispatch('pageshow', {{ persisted: true }});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(requests, ['/training/bootstrap', '/training/bootstrap']);
  assert.equal(timers.size, 0);

  dispatch('pagehide', {{ persisted: true }});
  dispatch('pageshow', {{ persisted: true }});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(requests, [
    '/training/bootstrap', '/training/bootstrap', '/training/bootstrap'
  ]);
  assert.equal(timers.size, 0);
  assert.equal(listenerMap('pagehide').size, 1);
  assert.equal(listenerMap('pageshow').size, 1);
  assert.equal(listenerMap('focus').size, 1);
  console.log(JSON.stringify({{ ok: true }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        [NODE, "-e", lifecycle_harness], capture_output=True, text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# ── Signed exercise-context token forwarding (Sprint 11 PR4 Task 4) ─────────


def test_legacy_client_carries_the_context_token_from_generate_into_save():
    """The token is a carrier, not client state.

    Legacy training.js must store the generate response's token beside the
    in-memory candidate and post it back on save — and must not parse it,
    render it, edit it, put it in a URL, or persist it anywhere the candidate
    itself does not live.
    """
    source = TRAINING_SCRIPT.read_text(encoding="utf-8")
    shared = PLAN_MANAGEMENT.read_text(encoding="utf-8")

    # UX-3 PR3: legacy still holds the token beside its in-memory candidate, but
    # the generate→save carry itself moved into the shared Training-management
    # contract, so the guard now covers both halves of the same journey.
    assert "currentContextToken = result.proposal.exercise_context_token" in source
    assert "exercise_context_token: body.exercise_context_token" in shared
    assert "exercise_context_token: proposal.exercise_context_token" in shared
    # Declared beside the rest of the in-memory candidate, so it dies with it.
    assert re.search(
        r"let currentPlan = null, currentScore = null, currentContextToken = null;",
        source)
    # ...and it is actually released when the candidate is.
    assert source.count("currentContextToken = null;") >= 3
    for forbidden in (
        "currentContextToken.split", "currentContextToken.slice",
        "atob(", "JSON.parse(currentContextToken)",
        "localStorage.setItem('exercise_context_token'",
        "textContent = currentContextToken",
    ):
        assert forbidden not in source, forbidden
    # Never persisted or shipped in a URL.
    token_lines = [
        line for line in source.splitlines() if "ContextToken" in line
    ]
    for line in token_lines:
        for forbidden in ("localStorage", "sessionStorage", "location",
                          "innerHTML", "searchParams"):
            assert forbidden not in line, line
