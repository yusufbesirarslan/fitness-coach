# Nutrition VNext: discovery and cross-platform contract (PR0)

## Executive outcome and baselines

**Decision:** retain Today, Plan, Coach, Progress as the four global destinations. Within Plan, present Training and Nutrition as siblings. Within Nutrition, expose Today and Plan as the two primary modes. Keep `/training`, `/nutrition`, and `/supplements` as the web canonical routes. This is an implementation contract, not a runtime change.

| Repository | Fetched `origin/main` | Audit workspace |
| --- | --- | --- |
| `yusufbesirarslan/fitness-coach` | `5ec5410614ef97f01d62248fc8a508010c0c0ce2` | `axisai-worktrees/nutrition-vnext-pr0-discovery-contract`, branch `docs/nutrition-vnext-pr0-discovery-contract` |
| `yusufbesirarslan/axisai-mobile` | `0da1f1ec793fe094aea9d955cab48706b3bd1afb` | `axisai_mobile/.worktrees/nutrition-vnext-pr0-mobile-audit`, branch `docs/nutrition-vnext-pr0-mobile-audit` (read-only audit) |

The existing web checkout was dirty on `fix/f7-coach-history-pagination`; the old mobile audit checkout was detached and dirty. Neither was reused. Recheck both `origin/main` refs before integrating downstream work. Current code, cited below, takes precedence over older design documents.

Both remotes were re-fetched after discovery and remained at the SHAs above; no main delta required rebase or revalidation.

## Current product truth and historical placement

The web global navigation maps the Plan destination to `/training` and activates it for Training, Nutrition, and Supplements ([`app/nav.py`](../app/nav.py)). GET `/training` unconditionally renders `plan.html`; `UIUX_PLAN_V2_ENABLED` is retired ([`app/blueprints/training.py`](../app/blueprints/training.py), [`app/feature_flags.py`](../app/feature_flags.py)). `plan_facts` reads bounded Nutrition target, today's `MealLog` aggregate, `NutritionPlan` presence, and `Supplement` count independently. `plan_presenter` maps those facts without writes, HTTP, or AI ([`app/services/plan_facts.py`](../app/services/plan_facts.py), [`app/plan_presenter.py`](../app/plan_presenter.py)). The rendered Training card is styled and described as primary; Nutrition is labelled child and Supplements is nested below it ([`templates/plan.html`](../templates/plan.html)). Thus the *read-only composition* is intentional, while the visual subordination is the incremental Training-first placement that PR1 must correct. `docs/PLAN_DOMAIN_CONVERGENCE.md` records that the prior work deliberately retained `/nutrition` and `/supplements` and did not redesign Nutrition.

`/nutrition` renders five peer tab panels: Today, Diary, Plan, History, Water. It also links to the canonical `/supplements` cabinet ([`templates/nutrition.html`](../templates/nutrition.html)). Today already shows intake, meals, quick add, hydration, Coach handoff, and end-of-day review; the floating Log action opens food methods. The large script fetches diary, plan, food, water, and history endpoints ([`static/nutrition.js`](../static/nutrition.js)). This is the present UI, not evidence that those five concepts deserve equal navigation rank. The Nutrition/Supplements visual convergence changed presentation, not their persistence or route ownership.

Flutter's route registry has exactly four shell destinations. Nutrition search, serving, manual entry, and barcode routes are owned by Nutrition but placed in the Today branch at `/today/nutrition/add...`; there is no Nutrition root under Plan (mobile `lib/app/navigation/app_destination.dart` and `app_route_registry.dart` at the SHA above). The Today screen embeds a `NutritionDiaryCard`. Its live repository consumes `/api/v1/nutrition/diary/today`, search, servings, barcode, log, slot-change and delete **only in configured native-auth-ON composition**. `AXISAI_NATIVE_AUTH_ENABLED` defaults to false (`lib/core/config/native_auth_rollout.dart`); auth-OFF composition has a null Nutrition repository, so the Add Food action is unavailable. Development composition uses fixtures. A screen or model alone is not proof of production support (mobile `lib/app/composition/app_composition.dart`, `lib/app/router/app_router.dart`, and `lib/features/nutrition/data/live_nutrition_repository.dart`). `PlanScreen` loads a `WeeklyPlanRepository` and presents Training; it has no Nutrition domain selector or Nutrition Plan model (mobile `lib/features/plan/presentation/plan_screen.dart`). The configured Progress repository is an unavailable implementation, while `CoachScreen` is an explicit “coming later” placeholder. No native Supplements, water, Nutrition Plan, menu scan, or Grocery repository/route was found in current `lib`; these are capability gaps, not reasons to delay web IA work.

## Authority, route, workflow, and parity map

| Capability / type | Canonical read and write authority | Web entry / route | Native status and failure rule |
| --- | --- | --- | --- |
| Plan landing / destination | `plan_facts` bounded reads; **no Plan write authority** | `/training`, `plan.html` | `/plan` is Training-only. Each child summary must fail independently. |
| Daily targets / read | latest `UserSession` target; `nutrition_targets` owns target derivation for detailed Nutrition flows, while Plan directly projects the persisted target; no Plan-local writer | `/nutrition`, `/meal-log/today`, Plan summary | Diary DTO exists; a cross-platform day presentation contract is still missing. Missing target is unknown, never 0. |
| Consumed meals / ledger | `MealLog`; `/meal-log` and shared mobile log service write canonical ledger | Nutrition Today, History, food sheet; `/meal-log`, `/meal-log/today`, `/meal-log/history` | Live diary and POST `/api/v1/nutrition/logs`. Failed read is unavailable, not no meals. |
| Meal correction/deletion | canonical `MealLog` entry token mutation; diary item paths own staged `CustomMealItem` changes | `/meal-log/entry/<token>`, `/api/diary/item/<id>` | PATCH/DELETE `/api/v1/nutrition/logs/<token>`; keep revision/conflict semantics. |
| Diary/custom meal / workflow | `CustomMeal`/`CustomMealItem` staging; explicit `/api/diary/meal/<id>/log` creates a consumed record | Diary tab and builder routes in `app/blueprints/nutrition/diary.py` | No equivalent full builder in live native repository. Staging is not intake. |
| Food search, barcode, servings / entry methods | provider discovery and serving services; `MealLog` only after explicit log | `/api/food/search`, `/api/food/barcode`, `/api/food/<id>/servings`, `/meal-log`; JS serving modal | Live native search/barcode/servings/log through `/api/v1/nutrition/...`. Provider failure must not invent nutrition values. |
| Menu analysis / input | `/api/menu/analyze` and `/api/proxy/scan-menu`; explicit log remains separate | Nutrition Log sheet delegates to the Coach widget scanner (`CW.startScan`) when available | No native contract or flow found. Analysis is not consumed food. |
| Quick add / workflow | `/api/quick-add-meal` reads `NutritionPlan`, writes `MealLog` with `source=ai_plan`, and supports a supplied idempotency key | Nutrition Today shortcut | No native planned-meal shortcut found. Existing key replay is user-wide and precedes plan/meal validation; bind key to request and plan revision before stronger exactly-once claims. |
| Nutrition Plan / plan data | `NutritionPlan`; validated replacement by `/nutrition-plan/save`, latest read by `/nutrition-plan/active` | Nutrition Plan tab; generate via POST `/nutrition-plan` | No native Nutrition Plan API/model/repository found. `exists:false` differs from failed read. |
| Hydration / detail and inline action | `WaterLog` via GET/POST `/water` | Today quick action and Water tab | No `/api/v1` hydration contract or native repository found. Read failure differs from zero cups. |
| History / secondary detail | `MealLog` history | History tab, `/meal-log/history` | No native history view found; preserve access while reducing primary tabs. |
| Supplements / child capability | `Supplement` and `app/blueprints/supplements.py` add/edit/delete | `/supplements`, Nutrition and Plan links; Profile also projects supplements | No native API/model/repository found. Keep `/supplements` sole cabinet writer; missing list differs from read failure. |
| Grocery Guide / prospective Plan capability | No dedicated Grocery route, service, or persistent model found in current Nutrition files | No current Nutrition UI entry found | Defer product promise until a source and output contract exist; never present as already shipped. |
| Coach review / contextual action | Coach's authorized tools plus canonical services; no Coach Nutrition ledger | `/coach` link and `/meal-log/review` | No automatic send, no private facts in URL, no advice from partial/failed reads. |

The existing web browser and native API endpoints are different transports over selected shared authorities. They are **not** interchangeable: native has no equivalent for several browser-session routes. Profile supplement projection is a second *read entry*, not a second cabinet writer. Plan and Nutrition both display Nutrition facts, but only Nutrition owns the detailed workflows. The existing Coach staged-food tool logs via `MealLog` on confirmation ([`app/services/ai_coach.py`](../app/services/ai_coach.py)); it must continue to require explicit authorization.

## Target information architecture and candidate decision

| Candidate | Benefit | Cost / verdict |
| --- | --- | --- |
| Keep five Nutrition peers | No immediate navigation migration | Diary, History, and Water compete with daily state and planning; rejects the intended user mental model. **Reject.** |
| Add Nutrition as fifth global destination | One-tap access from every screen | Contradicts the established four-destination shell and splits the forward-looking Plan domain. **Reject.** |
| Plan siblings plus Nutrition Today/Plan | Matches existing web canonical routes and read-only Plan composition; leaves workflows accessible | Requires navigation/focus migration and native API closure. **Adopt incrementally.** |

```text
Today             Plan                    Coach           Progress
                  ├─ Training
                  └─ Nutrition  (/nutrition)
                     ├─ Today  (default)
                     │  ├─ Log food → search | barcode | menu | quick add | build meal
                     │  ├─ Logged meals; planned meal shortcuts; hydration
                     │  └─ History (detail), Review with AxisAI (contextual handoff)
                     └─ Plan
                        ├─ Targets; active Nutrition Plan; planned meals
                        ├─ Generate/replace; Grocery Guide when supported
                        └─ Supplements → /supplements
```

**Accept with corrections:** two Nutrition modes are appropriate; History needs an accessible secondary destination, and Water needs an inline action plus optional detail. Grocery Guide is a deferred capability because the current source was not found. Supplements remains a Nutrition child but keeps its canonical URL. Do not introduce web `/plan` or a fifth global destination in this sequence. Training may open first in Plan, yet both domain choices must use equal heading rank, weight, and independent entry/failure states. A Plan overview reads bounded summaries; selecting a domain opens its canonical route. Do not mount full Nutrition, diary, barcode, menu, and Training workflows on the landing page.

### Nutrition Today and Plan presentation contract

Today shows a factual daily state in the first viewport: intake/target/remaining when known, protein and other macro progress only when canonical targets exist, then one dominant **Log food** action. Show logged meals as consumed records, a visually distinct “From your plan” group, hydration, and an optional server-derived insight. Do not use `Ready`, `Saved`, internal enum names, or safety claims as generic status. If targets are missing, show “Target not set”; if intake fails, show “Meals unavailable” with retry. Keep the rest of the page usable when one read fails. Logging methods are a chooser under one action, not five persistent destinations. History remains reachable from Today without occupying a primary mode.

Nutrition Plan shows target provenance, latest plan state, planned meals, and explicit generation/replacement. Generated output is a **proposal** until save succeeds. Replacement must reveal that the previous plan changes and must not claim success before the server confirms. The existing `/nutrition-plan/save` validates score and schema before replacing rows ([`app/blueprints/nutrition/plan.py`](../app/blueprints/nutrition/plan.py)); preserve that order. The existing active read is a browser session API, so native needs a protected contract before this screen can be implemented. Supplements links to the existing cabinet. Grocery appears only after a real server source and availability state are defined.

### Planned versus logged meals and food convergence

Label planned items “Planned”; label canonical `MealLog` items “Logged”. A planned item may offer “Log this meal” only when the source plan and required serving values are usable. That action submits through an existing canonical log path and refreshes the ledger. Show “Logged” only after a confirmed write. A timeout or conflict leaves status uncertain and asks the user to refresh; it must not silently retry a possibly committed write. `/api/quick-add-meal` already calls `meal_idempotency.commit_once` for a supplied idempotency key, but its existing replay lookup is user-wide and runs before current plan/meal validation. Reusing a key for a different request can report the older meal as success. A future PR must bind key to request fingerprint and plan revision, then characterize duplicate taps, cross-device repeats, intentional second servings, source attribution, and correction before claiming exactly-once behavior. Do not infer that a same-name meal is the same consumed event.

Search, barcode, menu analysis, quick add, and meal building converge at the user-facing chooser. Provider results and menu analysis are candidates; `CustomMeal` is staging; the `MealLog` write is the consumption boundary. Preserve provider serving authority and validation. Do not merge backend services merely to make the chooser look uniform. A logged item may be corrected/deleted only through canonical entry mutation, with conflict/revision handling retained.

### Adaptive coaching and shared read model

Define a future **server-owned** bounded `NutritionDayView` from existing canonical facts, not a new ledger: `day`, `target` `{state,value,unit}`, `intake` `{state,totals,meal_count}`, `hydration` `{state,amount}`, `plan` `{state,summary}`, and `next_action` `{state,kind,label_key}`. Each section state is `loading | available | empty | unavailable | invalid`; `partial` describes a composition with both usable and unavailable sections. Omit inferred adherence, health scores, “behind” thresholds, or personalized prescriptions until there is a reviewed server rule and reliable inputs. `next_action` can be deterministic “Log food” when the ledger is readable, “Set target” when missing, or “Retry” when a required read failed. No LLM/provider call on initial render. “Review with AxisAI” opens Coach with a narrow typed in-memory context or server lookup; it never sends a message automatically or serializes private nutrition facts into a URL. Coach mutations remain gated and use the canonical service; refresh reads after success.

## Failure, performance, design and parity requirements

For every independent section distinguish `loading`, `available`, `empty`, `unavailable`, and `invalid`. `unknown` is the factual value when no safe conclusion exists. Zero is shown only after a successful read proves zero. Thus no plan, no meals, no supplements, missing target, and zero hydration are all distinct from request failure. A partial Plan/Nutrition read must neither blank a healthy sibling nor produce a coaching claim. A failed write has `pending/uncertain` semantics until a fresh canonical read resolves it.

Plan landing keeps the present bounded aggregate/presence/count pattern, without provider calls, AI, N+1 reads, or full sub-app imports. The current `plan_facts` implementation performs separate bounded reads for target, meal aggregate, plan presence, and supplement count; this is a code baseline, not a measured production latency budget. The current Nutrition script starts `/meal-log/today`, `/nutrition-plan/active` (shared in-flight by plan and quick-add), and `/water` at page initialization; Diary and History load on tab entry (`static/nutrition.js`). Nutrition Today may fetch the day view and independent hydration/plan snippets, but opens scanners, providers, history, and generation lazily. Do not poll or request LLM inference during initial paint. Instrument request counts and latency in the implementation PR before setting numerical budgets.

Web and Flutter share destination hierarchy, source authorities, state vocabulary, action meaning, and failure/unknown rules. They may differ in layout, CSS, sheet/navigation presentation, and component implementation. Native-auth-OFF must remain honestly unavailable, never fall back to fixture meals. A native Coach review handoff is gated on a real Coach destination and context contract; the current placeholder cannot fulfill it. Candidate semantic primitives are Page, SectionHeader, PrimaryButton, SecondaryButton, DomainCard, ProgressMetric, MealRow, EmptyState, ErrorState, Disclosure, and InsightCard. Map existing web design tokens and Flutter `AxisAiTokens` into roles for surface, text, accent, danger, spacing, radius, control size, and typography; do not copy CSS literals into Dart. No primitives are implemented in PR0.

Future acceptance runs at widths **320, 390, 430, 768, 1024, 1366** in **TR and EN**: no page overflow, interactive targets at least 44 px, one H1 per destination and logical subheadings, keyboard access and visible focus, text labels on navigation, no raw enum/internal key, first viewport contains current Nutrition state and primary action, at most two primary Nutrition modes, safety context remains visible, and failures never render as empty data. Confirm dark/reduced-motion and large-text behavior alongside responsive checks.

## API and native capability gaps

| Gap | Severity / owner | Needed before |
| --- | --- | --- |
| Protected Nutrition Plan read, generation/save or safe equivalent, including absent/error and replacement contract | High / backend API | Native Nutrition Plan |
| Protected hydration read/write with units, day boundary, and read failure states | High / backend API | Native Nutrition Today parity |
| Protected Supplements list/CRUD with ownership and validation | High / backend API | Native cabinet |
| Server-owned bounded Nutrition day presentation/insight contract | Medium / backend API + product | Native adaptive presentation; web may initially compose existing reads |
| Native planned-meal quick-add provenance and duplicate policy | Medium / backend API + product | One-tap planned meal logging |
| Native menu analysis and custom meal builder contracts | Medium / backend API | Full Log Food method parity |
| Bounded history endpoint with paging/period semantics | Medium / backend API | Native History detail |
| Native Coach conversation/context contract and available destination | High / mobile + backend Coach | Native “Review with AxisAI”; web PR6 may proceed independently |
| Grocery source/output contract | Deferred / product + backend | Any Grocery UI promise |

These are missing mobile capabilities, not demonstrated blockers for web PR1 or PR2. Security and response semantics must be characterized before exposing browser session routes to native clients. No native client may scrape HTML or reimplement target/insight business rules locally.

## Downstream PR sequence, dependencies, rollback and gates

`PR0 → PR1 → PR2 → hydration reliability repair → PR3/PR4/PR5 → PR6`; API contract work starts after PR0 and gates native feature PR8; PR9 closes parity. Each PR keeps `/training`, `/nutrition`, `/supplements`, canonical tables, and existing write endpoints unless its own compatibility plan explicitly changes them. Rollback is a revert of presentation/optional API additions; do not migrate or delete user data to roll back IA.

| PR | Responsibility and dependency | Explicit exclusion / authority and risk | Gates and rollback |
| --- | --- | --- | --- |
| PR1 | Equal Training/Nutrition Plan domain cards; depends on PR0 | No Nutrition workflow rewrite; `plan_facts` stays read-only. Web only; visual/semantic risk. | Plan route, summary failure, keyboard/width/TR+EN checks; revert template/CSS. |
| PR2 | Two Nutrition modes, preserve Diary/History/Water workflows as reachable secondary paths; PR1 | No ledger/schema changes; browser history, focus, deep link compatibility risk. | Existing route/API tests, workflow reachability, focus and responsive checks; revert navigation composition. |
| PR3 | Factual Today state and hydration/meal grouping; PR2 and hydration reliability repair | No new thresholds or water writer; partial-read risk. | Empty/error/unknown and canonical totals tests; revert presenter/UI. |
| PR4 | One Log Food chooser with existing methods; PR2/3 | No merged backend authority; staging/duplicate risk. | Search/barcode/menu/manual/quick-add log boundary and retry tests; revert chooser. |
| PR5 | Plan presentation, planned/logged distinction and replacement UX; PR2 | `NutritionPlan` stays writer; destructive replacement and duplicate risk. | Proposal/save/active/failed-save and double-tap characterization; revert UI. |
| PR6 | Web deterministic insight and explicit Coach handoff; PR3/5 | Native handoff deferred until Coach exists; no automatic LLM or message; privacy and false advice risk. | Partial-read/privacy/authorization tests; disable insight/handoff. |
| PR7 | Add protected native API contracts for Plan, water, supplements, history, then remaining logging methods; PR0 | No second tables/writers; API/security compatibility risk. | Auth, schema, negative, ownership and failure tests; keep old endpoints and disable new routes if needed. |
| PR8 | Flutter Plan sibling selector and Nutrition Today/Plan using PR7; PR7 plus stable web semantics | No local business authority; native Coach handoff stays unavailable until separate Coach contract; high conflict in route registry, router, composition, Today and Plan. | Registry, API mapper, auth ON/OFF (null repository remains unavailable), lifecycle, navigation restoration, Flutter widget tests; feature gate/revert native composition. |
| PR9 | Cross-platform parity, responsive/a11y, legacy cleanup only after usage audit; PR1–8 | No removal of useful capability by label alone; migration risk. | Full route/workflow/parity matrix and telemetry; retain aliases and revert cleanup independently. |

PR7 can be split by API domain so native work consumes only reviewed contracts. PR8 should avoid changing shell registry and Today card in unrelated parallel branches. Characterize browser quick-add duplicates before PR4/5; a missing idempotency guarantee is a design risk, not proof that PR1 is blocked.

## Blocker verdict, risks, and deferred scope

**Confirmed production blocker for truthful hydration in PR3 (P1):** `static/nutrition.js` `initWaterButton` first renders `readWaterCache()` (which returns `0` on absent/bad cache), then silently ignores `/water` failures. `saveWaterCount` writes cache and immediately displays success while POST `/water` is fire-and-forget; `logWater` closes the amount modal and announces success without any POST. Thus a failed server read can look like zero cups, and a failed or absent write can look committed. Affected route: GET/POST `/water` via the Nutrition Today/Water UI. User impact: false hydration state and confirmation, particularly across devices. **Smallest safe prerequisite before PR3:** a separate web hydration reliability PR that adds explicit loading/unavailable/pending/confirmed states, awaits and checks writes, makes the modal's action persist its selected amount or removes the false confirmation, and characterizes cache behavior. No water persistence or UI runtime code is changed in PR0. This does not block PR1 or the navigation-only portion of PR2.

| Question | Verdict |
| --- | --- |
| Web sibling IA | **No architectural blocker.** Current hierarchy is presentation, with a working bounded Nutrition summary and canonical route. |
| Web two-mode IA | **No persistence blocker.** Reachability and focus restoration require careful PR2 implementation. |
| Persistence/authority | Existing canonical models remain; do not add a Plan or Coach shadow ledger. Planned-meal duplicate policy needs characterization before one-tap UX. Hydration UI reliability blocks truthful PR3 presentation, not the server authority. |
| API | Browser workflows exist; protected native contracts for Plan, water, supplements, history, menu, builder, and day insight are missing or unverified. |
| Flutter | **Missing capability**, not a blocker to web PR1/PR2. Plan is Training-only and Nutrition is under Today; live Nutrition requires native auth ON, Coach is placeholder, and configured Progress is unavailable. |
| Cross-platform | Semantic contract can be adopted now; full parity waits on PR7/PR8. |

No production issue is fixed inside PR0. Deferred: Grocery Guide, new health/adherence scores, plan-meal exactly-once enforcement, literal shared visual components, route renaming, data migrations, billing, and AI provider changes.

## Characterization and validation record

Read-only evidence: route decorators and models in `app/blueprints/nutrition/*`, `mobile_nutrition.py`, `training.py`, `supplements.py`, `food.py`, `menu.py`, `app/models.py`; web `plan_facts` and templates; Flutter route registry, `PlanScreen`, Today nutrition card, live repository and composition. Existing tests to run are `tests/test_plan_domain_convergence_contract.py`, `tests/test_ux3_pr4_nutrition_placement.py`, `tests/test_mobile_nutrition_api.py`, `tests/test_supplements_routes.py`, and Flutter `test/architecture/nutrition_boundaries_test.dart`, `nutrition_convergence_boundaries_test.dart`, `nutrition_mutation_boundaries_test.dart`, `test/app/navigation/route_registry_test.dart`. Record command results before claiming readiness. PR0 adds no production behavior or schema. The adversarial review must check duplicate write authority, route breakage, false empty states, eager loading, privacy, and stale docs.

Web command: `python -m pytest tests/test_plan_domain_convergence_contract.py tests/test_ux3_pr4_nutrition_placement.py tests/test_mobile_nutrition_api.py tests/test_supplements_routes.py -q` — **85 passed**. Flutter command: `flutter test test/architecture/nutrition_boundaries_test.dart test/architecture/nutrition_convergence_boundaries_test.dart test/architecture/nutrition_mutation_boundaries_test.dart test/app/navigation/route_registry_test.dart` — **70 passed** after dependency resolution with command-scoped Git safe-directory config. These existing assertions exercise routes, APIs, and architecture boundaries rather than strings in this document. No new tests were added because PR0 changes only documentation and the existing semantic characterization suites cover the current facts. The Flutter tool generated plugin registration files during dependency resolution; those files were restored, leaving the mobile audit worktree clean.
