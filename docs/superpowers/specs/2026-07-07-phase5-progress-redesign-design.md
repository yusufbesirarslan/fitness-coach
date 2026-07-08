# Phase 5 — Progress Redesign (Design Spec)

Date: 2026-07-07
Branch: `feat/phase5-progress` (off `origin/main` @ eca5a98, after the Workout surface merged)
Phase source: `phase-5.txt` (Progress section)
Previous surface: `docs/handoff.md` (Phase 5 Workout)

Phase 5 is decomposed into independent surface cycles (Workout ✅ →
**Progress** → Profile → global polish/QA). **This spec covers surface 2:
Progress (`/progress-page`).**

## Context

`/progress-page` today is a **weekly Check-In tool**, not the analytics
experience the spec asks for. It has 3 tabs — **Check-In** (weight + 4 wellness
sliders [intensity/fatigue/sleep/nutrition] + progressive-overload → `POST
/checkin` → AI coach feedback), **Charts** (weight / metrics / wellness line
charts from check-in history), **History** (list of past check-ins) — on the
legacy `--volt`/`theme.css` styling with an inline `<script>` and Chart.js from
jsdelivr.

`phase-5.txt` wants a **professional analytics experience**: tabs **Weight /
Nutrition / Workout / Body / Achievements**, **charts**, a **GitHub-style
consistency heatmap**, **weekly/monthly trends**, and **AI Insights**.

### Decisions (from user, 2026-07-07)

1. **Additive read-only backend** — new `GET`-only aggregation endpoints
   (nutrition/workout trends, heatmap, achievements, insights), each scoped to
   `current_user`, no writes, **no schema/migration**. Required to chart real data.
2. **Merge "Body" into "Weight & Body"** — no body-measurement data exists
   (`WeeklyCheckIn` has weight + wellness sliders only). One "Weight & Body" tab
   (weight trend + BMI + change rate + goal progress). **4 tabs total.** No
   measurement backend.
3. **Deterministic AI Insights** — reuse/extend `analytics_engine.py`
   (recovery/overload/protein/hydration/streak + trend deltas). No heavy AI on a
   hot path (CLAUDE.md warns), no LLM cost.

### Data sources (verified)

| Concern | Source | Notes |
|---|---|---|
| Weight/wellness | `WeeklyCheckIn` (weight, yogunluk, fatigue, uyku_kalitesi, beslenme_uyumu, progressive_overload, coach_feedback), `WeeklyLog` | via existing `/checkin-history` |
| Nutrition | `MealLog` (kalori/protein/karb/yag, tarih ISO) | only a *today* endpoint today → needs a trend endpoint |
| Workout | `WorkoutLog` (volume, created_at; `WORKOUT_COMPLETION_MARKER` = UI-completion sentinel, volume 0), `PumpCheck`, `DailyActivity` (steps/calories/duration/date_key) | needs a trend endpoint |
| Achievements | `User.rank_points` (→ `get_level`/`level_title`), `User.streak_count`, `User.weekly_xp`, `DailyQuest`/`UserQuestProgress`, `WeeklyWinner` | needs an endpoint |
| Consistency | per-day union of MealLog/WorkoutLog/DailyActivity/check-in | needs a heatmap endpoint |
| Insights | `analytics_engine.get_nudges(...)` (deterministic) | reuse + trend deltas |

### Hard constraints

- **Preserve the weekly Check-In feature** — `POST /checkin` (writes
  `WeeklyCheckIn`, returns `coach_feedback`) and `GET /checkin-history` unchanged;
  keep the AI-feedback **XSS escaping** (`&`/`<`/`>` → entities, `\n`→`<br>`) and
  the `window.CW.receiveCheckinFeedback` hook.
- **Backend additions are read-only aggregation only** — no writes, no schema,
  no migration; every query scoped to `current_user.id`.
- **i18n canonical coupling** — TR canonical values stay TR; display via
  `t()`/`__t`. New UI strings go through `t()` keys in both locales.
- **CSP** — Chart.js loads from `cdn.jsdelivr.net` (allowed) with its
  integrity/crossorigin pin; inline `<script>` carries `nonce`; **never inject
  `<style>` from JS**; page CSS via `<link>`.
- **Design system** — canonical tokens only in `progress.css`; reuse
  `components.css` primitives. Chart.js color literals stay in JS (documented
  design-system exception, as in the current page and `nutrition.js` ring).

## Goals

A premium, mobile-first analytics experience on the canonical design system:
consistency heatmap + deterministic insights up top, then 4 metric tabs (Weight &
Body / Nutrition / Workout / Achievements) with weekly/monthly trend charts —
backed by additive read-only endpoints — while preserving the weekly check-in.

## Approach

### 1. Foundation & refactor
- **New `static/progress.css`** — canonical tokens, mobile-first (mirrors
  `nutrition.css`/`training.css`). Keep `theme.css`+`nav.css` shell.
- **Extract inline JS → `static/progress.js`** (top-level globals for
  `actions.js` delegation; `node --check`-able). Tiny nonce'd bootstrap for any
  server value (e.g. `current_weight`, height for BMI).
- **Chart.js** stays from jsdelivr (CSP-allowed, integrity-pinned). **Heatmap =
  CSS grid**, no library.

### 2. Backend — additive read-only endpoints (`app/blueprints/tracking.py`)
All `@login_required`, `GET`, scoped to `current_user.id`, no writes.
- `GET /api/progress/nutrition?range=week|month` → `{days:[{date, kcal, p, c, f}],
  avg:{kcal,p,c,f}, target_kcal}` from `MealLog` grouped by `tarih` over the range.
- `GET /api/progress/workout?range=week|month` → `{days:[{date, sessions, volume,
  active_min}], totals:{sessions, volume}}` — `sessions` counts distinct workout
  days (incl. the completion marker); `volume` sums real `WorkoutLog` rows
  (EXCLUDING `WORKOUT_COMPLETION_MARKER`); `active_min` from `DailyActivity`.
- `GET /api/progress/heatmap?weeks=26` → `{cells:[{date, level}]}` where `level`
  is 0–4 bucketed from a per-day activity score (union: a logged meal / workout /
  activity / check-in each contribute), for a GitHub-style grid.
- `GET /api/progress/achievements` → `{level, title, rank_points, weekly_xp,
  streak, quests_done, weekly_wins, milestones:[{key, label, hit}]}` using
  `get_level`/`level_title` + quest/winner queries.
- `GET /api/progress/insights` → `{insights:[{icon, title, body, tone}]}` from a
  new `analytics_engine` helper that composes `get_nudges` output + trend deltas
  (weight direction, workout-consistency, calorie adherence). Deterministic;
  localized via the existing nudge-translation pattern.

### 3. Frontend structure (`templates/progress.html` + `progress.js`)
- **Header/overview** — page title + compact snapshot (streak, level, this-week).
- **Consistency heatmap** (`.heatmap`) — GitHub-style CSS grid, ~26 weeks ×
  7 days, cells colored by `level` (0–4 token-based tints), month labels, legend.
- **AI Insights** (`.insight-card`) — a small stack/carousel of deterministic
  insight cards (icon + title + body, tone via `.badge-*`).
- **Tabs (4)** — reuse `.tab-bar`/`.tab-btn`/`.tab-panel`:
  - **Weight & Body** — weight trend line (Chart.js) + `.stat-card`s (current
    weight, BMI from height, change rate, goal delta) + the wellness charts
    (intensity/fatigue, sleep/nutrition-adherence) from `/checkin-history`; a
    **Check-In** button → a `.sheet` with weight + 4 wellness sliders + overload
    chips → `POST /checkin` → coach-feedback card (preserved flow + escaping).
  - **Nutrition** — kcal trend + macro (P/C/F) trend charts with a **week/month
    toggle**; adherence `.stat-card`s. From `/api/progress/nutrition`.
  - **Workout** — sessions/week + volume trend charts + this-week vs last +
    active-minutes. From `/api/progress/workout`.
  - **Achievements** — level `.ring-*` + XP, streak, quests-done, weekly wins,
    milestone `.badge`s. From `/api/progress/achievements`.
- **Week/Month toggle** drives the Nutrition/Workout `range` param.

### 4. i18n
- Add `progress.*` keys for all new UI (tabs, heatmap legend, insight fallbacks,
  stat labels, week/month). Update `locales/{tr,en}.json`. Keep canonical TR
  values; insight text localized via the deterministic engine's translation map.

## Components created (page-scoped → `progress.css`)
`.heatmap`/`.hm-cell`/`.hm-legend`/`.hm-month`, `.insight-card`/`.insight-*`,
`.trend-toggle`, `.metric-hero`, `.chart-card` (restyled). Reuse from
`components.css`: `.card`, `.stat-card`, `.tab-*`, `.ring-*`, `.pbar-*`,
`.badge-*`, `.sec-label`, `.empty-state`, `.skeleton`, `.sheet-*`, `.btn-*`,
`.chip`, `.toast-*`.

## Non-goals (deferred)
- Body-measurement logging (chest/waist/body-fat) — no data model; merged into
  Weight & Body as weight/BMI for now.
- Any heavy-AI insight generation (deterministic only this phase).
- Wearable deep-dive analytics beyond the existing `DailyActivity`/wearable
  today-summary.
- Profile surface + app-wide QA (later Phase 5 cycles).

## Verification
- `node --check static/progress.js`.
- New endpoints unit-tested (`tests/test_progress_api.py`): shape, `current_user`
  scoping, empty-data, `range` handling, marker-exclusion in workout volume.
- Every class in `progress.html` resolves in tokens/components/theme/progress css;
  no `--volt`/raw hex in `progress.css` (Chart.js JS color literals excepted).
- i18n render + parity (`tests/test_i18n.py` / a progress render test).
- Full `pytest` green; `flask run` manual pass: heatmap renders, each tab loads
  its chart, week/month toggle, check-in submit → feedback, empty-data states,
  EN locale.
- Preserved: `/checkin` + `/checkin-history` behavior unchanged; only additive
  `GET /api/progress/*` routes added to `app/`.

## Risks / mitigations
- **New endpoints on a live app** → read-only, additive, `current_user`-scoped,
  no migration; rollback-safe (expand-only). Tested for empty-data + scoping.
- **Aggregation performance** → bounded ranges (week/month/26-weeks), indexed
  columns (`created_at`/`date_key`/`tarih`), simple group-by; no N+1.
- **Chart.js CSP** → keep the exact jsdelivr `<script>` with integrity; no other
  external hosts.
- **Check-in regression** → reuse the existing `/checkin` request + escaping
  verbatim; only relocate its UI into a sheet.
