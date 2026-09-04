# UX-2 PR5 Today Guidance Orchestration Design

## Status and scope

This design implements UX-2 PR5 on `origin/main` at
`ab178830e527a18867c2f35037ee5e83dca0174d`, the UX-2 PR4 squash merge. There
are no later `origin/main` commits to reconcile. PR4's single production Home,
canonical workout-state vocabulary, four-primary navigation, and deleted legacy
dashboard remain fixed.

PR5 adds an explicit server-side decision contract. It does not redesign Today,
add an LLM call, create a second fitness-state authority, or move domain rules
into the presenter.

## Authority matrix

| Fact | Source | Freshness and failure semantics | Safe for ranking? | Safe for copy? | PR5 use |
|---|---|---|---|---|---|
| Workout primary state | `workout_state.resolve_workout_state()` snapshot | Captured during the server request for the canonical Istanbul day; resolution error makes Today unavailable | Yes | Yes | Primary decision and brief emphasis |
| Canonical workout action | Same snapshot `action` dimension | Same request and failure boundary as primary state | Yes, only with a compatible canonical state | Yes | Resume/Start eligibility |
| Active plan existence | `today_facts.get_active_plan()` | Latest user-scoped `TrainingPlan`; read failure fails Today closed | Yes | Yes | Create Plan eligibility through canonical `no_plan` state; no separate re-derivation |
| Scheduled workout metadata | `serialize_today_plan()` over the active plan | Same captured Istanbul day; parse/bounds failure returns no summary while canonical state reports the condition | No new decision rule | Yes | Supporting focus/duration/exercise count only |
| Active/in-progress session | Workout-state resolver's v2 session enrichment | Canonical only when resolver emits `in_progress` + `resume`; session read failures cannot manufacture Resume | Yes through snapshot only | Yes | Resume candidate |
| Nutrition daily target | Latest `UserSession.target_calories` through `nutrition_targets` | Published by `/meal-log/today`; currently hydrated after HTML render | No in PR5 | Yes as existing compact status | Supporting-only |
| Nutrition totals/remaining | Today's user-scoped `MealLog` rows plus `remaining_macro_budget()` | Published by `/meal-log/today`; client-hydrated later than the server decision | No in PR5 | Yes as existing compact status | Supporting-only |
| Food logged today | Presence of current-day `MealLog` rows | Authoritative ledger fact, but no server-side Today fact and no owned urgency rule | No | Literal presence/absence only | Deferred; not surfaced as guidance |
| Check-in history | `/checkin-history` over `WeeklyCheckIn` | Client-hydrated history; absence is not a due signal | No | Measured values only | Existing compact progress signal |
| Check-in due | No canonical service or published field exists | Unknown; raw timestamps cannot supply cadence | No | No | Deferred |
| Axis Insight | `progress_insights.build_progress_insights()` | Server-request snapshot; independent failure becomes no insight | No primary ranking | Yes, verbatim Progress copy | Optional supporting insight |
| Canonical time/daypart | `timeutil.app_now()` / `app_today()` in Europe/Istanbul | Server-authoritative and test-freezable | Technically safe, but no competing low-priority actions justify it | Yes | Day captured; daypart deferred |

## Alternatives considered

### Selected: a small pure decision module

Add `app/today_guidance.py` as a pure bounded contract. It consumes only the
canonical workout `primary_state`, canonical `action`, and read availability.
It emits a typed `TodayDecision`; it performs no I/O and owns no domain facts.

This separates four responsibilities:

1. `today_facts` asks domain authorities what is true.
2. `today_guidance` decides which eligible action wins and what semantic brief
   emphasis follows.
3. `today_presenter` maps semantic decisions to localized copy keys and routes.
4. `today.html` renders the supplied view; `today.js` only formats and hydrates.

### Rejected: server-side Nutrition ranking in PR5

The canonical Nutrition ledger and target exist, but Today currently receives
them after render from `/meal-log/today`. Adding them to the primary request
would add bounded SQL and still would not create authority for “eat now,”
“behind,” or meal timing. Ranking “Log Food” would therefore add freshness and
query cost without an owned relevance predicate.

### Rejected: generic rules framework

A pluggable rules engine would obscure a small closed decision table and make
precedence harder to audit. PR5 needs one explicit contract, not reusable rule
infrastructure.

## Decision model

The pure module defines closed identifiers, frozen value objects, and an
explicit numeric priority table:

- candidate kinds: `resume_workout`, `start_workout`, `create_plan`;
- emphasis kinds: the canonical Today state identifiers plus `error`;
- decision fields: `state`, `primary_kind`, `emphasis`, `decision_reason`;
- candidate fields: `kind`, `priority`, `reason`.

The presenter remains the owner of action label keys, destinations, secondary
links, and localized copy. `decision_reason` is diagnostic/test-only and is not
rendered.

### Candidate eligibility

| Candidate | Eligibility | Authority | Destination |
|---|---|---|---|
| Resume Workout | `primary_state == in_progress` and `action == resume` | Canonical workout snapshot | `/training` |
| Start Workout | `primary_state == scheduled_not_started` and `action == start` | Canonical workout snapshot | `/training` |
| Create Plan | `primary_state == no_plan` and `action == none` | Canonical workout snapshot, whose schedule dimension consumed active-plan existence | `/training` |

No Nutrition, check-in, Progress, Coach, gamification, recovery, or daypart
candidate is eligible in PR5.

### Deterministic precedence

1. Resume Workout
2. Start Workout
3. Create Plan
4. No primary action

`rank_candidates()` chooses the lowest fixed priority number. Its unit tests
will supply deliberately competing candidates so swapping Resume/Start/Create
precedence fails even though today's canonical snapshot normally yields only
one workout candidate.

## Canonical state handling

| Canonical state | Required action dimension | Primary result | Brief emphasis | Continuation |
|---|---|---|---|---|
| `in_progress` | `resume` | Resume Workout | Resume the persisted session | Primary CTA |
| `scheduled_not_started` | `start` | Start Workout | Today's planned workout | Primary CTA |
| `no_plan` | `none` | Create Plan | No active plan | Primary CTA |
| `rest_day` | `none` | None | Honest rest day | Open Plan secondary |
| `execution_recorded` | `none` | None | Recorded evidence, not completion | Open Plan secondary |
| `unscheduled_execution` | `none` | None | Unscheduled recorded evidence | Open Plan secondary |
| `completed` | `none` | None | Canonically completed | Progress then Plan secondary |
| `unscheduled_completed` | `none` | None | Canonically completed off schedule | Progress then Plan secondary |
| `needs_attention` | `blocked` | None | Bounded attention state | Open Plan secondary |
| unknown/read failure/incompatible state-action pair | any | None | `error` | Open Plan secondary |

The state/action compatibility table is total. A recognized state carrying an
impossible action fails closed instead of allowing the presenter to trust the
action string in isolation. This prevents a future inconsistent snapshot from
rendering Resume on a scheduled state or Start on a terminal state.

## Brief and supporting information

The decision selects a semantic emphasis; the presenter selects the complete
localized `brief_key`. Existing state copy remains usable where it is already
truthful. Plan focus, duration, and exercise count remain optional supporting
facts only when the canonical bounded plan projection supplies them.

Axis Insight remains optional and subordinate. WATCH/WORKING codes use the same
Progress localization keys; NEXT MOVE remains excluded. Nutrition and check-in
hydration cannot change the brief or primary action.

## Failure and freshness contract

- Workout read failure, resolver `resolution_error`, unknown state, or an
  incompatible state/action pair produces the honest `error` decision.
- A secondary Axis Insight failure removes only the insight.
- Nutrition/water/check-in XHR failure leaves unknown values unknown and cannot
  rewrite server-rendered guidance.
- Missing target is not zero; missing data is never complete or rest.
- The decision uses one server request's canonical workout snapshot. Browser
  time and later hydration never participate.

## Client/server boundary

The route gathers facts and builds the decision before rendering. The template
receives one `TodayView.primary` value or `None`. JavaScript may format the
server-supplied date and hydrate compact status/progress values; it may not
contain candidate identifiers, priorities, state/action inference, urgency, or
daypart decisions. Structural tests protect this boundary.

## Query and performance budget

The selected design adds zero database queries, provider calls, HTTP calls, or
history scans. It reuses the facts PR4 already gathers. The current first-render
client request set is unchanged. A query-count regression test will pin that the
pure decision step performs no I/O.

## Files and tests

Expected implementation surface:

- create `app/today_guidance.py` for the pure decision contract;
- modify `app/today_presenter.py` to consume the decision and own copy/routes;
- modify `templates/today.html` only to render the explicit brief key if needed;
- update `tests/test_today_v2.py` and add a focused guidance test module if that
  keeps the decision table readable;
- update `locales/en.json` and `locales/tr.json` only for complete new semantic
  copy keys, if any;
- update `docs/handoff.md` and `CLAUDE.md` with the durable PR5 contract;
- extend the existing Today frontend-audit matrix only for materially distinct
  decision states.

Implementation follows red-green-refactor. Verification covers focused
decision tests, existing Today/Workout/mobile parity suites, navigation and
Coach guards, locale completeness, `node --check`, the hermetic responsive
matrix, full non-load pytest where practical, `git diff --check`, and the
repository database drift check. Browser evidence will cover English/Turkish,
320/390/768/1024/1366 widths, primary/no-primary/error states, and longest copy.

## Explicit deferrals

- **CHECK-IN DUE: DEFERRED.** No cadence/due authority exists.
- **RECOVERY/READINESS: DEFERRED.** No canonical state exists.
- **TIME-OF-DAY RANKING: DEFERRED.** The clock is canonical, but PR5 has no two
  otherwise-valid low-priority actions for daypart to break.
- **NUTRITION PRIORITY: SUPPORTING-ONLY.** Canonical values remain visible, but
  the late hydration and missing urgency rule exclude them from ranking.

## Rollback and non-goals

Rollback is git revert and deploy; `UIUX_TODAY_V2_ENABLED` remains historical.
There is no schema, migration, auth, navigation, Training mutation/generation,
Nutrition target computation, Progress algorithm, Coach behavior, provider,
or LLM change. Nothing in PR5 is pushed, merged, deployed, or flag-activated.
