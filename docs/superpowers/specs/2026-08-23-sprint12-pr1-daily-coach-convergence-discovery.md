# Sprint 12 PR1 — Daily Coach Convergence: Discovery & Architecture

**Date:** 2026-08-23
**Type:** Discovery + architecture. No product implementation.
**Primary repository:** `fitness-coach` (backend/web authority)
**Secondary repository:** `axisai_mobile` (read-only inspection)

---

## 1. Executive verdict

**READY FOR IMPLEMENTATION.**

The hard part is in better shape than expected. AxisAI already owns a genuinely
canonical daily-state authority — `app/services/workout_state` — that answers
"is there a workout today, is it done, is it a rest day, is there a plan" as one
deterministic, fail-safe, versioned snapshot. Nutrition already has a clean,
timezone-explicit mobile contract. Neither needs to be rebuilt, and Sprint 12
must not rebuild them.

What is missing is not intelligence. It is **wiring and truthfulness**:

1. **The mobile Today screen renders fabricated data in release builds.**
   `AppComposition.configured` wires `FixtureTodayRepository` — together with
   fixture Plan, Progress, Workout-detail and Workout-session repositories —
   unconditionally, including when native auth is enabled, and `pubspec.yaml`
   bundles `fixtures/` as a release asset. A production user sees a hardcoded
   "Upper Body Strength / Start workout" card dated 2026-07-28 (F10).
   **Fabricated release data is the highest-priority Sprint 12 implementation
   defect** (§9 *"The P0, stated explicitly"*, P0-1), and it is why **PR2A**
   precedes every other implementation PR (§36).
2. **The mobile app has no Daily Coach backend contract.** `/api/v1` publishes
   auth, nutrition and Pump Check. There is no training, workout-state,
   progress, check-in or Coach endpoint a native client can call — and the
   blueprint's registration is itself gated on `MOBILE_AUTH_ENABLED` (§7). Today
   cannot be composed from existing mobile endpoints, because the endpoints do
   not exist (F1).
3. **Three different vocabularies already describe "today"** (F3), and four
   different things already call themselves a primary/next action (F11). Sprint
   12's real job is to collapse these, not to add a fifth.

None of the three is an ambiguity about ownership or state semantics — those are
well understood and documented below. The three owner decisions that previously
held this report at `READY WITH CONDITIONS` have now been made and are recorded
in §41: mobile auth is **eligible for controlled staged rollout** rather than
permanently blocked (C1); today's workout is identified by a **typed context
tuple**, not a minted id (C2); and **Adaptive Coaching rollout is not a
prerequisite for core Today** (C3). What remains is implementation, in the order
§36 sets out.

### Findings index

`F*` = a factual finding about the current system, each backed by a test in
`tests/test_sprint12_daily_coach_discovery.py` (§31) or by a cited file:line.
`M*` = a mobile Today surface issue (§9). `P0/P1/P2` = the review-gate
classification (§38).

| id | Finding | Section |
|---|---|---|
| F1 | `/api/v1` has no training, workout, progress, check-in or Coach endpoint | §5, §7 |
| F2 | Web Today V2 already composes canonical authorities correctly and stays pure | §5b |
| F3 | Three different "today" state vocabularies exist | §8 |
| F4 | Today's plan projection publishes exercise names, not canonical `exercise_id` | §15 |
| F5 | Pending plan proposals and the mutation journal have no read path outside a Coach turn | §13 |
| F6 | P2-16: injury annotation runs before canonical exercise resolution, and the note is durable | §16 |
| F7 | "Today" is a single server-owned `Europe/Istanbul` day, used consistently | §20 |
| F8 | Workout completion is a Pump Check — an AI-gated, photo-bound write | §7 |
| F9 | No canonical recovery/readiness state, and no check-in due model | §7 |
| F10 | Mobile ships fixture Today/Plan/Progress/Workout data in release builds — **the sprint P0** | §9 (M1, M2) |
| F11 | Four different notions already call themselves a primary/next action | §8 |
| F12 | Pump Check "next check due" exists only as AI free text | §7 |
| F13 | `get_nudges` + `/dashboard-nudges` is a second "what should you do" engine | §8 |

---

## 2. Repositories, base SHAs, worktree

| | Backend / web | Native mobile |
|---|---|---|
| Repository | `fitness-coach` | `axisai_mobile` |
| Remote | `github.com/yusufbesirarslan/fitness-coach` | `github.com/yusufbesirarslan/axisai-mobile` |
| `origin/main` at discovery | `7707d750a241171e090a681fc398fb659f5d387d` | `e6aab4d594ecb5a0e24ac606c328d46ea2a3855e` |
| Role this PR | **primary** — worktree, report, tests | **secondary** — read-only inspection only |

**Worktree:** `.worktrees/sprint12-pr1-daily-coach-convergence-discovery`
**Branch:** `sprint12-pr1-daily-coach-convergence-discovery`
**Base:** `7707d75`

### Why `fitness-coach` is the primary repository

Not by default — by evidence:

- `docs/superpowers/specs/` in `fitness-coach` holds every prior sprint report
  (Sprints 9, 10, 11, the UIUX sprints, the Adaptive Coaching sprints). The
  mobile repo's `docs/superpowers/specs/` holds only its own client-side
  designs. A cross-repository convergence report belongs with the cross-cutting
  history.
- Every authority this report maps — workout state, plan, nutrition, progress,
  check-in, Pump Check, Coach context, plan mutation — is backend-owned. The
  mobile repo owns presentation and one navigation contract.
- The proposed Sprint 12 dependency graph is backend-first (§34, §36). The
  repository that gates the sprint should hold the plan for it.

The suggested path in the brief matches the repository's existing convention, so
it is used unchanged:
`docs/superpowers/specs/2026-08-23-sprint12-pr1-daily-coach-convergence-discovery.md`.

### Repository hygiene

Both remotes fetched. Sprint 11 PR4 **is merged** — `7707d75`, "Sprint 11 PR4:
canonical exercise authority (#236)", 2026-08-23. (The project memory recorded
it as open; it landed since.)

Both repositories carry many active unrelated worktrees (28 in `fitness-coach`,
13 in `axisai_mobile`). None was touched. In particular the `fitness-coach`
primary checkout is dirty on `fix/pr1-ui-layout-typography-stabilization` — that
work is untouched, which is precisely why a dedicated worktree was created off
`origin/main` rather than working in place.

**The mobile repository was not modified, branched, or checked out.** All mobile
evidence in this report comes from `git show origin/main:<path>` / `git ls-tree`
/ `git grep origin/main`. `axisai_mobile`'s working tree is clean and still on
`mobile/foundation-pr3-core-screens`.

---

## 3. Sprint 11 prerequisites verified

| Prerequisite | Status | Evidence |
|---|---|---|
| Sprint 11 PR4 merged | ✅ | `origin/main` = `7707d75` (#236) |
| Canonical exercise catalog exists | ✅ | `app/services/exercise_catalog.py`, `app/services/training_assets/exercises.json` |
| Generation resolves exercises canonically | ✅ | `app/services/training_generation/exercise_resolution.py` |
| Save enforces canonical identity | ✅ | `validate_plan_for_save(...)` → `canonicalize_plan_exercises` |
| Plan mutation enforces canonical identity | ✅ | `app/services/plan_mutation/document.py:246` |
| **Authority reaches execution + today's wire projection** | ❌ | see F4, §15 |

---

## 4. Current mobile entry flow (cold app open)

Traced through code, not screenshots.

```
main.dart
  → AppComposition.configured(rollout, rawApiBaseUrl, isRelease, allowsLoopbackHttp)
      ├─ rollout.enabled == false → NO auth graph, NO live repositories at all
      └─ rollout.enabled == true  → auth graph + LIVE nutrition + LIVE pump check
                                    (today / plan / progress / workout stay FIXTURE)
  → createAppRouter(composition)
  → /auth/loading  (bootstrapSafe)
      → session restore via FlutterSecureSessionStore + RefreshCoordinator
      → unauthenticated → /login  (unauthenticatedOnly)
      → authenticated   → StatefulShellRoute.indexedStack
                            branch 0: /today     ← landing surface
                            branch 1: /plan
                            branch 2: /coach
                            branch 3: /progress
```

`/` is a declared alias of `/today`, and `/today` is also the
`unknownRouteFallback`. **Today is unambiguously the app's home surface**
(`lib/app/navigation/app_route_registry.dart`).

| Property | Current behaviour |
|---|---|
| Route | `/today`, `AppRouteId.today`, `AppDestinationId.today` |
| State owner | `TodayController` (`ChangeNotifier`), created in `TodayScreen.initState` |
| API calls | **Zero.** `FixtureTodayRepository` reads a bundled JSON asset. |
| Second data source | `NutritionDiaryController` → `GET /api/v1/nutrition/diary/today` (1 real request) |
| Loading | `TodayInitial`/`TodayLoading` → `LoadingState` |
| Cache / staleness | None. No persistence, no TTL, no stale marker. |
| Refresh | `load()` once per screen mount. `StatefulShellRoute.indexedStack` keeps the branch alive, so **switching tabs does not reload**, and app resume does not either (`AppNavigationCoordinator.noteLifecycleState` deliberately re-evaluates only pending navigation). |
| Empty state | `TodayEmpty` → `EmptyState`, CTA opens an "not connected" dialog |
| Error states | `TodayTemporarilyUnavailable`, `TodayRetryableError` |
| Survives restart? | Navigation location does (`restorable: true`). Data does not — it is re-read from the asset. |
| Can the user determine their next action? | **No** — see §9. |

**Onboarding reachability.** `/api/v1/account/me` publishes `profile_complete`,
and `AuthAccount.profileComplete` parses it — so mobile *can* detect incomplete
onboarding. It cannot *resolve* it: profile setup is `GET/POST /setup`, a
session-authenticated HTML route with no `/api/v1` equivalent, and
`POST /training-plan` returns `400 no_session` until a `UserSession` exists. A
mobile-only user cannot reach a first plan at all.

---

## 5. Current Home/Today architecture (all three of them)

There are, right now, **three independent Today implementations**.

### 5a. Web legacy dashboard — `templates/index.html` (default, flag OFF)

`GET /` → `tracking.home()`. Client-side fetches on load: `/checkin-history`,
`/last-session`, `/meal-log/today`, `/water`, `/workout/status`,
`/leaderboard/reward-check` (~6 requests) plus a `cdn.jsdelivr.net` Chart.js
script. A dashboard of metrics, not a decision.

### 5b. Web Today V2 — `templates/today.html` (flag `UIUX_TODAY_V2_ENABLED`, default OFF, **shipped dark**)

The best-designed of the three and the correct architectural model:

```
app/services/today_facts.gather_today_facts(user_id)   ← impure, composes
    ├─ get_active_plan(user_id)          — the exact /training-plan/active selector
    └─ resolve_workout_state(user_id)    — the canonical Sprint 7 PR1 owner
              ↓  TodayFacts(read_ok, has_active_plan, workout_completed_today)
app/today_presenter.build_today_view(facts)            ← PURE, no I/O
              ↓  TodayView(state, primary: Action|None, secondary: tuple)
```

Properties worth preserving verbatim in Sprint 12: at most one dominant CTA;
`None` is a valid primary; a failed read renders an honest `error` state and
never degrades into `no_plan`; every `Action.href` is an existing route; labels
are localization *keys*, never translated copy; state identifiers are stable and
non-localized.

Its limitation: only four states (`no_plan`, `plan_ready`, `workout_done`,
`error`). It cannot express `rest_day`, so with the flag on it renders
"View plan" as the dominant CTA on a rest day. It also still fetches ~5 client
requests for the surrounding widgets.

### 5c. Mobile Today — `lib/features/today/` (fixture-backed)

A separate `Today` domain model with its own status enum, its own primary-action
enum, and server-authored `headline`/`reason` prose strings that **no server
produces**. Backed by `fixtures/today-*.json`, each explicitly stamped
`"status": "DRAFT_FIXTURE"`, `"authoritative_contract": false`.

---

## 6. The Daily Coach question, broken into deterministic questions

| # | Product question | Answerable **today** from canonical data? | Authority |
|---|---|---|---|
| Q1 | Is there a scheduled workout today? | ✅ Yes | `workout_state.schedule_state` |
| Q2 | Is today a rest day? | ✅ Yes | `workout_state.is_rest_day` |
| Q3 | Has today's workout been completed? | ✅ Yes | `workout_state.completed_today` (today's `PumpCheck`) |
| Q4 | Is there execution evidence but no confirmed completion? | ✅ Yes | `execution_state = execution_recorded` |
| Q5 | Is there a current canonical training plan? | ✅ Yes | `today_facts.get_active_plan` |
| Q6 | What exercises are scheduled today? | ✅ Yes (names only) | `serialize_today_plan` — **no `exercise_id`** (F4) |
| Q7 | Is a workout in progress / resumable? | ⚠️ Only with `FITX_WORKOUT_SESSIONS_ENABLED` (default OFF, staging-only) | `workout_state.session_state` |
| Q8 | Is a training-plan change pending confirmation? | ❌ **No read path exists** (F5) | `plan_confirmation.get_pending`, reachable only inside a Coach turn |
| Q9 | Did Adaptive Coaching change my plan, and why? | ❌ **No read path exists** (F5) | `PlanMutationRecord` journal, Coach-turn-only |
| Q10 | What is my plan version? | ⚠️ Persisted (`mutation_version`), not published | `TrainingPlan` |
| Q11 | Is nutrition logging empty/incomplete today? | ✅ Yes | `GET /api/v1/nutrition/diary/today` |
| Q12 | Is a check-in due? | ❌ **No due model exists** (F9) | `WeeklyCheckIn` rows only |
| Q13 | What is my recovery/readiness state? | ❌ **Does not exist** (F9) | — |
| Q14 | Is a Pump Check follow-up due? | ❌ Not deterministically (F12) | `next_check_guidance` is AI prose |
| Q15 | Is there a meaningful Progress update? | ✅ Yes, web-only | `/api/progress/summary`, `/api/progress/axis-insights` |
| Q16 | Is there a blocked/error state needing action? | ✅ Yes | `workout_state.action = blocked`, `anomaly` |
| Q17 | Is onboarding incomplete? | ✅ Yes | `account/me.profile_complete` |
| Q18 | Is there genuinely nothing actionable? | ✅ Yes | derivable from the above |

**Eight of eighteen are already deterministic and canonical. None requires an
LLM.** Five are genuinely unavailable and must render as absent, not invented.

---

## 7. Canonical authority map

`det.` = deterministic · `stale?` = can be served stale · `mobile?` = consumed
by the native client today · `new EP?` = a new endpoint is required for mobile.

### Training

| Signal | Authority | Read model / endpoint | Persistence | Freshness | det. | stale? | mobile? | new EP? |
|---|---|---|---|---|---|---|---|---|
| Active training plan | `today_facts.get_active_plan` | `GET /training-plan/active`, `GET /training/bootstrap` | `TrainingPlan` (newest by `created_at`) | immediate | ✅ | no | ❌ | ✅ |
| Today's planned workout | `workout_state.serialize_today_plan` | `GET /training/bootstrap` → `today_plan` | `TrainingPlan.plan_data` JSON | immediate | ✅ | no | ❌ | ✅ |
| Workout completion | `workout_state.resolve_workout_state` | `GET /workout/status`, `/training/bootstrap` | today's `PumpCheck` | near-immediate | ✅ | **no** | ❌ | ✅ |
| Execution evidence | same | same | `WorkoutLog` (non-marker) | near-immediate | ✅ | no | ❌ | ✅ |
| Rest day | same | same | `plan_data.tip == dinlenme` | immediate | ✅ | ok | ❌ | ✅ |
| Session in-progress / resume | `workout_session` | `/workout/session/*` (404 when flag OFF) | `WorkoutSession` | immediate | ✅ | no | ❌ | conditional |
| Plan version | `TrainingPlan.mutation_version` | *unpublished* | `TrainingPlan` | immediate | ✅ | no | ❌ | ✅ |
| Pending AC proposal | `plan_confirmation.get_pending` | **none** | `TrainingPlanConfirmationProposal` | immediate | ✅ | **no** | ❌ | ✅ |
| Applied mutation / "why" | `plan_mutation.journal` | **none** | `PlanMutationRecord` | immediate | ✅ | ok | ❌ | ✅ |
| Deliberate regenerate | `POST /training-plan` + `/training-plan/save` | web only, 2-step, signed context token | `TrainingPlan` | — | ✅ | n/a | ❌ | ✅ (2 EPs) |
| Missed-workout state | — | **does not exist** | — | — | — | — | — | — |

`stale_previous_workout` exists on the snapshot (prior-day real rows with no
completion) — the nearest thing to "missed workout", but it is a diagnostic
flag, not a product state.

### Nutrition

| Signal | Authority | Endpoint | Freshness | det. | stale? | mobile? | new EP? |
|---|---|---|---|---|---|---|---|
| Today's diary + meals + totals + goal | `mobile_nutrition.build_diary_day` | `GET /api/v1/nutrition/diary/today` | near-immediate | ✅ | no | ✅ | no |
| Calorie target | same (`goal`, `null` when unset) | same | daily | ✅ | ok | ✅ | no |
| Hydration | `WaterLog` | `GET/POST /water` (web) | daily | ✅ | ok | ❌ | if needed |
| Nutrition plan | `nutrition/plan.py` | `GET /nutrition-plan/active` (web) | daily | ✅ | ok | ❌ | if needed |

The mobile nutrition contract is the **reference implementation** for this
sprint: `day: {date, timezone}` published explicitly, null macros stay null,
an unset goal is `null` not `0`, totals are server-authoritative, entry identity
is an opaque signed token, `Cache-Control: no-store`.

### Recovery / check-in

| Signal | Authority | Endpoint | det. | Notes |
|---|---|---|---|---|
| Weekly check-in write | `tracking.checkin` | `POST /checkin` | — | **AI-bound** (Bedrock feedback, `@ai_concurrency_gate`) |
| Check-in history | `WeeklyCheckIn` | `GET /checkin-history` (web) | ✅ | rows only |
| Fatigue / sleep / intensity / adherence | `WeeklyCheckIn` columns | not published individually | ✅ | 1–5 self-report |
| "Check-in due" | — | **does not exist** | ❌ | no cadence, no due date, no flag (F9) |
| Recovery / readiness classification | — | **does not exist** | ❌ | (F9) |

The closest surrogates are `analytics_engine._check_recovery_signals` (a nudge
heuristic: last check-in sleep ≤ 2 **or** fatigue ≥ 4) and the planner's weekly
`deload` focus. Neither is a readiness score and neither should be presented as
one.

### Pump Check

| Signal | Authority | Endpoint | det. | mobile? |
|---|---|---|---|---|
| Create | `mobile_pump_checks.service` | `POST /api/v1/pump-checks` | — | ✅ |
| History (paged, owner-private) | `mobile_pump_checks.history` | `GET /api/v1/pump-checks` | ✅ | ✅ |
| Single check | same | `GET /api/v1/pump-checks/<token>` | ✅ | ✅ |
| Comparison | `mobile_pump_check_comparisons` | `POST`/`GET /api/v1/pump-check-comparisons` | ✅ | ✅ |
| "Next check due" (**F12**) | — | `next_check_guidance` is **AI free text** | ❌ | prose only |

Pump Check is the one domain already fully wired to mobile. Note the coupling:
**a Pump Check *is* the workout-completion proof** (`workout_state` reads
today's `PumpCheck`), and `POST /workout/complete` runs a Bedrock vision call
behind the AI gate (F8).

### Progress

| Signal | Authority | Endpoint | det. | mobile? | new EP? |
|---|---|---|---|---|---|
| Canonical summary (trajectory/body/performance/consistency) | `progress_summary` | `GET /api/progress/summary` | ✅ | ❌ | ✅ |
| Axis Insights (working / watch / **next move**) | `progress_insights` | `GET /api/progress/axis-insights` | ✅ | ❌ | ✅ |
| Physique progress | `progress_physique` | `GET /api/progress/physique` | ✅ | ❌ | ✅ |
| History / heatmap / achievements | `progress_history` etc. | `/api/progress/*` | ✅ | ❌ | later |

`next_move` deserves care: it is projected **1:1 from `AdaptivePlan.week_focus`**
and is a *weekly training-emphasis* decision (`deload`, `progress_training`,
`maintain_current_training`, …). It is **not** a daily task and must not be
confused with a Today CTA — but Sprint 12 must not invent a second vocabulary
that contradicts it either.

### Coach

| Signal | Authority | Notes |
|---|---|---|
| Conversation | `blueprints/coach.py` — `/chat`, `/ask`, `/ask/stream`, `/coach/history`, `/coach/conversation/reset` | web/session auth only |
| Context injection | `services/context_builder.py` | already includes `resolve_workout_state(user_id).to_dict()` |
| Adaptive plan block | `adaptive_plan_context` | flag `AI_ADAPTIVE_PLAN_CONTEXT`, default OFF |
| Plan mutation proposals | `coach_plan_tools` | flag `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, default OFF |
| Deep-link into a Coach action | — | **mobile Coach is a hardcoded placeholder** |

`lib/features/coach/presentation/coach_screen.dart` is a `const UnavailableState`
— "Coach conversations are not connected in this foundation build." There is no
mobile Coach client and no `/api/v1/coach/*` endpoint.

### Feature-flag reality check

Every rollout flag's **repository default is OFF**, and no committed file sets
any of them — the repository ships `.env.example` only, and the deployed host's
`.env` is not in version control.

**What the repository can prove, and what it cannot.** It proves each flag's
declared default (`app/feature_flags.py`) and the code path each flag gates. It
**cannot** prove any value on the deployed host. Every "not registered" / "does
not occur" statement below is therefore a statement about the **repository
default**, and describes the deployed runtime only where the host has not
explicitly set the flag.

| Flag | Repo default | Lifecycle | Consequence for Sprint 12 (at the repository default) |
|---|---|---|---|
| `MOBILE_AUTH_ENABLED` | False | `blocked` in the registry — **superseded by C1: eligible for controlled staged rollout** | `/api/v1` blueprint registration is **gated on this flag**; at the default it is not registered and the mobile client has no backend contract |
| `AXISAI_NATIVE_AUTH_ENABLED` | — (compile-time) | blocked — **stays behind `MOBILE_AUTH_ENABLED`** | mobile builds no auth graph, no live repositories |
| `UIUX_TODAY_V2_ENABLED` | False | shipped dark | web Today V2 is not live |
| `FITX_WORKOUT_SESSIONS_ENABLED` | False | staging only | `in_progress`/`resume` are **not producible at the default** |
| `AI_ADAPTIVE_PLAN_CONTEXT` | False | staging only | no adaptive block in Coach prompts |
| `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` | False | staging only | **no plan mutations or proposals are created at the default** |

`app/__init__.py:336` gates blueprint registration on `MOBILE_AUTH_ENABLED`:

> `/api/v1` registration is gated by `MOBILE_AUTH_ENABLED`; the repository
> default is OFF, and the deployed runtime must explicitly enable the flag for
> the blueprint to exist. **The repository cannot prove the current host `.env`
> value**, because host rollout flags are not committed.

This distinction matters for sequencing: PR3's endpoint is only *operationally*
useful once the host has the flag on, but its correctness does not depend on
knowing today's host value, and PR2A's P0 fix does not depend on the flag at all.

**Registry lifecycle drift — resolved as a decision, recorded as a follow-up
(P1-1, C1).** `MOBILE_AUTH_ENABLED` is still marked `LIFECYCLE_BLOCKED` in
`app/feature_flags.py:507` on the prerequisite "PR4 (capacity hardening)
merged". Hardening PR4 **is** merged
(`34f8dc79be746f233974461bf0465e4bf32eb72d`, #200), and it added the shared
blocking-concurrency slot and thread-reserve/capacity accounting that the
prerequisite named. **The recorded blocker is satisfied** (owner decision C1,
§41), so the flag is eligible for controlled staged rollout.

The registry text has *not* been changed by this PR, deliberately: PR1 ships
architecture, not feature-flag implementation, and no PR1 characterization test
depends on the stale wording. What PR1 does is record the drift as a bounded
follow-up — see §41 *"Follow-ups owned by a later PR"*. The rollout **order** is
unchanged and non-negotiable: `MOBILE_AUTH_ENABLED` on the backend first, then
`AXISAI_NATIVE_AUTH_ENABLED` in the Flutter build only after backend
verification.

---

## 8. Duplicate and competing authorities

### F3 — three "today" state vocabularies

| Layer | Vocabulary | Source |
|---|---|---|
| Canonical resolver | `rest_day`, `scheduled_not_started`, `execution_recorded`, `completed`, `unscheduled_execution`, `unscheduled_completed`, `no_plan`, `needs_attention`, `in_progress` | `workout_state/models.py` |
| Web Today V2 | `no_plan`, `plan_ready`, `workout_done`, `error` | `app/today_presenter.py` |
| Mobile Today | `workoutReady`, `restDay`, `workoutActive`, `workoutCompleted`, `noPlan`, `error`, `unknown` | `lib/features/today/domain/today.dart` |

`no_plan` is the **only** shared token (proved by
`test_web_today_states_and_workout_state_vocabulary_do_not_match`).

### F11 — four "primary action" notions

1. `TodayPrimaryActionType` (mobile Today fixture) — `start_workout`,
   `view_recovery`, `resume_workout`, `view_progress`, `create_plan`, `retry`
2. `primary_action` in `fixtures/progress-summary.json` — `view_next_workout`
3. `today_presenter.Action` (web) — `create_plan`, `view_plan`, `view_progress`,
   `open_plan`
4. `progress_insights.next_move` (canonical, weekly emphasis) — `deload`,
   `progress_training`, …

Only #4 is canonical, and it answers a different question from the other three.

### F13 — two "what should you do" engines

`analytics_engine.get_nudges` + `GET /dashboard-nudges` is an existing daily
prompting engine (missing logs, streak at risk, protein goal, weekly report day,
recovery signals, overload stall, hydration). It is dual-purpose: the raw
strings are **LLM prompt directives** injected into the Coach context, and
`tracking.dashboard_nudges` re-maps them to hardcoded Turkish user copy with no
i18n, no priority, and no CTA routes.

This is the closest existing thing to a Next Best Action, and Sprint 12 must
decide explicitly whether Today consumes it, replaces its user-facing half, or
ignores it. **Recommendation: ignore it for Today.** Its user-facing half is
un-internationalized and route-less; its valuable half is Coach prompt context
and should stay there. Today derives its priority from canonical state instead
(§11). Do not delete it in Sprint 12 — the Coach depends on it.

### Progress contract divergence

`fixtures/progress-summary.json` (`range`/`summary`/`weight`/`training`/
`insight`/`primary_action`) and the canonical
`progress_summary_payload` (`contract_version`/`window`/`trajectory`/`body`/
`performance`/`consistency`) share **no** structural key. The mobile Progress
screen renders a shape the backend has never published.

### Non-duplications worth recording

- Web Today V2 correctly **delegates** completion to `resolve_workout_state`
  instead of re-querying `PumpCheck` (proved by
  `test_today_facts_delegates_completion_and_owns_no_query_of_its_own`).
- The Coach context already reads the canonical workout-state snapshot. No new
  Coach-facing training read model is needed.
- `/training/bootstrap` already composes plan + workout state + today's plan in
  one coherent transaction. It is the architectural precedent for §22, not a
  competitor.

---

## 9. Mobile Today surface audit

Every issue as *current behaviour → consequence → required ownership*.

| # | Current behaviour | Consequence | Ownership |
|---|---|---|---|
| M1 | `AppComposition.configured` wires `FixtureTodayRepository` even in release with native auth on; `pubspec.yaml` ships `fixtures/`. Default scenario is `workoutReady`. | Every production user sees the same fabricated workout ("Upper Body Strength", 55 min, 6 exercises) dated 2026-07-28. The app asserts a plan the user does not have. **P0.** | mobile |
| M2 | Same for `planRepository`, `progressRepository`, `workoutDetailRepository`, `workoutSessionRepository`. | Plan, Progress, workout detail and workout session are all fabricated in release. **P0.** | mobile |
| M3 | `headline` and `reason` are free-text strings read from the payload and rendered verbatim. | The contract expects the server to author user-facing prose. No server does. If a backend later fills them, copy becomes un-localizable server data. | both |
| M4 | Loading copy reads "Preparing your local daily direction"; error copy reads "The local development data could not be read." | Development language ships to users. | mobile |
| M5 | `TodayStatus` is one monolithic enum; a single failure moves the whole screen to `TodayRetryableError`. | One failed dependency blanks the decision surface. The nutrition card is the only independently-degrading section. | mobile |
| M6 | `createPlan` and `viewRecovery` CTAs open an `AlertDialog` saying the feature is "not connected". | The dominant CTA in the `no_plan` state — the state a new user is in — leads to a dead end. | both |
| M7 | `startWorkout` navigates to `/workout/detail` with `WorkoutRouteSource.today` and **no workout identity**; the detail screen then loads a fixture. | The CTA cannot open *today's* workout, only *a* workout. | both |
| M8 | Above the fold: date label, headline, reason, two facts (duration, exercise count), one full-width CTA — then the nutrition card as a `Flexible` footer. | Hierarchy is actually good. The dominant decision genuinely dominates. This is the one thing to preserve. | mobile |
| M9 | `_dateLabel()` formats `TODAY · dd.MM.yyyy` from the payload's date; `today.timezone` is parsed but never displayed or used. | The client cannot tell the user its "today" came from Istanbul, and cannot detect a stale day. | both |
| M10 | `TodayController.load()` runs once in `initState`; `indexedStack` keeps the branch alive; app resume does not refresh. | After logging a meal on another tab, completing a workout, or crossing midnight, Today keeps showing the first-load answer for the whole app session. | mobile |
| M11 | `_withNutrition` wraps the primary state in `Expanded` + a `Flexible`/`SingleChildScrollView` footer in the non-`TodayData` branches. | Two competing scroll/flex regions; at 2× text the split is fragile. `TodayData` uses a different composition (footer inside `TodayContent`), so loading/empty/error lay out differently from the populated state. | mobile |

**M8 is a genuine strength.** The mobile Today already renders *one dominant
daily decision*, not a grid of equal-weight cards. Sprint 12 should keep the
layout and replace the data behind it.

### The P0, stated explicitly — fabricated production fitness state

> **Fabricated release data is the highest-priority Sprint 12 implementation
> defect.** Release/mobile production composition can present fabricated
> Today/Plan/Progress/Workout state as if it were the signed-in user's own.

This is M1 + M2 + F10 + P0-1, restated in one place because every later decision
in this report depends on it and it must not be quietly downgraded.

Why it outranks everything else in the sprint, including the missing backend:

1. **It violates the canonical-state architecture this whole report is built
   on.** Every other section insists that exactly one authority owns each
   signal and that unsupported state stays unrepresented (§7, §10, §35). A
   client that can choose a fixture repository is a second, unowned authority
   for *user-specific* facts — the worst possible kind.
2. **It can misrepresent a real user's workout state.** Not a placeholder, not
   a demo mode: a specific fabricated workout ("Upper Body Strength", 55 min,
   6 exercises, dated 2026-07-28) presented as this user's plan for today. A
   user who trains what the app shows is training something nobody prescribed.
3. **It undermines every later Daily Coach feature.** Priority ranking, Coach
   handoff, invalidation, notifications and Progress all read Today's state. A
   surface that may be fictional makes all of them unverifiable.
4. **Backend Today work cannot be trusted while release UI can still choose
   fake repositories.** PR3 could ship a perfectly correct `GET /api/v1/today`
   and a release build would still be free to ignore it. Fixing the composition
   first is what makes every subsequent PR *checkable*.

**Required fallback hierarchy — in order, with no fifth option:**

| # | State | Acceptable in production? |
|---|---|---|
| 1 | Real canonical data | ✅ the goal |
| 2 | Truthful loading state | ✅ |
| 3 | Truthful empty state ("you have no plan yet") | ✅ |
| 4 | Truthful degraded / unavailable state ("we can't load this right now") | ✅ |
| 5 | **Fabricated production fitness state** | ❌ **never** |

The desired invariant, stated as an acceptance criterion for PR2A:

> Production may show real, empty, unavailable, or degraded state — **never
> fabricated user-specific fitness state.**

Fixtures remain entirely legitimate in tests, in local development, and in any
build that cannot reach a real user's data — the defect is that the *release*
composition path can select them. Exact scope (which repositories, which
composition branches, what happens to the bundled `fixtures/` asset, and what
architecture test enforces it) is **PR2A's** to derive from §9 and §35; it is
not implemented here.

---

## 10. Proposed Daily Coach state model

Derived from what the authorities can actually prove. **Not a single monolithic
enum** — Today is a small set of independently-degradable sections over one
shared day.

```
TodayReadModel
  day            : { date: ISO, timezone: IANA }        ← always present, server-owned
  sections:
    training     : SectionState<TrainingToday>
    nutrition    : SectionState<NutritionToday>
    onboarding   : SectionState<OnboardingToday>
    plan_change  : SectionState<PlanChangeToday>        ← optional capability
                                                          state; PR5, conditional
  primary        : Action | null                        ← at most one, server-decided
  secondary      : Action[]
```

`SectionState` is deliberately tiny and uniform:

| State | Meaning |
|---|---|
| `available` | the section resolved; payload present |
| `empty` | resolved, and the honest answer is "nothing here" |
| `unavailable` | this section's read failed; other sections are unaffected |

Client-only states, never on the wire: `loading`, `offline_stale`.

`TrainingToday` reuses the canonical vocabulary **unchanged** — the resolver's
`primary_state`, `schedule_state`, `execution_state`, `action`, `is_rest_day`,
`completed_today`, `anomaly`, `contract_version`. Sprint 12 introduces **no new
training state names**. Web Today V2's four-state vocabulary is retired in
favour of it (that is the F3 convergence).

`TrainingToday` also carries the **daily workout context identity** decided in
C2 — `(plan_lineage, mutation_version, canonical_local_date)`, §18 — as typed
context for the "open today's workout" CTA. It is a context key, not an entity
id: nothing persists it and nothing may treat it as a durable handle.

`plan_change` is **optional capability state** (C3, §13). Core Today is complete
and correct when that section is `empty`, which is what it always is while the
Adaptive Coaching flags are off. No section may be promoted to a prerequisite
just because a future rollout might fill it.

Why not one enum: a Progress or plan-change failure must never blank the
training decision, and the nutrition card already proves independent degradation
works well in this app.

---

## 11. Next-best-action architecture

**Yes, AxisAI needs exactly one explicit Next Best Action**, and it must be a
**canonical backend decision**, not a client presentation rule and not an LLM
call.

Rationale, from evidence rather than taste:

- The inputs are all server-owned (§7). A client rule would have to re-derive
  ownership it does not have.
- The repository already made this call twice, deliberately: `today_presenter`
  is pure and receives a decision; `progress_insights` refuses to derive a move
  from evidence the planner already weighed. A third, client-side ranking would
  contradict both.
- A future notification system needs the same decision without a client (§29).
- `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` is off by default, so most of the
  interesting priority interactions are currently empty — the policy must be
  data-driven, not hardcoded to states that never occur. Per C3 (§13), rank 2
  below is **evaluated from data and simply never fires** while the Adaptive
  Coaching flags are off; the policy is not conditioned on the flags, and no
  rank above or below it changes.

**Deterministic priority policy** (proposed; every rule maps to a signal proved
available in §6):

| Rank | Condition | Primary action | Canonical source |
|---|---|---|---|
| 0 | `profile_complete == false` | Complete setup | `account/me` |
| 1 | `workout_state.action == blocked` or `anomaly` present | Resolve / open plan | `workout_state` |
| 2 | pending plan proposal exists *(PR5)* | Review plan change | `plan_confirmation` |
| 3 | no active plan | Create plan | `today_facts` |
| 4 | `session_state == active_resumable` *(flag on)* | Resume workout | `workout_session` |
| 5 | `primary_state == scheduled_not_started` | Start today's workout | `workout_state` |
| 6 | `primary_state == execution_recorded` | Finish / confirm workout | `workout_state` |
| 7 | rest day **and** nutrition empty | Log your first meal | nutrition |
| 8 | workout completed **and** nutrition empty | Log your first meal | nutrition |
| 9 | rest day, nothing outstanding | View plan (informational) | `workout_state` |
| 10 | everything done | No dominant CTA — `primary = null` | — |

Rank 10 is not a gap. `today_presenter` already establishes that `None` is a
valid primary action, and §19 requires the app to stop shouting after
completion.

**Deliberately absent from the ranking:** check-in due (Q12) and Pump Check
follow-up (Q14). Neither has a canonical due model, so neither can be ranked
without inventing one. They stay out until a due model is designed — that is a
separate product decision, not a Today problem.

---

## 12. AI vs deterministic classification

| Behaviour | Classification | Note |
|---|---|---|
| Is there a workout today / rest day / no plan | **Deterministic** | `workout_state` |
| Is it completed | **Deterministic** | `completed_today` |
| Which exercises are scheduled today | **Deterministic** | `serialize_today_plan` |
| Nutrition totals / goal / remaining | **Deterministic** | server-authoritative totals |
| Is onboarding incomplete | **Deterministic** | `profile_complete` |
| Next Best Action ranking | **Deterministic** | §11 |
| Today's headline / reason copy | **Templated explanation** | localization key + typed params. **Never server prose** — that is M3 |
| "Why did my plan change?" | **Templated explanation** | the journal holds typed commands; render them, don't narrate them |
| Progress trajectory / Axis Insights / next move | **Deterministic** | already computed server-side |
| Pump Check analysis + comparison | **AI-assisted** | already is, already scoped |
| Check-in feedback | **AI-assisted** | already is |
| Coach conversation | **AI-assisted** | already is |
| Recovery / readiness state | **Not supportable yet** | no canonical data (F9) |
| "Is a check-in useful today?" | **Not supportable yet** | no cadence model (F9) |
| "Is a Pump Check follow-up due?" | **Not supportable yet** | AI prose only (F12) |
| Free-form "what should I do today?" narration | **Not supportable — and not wanted** | it would be an LLM wrapped around an if-statement |

**No Today behaviour in the proposed architecture requires an LLM.** Every
AI-assisted item above is an existing feature Today merely links to.

---

## 13. Adaptive Coaching integration

Shipped and solid — and completely invisible outside a Coach conversation.

- **How a pending proposal surfaces today:** only as text inside an AI Coach
  turn. `get_pending` has exactly one consumer in the whole application,
  `coach_plan_tools/executor.py` (proved by
  `test_pending_plan_confirmation_is_reachable_only_from_coach_plan_tools`).
- **Confirmation / cancellation:** `coach_plan_policy/confirmation.py`, a narrow
  structural CONFIRM/CANCEL/NONE parser over the user's next chat message. Fail
  closed: negation or hedging never confirms. No LLM. Owner-scoped
  `SELECT … FOR UPDATE` in `plan_confirmation.lock_proposal`.
- **Applied mutations and today's workout:** a confirmed mutation rewrites
  `TrainingPlan.plan_data` and bumps `mutation_version`, so
  `serialize_today_plan` reflects it on the next read. Correct by construction.
- **Plan version exposure:** `mutation_version` is persisted but published
  nowhere.
- **Can the user understand something changed?** Only if they are in the
  conversation where it happened. No blueprint reads `plan_mutation` (proved by
  `test_no_blueprint_reads_the_plan_mutation_journal`).
- **"Why did my plan change?"** — the evidence is excellent
  (`PlanMutationRecord`: typed command, snapshot fingerprint, lineage, actor,
  outcome, idempotency key) and completely unreachable.

**Constraint for Sprint 12 (non-negotiable):** Today never mutates a plan. It
surfaces `pending: true` + a summary and routes to the existing confirmation
authority. It must not gain its own confirm/cancel path, and it must not call
the Coach to perform a confirmation.

**Production caveat:** with `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` off, no
proposals are ever created. A pending-proposal surface would be permanently
empty. This is why PR5 is flag-conditional and sequenced last.

### Owner decision C3 — Adaptive Coaching is not a Today prerequisite

> **Sprint 12's core Today architecture must not depend on Adaptive Coaching
> rollout flags being enabled.**

`AI_ADAPTIVE_PLAN_CONTEXT` and `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` remain
staged/experimental capabilities. Neither is activated by PR1, and neither may
be activated merely to populate a Today card. Concretely, this binds the
architecture as follows:

- **Core Today must be correct with both flags OFF.** That is the *default*
  design target, not a degraded mode. Ranks 0–1 and 3–10 of the §11 policy are
  fully reachable without either flag; rank 2 simply never fires.
- **Pending-proposal state is optional capability state, not a core Today
  prerequisite.** `plan_change` is a `SectionState` like any other (§10); when
  no proposal source is active it is `empty`, and an `empty` optional section is
  a normal, shippable outcome.
- **"Why did my plan change?" is never fabricated.** If `PlanMutationRecord`
  evidence is unavailable — because no mutation happened, or because the read
  failed — Today says nothing about a plan change. It does not infer one from a
  plan-content diff, a `mutation_version` bump alone, or a Coach transcript.
- **No permanently empty Adaptive Coaching UI ships** merely because a rollout
  may happen later. §37's rule applies: a surface with no possible content is
  not shipped.

If Adaptive Coaching rollout is approved later, it is integrated in a dedicated
**conditional** Sprint 12 PR (**PR5**, §36) *after* its own rollout
prerequisites are proven — not folded into PR3 or PR4. If the flags stay off,
PR5 is deferred out of the sprint and nothing in PR2A–PR4 needs to change.

---

## 14. Training Generator integration

- **Can mobile create a plan?** No. No `/api/v1` training route exists; the
  `createPlan` CTA opens an "not connected" dialog.
- **Does mobile only read plans?** It does not even read them — `plan` is
  fixture-backed.
- **Is web generation the sole entrypoint?** Yes, and it is a **two-step, gated,
  stateful** flow:
  `POST /training-plan` (requires an existing `UserSession`; `@premium_ai_plan_gate`
  — non-premium: one generation per week; `@ai_concurrency_gate`; Bedrock rate
  limit) returns a candidate **plus a signed exercise-context token**, then
  `POST /training-plan/save` re-validates and persists it.
- **What happens to a mobile user with no plan?** They see the fixture
  `workoutReady` card — i.e. they are told they *have* a plan. Once M1 is fixed
  they would correctly see `no_plan`, whose only CTA is currently a dead end.

**Recommendation: mobile Create Plan is *not* in the Sprint 12 core sequence,
and probably not Sprint 12 at all.** It carries its own dependency chain — mobile profile setup
(`UserSession`), the premium gate, the two-step generate/save round-trip with a
signed context token, and long AI latency on a mobile connection. That is a
sprint, not a PR. **Sprint 12 scope: Today must render the `no_plan` state
honestly and route to a surface that can actually create a plan** — which today
means an explicit, honest handoff. Getting a first plan onto a phone is the
correct Sprint 13 candidate.

---

## 15. Canonical exercise authority integration

Sprint 11 PR4's authority is real and correctly enforced **at the plan-document
boundary** — generation (`exercise_resolution`) and mutation
(`plan_mutation/document.py`) both refuse non-canonical identity.

It does **not** reach two places Today would touch:

**F4 — today's wire projection drops it.**
`workout_state/serialization.py:_serialize_day` publishes
`{isim, set, tekrar, dinlenme, not}` and omits `exercise_id`, even when the
stored plan carries one (proved by
`test_today_plan_projection_publishes_names_not_canonical_ids`). Any Today
surface built on `/training/bootstrap` would put name-based exercise identity
back on the wire.

**Remaining name-based consumers** (documented, not migrated in PR1):

| Consumer | Identity | Note |
|---|---|---|
| `WorkoutLog.exercise_name` (`String(120)`) | name | the execution ledger has **no** `exercise_id` column |
| `ai_coach._tool_stage_workout_log` / `commit_workout_log` | name | LLM-supplied string, truncated to 120 |
| `WORKOUT_COMPLETION_MARKER` | magic name string | `"Antrenman tamamlandı (Pump Check)"` |
| `injury_constraints.find_contraindicated` | name | this is P2-16 |
| `training_history` / progress volume aggregation | name | grouped by `exercise_name` |

**Required guard for Sprint 12:** the new Today read model must publish
`exercise_id` alongside `isim` for today's exercises. Migrating `WorkoutLog` to
canonical identity is a larger Training project and is explicitly **not** Sprint
12 scope.

---

## 16. P2-16 disposition (finding **F6**)

**Finding restated and confirmed.** `annotate_injuries` matches
`find_contraindicated(ex["isim"], injuries)` on the **raw provider string**;
`canonicalize_plan_exercises` runs afterwards. Proved by source line ordering,
not by comment: `_parse_and_validate` at `service.py:246` and `:266`,
`canonicalize_plan_exercises` at `:282`
(`test_injury_annotation_precedes_canonical_exercise_resolution`).

Two provider aliases that resolve to the same catalog entry can therefore
receive different warning annotations.

**Severity is higher than "annotation inconsistency".** `annotate_injuries`
writes into `ex["not"]`, and `not` is a persisted plan-schema key
(`EXERCISE_KEYS`), so the inconsistent warning is **durable in
`TrainingPlan.plan_data`** and is republished verbatim by `serialize_today_plan`
(proved by `test_injury_warning_text_persists_into_the_stored_plan`).

**Disposition — all four of the brief's options, answered:**

| Question | Answer |
|---|---|
| Does it belong in Sprint 12? | **Yes**, as an isolated Training fix — not as Daily Coach work. |
| Is it an isolated Training follow-up? | **Yes.** The fix is local: run `annotate_injuries` after canonicalization and match on the resolved `ExerciseDefinition`. |
| Should it be fixed before Daily Coach consumes injury warnings? | **Yes.** Today's exercise projection carries `not`. |
| Should Today consume that warning at all? | **Not as free text.** Today should render at most a typed "has warnings" affordance and route to the plan/workout surface for detail. Free-text safety copy on a decision surface is a product risk. |

**Owner: backend (Training). Sequenced as PR2B** — after the P0 fixture
elimination (PR2A) and before the Today read model (PR3) republishes persisted
workout exercise notes more broadly. Not implemented in PR1.

**Bounded closure, not a new capability.** The desired direction is exactly one
reordering: **canonicalize exercise identity first, then apply the supported
warning annotation against the canonical identity/name.** PR2B must preserve
warn-only semantics; it introduces no medical inference engine, no diagnosis or
contraindication behaviour, and no broadening of the existing injury rules
beyond the logic already supported. It closes a Sprint 11 defect; it does not
open a Health domain.

---

## 17. Coach context handoff

The Coach is already well fed. `context_builder.fetch_profile_and_trends`
injects: user profile + memory, injury constraints (same engine as the plan
generator), fitness summary, weekly check-in trend, daily activity, friend
activity, proactive nudges — and, at line 121,
`resolve_workout_state(user_id).to_dict()`, the **same canonical snapshot**
Today would use. With `AI_ADAPTIVE_PLAN_CONTEXT` on it also gets the versioned
read-only `AdaptivePlan` block.

Not injected: Pump Check, and the canonical Progress summary / Axis Insights.

**Can Today safely offer contextual Coach actions?** Architecturally yes — the
Coach already shares Today's authority, so a handoff duplicates nothing.
Practically **no, not in early Sprint 12**: there is no mobile Coach client and
no `/api/v1/coach/*` endpoint. This is a whole PR, not a CTA.

**Rule when it is built:** the handoff carries a typed intent plus canonical
identifiers (`{intent: "explain_today_workout", day: "2026-08-23",
plan_version: 7}`) — never client prose, and never a client-composed summary of
state the server already owns.

**Circular-dependency guard:** the Coach must not call the Today read model, and
the Today read model must not call the Coach. Both compose the same domain
authorities independently. A shared `Today → Coach → Today` path would make
Coach latency a Today failure mode.

---

## 18. Navigation / deep-link model

Mobile's navigation contract is the strongest asset in either repository:
one canonical registry owns every path, credential-shaped route parameters are
structurally rejected, aliases can never become second identities, and
destinations must agree with their root route on auth policy, shell
participation and restorability.

| Today action | Canonical route | Deep-link eligible? | Exists today? |
|---|---|---|---|
| Open today's workout | `/workout/detail` (owner `plan`) | ❌ needs typed in-memory context | route ✅ / data ❌ |
| Resume/start session | `/workout/session` | ❌ | route ✅ / data ❌ |
| Open training plan | `/plan` | ✅ | ✅ |
| Confirm plan change | — | — | **does not exist** |
| Create plan | — | — | **does not exist** (dialog) |
| Log food | `/today/nutrition/add` | ❌ (by design) | ✅ live |
| Open nutrition diary | `/today` (card in place) | ✅ | ✅ live |
| Complete check-in | — | — | **does not exist** |
| Open Pump Check | `/progress/pump-check/new` | ❌ (private) | ✅ live |
| View comparison | `/progress/pump-check/compare/result` | ❌ (private) | ✅ live |
| View Progress | `/progress` | ✅ | route ✅ / data fixture |
| Open Coach with context | `/coach` | ✅ | route ✅ / screen placeholder |

**The workout-identity problem (settled by owner decision C2, below).**
`/workout/detail` and `/workout/session` are deliberately not deep-link eligible
because *no public workout identifier exists*. And none can be minted from the
data: `TrainingPlan.plan_data` is a 7-day array keyed by Turkish weekday name;
a "workout" has no id, no stable key, and no row of its own. The mobile fixtures
invent `workout_example_001`, which is exactly the fabrication to avoid.

### Owner decision C2 — the daily workout context identity

**Accepted: do not mint a workout id.** Today's workout is identified by a typed
**daily workout context identity**:

```
DailyWorkoutContext = (plan_lineage, mutation_version, canonical_local_date)
```

This is **not** a new permanent or public Workout ID. It is an in-memory / API
*context* identity whose only job is to distinguish three things the system
already knows:

| Component | Answers | Canonical source | Proof it exists |
|---|---|---|---|
| `plan_lineage` | which plan lineage owns this workout | `TrainingPlan.lineage_id` — opaque `secrets.token_urlsafe(32)`, unique index `uq_training_plan_lineage_id`, regenerated whenever `POST /training-plan/save` replaces the plan | `app/models.py:372` |
| `mutation_version` | which mutation version produced this projection | `TrainingPlan.mutation_version` — server-authoritative history position, +1 per persisted transition including an undo; **not** a content hash | `app/models.py:374` |
| `canonical_local_date` | which canonical calendar day is represented | `app_today()` / `app_date_of()` — the fixed `Europe/Istanbul` authority (§20) | `app/timeutil.py` |

**Binding requirements:**

- `plan_lineage` **must** come from canonical plan authority (`TrainingPlan`),
  never from a client and never re-derived from plan contents.
- `mutation_version` **must** come from canonical plan/version authority. The
  pairing matters: lineage alone cannot tell two mutation states apart, and
  version alone is meaningless across a regenerated plan (a fresh row gets a
  fresh lineage, so version restarts).
- The date **must** use backend canonical date/timezone semantics. The mobile
  client **must not** independently derive identity from `DateTime.now()` — that
  would reintroduce client clock-based workout selection, which §9 forbids.
- **Do not hash the tuple and treat the digest as a durable entity ID.** A hash
  would look like an id, would be quoted like an id, and would silently become
  one.
- **Do not create a new DB table** solely to persist this identity. Nothing
  about it is persistent; all three components are already stored or derived.
- **Do not mint a fake public workout UUID.** The mobile fixtures' invented
  `workout_example_001` is precisely the fabrication this decision exists to
  prevent.

**Explicitly supersedable.** If a canonical *persisted* `WorkoutSession`
identity later becomes authoritative — `WorkoutSession.public_id`
(`app/models.py:1150`) already exists behind `FITX_WORKOUT_SESSIONS_ENABLED` —
this context key can and should be
superseded by it. That is a normal contract evolution, not a migration of a
public identifier, precisely because nothing durable ever referenced the tuple.

**Routing consequence, unchanged:** `/workout/detail` and `/workout/session`
stay non-deep-link-eligible. The typed context is passed in-memory exactly as
the Pump Check routes already do for private records, so the navigation
registry's invariants hold and no new identity authority is created.

---

## 19. Completed-day behaviour

| Action | Completion signal | Today's response |
|---|---|---|
| Workout | `completed_today` (today's `PumpCheck`) | training section → completed; primary CTA drops out of ranks 4–6; a completion summary becomes secondary. **Never a stale "Start".** |
| Meal logging | first entry in `diary/today` | "Log your first meal" (ranks 7–8) retires; the diary card stays as informational context |
| Check-in | a `WeeklyCheckIn` row with `yogunluk` not null | not ranked in Sprint 12 (no due model) |
| Comparison | comparison record exists | not ranked in Sprint 12 |
| Plan proposal | status leaves `PENDING` | rank 2 retires |

When every rank is exhausted, `primary` is `null` and Today renders a completed
day — the existing `today_presenter` `workout_done` state already establishes
that "no dominant CTA" is a legitimate, deliberate outcome.

---

## 20. Date / timezone authority

**A single, fixed, server-owned authority already exists and is used
consistently.** `app/timeutil.py`: `APP_TZ = ZoneInfo("Europe/Istanbul")`,
`app_today()`, `app_date_of()`, `utc_day_bounds()`, `to_app_tz()`.

The rules are precise and already load-bearing:

- `created_at` columns are **naive UTC** (`datetime.utcnow()`). `app_date_of`
  assumes UTC for naive input; `day_key` assumes `APP_TZ`. This asymmetry is
  deliberate and documented — a UTC-day comparison shifts records written late
  in the Istanbul evening by a day.
- `utc_day_bounds` is the only correct way to window an Istanbul day against
  those columns, and `workout_state/queries.py` uses it.
- The mobile nutrition contract **publishes the zone that resolved the day**:
  `{"date": "2026-08-23", "timezone": "Europe/Istanbul"}` (proved by
  `test_mobile_nutrition_publishes_the_zone_that_resolved_the_day`).
- No Today-signal module uses `date.today()` or `datetime.now()` (proved across
  four modules by
  `test_today_signals_take_their_day_from_the_istanbul_authority`).

**Consequences for Sprint 12:**

1. The Today read model must publish `day: {date, timezone}` exactly as
   nutrition does. Mobile already parses `timezone` into `Today.timezone` — and
   then ignores it (M9).
2. **Mobile must never compute "today" locally.** A user in another timezone has
   an Istanbul day, and the client must display the server's day.
3. **Midnight rollover is currently broken on mobile (M10).** `TodayController`
   loads once per screen mount; `StatefulShellRoute.indexedStack` keeps the
   branch alive; app resume explicitly does not refresh. An app left open across
   03:00 Istanbul shows yesterday until the process restarts. The fix is a
   day-key comparison on resume/foreground: if the cached `day.date` differs
   from the freshly-fetched one, replace rather than merge.
4. A single fixed zone is a real product limitation for non-Turkish users, but
   changing it is a whole-application migration. **Sprint 12 inherits
   `Europe/Istanbul` and publishes it honestly.** It does not fix it.

---

## 21. Freshness and caching

| Signal | Acceptable freshness | Invalidation trigger |
|---|---|---|
| Workout completion | near-immediate | `POST /workout/complete` succeeds; a Pump Check is created |
| Plan mutation applied / proposal resolved | immediate | confirmation resolves |
| Today's planned workout | immediate (changes only on mutation) | plan mutation, regenerate |
| Nutrition diary | near-immediate | any `POST/PATCH/DELETE /api/v1/nutrition/logs` |
| Onboarding completeness | on login / on resume | profile setup completes |
| Progress summary / Axis Insights | minutes; tolerates staleness | day change |
| Pump Check history | event-driven | Pump Check created |

**Current mobile caching: none.** No HTTP cache (`/api/v1` sends
`Cache-Control: no-store` blanket), no disk cache, no TTL, no stale rendering.
The nutrition controller does establish the right *pattern*: a serialized read
tail, a generation counter that fences in-flight reads across auth transitions,
and a `reconcile()` boundary that write controllers call after a mutation.

**Design for Sprint 12:** extend that exact pattern to Today. An explicit
`invalidate()` called on — nutrition mutation committed, Pump Check created,
workout completed, plan confirmation resolved, app resumed with a changed day
key. **No polling.** No background timers. Today is refreshed by events the app
already knows about.

---

## 22. API architecture — recommendation

**Recommendation: (C) hybrid, weighted heavily toward one bounded backend read
model.**

Concretely: **one new `GET /api/v1/today`** that composes the *decision* —
training state, today's plan, nutrition summary, onboarding, and the priority
ranking — plus the **existing** `/api/v1/nutrition/*` and `/api/v1/pump-checks/*`
endpoints for detail and mutation, and later-hydrating optional sections
(Progress) as separate calls.

| Criterion | (A) client-composed | (B) one Today endpoint | Verdict |
|---|---|---|---|
| Latency | ≥4 sequential round-trips on mobile networks | 1 | B |
| Failure isolation | naturally per-call | needs explicit per-section states | A, unless B designs for it |
| Request count | 4–6 | 1 | B |
| Duplicated business logic | **client re-implements the priority policy** | server-owned | **B decisively** |
| Versioning | 4 contracts to evolve | 1, and the repo already versions contracts additively | B |
| Mobile simplicity | 4 controllers to orchestrate | 1 | B |
| Cache invalidation | 4 independent invalidations | 1, plus targeted section refresh | B |
| Authorization | 4 owner checks | 1 owner check, one payload | B |
| Future notifications | impossible without a client | **the same read model drives them** | B |

The brief warns against creating `/api/today` merely because an aggregate sounds
convenient. That is not the argument here. The argument is **the priority policy
must be server-owned** (§11); once that is true, an endpoint that returns the
decision without also returning the state it was derived from would force the
client to make a second round of calls just to render it.

**Precedent, not invention:** `/training/bootstrap` already does exactly this
shape for Training — one coherent read snapshot over `get_active_plan` +
`resolve_workout_state` + `serialize_today_plan`, `Cache-Control: private,
no-store`.

**One thing to do differently from `/training/bootstrap`.** It fails **closed as
a whole** — any exception returns a single 500 `bootstrap_unavailable`. That is
right for a Training page and wrong for Today, where §24 requires a partial
outage to degrade one section, not blank the screen. The Today read model must
wrap **each section** in its own boundary and emit `unavailable` for that section
only. The day, and the training section, are the two things whose failure may
legitimately fail the request.

**Explicitly rejected:** returning `headline`/`reason` prose (M3). The endpoint
returns a state code plus typed parameters; the client owns copy and
localization. This is the rule `today_presenter` already enforces on web
(`label_key`, never a translated string).

---

## 23. Performance budget

**Measured current behaviour:**

| Surface | Requests on load | Notes |
|---|---|---|
| Mobile Today | **1** | `GET /api/v1/nutrition/diary/today`. Today itself is a bundled asset read — 0 network, and 0 truth. |
| Web legacy home (`index.html`) | ~6 + 1 CDN | `/checkin-history`, `/last-session`, `/meal-log/today`, `/water`, `/workout/status`, `/leaderboard/reward-check`, + jsdelivr Chart.js |
| Web Today V2 (`today.html`) | ~5 | drops `/workout/status` (server-rendered); the rest remain |

No N+1 was found in the Today path: `resolve_workout_state` performs a bounded
set of reads, `serialize_today_plan` is pure over already-loaded JSON, and
`/training/bootstrap` wraps its reads in one transaction. Pump Check images are
not fetched by any Today surface.

**Sprint 12 target (evidence-based, not arbitrary):**

- Shell + navigation render immediately from restored navigation state (already
  true).
- **The decision surface resolves in exactly one request** — `GET /api/v1/today`.
  That is 1 request replacing what would otherwise be 4–5 for the same
  information, and it is one more than today's Today only because today's Today
  makes zero truthful requests.
- Optional sections (Progress, Pump Check follow-up) hydrate independently,
  **after** first paint, and never block the primary CTA.
- Serialized `GET /api/v1/today` payload: **≤ 16 KB** — bounded by
  `MAX_TODAY_EXERCISES` and the existing per-field bounds in
  `workout_state/serialization.py` (`isim` ≤ 120, `not` ≤ 240, etc.), plus the
  diary summary. No image URLs.
- No polling; refresh is event-driven (§21).

---

## 24. Offline / degraded behaviour

| Condition | Required behaviour |
|---|---|
| No network, no cache | Honest retryable error for the whole surface. Never a fabricated plan. |
| No network, cache present | Render cached Today **marked stale, with the cached day's date visible**. If the cached `day.date` ≠ the device's plausible today, do not render the decision at all — show the day mismatch. Acting on a stale "start today's workout" is misleading. |
| Training section fails | Training section `unavailable`; nutrition, onboarding still render; **no primary CTA** is claimed (a ranking derived from a failed read is not a ranking) |
| Nutrition fails | Nutrition section `unavailable`; training decision unaffected — this already works today |
| Progress fails | Section hidden or `unavailable`; never blocks Today |
| Coach unavailable | Contextual handoff CTA hidden; Today unaffected (no dependency by construction, §17) |

The governing rule is already in the codebase and should be quoted in the PR3
brief: `today_facts` treats a resolver `resolution_error` as `read_ok=False`
specifically so the presenter renders an honest error "rather than a fabricated
'not completed'". Sprint 12 extends that discipline per section.

---

## 25. Onboarding / no-data state

| User | Current mobile behaviour | Required behaviour |
|---|---|---|
| Brand-new, `profile_complete == false` | Fixture "Upper Body Strength" card | One step: complete setup. Honest handoff — mobile cannot do it yet (§14) |
| Profile complete, no plan | Fixture card | `no_plan`; one step: create a plan |
| No nutrition data | Diary card renders empty correctly ✅ | keep — an empty day is a measurement, not a gap |
| No Pump Checks | Not surfaced on Today | keep out of Today |
| No check-ins | Not surfaced on Today | keep out of Today (no due model) |
| Partial onboarding | Indistinguishable from complete | Rank 0 in the priority policy |

**No fabricated stats to fill the screen.** The nutrition contract already sets
the precedent: a null macro stays null, an unset goal is `null` not `0`. Today
inherits that rule.

---

## 26. Accessibility requirements (acceptance criteria for mobile PRs)

Existing baseline is genuinely good and must not regress:
`test/app/responsive_accessibility_test.dart` already exercises every
destination at 320/390/768 px × 1.0/2.0 text scale, and
`navigation_semantics_test.dart` covers destination selection and labels.
`TodayContent` already wraps itself in a `Semantics` container labelled
`Today: <headline>`.

Required for the Today PRs:

1. Screen-reader order: day → state → primary CTA → supporting facts →
   secondary sections. The CTA must be reachable before the diary card.
2. Semantic headings for each section; one `header: true` for the Today title.
3. CTA labels must be self-describing out of context ("Start today's workout",
   not "Start"). `_Fact` currently labels "6 exercises" — fine; the CTA must be
   equally explicit.
4. Live region on the primary decision when it changes after an invalidation, so
   a completed workout is announced rather than silently swapped.
5. Focus management after navigation and after retry — mirror the web pattern
   (success → heading, error → retry control).
6. 320 px × 2× text with no overflow across **all** Today states — including the
   loading/empty/error branches, which currently use a different composition
   from the populated branch (M11).
7. No information conveyed by colour alone. `_statusColor` currently encodes
   rest/completed/active/other purely as an accent bar colour — it needs a text
   or icon companion.
8. Reduced-motion honoured for any state-change animation.
9. Stale state must be conveyed textually, not only visually (§24).

---

## 27. Visual quality principles (for later mobile PRs — no redesign in PR1)

1. **One dominant daily decision.** `TodayContent` already achieves this; do not
   dilute it into a card grid.
2. **Rank by weight, not by order.** Primary CTA full-width and prominent;
   secondary actions text-weight; context (diary card) visually subordinate.
3. **Facts support the decision; they do not compete with it.** Duration and
   exercise count are supporting evidence, not metrics to admire.
4. **Remove chrome that carries no information.** The accent bar earns its place
   only if it encodes state redundantly with text (§26.7).
5. **A completed day should look finished, not empty.** Different visual
   treatment from `no_plan`.
6. **Missing data renders as missing.** No skeleton that resolves into a
   fabricated value, no zero-filled rings.
7. **One vertical rhythm.** All Today states must share one layout skeleton so
   loading→populated does not reflow the page (fixes M11).
8. Long headlines wrap to at most two lines and truncate with an accessible full
   label; test at 2× scale and 320 px.

---

## 28. Analytics / instrumentation

**Existing architecture:** web has Google Analytics (`static/analytics.js`,
`gtag` from `_head.html`); backend has CloudWatch operational SLIs
(`app/services/runtime_metrics.py` — `increment`, `record_latency`,
`set_gauge`); **mobile has no analytics of any kind** (verified: no vendor
dependency in `pubspec.yaml`, no telemetry module in `lib/`).

**Recommendation: introduce no vendor, in PR1 or in Sprint 12.** Instrument the
Today decision **server-side**, where the decision is made, using the existing
PII-free structured-log + `runtime_metrics.increment` path already used by
`[PROGRESS][SUMMARY]`, `[TRAINING][WEEKLY_PROGRAM]` and `[WORKOUT_STATE]`.

Minimum event set to evaluate whether Daily Coach changes behaviour:

| Event | Emitted by | Dimensions (PII-free) |
|---|---|---|
| Today served | `GET /api/v1/today` | `primary_action_code`, `training_state`, `section_unavailable_count` |
| Primary CTA offered | same request | `primary_action_code` (or `none`) |
| Primary CTA followed | the destination endpoint | correlate via `request_id`, not a user id |
| Workout completed | `POST /workout/complete` | already logged |
| First meal of day logged | `POST /api/v1/nutrition/logs` | `is_first_of_day` |

**Never in a dimension or a log line:** weights, macros, calories, exercise
names, plan content, injuries, Pump Check anything, or a user identifier. This
matches the discipline every existing observability site in the repository
already follows.

---

## 29. Notifications boundary

**No notification implementation in Sprint 12.** Documented for later:

| Today state | Could later drive |
|---|---|
| `scheduled_not_started` late in the day | "Your workout is still waiting" |
| pending plan proposal unresolved | "A plan change needs your confirmation" |
| `execution_recorded`, not completed | "Finish logging today's session" |
| nutrition empty past midday | "Log your first meal" |

This is the strongest architectural argument for §22's server-owned decision: a
notification job needs the same ranking with **no client present**. If the
priority policy lives in Dart, it must be written twice. If it lives in
`GET /api/v1/today`'s underlying service, a scheduler calls the same function.

Design constraint: the Today read model's service layer must be callable
**without a request context** (like `resolve_workout_state` already is), so the
future job reuses it directly.

---

## 30. Security / privacy

| Risk | Assessment |
|---|---|
| Owner scoping | Strong precedent to follow: every mobile service takes `g.mobile_user.id` and nothing else; there is no account parameter to tamper with. Today must do the same. |
| Cross-user leak | Structurally prevented in `plan_confirmation` and `plan_mutation.journal` (owner in every WHERE clause). The Today read model must not accept a `user_id`. |
| Private Pump Check data | Pump Check identifiers are opaque signed tokens and its routes are deliberately not deep-link eligible. **Today must not embed Pump Check images or tokens.** Completion is a boolean; the photo is not Today's business. |
| Coach conversation privacy | Today must not surface conversation content. A handoff carries an intent + ids (§17). |
| Plan mutation internals | `PlanMutationRecord` holds snapshot fingerprints, lineage ids and idempotency keys. **None of these belong on the wire.** Publish a typed, bounded summary of the change — never the journal row. |
| Over-broad payload | The current `/training/bootstrap` returns the **entire 7-day plan**. Today needs today only. Bound the payload to today's day object. |
| Caching private state | `/api/v1` already sets a blanket `Cache-Control: no-store` via `mobile_api.prevent_mobile_response_caching`. Today inherits it. Any client-side cache must be cleared on the auth transition — the nutrition controller's `resetForAuthTransition` generation-fence is the pattern. |
| Logs | Follow the existing pattern exactly: request id + coarse state codes, never content (§28). |

---

## 31. Tests / evidence produced by this PR

`tests/test_sprint12_daily_coach_discovery.py` — **38 tests, all passing.**
These are characterization tests: they pin what *is*, so any later PR that
changes one of these facts must say so.

The module has two halves. **Discovery (24 tests, unchanged)** pins the findings
F1–F9 and P2-16 that this report is built on; the decision closure rewrote none
of them. **Decision closure (9 functions, 14 collected cases, added when
C1/C2/C3 were made)** pins the facts the finalized architecture leans on —
tabulated after the findings table below.

| Finding | Test |
|---|---|
| F1 | `test_mobile_api_publishes_only_auth_nutrition_and_pump_check` (exact set), `test_no_mobile_endpoint_serves_a_daily_coach_domain` (×7 domains) |
| F2 | `test_today_facts_delegates_completion_and_owns_no_query_of_its_own`, `test_today_presenter_is_pure` (AST import analysis) |
| F3 | `test_web_today_states_and_workout_state_vocabulary_do_not_match` |
| F4 | `test_today_plan_projection_publishes_names_not_canonical_ids` |
| F5 | `test_pending_plan_confirmation_is_reachable_only_from_coach_plan_tools`, `test_no_blueprint_reads_the_plan_mutation_journal` |
| P2-16 | `test_injury_annotation_precedes_canonical_exercise_resolution` (source-line ordering), `test_injury_warning_text_persists_into_the_stored_plan` |
| F7 | `test_today_signals_take_their_day_from_the_istanbul_authority` (×4 modules), `test_mobile_nutrition_publishes_the_zone_that_resolved_the_day` |
| F8 | `test_workout_completion_is_gated_behind_the_ai_concurrency_gate` |
| F9 | `test_no_module_publishes_a_recovery_or_readiness_score`, `test_check_in_has_no_due_read_model` |

**Decision-closure guards (added by the C1/C2/C3 closure):**

| Decision | Test | Pins |
|---|---|---|
| C1 | `test_mobile_api_registration_remains_feature_gated` | `MOBILE_AUTH_ENABLED` default is still `False` **and** both the mobile blueprint import and its registration are still inside the gate. The rollout is unblocked; nothing is enabled. |
| C1 | `test_mobile_auth_registry_lifecycle_is_still_the_stale_blocked_record` | The registry still says `LIFECYCLE_BLOCKED` on "PR4 (capacity hardening) merged", **and** `mobile_auth.py` really does import `blocking_concurrency_slot` from `ai_gate` — i.e. the prerequisite is satisfied while the record is stale. The follow-up PR (§41) cannot correct the registry without updating this test out loud. |
| C2 | `test_workout_context_identity_components_are_canonical_plan_columns` | `TrainingPlan.lineage_id` and `TrainingPlan.mutation_version` remain the canonical sources for two thirds of the context tuple. |
| C2 | `test_no_durable_workout_identity_is_minted_on_the_training_plan` | No `workout_id` / `workout_public_id` / `context_key`-style column has appeared — the context key has not become an entity id. |
| C3 | `test_core_today_composition_does_not_depend_on_adaptive_coaching` (×6 modules) | No Today authority — `today_facts`, `today_presenter`, or any `workout_state` module — references an AC flag, `plan_confirmation`, `plan_mutation` or `get_pending`. Core Today is independent by construction, not by luck. |
| C3 | `test_adaptive_coaching_flags_are_still_default_off` | PR1 changed neither AC flag. |
| §34 | `test_no_second_daily_state_authority_has_been_created` | No `daily_coach*` module exists in `app/services/` yet. PR3 may add orchestration; it may not add a second authority. |
| P0 | `test_report_keeps_the_fixture_p0_first_and_undowngraded` | The report still carries `READY FOR IMPLEMENTATION`, the "highest-priority Sprint 12 implementation defect" wording, the PR2A next-PR line, and the fallback hierarchy's forbidden fifth option. |
| §7 | `test_report_does_not_claim_to_know_the_deployed_flag_value` | The two pre-decision overstatements are gone, the precise gating sentence is present, and `.env` really is uncommitted — so the hedge is honest rather than decorative. |

The last two read the report itself. That is deliberate: the report *is* this
PR's deliverable, and the P0's priority plus the deployment-state wording are
the two claims a later editor is most likely to soften without noticing.

No snapshot tests were added. No mobile test was added — the mobile repository
was kept read-only (§2); the equivalent Dart architecture test
(`FixtureTodayRepository` must not appear in `AppComposition.configured`) is
specified as an acceptance criterion for **PR2A** instead.

**Baseline:** `tests/test_mobile_auth_feature_gate.py` — 11 passed, before any
change.

---

## 32. Required state matrix

`P` = primary CTA · AI = does this state require an LLM (all: **no**).

| # | Scenario | Primary message | Primary CTA | Secondary | Canonical source | Degraded behaviour |
|---|---|---|---|---|---|---|
| 1 | New user, onboarding incomplete | Finish setting up | Complete setup | — | `account/me.profile_complete` | if unknown → treat as complete, rank on other signals |
| 2 | Profile complete, no plan | You don't have a plan yet | Create plan | View Coach | `today_facts.get_active_plan` | section `unavailable` → no CTA |
| 3 | Active plan, workout today, not started | Today: `<focus>` | Start today's workout | View plan | `primary_state=scheduled_not_started` | training `unavailable` → no CTA claimed |
| 4 | Active plan, workout completed | Today's workout is done | *none* | View progress · View plan | `completed_today` | — |
| 5 | Rest day | Recovery day | *none* (or Log a meal if nutrition empty) | View plan | `is_rest_day` | — |
| 6 | Execution recorded, not completed | You logged training today | Finish today's workout | View plan | `execution_state=execution_recorded` | — |
| 7 | Session active/resumable *(flag on)* | Workout in progress | Resume workout | Abandon (existing authority) | `session_state=active_resumable` | flag off → state impossible |
| 8 | Pending Adaptive Coaching proposal *(PR5, conditional)* | A plan change is waiting | Review plan change | Ask Axis why | `plan_confirmation.get_pending` | read fails → hide section, do not guess; **never fabricated** (C3) |
| 9 | Plan change recently applied *(PR5, conditional)* | Your plan changed | *inherits rank 3–6* | See what changed | `PlanMutationRecord` | no canonical journal evidence → say nothing (C3) |
| 10 | Nutrition empty | *inherits training message* | ranks 7–8 only if training is settled | Log a meal | `diary/today.meals == []` | nutrition `unavailable` → hide card |
| 11 | Nutrition partially logged | — | — | diary card shows totals vs goal | same | — |
| 12 | Check-in due | **not represented** — no due model | — | — | — | F9 |
| 13 | Pump Check follow-up available | **not represented** — AI prose only | — | — | — | F12 |
| 14 | Meaningful Progress update | — | never primary | View progress | `/api/progress/summary` | hydrates late; failure hides section |
| 15 | One subsystem unavailable | remaining sections render | from available signals only | — | per-section state | §24 |
| 16 | Offline with cache | Cached, marked stale + cached date | *suppressed if day mismatch* | Retry | client | §24 |
| 17 | Multiple simultaneous actions | highest rank wins | §11 ranking | the rest become secondary | server policy | §33 |
| 18 | Everything done | You're done for today | *none* | View progress | all | — |

**Rows 12 and 13 are deliberately empty.** The brief asked which questions
AxisAI can answer today; fabricating a due date would be exactly the failure
mode §38 forbids.

---

## 33. Priority conflict matrix

| Class | States | Behaviour |
|---|---|---|
| **Hard blocker** | onboarding incomplete (rank 0); `action == blocked` / anomaly (rank 1) | Nothing else can be primary. Other sections still render as context. |
| **High priority** | pending plan confirmation (2); no plan (3); resumable session (4); scheduled workout not started (5); execution recorded (6) | Exactly one becomes primary, by rank. |
| **Secondary** | nutrition empty (7–8) when training is settled; rest-day plan view (9) | Rendered as a secondary action, never promoted over a high-priority item. |
| **Informational only** | Progress update; diary totals; completion summary; streak | Never a primary CTA. Never competes. |

**Worked example** — the brief's four-way conflict (workout scheduled + nutrition
empty + check-in due + plan confirmation pending):

> Primary = **Review plan change** (rank 2). The plan change is a *precondition*
> for the workout: confirming it may change what today's workout is, so telling
> the user to start a workout that is about to be rewritten is actively wrong.
> Secondary = Start today's workout, Log a meal. Check-in is **not surfaced** —
> no due model exists.

**Guard: UI order must never become business logic.** The rank is a server-owned
field on the response; the client renders `primary` and iterates `secondary` in
the order given. The mobile PR's acceptance criteria must include a test that
the client performs no ranking of its own — mirroring the existing
`test/architecture/*_boundaries_test.dart` pattern.

---

## 34. Proposed target architecture

```
                    ┌──────────────── canonical domain authorities ────────────────┐
                    │  workout_state    today_facts      mobile_nutrition          │
                    │  plan_mutation    plan_confirmation                          │
                    │  progress_summary progress_insights  pump_checks             │
                    └──────────────────────────┬──────────────────────────────────┘
                                               │  reads only — never replaced
                    ┌──────────────────────────▼──────────────────────────────────┐
                    │  app/services/daily_coach/           (thin orchestration)    │
                    │    read_model.py   compose sections in ONE coherent snapshot │
                    │    policy.py       PURE deterministic priority ranking       │
                    │    payload.py      PURE wire projection (codes + params)     │
                    │  No business rules. No AI. No writes. No request context.    │
                    └───────┬───────────────────────────────────────┬─────────────┘
                            │                                       │
              GET /api/v1/today                        (future) notification job
                            │                          calls the same service
                    ┌───────▼───────────────────────────────────────────────────┐
                    │  mobile: TodayRepository → TodayController                │
                    │    primary next-action renderer  (one dominant CTA)       │
                    │    independent secondary sections (own SectionState)      │
                    │    canonical deep links via AppRouteRegistry              │
                    │    contextual Coach handoff (typed intent + ids)          │
                    └───────────────────────────────────────────────────────────┘
```

**Explicitly not built (§35):** there is no `daily_coach.py` god object. The
package holds three small modules — a composition function, a pure policy, and a
pure projection — mirroring the split the repository already uses for
`workout_state` (queries/resolver/serialization) and for `today_facts` +
`today_presenter`.

**Existing authority stays the authority.** `app/services/workout_state` is
already the canonical daily workout-state owner, and `today_facts` +
`today_presenter` already compose canonical Today behaviour for web. Sprint 12
must therefore **not** introduce any of the following, in any PR:

| Forbidden | Because |
|---|---|
| a `daily_coach_state.py` that duplicates the resolver's rules | that is a second daily-state authority — the exact failure §35 exists to prevent |
| a mobile-only workout-state algorithm | the client would own a rule the server already owns, and the two would drift |
| a second "today workout" calculator anywhere | `serialize_today_plan` is the one selection rule |
| client clock-based workout selection | violates the single server-owned `Europe/Istanbul` day (§20) and C2 |
| client rest-day inference | `is_rest_day` is resolver output, not a client derivation |

Any new API layer **orchestrates and projects** existing authority. It does not
re-decide anything the authority already decided.

Rules carried over verbatim from existing code, because they already work:

- The policy module is **pure** and unit-testable without a DB or Flask (like
  `workout_state/resolver.py` and `today_presenter.py`).
- The composition module performs **reads only**, inside
  `coherent_read_snapshot()`.
- The projection emits **state codes and typed parameters**, never translated
  copy (like `today_presenter`'s `label_key`).
- The contract is **additively versioned** with a `contract_version` integer
  (like `workout_state` and `progress_summary`).
- Web Today V2 is re-pointed at the same policy so web and mobile stop
  disagreeing — retiring the four-state vocabulary (F3).

---

## 35. Avoiding a mega-service — explicit guards

For the PR3 brief and its review checklist:

1. `app/services/daily_coach/` may **import** domain services; it may not
   contain a domain rule.
2. It performs **no writes**, opens no transaction of its own beyond the read
   snapshot, and makes no AI or HTTP call.
3. It defines **no new state names for training** — it re-exports
   `workout_state`'s vocabulary.
4. It does not query `PumpCheck`, `WorkoutLog`, `MealLog` or `TrainingPlan`
   directly. Every fact arrives from an existing service function.
5. `policy.py` takes a frozen input dataclass and returns a ranked decision —
   no ORM, no Flask, no clock.
6. A structural test enforces 1/4/5, in the style of
   `tests/test_coach_plan_tools_architecture.py`.
7. It is **not conditioned on the Adaptive Coaching flags** (C3). The
   `plan_change` section resolves to `empty` when no proposal source is active;
   the composition does not branch on a flag to decide whether Today works.

---

## 36. Proposed Sprint 12 PR sequence

Derived from the dependency graph, not assumed. Each PR is independently
reviewable and shippable.

**This sequence supersedes the pre-decision proposal.** The earlier draft opened
with `P2-16 fix → GET /api/v1/today`. That ordering was wrong: discovery
identified a more urgent P0 — release mobile composition can use fabricated
fixture repositories and present false workout/plan/progress state to production
users (§9). Truthfulness of the shipped surface outranks both the backend
contract and a Sprint 11 annotation defect, so **fixture elimination moves to
the front**.

| PR | Title | Owner | Depends on | Why here |
|---|---|---|---|---|
| **PR1** | Daily Coach convergence discovery & architecture *(this PR)* | backend | — | Decisions and architecture only. No implementation. |
| **PR2A** | **Mobile production fixture elimination — P0** | **mobile** (`axisai_mobile`) | PR1 | **The first implementation PR.** Ensure release/production composition cannot use fabricated Today/Plan/Progress/Workout fixtures. `AppComposition.configured` wires fixture repositories in production/release paths and `pubspec.yaml` bundles `fixtures/`. Invariant: production may show real, empty, unavailable or degraded state — **never fabricated user-specific fitness state** (§9). Exact scope derives from §9 and §35. |
| **PR2B** | Canonical injury annotation ordering (closes Sprint 11 P2-16) | backend | PR1 | Bounded backend closure, before Today republishes persisted workout exercise notes more broadly. Canonicalize exercise identity **first**, then annotate against canonical identity/name. Warn-only semantics preserved; no medical inference, no new diagnosis or contraindication behaviour, no broadened injury rules (§16). |
| **PR3** | Canonical mobile `GET /api/v1/today` | **backend** | PR2B | Expose mobile-safe Today state by **composing existing canonical authorities** — `app/services/workout_state`, `today_facts`, `today_presenter`. **Do not create a second daily-state authority** (§34, §35). A typed read *projection*, not rebuilt Today business logic. Adds `exercise_id` to today's exercise projection (F4); per-section degradation; server-owned priority policy; **no prose**; **no LLM**. |
| **PR4** | Mobile Today real-data convergence | **mobile** (backend support first) | PR3 | Replace production fixture Today behaviour with the real mobile Today repository/read model: typed Today repository · real backend consumption · loading · truthful empty · partial/degraded · actionable state · canonical CTA navigation · invalidation after relevant actions · typed workout context `(plan_lineage, mutation_version, canonical_local_date)` (§18). **No fake fallback data.** |
| **PR5** | Adaptive Coaching Today integration — **CONDITIONAL** | **both** (backend first) | PR4 | Execute **only** if Adaptive Coaching rollout is explicitly approved and its flags are intended to become active. Possible scope: pending plan proposal visibility · applied plan-change explanation · canonical mutation journal evidence · contextual Coach handoff. If the flags remain OFF, **do not ship PR5 against permanently empty state** — defer it outside Sprint 12 (C3, §13). |

**Critical path:** PR2A → PR2B → PR3 → PR4. PR5 branches off the end and may be
cut entirely without breaking the loop.

**Ordering rationale, stated once:** PR2A is first because backend Today work
cannot be trusted while release UI can still choose fake repositories (§9).
PR2B precedes PR3 because PR3's projection republishes the persisted `not`
warning that P2-16 corrupts (§16). PR3 precedes PR4 because PR4 has nothing
truthful to render until the endpoint exists. PR2A does **not** depend on PR2B
or PR3 — it can start immediately, and it is independent of every rollout flag.

**Mobile auth is a rollout dependency, not a sequencing dependency.** PR3's
endpoint is only *operationally* useful once `MOBILE_AUTH_ENABLED` is on for the
host (§7, C1). That gates when PR3 and PR4 can be verified end-to-end against a
deployed environment; it does not gate when they can be written, reviewed, or
tested. The rollout order stays `MOBILE_AUTH_ENABLED` → verify backend →
`AXISAI_NATIVE_AUTH_ENABLED` in the Flutter build.

### Later Sprint 12 closure — deliberately unnumbered

Once the core data flow is real, the remaining work is:

- Today UX hierarchy / retention hardening (§27, M11)
- contextual Coach handoff + a real mobile Coach client (§17)
- performance / freshness polish (§21, §23)
- accessibility (§26)
- analytics / instrumentation, **if justified** (§28)

**No PR number is assigned to these yet.** Their dependency graph is not settled
— the Coach client in particular is the largest unknown in either repository —
and numbering them now would imply an ordering this report cannot yet justify.

**Web convergence** (re-pointing Today V2 at the shared policy, §34) folds into
PR3 as a follow-on commit — it is the same service and the same flag.

---

## 37. Explicit non-goals for PR1 — all honoured

No implementation of: a Today redesign, an AI recommendation engine, a readiness
or recovery score, a training generator, an exercise catalog, a Pump Check
model, a Progress algorithm, notifications, background jobs, workout mutations,
nutrition intelligence, social/feed expansion, auto baseline selection,
autonomous plan changes, App Store/TestFlight hardening, or an analytics vendor.

The only files added are this report and one characterization test module.

---

## 38. Professional review gate — findings

A fresh architecture review of this report's own proposals, against the
brief's checklist.

### P0

| id | Finding | Disposition |
|---|---|---|
| **P0-1** | **Mobile ships fabricated Today/Plan/Progress/Workout data in release builds.** `AppComposition.configured` wires all five `Fixture*Repository` instances unconditionally; `pubspec.yaml` bundles `fixtures/` as an asset. A production user is shown a workout they do not have. Violates "a missing truth should render as missing". **This is the highest-priority Sprint 12 implementation defect** (§9). | **PR2A — the first implementation PR.** If `AXISAI_NATIVE_AUTH_ENABLED` is enabled before PR2A lands, this becomes user-visible immediately — so PR2A gates the rollout, not the other way round. Deliberately *not* downgraded and *not* deferred behind the backend contract. |

### P1

| id | Finding | Disposition |
|---|---|---|
| **P1-1** | Feature-flag registry drift: `MOBILE_AUTH_ENABLED` is `LIFECYCLE_BLOCKED` on "PR4 capacity hardening merged", which merged as `34f8dc7` (#200); `mobile_auth.py` now uses the `ai_gate` blocking slot. Stale lifecycle documentation describes a blocker that no longer exists. | **Decision made (C1, §41): the prerequisite has landed and the flag is eligible for controlled staged rollout.** Correcting the registry text itself is a bounded documentation follow-up owned by the rollout PR, not by PR1 (§7, §41). No PR1 test depends on the stale wording. |
| **P1-2** | **Frontend-owned business logic risk.** Mobile's `TodayController._stateFor` currently maps a status enum to a view state — a client rule. If the priority ranking is not server-owned (§11/§22), it will be duplicated in Dart and again in any notification job. | Prevented by design in PR3 + an architecture test in PR4. |
| **P1-3** | **Stale-state hazard on mobile.** Today loads once per app session; `indexedStack` keeps it alive and resume does not refresh. Combined with a fixed Istanbul day boundary, a long-lived app shows the wrong day (M10, §20.3). | PR4: day-key comparison on resume + event-driven invalidation. |
| **P1-4** | **Duplicate authority risk.** Four "primary action" notions (F11) and three "today" vocabularies (F3) already exist. Adding a fifth/fourth would make convergence permanently impossible. | PR3 reuses `workout_state`'s vocabulary and retires the web four-state set; §35 guard 3. |
| **P1-5** | **P2-16's inconsistent injury warning is durable in `plan_data`** and is republished by today's projection. Higher severity than "annotation inconsistency". | PR2B, before PR3. |
| **P1-6** | Canonical exercise identity does not reach today's wire projection (F4) — a Today surface built on `/training/bootstrap` reintroduces name-based identity. | PR3 publishes `exercise_id`. |
| **P1-7** | `/training/bootstrap` fails closed as a whole (single 500). Copying that pattern into Today would turn a partial outage into a blank screen, violating §24. | Explicitly designed against in §22; per-section boundaries. |

### P2

| id | Finding | Disposition |
|---|---|---|
| **P2-1** | Development copy ships to users ("Preparing your local daily direction", "The local development data could not be read"). | PR2A/PR4 |
| **P2-2** | Two "what should you do" engines (`get_nudges` vs Today). Not a defect yet; would be if Today started consuming the nudge strings. | Documented decision (§8): Today ignores it; nudges stay Coach-prompt context. |
| **P2-3** | `Today.timezone` is parsed and never used; the day is rendered without its zone (M9). | PR4 |
| **P2-4** | Layout composition differs between the populated Today branch and the loading/empty/error branches (M11). | Later closure (§36) |
| **P2-5** | Status conveyed by accent-bar colour alone (§26.7). | Later closure (§36) |
| **P2-6** | `/training/bootstrap` returns the entire 7-day plan where today's day would do — over-broad payload for a Today consumer. | PR3 bounds its own payload; the existing endpoint is left alone. |
| **P2-7** | The single fixed `Europe/Istanbul` day boundary is a product limitation for non-Turkish users. | Out of scope; inherited and published honestly (§20.4). |

### Checks that came back clean

- **Circular Coach ↔ domain dependency:** none proposed; explicitly guarded
  (§17). The Coach already reads `workout_state` directly, and the Today read
  model will too — neither calls the other.
- **Unnecessary new endpoints:** exactly one new endpoint is proposed, and §22
  justifies it on ownership rather than convenience.
- **Unnecessary LLM calls:** none. §12 classifies every Today behaviour as
  deterministic or templated.
- **Destructive action exposure:** Today exposes no mutation. Plan changes route
  to the existing confirmation authority (§13).
- **Owner/privacy leaks:** §30. No Pump Check images or tokens, no journal
  internals, no Coach content on Today.
- **Excessive fan-out:** 1 request for the decision (§23).
- **Today becoming a mega-service:** guarded by six explicit rules (§35).

**All P0/P1 items above are report-level *resolved*** — each has a named owner,
a named PR, and a design decision recorded here. None is an unresolved ambiguity
about ownership or state semantics.

### Post-decision consistency review (fresh pass)

A second read-only architecture review was run over the finalized report,
hunting specifically for contradictions introduced by C1/C2/C3 and the
resequence.

| Checked for a contradiction in | Result |
|---|---|
| Mobile fixture P0 priority | **Consistent.** P0 in §1, §9, §36 (PR2A first), §38, §44. Nowhere deferred behind PR2B or PR3, nowhere softened below P0. |
| `MOBILE_AUTH_ENABLED` | **Consistent.** Eligible for staged rollout (C1), default stays OFF, not activated by PR1, ordered before `AXISAI_NATIVE_AUTH_ENABLED` in §7, §36 and §41. No section claims the flag is on, or that the repository can prove the host value. |
| Backend vs mobile ownership | **Consistent.** Priority policy backend-owned (§11, §22, P1-2); PR2A and PR4 mobile-owned; PR2B and PR3 backend-owned. No section gives the client a ranking rule. |
| Workout identity | **Consistent.** §10, §18 and §36/PR4 name the same tuple; no section mints an id, hashes the tuple, or adds a table. §18 records the supersession path. |
| Today authority | **Consistent.** §7, §22, §34, §35 and §36/PR3 all say *compose* `workout_state` + `today_facts` + `today_presenter`. No `daily_coach_state.py`, no mobile-only algorithm, no second calculator, no client clock or client rest-day inference. |
| Adaptive Coaching flags | **Consistent.** Optional everywhere: §10 `plan_change` optional, §11 rank 2 data-driven, §13 C3, §32 rows 8–9 conditional, §35 guard 7, §36 PR5 conditional. Core Today never depends on them. |
| P2-16 | **Consistent.** One disposition (§16), one PR (PR2B), one ordering claim, warn-only semantics preserved. |
| PR numbering | **Consistent.** PR1 · PR2A · PR2B · PR3 · PR4 · PR5. The old PR6/PR7 retired into unnumbered closure work (§36). No stale `PR2a` or bare-`PR2` reference survives. |
| API sequencing | **Consistent.** Exactly one new endpoint, in PR3, after PR2B and before PR4. Rollout dependency is separated from sequencing dependency (§36). |
| Release semantics | **Consistent.** The fallback hierarchy in §9 is the one §24, §25 and §32 describe; "fabricated" is never an acceptable production state anywhere in the report. |

**New findings from this pass: P0 — 0 · P1 — 0 · P2 — 0.** No new architectural
ambiguity appeared, so the verdict in §41 is not being forced green over an open
question.

---

## 39. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `MOBILE_AUTH_ENABLED` stays off on the host, so PR3/PR4 cannot be verified end-to-end against a deployed environment | Medium | PR3/PR4 verifiable only in staging/local until rollout | **C1 resolved** the blocker; rollout is now an ops decision with a known order (§7). PR2A and PR2B are unaffected — neither touches the flag. |
| P0-1 becomes user-visible if native auth is enabled before PR2A | Medium | Users shown fabricated training data | Sequence **PR2A first** and treat it as a release gate for any mobile rollout (§9, §36) |
| Adaptive Coaching flags stay off, making PR5 permanently empty | High | PR5 would ship dead code | **C3**: PR5 is conditional and last; if the flags stay off it is deferred out of the sprint, and PR2A–PR4 need no change |
| The registry's stale `MOBILE_AUTH_ENABLED` lifecycle is read as still-blocking after C1 | Medium | A rollout is delayed by documentation, or run without updated abort criteria | Recorded as an explicit bounded follow-up owned by the rollout PR (§41), and pinned by a characterization test so it cannot drift silently |
| `FITX_WORKOUT_SESSIONS_ENABLED` stays off, so `resume`/`in_progress` never occur | High | Rank 4 unreachable | Policy is data-driven; the rank simply never fires. No dead UI shipped. |
| The Today endpoint accretes domain logic over PRs | Medium | The mega-service §35 forbids | Structural test in PR3 |
| Mobile Create Plan pulled into scope mid-sprint | Medium | PR3/PR4 slip | §14: explicitly Sprint 13 |
| Workout identity pressure to mint a public id | Medium | A new identity authority nobody owns | **C2 settled it**: §18's typed context tuple, with an explicit supersession path if a persisted `WorkoutSession` identity later becomes authoritative |
| Backend/mobile contract drift across repos | Medium | Silent breakage | Additive `contract_version`; mobile parser rejects unknown required fields, tolerates unknown optional ones (the pattern nutrition already uses) |

---

## 40. Required final questions — answered

| # | Question | Answer |
|---|---|---|
| 1 | What does AxisAI currently consider "today"? | The calendar date in `Europe/Istanbul`, from `app_today()`. Server-owned, hardcoded, consistent across workout state, nutrition, streaks and check-ins. Naive-UTC `created_at` columns are windowed with `utc_day_bounds`. |
| 2 | Can it reliably identify today's workout? | **Yes.** `serialize_today_plan(plan_data, app_today())` selects today's weekday row from the newest `TrainingPlan`. It publishes names, not canonical ids (F4). |
| 3 | Can it know whether that workout is complete? | **Yes.** `workout_state.completed_today` — today's `PumpCheck` — the same signal `/workout/status` returns. It also distinguishes *execution evidence* from *confirmed completion*. |
| 4 | Can it know whether a plan change is pending? | **Yes in the database, no in practice.** `plan_confirmation.get_pending` exists but has exactly one consumer, inside a Coach turn. No read path (F5). And at the repository default for `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, no proposals are created at all — which is why C3 makes this optional capability state rather than a Today prerequisite. |
| 5 | Can it explain an applied plan change from canonical evidence? | **Yes in the database, no in practice.** `PlanMutationRecord` holds typed commands, lineage, actor and outcome. No blueprint reads it (F5). |
| 6 | Can it determine nutrition status today? | **Yes**, and it is the best contract in the codebase: `GET /api/v1/nutrition/diary/today` — entries, server-authoritative totals, goal-or-null, explicit day + timezone. Already live on mobile. |
| 7 | Is there a canonical recovery/readiness state? | **No.** Nothing publishes one (F9). The nearest signals are a nudge heuristic over the last check-in and the planner's `deload` week focus. Do not invent one. |
| 8 | Can it determine whether check-in is due? | **No.** No cadence, no due date, no flag. Only rows and history (F9). |
| 9 | Can it determine whether a Pump Check follow-up is useful? | **No deterministically.** `next_check_guidance` is AI-generated free text on a past analysis (F12). |
| 10 | What Progress state is actionable today? | The canonical summary (`trajectory`, `body`, `performance`, `consistency`) and Axis Insights (`working` / `watch` / `next_move`). All deterministic, all web-only. **None is a daily action** — `next_move` is a weekly training emphasis and must never become a Today CTA. |
| 11 | What should be the highest-priority CTA? | By rank: incomplete onboarding → blocked/anomaly → pending plan change → no plan → resumable session → start today's workout → finish recorded workout → log first meal → informational. §11. |
| 12 | Should priority be backend- or mobile-owned? | **Backend.** All inputs are server-owned; a client rule would be duplicated in Dart and again in any notification job (P1-2, §29). |
| 13 | Does Sprint 12 need a new aggregate endpoint? | **Yes — exactly one**, `GET /api/v1/today`. Justified by ownership of the priority policy, not convenience (§22), and precedented by `/training/bootstrap`. |
| 14 | Which existing endpoints can be reused? | On mobile: `/api/v1/nutrition/*` (all), `/api/v1/pump-checks*`, `/api/v1/pump-check-comparisons*`, `/api/v1/account/me`. Server-side, the Today service reuses `resolve_workout_state`, `today_facts.get_active_plan`, `serialize_today_plan`, `mobile_nutrition.build_diary_day`, `progress_summary`, `progress_insights` — **as functions, not as HTTP calls**. |
| 15 | What is the minimum request count for Today? | **1** for the decision surface (`GET /api/v1/today`). Optional sections hydrate separately after first paint. Today's mobile Today makes 1 request — for nutrition only — and reads its decision from a bundled fixture. |
| 16 | Which data may be cached? | Progress summary / Axis Insights (minutes). The training decision and nutrition may be cached only for offline display, must be marked stale, and must be suppressed on a day-key mismatch (§24). |
| 17 | Which actions require immediate invalidation? | Workout completion, Pump Check creation, any nutrition log mutation, plan-confirmation resolution, plan mutation applied, app resume with a changed day key. |
| 18 | Does any Today behaviour genuinely require an LLM? | **No.** Every proposed Today behaviour is deterministic or a templated explanation (§12). The AI-assisted features Today links to (Pump Check analysis, check-in feedback, Coach) already exist and stay where they are. |
| 19 | Is Mobile Create Plan part of Sprint 12? | **No.** It carries mobile profile setup, the premium gate, and a two-step generate/save round-trip with a signed exercise-context token (§14). Sprint 12 renders `no_plan` honestly; getting a first plan onto a phone is Sprint 13. |
| 20 | Where should P2-16 be fixed? | Backend, in `training_generation` — canonicalize exercise identity first, then annotate against the resolved catalog entry. As **PR2B**, after the P0 fixture elimination and before the Today read model republishes exercise notes (§16). |
| 21 | What exact PR should implementation begin with? | **PR2A — Mobile Production Fixture Elimination.** The P0 outranks both the Sprint 11 annotation defect and the backend contract: backend Today work cannot be trusted while release UI can still choose fake repositories (§9). Then **PR2B**, then **PR3** (`GET /api/v1/today`), then **PR4**. |

---

## 41. Owner decisions and final verdict

### The three conditions, now resolved

This report previously stood at `READY WITH CONDITIONS` for exactly three
reasons, all of them decisions rather than open discovery. All three have been
made.

**C1 — Mobile auth is no longer conceptually blocked.**
The blocker recorded in the rollout registry and runbook was the pre-auth
blocking-Cognito / thread-exhaustion risk: `/api/v1/auth/login` and `/refresh`
make blocking Cognito calls with no concurrency gate and no thread-reserve
accounting, reachable pre-auth. **Hardening PR4 merged as
`34f8dc79be746f233974461bf0465e4bf32eb72d` (#200)** and added the shared
blocking-concurrency and thread-reserve/capacity protections that prerequisite
named. Sprint 12 architecture therefore treats `MOBILE_AUTH_ENABLED` as
**eligible for controlled staged rollout**, not permanently blocked.

What this decision does **not** do, in this PR or any PR1 change:

- it does **not** change the `MOBILE_AUTH_ENABLED` default to ON;
- it does **not** activate the flag anywhere;
- it does **not** modify a production `.env`;
- it does **not** enable native auth;
- it does **not** perform any rollout work.

The backend rollout dependency is unchanged and non-negotiable:
**`MOBILE_AUTH_ENABLED` first**, then — only after backend verification —
`AXISAI_NATIVE_AUTH_ENABLED` in the Flutter build. That order is not reversible:
the Dart flag is a compile-time constant with no server-side rollback (§7).

**C2 — Today's workout is identified by a typed daily context, not a new id.**
Accepted as specified in §18:
`(plan_lineage, mutation_version, canonical_local_date)`, sourced from
`TrainingPlan.lineage_id`, `TrainingPlan.mutation_version` and the canonical
`Europe/Istanbul` day authority. It is an in-memory / API **context identity**,
not a permanent or public Workout ID: it is not hashed into a pseudo-id, gets no
DB table of its own, and no fake public workout UUID is minted. The mobile
client must not derive it from `DateTime.now()`. If a canonical persisted
`WorkoutSession` identity later becomes authoritative, this context key is
**explicitly supersedable** by it.

**C3 — Adaptive Coaching rollout is not a Sprint 12 core dependency.**
Core Today must work correctly with `AI_ADAPTIVE_PLAN_CONTEXT` and
`AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` both OFF, and neither is activated to
populate Today. Pending-proposal state is optional capability state; "why did my
plan change?" is never fabricated when canonical evidence is unavailable; and no
permanently empty Adaptive Coaching UI ships against a rollout that may never
happen. If the rollout is approved later, it lands as the dedicated conditional
**PR5** after its own prerequisites are proven (§13).

### Follow-ups owned by a later PR — not by PR1

**Mobile auth lifecycle documentation.** The feature-flag inventory and runbook
still describe `MOBILE_AUTH_ENABLED` as `LIFECYCLE_BLOCKED` pending "PR4
(capacity hardening) merged". That prerequisite has now landed (C1), so the
lifecycle record is stale. A future implementation/rollout PR should update:

- the **registry lifecycle** state in `app/feature_flags.py`;
- the recorded **prerequisites**;
- the **rollout documentation** / runbook;
- the **success and abort evidence** an operator watches during the rollout.

**PR1 deliberately does not perform that activation or that registry edit.** It
would be feature-flag implementation, not architecture, and no PR1
characterization test depends on the stale wording — the test suite pins the
drift as a *current fact* precisely so the follow-up has to change it out loud.
This report itself has been corrected where the stale wording made it untruthful
(§7, §38 P1-1), which is the only correction PR1 owns.

### Final verdict

# READY FOR IMPLEMENTATION

Meaning, specifically:

- **architecture is settled** — §10 state model, §11 priority policy, §22 API
  ownership, §34–§35 target shape and mega-service guards;
- **authorities are settled** — §7 maps every Today signal to exactly one
  owner, and §34 holds the line: `workout_state` stays the canonical daily
  workout-state authority, `today_facts` + `today_presenter` stay the canonical
  Today composition, and Sprint 12 introduces no second daily-state authority,
  no mobile-only workout-state algorithm, no second "today workout" calculator,
  no client clock-based workout selection and no client rest-day inference;
- **the unsupported stays unsupported** — no canonical readiness score and no
  check-in-due model exist (F9), and neither is invented to fill a Today card;
  rows 12–13 of §32 stay deliberately empty;
- **priority is deterministic product logic, not LLM reasoning** — §11 and §12;
  no Today behaviour requires an LLM, and primary-action ownership lives in
  exactly one place, the backend (§22, P1-2), never in a client;
- **rollout dependency is understood** — C1, with its order and its separation
  from sequencing (§36);
- **workout context identity is decided** — C2;
- **Adaptive Coaching dependency is decided** — C3;
- **PR ordering is settled** — §36, P0 first.

The fresh post-decision review (§38) found **zero new P0 and zero new P1**
report-level ambiguities, which is the bar this verdict requires.

**This does not mean production is ready.** It means Sprint 12 implementation
can begin. The P0 in §9 is still live in release builds, `/api/v1` registration
still depends on a host flag this repository cannot read, and nothing in this PR
has been pushed, merged or deployed (§42).

> **Next implementation PR: Sprint 12 PR2A — Mobile Production Fixture
> Elimination.**

---

## 42. Repository state at completion

| | |
|---|---|
| Primary repository | `fitness-coach` |
| Secondary repository inspected | `axisai_mobile` (read-only; **not modified, not branched, not checked out** — the decision closure inspected nothing new) |
| Worktree | `.worktrees/sprint12-pr1-daily-coach-convergence-discovery` |
| Branch | `sprint12-pr1-daily-coach-convergence-discovery` |
| Base SHA (backend) | `7707d750a241171e090a681fc398fb659f5d387d` |
| Base SHA (mobile, read) | `e6aab4d594ecb5a0e24ac606c328d46ea2a3855e` |
| HEAD before the decision closure | `e3611f3b2c4676306a70b5875f891afe5e8e0029` (2 commits) |
| Decision-closure commit | `714154d` — `docs(sprint12): finalize daily coach architecture decisions` (C1/C2/C3, resequence, closure guards) |
| Final HEAD | recorded by the follow-up commit that writes this row — a commit cannot contain its own SHA. Branch total: **4 commits** ahead of `7707d75`. |
| Working tree | clean · no untracked files |
| Discovery + closure tests | `tests/test_sprint12_daily_coach_discovery.py` — **38 passed** (128.5 s cold, 49.5 s on the final re-run after the last report edit). Baseline before the closure was 24 passed; the 14 added cases are the C1/C2/C3 guards in §31. **No discovery test was rewritten or removed.** |
| Related characterization tests | `tests/test_mobile_auth_feature_gate.py` + `tests/test_feature_flag_registry.py` + `tests/test_feature_flags.py` — **141 passed** (150.5 s), run because the closure touched flag/auth claims. No production flag value changed. |
| Whitespace | `git diff --check` — clean |
| Findings | P0: 1 · P1: 7 · P2: 7, all dispositioned (§38). Post-decision review pass: **0 new P0 · 0 new P1 · 0 new P2**. |
| Flags changed | **none.** `MOBILE_AUTH_ENABLED`, `AXISAI_NATIVE_AUTH_ENABLED`, `AI_ADAPTIVE_PLAN_CONTEXT` and `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` are untouched, and `app/feature_flags.py` is unmodified |
| Production config | **untouched.** No `.env` was read, written, or deployed |
| Push status | **not pushed** |
| PR status | **not opened** |
| Merge status | **not merged** |
| Deploy status | **not deployed** |

**Files changed by the decision closure:** this report and
`tests/test_sprint12_daily_coach_discovery.py`. No application code was
modified — the closure is architecture and evidence, not implementation.

---

## 43. Files inspected

**Backend — `fitness-coach` @ `7707d75`**

`app/__init__.py` · `app/config.py` · `app/feature_flags.py` · `app/models.py` ·
`app/nav.py` · `app/plan_presenter.py` · `app/timeutil.py` ·
`app/today_presenter.py` · `app/mobile_auth_middleware.py` ·
`app/observability.py` ·
`app/blueprints/{mobile_api,mobile_nutrition,mobile_pump_checks,mobile_pump_check_comparisons,training,tracking,coach,profile,nutrition/*}.py` ·
`app/services/today_facts.py` ·
`app/services/workout_state/{__init__,models,queries,serialization,snapshot}.py` ·
`app/services/{context_builder,adaptive_plan_context,coach_context_queries,analytics_engine,ai_recovery,ai_gate,exercise_catalog,runtime_metrics}.py` ·
`app/services/plan_confirmation/service.py` ·
`app/services/plan_mutation/journal.py` ·
`app/services/coach_plan_policy/confirmation.py` ·
`app/services/coach_plan_tools/*` ·
`app/services/mobile_nutrition/{__init__,serialization}.py` ·
`app/services/mobile_pump_checks/*` · `app/services/mobile_pump_check_comparisons/*` ·
`app/services/progress_insights/{analysis,models,payload}.py` ·
`app/services/progress_summary/payload.py` ·
`app/services/training_generation/{service,response_validator,exercise_resolution,plan_schema}.py` ·
`templates/{index,today}.html` · `static/analytics.js` ·
`tests/{conftest,test_mobile_auth_feature_gate}.py` · `pytest.ini` · `CLAUDE.md`

**Mobile — `axisai_mobile` @ `e6aab4d` (read-only)**

`pubspec.yaml` · `lib/app/composition/app_composition.dart` ·
`lib/app/navigation/app_route_registry.dart` ·
`lib/app/router/{app_router,app_navigation_coordinator}.dart` ·
`lib/app/shell/shell_destination_presentation.dart` ·
`lib/core/data/fixture/fixture_repository_guard.dart` ·
`lib/features/today/**` (domain, data, presentation, state) ·
`lib/features/coach/presentation/coach_screen.dart` ·
`lib/features/progress/presentation/progress_screen.dart` ·
`lib/features/nutrition/presentation/state/nutrition_diary_controller.dart` ·
`lib/features/auth/{domain/auth_account,data/auth_contract_parser}.dart` ·
`fixtures/{today-*,progress-summary,weekly-plan,workout-detail}.json` ·
`test/app/responsive_accessibility_test.dart` · `test/` inventory (141 files)

---

## 44. Final recommendation

Sprint 12 does not need new intelligence. It needs one deletion and one honest
pipe — **in that order.**

**Begin with PR2A** — make it impossible for release/production mobile
composition to select a fabricated repository. Production may show real, empty,
unavailable or degraded state; never fabricated user-specific fitness state.
This is the sprint's P0 and it is independent of every flag, every endpoint and
every other PR, so nothing justifies sequencing it second.

**Then PR2B** — the bounded Sprint 11 closure: canonicalize exercise identity
before annotating injuries, so a Today surface cannot republish a warning that
differs between two aliases of the same exercise. Warn-only semantics preserved;
no medical inference added.

**Then PR3** — `GET /api/v1/today`: a thin composition over
`resolve_workout_state`, `get_active_plan`, `serialize_today_plan` (plus
`exercise_id`) and `build_diary_day`, with a pure server-owned priority policy,
per-section degradation, state codes instead of prose, an explicit
`day: {date, timezone}`, and **no second daily-state authority**.

**Then PR4** — replace fixture Today behaviour with the real repository against
that contract: loading, truthful empty, partial/degraded, actionable, canonical
CTA navigation, invalidation after relevant actions, and the typed workout
context `(plan_lineage, mutation_version, canonical_local_date)`. No fake
fallback data.

**PR5 only if Adaptive Coaching rollout is approved.** Otherwise it is deferred
out of the sprint, and nothing above changes.

Everything after that is convergence work with a real contract underneath it —
UX hierarchy, Coach handoff, freshness, accessibility, instrumentation —
deliberately unnumbered until the dependency graph earns it.

The single most important line in this report: **release mobile builds can tell
a production user they have a workout called "Upper Body Strength" scheduled on
2026-07-28.** Whatever else Sprint 12 does, it should stop doing that first.
