# NUTR-PR4 — Food logging convergence (one "Log food" front door)

Base: `origin/main` `9576d5a` (NUTR-PR3, #361; deployed and production-accepted).
Scope: the food-logging entry model of **Nutrition → Today**. The chooser behind `#log-food-btn`
now offers the user intents (Search · Barcode · Menu · Quick add · Build meal, with Photo as a
secondary method). Every method hands off to the workflow that already owns it.
There is no backend, route, model, schema, migration, provider or AI change. The PR3 hierarchy,
first viewport and initial request topology are unchanged.

## 1. Pre-change inventory (9576d5a)

| Path | A. entry control | B. function chain | C. network | D. authority | E/F. MealLog write · when | G. failure | H. cancel | I. focus after | J. works? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Front door | `#log-food-btn` | `openLogSheet` | none | — | no | — | Esc/backdrop → `dismissLogSheet` | into chooser; back to opener | yes |
| Manual / search | chooser "Manual Entry" | `logManual` → `openManualSheet` → `searchFood` → `selectFood` → `openSelectServing` (serving modal, `select` mode) → `addSelectedFood` → `logMeal` | `GET /api/food/search`, `GET /api/food/<id>/servings` or `/servings-by-name` (discovery); `POST /meal-log` (`provider_food`, or `override_macros` without a provider id) | provider = discovery; server re-reads serving truth; MealLog | yes · only on "Log meal", one POST per selected food (per-food idempotency key, replay-safe partial retry) | error toast, unwritten foods stay listed | sheet closes, nothing written | search field; on close **lost** (body) | **partly**: the serving modal painted UNDER the sheet (same z-index, earlier in the DOM), so its Add button was covered (§6) |
| Manual free text | same sheet textarea | `logMeal` | `POST /meal-log` (`yemekler`; server LLM estimate) | MealLog | yes · on "Log meal" | 502 → error toast, nothing written | nothing written | lost | yes; a second **Enter** during the write sent a second POST with a new key (§6) |
| Barcode | chooser "Scan Barcode" | `logScanBarcode` → `openScanOverlay` → (BarcodeDetector or manual code) → `resolveBarcode` → `openMealLogServing` (`meallog` mode) → `confirmServingModal` → `logProviderFoodToLedger` | `GET /api/food/barcode` (discovery); `POST /meal-log` `provider_food` (`discovery_source: barcode`) | discovery; MealLog | yes · only on serving confirm | 404 → "Product not found"; other → "Couldn't read barcode"; nothing written | overlay/modal close, nothing written | lost | yes |
| Menu | chooser "Menu Scanner" | `logMenuScan` → `CW.startScan` (Coach widget) → QR decode → `CW.processMenuUrl` | `POST /api/proxy/scan-menu` → `POST /api/menu/analyze` (on scan only) | analysis/candidates | **no** (analysis). The widget's dish "Add" posts `/meal-log` with slot tokens `kahvalti/ogle/aksam/ara`, which the web write rejects (400, `WEB_MEAL_SLOTS` expects `Kahvaltı/Öğle/Akşam/Ara Öğün`) — **pre-existing, not fixed here (§6)** | widget messages; CW missing → toast | widget close | widget | analysis yes; dish → log bridge broken |
| Photo | chooser "Take Photo" | `logTakePhoto` → file picker → `onPhotoPicked` → `openPhotoConfirm` → `submitPhotoMeal` | `POST /meal-log` (`image` + `yemekler` = note or slot label) | MealLog (photo stored if S3 on; fail-open) | yes · only on the modal's "Log meal" | error toast | modal close, nothing written | not moved into the modal; lost on close | **yes** — see §4 |
| Voice | chooser "Voice Entry" | `logVoice` → `#voice-sheet` | none | — | **never** | — | close | — | **no** — placeholder ("in the mobile app") |
| Planned shortcut | "From your plan" rows | `quickAddMeal` | `POST /api/quick-add-meal` (Idempotency-Key) | NutritionPlan → MealLog (`ai_plan`) | yes · immediately on tap | PR3 state machine: pending · unconfirmed · logged; 4xx retry | — | row | yes |
| Diary builder | Diary disclosure | `loadDiary` · `addDiaryFood` → `openServingModal` (`diary`) → `confirmServingModal` · `logDiaryMeal` | `GET /api/diary/today`; `POST /api/diary/meal`, `/item`, `PATCH/DELETE /api/diary/item/<id>` (staging); `POST /api/diary/meal/<id>/log` | CustomMeal/CustomMealItem staging → MealLog | only on "Log this meal" (server claims `is_logged` atomically) | a failed/5xx **read** rendered four EMPTY meals (unknown drawn as empty, §6) | staging persists server-side | — | yes |
| Slot add / edit | empty-slot "+ Add to this meal", `.mc-edit` | `logManualSlot` / `quickEditMeal` → `openManualSheet` (slot preselected) | as manual | as manual | as manual | as manual | as manual | lost on close | yes (edit = add to that slot) |
| Delete | `.mc-del` | `deleteMeal` | `DELETE /meal-log/entry/<token>` (`If-Match`) | MealLog correction | deletes | 404/412/503 classified | confirm dialog | — | yes |

## 2. Canonical chooser

`#log-sheet` is a titled (`h2#log-sheet-title`, "Log food") and described (`#log-sheet-lead`,
"How do you want to log it?") modal dialog with an explicit 44px close button. Primary methods,
in visual = reading order:

| Method | EN / TR | Hands off to | Writes? |
| --- | --- | --- | --- |
| Search food (`logManual`) | Search food · Find a food and choose the amount / Yemek ara · Besini bul, miktarını seç | the search sheet (unchanged workflow) | only its "Log meal" |
| Scan barcode (`logScanBarcode`) | Scan barcode · Look up a packaged product / Barkod tara · Paketli bir ürünü bul | the scanner → lookup → serving | only the serving confirm |
| Scan menu (`logMenuScan`) | Scan menu · Get suggestions from a restaurant menu's QR code / Menü tara · Restoran menüsünün QR kodundan öneri al | Coach widget scanner (reused, not moved) | never (analysis) |
| Quick add (`logQuickAdd`) | Quick add · Log a meal from your plan / Hızlı ekle · Planındaki bir öğünü kaydet | scroll + focus into the existing "From your plan" rows | only a row tap (`quickAddMeal`) |
| Build meal (`logBuildMeal`) | Build meal · Combine foods, then log them as one meal / Öğün oluştur · Besinleri birleştir, tek öğün olarak kaydet | `switchTab('diary')` → the meal builder | only its "Log this meal" |

Then an "Other ways / Diğer yollar" group with **Log with a photo** ("Calories are estimated from
your note" / "Kalori, yazdığın nottan tahmin edilir").

The Quick add option's sub-line reads `#quick-add-section[data-plan-state]`, so it makes no read.
It says *Log a meal from your plan*, *No active plan yet*, *Your plan couldn't be loaded.*, or
*Checking your plan…*. A failed plan read is never shown as "no plan".

Interaction:
- Focus moves to the first method on open.
- Tab is contained while the chooser is open. One keydown listener is installed at load, never per open.
- Escape, the backdrop, or Close returns focus to "Log food" and resets `aria-expanded`.
- A method launched from the chooser hands focus to its surface.
- Cancelling that surface returns focus to its opener, or to "Log food" (`_returnFocus`).
- Escape closes a surface through its OWN close function, not by stripping a class.

## 3. Write boundaries (unchanged authorities)

MealLog is the only consumed-food authority. CustomMeal/CustomMealItem are staging. NutritionPlan
is plan truth. Provider search and barcode lookup are discovery, and menu analysis produces
candidates. WaterLog is untouched. The chooser functions contain no `fetch`, and no chooser method
adds a caller to a writer. The callers of `POST /meal-log` are still `submitPhotoMeal`,
`postSelectedFood`, `submitMealLog` and `logProviderFoodToLedger`. The only caller of
`/api/quick-add-meal` is `quickAddMeal`, and the only caller of the diary commit is `logDiaryMeal`.

Planned rows keep PR3 byte-for-byte: `_plannedWriteLocks` pending/unconfirmed/logged, the
confirmed-redraw lock, the ambiguous lock, 4xx retry, the active-plan fingerprint, the reload
boundary, and the M9/M10 lines.

## 4. Photo and Voice decisions

- **Photo — functional, writes MealLog only on explicit confirm, KEPT (demoted to secondary).**
  The photo is not analysed. It is attached to the log (stored when S3 is enabled; fail-open), and
  the calories come from the server's existing text estimate of the note, or of the meal-slot name
  when the note is empty. The old copy ("auto-calculated") implied analysis. The new copy says
  *Calories are estimated from your note*. Persistence timing is unchanged.
- **Voice — not functional, a placeholder that could never log food, RETIRED.** The chooser
  option, `#voice-sheet`, `logVoice`/`closeVoiceSheet`, its CSS and the five voice-only locale keys
  are removed. No voice logging or AI call was added.

## 5. Request topology (measured in the browser tests)

| Step | Requests |
| --- | --- |
| A. initial `/nutrition` | `/nutrition`, `/meal-log/today`, `/nutrition-plan/active`, `/water`, `/notifications/unread-count`, `/coach/history` — ×1 each, **identical to PR3** |
| B. open chooser / C. close (Esc, backdrop, Close; ×3 cycles) | **0**. Listener and MutationObserver counts are unchanged across the cycles |
| D. Search selected | 0 (provider search only after typing ≥2 chars) |
| E. Barcode selected | 0 (lookup only after a code is scanned/entered) |
| F. Menu selected | 0 app requests. The widget lazy-loads its QR library (third-party CDN script, pre-existing behaviour on first scan) |
| G. Quick add selected | 0 (reuses the loaded plan state; no duplicate active-plan read) |
| H. Build meal selected | `/api/diary/today` ×1 on first open, 0 afterwards (the same lazy read the disclosure always had) |

There is no polling, and no LLM or provider call on open.

## 6. Defects found and how they were treated

| Defect (pre-existing on 9576d5a) | Treatment |
| --- | --- |
| Serving modal painted under the Search sheet (same `--z-overlay`, earlier in the DOM), covering its Add button, so the Search → serving → confirm path was blocked on the page. | **Fixed** (CSS only): `#serving-modal` takes `calc(var(--z-overlay) + 1)` (toasts stay above). |
| Diary read failure drew four empty meals (unknown shown as empty). | **Fixed**: `#diary-meals[data-diary-state]` loading/available/unavailable, "Your meal builder couldn't be loaded." + Try again, and a generation guard. Staged items live on the server, so nothing is discarded. |
| A second Enter during a free-text/search log sent a second `POST /meal-log` with a new key. | **Fixed** (smallest UI containment): `logMeal` is single-flight; the body is `submitMealLog`, unchanged. |
| Focus was lost (to `<body>`) after cancelling the search sheet, barcode scanner, photo modal or serving modal. | **Fixed**: deterministic return (§2). |
| The Coach widget's menu dish "Add" posts slot tokens the web write rejects (400). The menu → log bridge does not work. | **Not fixed** (Coach widget scope; §14 "preserve that truth"). The chooser copy promises suggestions, not logging. Follow-up: map the widget's slot tokens to `WEB_MEAL_SLOTS`. |
| Barcode/search confirm: a network failure is reported as an error, and a re-tap sends a new idempotency key (the write may already have committed). | **Characterized, not changed**. It predates PR4, PR4 does not make it worse, and repairing it means request-bound idempotency across methods (deferred, like planned-meal idempotency). |

## 7. Tests

- `tests/test_nutrition_vnext_pr4_log_food_contract.py` checks:
  - one front door and one dialog;
  - method order and labels, no nesting, no tabs, and every method only inside the chooser;
  - plain EN/TR copy;
  - Voice retired and Photo retained;
  - the chooser functions never fetch;
  - the writer ownership map, and no new route, mode or destination;
  - locale parity.
- `tests/test_nutrition_vnext_pr4_log_food_browser.py` checks:
  - the dialog, the focus trap, and 0-request open/close with listener counts;
  - routing for each method;
  - photo logs only on confirm;
  - search, barcode, menu and staging write boundaries;
  - barcode not-found and cancel;
  - search and menu failure isolation;
  - quick add through the planned row (M9/M10 locks hold when routed again);
  - no-plan vs plan failure;
  - the builder staging commit, and builder read failure;
  - exactly one write after open/close/open, method cancel, Today reselect and dismissal;
  - single-flight on a double Enter;
  - per-method topology;
  - geometry at 320/390/430/768/1024/1366 in EN/TR, and visible focus.
- `tests/test_nutrition_vnext_pr4_log_food_non_vacuity.py` — N1–N10, each shown to fail its guard,
  with the served script checked to be the mutated one.
- PR3 tests updated deliberately (method list only):
  - `CHOOSER_METHODS` in the PR3 contract now names the PR4 methods, and the "unchanged chooser" test was renamed;
  - `tests/test_i18n.py` checks the new copy and that the voice placeholder is gone.
  - `tests/test_sprint13_nutrition_write_convergence.py` F4 guard now inspects `submitMealLog`, the
    write body behind the single-flight `logMeal` wrapper, and asserts that `logMeal` only delegates to it.
- Every other PR3 invariant is untouched.
