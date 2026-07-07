# Phase 5 — Workout Redesign (Design Spec)

Date: 2026-07-06
Branch: `feat/phase5-workout` (off `origin/main` @ f411836, Phase 4 already merged/deployed)
Phase source: `phase-5.txt` (Workout section)
Previous phase: `docs/handoff.md` (Phase 4 nutrition)

Phase 5 is decomposed into independent surface cycles (Workout → Progress →
Profile → global polish/QA), each its own spec → plan → build → handoff. **This
spec covers surface 1: Workout (`/training`).**

## Context

`/training` is the app's workout surface. Today it is a two-state page still on
the **legacy `theme.css` `--volt` styling** (not the canonical AxisAI design
system that Phases 1–4 established), with ~1390 lines of inline template + inline
`<script>`:

1. **Setup form** — configure days/style/goal/equipment/focus/duration/cardio/
   injuries → AI-generate a plan → preview (score banner, weekly grid, exercise
   table, weekly summary) → save.
2. **Active-plan view** — score header, a thin "today" banner, a 7-day grid,
   a day-detail exercise **table** (Exercise / Set / Reps / Rest), weekly summary
   stats, and a **Finish Today's Workout** button that opens the **Pump Check**
   modal (photo → AI validate → XP), with feed/friends sharing.

Phase 5 asks for a premium mobile workout experience: **Today's Workout Hero,
Exercise Cards, Sets, Reps, Weights, Rest Timer, PR Tracking, Notes, Workout
Completion, XP Animation, Celebration.** The current page has no rest timer, no
weight entry, no PR tracking, and only a static prescribed-plan table.

### Hard constraints (from `phase-5.txt` + user direction in `answer.txt`)

- **Preserve all existing backend logic.** No new endpoints, no schema/migration.
- **Weights + PR tracking:** build the *complete* UI (weight input, sets, reps,
  rest timer, PR indicators) as **reusable, state-driven components**, holding
  values in **in-memory session state only** — **no `localStorage`**, **no new
  backend**. Cleanly separate the UI layer from a future persistence layer so a
  Workout Log backend plugs in later with minimal change. At session end: compute
  stats in memory, show completion (XP + summary), then **discard** the state.
- **Reuse canonical components**; refactor only where it serves consistency/
  maintainability. Production-safe, incremental, mobile-first.

### Backend contract to preserve (verified in `app/blueprints/training.py`)

| Call | Shape |
|---|---|
| `GET /last-session` | `{exists, goal, fitness_level, target_calories, …}` → info banner |
| `POST /training-plan` | body `selections` → `{program, overall_score, score_label, haftalik_ozet}` (AI, premium-gated) |
| `POST /training-plan/save` | body `{plan, score}` → replaces stored plan |
| `GET /training-plan/active` | `{exists, plan[], score, created_at}` |
| `GET /workout/status` | `{completed}` (per-day, cross-device) |
| `POST /workout/complete` | body `{image(b64), location_type, description, visibility, shared_friend_ids}` → `{message, points_awarded, pump_bonus, new_total, level, title, …}` or 422/400 `{error, code}` |
| `GET /friends/select-list?q=` | `{friends:[{id, username}]}` (pump friend picker) |

**Plan/exercise JSON** (per `response_validator.py`): day = `{gun (TR weekday),
tip (antrenman|dinlenme|kardiyo), odak, sure_dk, tahmini_kalori, egzersizler[]}`;
exercise = `{isim, set (int), tekrar (str "8-12"), dinlenme (str "90 sn"),
not (coaching note)}`. **No weight field** — weights are user-entered at runtime.

## Goals

Deliver a premium, mobile-first workout experience on the canonical design
system, with a fully-built weight/sets/reps/rest-timer/PR **UI** driven by
ephemeral in-memory session state and a clean seam for future persistence —
without changing any backend or introducing storage tech debt.

## Approach

### 1. Foundation & refactor
- **New `static/training.css`** — canonical tokens only (`--color-*`,
  `--space-*`, `--radius-*`, `--text-*`, `--duration-*`, `--ease-*`), mirroring
  `nutrition.css`. Loaded after `tokens.css`/`components.css`/`theme.css`/
  `nav.css` (keep theme+nav for shell, as Phase 3/4 did). Remove page-specific
  `--volt` rules from the template `<style>`.
- **Extract inline JS → `static/training.js`** (like `nutrition.js`): improves
  maintainability, enables `node --check`, and gives the session layer a real
  module boundary. Template keeps only a tiny inline bootstrap (`window.LOCALE`,
  server-injected `injuries`, canonical i18n maps) under `nonce="{{ csp_nonce }}"`.
- **Remove dead code:** the bottom-left quick-add FAB (`toggleQuickAdd`,
  `fxOpenSetupForm`, `fxTriggerFinish`) — superseded by hero Start/Finish
  (Phase 4 removed its dead FAB similarly).

### 2. State A — active plan (active-first)
- **`.workout-hero`** — today's session: focus (`odak`), exercise count, est.
  duration/calories, a progress ring (reuse `.ring-*`, 0% until started), and a
  primary **Start Workout** CTA. Variants: **rest day** (calm "active recovery"),
  **already completed today** (checkmark + day summary + earned state, driven by
  `GET /workout/status`).
- **`.week-strip`** — compact 7-day overview (today highlighted); tap a
  non-today day → read-only exercise preview (sheet/inline). Replaces the heavy
  `.weekly-grid` while keeping weekly visibility.
- **Weekly stats** — reuse `.stat-card` for workout-days / weekly-kcal /
  total-min (same computed values as today).
- **Plan meta** — program score (reuse badge/ring), created date, reset plan.

### 3. Session Mode — the premium interactive player (opened by Start Workout)
A focused, full-screen overlay (uses `--z-overlay`, reuses sheet/modal shell
patterns) so the workout has minimal distraction and big touch targets.

- **`.exercise-card`** per exercise — name + coaching note (`not`); prescribed
  sets × reps · rest.
- **`.set-row`** per prescribed set — set #, **weight (kg) input**, **reps
  input** (pre-filled from `tekrar`, editable), **done** toggle. Checking done →
  starts the **rest timer** and marks the set complete.
- **`.rest-timer`** — countdown parsed from `dinlenme` (e.g. "90 sn" → 90s);
  ±15s, skip, auto-dismiss at 0 (optional `navigator.vibrate`, reduced-motion
  aware). Pure `setInterval`, no libraries.
- **`.pr-badge`** — lights when a set's weight beats the exercise's previous
  best from a pluggable `prProvider.getBest(name)`. **Current impl returns
  `null`** (no persistence → no false PRs); an in-session "top set" flag still
  highlights the session's heaviest set. `WORKOUT-PERSIST-HOOK` comment marks the
  future backend seam.
- **Session progress** — header shows completed/total sets + a `.pbar-*` bar.
- **Finish Workout** → `computeSessionStats(session)` in memory (total volume =
  Σ weight×reps, sets done, exercises completed, PR count, elapsed) → opens the
  existing **Pump Check** modal.

**Session state architecture (the persistence seam):**
```
// ── ephemeral, in-memory only. No localStorage, no network. ──
session = {
  startedAt: <ts>,
  day: <plan day object>,
  exercises: [{ isim, tekrar, dinlenme, not,
    sets: [{ weightKg: null, reps: <from tekrar>, done: false, isPR: false }] }],
}
```
- Pure helpers: `buildSession(day)`, `computeSessionStats(session)`,
  `evaluatePR(exerciseName, weightKg, prProvider)`.
- `prProvider` interface `{ getBest(exerciseName) → {weightKg} | null }`;
  `NullPrProvider` today. // WORKOUT-PERSIST-HOOK: swap for a backend-backed
  provider + `POST /workout/session` when Workout History ships.
- On finish/close: after stats are consumed by completion/celebration,
  `session = null` (state discarded). Nothing persisted.

### 4. Completion + Celebration
- **Finish → Pump Check modal** (existing markup/flow restyled on canonical
  tokens): photo, location, description, feed/friends sharing + friend picker,
  submit → `POST /workout/complete`. Preserve the `already_completed` code path,
  422/400 error handling, and progress states.
- **On success → `.celebration`** screen: **XP count-up animation** from
  `points_awarded`, level/title, and the in-memory **session summary** (volume,
  sets, PRs, duration). Tasteful CSS-only accent (confetti via CSS, static under
  `prefers-reduced-motion`). Reuse `.badge-*`/`.stat-card`. Then return to the
  "completed" hero variant.

### 5. State B — no plan (setup form, token migration + polish)
- Same configuration flow restyled on canonical tokens/components: option grids →
  `.chip`/`.card`, section headers → `.sec-label`, buttons → `.btn-*`, preview
  score → `.stat-card`/`.pbar-*`. **Preserve exactly**: the `selections` payload,
  the injury multi-select picker (free-text = source of truth; canonical TR
  labels), the generate → preview → save flow, and score/label coloring.

### 6. i18n
- Add `training.*` keys for all new UI (hero, session mode, set/weight/reps
  labels, rest timer, PR, celebration). Update `locales/tr.json` + `en.json`.
  Keep **canonical values TR** (day/injury/goal/score keys) — display-only
  translation via `__t`/`_EN`/`injuryLabel`/`dayLabel`, per the i18n coupling rule.

## Components created (page-scoped → `training.css`)
`.workout-hero` (+ rest/done variants), `.week-strip`/`.week-chip`,
`.session-view`/`.session-head`/`.session-progress`, `.exercise-card`,
`.set-row`/`.set-input`/`.set-check`, `.rest-timer`, `.pr-badge`,
`.celebration`/`.xp-count`. Reuse from `components.css`: `.ring-*`, `.pbar-*`,
`.stat-card`, `.badge-*`, `.chip`, `.card`, `.sheet-*`/`.modal-*`,
`.empty-state`, `.skeleton`, `.btn-*`, `.sec-label`, `.avatar`, `.toast-*`.
(Rest timer / PR badge / steppers stay page-scoped now; promote to
`components.css` only if reused by a later surface — per design-system guidance.)

## Non-goals (explicitly deferred)
- Persistent workout logging / cross-device weight & PR history (future
  Workout History phase; the `WORKOUT-PERSIST-HOOK` seam is prepared for it).
- Any change to plan generation, scoring, injury-constraint, or Pump Check
  backend logic.
- Progress and Profile surfaces (later Phase 5 cycles).

## Verification
- `node --check static/training.js`.
- Every class in `training.html` resolves in `tokens`/`components`/`theme`/
  `training` css; no `--volt`/raw-hex left in the workout surface (document any
  single unavoidable exception, e.g. an overlay scrim).
- i18n render + canonical-coupling assertions (extend `tests/test_i18n.py`);
  add `tests/test_training_ui.py` render checks (hero, session markup, new keys).
- Full `pytest` green; `flask run` manual pass of: setup→generate→save,
  active-plan render, start session → set entry → rest timer → finish → pump
  check → celebration, rest-day + already-completed hero variants, EN locale.
- Backend untouched (git diff shows no `app/` route changes beyond none).

## Risks / mitigations
- **Big rewrite of a live surface** → keep every backend call + payload
  identical; preserve i18n canonical coupling; land behind the same routes.
- **Session state confusion** → single `WorkoutSession` object, pure helpers,
  state nulled on finish; no persistence paths at all this phase.
- **CSP** → all inline blocks carry `nonce`; no external libs (timer/celebration
  are vanilla JS + CSS).
- **Pre-existing completion-cache `localStorage`** (`fitx_workout_completed_*`)
  is a *distinct*, pre-existing cross-device optimization tied to
  `/workout/status`; it is preserved as-is and is **not** the forbidden
  session-state storage.
