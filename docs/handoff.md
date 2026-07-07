# Phase 5 Handoff — AxisAI V2 Workout Redesign (Surface 1)

Date: 2026-07-07
Branch: `feat/phase5-workout` (off `origin/main` @ f411836; Phase 4 already merged/deployed)
Spec: `docs/superpowers/specs/2026-07-06-phase5-workout-redesign-design.md`
Plan: `docs/superpowers/plans/2026-07-06-phase5-workout-redesign.md`
Previous phase: `docs/archive/handoff-2026-07-06-phase4-nutrition.md`

Phase 5 (`phase-5.txt`) was decomposed into independent surface cycles — **Workout
→ Progress → Profile → global polish/QA** — each its own spec → plan → build →
handoff. **This handoff covers surface 1: Workout (`/training`).** Progress,
Profile, and the app-wide FINAL QA remain as separate follow-up cycles.

## Completed Work

`/training` is rebuilt into a premium, mobile-first workout experience on the
canonical AxisAI design system, replacing the legacy `--volt`/`theme.css`-styled
page. The old ~1390-line template (inline `<style>` + inline `<script>`) is now a
286-line template + external `static/training.js` (1064 lines) + `static/training.css`
(351 lines). **No backend changed** — every route, payload, and the Pump-Check flow
is byte-for-byte preserved.

**Active-plan view (active-first):**
- **Today's Workout Hero** (`.workout-hero`) — today's focus, exercise count, est.
  duration/calories, an SVG progress ring (reuses `.ring-*`), and a **Start Workout**
  CTA. Variants: rest-day (calm "active recovery") and already-completed (✓ badge,
  driven by `GET /workout/status` + the pre-existing `fitx_workout_completed_*` cache).
- **This-week strip** (`.week-strip`) — compact 7-day overview, today highlighted;
  tapping a non-today day opens a read-only exercise preview sheet (`#day-preview`).
- **Weekly stats** (`.wstats`) — reuses `.stat-card` (workout days / weekly kcal /
  total min).

**Session player** (`#session-view`, opened by Start Workout) — a focused full-screen
overlay:
- **Exercise cards** (`.exercise-card`) — name + coaching note (`not`), prescribed
  sets × reps · rest.
- **Set rows** (`.set-row`) — per set: **weight (kg)** input, **reps** input
  (pre-filled from `tekrar`), a **done** toggle. Checking a set fires the **rest timer**.
- **Rest timer** (`.rest-timer`) — countdown parsed from `dinlenme` (e.g. "60-90 sn"
  → 90s), ±15s / skip, auto-hide at 0, guarded `navigator.vibrate`. Pure `setInterval`,
  no libraries.
- **PR indicators** (`.pr-badge` / `.top-set` ★) — wired to a pluggable
  `prProvider.getBest(name)` that returns `null` this phase (no persistence → **no
  false PRs**); an in-session "top set" ★ marks the heaviest done set.
- **Session progress** — completed/total sets + a `.sv-progress` bar.
- **Finish** → `computeSessionStats()` (in-memory volume Σ w×reps, sets, PRs, elapsed)
  → the existing **Pump Check** modal.

**Completion + Celebration:**
- Pump Check flow (photo → AI validate → XP, incl. feed/friends sharing) unchanged.
- On success → **`.celebration`** screen: **XP count-up animation** (`animateXP`,
  reduced-motion aware), level/title, and the in-memory session summary
  (volume/sets/exercises/duration). On close, ephemeral state is discarded and the
  hero repaints as completed.

**Setup form** — the plan-generation flow (days/style/goal/equipment/focus/cardio/
injuries → generate → preview → save) migrated onto canonical tokens/components; the
generated-plan preview now reuses `.stat-card`/`.pbar-*`/`.exercise-card`. The exact
`selections` payload, injury multi-select logic, and generate/save contract are
preserved. The dead bottom-left quick-add FAB was removed.

## Files Modified

- **Created:** `static/training.js`, `static/training.css`, `tests/test_training_ui.py`,
  the spec + plan docs (above).
- **Rewritten:** `templates/training.html` (1391 → 286 lines; inline `<style>` +
  `<script>` removed).
- **i18n:** `locales/tr.json`, `locales/en.json` (+17 new `training.*` keys each).
- **Tests:** `tests/test_i18n.py` + `tests/test_pump_check_sharing.py` (retargeted
  the canonical-coupling assertions to `static/training.js` after the JS extraction),
  `tests/test_training_ui.py` (new render assertions).
- **Docs:** `docs/handoff.md` (this), Phase 4 handoff archived.

## Components Created or Refactored

- **New page CSS (canonical tokens):** `.workout-hero`/`.wh-*`, `.week-strip`/
  `.week-chip`/`.wc-*`, `.wstats`, `.apv-meta-row`, `.session-view`/`.sv-*`,
  `.exercise-card`/`.ec-*`, `.set-row`/`.set-input`/`.set-check`/`.set-col-label`,
  `.pr-badge`/`.top-set`, `.rest-timer`/`.rt-*`, `.celebration`/`.cel-*`/`.xp-count`,
  `.tw-chip*`, plus the migrated setup-form + `.pump-*` rules.
- **Reused (no new copies):** `.ring-*`, `.pbar-*`, `.stat-card`, `.badge-*`, `.chip`,
  `.card`, `.sheet-*`, `.empty-state`, `.btn-*`, `.sec-label`, `.toast-*`.
- **New JS:** the ephemeral session module (`_session`, `buildSession`,
  `computeSessionStats`, `defaultReps`), `startWorkout`/`openSession`/`closeSession`/
  `renderSession`/`finishSession`, the rest-timer (`parseRestSeconds`/`startRestTimer`/
  `addRest`/`skipRest`), the PR seam (`prProvider`/`evaluatePR`/`sessionTopSetIndex`/
  `refreshPRFlags`), `renderHero`/`renderWeekStrip`/`renderWeekStats`, `animateXP`/
  `showCelebration`/`closeCelebration`, `openDayPreview`, Esc/focus-trap a11y.
- **Refactored:** the entire page JS extracted from inline into `static/training.js`
  (top-level globals, so `static/actions.js` `data-action` delegation still resolves
  them). **Removed (dead):** the bottom-left quick-add FAB and old active-plan render
  (`renderApvGrid`/`showApvDetail`/`showDetail`).

## Architectural Decisions

1. **Ephemeral in-memory session, zero persistence** — weight/reps/sets/PR state lives
   only in the `_session` object; **no `localStorage`, no new endpoints**. On finish
   the stats are computed in memory, shown, then discarded. (Per the user's explicit
   direction: build the full UI, defer storage, avoid temporary tech debt.)
2. **`prProvider` persistence seam** — `WORKOUT-PERSIST-HOOK` marks exactly where a
   backend-backed provider (`getBest(exercise)` + a session POST) plugs in when Workout
   History ships. Today it returns `null`, so PR badges never false-fire; the in-session
   ★ top-set still gives feedback.
3. **Client-side, offline-safe** — rest timer (`setInterval`), XP count-up
   (`requestAnimationFrame`), celebration (CSS) — no libraries, CSP-clean, no blocking
   AI on a hot path (mirrors Phase 3/4 precedent).
4. **Backend untouched** — no route/payload/schema/migration change; rollback-safe.
5. **JS extracted to an external module** — improves maintainability, enables
   `node --check`, and gives the session layer a real boundary. The canonical-coupling
   tests were retargeted from the rendered HTML to `static/training.js` accordingly.
6. **Full canonical-token migration** — the inline `<style>` block is gone; the whole
   surface uses `--color-*`/`--space-*`/`--radius-*`/… with **no `--volt`/raw hex**
   (the one carried-over exception is the `%23808080` select-arrow SVG data-URI, which
   can't reference CSS vars).

## Verification

- Full `pytest` suite: **1102 passed, 0 failures** (incl. the new `test_training_ui.py`
  and the retargeted coupling tests). `node --check static/training.js` clean.
- No `--volt`/raw hex/`rgba()` in `templates/training.html` or `static/training.css`.
- Every template class resolves in tokens/components/theme/training CSS.
- **Not yet done — manual browser QA on a running app** (see Known Issues): the
  interactive flows (session set-entry, rest timer, celebration, EN locale, responsive
  widths) were verified by `node --check` + render tests only, not driven in a browser.

## Remaining Tasks / Known Issues

- **Manual/live QA recommended** before merge/deploy: drive Start → set-entry → rest
  timer → top-set → finish → Pump Check → celebration → Done; the rest-day and
  already-completed hero variants; the setup→generate→save flow; EN locale; and
  360/768/1024px widths. No agent could exercise the browser.
- **Pump-cancel strands an in-progress session (minor UX):** `finishSession()` hides
  the session overlay and opens Pump Check; if the user cancels the pump check, the
  session overlay stays closed and `_session` lingers (harmless — overwritten on the
  next Start, GC'd on unload) but the in-progress entries aren't re-shown. A future
  polish could re-open the session on pump-cancel.
- **Persistent workout logging is deferred by design** — the `prProvider` /
  `WORKOUT-PERSIST-HOOK` seam is ready for it (Workout History + cross-device sync as a
  future additive-backend phase).
- **Loading skeleton deferred:** the hero lives inside `#active-plan-view` (hidden
  until `/training-plan/active` resolves), so a skeleton there was awkward; the setup
  form shows immediately with no blank flash. Revisit if a dedicated loading state is
  wanted.
- Score-badge color in `loadActivePlan()` still uses a JS hex literal (`#3D8BFF`…) —
  a documented JS color-literal exception (as in `nutrition.js`); could be tokenized
  later.

## Next Recommended Steps

1. Live-QA the workout flows on a running app (localhost or the deployed HTTPS site).
2. Merge `feat/phase5-workout` → `main` (health-gated EC2 deploy).
3. Start **Phase 5 surface 2: Progress** (`/progress-page`) as its own spec → plan →
   build cycle, then **surface 3: Profile**, then the app-wide **FINAL QA** pass.

## Quality Review

- **Responsiveness:** Strong (mobile-first; hero stacks <560px, week strip 7→4 cols
  <420px, session set-rows and rest timer clear the action bar). *Widths not visually
  verified — flagged for manual QA.*
- **Accessibility:** Strong. All overlays `role="dialog"`/`aria-modal`; Esc closes
  session/celebration/day-preview/pump; focus-on-open + return-to-trigger; Tab
  focus-trap on the session player; ≥44px targets; `:focus-visible` rings; `aria-live`
  rest timer.
- **Visual consistency:** Strong. Whole surface on canonical tokens; no `--volt`.
- **Code maintainability:** Strong. Page JS isolated in `training.js`; single-purpose
  functions; ephemeral session is one object with pure helpers; dead FAB/old render
  removed.
- **Reusability:** Strong. Ring/bar/stat-card/badge/sheet/exercise-card primitives
  reused across hero, session, preview, and celebration; `statCard`/`esc`/`dayLabel`
  shared.
- **Performance:** Strong. O(1) client math; no new blocking/AI calls; timer is a
  single `setInterval`; celebration is CSS + rAF, reduced-motion aware.
- **UX clarity:** Strong. One hero → Start → focused player → finish → celebration;
  weekly overview reads at a glance. *Weak spot:* pump-cancel mid-session UX (above).
