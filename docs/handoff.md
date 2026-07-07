# Phase 5 Handoff — AxisAI V2 Progress Redesign (Surface 2)

Date: 2026-07-07
Branch: `feat/phase5-progress` (off `origin/main` @ eca5a98, after the Workout surface merged)
Spec: `docs/superpowers/specs/2026-07-07-phase5-progress-redesign-design.md`
Plan: `docs/superpowers/plans/2026-07-07-phase5-progress-redesign.md`
Previous surface: `docs/archive/handoff-2026-07-07-phase5-workout.md`

Phase 5 is decomposed into independent surface cycles — **Workout ✅ →
Progress (this) → Profile → app-wide FINAL QA.** This handoff covers surface 2:
Progress (`/progress-page`).

## Completed Work

`/progress-page` is rebuilt from a weekly check-in tool into a **premium analytics
experience** on the canonical AxisAI design system, backed by **five additive
read-only endpoints** — with **zero writes/schema/migration** and the weekly
Check-In flow preserved verbatim.

**Backend — additive read-only aggregation (`app/blueprints/tracking.py`), all
`GET`, `@login_required`, scoped to `current_user.id`, no writes:**
- `GET /api/progress/nutrition?range=week|month` — per-day kcal + P/C/F from
  `MealLog` (Istanbul `tarih` grouping) + averages + `target_kcal`.
- `GET /api/progress/workout?range=week|month` — sessions/day + real-exercise
  volume (EXCLUDES `WORKOUT_COMPLETION_MARKER`, counts it as a session) +
  `DailyActivity` active minutes.
- `GET /api/progress/heatmap?weeks=26` — per-day 0–4 activity level (union of
  meal/activity/workout/check-in signals), clamped 1–53 weeks.
- `GET /api/progress/achievements` — `rank_points`→`get_level`/`level_title`,
  `streak_count`, `weekly_xp`, quests-done, `WeeklyWinner` count, 5 milestones.
- `GET /api/progress/insights` — **deterministic** insights (weight direction,
  workout consistency, calorie adherence, streak); always ≥1 insight; tones
  `success|warning|info`. No LLM/heavy AI.

**Frontend (`templates/progress.html` 1391→~250 lines, new `static/progress.js`
467 L, new `static/progress.css` 241 L):**
- **Header + overview** (streak / level / weekly-XP snapshot).
- **GitHub-style consistency heatmap** — pure CSS grid (`.hm-grid` / `.hm-cell
  .lvl-0..4`), 26 weeks, horizontal-scroll, legend.
- **AI Insights** — deterministic cards (icon + tone badge + body).
- **4 tabs** (Weight & Body / Nutrition / Workout / Achievements):
  - **Weight & Body** — weight trend (Chart.js) + wellness charts (from the
    existing `/checkin-history`) + BMI/current-weight/Δ `.stat-card`s + the
    **Check-In sheet** (`POST /checkin` + coach feedback, preserved with its XSS
    escaping and `window.CW.receiveCheckinFeedback` hook).
  - **Nutrition** — kcal bar + macro trend charts with a **week/month toggle**;
    adherence stat cards.
  - **Workout** — volume trend chart + sessions/volume/active-minutes stats.
  - **Achievements** — level/XP/streak/quests/wins stat cards + milestone badges.

## Files Modified

- **Created:** `static/progress.js`, `static/progress.css`,
  `tests/test_progress_api.py`, `tests/test_progress_ui.py`, the spec + plan docs.
- **Rewritten:** `templates/progress.html` (inline `<style>`/`<script>` removed).
- **Backend:** `app/blueprints/tracking.py` (+`_progress_range` + 5 read-only
  routes; `progress_page` gains `height`/`goal_weight` render context).
- **i18n:** `locales/{tr,en}.json` (+38 `progress.*` keys each; 7 hardcoded TR
  strings in `progress.js` wired to `__t()`).
- **Docs:** `docs/handoff.md` (this); Phase 5 Workout handoff archived.

## Components Created or Refactored

- **New page CSS (canonical tokens):** `.heatmap`/`.hm-*`, `.insight-row`/
  `.insight-card`/`.ic-*`, `.trend-toggle`/`.tt-btn`, `.chart-card` (restyled),
  `.metric-stats`, `.prog-overview`/`.po-*`, `.ach-title`/`.ach-badges`, plus the
  migrated check-in slider/overload/feedback/history rules.
- **Reused:** `.card`, `.stat-card`, `.tab-*`, `.badge-*`, `.sec-label`,
  `.sheet-*`, `.btn-*`, `.skeleton`, `.empty-state`.
- **New JS:** `switchTab` (with `aria-selected` sync), `loadOverviewAndExtras`/
  `renderHeatmap`/`renderInsights`/`renderOverview`, `statCard` (shared),
  `loadWeightTab`/`renderBodyStats`/`_chartBase`, `loadNutritionTab`/
  `loadWorkoutTab`/`setTrendRange`, `loadAchievementsTab`, `openCheckin`/
  `closeCheckin` + Esc/focus. **Preserved verbatim:** `submitCheckin`,
  `selectOverload`, `showToast`, `escapeHTML`, the coach-feedback escaping.

## Architectural Decisions

1. **Additive read-only backend** — five `GET` aggregation endpoints, no writes,
   no schema, no migration; grouping done in Python (DB-agnostic: SQLite local /
   Postgres prod); Istanbul day keys via `app/timeutil`. Rollback-safe (expand-only).
2. **Deterministic insights** — no LLM/heavy AI on a hot path (CLAUDE.md warns);
   computed from the user's own trends; always returns ≥1 insight.
3. **Body merged into Weight & Body** — no body-measurement data exists; shows
   weight + BMI + Δ. Measurement logging deferred (would need a new model).
4. **Heatmap = CSS grid, Chart.js for trends** — Chart.js kept from jsdelivr
   (CSP-allowed, integrity-pinned); no new JS libraries. Chart color literals
   stay in JS (documented design-system exception).
5. **Check-In preserved** — `POST /checkin` + `/checkin-history` unchanged;
   relocated into a sheet with the same element ids so behavior is identical.

## Verification

- Full `pytest`: **1110 passed, 0 failures** (incl. 9 new `test_progress_api.py`
  endpoint tests + `test_progress_ui.py` render checks). `node --check
  static/progress.js` clean.
- No `--volt`/raw hex/`rgba()` in `progress.css` (Chart.js JS color literals
  excepted); every `progress.*` key resolves in both locales (parity).
- Backend read-only: `git diff` shows only `app/blueprints/tracking.py` in `app/`,
  additive routes only.
- **Not yet done — manual browser QA:** the interactive flows (each tab's Chart.js
  render, heatmap, week/month toggle, check-in submit, responsive widths) were
  verified via `node --check` + render/endpoint tests only, not driven in a browser.

## Remaining Tasks / Known Issues

- **Manual/live QA recommended** before/after deploy: drive each tab (charts
  render), the heatmap, week/month toggle, the Check-In sheet → coach feedback,
  empty-data states, EN locale, and 360/768/1024px widths.
- **No full focus-trap** in the Check-In sheet (has focus-on-open/return + Esc);
  a follow-up if needed (same posture as the Workout session player).
- **Body-measurement logging deferred** — merged into Weight & Body as weight/BMI;
  a future additive feature (new model + endpoints) if measurements are wanted.
- Insight/milestone copy was authored to match the existing nudge tone — worth a
  quick product copy read-through.

## Next Recommended Steps

1. Live-QA the Progress flows on a running app.
2. Merge `feat/phase5-progress` → `main` (health-gated EC2 deploy).
3. Start **Phase 5 surface 3: Profile** (`/edit-profile`) as its own spec → plan
   → build cycle, then the app-wide **FINAL QA** pass.

## Quality Review

- **Responsiveness:** Strong (heatmap + insight row horizontal-scroll, tabs wrap,
  charts `maintainAspectRatio:false`). *Widths not visually verified — manual QA.*
- **Accessibility:** Good. `role="tablist"` + dynamic `aria-selected`; check-in
  sheet `role="dialog"`/`aria-modal`/Esc/focus-on-open+return; heatmap cell
  `title=`; chart `aria-label`s; ≥44px targets; `:focus-visible`. *Weak spot:* no
  full focus-trap in the sheet.
- **Visual consistency:** Strong. Whole surface on canonical tokens; no `--volt`.
- **Code maintainability:** Strong. Page JS isolated in `progress.js`; single-
  purpose functions; shared `statCard`/`_chartBase`; endpoints small + tested.
- **Reusability:** Strong. `.stat-card`/`.badge`/`.tab-*`/`.sheet-*`/`.skeleton`
  reused; `_progress_range` shared across endpoints.
- **Performance:** Strong. Bounded ranges (week/month/26-weeks) over indexed
  columns; Python group-by (no N+1); deterministic O(n) insights; no blocking AI.
- **UX clarity:** Strong. Heatmap + insights up top, then focused metric tabs;
  week/month toggle; check-in a tap away. *Minor:* insight copy read-through.
