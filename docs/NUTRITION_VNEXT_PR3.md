# NUTR-PR3 — Daily nutrition execution (Today surface convergence)

Base: `origin/main` `983f3dc` (hydration reliability, #359; CI 36458258062 green).
Scope: presentation, hierarchy, state truthfulness and tests for **Nutrition → Today**.
No backend, route, model, schema, migration, provider or AI change.

## 1. Pre-change characterization (983f3dc)

**Visual order of Today:** ring gauge + three macro bars → Coach link → "Today's Meals" (four slots, each
empty slot a separate dashed "+ Add to this meal" block) → History disclosure → "Quick Add" (water cup +
plan meals) → Water disclosure → "AI Review" divider + end-of-day review → Diary disclosure. The primary
logging control was a floating 56px circle (`#log-fab`) on the left rail that tucked away while scrolling.

**Data sources:** one canonical read `GET /meal-log/today` → `totals` (MealLog), `meals` (MealLog rows with
opaque `entry_token`/`revision`), `targets`/`remaining` (`nutrition_targets` projection of the latest
`UserSession.target_calories`, `null` when not configured). Planned meals: `GET /nutrition-plan/active`.
Hydration: `GET /water` (hydration-repair state machine).

**Failure behaviour (defects fixed here):**
- a failed or never-answered `/meal-log/today` left the server-rendered `0` kcal / `0` g visible and the
  skeleton spinning forever — an unknown intake read as a confirmed zero;
- a failed `/nutrition-plan/active` blanked the plan shortcuts silently (indistinguishable from nothing);
- an ambiguous (5xx/network) planned-meal write re-enabled the button with an error, inviting a second
  write under a fresh idempotency key;
- the ring's `%` was a completion percentage and the ring repeated the intake/target pair as a picture.

**Logging entry points (all remain reachable after PR3):**

| Control | JS function | Endpoint / boundary | Writes MealLog? | After PR3 |
| --- | --- | --- | --- | --- |
| Floating `#log-fab` | `openLogSheet` | none (opens chooser) | no | **retired** — replaced by `#log-food-btn`, same function |
| `#log-food-btn` (new) | `openLogSheet` | none (opens chooser) | no | the one front door |
| Chooser: Take photo | `logTakePhoto` → `submitPhotoMeal` | `POST /meal-log` (image) | yes, on confirm | unchanged |
| Chooser: Scan barcode | `logScanBarcode` → `resolveBarcode` → `logProviderFoodToLedger` | `GET /api/food/barcode` (discovery) → `POST /meal-log` `provider_food` | yes, on confirm | unchanged |
| Chooser: Menu scanner | `logMenuScan` → `CW.startScan` | Coach widget scan/analysis; logging stays separate | no (analysis only) | unchanged |
| Chooser: Voice | `logVoice` | placeholder sheet | no | unchanged |
| Chooser: Manual | `logManual` → `searchFood`/`selectFood` → `logMeal` | `GET /api/food/search`, servings (discovery) → `POST /meal-log` (`provider_food` / `override_macros` / free text) | yes, on submit | unchanged |
| Meal row edit | `quickEditMeal` | opens manual sheet on that slot | no | unchanged |
| Meal row delete | `deleteMeal` | `DELETE /meal-log/entry/<token>` (`If-Match`) | deletes | unchanged |
| Empty slot "+ Add to this meal" | `logManualSlot` | opens manual sheet on that slot | no | now inline in the slot line |
| Planned shortcut | `quickAddMeal` | `POST /api/quick-add-meal` | yes, immediately | "From your plan", Planned → Logged |
| Diary builder | `addDiaryFood`/`confirmServingModal` → `logDiaryMeal` | `/api/diary/meal*` staging → `POST /api/diary/meal/<id>/log` | only on explicit commit | unchanged (secondary) |
| Coach | `.coach-entry` link, end-of-day review | `/coach`; `POST /meal-log/review` on click | no | Tier 3, unchanged scope |

**Initial request topology (measured, unchanged by PR3):** `/nutrition`, `/meal-log/today` ×1,
`/nutrition-plan/active` ×1 (in-flight shared by Plan detail + shortcuts), `/water` ×1,
`/notifications/unread-count` ×1 (shell), `/coach/history` ×1 (pre-existing Coach-widget hydration; the
widget hosts the menu scanner). Lazy: Diary (`/api/diary/today` on open), History (`/meal-log/history` on
open), search/barcode/servings/menu (on use), plan generation/save (on click), Supplements (separate page),
review (on click). No polling, no provider or LLM call on initial render.

## 2. Implemented hierarchy

1. **Tier 1 — `#nut-day`:** "Today's intake" → intake kcal · target (or *Target not set yet* / *Target
   unavailable*) → kcal bar (only when both are known) → *N kcal left* / *N kcal over target* → status line
   (*Meals unavailable* or *Couldn't refresh…* + *Try again*) → Protein · Carb · Fat (consumed, `/ target`
   and a bar only when the target exists; protein first and louder) → **Log food** (`btn-volt`, the only
   filled primary on Today).
2. **Tier 2:** *Logged meals* + count (MealLog rows in the four canonical slots; an empty slot is one line
   with its contextual add) → compact hydration (`#qab-water`, same Water state machine).
3. **Tier 3:** *From your plan* (Planned badge; *Log* → *Logged* only after the write confirms) → History,
   Water, Diary disclosures → end-of-day review → Coach link.

The page header drops its explanatory sub-line. Exactly two primary tabs remain (Today | Plan).

## 3. State semantics

| Section | States | Source |
| --- | --- | --- |
| Intake (`data-intake-state`) | `loading` · `confirmed` · `stale` (refresh failed after a confirmed read; values kept and labelled — PR2's post-commit contract) · `unavailable` (never confirmed; `—`, no zero) | `/meal-log/today` `totals` |
| Target (`data-target-state`) | `pending` · `known` · `absent` (`targets: null`) · `unavailable` (read failed or target unusable — never "not set") | `/meal-log/today` `targets` |
| Remaining | shown only when target `known` and intake confirmed; `target − intake` on the displayed rounded numbers; signed as *over target* | arithmetic on the two canonical values (server `remaining` is clamped at 0 and cannot express "over") |
| Ledger (`data-ledger-state`) | `loading` · `available` · `empty` (proven zero rows) · `unavailable` | `/meal-log/today` `meals` |
| Plan shortcuts (`data-plan-state`) | `loading` · `available` · `none` (`exists:false`) · `unavailable` (+ *Try again*) | `/nutrition-plan/active` |
| Hydration | `loading` · `confirmed` · `saving` · `unavailable` · `unconfirmed` (unchanged) | `/water` |

Topology note: target and intake come from **one** read, so a server failure takes both down together
(both shown as unavailable). An unusable target inside a healthy reply is the independent case and shows
*Target unavailable* next to the confirmed intake.

## 4. Deferred / not owned

- **Planned-meal idempotency (known defect, not repaired):** `/api/quick-add-meal` replay lookup is
  user-wide and runs before plan/meal validation. PR3 claims no exactly-once behaviour; it only disables
  the row while in flight, shows *Logged* after a confirmed response, and on an ambiguous outcome warns,
  re-reads the ledger and never re-sends by itself.
- NUTR-PR4 owns food-method convergence (chooser contents, search/barcode/menu/manual/build-meal UX).
- NUTR-PR5 owns the Nutrition Plan; NUTR-PR6 owns insight/coaching; native/Flutter are PR7/PR8.
- `/coach/history` on initial load is the Coach widget's pre-existing behaviour, recorded, not changed.

## 5. Tests

- `tests/test_nutrition_vnext_pr3_daily_contract.py` — IA, one front door, order, placeholders, hydration
  contract, endpoint allow-list, routes/destinations, read shape, locale parity/plain copy.
- `tests/test_nutrition_vnext_pr3_daily_browser.py` — hierarchy EN/TR, over-target, F1–F5 failure
  isolation, empty, stale refresh, real-write freshness (manual log, planned shortcut, delete), ambiguous
  planned write, request topology, first-viewport geometry (320×640, 390×844, 430×844 EN/TR), responsive
  320–1366, keyboard/focus return.
- `tests/test_nutrition_vnext_pr3_daily_non_vacuity.py` — M1–M8 mutations, each proven to fail its guard.
