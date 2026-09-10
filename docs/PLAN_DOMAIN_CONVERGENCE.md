# Plan Domain Convergence

Status: **contract locked for implementation**

Discovery date: 2026-09-05

Baseline: `origin/main` at `ddfbe043c8f3b472f1b2ebbc1fa222082c0804cc`

Product authority: [`docs/PRODUCT_IA.md`](PRODUCT_IA.md)

This document turns the locked Product IA into an implementation contract. It
does not change production behavior or prescribe visual styling.

## A. Executive verdict

AxisAI should keep `/training` as the canonical URL reached by the primary
**Plan** destination and evolve that route into a lightweight, server-grounded
Plan overview. The overview owns placement and discovery of Training,
Nutrition, Supplements, and future Recovery; it does not own or copy their
business logic.

The existing `plan.html` is useful input but is not the target shell unchanged.
It is a read/create Training-plan presentation that omits workout execution and
does not expose Nutrition or Supplements. Conversely, `training.html` includes
generation and execution but is not a multi-domain Plan landing. PR2 must form
one structural shell from the safe properties of both while retaining the
existing authorities and stable routes.

No `/plan` route is introduced. No database, API, mutation, flag, mobile, or
production template behavior changes in this discovery.

## B. Baseline

- `git fetch origin --prune` completed before the audit.
- `origin/main`: `ddfbe043c8f3b472f1b2ebbc1fa222082c0804cc`.
- Baseline commit `000e322` is `feat(ux): orchestrate Today guidance from
  canonical state (#282)`; therefore PR #282 is present.
- One later commit is present: `ddfbe04`, `fix(coach): make provider failure
  and plan claims observable (#283)`. Its Coach truthfulness/observability
  changes were reviewed after the branch was fast-forwarded. It strengthens
  the rule that Coach may claim a plan mutation only with server-owned
  execution evidence and does not change Plan routes or domain authorities.
- Discovery branch: `docs/ux3-pr1-plan-domain-convergence-discovery`.
- Isolated worktree:
  `.worktrees/ux3-pr1-plan-domain-convergence-discovery`.
- Repository truth is sufficient to lock the architecture. Runtime production
  flag values remain **UNKNOWN** because deployment environment values are not
  stored in this repository.

## C. Locked Product IA

The primary product destinations remain exactly:

| Destination | Stable route | Responsibility |
|---|---|---|
| Today | `/` | Current-day guidance and ranked next action |
| Plan | `/training` | Forward-looking plan overview and domain placement |
| Coach | `/coach` | Contextual explanation and narrow, authorized change |
| Progress | `/progress-page` | Retrospective outcomes, trends, and history |

Plan owns the product placement of Training, Nutrition, Supplements, and future
Recovery. Ownership here means information architecture and navigation—not a
new data layer. `app/nav.py` already encodes this by resolving `training`,
`nutrition`, and `supplements` to the Plan primary destination.

Account/Profile remains a utility boundary. Community remains outside the four
primary destinations. This discovery does not reopen those decisions.

## D. Current route and surface inventory

### User-facing homes

| Route | Surface | Current behavior | Target classification |
|---|---|---|---|
| `/` | Today | Ranks Resume, Start, or Create from canonical state | Stable canonical home |
| `/training` | `training.html` or `plan.html` | Full-template swap controlled by `UIUX_PLAN_V2_ENABLED` | Stable canonical Plan home |
| `/nutrition` | `nutrition.html` | Nutrition Today, Diary, Plan, History, Water | Stable Plan child home |
| `/supplements` | `manage_stack.html` | Supplement cabinet CRUD | Stable Plan → Nutrition child home |
| `/coach` | Coach | Conversation and contextual plan tools | Stable contextual home |
| `/progress-page` | Progress | Retrospective outcomes | Stable canonical home |
| `/edit-profile` | Account/Profile | Identity, preferences, and links including Supplements | Stable utility home; domain links are compatibility entries |
| `/pump-check-gallery` | Gallery | Retrospective evidence | Stable Progress-owned child |

### Training reads and mutations

| Route | Method | Responsibility |
|---|---|---|
| `/training-plan` | POST | Validate preferences and generate a proposed training plan |
| `/training-plan/save` | POST | Whole-plan replacement and lineage initialization |
| `/training-plan/active` | GET | Read active training plan |
| `/training/bootstrap` | GET | Coherent, `no-store` execution snapshot |
| `/workout/status` | GET | Resolve current workout state |
| `/workout/complete` | POST | Canonical browser completion entry |
| `/workout/session/start` | POST | Start durable browser session when gated on |
| `/workout/session/current` | GET | Read current durable session |
| `/workout/session/<id>/resume` | POST | Resume session |
| `/workout/session/<id>/checkpoint` | POST | Save bounded progress |
| `/workout/session/<id>/abandon` | POST | Abandon session |
| `/api/training/weekly-program` | GET | Read weekly/adaptive projection |

Native equivalents live below `/api/v1/training`: preferences, plan generation,
current plan, workout detail, and gated workout-session lifecycle routes.

### Nutrition reads and mutations

| Route family | Responsibility |
|---|---|
| `/nutrition` | One domain surface with Today, Diary, Plan, History, and Water tabs |
| `/nutrition-plan`, `/nutrition-plan/save`, `/nutrition-plan/active` | Generate, replace, and read nutrition plans |
| `/meal-log`, `/meal-log/today`, `/meal-log/history`, `/meal-log/entry/<token>`, `/meal-log/review` | Canonical consumed-food ledger |
| `/api/diary/*` | CustomMeal draft assembly and explicit commit to MealLog |
| `/api/quick-add-meal` | Direct canonical meal logging |
| `/api/food/search`, `/api/food/barcode`, `/api/food/<id>/servings`, `/api/food/servings-by-name` | Provider-backed food and serving lookup |
| `/api/proxy/scan-menu`, `/api/menu/analyze` | Menu-image analysis, not a separate ledger |
| `/water` | Water read/update used by Nutrition |

Mobile nutrition contracts provide diary, search, serving, barcode, log,
correction, and deletion operations under `/api/v1`; they retain the same
server-owned ledger rules.

### Supplements

`/supplements` renders the cabinet. `/supplement/add`,
`/supplement/edit/<id>`, and `/supplement/delete/<id>` mutate the persisted
`Supplement` records and associated XP/quest/activity effects. The Profile link
is an entry point, not ownership evidence.

## E. Training authority map

| Concern | Current authority | Write path / rule | Convergence rule |
|---|---|---|---|
| Preferences | Latest `UserSession` plus validated request preferences | Generation endpoints validate before providers | Plan may summarize; never revalidate in UI |
| Plan generation | Training planning/generation pipeline | `POST /training-plan`; native `POST /api/v1/training/plans` | Keep one server pipeline |
| Active-plan persistence | `TrainingPlan` records | `/training-plan/save` performs whole replacement | Do not add shell persistence |
| Lineage/revision/mutation | `app/services/plan_mutation` and journal/version fields | Narrow service operations | All Coach mutations remain here |
| Current workout selection | Active plan plus canonical day/state facts | Server selectors/fact gathering | Do not infer from browser clock |
| Workout-state resolution | `app/services/workout_state` | `/workout/status`, `/training/bootstrap`, native reads | One resolver across entries |
| Execution/session lifecycle | `WorkoutSession` service/routes behind `FITX_WORKOUT_SESSIONS_ENABLED` | start/resume/checkpoint/abandon/complete | Execution is a transient child workflow |
| Completion | `app/services/workout_completion` | Browser, native, AI/photo adapters converge here | No completion logic in Plan shell |
| Weekly/adaptive planning | Weekly-program and training progression/planning services | Read projection; mutation only through authorized service paths | Overview may lazy-load a summary |
| Coach plan mutation | `app/services/plan_mutation` invoked by six narrow tools | Gated by Coach/AI mutation flags | Refresh Plan facts after mutation |

The shell may compose bounded read models. It must never become a second
training-plan, workout-state, completion, or mutation authority.

## F. Nutrition authority map

| Concern | Current authority | Convergence rule |
|---|---|---|
| Daily targets | Current `UserSession` nutrition targets | Server values; null remains unknown, never zero-filled |
| Diary ledger | Canonical `MealLog` | Plan summary reads totals only |
| Meal logging | MealLog service/blueprint paths | All scanners/builders end in this ledger |
| Correction/edit/delete | Revision-aware server mutations; current-day constraints | Preserve opaque IDs and revision semantics |
| Food search | Food provider abstraction | No shell-owned search cache or serving math |
| Barcode | Food provider plus canonical log adapter | Barcode discovery is not a second ledger |
| Menu scan | Menu analysis endpoints | Suggestions require explicit canonical logging |
| Nutrition planning | Nutrition plan generation and `NutritionPlan` persistence | Remains within `/nutrition` Plan tab |
| History | MealLog history projection | Remains retrospective view within Nutrition |
| Water | Server `/water` state | Nutrition-owned capability despite current route placement in training blueprint |
| Supplements | `Supplement` persistence and supplement blueprint | Product placement is Plan → Nutrition → Supplements |
| Serving truth | Provider-returned portions/units | Client must preserve provider truth and bounded choices |

Nutrition should remain one stable route with domain-level sub-navigation. Its
Today tab answers nutrition status, not the global Today job. Diary is consumed
food, Plan is intended food, History is prior ledger state, and Water is a
nutrition measure. These are coherent siblings and need no new top-level route.

## G. Supplements authority map

The `Supplement` model and `app/blueprints/supplements.py` own cabinet reads and
CRUD. Mutations also trigger existing XP, quest, and activity effects; moving
those writes into a Plan presenter would create duplicate authority.

Canonical home: **Plan → Nutrition → Supplements**, stable URL
`/supplements`. Compatibility/contextual entries may remain in Account/Profile
and Coach where they help the user, but those entries must label or link to the
same cabinet. A Profile preview must not grow into a second editable cabinet.

## H. Plan flag analysis

`UIUX_PLAN_V2_ENABLED` is still a meaningful behavioral flag, not historical.

| Question | Finding |
|---|---|
| Default | `False` in application configuration and `.env.example` |
| Lifecycle record | `shipped_dark`; decision is enable; review-by 2026-10-01 |
| Selector | A single server-side branch in GET `/training`; no client/query override |
| OFF | Renders `training.html`, loads `training.js`, supports generation and workout entry/execution |
| ON | Renders the bounded `plan.html` shell from `plan_facts` + presenter; canonical Create/Start/Resume are functional without loading `training.js` |
| Reachability | Exactly one branch is user-reachable per process, but both are live tested paths |
| Staleness | Neither is dead. Both are incomplete representations of the locked Plan concept; ON is materially behind current execution access |
| Rollback | Set `UIUX_PLAN_V2_ENABLED=0` and restart; documented in flag registry/rollout docs |
| Observability | Boot/health flag exposure and weekly telemetry are partial; no repository evidence of a dedicated Plan-render metric |
| Tests | `tests/test_plan_v2.py`, feature-flag/env tests, and Coach-entry convergence tests cover selection and key states |
| Production value | **UNKNOWN** in source; host environment is authoritative |

Do not retire or change the flag in PR1. PR2 should continue to use it for an
atomic full-template rollout. After the converged shell proves stable, a later
cleanup PR may make the converged path unconditional and classify the flag as
historical. `UIUX_NAV_V2_ENABLED` is already historical because hooks always emit
the converged four-destination shell. `WEEKLY_PROGRAM_UI_ENABLED` is an additive
section gate. Workout-session and Coach mutation flags gate separate staging
capabilities and must not be coupled to shell rollout.

## I. `training.html` versus `plan.html`

| Dimension | `training.html` (flag OFF) | `plan.html` (flag ON) |
|---|---|---|
| Product concept | Training creation plus execution workspace | Plan shell: Training first, then Nutrition and nested Supplements |
| Data | Client bootstrap, status/session, generation responses | Bounded `plan_facts`, canonical workout snapshot, and child summaries |
| JavaScript | `training.js` plus shared execution/draft/state modules | No `training.js`; creation-only or actionable shared execution assets |
| Active plan | Renders plan and exposes workout flow | Native `<details>` summary plus canonical Start/Resume workflow |
| No plan | Full generator | Bounded creation flow |
| Regeneration | Supported by training client | Not presented as active-plan action |
| Workout entry | Present | Present through the PR #285 durable browser contract |
| Weekly program | Conditional mount/API | Conditional presenter section |
| Coach entry | Contextual entry exists | Same contextual entry exists |
| Error/partial state | Client-specific errors/loading | Explicit `read_error`, `no_active_plan`, `active_plan`, `partial` states |
| Responsive behavior | Existing responsive training UI | Responsive bounded multi-domain Plan shell |

They present overlapping Training authority, but they embody materially
different workflows. Treating either template as the final Plan product would
lock in a missing half: server-state clarity without execution, or execution
without Plan-domain hierarchy. The target is one presentation contract over
the same authorities, not two permanent concepts.

## J. User workflow map

1. **No plan:** Today Create → `/training`; Plan overview shows Training as
   missing and exposes the bounded creation action. Nutrition and Supplements
   remain independently reachable and must not be blocked by absent Training.
2. **Existing plan:** `/training` shows a compact cross-domain overview;
   Training reveals the active program and entry into the scheduled workout,
   while `/nutrition` and `/supplements` remain stable specialized homes.
3. **Regeneration:** begins from Training placement within Plan, uses the same
   generation endpoint, makes destructive replacement explicit, then refreshes
   canonical facts. Coach may propose narrow mutations but is not the generic
   regeneration home.
4. **Workout execution:** Today Resume/Start and Plan Training entries resolve
   canonical state, then enter the same transient workout workflow. They never
   infer completion locally.
5. **Nutrition logging:** Plan → Nutrition → Today/Diary; search, barcode,
   menu scan, quick-add, or builder workflows commit only through MealLog.
6. **Today to Plan:** Create targets the Training creation placement; Start and
   Resume must target the execution-capable Training placement/workflow. The
   current generic `/training` link is compatible only when the converged shell
   exposes those state-appropriate actions.

## K. State matrix

| State | Plan landing | Training placement | Nutrition placement | Primary action |
|---|---|---|---|---|
| Read failure | Bounded section error; other domains remain usable | Retry Training read | Independent Nutrition state | Retry affected section |
| No Training plan | Overview still renders | Creation state | Normal independent state | Create Training plan |
| Active plan, no active session | Summary | Scheduled workout/program | Normal independent state | Start canonical workout |
| Active resumable session | Summary prioritizes resume | Resume state from workout authority | Normal independent state | Resume |
| Completed today | Completion state | Next/rest guidance from resolver | Normal independent state | Review or next canonical action |
| Partial weekly data | Overview survives | Mark weekly section partial | Unaffected | Retry weekly section |
| No Nutrition plan | Overview marks Nutrition plan absent without blocking diary | Unaffected | Diary remains usable; Plan tab offers creation | Create Nutrition plan contextually |
| Nutrition read failure | Training remains usable | Unaffected | Section error and retry | Retry Nutrition |
| No supplements | Overview may show empty cabinet summary | Unaffected | Supplements entry remains available | Add supplement |
| Coach mutation just completed | Refresh/invalidate bounded plan facts | Show new mutation version; flag stale session if applicable | Unaffected unless future authorized nutrition tool exists | Review changed plan |
| Feature flag OFF | Legacy Training surface until PR2 rollout | Existing full workflow | Existing route | Existing behavior |
| Feature flag ON after PR2 | Converged Plan shell | Execution-capable placement | Stable specialized link/summary | State-appropriate action |

Loading and errors are isolated per domain. The overview must not turn three
independent reads into one all-or-nothing page.

## L. Current duplication and ownership defects

- **P1:** Today Start/Resume/Create all link to `/training`, while the current
  flag-ON template lacks workout execution. Enabling the flag can break the
  meaning of the highest-priority guidance even though the URL resolves.
- **P1:** Two templates behind one route expose different core capabilities;
  continued dual evolution will deepen behavioral drift.
- **P2:** Neither template represents Plan's multi-domain ownership. The label
  says Plan while the landing remains Training-only.
- **P2:** Training plan creation appears in two presentations with different
  active/no-plan behavior; the backend is shared but user expectations differ.
- **P2:** Supplements is Plan-owned in navigation but primarily surfaced as a
  standalone/Profile-linked cabinet, obscuring its Nutrition relationship.
- **P2:** A combined overview could duplicate server authorities if it fetches
  full child pages or reconstructs status in JavaScript.
- **P3:** `/water` resides in the training blueprint although it is a Nutrition
  concern. This is code-placement debt, not permission to change the route now.
- **P3:** Some durable narrative docs still describe the nav flag as rollout
  selectable even though runtime hooks always use the four-destination shell.

There is no evidence of duplicate database ownership for Training, Nutrition,
or Supplements today. The defect is primarily duplicate presentation and
unclear placement; convergence must avoid creating data duplication while
fixing it.

## M. Plan architecture candidates

**A — evolve `/training` into the Plan overview.** The stable route becomes a
lightweight overview exposing Training, Nutrition, and Supplements. Specialized
routes remain deep links; execution is available from Training placement.

**B — promote existing Plan V2 unchanged.** Keep `/training`, declare
`plan.html` the shell, then append other domains later. This preserves a clean
server read model but initially retains its execution gap and Training-only
concept.

**C — separate lightweight Plan landing and stable specialized children.** Add
a distinct landing (practically `/plan`) while preserving `/training` and
`/nutrition` as children. This is conceptually clean but violates the default
route-stability posture and adds redirect, analytics, mobile, and dual-home
work.

**D — make `/nutrition` or `/` the Plan landing.** This reuses an existing route
but conflicts with both locked ownership and user mental models; it is included
as the strongest repository-grounded alternative to inventing another shell.

## N. Candidate scoring

Scores are 1 (poor) to 5 (strong). All criteria are equally weighted because
the locked IA provides no basis for hidden weighting.

| Criterion | A | B | C | D |
|---|---:|---:|---:|---:|
| Product IA alignment | 5 | 3 | 4 | 1 |
| User mental model | 5 | 3 | 5 | 1 |
| Route stability | 5 | 5 | 2 | 2 |
| Implementation risk | 4 | 3 | 2 | 1 |
| Duplicate ownership reduction | 5 | 3 | 3 | 1 |
| Backend authority preservation | 5 | 5 | 5 | 3 |
| Mobile conceptual parity | 5 | 3 | 3 | 1 |
| Deep-link compatibility | 5 | 5 | 3 | 2 |
| Testability | 5 | 5 | 4 | 3 |
| Rollback | 5 | 5 | 3 | 2 |
| Migration complexity | 4 | 3 | 2 | 1 |
| Minimal temporary dual architecture | 4 | 2 | 1 | 1 |
| **Total / 60** | **57** | **45** | **37** | **19** |

Candidate A wins because it distinguishes stable URL from page content, uses
the existing atomic flag boundary, and closes the Today/execution mismatch
without route migration.

## O. Recommended architecture

Implement Candidate A as a **bounded overview with stable specialized
destinations**:

- GET `/training` remains the Plan primary route.
- The converged template receives a small server-composed Plan read model with
  independently representable Training, Nutrition, and Supplements summaries.
- Training is the first/default placement because `/training` is also its
  historical deep link. It includes the state-appropriate Create, Start, Resume,
  or Review action from canonical workout state.
- Nutrition summary links to `/nutrition`; Supplements summary links to
  `/supplements`. Their full data and mutation UIs are not embedded.
- Workout execution is entered from Plan or Today but remains a transient
  Training child workflow using existing bootstrap/session/completion services.
- `UIUX_PLAN_V2_ENABLED` remains the atomic rollout selector during convergence.
  The OFF branch is the rollback until parity is demonstrated.
- The existing `plan_facts`/presenter boundary should be extended or composed,
  not bypassed with client-side inference and not made a mutation service.

## P. Target domain contract

A. **Purpose:** Plan answers “what am I following and what can I adjust next?”
across forward-looking health domains.

B. **Canonical primary route:** `/training`, labeled Plan.

C. **Training:** first/default Plan placement; owns program creation,
regeneration, scheduled workout entry, and program inspection through existing
services.

D. **Nutrition:** Plan child at `/nutrition`; its Today, Diary, Plan, History,
and Water sub-navigation remains intact.

E. **Supplements:** Nutrition-related Plan child at `/supplements`; Profile and
Coach links are contextual only.

F. **Creation/regeneration:** Training placement within Plan. Creation is shown
when absent; regeneration is explicit when active and continues through the
canonical generation/save pipeline.

G. **Workout execution:** transient Training child workflow, entered from
Today or Plan and governed by workout-state/session/completion authorities.

H. **Workout history:** Progress owns retrospective outcomes. Plan may show
current program context but not duplicate historical analysis.

I. **Coach:** explains and performs only narrow, gated mutations through
`plan_mutation`; it is not a second plan editor or landing.

J. **Progress:** owns outcomes, trends, history, and gallery evidence; it does
not own future scheduling.

K. **Today deep links:** Create → Plan/Training creation; Start → scheduled
workout entry; Resume → active session. `/training` remains compatible, but the
landing must resolve the right action before flag rollout completes.

L. **Account/Profile:** owns identity and durable account preferences. It may
link to Plan capabilities but must not duplicate editable Training, Nutrition,
or Supplement surfaces.

M. **Stable routes:** `/`, `/training`, `/nutrition`, `/supplements`, `/coach`,
`/progress-page`, `/edit-profile`, existing mutation APIs, and native `/api/v1`
contracts remain unchanged.

N. **Flag convergence:** use `UIUX_PLAN_V2_ENABLED` for atomic full-template
rollout through parity; retire only in a later cleanup after evidence. Do not
couple it to weekly, session, or Coach mutation flags.

O. **Mobile parity:** mobile keeps Today/Plan/Coach/Progress and a Plan concept
that places Training, Nutrition, and Supplements. Platform UI may differ, but
authority, state meanings, and canonical capabilities may not.

## Q. Stable-route contract

No `/plan` route, redirect, or alias is required. These externally meaningful
paths remain stable:

- primary: `/`, `/training`, `/coach`, `/progress-page`;
- Plan children: `/nutrition`, `/supplements`;
- account/progress context: `/edit-profile`, `/pump-check-gallery`;
- all current Training, Nutrition, Supplement, Coach mutation, and native API
  endpoints listed above.

Changing a navigation label does not require changing a URL. Downstream PRs
must add an explicit migration decision, redirect tests, analytics analysis,
and mobile assessment before altering any path in this contract.

## R. Mobile parity

The sibling Flutter repository currently encodes the four primary destinations
and routes Plan to a training-focused `PlanScreen`, with workout detail/session
children. That is conceptually aligned at the top level but incomplete:

- Plan is Training-only and does not place Nutrition or Supplements.
- The inspected development composition uses fixture repositories for weekly
  plan data.
- Today reports creation/recovery actions as unavailable where no live
  repository is connected.
- Server native Training and Nutrition contracts exist, but the inspected app
  does not wire a complete Nutrition Plan experience into the shell.

The future boundary is shared semantics, not pixel parity: same four primary
destinations; same Plan-owned domains; same null/error/session meanings; same
server mutation authorities. Native may ship domain placements incrementally.
The sibling repository is read-only and is not changed by this PR.

## S. Performance and load analysis

Current legacy Training commonly loads `/training/bootstrap` plus current/last
session data and then feature-specific calls. Nutrition's initial surface issues
at least the today-ledger, active-plan, and water reads before tab-specific food,
history, diary, barcode, or menu work. Supplements is server-rendered and small.

The converged landing must not boot full Training and Nutrition applications at
once. Contract:

- server-render a bounded shell and independently useful summaries;
- reuse a coherent Training facts/read-model boundary;
- do not call provider-backed food search, barcode, menu analysis, or full
  history on landing;
- lazy-load detailed weekly, diary, and child-domain data only after intent;
- isolate timeouts/errors so one domain cannot blank the shell;
- keep generation and Coach calls user initiated;
- preserve `no-store` on volatile workout/bootstrap reads;
- instrument render result, per-section latency/failure, CTA selection, and
  child-navigation events before rollout.

A practical PR2 budget is one initial HTML response with bounded database reads
and zero provider/AI calls. Exact numeric latency budgets belong to performance
measurement during implementation, not this repository-only discovery.

## T. Risk register

| Severity | Risk and evidence | Impact | Mitigation owner / closure |
|---|---|---|---|
| P0 | None found in the documentation-only change | — | Any production diff would make PR1 not ready |
| P1 | Flag-ON `/training` lacks Start/Resume while Today links there | Broken primary next action | PR2: parity tests and canonical CTA resolver |
| P1 | Permanent two-template drift behind one route | Inconsistent behavior by environment | PR2 then final cleanup PR |
| P1 | New shell accidentally writes/recomputes plan state | Duplicate authority/data corruption | PR2: read-model-only boundary and mutation tests |
| P1 | Regeneration bypasses lineage or targets stale plan | Lost history/wrong plan | PR3: reuse generation/save and `plan_mutation` contracts |
| P1 | Coach mutation leaves shell/session stale | User acts on superseded plan | PR3: refresh versioned facts and stale-session semantics |
| P1 | Nutrition embedding changes MealLog semantics | Incorrect totals or lost corrections | PR4: link/summary first; existing nutrition suites gate |
| P1 | Route/deep-link changes | Today, bookmarks, native links break | All PRs: stable-route contract tests |
| P2 | Mobile remains Training-only | Cross-platform mental-model divergence | PR5 mobile contract/alignment work, separately scoped |
| P2 | Eager child loading multiplies queries and providers | Slow/fragile landing | PR2 bounded reads; PR6 instrumentation/hardening |
| P2 | Supplements remains duplicated in Profile sitemap | Confused ownership | PR5 placement cleanup; retain contextual link only |
| P2 | Runtime flag state is unknown in repository | Rollout assumptions could be false | Release owner verifies host env and `/health?deep=1` |
| P3 | Water code lives in Training blueprint | Misleading code placement | Defer service extraction until behavior-safe cleanup |

## U. Downstream PR decomposition

### PR2 — Plan shell and state-action parity

- **Goal:** behind `UIUX_PLAN_V2_ENABLED`, make `/training` the bounded Plan
  overview with Training/Nutrition/Supplements placement and canonical
  Create/Start/Resume behavior.
- **Likely systems:** training route/template, Plan facts/presenter, focused CSS
  and JS, Today/Plan contract tests, observability.
- **Non-goals:** no route/API/schema changes, no Nutrition or Supplement CRUD
  rewrite, no visual-system overhaul, no mobile implementation.
- **Prerequisite:** this discovery.
- **Acceptance:** atomic flag swap; OFF unchanged; ON preserves Today actions;
  no provider/AI boot calls; stable routes and backend authorities pass.
- **Rollback:** flag OFF and restart.
- **Major risk:** breaking active workout entry.

#### PR2 implementation record (2026-09-06)

PR #285 (`6422028` on main) supplied the prerequisite durable browser execution
contract. PR2 extracts only its presentation-independent flow helpers into
`workout_execution.js`; both legacy Training and Plan consume the same
`workout_execution`, `workout_draft`, and `workout_state_client` boundaries.
Plan does not load `training.js`, does not derive the day or workout state in the
browser, and hydrates the server-rendered canonical bootstrap without an initial
duplicate request.

The server presenter validates the exact canonical state/action pair through
`decide_today_guidance`: no plan keeps the canonical in-page Create flow,
scheduled exposes Start, resumable exposes Resume, and completed/rest/evidence/
attention/read-error/unknown/incompatible states expose no workout action. A
successful mutation refreshes `/training/bootstrap`; Close retains the draft,
while Abandon remains an explicit session mutation. Completion still waits for
the final checkpoint acknowledgement and sends `expected_checkpoint_revision`.

The shell adds two cheap independent child reads (latest nutrition target and
supplement count). Together with the supplied-plan workout resolver, an active
render uses five bounded SELECTs with sessions OFF and six with sessions ON:
active plan, two workout evidence reads, two child reads, and the optional
session read. It makes zero provider/LLM/food/barcode/menu/history calls. An
actionable page loads four focused execution scripts; non-actionable pages load
none of them. Start/Resume mutations retain the existing POST plus canonical
bootstrap-refresh request pattern.

Child reads fail independently to `unavailable`; null nutrition target is
`empty` rather than fabricated zero, and no supplements is `empty` rather than
error. A Training read failure removes unsafe Start/Resume while leaving the
shell and child links reachable. Dedicated Plan telemetry is deferred because
the repository has no suitable low-cardinality product-event facility; existing
request logging and flag exposure remain in place. Instrumentation hardening is
still PR6 scope. Both rollout flags remain default-OFF and independent.

### PR3 — Training placement and regeneration convergence

- **Goal:** remove remaining Training presentation duplication and make
  creation/regeneration/session refresh coherent within the Plan shell.
- **Likely systems:** Training client modules, generation UI, workout session
  entry, `plan_facts`, `plan_mutation` refresh handling.
- **Non-goals:** no new planning algorithm or completion authority.
- **Prerequisite:** PR2; coordinate with Sprint 14 session work and Coach plan
  mutation contracts.
- **Acceptance:** no-plan, active, partial, regeneration, Coach mutation, and
  stale-session cases use canonical services.
- **Rollback:** revert PR3 while leaving PR2 shell summaries enabled, or disable
  Plan V2 if entry parity is affected.
- **Major risk:** mutation/version disagreement.

#### PR3 implementation record (2026-09-07)

Baseline `origin/main` `b77a1dc` (PR #289, workout-execution reliability), one
commit after the PR2 merge `720d590` (PR #288).

**Canonical Training-management placement.** Plan owns ONE Training-management
workflow with two deliberately different operations, named by a stable
presenter identifier (`app/plan_presenter.py`):

| `management_state` | Page state | Operation | Confirmation |
|---|---|---|---|
| `create` | `no_active_plan` | additive | none required — nothing is destroyed |
| `regenerate` | `active_plan`, `partial_active_plan` | destructive whole-plan replacement | explicit modal confirmation |
| `unavailable` | `read_error` | none offered | — |

A `partial_active_plan` resolves to `regenerate`, never `create`: a plan whose
data cannot be displayed is still a plan the user follows, and offering
"create" would let it be overwritten without the confirmation an active plan is
entitled to. `read_error` ships no management client at all — a page that
cannot say what the plan is must not be able to replace it.

**Shared client boundary.** `static/training_plan_management.js`
(`window.FitXPlanManagement`) is the one browser Training-management contract,
extracted the way `workout_execution.js` was in PR2. It owns the canonical
request sequence (`POST /training-plan` → `POST /training-plan/save`, with the
server-signed `exercise_context_token` forwarded verbatim), the bounded state
machine (`idle` / `generating` / `proposal_ready` / `saving` / `refreshing` /
`replaced` / `error` / `stale_plan`), the freshness precheck, and the rule that
a successful write is not reported as done until a canonical refresh has run. It
renders nothing. Two renderers consume it: `static/plan_training_manage.js`
(Plan; replaces the retired creation-only `plan_create.js`) and
`static/training.js` (legacy, which keeps its own `#results` presentation and
injects `workoutStateClient.mutate` as the persist transport so PR2/#285
save-ordering is unchanged).

**Creation workflow.** Plan renders the preference form → user submits →
`POST /training-plan` returns a proposal → the proposal is rendered in its own
region, labelled not-yet-saved → user confirms → freshness precheck →
`POST /training-plan/save` → canonical refresh (a reload, so the SERVER renders
the now-active plan). The proposal is never written to, never rendered into the
active-plan region, and is discarded by Cancel.

**Regeneration workflow.** Identical, plus two boundaries: the panel is closed
on arrival and must be opened deliberately, and the confirm button opens a
destructive dialog whose Cancel/Escape path cannot reach the write. Producing a
proposal is not confirmation; navigating away is not confirmation.

**Mutation/version boundary.** The canonical plan-freshness identity is
`TrainingPlan.lineage_id` + `mutation_version` — `lineage_id` changes on
replacement (the save path deletes and re-inserts), `mutation_version` counts up
on every targeted mutation including a Coach one. PR #293 is the
browser-projection authority: `_active_plan_payload` publishes the pair as
`lineage_id` / `mutation_version` on `GET /training/bootstrap` and
`GET /training-plan/active`. PR3 consumes that pair; it does not mint a second
identity. The Plan presenter still hands the same server reading to the
management bootstrap as `plan_lineage` / `mutation_version` (a different
envelope). No new query is added — both fields are read off the row the shell
already loaded.

**Stale-plan protection (PR #293 is the replacement-concurrency authority).**
`POST /training-plan/save` requires `expected_plan`. `null` asserts "I expect no
active plan". `{lineage_id, mutation_version}` names the exact active plan the
caller intends to replace. Comparison happens under the same lock that protects
replacement. Mismatch is typed 409 `TRAINING_PLAN_SAVE_PLAN_CHANGED`; a
malformed/missing expectation is 400. Both perform no delete and no insert.

PR3 does not own that server contract. It consumes it:

- CREATE sends `expected_plan: null` only from a known no-plan origin.
- REGENERATE captures `proposal_origin` from the canonical identity at generate
  time and sends that frozen pair as `expected_plan`. A later fresh read may
  detect staleness early; it must never upgrade the proposal's replacement
  authority.
- UNKNOWN (missing/partial/invalid origin) does not POST. UNKNOWN is never
  coerced to `null`.
- A 409 refreshes canonical state, leaves the proposal non-active, does not
  auto-retry, and does not rewrite `expected_plan`. The user must regenerate
  intentionally against current state.

The optional client precheck remains: immediately before POST the client
re-reads `/training/bootstrap` and compares it to `proposal_origin`, refusing
(`plan_changed`) without a write when they differ. The server comparison is
authority for the window between that re-read and the write.

**Coach mutation coherence.** After a Coach mutation the Plan page would
otherwise keep showing the program it was server-rendered with: PR2's refresh
only exists on pages that offer Start/Resume, and even there it refreshes
execution state, not the rendered program. PR3 adds a throttled (5 s, matching
`workout_state_client`'s focus interval) canonical re-read on `focus` /
`visibilitychange` in the management renderer. When the identity has moved it
reveals a bounded notice saying the page is out of date and offering a reload —
the server then re-renders. The page never learns, guesses, or renders what the
new plan is; there is no polling, no cross-page storage signalling, and nothing
reads Coach prose. A failed re-read claims nothing.

**Active session + plan replacement.** The discovered canonical contract is
preserved exactly, not extended: the server does NOT block a replacement during
an active session. The session's stored `plan_fingerprint` stops matching, so
`classify` returns `plan_regenerated_or_replaced` with `resumable=False`, the
resolver emits `session_state=active_blocked` / `action=blocked` /
`primary_state=needs_attention`, and checkpoint/completion refuse with
`SessionStale`. PR3 therefore (a) warns truthfully in the destructive
confirmation when an ACTIVE session exists, before the user replaces, and (b)
renders the server's own `stale_reason` as copy afterwards. Stale is never
converted into rest, completed, or Resume, and no fifth behaviour is invented.
Recovery still runs through the existing canonical session routes; a dedicated
Plan-side Abandon affordance for an `active_blocked` session is **not** added
(see open items).

**Draft preservation (#285/#288).** Unchanged. After a replacement the canonical
refresh makes `activeSessionFrom()` return null, `acceptCanonical` clears
checkpoint state and signals `replaceDraft`, and `plan_workout.js` discards the
draft and closes the session view. PR3 adds regression coverage around plan
replacement rather than new behaviour.

**Failure semantics.** Generation failure → current plan untouched, no proposal.
Malformed generator output → treated as a failed generation, never a saveable
proposal. Save failure → current plan untouched, proposal retained AS a proposal.
Refresh failure after a successful write → reported as unconfirmed
(`refresh_failed`) with a reload offer, never as "synchronized". Freshness
unknown → refuse. Unknown is never success.

**Flags.** `UIUX_PLAN_V2_ENABLED` and `FITX_WORKOUT_SESSIONS_ENABLED` keep their
defaults, lifecycles and rollback. Regeneration requires neither the session flag
nor `WEEKLY_PROGRAM_UI_ENABLED`. The flag-OFF legacy path keeps every capability
it had (no-plan generation, active-plan display, regeneration via `resetPlan`,
workout entry, shared execution, errors) and now shares the management contract
instead of duplicating it.

**Cost.** The Plan landing gains no query (the identity is read off the row it
already loaded), no provider/LLM call, and no polling. It loads two focused
scripts where management is offered and none on `read_error`. The only added
requests are user-initiated: one canonical `/training/bootstrap` read before each
destructive write, plus one throttled read per return-to-page.

**Prerequisite landed.** PR #293 (`fix(training): guard plan replacement by
expected version`) is the replacement-concurrency authority. PR3 binds each
regeneration proposal to the canonical origin identity from which that proposal
was created and sends that origin as `expected_plan`. Remaining PR4/PR5/PR6
work is Nutrition convergence, remaining Plan-domain cleanup, and hardening —
not a second TrainingPlan writer and not a second version authority.

**Two defects fixed here.** The destructive confirmation must live outside
`<main>`: `.main-content` carries a filled `page-enter` animation whose retained
transform makes it a containing block for `position: fixed`, so a nested dialog
renders clipped to the 720 px column instead of overlaying the viewport —
`#session-view` and `#plan-completion` already sit outside for this reason. Its
absence now fails closed rather than falling through to the write. Separately,
`plan_workout.js` withdraws Start/Resume with `button.hidden = true`, but
`.btn-volt` declares `display: inline-flex` unconditionally and an author rule of
equal specificity beats the UA `[hidden]` rule, so a withdrawn workout action
stayed visible and clickable; `.plan-workout-action[hidden] { display: none; }`
corrects it. Both were caught by a failing test before the fix.

**Open item (P2).** An `active_blocked` session has no recovery affordance on the
Plan surface (the Abandon control lives in the session dialog, which only renders
when Start/Resume is actionable). This predates PR3 — a previous-day session or a
Coach mutation reaches the same state — and is dark in production while
`FITX_WORKOUT_SESSIONS_ENABLED` is OFF. Adding a Plan-side canonical Abandon
belongs with PR6 hardening.

**Open item (P2).** `#sv-abandon` in the shared session dialog has the same
`hidden`-versus-`.btn-ghost` defect that was corrected for
`.plan-workout-action`. It is PR2 execution-dialog debt that the regeneration
workflow does not reach, so it is reported rather than absorbed here.

### PR4 — Nutrition placement convergence

- **Goal:** make Nutrition's relationship to Plan explicit while preserving
  `/nutrition` and its five internal areas.
- **Likely systems:** Plan summary presenter/template, Nutrition navigation and
  focused tests.
- **Non-goals:** no ledger, target, provider, serving, barcode, menu, or plan
  persistence rewrite.
- **Prerequisite:** PR2; can proceed in parallel with PR3 after the shell read
  contract is stable.
- **Acceptance:** diary works without a Nutrition plan; all mutations still end
  in canonical authorities; Plan navigation remains active.
- **Rollback:** remove summary/placement links; `/nutrition` remains canonical.
- **Major risk:** eager loading or ledger-semantic regression.

#### PR4 implementation record (2026-09-10)

PR4 keeps `/nutrition` as the only Nutrition workspace and makes its ownership
visible in both directions. Plan's existing Nutrition placement now presents a
bounded read-only snapshot: the latest positive `UserSession.target_calories`,
the current Istanbul day's canonical `MealLog` count/calorie aggregate, and
whether the canonical newest `NutritionPlan` exists. Zero MealLog rows is the
known value zero; a missing target remains unknown and is never rendered as
zero. Plan adds no Nutrition write, provider/serving/barcode/menu/history/water
read, browser fetch, prompt, or model call.

The three facts use separate nested transactions, as does Supplements. A failed
target, ledger, or plan-presence statement therefore degrades only that
dimension; the placement reports `partial` when a subset is unavailable and
never blanks Training. The presenter only copies frozen facts. The resulting
facts read costs exactly seven SELECTs with workout sessions OFF and nine with
sessions ON: the pre-PR4 five/seven plus one bounded MealLog aggregate and one
NutritionPlan presence read. It is independent of MealLog history length.

`/nutrition` adds compact linked `Plan / Nutrition` context, while its global
active destination remains Plan through the existing `app/nav.py` ownership
map. Its five local areas remain Today, Diary, Nutrition Plan, History, and
Water; only the ambiguous local “AI Plan” label changed. Tab/panel relationships
are explicit, the quick-add empty state targets the actual Nutrition Plan tab,
and existing write routes and clients are unchanged.

Plan projects kcal for display with the browser's rule, not Python's.
`static/nutrition.js` renders every Today/Diary/History calorie through
`Math.round()` (`floor(x + 0.5)`), while Python's `round()` is half-to-even, so
the same stored 2200.5 would print 2201 in Nutrition and 2200 in Plan.
`_display_kcal` in `app/services/plan_facts.py` reproduces the JS contract at
display time only; stored `MealLog` and `UserSession` values are never
rewritten, nothing rounds at persistence time, and Python's global rounding is
untouched. Absent, non-numeric, NaN and infinite inputs stay unknown.

The read isolation is proved on real PostgreSQL, because SQLite does not abort a
transaction on a failed statement and so cannot prove anything here.
`tests/test_ux3_pr4_nutrition_placement_pg.py` injects a genuine failing
statement inside the target subread and asserts the placement degrades to
`partial` while intake, plan presence and Supplements stay `available`; it
refuses any dialect but `postgresql`. Because the CI `mobile-pg-concurrency` job
selects PostgreSQL modules by an explicit file list, the module is registered in
that list. Removing the savepoint collapses `partial` to `unavailable`, so the
isolation boundary is load-bearing.

Focused verification covers the state/failure matrix, query budget, EN/TR
catalogs, stable hierarchy, the rounding contract, and real Chromium journeys at
320/390/768/1024/1366 with no horizontal overflow — including a
no-`NutritionPlan` 320px journey that logs a meal through the canonical
`POST /meal-log` web write, finds it in Today and History, keeps the Diary
builder operable, creates a plan, and writes water. Results (2026-09-10, on
`origin/main` a962599): focused PR4 module 23 passed; PR4 + Plan V2 +
Plan-convergence + PR3 contracts 107 passed; browser suite 2 passed; PostgreSQL
proof 1 passed on PostgreSQL 16.15; canonical Nutrition/provider/write
regression 582 passed; Plan/Training/PR3 regression 323 passed; whole suite in 8
batches 6610 passed / 29 skipped (every skip an opt-in PostgreSQL test or the
`test_staging_separation` bash guard), with
`tests/test_production_deploy_script.py` excluded as a Windows-host limitation
that reproduces identically on unmodified `origin/main`.

Rollback is a code revert; `UIUX_PLAN_V2_ENABLED` remains default OFF and no
schema, migration, route, or rollout value changed. PR5 can now converge the
deeper Supplements placement without reopening Nutrition authority.

### PR5 — Supplements placement and cross-platform contract

- **Goal:** establish Plan → Nutrition → Supplements placement, reduce Profile
  sitemap ambiguity, and publish equivalent mobile navigation semantics.
- **Likely systems:** Plan/Nutrition links, supplement presentation, Profile
  contextual link, mobile contract/tests (in its own repository/change).
- **Non-goals:** no Supplement model/effect rewrite; no Account convergence.
- **Prerequisite:** PR4; mobile implementation is separately reviewable.
- **Acceptance:** one editable cabinet; stable `/supplements`; Profile is
  contextual; mobile hierarchy matches.
- **Rollback:** retain stable cabinet and restore prior contextual link layout.
- **Major risk:** cross-repository sequencing.

### PR6 — Flag retirement and hardening

- **Goal:** after rollout evidence, remove dual template architecture and harden
  responsive, loading, error, accessibility, and performance behavior.
- **Likely systems:** flag registry/config, obsolete template/client code,
  metrics and end-to-end tests.
- **Non-goals:** no new domain capabilities; no unrelated Design System or
  Account work.
- **Prerequisite:** PR2–PR5 shipped and observed; release owner confirms runtime
  state.
- **Acceptance:** one canonical renderer, no obsolete flag branch, performance
  and workflow gates green, rollback documented at commit/release level.
- **Rollback:** revert cleanup release; do not preserve a hidden duplicate
  authority indefinitely.
- **Major risk:** retiring the rollback before parity is proven.

## V. Dependency graph

```text
PR1 discovery / contract
          |
          v
PR2 shell + state-action parity  <--> Sprint 14 workout-session contracts
       /       \
      v         v
PR3 Training   PR4 Nutrition       (parallel after PR2 contract stabilizes)
   |              |
   |              v
   |           PR5 Supplements + mobile conceptual alignment
   \              /
    v            v
      PR6 hardening + flag retirement
```

Coach plan mutation work constrains PR2/PR3 refresh and version semantics.
Future Account convergence must consume the Plan ownership decided here and
must not move Supplement ownership back to Profile. Future Design System work
may style the resulting hierarchy after structural ownership is stable; it is
not a prerequisite for PR2.

## W. Test strategy

PR1 adds/retains small behavioral characterization guards for:

- exact primary destination URLs and Plan active ownership;
- stable GET route ownership for `/training`, `/nutrition`, and `/supplements`;
- atomic `/training` template selection with Plan V2 OFF and ON.

These guards are intentionally behavioral: changing a route removes a matched
rule, changing ownership alters `resolve_active`, and collapsing the flag branch
changes rendered markers. A downstream PR must update a guard only with an
explicit contract justification.

PR2 adds state-action tests across no-plan, start, resume, complete, read-error,
and partial states, plus a query/provider-call budget. PR3 adds mutation-version
and session-staleness cases. PR4 re-runs canonical MealLog, provider serving,
water, and Nutrition-plan suites. PR5 adds one-cabinet and mobile navigation
contract tests. PR6 proves one renderer and removes obsolete-branch assertions.

### PR1 verification and adversarial review record

- Restored-tree focused run: 366 passed across the new guards plus navigation,
  Plan rendering, Nutrition, Supplements, feature flags, Today guidance, Coach
  plan-tool architecture, and workout-state/session suites.
- Non-vacuity run: intentionally wrong endpoint, `/plan` expectation, and
  flag-selected asset produced three targeted failures; all were restored and
  the 366-test run then passed.
- Documentation check: the relative link resolves and all 25 required A–Y
  headings are present. `git diff --check` is the whitespace gate.
- Adversarial result: no P0. One process P1 (the remote advanced during the
  audit) was closed by fetching, rebasing to `ddfbe04`, inspecting PR #283, and
  rerunning validation. Product P1 risks are explicitly assigned to PR2–PR4 in
  section T; they are risks of the future implementation, not defects introduced
  by this docs/test-only change.
- Residual P2/P3 and the UNKNOWN production flag value remain visible in
  sections B, H, L, R, and T. None makes the selected architecture unsafe.

## X. Explicit deferred items

- Production implementation, visual design, exact component styling, animation,
  and Design System adoption.
- Any new `/plan` route or migration.
- Plan V2 flag changes or retirement.
- Training algorithms, Nutrition calculations, provider selection, schemas,
  APIs, and mutation semantics.
- Workout-session rollout and Sprint 14 capability changes.
- New Coach tools or broader Coach write authority.
- Mobile code changes; the sibling repository remains untouched.
- Account/Profile convergence and Community IA.
- Recovery product definition beyond reserving its future Plan placement.
- Water blueprint relocation and unrelated technical-debt cleanup.

## Y. Definition of Plan Domain Complete

Plan Domain convergence is complete only when all of these are true:

- `/training` is the single canonical Plan landing and no competing `/plan`
  home exists.
- The landing clearly places Training, Nutrition, Supplements, and future
  Recovery without embedding duplicate child applications.
- Today Create/Start/Resume and Plan Training entry reach the same canonical
  state-appropriate workflow.
- Training creation, regeneration, state, sessions, completion, weekly planning,
  and Coach mutation retain the authorities named in section E.
- Nutrition targets, MealLog, providers, planning, history, water, and
  Supplements retain the authorities named in sections F–G.
- `/nutrition` and `/supplements` remain stable, with Plan navigation active.
- Workout history is Progress-owned; Account/Profile exposes contextual links
  without duplicate editors.
- Web and mobile share the four destinations and domain/state semantics.
- The landing performs no provider/AI boot calls, isolates section failures,
  and meets measured performance gates.
- One renderer remains after observed rollout; the Plan V2 flag and obsolete
  template path are retired through an explicit cleanup change.
- Contract, workflow, mutation, route, accessibility, responsive, performance,
  and cross-platform tests pass, and no P0/P1 convergence risk remains open.
