# Phase 4 Handoff — AxisAI V2 Nutrition Redesign

Date: 2026-07-06
Branch: `feat/phase4-nutrition`
Spec: `docs/superpowers/specs/2026-07-06-phase4-nutrition-redesign-design.md`
Plan: `docs/superpowers/plans/2026-07-06-phase4-nutrition-redesign.md`
Previous phase: `docs/archive/handoff-2026-07-05-phase3-home-dashboard.md`

## Completed Work

The Nutrition page (`/nutrition`) is rebuilt as an **image-first meal-logging**
experience on the canonical AxisAI design system (`tokens.css` + `components.css`
+ new `static/nutrition.css`), replacing the old `--volt`/`theme.css`-styled page.
The 5-tab structure (Today / Diary / Plan / History / Water) is preserved; **Today**
is the new centerpiece.

**Today tab**
- **Macro ring header** (`.nut-hero`): calorie ring (reuses `.ring-*`, r=48) +
  Protein/Carb/Fat bars (`.pbar-*`). Ring & bars driven by the existing
  `updateRing`/`updateMacroBars` (same element ids retained), macro targets from
  the 30/40/30 split off `/last-session`.
- **Meal timeline** (`.meal-timeline`): meals grouped by Kahvaltı / Öğle / Akşam /
  Ara Öğün. Each logged meal is a `.meal-card` with photo (or gradient
  placeholder), calories, P/K/Y macros, **time** (new `created_at`), an **AI Score
  badge** (client-side), and **Quick Edit**. Empty slots show an inline
  "add to this meal" prompt.
- **AI daily review** button (activates the previously-orphaned `getReview`) +
  the existing quick-add-from-plan section (preserved).

**FAB → bottom sheet** (`.log-fab` → `.sheet`), image-first options:
- **Take Photo** → camera/file capture → confirm modal (meal type suggested by
  time + optional note) → existing `POST /meal-log` with base64 `image` (S3).
- **Scan Barcode** → live `BarcodeDetector` camera overlay (EAN/UPC) with a
  manual barcode-number fallback → new `GET /api/food/barcode` (FatSecret) →
  reuses the serving-selector modal in a new **'meallog' mode** → logs to today.
- **Menu Scanner** → reuses the coach widget (`window.CW.startScan()`).
- **Voice** → first-class action opening a **designed placeholder** sheet
  ("In the mobile app") with a `NATIVE-VOICE-HOOK` comment marking the native
  integration point. No Web Speech (discarded at native migration).
- **Manual Entry** → the former inline meal form, relocated into a sheet.

**AI Score** — pure client-side `mealScore(m)` in `nutrition.js`: protein density
(0–40) + macro balance (0–35) + calorie sanity (0–25) → 0–100 → grade A/B/C/D →
`.badge-{success|warning|danger}`. No network, offline-safe (Phase 3 rule-engine
precedent).

**Backend** (minimal, additive, expand/contract-safe — no migration):
- `GET /api/food/barcode?code=<gtin>` (`app/blueprints/food.py`) +
  `_food_find_by_barcode()` (`app/services/fatsecret.py`): GTIN-13 normalization,
  `food.find_id_for_barcode` → `food.get` → `_food_get_servings`; returns
  `{food_id,name,brand,servings}` / 400 / 404.
- `created_at` (ISO) added to each meal in `GET /meal-log/today`.

## Files Modified

- **Created:** `static/nutrition.css`, `tests/test_barcode.py`,
  `docs/superpowers/specs/2026-07-06-phase4-nutrition-redesign-design.md`,
  `docs/superpowers/plans/2026-07-06-phase4-nutrition-redesign.md`.
- **Rewritten:** `templates/nutrition.html`, `static/nutrition.js`.
- **Backend:** `app/services/fatsecret.py` (+`_food_find_by_barcode`),
  `app/blueprints/food.py` (+`/api/food/barcode`),
  `app/blueprints/nutrition/meallog.py` (+`created_at`).
- **i18n:** `locales/tr.json`, `locales/en.json` (30 new `nutrition.*` keys).
- **Tests:** `tests/test_barcode.py` (new), `tests/test_nutrition_routes.py`
  (+`created_at`), `tests/test_i18n.py` (render assertions for new markup).
- **Docs:** `docs/handoff.md` (this), Phase 3 handoff archived.

## Components Created or Refactored

- **New CSS classes** (canonical tokens): `.nut-hero/.nut-macros/.nut-macro*`,
  `.meal-timeline/.meal-slot/.slot-head/.slot-emoji/.slot-name/.slot-kcal`,
  `.meal-card/.mc-img/.mc-body/.mc-title/.mc-macros/.mc-time/.mc-side/.mc-edit`,
  `.slot-empty`, `.log-fab`, `.log-sheet-grid/.log-sheet-opt/.lso-*`,
  `.scan-overlay/.scan-*`, `.voice-placeholder/.voice-*`, `.photo-preview`.
  Migrated (from the deleted inline `<style>`, now on tokens): `.autocomplete-*`,
  `.diary-*`, `.water-*`, `.qab*`, `.apd-*`, `.totals-row`, `.score-bar-*`,
  `.source-badge`, `.modal-overlay/.modal-box`, `.sm-macros`.
- **Reused (no new copies):** `components.css` `.sheet*`, `.ring-*`, `.pbar-*`,
  `.badge-*`, `.stat-card`, `.empty-state`, `.tab-*`, `.skeleton`, `.modal-title`,
  `.fc-*`, `.btn-*`; `theme.css` shell (`.page-body`, nav, `.meal-type-*`,
  `.plans-grid`, `.bar-chart-*`, `.cat-label`).
- **New JS:** `mealScore`, `renderTimeline`, `mealCardHTML`, `fmtTime`,
  `selectMealTypeByValue`, log-sheet/manual-sheet/voice-sheet controllers, photo
  flow (`onPhotoPicked`/`openPhotoConfirm`/`submitPhotoMeal`), barcode flow
  (`logScanBarcode`/`startBarcodeScan`/`resolveBarcode`/`onBarcodeManual`),
  serving-modal `'meallog'` mode (`openMealLogServing`, `_smCurrentMacros`,
  `_smApplyServings`), Esc-to-close + focus-on-open a11y.
- **Removed (dead):** old bottom-left quick-add FAB (`toggleQuickAdd`,
  `fxQuickWater`, `fxQuickScroll`, `scrollToForm`, `quickAddOpen`) and the
  `renderTodayMeals` card renderer.

## Architectural Decisions

1. **Client-side deterministic AI Score** — no LLM/network on render (single
   gunicorn worker/8 threads; CLAUDE.md warns against sync AI on hot paths).
2. **Barcode reader = browser `BarcodeDetector` + manual fallback** — zero
   external libraries (CSP-clean), swappable for native scanning later. Camera
   needs a secure context (HTTPS); manual number entry always works.
3. **Serving modal reused with a mode flag** — `'diary'` (writes to diary API,
   unchanged) vs `'meallog'` (barcode → `POST /meal-log` `override_macros`,
   which passes the existing `clamp_serving_macros` physical-health gate).
4. **Voice is a designed placeholder** ("In the mobile app"), architected for a
   native STT plug-in via the documented `NATIVE-VOICE-HOOK`. No Web Speech.
5. **Kept `theme.css` + `nav.css` loaded** (mirrors the Phase 3 dashboard) and
   layered `nutrition.css` on canonical tokens; nutrition surface has **no
   `--volt`** left. Shared shell classes come from theme/components.
6. **Backend additive only** — new route + new response field; no DB migration
   (`created_at` already existed on `MealLog`). Rollback-safe.

## Verification

- `tests/test_barcode.py` (8), `tests/test_nutrition_routes.py`
  (`created_at`), `tests/test_i18n.py` (nutrition render + canonical-value
  coupling): green. `nutrition.js` passes `node --check`. Every template class
  resolves in theme/components/nutrition CSS. Full-suite result recorded at
  commit time (see PR/CI). No `--volt`/raw-hex in the nutrition surface (camera
  overlay scrim is the single documented exception).

## Remaining Tasks / Known Issues

- **Live QA on HTTPS (recommended):** barcode camera (`BarcodeDetector` +
  `getUserMedia`) only runs in a secure context, so end-to-end barcode scan must
  be verified on the deployed HTTPS site (or `localhost` in Chrome) with a real
  product barcode. Manual-number entry is testable anywhere.
- `BarcodeDetector` is Chromium/Android only; Safari/Firefox fall back to manual
  entry (by design for this web MVP).
- Diary/Plan/History render functions still emit a few inline theme-alias vars
  (`var(--text)`, `var(--border)`) — valid tokens, render correctly; a future
  cleanup could move them to classes.

## Next Recommended Steps

1. Merge `feat/phase4-nutrition` → `main`; push triggers EC2 deploy (health-gated).
2. Live-QA the barcode flow on HTTPS with a real product.
3. Phase 5 (`phase-5.txt`) starts from the merged main.

## Quality Review

- **Responsiveness:** Strong. Mobile-first; ring/bars stack <560px, macros
  2-col <420px, sheet centers ≥768px, FAB clears the action bar. No horizontal
  overflow expected (flex/grid, `max-width:100%` media).
- **Accessibility:** Good. `role="dialog"`/`aria-modal` on sheets+modals,
  `aria-selected` synced on tabs, `aria-label` on icon buttons, ≥44px touch
  targets, `:focus-visible` rings, Esc-to-close, focus-on-open. *Weak spot:* no
  full focus-trap inside sheets (focus can tab out) — follow-up if needed.
- **Visual consistency:** Strong. Whole page on canonical tokens; no `--volt`.
- **Code maintainability:** Good. Page CSS isolated in `nutrition.css`; JS
  functions single-purpose; dead FAB code removed.
- **Reusability:** Strong. Serving modal, ring/bar/badge/sheet/empty-state
  primitives reused; barcode logging rides the existing meal-log path.
- **Performance:** Strong. AI score is O(1) client math; no new blocking AI
  calls; barcode does one FatSecret lookup on demand.
- **UX clarity:** Strong. One FAB → one sheet → image-first options; timeline
  reads at a glance; empty slots invite logging.
