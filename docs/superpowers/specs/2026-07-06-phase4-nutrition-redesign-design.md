# Phase 4 — Nutrition Redesign (Design Spec)

Date: 2026-07-06
Phase: AxisAI V2 · Phase 4 (`phase-4.txt`)
Precedes: implementation plan (`docs/superpowers/plans/2026-07-06-phase4-nutrition-redesign.md`)
Prior phase: Phase 3 home dashboard (`docs/superpowers/specs/2026-07-05-phase3-home-dashboard-design.md`)

## Goal

Turn the Nutrition page into the easiest possible meal-logging experience:
**image-first logging**, a macro ring, a modern meal timeline, and a FAB bottom
sheet — rebuilt on the canonical AxisAI design system. Preserve every backend
endpoint and business rule; reuse existing components.

## Scope Decisions (approved)

1. **Reach:** Rebuild the *Today* tab into the new Macro-Ring + Meal-Timeline +
   FAB experience AND migrate the other four tabs (Diary / Plan / History /
   Water) off the old `--volt` / `theme.css` palette onto canonical
   `tokens.css` + `components.css`. The whole page becomes visually consistent.
2. **AI Score:** Client-side **deterministic** score (no LLM, no backend, no
   network). Mirrors Phase 3's client rule-engine precedent.
3. **Voice:** NOT implemented as a working feature. Kept as a **first-class FAB
   action** with a fully-designed placeholder screen labelled *"Available in the
   mobile app"* — architected so native iOS/Android speech recognition can plug
   in later without UI changes. No Web Speech API (would be discarded at native
   migration).
4. **Barcode:** **Fully implemented and integrated** into the logging flow
   (FatSecret barcode API approved). Reader = browser **`BarcodeDetector`** on a
   live camera stream, with a **manual barcode-number entry fallback** where the
   API/camera is unavailable. Reader is swappable for native scanning later.

## Information Architecture

Keep the 5 tabs (**Today / Diary / Plan / History / Water**); restyle all onto
canonical tokens. `Today` is the centerpiece.

### Today tab

1. **Macro Ring header** — large calorie ring (`components.css` `.ring-*`, kcal
   in center, animated fill) + four macro tiles (Calories · Protein · Carbs ·
   Fat) as `.stat-card`s with animated `.pbar-*` progress. Mobile-first.
   Macro targets from the existing nutrition.js formula (protein = target·0.30/4,
   carb = ·0.40/4, fat = ·0.30/9); calorie target from `/last-session`.

2. **Meal Timeline** — meals grouped by slot: **Kahvaltı / Öğle / Akşam / Ara
   Öğün** (canonical TR values preserved). Each logged meal is a `.card` with:
   - **Meal image** (`photo_url`) or a gradient placeholder when none.
   - **Calories** + **macro row** (P / K / Y).
   - **Time** (from new `created_at` field, formatted Istanbul).
   - **AI Score badge** (client-side, see below).
   - **Quick Edit** affordance (opens the existing serving/edit path).
   Empty slots render an inline `.empty-state`-style "add to this meal" prompt.

3. **FAB → Bottom Sheet** — a single prominent FAB opens a `.sheet` with five
   image-first options; text search is demoted into *Manual Entry*:
   - **Take Photo** → camera/file capture → confirm card (meal type + auto
     macros) → existing `POST /meal-log` with `image` (already supported;
     uploads to S3, macros via existing AI path or manual override).
   - **Scan Barcode** → live camera overlay via `BarcodeDetector`; on EAN/UPC
     hit → `GET /api/food/barcode?code=<gtin>` → reuse existing serving-selector
     modal → log. Manual barcode-number input fallback.
   - **Menu Scanner** → reuse existing coach-widget menu scan (`window.CW`);
     fallback to current menu flow if the widget is absent.
   - **Voice** → placeholder screen ("Available in the mobile app").
   - **Manual Entry** → existing meal-type + food-autocomplete + serving flow,
     relocated into the sheet.

### Other tabs (restyle only, no logic change)

- **Diary / Plan / History / Water** — swap `--volt`/`theme.css` classes for
  canonical tokens & `components.css` primitives. Behavior identical.

## AI Score (client-side, deterministic)

Pure JS `mealScore(meal, targets)` → `{value: 0-100, grade: 'A'|'B'|'C'|'D',
tone: 'success'|'warning'|'danger'}`. Inputs from the meal's own macros only:

- **Protein density** — protein kcal share of the meal vs. a healthy band.
- **Calorie reasonableness** — meal kcal sane for a single eating occasion
  (penalize extreme highs).
- **Macro balance** — distance from a balanced P/C/F split.

Weighted sum → 0–100 → grade + tone → `.badge-{tone}`. No network; offline-safe.
Exact weights specified in the implementation plan.

## Backend Changes (minimal, additive, backward-compatible)

1. **NEW** `GET /api/food/barcode?code=<gtin>` (`app/blueprints/food.py`),
   auth + `FOOD_SEARCH_RATELIMIT`, calling a new
   `_food_find_by_barcode(code)` in `app/services/fatsecret.py`
   (FatSecret `food.find_id_for_barcode` → `food.get` → `_food_get_servings`).
   Returns `{food_id, name, brand, servings}` or `{error}` (404 when not found).
   Barcode normalized to GTIN-13 (FatSecret expects 13 digits, left-padded).
2. Add `created_at` (ISO 8601, UTC) to each meal object in
   `GET /meal-log/today` (`app/blueprints/nutrition/meallog.py`). Purely
   additive; existing callers (dashboard) ignore unknown fields.

Everything else — `POST /meal-log`, diary API, plan API, `/meal-log/history`,
`/meal-log/review`, water, menu, `/api/food/search` — is untouched.

Expand/contract safety (CLAUDE.md A2): both changes are additive (new route,
new response field). No schema migration required — `created_at` already exists
on `MealLog`.

## Cross-cutting

- **Animations:** `.sheet` slide-up, ring stroke fill, staggered card entrance,
  water fill — all via `components.css` easings/tokens.
- **Loading:** `.skeleton` placeholders for timeline + history.
- **Empty states:** `.empty-state` for no-meals, no-history, barcode-not-found,
  camera-unavailable.
- **Accessibility:** ≥44px touch targets, `role="tablist"`/`aria-selected` on
  tabs, `aria-modal`/focus-trap on sheet & modals, `:focus-visible` rings,
  labelled icon buttons.
- **Responsive:** mobile-first; ring + tiles stack on narrow, side-by-side
  ≥768px; sheet becomes a centered panel ≥768px (already in `.sheet`).
- **i18n:** new TR/EN keys for FAB options, barcode UI, voice placeholder, AI
  score labels, empty states. Canonical backend values (meal names, food codes)
  stay TR; only display text is mapped (per i18n coupling rule).
- **CSP:** inline `<script>` carries the `nonce`; no JS-injected `<style>`;
  new CSS loaded via `<link href="/static/nutrition.css">`.

## Files

- **Rewrite:** `templates/nutrition.html`, `static/nutrition.js`.
- **New:** `static/nutrition.css` (page styles on canonical tokens; page-specific
  only — timeline card, FAB sheet options, barcode overlay, voice placeholder).
- **Backend:** `app/blueprints/food.py` (+barcode route),
  `app/services/fatsecret.py` (+`_food_find_by_barcode`),
  `app/blueprints/nutrition/meallog.py` (+`created_at` in today response).
- **i18n:** `locales/tr.json`, `locales/en.json`.
- **Tests:** extend `tests/` — barcode lookup (mock FatSecret), `created_at`
  present in `/meal-log/today`, i18n render assertions for new markup.
- **Docs:** update `docs/handoff.md` at phase end (required by phase-4.txt).

## Non-Goals

- No working Voice logging (placeholder only).
- No new database columns / migrations.
- No changes to macro-calculation, plan-generation, diary, or menu-extraction
  business logic.
- No bundled third-party barcode library (native `BarcodeDetector` + manual
  fallback only).

## Success Criteria

- Today tab shows macro ring + timeline with image, macros, time, AI score,
  quick edit; FAB opens the 5-option sheet.
- Barcode scan (or manual number) resolves a FatSecret food and logs it via the
  existing serving modal.
- Take Photo logs a meal with an image through the existing endpoint.
- All 5 tabs render on canonical tokens (no `--volt` left in nutrition surface).
- `pytest` green; existing meal-logging / dashboard flows unbroken.
- `docs/handoff.md` regenerated per the phase-4.txt end checklist.
