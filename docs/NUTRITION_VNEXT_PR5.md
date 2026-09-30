# NUTR-PR5 — Nutrition Plan / planned meals (Plan mode of `/nutrition`)

Base: `origin/main` `e10abf9` (NUTR-PR4 #365 + staging docs #366; NUTR-PR4 deployed and production-accepted).
Scope: the quality and mental model of **Nutrition → Plan**. Frontend, tests and docs only.
No backend, route, model, schema, migration, provider, prompt, rate-limit, premium-gate or AI change.

Plan answers three questions, in this order: *what am I planning to eat?*, *do I already have a
plan?*, *how do I intentionally replace it?* The distinctions it keeps mechanically enforceable:
**generated ≠ saved · planned ≠ logged · absent ≠ unavailable · failed replacement ≠ successful replacement.**

## 1. Pre-change inventory (e10abf9)

| Operation | A. UI entry | B. function chain | C. network | D. authority | E. mutation | F. loading | G. empty | H. failed read | I. failed write | J. success timing | K. focus | L. Today dependency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Active plan read | page init | `loadActivePlan` → `getActivePlan` | `GET /nutrition-plan/active` (shared in-flight promise) | NutritionPlan (newest row) | — | nothing drawn | `exists:false` → nothing; the generation form was the page | **swallowed** (`catch {}`): the form showed, so a failed read looked exactly like "no plan" | — | on answer | — | same promise as "From your plan" |
| Shared cache | — | `getActivePlan(force)` / `invalidateActivePlan()` | one GET per invalidation | — | — | — | — | failure not cached; but an OLD failing promise could null a NEWER cached one | — | — | — | yes |
| Current plan | automatic | `renderActivePlanDetail` | — | the read | — | — | — | — | — | — | — | — |
| "+ Create New Plan" | detail header | `resetPlan` | none | — | — | — | — | — | — | — | not moved | — |
| Generation | "CREATE PLAN" (below the food form) | `generatePlan` | `POST /nutrition-plan` (AI) | proposal only | none | **full-screen overlay** ("Calculating…") over the page; button "PREPARING…"; **no single-flight guard** | — | — | toast (`e.message` raw) | cards appear | lost | none |
| Options | cards | `renderPlans` | — | proposal | — | — | — | — | — | — | — | — |
| Selection = save | "SELECT THIS PLAN" | `selectPlan(i, plan, score)` | `POST /nutrition-plan/save` **immediately, no confirmation, no single-flight** (double click = two POSTs) | canonical replacement | deletes + inserts | none | — | — | 4xx → toast; network → toast "Save error: …" (a possibly committed save shown as failed; a re-tap re-sends) | card relabelled "✓ ACTIVE PLAN" client-side; **no canonical re-read**; the current-plan view was not redrawn | none | `invalidateActivePlan()` only; Today re-read on its next redraw |
| Plan tab | tab | `switchTab('plan')` → `applyNutritionNavigation` | none | — | — | — | — | — | — | — | tab | — |
| Today "From your plan" | Today redraw | `loadQuickAddSection` | shared GET | NutritionPlan | via `quickAddMeal` only | `loading` | `none` | `unavailable` + retry | PR3 state machine | — | — | fingerprint locks (`_plannedLocksPlan`) |
| Food preferences | `div.chip` (click listener) + custom input/Add + `span` × | `createFoodChip`, `addCustomFood`, `removeCustomFood` | none | local selection only | — | — | — | — | — | — | chips and × **not keyboard operable** | — |
| Supplements | nav link at the TOP of Plan | `<a href="/supplements">` | none until followed | Supplement cabinet (`/supplements`) | none | — | — | — | — | — | — | — |

Target: Plan showed **no** target at all. Score: the generator's `overall_score` is the average of
the chosen FOODS' micronutrient / bioavailability / gluten ratings from `FOOD_DATABASE` (0–10, not a
rating of portions, calories or the user), yet `renderPlans` presented it as a coloured verdict
(`İyi/Orta/Kötü` → green/orange/red, "Good Plan") and the detail as "Score: 8/10".

## 2. Authority map (unchanged)

| Fact | Authority | How Plan reads it |
| --- | --- | --- |
| Current plan | `NutritionPlan` (newest row, owner-scoped) | the ONE shared `GET /nutrition-plan/active` (`getActivePlan`) |
| Consumed food | `MealLog` | never on Plan (Today only) |
| Daily target | latest `UserSession.target_calories` → `nutrition_targets` projection | the `/meal-log/today` `targets` Today already read — `renderPlanTarget` is fed by `renderDaySummary` / `renderDaySummaryFailure`; **no read of its own** |
| Supplements | `Supplement` via `/supplements` | a link only |
| Generated option | `POST /nutrition-plan` answer | **proposal only** (page memory; never persisted by generation) |
| Replacement | `POST /nutrition-plan/save` | the ONE writer (`savePlanOption`) |

No plan-local consumed state, no client-side plan authority (no storage), no second cache, no
second supplements writer, no new persistent model.

## 3. Target

Source: `/meal-log/today` `targets` (`derive_daily_macro_targets(latest UserSession.target_calories, goal)`).
Every writer of `target_calories` computes it with `calculate_target(tdee, goal)` from the profile
(onboarding, weight update, Coach setup), and the generator builds options around the same latest
session target — so the only provenance claimed is *"Calculated from your profile and goal. New plan
options are built around this calorie target."* No "AI / coach / optimized target".

| `#plan-target[data-target-state]` | Shows |
| --- | --- |
| `pending` | `—` |
| `known` | `2100 kcal a day` · `Protein P g · Carbs C g · Fat F g` (only when all three canonical macro targets exist) · provenance line |
| `absent` (`targets: null`) | *Target not set yet* + "New plan options need a daily target." (no number) |
| `unavailable` (read failed / unusable target) | *Target unavailable* — never 0 |

A refresh that fails after a confirmed read keeps the confirmed target (PR2/PR3 stale contract).

## 4. Current-plan states (`#plan-current[data-plan-state]`)

| State | Source | Shows |
| --- | --- | --- |
| `loading` | before the first answer | "Loading your plan…" (`aria-busy`) |
| `active` | `exists:true` + drawable document | name · *Created DD.MM.YYYY* · Daily total (only when the document has totals) · **Planned meals** (each with a *Planned* badge) · "Planned meals aren't logged…" · demoted rating · *Replace plan* |
| `absent` | `exists:false` | "You don't have a nutrition plan yet." + *Create plan* |
| `unavailable` | read failed (non-2xx, network, malformed, or the pre-existing HTML 500 for a corrupt `plan_data`) | "Your plan couldn't be loaded. This doesn't mean you have no plan." + *Try again* (no Create over an unknown) |
| `invalid` | `exists:true` but no drawable meal object (legacy/corrupt shape) | "Your current plan can't be shown…" + *Replace plan* |

Legacy rows whose `yemekler` is one string render that string (escaped); non-numeric macros render `—`.
The rating is secondary neutral text: *Food choice rating 8/10 · the average micronutrient,
bioavailability and gluten rating of the chosen foods, not a rating of portions or calories* — no
colour, no Good/Fair/Poor label, no health claim. `SCORE_LABELS_EN`/`scoreLabel` are retired.

## 5. Planned meals and the Plan/Today boundary

Plan = planning/review/replacement; Today = daily execution. Plan draws planned meals with
`data-planned="true"` and a *Planned* badge and never says *Logged* (no MealLog read, no
`quickAddMeal`, no `/api/quick-add-meal`, no `/meal-log` on Plan — asserted per function). Today's
"From your plan" remains the only planned-meal writer; its PR3 machinery (pending · unconfirmed ·
logged locks, fingerprint, reload boundary, M9/M10 lines) is byte-identical.

## 6. Generation = proposal

- Entry: *Create plan* (absent) or *Replace plan* (active/invalid) opens `#plan-builder` (closed by
  default, below the current plan; the CTA hides while it is open; *Cancel* closes it and returns
  focus). Nothing runs until **Generate options**.
- Food choices are real toggle buttons (`aria-pressed`, 44 px); custom-food remove is a labelled button.
- `generatePlan` is single-flight (`_planGenInFlight`; button disabled + `aria-busy`); status in
  `#plan-gen-status` (`role="status"`). The full-screen overlay is no longer used: the current plan
  stays drawn during generation.
- Success: options render as **Option** cards with *Use this plan* (index only — the plan document
  no longer rides in a `data-args` attribute). Status: "N options to compare. Nothing is saved until
  you choose one." The current plan, the database and Today are unchanged.
- Failure (4xx/5xx/network/malformed): "Couldn't generate options. Nothing was changed."; a 4xx server
  message is also toasted; earlier options stay; the button re-enables for an explicit retry.

## 7. Replacement state machine

```
option ──Use this plan──► (current state absent) ─────────────────────────┐
   │                                                                       ▼
   └──► (active | invalid | loading | unavailable) ─► CONFIRM dialog ─► SAVING (single flight)
            Keep / Escape / backdrop ─► nothing written, focus → option        │
                                                                                ▼
              4xx ──────────► REJECTED: current plan untouched; option retryable (explicit)
              2xx ──────────► invalidate → ONE shared re-read (Plan + Today) → CURRENT = drawn read
              5xx / network / unreadable ─► invalidate → ONE shared re-read:
                    re-read = the sent document ─► CURRENT (proven)
                    re-read = the old plan      ─► FAILED: "Your plan wasn't replaced…" (explicit retry)
                    anything else / read failed ─► UNCONFIRMED: "Couldn't confirm replacement…";
                                                   these options stay disabled (no resend, ever)
```

- The dialog (`role="dialog"`, `aria-modal`, titled "Replace your current plan?") names both plans
  ("“Option B” will replace your current nutrition plan “Lean plan”. A replaced plan can't be
  restored.") because the save route deletes the stored plan. Focus starts on *Keep current plan*,
  Tab is contained (one load-time listener), Escape/backdrop/Keep cancel with zero writes and
  return focus to the option.
- A proposal is never drawn as the current plan and success is never announced before the save
  answers **and** the canonical re-read ran (`finishPlanSave` is the only place that says *saved*;
  `_planShown` is only assigned by the two current-plan renderers).
- "Proven" compares the plan documents field-by-field (`planDocumentKey`), because the server
  rebuilds the document and may turn `"420"` into `420`.

## 8. Backend save invariant (unchanged, now regression-pinned)

`save_nutrition_plan`: `parse_plan_score` → `validate_nutrition_plan_for_save` → `delete()` → `add` →
`commit`. AST order test plus a real-route test posting six invalid bodies (bad/out-of-range score,
unknown key, no meal, non-list items, missing plan) — each 400, the current plan byte-identical after
each — then a valid save replaces it with exactly one row.

## 9. Cache, invalidation and races

`getActivePlan` now keeps an epoch (`_activePlanEpoch`, bumped by every new read and by
`invalidateActivePlan()`); `loadActivePlan` and `loadQuickAddSection` drop an answer whose read was
replaced and await the current one instead (no extra GET). A failing OLD read no longer nulls a newer
cached read. After a confirmed save: invalidate once → `loadActivePlan()` + `loadQuickAddSection()`
share ONE GET → Plan and Today converge on the canonical plan; Today's own later redraw reuses it.

| Race | Handling (tested) |
| --- | --- |
| A. slow active read, switch Today/Plan | one GET; both sections stay `loading` (never "no plan"), then draw it |
| B. generation running, Today → Plan | one POST; options appear; current plan intact |
| C. double Generate (click, forced click with `disabled` stripped, direct calls, Enter) | exactly one POST, one provider call |
| D. double select/confirm (forced clicks, direct calls) | exactly one save |
| E. save success, then a stale old read answers | dropped by the epoch; Plan and Today stay on B |
| F. save 4xx while a plan exists | plan untouched, option retryable |
| G. ambiguous save | never re-sent; one re-read decides proven-new / proven-old / unconfirmed |
| H. proposal B while A active | A stays current in Plan, DB and Today |
| I. confirmed B → Today | Today's rows show B without another GET |
| J. old-plan quick-add answer after B | PR3 fingerprint: toast + ledger re-read only; B's row stays *Planned* and actionable |

## 10. Request topology (measured, `test_request_topology_per_step`)

| Step | Before (e10abf9) | After |
| --- | --- | --- |
| A. initial `/nutrition` | `/nutrition`, `/meal-log/today`, `/nutrition-plan/active`, `/water`, `/notifications/unread-count`, `/coach/history` ×1 | **identical** |
| B. select Plan | 0 | 0 |
| C. Plan → Today | `/meal-log/today` ×1 | `/meal-log/today` ×1 |
| D. Today → Plan | 0 | 0 |
| open Create/Replace | — | 0 |
| E. Generate | `POST /nutrition-plan` ×1 (duplicates possible) | ×1, provider ×1 (single-flight) |
| F. choose option | `POST /nutrition-plan/save` immediately | 0 (confirmation) |
| G. confirmed save | save ×1, no re-read | save ×1 + active ×1 (shared by Plan + Today) |
| Today after save | active ×1 on next redraw | `/meal-log/today` ×1, active 0 |
| H. refused save | save ×1 | save ×1, no re-read |
| ambiguous save | save ×1 (re-tap re-sent) | save ×1 + active ×1, never re-sent |
| idle | 0 | 0 (no polling) |

No Supplements request, no provider/LLM call on open, on initial render or on an active-plan refresh.

## 11. Accessibility and responsive

Exactly two primary tabs and one H1; Plan headings h2 (*Your nutrition plan*) → h3 (Daily target,
Current plan, builder) → h4 (Planned meals / categories / Generated options) → h5 (meal and option
names), never skipping a level. Visible focus on chips, option and dialog buttons; `role="status"`
regions for plan and generation messages; ≥44 px for Create/Replace/Generate/Cancel/Use/chips/dialog
buttons; states always in words (no colour-only). Verified at 320/390/430/768/1024/1366 in EN/TR with a
long Turkish meal name (no horizontal scroll, no clipped names, dialog actions on screen), plus
`prefers-reduced-motion: reduce` and 150 % root text at 320 px.

## 12. Security / rendering

Every generated or stored string goes through `esc()` (names/items) or `fmtNum()` (numbers); the
dialog body is `textContent`; the rating line of the options is `textContent`. The plan document no
longer travels in a `data-args` attribute. `tests/test_plan_save_validation.py` pins the exact
interpolation set of `renderPlans`, `renderActivePlanDetail`, `loadQuickAddSection`, `planCta` and
`renderPlanStateBlock`; `tests/js/nutrition_plan_render.test.js` (poisoned plans) still passes; a PR5
browser test proves generated and persisted markup renders as text end to end.

## 13. Supplements and Grocery

Supplements stays a child link (`.nutrition-child-domain` → `/supplements`), now placed after the
plan sections; no inline CRUD, no preload, no request. Grocery Guide is **absent** (no UI, copy or
"coming soon"): the contract still defers it until a real source/output contract exists.

## 14. Known limitations (deferred, not claimed)

- **Planned-meal idempotency (unchanged):** `/api/quick-add-meal` replay is user-wide and precedes
  plan/meal validation; exactly-once across reloads/devices is not guaranteed and PR5 claims nothing
  stronger. Row locks remain page-life presentation containment.
- **Replacement freshness:** `/nutrition-plan/save` has no expected-plan precondition (unlike
  Training's `expected_plan`); a second tab could replace the plan between this page's read and its
  save. The confirmation names the plan this page last read. A backend precondition is out of PR5 scope.
- Pre-existing, reported: a corrupt `plan_data` row makes `/nutrition-plan/active` raise (HTML 500),
  which Plan shows as *unavailable*; the non-premium weekly-quota message of `POST /nutrition-plan`
  is Turkish-only server text (shown as a toast in EN too).

## 15. Tests

- `tests/test_nutrition_vnext_pr5_plan_contract.py` (20): Today|Plan on `/nutrition`; Plan order and
  closed builder; no new `/nutrition-plan*` route; one shared active read, no storage authority;
  generation ≠ save (writer ownership); nothing current before save + re-read; confirmation and
  single flight; absent ≠ failure; target absent ≠ failure; Planned ≠ Logged and PR3 lines intact;
  neutral score; Supplements link only; Grocery absent; save validates before delete (AST) and six
  invalid saves never destroy the plan (real route); no eager generation/provider; NutritionPlan
  columns unchanged; EN/TR parity and bounded copy.
- `tests/test_nutrition_vnext_pr5_plan_browser.py` (39): scenarios 1–14 of the brief, the invalid and
  corrupt states, races A–J, request topology, XSS end to end, headings/keyboard/dialog, responsive
  320–1366 EN/TR, reduced motion + large text.
- `tests/test_nutrition_vnext_pr5_plan_non_vacuity.py` (13): P5-N1…P5-N12 (plus a drift guard), each
  mutation run in the served script / template / real route and shown to fail its guard.
- Intentionally superseded older assertions: PR2 navigation browser (Plan now opens on the current
  plan, generation behind *Create plan*), UX-3 PR4 placement browser (same), plan-save validation
  (new pinned interpolation sets; the attribute test now asserts no plan document in `data-args`),
  `tests/js/nutrition_plan_render.test.js` (save tests follow the proposal → save → re-read contract).
