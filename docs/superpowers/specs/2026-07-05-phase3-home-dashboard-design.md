# Phase 3 — Home Dashboard Redesign (AI Command Center)

Date: 2026-07-05
Branch: `feat/phase2-app-shell` (Phase 3 continues on the app-shell branch)
Source spec: `phase-3.txt` (OneDrive/Masaüstü)
Reads: `docs/handoff.md` (Phase 2), `docs/design-system.md` (tokens + components)

## Goal

Redesign **only** the home dashboard (`templates/index.html` + `static/dashboard.css`)
into an "AI Command Center": the user should open the app and immediately know
their standing and **what to do next**. Backend logic, routes, and the Phase 2
app shell are preserved unchanged. Mobile-first, premium, consistent with the
AxisAI design system.

## Scope

- **In scope:** `templates/index.html` (markup + inline JS), `static/dashboard.css`
  (full rewrite on canonical tokens), `locales/tr.json` + `locales/en.json` (new
  `index.*` keys), and the one i18n test that asserts on removed text.
- **Out of scope:** any backend/route change, the app shell (`_nav.html`,
  `_actionbar.html`, `nav.css`), other pages, the coach widget internals, new
  Python endpoints, and a real barcode-scanner backend (does not exist).

## Data contract (all existing — no backend changes)

Rendered into the template (Jinja context):
`username`, `profile_picture` (avatar_src, may be `None`), `streak_count`,
`last_weight_update`, plus from `inject_rank`: `user_xp`, `user_level`,
`user_title`, `xp_in_level`, `xp_for_next`. Also `locale`, `csp_nonce`, `t`.

Fetched client-side on load (unchanged endpoints):
- `GET /last-session` → `goal`, `target_calories`, `bmr`, `tdee`, `weight`,
  `target_weight`, `goal_type`.
- `GET /meal-log/today` → `meals[]` (each has `ogun`), `totals {kalori, protein,
  karb, yag}`.
- `GET /water` → `{count, goal}` (goal = 8).
- `GET /workout/status` → `{completed}`.
- `GET /checkin-history` → weight history for the sparkline.
- `POST /water` → set today's glass count (used by the Next-Action water CTA;
  CSRF header auto-added by `static/csrf.js`).

Macro targets are **derived** (reusing the exact split in `static/nutrition.js`):
`protein = cal*0.30/4`, `karb = cal*0.40/4`, `yag = cal*0.30/9`.

## Layout (top → bottom; mobile single column, ≥1024px 2-col bento)

1. **Top — identity.** `.avatar` (img if `profile_picture` else initials) +
   greeting "Merhaba, {username}", `Lv {level} · {title}`, and two chips:
   `⚡ {xp} XP` and `🔥 {streak} gün`. Uses the `.avatar` and `.badge` components.
2. **Hero card — "Bugünün Hedefi".** Large circular calorie ring (reuses
   `.ring-wrap/.ring-svg/.ring-track/.ring-fill/.ring-label`), hero number =
   **calories remaining**, sub-line = `consumed / target` + goal label. Ring
   color: blue < 85%, amber 85–100%, danger ≥ 100% (same thresholds as today).
3. **★ AI Next Action.** The most prominent section: accent border/background,
   one icon + title ("Sıradaki adımın") + a dynamic recommendation + a CTA
   button. Deterministic client rule engine (see below).
4. **Quick Actions.** 2×2 tile grid:
   - Öğün Ekle → `/nutrition`
   - Barkod Tara → **disabled "yakında" (coming soon)** placeholder
   - Menü Tara → opens the global coach widget scan:
     `if (window.CW){ if(!CW.open) CW.toggle(); setTimeout(()=>CW.startScan(),300);} else location.href='/nutrition'`
   - Antrenman → `/training`
5. **Nutrition Summary.** Four animated rings (reuse `.ring-*`): Protein, Karb,
   Yağ, Su. Each shows `current / target`; fill animates on load. Su ring uses
   `count/8`.
6. **Weight card (kept).** Current kg, trend delta (goal-aware color), Chart.js
   sparkline, weight input → `POST /update-weight`, BMR/TDEE/target meta. Logic
   ported verbatim from current `index.html`.
7. **Achievements.** Level emblem (level-tiered, mirrors quests.html emoji
   tiers) + title, XP progress bar (`.pbar-track/.pbar-fill`, width =
   `xp_in_level/xp_for_next`), streak flame, streak multiplier hint, and a
   `Görevler →` link to `/quests`.
8. **AI Tip (kept).** Daily tip carousel — TIPS_TR/TIPS_EN arrays, auto-advance,
   reduced-motion + visibilitychange handling — ported verbatim.

## AI Next Action — rule engine (client-side, first match wins)

Signals: `h = new Date().getHours()`, meals logged (from `/meal-log/today`),
`consumed` vs `target`, water `count` vs 8, `workout.completed`.

1. `5 ≤ h < 11` and no meal logged → **"Kahvaltını kaydet"** → `/nutrition`.
2. `11 ≤ h < 16` and `consumed < 0.35*target` → **"Öğle yemeği zamanı"** → `/nutrition`.
3. `16 ≤ h < 22` and `consumed < 0.75*target` → **"Akşam yemeğini kaydet"** → `/nutrition`.
4. `water.count < 8` and `h ≥ 12` → **"Su iç — {n}/8 bardak"** → inline `POST /water`
   (increment, then re-evaluate the card).
5. `!workout.completed` and `8 ≤ h < 22` → **"Antrenmana başla"** → `/training`.
6. otherwise → **"Harika gidiyorsun 💪"** → `/progress-page` (celebratory, soft CTA).

Framed as a "smart next step," not a live LLM call — consistent with the
existing deterministic `analytics_engine` nudges, and it avoids the
synchronous/thread-blocking AI path CLAUDE.md warns about.

## CSS strategy

Rewrite `static/dashboard.css` from scratch using **canonical design-system
tokens only** (`--color-*`, `--space-*`, `--radius-*`, `--text-*`, `--elevation-*`,
`--ease-*`, `--duration-*`), reusing `components.css` classes for rings, bars,
avatar, badges, cards, and section labels. This retires the legacy
`--volt`/hardcoded-gray palette (handoff Phase-3 TODO). Page-specific classes
(`.dash-*`, `.hero-*`, `.next-*`, `.qa-*`, `.nutri-*`) live in `dashboard.css`;
no new tokens or `components.css` entries are required. Inline `<style>`/`<script>`
keep `nonce="{{ csp_nonce }}"`.

## Preserve / remove

- **Preserve:** every backend route; `_nav.html`/`_actionbar.html` includes with
  `nav_active='home'`; the Chart.js SRI `<script>` tag (asserted by
  `test_chart_js_tag_carries_sri`); the weekly-reward modal; the quick-add FAB
  (Log Meal) may be kept or folded into Quick Actions — folding is preferred to
  reduce redundancy, but the FAB's removal must not break the shell.
- **Remove:** the placeholder Activity/steps logging card (`.act-*`, intensity
  buttons, `/api/activity/log` UI). Steps auto-sync from device health is a
  future feature. `/api/activity/today` may still be read for the calorie ring's
  activity bonus (returns 0 with no data) or dropped — implementer's choice,
  default to dropping the steps UI and defaulting activity cals to 0.

## i18n & tests

- Add all new visible strings as `index.*` keys in both `locales/tr.json` and
  `locales/en.json` (section labels, next-action titles/subs, quick-action
  labels, "yakında", achievements labels, macro labels). Canonical backend
  values (goal names, `ogun` values) stay Turkish — only display text is
  translated (per the i18n coupling rule).
- Update `tests/test_i18n.py::test_dashboard_renders_localized`: it currently
  asserts EN `"Activity Tracking"` (removed). Re-point it to new stable
  **server-rendered** EN strings (e.g. a section label + "Quick Actions") while
  keeping the `"Sports Physiology"` tip check. Ensure enough labels are rendered
  server-side (via `t()` in the template) for a meaningful assertion.
- Existing shell tests (`test_app_shell.py`) must still pass unchanged (home tab
  active, action bar, no drawer). Run full `pytest` before completion.

## Responsive & accessibility

- Mobile-first single column; ≥768px introduces 2-col groupings; ≥1024px a
  2-col bento with Hero + Next Action emphasized in the top row.
- Touch targets ≥ 44px; `aria-label`s on icon-only actions; disabled Barkod tile
  is `aria-disabled` + non-interactive; respect `prefers-reduced-motion` for ring
  fills, tip carousel, and card entrance.
- Contrast via semantic text tokens (`--color-text-1..3`); tabular figures for
  all numeric stats.

## Verification

1. `python -m pytest -q` → all green (updated i18n test + untouched shell tests).
2. `flask run` smoke: `/` renders in TR and EN; rings animate; Next Action shows
   a sensible CTA; Quick Actions navigate/open scan; weight update + sparkline
   still work; weekly-reward modal still triggers.
3. Update `docs/design-system.md` (dashboard now token-compliant) and produce
   `docs/handoff.md` for Phase 3 (required by the phase spec's End section).

## Out-of-scope / follow-ups

- Real barcode scanner backend (Open Food Facts etc.).
- Device-health steps auto-sync (replaces the removed steps card).
- Enriching Next Action with server nudges (`/dashboard-nudges`).
- Browser pixel/axe verification (Chrome extension not connected this session —
  same constraint noted in Phase 1/2 handoffs).
