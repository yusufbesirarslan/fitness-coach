# Phase 5 — Workout Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/training` into a premium, mobile-first workout experience on the canonical AxisAI design system — Today's Workout Hero, an interactive session player (exercise cards, weight/reps/sets, rest timer, PR indicators), and an XP celebration — driven entirely by in-memory session state, with **zero backend changes**.

**Architecture:** Front-end-only. Extract the page's inline `<script>` into `static/training.js` and add a canonical `static/training.css` (mirrors `nutrition.js`/`nutrition.css`). The active-workout session is a single ephemeral in-memory object (`_session`) with pure helpers and a `prProvider` seam (`WORKOUT-PERSIST-HOOK`) for a future Workout Log backend — **no `localStorage`, no new endpoints**. Every existing backend call and payload is preserved byte-for-byte.

**Tech Stack:** Flask + Jinja templates, vanilla JS with `data-action` delegation (`static/actions.js`), canonical CSS design tokens, pytest + `node --check`.

## Global Constraints

- **Preserve backend business logic** — no route, payload, schema, or migration changes. The only files touched are `templates/training.html`, `static/training.js` (new), `static/training.css` (new), `locales/{tr,en}.json`, `tests/`, `docs/`.
- **Weights/PR = UI only, in-memory** — build the full weight/sets/reps/rest-timer/PR UI as reusable, state-driven components holding values in one in-memory `_session` object. **No `localStorage`. No new backend.** Separate the UI layer from persistence via the `prProvider` seam so a backend plugs in later. On finish: compute stats in memory → show XP + summary → discard `_session`.
- **Preserve exactly:** all backend calls & shapes (`/last-session`, `/training-plan`, `/training-plan/save`, `/training-plan/active`, `/workout/status`, `/workout/complete`, `/friends/select-list`); the `selections` generate payload; the injury multi-select picker (free-text = source of truth); Pump Check feed/friends sharing; the pre-existing `fitx_workout_completed_*` completion-cache `localStorage` (distinct from the forbidden session storage).
- **i18n canonical coupling** — day/injury/goal/score **values stay Turkish** (plan keys, `getTodayTurkish()` match); only *display* text is localized via `__t`/`_EN`/`injuryLabel`/`dayLabel`. New UI strings go through `t()` keys in both locales.
- **CSP** — every inline `<script>` carries `nonce="{{ csp_nonce }}"`; never inject `<style>` from JS; load page CSS via `<link href="/static/training.css?v={{ _v }}">`; no external libraries (rest timer = `setInterval`; celebration/confetti = CSS only).
- **Design system** — reuse `components.css` primitives (`.card`, `.ring-*`, `.pbar-*`, `.stat-card`, `.badge-*`, `.chip`, `.sheet-*`, `.modal-*`, `.empty-state`, `.skeleton`, `.btn-*`, `.sec-label`, `.toast-*`, `.avatar`). No `--volt`/raw hex/rgba in `training.css` — canonical tokens only (single allowed exception: a full-bleed overlay scrim, documented).
- **Test:** `python -m pytest -q` stays green; `node --check static/training.js` after every JS task. **Do NOT `git add -A`** — untracked scratch exists at repo root; stage explicit paths only.

## File Structure

- `static/training.js` — CREATE: all page JS (extracted from the inline block, then extended). Owns: setup-form options/injury picker, plan generate/preview/save, active-plan render + **Today's Workout Hero**, **session player** (state module + exercise cards + set rows + rest timer + PR), Pump Check flow, **celebration**.
- `static/training.css` — CREATE: page-scoped canonical-token styles for hero, week strip, session player, exercise/set components, rest timer, PR badge, celebration, restyled setup form + pump modal.
- `templates/training.html` — REWRITE: canonical shell, active-plan-view (hero + week strip + stats), session-view overlay, celebration overlay, restyled setup form, restyled pump modal; loads `training.css` + `training.js`; tiny nonce'd bootstrap for `injuries`.
- `locales/tr.json`, `locales/en.json` — MODIFY: new `training.*` keys.
- `tests/test_training_ui.py` — CREATE: render assertions (hero, session markup, new keys) + canonical-coupling checks.
- `tests/test_i18n.py` — MODIFY (if it enumerates training keys).
- `docs/handoff.md` — REWRITE at phase end; prior handoff archived.

---

### Task 1: Extract inline JS → `static/training.js` + scaffold `static/training.css` (behavior-preserving baseline)

**Files:**
- Create: `static/training.js`
- Create: `static/training.css` (initially a header comment only)
- Modify: `templates/training.html` (head `<link>` + tail `<script>` + bootstrap; remove inline `<script>` body)

**Interfaces:**
- Produces: external `training.js` exposing the SAME global functions the template's `data-action`s already call (`generatePlan`, `savePlan`, `resetPlan`, `finishWorkout`, `submitPumpCheck`, `closeApvDetail`, `closeDetail`, `toggleQuickAdd`, `fxOpenSetupForm`, `fxTriggerFinish`, …). Server value passed via `window.__TRAINING = { injuries }`.

- [ ] **Step 1: Create `static/training.js` from the inline block**

Copy the entire body of the current inline `<script nonce>` in `templates/training.html` (from `var __t = …` through the bottom bootstrap calls `populateOptions(); setupInjuryPicker(); loadInfo(); loadActivePlan().then(...)`) into a new `static/training.js`. Make exactly one change — replace the Jinja-injected injuries line:

```javascript
// BEFORE (inline template):
//   injuries: {{ injuries|tojson }}
// AFTER (external js, reads bootstrap global):
injuries: (window.__TRAINING && window.__TRAINING.injuries) || ""
```

Leave all other logic identical for now (rewrites happen in later tasks).

- [ ] **Step 2: Wire the template**

In `templates/training.html`:
- In `<head>`, add after the existing `theme.css`/`nav.css` links:
  ```html
  <link rel="stylesheet" href="/static/training.css?v={{ _v }}">
  ```
- Replace the entire inline `<script nonce="{{ csp_nonce }}"> … </script>` block with a tiny bootstrap + external load, placed where the inline block was (before `coach_widget.js`):
  ```html
  <script nonce="{{ csp_nonce }}">window.__TRAINING = { injuries: {{ injuries|tojson }} };</script>
  <script src="/static/training.js?v={{ _v }}"></script>
  ```
- Add `?v={{ _v }}` to the existing `coach_widget.js`/`actions.js` includes for consistency:
  ```html
  <script src="/static/coach_widget.js?v={{ _v }}"></script>
  <script src="/static/actions.js?v={{ _v }}"></script>
  ```

- [ ] **Step 3: Create `static/training.css` placeholder**

```css
/* Phase 5 workout page — canonical tokens, mobile-first.
   Page-scoped rules only; shared primitives come from components.css. */
```

- [ ] **Step 4: Syntax + render checks**

Run: `node --check static/training.js`
Expected: no output (valid).
Run: `python -m pytest tests/test_i18n.py -q -k training` (or the training render test if present)
Expected: PASS, or template renders without Jinja error.

- [ ] **Step 5: Manual smoke**

`flask run` (FLASK_DEBUG=1) → `/training`: setup form works (options select, generate, save), active-plan view renders, day detail opens, Finish → Pump Check works. Behavior must be identical to before extraction.

- [ ] **Step 6: Commit**

```bash
git add static/training.js static/training.css templates/training.html
git commit -m "Extract training JS to external module + scaffold training.css"
```

---

### Task 2: `static/training.css` — full canonical stylesheet for all new components

**Files:**
- Modify: `static/training.css`

**Interfaces:**
- Produces classes consumed by Tasks 3–7 markup: `.workout-hero`/`.wh-*`, `.week-strip`/`.week-chip`, `.wstats`, `.session-view`/`.sv-head`/`.sv-progress`, `.exercise-card`/`.ec-*`, `.set-row`/`.set-cell`/`.set-input`/`.set-check`, `.pr-badge`, `.top-set`, `.rest-timer`/`.rt-*`, `.celebration`/`.cel-*`/`.xp-count`, plus restyled `.tw-chip` (setup option), `.tw-score`, and pump-modal token overrides.

- [ ] **Step 1: Write the stylesheet**

Append to `static/training.css`. Canonical tokens only (no `--volt`/hex). Representative core blocks (fill remaining states to `dashboard.css`/`nutrition.css` density):

```css
/* ── Today's Workout Hero ── */
.workout-hero { display: grid; grid-template-columns: auto 1fr; gap: var(--space-4);
  align-items: center; background: var(--color-surface-2);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); padding: var(--space-5); position: relative;
  overflow: hidden; }
.workout-hero.is-rest { grid-template-columns: 1fr; text-align: center; }
.wh-ring { grid-row: span 2; }
.wh-focus { font-family: var(--font-display); letter-spacing: 2px;
  color: var(--color-primary); font-size: var(--text-2xl); }
.wh-meta { display: flex; gap: var(--space-4); color: var(--color-text-3);
  font-size: var(--text-sm); margin-top: var(--space-1); flex-wrap: wrap; }
.wh-cta { grid-column: 1 / -1; }        /* full-width Start button */
.workout-hero.is-done .wh-cta { display: none; }
.wh-done-badge { display: inline-flex; align-items: center; gap: var(--space-1); }
@media (max-width: 560px) { .workout-hero { grid-template-columns: 1fr;
  justify-items: center; text-align: center; } .wh-ring { grid-row: auto; } }

/* ── This-week strip ── */
.week-strip { display: grid; grid-template-columns: repeat(7, 1fr);
  gap: var(--space-1); }
.week-chip { border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-md); padding: var(--space-2) var(--space-1);
  text-align: center; cursor: pointer; min-height: 44px;
  background: var(--color-surface-2); transition: border-color var(--duration-fast) var(--ease-standard); }
.week-chip.is-today { border-color: var(--color-primary);
  box-shadow: var(--shadow-primary); }
.week-chip.is-rest { opacity: var(--opacity-muted); cursor: default; }
.week-chip.is-cardio { border-color: var(--color-info); }
.wc-day { font-family: var(--font-display); letter-spacing: 1px;
  font-size: var(--text-xs); color: var(--color-text-2); }
.wc-focus { font-size: var(--text-2xs); color: var(--color-text-3);
  margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 420px) { .week-strip { grid-template-columns: repeat(4, 1fr); } }

/* ── Weekly stats (reuse .stat-card) ── */
.wstats { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-2); }

/* ── Session player overlay ── */
.session-view { position: fixed; inset: 0; z-index: var(--z-overlay);
  background: var(--color-bg); display: none; flex-direction: column; }
.session-view.open { display: flex; }
.sv-head { display: flex; align-items: center; gap: var(--space-3);
  padding: var(--space-4); border-bottom: var(--border-w-1) solid var(--color-border-1);
  position: sticky; top: 0; background: var(--color-bg); }
.sv-title { font-family: var(--font-display); letter-spacing: 2px;
  color: var(--color-text-1); flex: 1; }
.sv-progress { height: 4px; background: var(--overlay-6);
  border-radius: var(--radius-full); overflow: hidden; }
.sv-progress > div { height: 100%; width: 0; background: var(--color-primary);
  transition: width var(--duration-base) var(--ease-standard); }
.sv-body { flex: 1; overflow-y: auto; padding: var(--space-4);
  display: flex; flex-direction: column; gap: var(--space-3); }
.sv-foot { padding: var(--space-4); border-top: var(--border-w-1) solid var(--color-border-1); }

/* ── Exercise card ── */
.exercise-card { background: var(--color-surface-2);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); padding: var(--space-4); }
.ec-head { display: flex; align-items: baseline; gap: var(--space-2); }
.ec-name { font-weight: var(--weight-semibold); color: var(--color-text-1);
  font-size: var(--text-lg); }
.ec-prescribed { margin-left: auto; color: var(--color-text-3);
  font-size: var(--text-sm); }
.ec-note { color: var(--color-text-3); font-size: var(--text-sm);
  margin-top: var(--space-1); }

/* ── Set rows (weight / reps / done) ── */
.set-row { display: grid;
  grid-template-columns: 32px 1fr 1fr 44px; gap: var(--space-2);
  align-items: center; padding: var(--space-2) 0;
  border-top: var(--border-w-1) solid var(--color-border-1); }
.set-row.is-done { opacity: var(--opacity-faint); }
.set-idx { color: var(--color-text-3); font-size: var(--text-sm); text-align: center; }
.set-input { width: 100%; min-height: 44px; text-align: center;
  background: var(--color-surface-1); color: var(--color-text-1);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-sm); font: inherit; }
.set-input:focus-visible { outline: none; border-color: var(--color-primary);
  box-shadow: var(--focus-ring); }
.set-check { width: 44px; height: 44px; border-radius: var(--radius-sm);
  border: var(--border-w-2) solid var(--color-border-2); background: none;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  color: transparent; }
.set-row.is-done .set-check { background: var(--color-success);
  border-color: var(--color-success); color: var(--color-on-primary); }
.set-col-label { font-size: var(--text-2xs); letter-spacing: var(--tracking-label);
  text-transform: uppercase; color: var(--color-text-3); }

/* ── PR + top-set indicators ── */
.pr-badge { display: none; }              /* shown via .is-pr on the row */
.set-row.is-pr .pr-badge { display: inline-flex; }
.set-row.top-set .set-idx::after { content: '★'; color: var(--color-warning);
  margin-left: 2px; }

/* ── Rest timer ── */
.rest-timer { position: fixed; left: 50%; bottom: calc(var(--action-bar-h,64px) + var(--space-4));
  transform: translateX(-50%); z-index: var(--z-toast);
  background: var(--color-surface-3); border: var(--border-w-1) solid var(--color-border-2);
  border-radius: var(--radius-full); padding: var(--space-2) var(--space-3);
  display: none; align-items: center; gap: var(--space-3); box-shadow: var(--elevation-3); }
.rest-timer.open { display: flex; }
.rt-time { font-family: var(--font-display); font-size: var(--text-xl);
  color: var(--color-primary); min-width: 56px; text-align: center; }
.rt-btn { min-width: 44px; min-height: 36px; border-radius: var(--radius-sm);
  border: var(--border-w-1) solid var(--color-border-2); background: none;
  color: var(--color-text-2); cursor: pointer; }

/* ── Celebration ── */
.celebration { position: fixed; inset: 0; z-index: var(--z-overlay);
  background: var(--color-bg); display: none; flex-direction: column;
  align-items: center; justify-content: center; gap: var(--space-4);
  padding: var(--space-6); text-align: center; }
.celebration.open { display: flex; }
.cel-xp { font-family: var(--font-display); font-size: var(--text-display-lg);
  color: var(--color-primary); }
.cel-summary { display: grid; grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2); width: 100%; max-width: 360px; }

/* ── Setup-form option chips (reuse .chip look) restyle ── */
.tw-chip { display: flex; align-items: center; gap: var(--space-2);
  background: var(--color-surface-2); border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-md); padding: var(--space-3) var(--space-4);
  cursor: pointer; user-select: none; min-height: 44px;
  transition: border-color var(--duration-fast) var(--ease-standard),
    background var(--duration-fast) var(--ease-standard); }
.tw-chip.selected { background: var(--color-primary-soft);
  border-color: var(--color-primary); }
.tw-chip-dot { width: 8px; height: 8px; border-radius: var(--radius-full);
  border: var(--border-w-2) solid var(--color-text-3); flex-shrink: 0; }
.tw-chip.selected .tw-chip-dot { background: var(--color-primary);
  border-color: var(--color-primary); }

@media (prefers-reduced-motion: reduce) {
  .sv-progress > div, .set-input, .week-chip { transition: none; }
}
```

- [ ] **Step 2: Token sanity check**

Run: `grep -nE "\-\-volt|#[0-9a-fA-F]{3,6}|rgba\(" static/training.css`
Expected: no `--volt`, no raw hex/rgba (document any single overlay-scrim exception). Fix stragglers to tokens.

- [ ] **Step 3: Commit**

```bash
git add static/training.css
git commit -m "Add canonical training.css for Phase 5 workout redesign"
```

---

### Task 3: Active-plan view — Today's Workout Hero + week strip + weekly stats

**Files:**
- Modify: `templates/training.html` (active-plan-view markup)
- Modify: `static/training.js` (`loadActivePlan` → hero/week/stats render)

**Interfaces:**
- Consumes: `GET /training-plan/active` (`{exists, plan[], score, created_at}`), `GET /workout/status` (`{completed}`), `getTodayTurkish()`, `dayLabel()`, `esc()`, `__t`.
- Produces: `renderHero(program, completed)`, `renderWeekStrip(program)`, `renderWeekStats(program)`; DOM ids `#wh-focus`, `#wh-meta`, `#wh-ring`, `#wh-cta`, `#week-strip`, `#wstats`, `#apv-score`, `#apv-meta`. `startWorkout()` (Task 4) is the hero CTA action.

- [ ] **Step 1: Rewrite the active-plan-view markup**

Replace the `#active-plan-view` inner markup (the `.apv-header`, `.today-banner`, `.weekly-grid`, `.detail-panel`, `.weekly-summary`, finish button) with:

```html
<div id="active-plan-view" style="display:none">
  <!-- Today's Workout Hero -->
  <div class="card">
    <div class="workout-hero" id="workout-hero">
      <div class="wh-ring" id="wh-ring"><!-- reuse .ring-* markup, JS sets pct --></div>
      <div>
        <div class="wh-focus" id="wh-focus">—</div>
        <div class="wh-meta" id="wh-meta"></div>
      </div>
      <div class="wh-cta" id="wh-cta">
        <button class="btn-volt w-full" data-action="startWorkout">{{ t('training.start_workout') }}</button>
      </div>
    </div>
  </div>

  <!-- This week -->
  <div class="sec-label">{{ t('training.this_week') }}</div>
  <div class="week-strip" id="week-strip"></div>

  <!-- Weekly stats -->
  <div class="wstats" id="wstats" style="margin-top:var(--space-4)"></div>

  <!-- Plan meta + reset -->
  <div class="apv-meta-row">
    <span id="apv-meta"></span>
    <span class="badge badge-primary" id="apv-score">—</span>
    <button class="btn-ghost" data-action="resetPlan">↺ {{ t('training.reset_plan') }}</button>
  </div>
</div>
```

- [ ] **Step 2: Rewrite `loadActivePlan` render logic**

In `training.js`, keep the fetch + `activePlan = data.plan`, but replace the banner/grid/summary DOM writes with hero/week/stats renderers. Add:

```javascript
function todayDay() {
  var name = getTodayTurkish();
  return (activePlan || []).find(function (g) { return g.gun === name; }) || null;
}

function renderHero(program, completed) {
  var hero = document.getElementById('workout-hero');
  var day = todayDay();
  var isRest = !day || day.tip === 'dinlenme';
  hero.classList.toggle('is-rest', isRest);
  hero.classList.toggle('is-done', !!completed);
  var focusEl = document.getElementById('wh-focus');
  var metaEl = document.getElementById('wh-meta');
  var cta = document.getElementById('wh-cta');
  if (isRest) {
    focusEl.textContent = __t('training.rest_day');
    metaEl.textContent = __t('training.active_recovery');
    cta.innerHTML = '';
  } else {
    var exs = day.egzersizler || [];
    focusEl.textContent = (day.odak || exs[0] && exs[0].isim || __t('training.workout')).toUpperCase();
    metaEl.innerHTML =
      '<span>' + exs.length + ' ' + __t('training.exercises') + '</span>' +
      '<span>' + (day.sure_dk || 0) + ' ' + __t('training.min') + '</span>' +
      '<span>~' + (day.tahmini_kalori || 0) + ' kcal</span>';
    if (completed) {
      cta.innerHTML = '<span class="wh-done-badge badge badge-success">✓ ' +
        __t('training.workout_done_label') + '</span>';
    } else {
      cta.innerHTML = '<button class="btn-volt w-full" data-action="startWorkout">' +
        __t('training.start_workout') + '</button>';
    }
  }
  // progress ring: 0% until a session runs (session is ephemeral)
  updateHeroRing(completed ? 100 : 0);
}

function renderWeekStrip(program) {
  var strip = document.getElementById('week-strip');
  var todayName = getTodayTurkish();
  strip.innerHTML = (program || []).map(function (g) {
    var isRest = g.tip === 'dinlenme', isCardio = g.tip === 'kardiyo';
    var cls = 'week-chip' + (g.gun === todayName ? ' is-today' : '') +
      (isRest ? ' is-rest' : '') + (isCardio ? ' is-cardio' : '');
    var focus = isRest ? __t('training.off') : esc(g.odak || (g.egzersizler && g.egzersizler[0] && g.egzersizler[0].isim) || '');
    return '<div class="' + cls + '" data-action="previewDay" data-args=\'["' + esc(g.gun) + '"]\'>' +
      '<div class="wc-day">' + esc(dayLabel(g.gun)).slice(0, 3) + '</div>' +
      '<div class="wc-focus">' + focus + '</div></div>';
  }).join('');
}

function renderWeekStats(program) {
  var days = (program || []).filter(function (g) { return g.tip !== 'dinlenme'; }).length;
  var kcal = (program || []).reduce(function (a, g) { return a + (g.tahmini_kalori || 0); }, 0);
  var mins = (program || []).reduce(function (a, g) { return a + (g.sure_dk || 0); }, 0);
  document.getElementById('wstats').innerHTML =
    statCard(days, __t('training.workout_day')) +
    statCard(kcal, __t('training.weekly_cal')) +
    statCard(mins, __t('training.total_min'));
}
function statCard(v, label) {
  return '<div class="stat-card"><div class="stat-value">' + v +
    '</div><div class="stat-label">' + label + '</div></div>';
}
```

Wire the score badge/meta (`#apv-score`, `#apv-meta`) from `data.score`/`data.created_at` as before, then call `renderHero(activePlan, false)` / `renderWeekStrip` / `renderWeekStats`. Keep the view switch (`active-plan-view` shown, `setup-form` hidden). Replace `checkWorkoutCompleted()`'s `markWorkoutCompleted()` with `renderHero(activePlan, true)` so completion repaints the hero.

- [ ] **Step 3: `previewDay` + hero ring helpers**

```javascript
function previewDay(gunName) {
  var day = (activePlan || []).find(function (g) { return g.gun === gunName; });
  if (!day || day.tip === 'dinlenme') return;
  openDayPreview(day);   // read-only sheet listing exercises (Task 4 reuses .sheet)
}
function updateHeroRing(pct) {
  var el = document.getElementById('wh-ring');
  if (el) el.setAttribute('data-pct', pct);  // ring-fill stroke set by shared ring helper
}
```

- [ ] **Step 4: `node --check` + manual**

Run: `node --check static/training.js` → valid.
Manual: `/training` with a saved plan → hero shows today's focus/exercise-count/duration/kcal + Start button; week strip highlights today; stats show days/kcal/min; rest-day → calm hero, no CTA; after completing (or if `/workout/status` says completed) → hero shows "✓ done".

- [ ] **Step 5: Commit**

```bash
git add templates/training.html static/training.js
git commit -m "Redesign active-plan view: Today's Workout Hero + week strip + stats"
```

---

### Task 4: Session player core — state module, exercise cards, set rows, finish→stats

**Files:**
- Modify: `templates/training.html` (add `#session-view` overlay + day-preview sheet)
- Modify: `static/training.js` (session state module + render + open/close/finish)

**Interfaces:**
- Consumes: today's plan day object; `esc`, `__t`.
- Produces (used by Task 5/6): `_session` (module-global, `null` when idle), `buildSession(day)`, `defaultReps(tekrar)`, `computeSessionStats(session)`, `startWorkout()`, `openSession(day)`, `closeSession()`, `renderSession()`, `finishSession()`, and a scoped input listener updating `_session`. `finishSession()` calls `openPumpCheck()` (existing) after stashing stats in `_pendingStats`.

- [ ] **Step 1: Add the session-view overlay markup**

Before `{% include "_actionbar.html" %}` (or after `</main>`), add:

```html
<div class="session-view" id="session-view" role="dialog" aria-modal="true" aria-labelledby="sv-title">
  <div class="sv-head">
    <button class="btn-ghost" data-action="closeSession" aria-label="{{ t('training.close') }}">×</button>
    <div class="sv-title" id="sv-title">{{ t('training.session') }}</div>
    <span class="badge badge-neutral" id="sv-count">0/0</span>
  </div>
  <div class="sv-progress"><div id="sv-progress-bar"></div></div>
  <div class="sv-body" id="sv-body"></div>
  <div class="sv-foot">
    <button class="btn-volt w-full" data-action="finishSession">{{ t('training.finish_workout') }}</button>
  </div>
</div>

<!-- read-only day preview (non-today days) -->
<div class="sheet-backdrop" id="day-preview" data-action-self="closeDayPreview">
  <div class="sheet" role="dialog" aria-modal="true">
    <div class="sheet-handle"></div>
    <div class="sheet-title" id="dp-title"></div>
    <div id="dp-body"></div>
  </div>
</div>
```

- [ ] **Step 2: Session state module (pure, in-memory)**

Add to `training.js` (grouped under a clear `// ── WORKOUT SESSION (ephemeral, in-memory only) ──` banner):

```javascript
// ── WORKOUT SESSION — ephemeral, in-memory only. No localStorage, no network.
//    A future Workout Log backend plugs in at the prProvider seam (Task 5). ──
var _session = null;        // { startedAt, day, exercises:[{isim,tekrar,dinlenme,not,sets:[{weightKg,reps,done,isPR}]}] }
var _pendingStats = null;   // stats snapshot handed to the celebration after Pump Check

function defaultReps(tekrar) {
  var m = String(tekrar || '').match(/\d+/g);
  return m && m.length ? parseInt(m[m.length - 1], 10) : null;   // "8-12" -> 12
}

function buildSession(day) {
  return {
    startedAt: Date.now(),
    day: day,
    exercises: (day.egzersizler || []).map(function (ex) {
      var n = Math.max(1, parseInt(ex.set, 10) || 1);
      var sets = [];
      for (var i = 0; i < n; i++) {
        sets.push({ weightKg: null, reps: defaultReps(ex.tekrar), done: false, isPR: false });
      }
      return { isim: ex.isim, tekrar: ex.tekrar, dinlenme: ex.dinlenme,
               not: ex.not || '', sets: sets };
    }),
  };
}

function computeSessionStats(session) {
  var vol = 0, done = 0, total = 0, prs = 0, exDone = 0;
  (session.exercises || []).forEach(function (ex) {
    var any = false;
    ex.sets.forEach(function (st) {
      total++;
      if (st.done) {
        done++; any = true;
        vol += (Number(st.weightKg) || 0) * (Number(st.reps) || 0);
        if (st.isPR) prs++;
      }
    });
    if (any) exDone++;
  });
  return { totalVolume: Math.round(vol), setsDone: done, totalSets: total,
           prCount: prs, exercisesDone: exDone,
           elapsedMin: Math.max(0, Math.round((Date.now() - session.startedAt) / 60000)) };
}
```

- [ ] **Step 3: Open / render / close / finish**

```javascript
function startWorkout() {
  var day = todayDay();
  if (!day || day.tip === 'dinlenme') return;
  openSession(day);
}

function openSession(day) {
  _session = buildSession(day);
  document.getElementById('sv-title').textContent =
    (day.odak || __t('training.session'));
  renderSession();
  var v = document.getElementById('session-view');
  v.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeSession() {
  document.getElementById('session-view').classList.remove('open');
  document.body.style.overflow = '';
  stopRestTimer();            // Task 5 (safe no-op until defined)
  _session = null;            // discard ephemeral state
}

function renderSession() {
  if (!_session) return;
  var body = document.getElementById('sv-body');
  body.innerHTML = _session.exercises.map(function (ex, ei) {
    var rows = ex.sets.map(function (st, si) {
      return '<div class="set-row' + (st.done ? ' is-done' : '') +
          (st.isPR ? ' is-pr' : '') + '" data-ex="' + ei + '" data-set="' + si + '">' +
        '<div class="set-idx">' + (si + 1) + '</div>' +
        '<input class="set-input" type="number" inputmode="decimal" min="0" step="0.5" ' +
          'placeholder="kg" data-field="weight" value="' + (st.weightKg == null ? '' : st.weightKg) + '">' +
        '<input class="set-input" type="number" inputmode="numeric" min="0" step="1" ' +
          'placeholder="reps" data-field="reps" value="' + (st.reps == null ? '' : st.reps) + '">' +
        '<button class="set-check" data-field="done" aria-label="' + __t('training.set_done') + '">✓' +
          '<span class="pr-badge badge badge-warning">PR</span></button>' +
      '</div>';
    }).join('');
    return '<div class="exercise-card"><div class="ec-head">' +
      '<span class="ec-name">' + esc(ex.isim) + '</span>' +
      '<span class="ec-prescribed">' + ex.sets.length + '×' + esc(ex.tekrar) +
        ' · ' + esc(ex.dinlenme) + '</span></div>' +
      (ex.not ? '<div class="ec-note">' + esc(ex.not) + '</div>' : '') +
      '<div class="set-list">' +
        '<div class="set-row set-head"><div class="set-idx"></div>' +
        '<div class="set-col-label">' + __t('training.weight') + '</div>' +
        '<div class="set-col-label">' + __t('training.reps') + '</div><div></div></div>' +
        rows + '</div></div>';
  }).join('');
  updateSessionProgress();
}

function updateSessionProgress() {
  var s = computeSessionStats(_session);
  document.getElementById('sv-count').textContent = s.setsDone + '/' + s.totalSets;
  var pct = s.totalSets ? (s.setsDone / s.totalSets) * 100 : 0;
  document.getElementById('sv-progress-bar').style.width = pct + '%';
}

function finishSession() {
  if (!_session) return;
  _pendingStats = computeSessionStats(_session);
  document.getElementById('session-view').classList.remove('open');
  stopRestTimer();
  openPumpCheck();            // existing flow; on success → showCelebration (Task 6)
}
```

- [ ] **Step 4: Scoped input/click listener (updates `_session`)**

Bind once (in the bootstrap init) a delegated listener on `#sv-body`:

```javascript
(function initSession() {
  var body = document.getElementById('sv-body');
  if (!body) return;
  body.addEventListener('input', function (e) {
    var row = e.target.closest('.set-row'); if (!row || !_session) return;
    var ex = _session.exercises[+row.dataset.ex]; var st = ex && ex.sets[+row.dataset.set];
    if (!st) return;
    var field = e.target.dataset.field;
    if (field === 'weight') { st.weightKg = e.target.value === '' ? null : parseFloat(e.target.value);
      st.isPR = evaluatePR(ex.isim, st.weightKg); refreshPRFlags(ex, +row.dataset.ex); }  // Task 5
    else if (field === 'reps') { st.reps = e.target.value === '' ? null : parseInt(e.target.value, 10); }
  });
  body.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-field="done"]'); if (!btn || !_session) return;
    var row = btn.closest('.set-row');
    var ex = _session.exercises[+row.dataset.ex]; var st = ex.sets[+row.dataset.set];
    st.done = !st.done;
    row.classList.toggle('is-done', st.done);
    updateSessionProgress();
    if (st.done) startRestTimer(parseRestSeconds(ex.dinlenme));   // Task 5
  });
})();
```

Add a temporary stub so Task 4 runs before Task 5 lands:
```javascript
function evaluatePR() { return false; }
function refreshPRFlags() {}
function parseRestSeconds() { return 60; }
function startRestTimer() {}
function stopRestTimer() {}
```
(These get their real bodies in Task 5.)

- [ ] **Step 5: Day preview (read-only)**

```javascript
function openDayPreview(day) {
  document.getElementById('dp-title').textContent = dayLabel(day.gun) + ' — ' + (day.odak || '');
  document.getElementById('dp-body').innerHTML = (day.egzersizler || []).map(function (e) {
    return '<div class="exercise-card"><div class="ec-head"><span class="ec-name">' +
      esc(e.isim) + '</span><span class="ec-prescribed">' + e.set + '×' + esc(e.tekrar) +
      ' · ' + esc(e.dinlenme) + '</span></div>' +
      (e.not ? '<div class="ec-note">' + esc(e.not) + '</div>' : '') + '</div>';
  }).join('');
  document.getElementById('day-preview').classList.add('open');
}
function closeDayPreview() { document.getElementById('day-preview').classList.remove('open'); }
```

- [ ] **Step 6: `node --check` + manual**

Run: `node --check static/training.js` → valid.
Manual: Start Workout → overlay lists today's exercises as cards; each set row has weight+reps inputs + a done check; typing updates values; checking a set greys the row and advances the progress count/bar; Finish → Pump Check modal opens. Tap a non-today week chip → read-only preview sheet.

- [ ] **Step 7: Commit**

```bash
git add templates/training.html static/training.js
git commit -m "Add workout session player: state module, exercise cards, set rows"
```

---

### Task 5: Rest timer + PR indicators (layer onto set rows)

**Files:**
- Modify: `static/training.js` (replace the Task 4 stubs), `templates/training.html` (rest-timer widget)

**Interfaces:**
- Produces: `parseRestSeconds(dinlenme)`, `startRestTimer(sec)`, `stopRestTimer()`, `addRest(delta)`, `skipRest()`, `evaluatePR(name, weightKg)`, `refreshPRFlags(ex, exIdx)`, `sessionTopSetIndex(ex)`, and the `prProvider` seam.

- [ ] **Step 1: Rest-timer widget markup**

Add near the session overlay:

```html
<div class="rest-timer" id="rest-timer" role="status" aria-live="polite">
  <button class="rt-btn" data-action="addRest" data-args='[-15]'>-15</button>
  <span class="rt-time" id="rt-time">0:00</span>
  <button class="rt-btn" data-action="addRest" data-args='[15]'>+15</button>
  <button class="rt-btn" data-action="skipRest">{{ t('training.skip') }}</button>
</div>
```

- [ ] **Step 2: Rest-timer logic (replace stubs)**

```javascript
var _rest = { id: null, remaining: 0 };

function parseRestSeconds(dinlenme) {
  var s = String(dinlenme || '').toLowerCase();
  var nums = s.match(/\d+/g);
  var n = nums && nums.length ? parseInt(nums[nums.length - 1], 10) : 60;   // "60-90 sn" -> 90
  if (s.indexOf('dk') >= 0 || s.indexOf('min') >= 0) n *= 60;
  return Math.max(5, Math.min(n, 600));
}

function _fmtRest(sec) {
  var m = Math.floor(sec / 60), s = sec % 60;
  return m + ':' + (s < 10 ? '0' : '') + s;
}

function startRestTimer(sec) {
  stopRestTimer();
  _rest.remaining = sec;
  var el = document.getElementById('rest-timer');
  document.getElementById('rt-time').textContent = _fmtRest(sec);
  el.classList.add('open');
  _rest.id = setInterval(function () {
    _rest.remaining -= 1;
    if (_rest.remaining <= 0) { stopRestTimer();
      if (navigator.vibrate) { try { navigator.vibrate(120); } catch (e) {} }
      return; }
    document.getElementById('rt-time').textContent = _fmtRest(_rest.remaining);
  }, 1000);
}
function stopRestTimer() {
  if (_rest.id) { clearInterval(_rest.id); _rest.id = null; }
  var el = document.getElementById('rest-timer');
  if (el) el.classList.remove('open');
}
function addRest(delta) {
  if (!_rest.id) return;
  _rest.remaining = Math.max(1, Math.min(_rest.remaining + delta, 600));
  document.getElementById('rt-time').textContent = _fmtRest(_rest.remaining);
}
function skipRest() { stopRestTimer(); }
```

- [ ] **Step 3: PR provider seam + evaluation (replace stubs)**

```javascript
// ── PR provider seam. No persistence this phase → returns null (no false PRs).
//    WORKOUT-PERSIST-HOOK: swap NullPrProvider for a backend-backed provider
//    (e.g. GET /workout/history/best?exercise=) when Workout History ships. ──
var prProvider = { getBest: function (exerciseName) { return null; } };  // {weightKg}|null

function evaluatePR(exerciseName, weightKg) {
  var w = Number(weightKg) || 0;
  if (w <= 0) return false;
  var best = prProvider.getBest(exerciseName);
  return best && typeof best.weightKg === 'number' ? w > best.weightKg : false;
}

// In-session "top set": heaviest done set of this exercise gets a ★.
function sessionTopSetIndex(ex) {
  var best = -1, idx = -1;
  ex.sets.forEach(function (st, i) {
    var w = st.done ? (Number(st.weightKg) || 0) : -1;
    if (w > best) { best = w; idx = (best > 0 ? i : -1); }
  });
  return idx;
}

function refreshPRFlags(ex, exIdx) {
  var top = sessionTopSetIndex(ex);
  var rows = document.querySelectorAll('#sv-body .set-row[data-ex="' + exIdx + '"]');
  ex.sets.forEach(function (st, i) {
    var row = rows[i]; if (!row) return;
    row.classList.toggle('is-pr', !!st.isPR);
    row.classList.toggle('top-set', i === top);
  });
}
```

> Note: `rows` from `querySelectorAll` includes the `.set-head` row. Guard by
> selecting only data-set rows: use selector `.set-row[data-ex="..."][data-set]`.
> Update the selector accordingly in this step.

- [ ] **Step 4: `node --check` + manual**

Run: `node --check static/training.js` → valid.
Manual: check a set → rest timer appears counting down from the exercise's prescribed rest; ±15 and Skip work; timer auto-hides at 0. Enter a heavy weight + mark done → that set shows the ★ top-set marker. (PR badge stays hidden — `prProvider` returns null by design; confirm no false PRs.)

- [ ] **Step 5: Commit**

```bash
git add templates/training.html static/training.js
git commit -m "Add rest timer + PR/top-set indicators to session player"
```

---

### Task 6: Completion + Celebration (restyle Pump Check + XP count-up + summary)

**Files:**
- Modify: `templates/training.html` (pump modal token classes + celebration overlay)
- Modify: `static/training.js` (`submitPumpCheck` success → `showCelebration`; XP animation)

**Interfaces:**
- Consumes: `POST /workout/complete` response (`{message, points_awarded, new_total, level, title, …}`), `_pendingStats` (Task 4), `__t`.
- Produces: `showCelebration(xpResp, stats)`, `closeCelebration()`, `animateXP(el, target)`.

- [ ] **Step 1: Celebration overlay markup**

```html
<div class="celebration" id="celebration" role="dialog" aria-modal="true" aria-labelledby="cel-title">
  <div class="cel-title" id="cel-title">{{ t('training.workout_complete') }}</div>
  <div class="cel-xp"><span id="cel-xp">0</span> XP</div>
  <div class="cel-level" id="cel-level"></div>
  <div class="cel-summary" id="cel-summary"></div>
  <button class="btn-volt" data-action="closeCelebration">{{ t('training.done') }}</button>
</div>
```

- [ ] **Step 2: Restyle the pump modal onto tokens**

In `templates/training.html`, the pump modal keeps its ids/structure and flow; migrate its page `<style>` `--volt`/`--surface-2`/`--r-*` rules to canonical tokens in `training.css` (`.pump-*` selectors). No JS/flow change — only visual tokens. (Camera/scrim `rgba` is the documented overlay exception.)

- [ ] **Step 3: XP count-up + celebration**

```javascript
function animateXP(el, target) {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || !target) { el.textContent = target || 0; return; }
  var start = null, dur = 900;
  function frame(ts) {
    if (start == null) start = ts;
    var p = Math.min((ts - start) / dur, 1);
    var eased = 0.5 - Math.cos(p * Math.PI) / 2;         // easeInOutSine
    el.textContent = Math.round(target * eased);
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function showCelebration(xpResp, stats) {
  var xp = (xpResp && xpResp.points_awarded) || 0;
  document.getElementById('cel-level').textContent =
    xpResp && xpResp.title ? xpResp.title + (xpResp.level ? ' · Lv ' + xpResp.level : '') : '';
  document.getElementById('cel-summary').innerHTML = stats ? (
    statCard(stats.totalVolume + ' kg', __t('training.volume')) +
    statCard(stats.setsDone + '/' + stats.totalSets, __t('training.sets')) +
    statCard(stats.exercisesDone, __t('training.exercises')) +
    statCard(stats.elapsedMin + ' ' + __t('training.min'), __t('training.duration'))
  ) : '';
  document.getElementById('celebration').classList.add('open');
  document.body.style.overflow = 'hidden';
  animateXP(document.getElementById('cel-xp'), xp);
}

function closeCelebration() {
  document.getElementById('celebration').classList.remove('open');
  document.body.style.overflow = '';
  _session = null; _pendingStats = null;      // discard ephemeral state
  renderHero(activePlan, true);               // repaint hero as completed
}
```

- [ ] **Step 4: Hook Pump Check success → celebration**

In `submitPumpCheck`, in the `if (res.ok || already)` branch, after `markWorkoutCompleted()` and the completion-cache write, replace `closePumpCheck()` with:

```javascript
        closePumpCheck();
        showCelebration(res.ok ? data : null, _pendingStats);
```

(Keep `markWorkoutCompleted()` + the `fitx_workout_completed_*` cache write untouched.)

- [ ] **Step 5: `node --check` + manual**

Run: `node --check static/training.js` → valid.
Manual: complete a session → Finish → Pump Check → submit a valid photo → celebration overlay: XP counts up from 0 to `points_awarded`, level/title shows, summary tiles show volume/sets/exercises/duration → Done returns to a "completed" hero. Reduced-motion: XP shows final value instantly.

- [ ] **Step 6: Commit**

```bash
git add templates/training.html static/training.js
git commit -m "Add XP celebration + restyle pump check on canonical tokens"
```

---

### Task 7: Setup form restyle (token migration; preserve payload + flow)

**Files:**
- Modify: `templates/training.html` (setup-form markup), `static/training.js` (chip class names only), `static/training.css` (already has `.tw-chip`)

**Interfaces:** none new — visual token migration. `selections` payload, injury picker logic, generate/preview/save flow unchanged.

- [ ] **Step 1: Migrate option chips + sections**

In `createOptionChip`/`makeInjuryChip`, rename emitted class `option-chip`→`tw-chip`, `option-chip-dot`→`tw-chip-dot`, `option-chip-text`→`tw-chip-text`, `option-chip-sub`→`tw-chip-sub` (and update the `querySelectorAll('#grid .option-chip')` selectors to `.tw-chip`). Section titles use `.sec-label`; the info banner + injury input use `.card`/`.fc-input`. No logic change.

- [ ] **Step 2: Migrate the preview (score banner, weekly grid, detail)**

Restyle `renderResults` output: score → a `.card` with `.stat-card`/`.pbar-*` for intensity/balance/fit (reuse the `statCard` helper + `.pbar-track/.pbar-fill`); the preview weekly grid reuses `.week-strip`/`.week-chip`; the detail exercise list reuses the `.exercise-card` read-only markup (same as `openDayPreview`). Keep `currentPlan`/`currentScore` + `savePlan()` intact.

- [ ] **Step 3: Remove dead FAB**

Delete the bottom-left `#quick-add-wrap` markup and its handlers (`toggleQuickAdd`, `fxOpenSetupForm`, `fxTriggerFinish`, the outside-click closer, `quickAddOpen`) — superseded by the hero Start/Finish. Keep `showToast`.

- [ ] **Step 4: Token sanity + `node --check` + manual**

Run: `grep -nE "\-\-volt" templates/training.html static/training.css` → no matches (page `<style>` block should now be empty or removed; all rules live in `training.css`).
Run: `node --check static/training.js` → valid.
Manual (no saved plan / after reset): configure options → Generate → preview renders on tokens → Save → switches to the new active-plan hero view. Injury chips + free-text still drive the payload.

- [ ] **Step 5: Commit**

```bash
git add templates/training.html static/training.js static/training.css
git commit -m "Restyle training setup form + preview on canonical tokens; drop dead FAB"
```

---

### Task 8: i18n keys (TR/EN)

**Files:**
- Modify: `locales/tr.json`, `locales/en.json`
- Modify: `tests/test_i18n.py` (if it enumerates training keys)

**Interfaces:** new `training.*` keys used by Tasks 3–7.

- [ ] **Step 1: Add keys to both locales**

Keys (TR canonical display values; EN translations): `training.start_workout`, `this_week`, `active_recovery`, `workout` , `exercises`, `min`, `workout_done_label`, `off`, `session`, `finish_workout`, `set_done`, `weight`, `reps`, `skip`, `workout_complete`, `done`, `volume`, `sets`, `duration`, `close`. Reuse existing `training.rest_day`, `training.reset_plan`, `training.workout_day`, `training.weekly_cal`, `training.total_min`, `training.minutes` where already present (grep first to avoid dupes).

Example (`tr.json`):
```json
"training.start_workout": "Antrenmana Başla",
"training.this_week": "Bu Hafta",
"training.active_recovery": "Aktif Toparlanma",
"training.exercises": "hareket",
"training.min": "dk",
"training.session": "Seans",
"training.finish_workout": "Antrenmanı Bitir",
"training.weight": "Ağırlık",
"training.reps": "Tekrar",
"training.skip": "Geç",
"training.workout_complete": "Antrenman Tamamlandı",
"training.volume": "Hacim",
"training.sets": "Set",
"training.done": "Tamam"
```
Example (`en.json`): `"training.start_workout": "Start Workout"`, `"training.this_week": "This Week"`, `"training.finish_workout": "Finish Workout"`, `"training.weight": "Weight"`, `"training.reps": "Reps"`, `"training.skip": "Skip"`, `"training.volume": "Volume"`, `"training.sets": "Sets"`, `"training.done": "Done"`, etc.

- [ ] **Step 2: Parity + full suite**

Run: `python -m pytest tests/test_i18n.py -q`
Expected: PASS (TR/EN key parity holds — add missing counterparts if the test flags them).

- [ ] **Step 3: Commit**

```bash
git add locales/tr.json locales/en.json tests/test_i18n.py
git commit -m "Add i18n keys for workout redesign"
```

---

### Task 9: Polish + regression tests (a11y, skeleton/empty, reduced-motion, responsive)

**Files:**
- Modify: `static/training.css`, `templates/training.html`, `static/training.js`
- Create: `tests/test_training_ui.py`

**Interfaces:** none new — hardening + tests.

- [ ] **Step 1: Render/regression test**

Create `tests/test_training_ui.py` (mirror the login/seed helper pattern from existing tests; if the suite has an authed-client fixture, use it):

```python
def test_training_renders_hero_and_session(auth_client):
    r = auth_client.get("/training")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # new canonical markup present
    assert 'id="workout-hero"' in html
    assert 'id="session-view"' in html
    assert 'id="celebration"' in html
    assert 'data-action="startWorkout"' in html
    # no legacy volt styling leaked into the page
    assert '--volt' not in html


def test_training_loads_external_assets(auth_client):
    html = auth_client.get("/training").get_data(as_text=True)
    assert '/static/training.js' in html
    assert '/static/training.css' in html
```

> If no `auth_client` fixture exists, reuse the test-client login/CSRF-bypass
> helper from `tests/test_nutrition_routes.py` (or the training test if present)
> and inline the setup.

Run: `python -m pytest tests/test_training_ui.py -q`
Expected: PASS (fix markup if an assertion points at a gap).

- [ ] **Step 2: A11y + touch targets**

Ensure: `#session-view`, `#celebration`, `#day-preview`, pump modal have `role="dialog"`/`aria-modal="true"`; focus moves into the overlay on open and returns to the trigger on close; `Esc` closes each (extend the existing pump `keydown` handler to cover session/celebration/day-preview); week chips + set checks + rt buttons are ≥44px; `:focus-visible` rings via `--focus-ring`; the done check and inputs have `aria-label`s. Add a lightweight focus-trap on the session overlay (cycle Tab within `#session-view`).

- [ ] **Step 3: Loading skeleton + empty states**

- While `/training-plan/active` and `/last-session` are in flight, show `.skeleton` blocks in the hero/stats area.
- No saved plan → the setup form is the empty state (already the default); ensure a clean `.empty-state` intro at the top of the setup form.
- Session with a day that has zero exercises can't happen (validator guarantees ≥1), but guard `renderSession` against an empty list with an `.empty-state`.

- [ ] **Step 4: Reduced-motion + responsive**

- Confirm `@media (prefers-reduced-motion: reduce)` disables the XP count-up (done in Task 6), progress-bar transition, and any celebration animation.
- Verify at 360 / 768 / 1024px: no horizontal overflow; hero stacks <560px; week strip 7→4 cols <420px; session set-row stays tappable; rest timer clears the action bar; celebration centers. Fix with the documented breakpoint tokens (`--bp-*` values, not CSS vars in `@media`).

- [ ] **Step 5: Full verification**

Run: `node --check static/training.js`
Run: `python -m pytest -q`
Expected: all green. Then manually walk: setup→generate→save; hero (workout/rest/completed variants); Start→set entry→rest timer→top-set→finish→pump check→celebration→Done; EN locale; 360px width. Confirm `git diff --stat` shows **no `app/` changes**.

- [ ] **Step 6: Commit**

```bash
git add static/training.css templates/training.html static/training.js tests/test_training_ui.py
git commit -m "Workout a11y, skeletons, empty states, responsive polish + render tests"
```

---

### Task 10: Handoff doc

**Files:**
- Create: `docs/archive/handoff-2026-07-06-phase4-nutrition.md` (move current `docs/handoff.md`)
- Rewrite: `docs/handoff.md`

- [ ] **Step 1: Archive Phase 4 handoff + write Phase 5 (Workout) handoff**

`git mv docs/handoff.md docs/archive/handoff-2026-07-06-phase4-nutrition.md`, then write a new `docs/handoff.md` per the phase-5 "End" checklist: Completed work, Files modified, Components created/refactored, Architectural decisions (in-memory session, `prProvider`/`WORKOUT-PERSIST-HOOK` seam, no-backend/no-localStorage), Remaining tasks, Known issues, Next recommended steps (Progress surface next), plus the quality review (Responsiveness / Accessibility / Visual consistency / Code maintainability / Reusability / Performance / UX clarity). Flag any weak metric + follow-up. State clearly that persistent workout logging is deferred and the seam is ready.

- [ ] **Step 2: Commit**

```bash
git add docs/handoff.md docs/archive/handoff-2026-07-06-phase4-nutrition.md docs/superpowers/plans/2026-07-06-phase5-workout-redesign.md
git commit -m "Phase 5 workout redesign handoff"
```

---

## Self-Review

**Spec coverage:**
- Today's Workout Hero → Task 3. Exercise Cards / Details → Task 4 (+ read-only preview). Sets / Reps / Weights → Task 4 (set rows, in-memory). Rest Timer → Task 5. PR Tracking → Task 5 (`prProvider` seam + in-session top-set). Notes → Task 4 (`ec-note` from `not`). Workout Completion → Task 6 (Pump Check preserved). XP Animation / Celebration → Task 6. Whole-page token migration (setup form) → Task 7. i18n → Task 8. Animation/loading/skeleton/micro-interaction/a11y/keyboard/responsive/performance → Task 9. Handoff → Task 10. Foundation/refactor (external JS + CSS, dead-FAB removal) → Tasks 1/2/7. Persistence seam / no-backend / no-localStorage → Tasks 4/5 (architecture). All spec sections covered.

**Placeholder scan:** No "TBD/TODO". The two `>` notes (fixture reuse in Task 9, `querySelectorAll` set-row selector guard in Task 5) are explicit "do X precisely" instructions with concrete surrounding code, not deferrals. Task 4 ships intentional stubs that Task 5 replaces — each task is independently runnable/green.

**Type consistency:** `_session` shape (`{startedAt, day, exercises:[{isim,tekrar,dinlenme,not,sets:[{weightKg,reps,done,isPR}]}]}`) is identical across Tasks 4/5/6. `computeSessionStats() -> {totalVolume,setsDone,totalSets,prCount,exercisesDone,elapsedMin}` produced in Task 4, consumed in Task 6 `showCelebration(stats)`. `statCard(v,label)` defined in Task 3, reused in Tasks 6/7. `evaluatePR(name, weightKg)`, `startRestTimer(sec)`/`stopRestTimer()`, `parseRestSeconds(dinlenme)` stubbed in Task 4, implemented with matching signatures in Task 5. `showCelebration(xpResp, stats)` uses `xpResp.points_awarded`/`.title`/`.level` — the exact keys `/workout/complete` returns (verified in `app/blueprints/training.py`). Class names in Task 2 CSS (`.workout-hero`, `.week-chip`, `.set-row`, `.set-input`, `.rest-timer`, `.celebration`, `.tw-chip`) match the markup emitted in Tasks 3–7.

**TDD deviation (intentional):** UI Tasks (3–7) verify via `node --check` + browser + Task 9 render tests rather than per-function unit tests — appropriate for a template/JS redesign with no backend change; the pure session helpers (`buildSession`/`computeSessionStats`/`parseRestSeconds`) are small and covered by the manual + render checks. Backend is untouched, so no backend tests change.
