# Native Training Vertical Slice: Discovery and Contract Convergence

**Date:** 2026-08-30
**Status:** READY WITH PREREQUISITES
**Branch:** `mobile-training-pr1-discovery-contract`
**Scope:** PR1 architecture and contract specification only; no product behavior

## A. Executive Summary

The native Training vertical slice can proceed safely after two backend
prerequisites are implemented:

1. first-plan generation needs one server-owned, durable, idempotent
   generate-and-persist command; and
2. workout checkpoints need canonical persisted progress and a concurrency
   contract. The existing session checkpoint is only a heartbeat.

The backend already owns the difficult semantics: preference validation,
capability classification, bounded provider execution, output validation,
canonical exercise resolution, plan persistence validation, plan selection,
workout-state resolution, session lifecycle, and atomic completion. The missing
boundary is a native-safe `/api/v1` projection. Existing Training HTTP routes use
browser authentication and browser-oriented response shapes and must not be
consumed by Flutter.

Flutter already has reusable Today, Plan, workout-detail, and active-workout
presentation architecture. Production composition deliberately wires Plan and
Workout to unavailable repositories. Today is the only canonical live Training
consumer. Plan creation is an explicit unavailable dialog, and workout completion
is an explicit unavailable state.

No Sprint 13, Pump Check, Progress, Coach, authentication, or production source
file is changed by PR1.

## B. Repository State

Repository truth was refreshed with `git fetch --prune origin` before discovery.
Open pull requests were queried from GitHub after the fetch.

| Field | Backend | Mobile |
|---|---|---|
| Repository | `fitness-coach` | `axisai_mobile` / remote `axisai-mobile` |
| Repository path | `C:\Users\yusuf\fitness-coach` | `C:\Users\yusuf\develop\axisai_mobile` |
| Inspection authority | fetched `origin/main` | fetched `origin/main` |
| `origin/main` SHA | `a44f31effb4ac23a020bcef322765dfe620c88f9` | `3386df37198ef0193c64fa4754a686357868f785` |
| Original checkout branch | `feat/pump-check-gallery-premium-ui` | `mobile/foundation-pr3-core-screens` |
| Original checkout HEAD | `a44f31effb4ac23a020bcef322765dfe620c88f9` | `25c9ca63bff4d6457507535ae6044b6337f1e9a6` |
| Original checkout ahead/behind `origin/main` | `0 / 0` | `0 / 60` |
| Original checkout dirty state | dirty Pump Check UI work | clean |
| Original checkout untracked state | 3 Pump Check files | none |
| Main-snapshot worktree | PR1 sparse worktree below | `.worktrees/sprint12-pr4-mobile-today-real-data-convergence` |
| Main-snapshot dirty state | clean before this document | 7 modified generated desktop plugin files |

The backend original checkout contains modified Pump Check files:
`docs/design-system.md`, `locales/en.json`, `locales/tr.json`,
`static/pump_check_gallery.css`, `static/tokens.css`, and
`templates/pump_check_gallery.html`; and untracked
`docs/PUMP_CHECK_GALLERY_UI.md`, `scripts/frontend_audit/pump_check_pr2_matrix.py`,
and `tests/test_pump_check_gallery_ui.py`. They were not read as Training
authority and were not modified.

The mobile main-snapshot worktree contains generated plugin changes under
`linux/flutter`, `macos/Flutter`, and `windows/flutter`. They were not modified.

The PR1 worktree is
`C:\Users\yusuf\fitness-coach\.worktrees\mobile-training-pr1-discovery-contract`.
It is a sparse checkout because the C: drive ran out of space during the first
full checkout. It tracks backend `origin/main` at `a44f31e` and checks out only
the documentation path required by PR1. Code discovery used the fetched main
snapshot; no source changes were made in the dirty original checkout.

### Parallel-workstream evidence

Sprint 13 has an explicit backend worktree at
`.worktrees/sprint13-pr1-nutrition-closure-discovery`, on branch
`sprint13-pr1-nutrition-closure-discovery`, at `a44f31e`. There was no open PR
whose title or branch identified Sprint 13. The operational label and the local
worktree agree that Sprint 13 is Nutrition closure work.

Pump Check evidence includes the dirty backend checkout above and dedicated
backend worktrees/branches `pump-check-created-race`,
`pump-check-gallery-media-reliability`, `sprint10-pr1-pump-check-canonical-foundation`,
and `sprint10-pr4a-pump-check-history`. Mobile has dedicated worktrees for
`closure/pump-check-entry-and-networking-guards`,
`fix/pump-check-post-merge-history-comparison`,
`sprint10-pr2-pump-check-mobile-vertical-slice`, and
`sprint10-pr4b-pump-check-history-comparison-loop`.

Open backend PRs at discovery were #254, #208, #201, and #193; none was labeled
Sprint 13, Pump Check, or Training. Mobile had no open PRs. Local worktrees are
therefore stronger evidence of current parallel work than open PRs.

## C. Existing Backend Training Authority

### Authority map

| Concern | Canonical owner | Evidence |
|---|---|---|
| Preferences | `training_generation/preference_contract.py` | closed allow-lists, defaults, typed failures |
| Supported combinations | `training_generation/capability.py` | provider-independent matrix |
| Generation orchestration | `training_generation/service.py` | classification, provider budget, repair, validation, identity, warnings |
| Plan shape | `training_generation/plan_schema.py` and validators | seven-day schema and bounds |
| Exercise identity | `exercise_catalog.py` and `training_generation/exercise_resolution.py` | catalog IDs, aliases, equipment/placement compatibility |
| Current persisted plan | `TrainingPlan.plan_data` | newest plan selected by `today_facts.get_active_plan` |
| Current workout state | `services/workout_state` | schedule, execution, action, dominant state, session enrichment |
| Session lifecycle | `services/workout_session` and `WorkoutSession` | start/resume/heartbeat/abandon/complete and active-session unique claim |
| Confirmed completion | `services/workout_completion.complete_workout` | atomic PumpCheck, marker, XP, quest, activity, session terminalization |
| Mobile Today | `services/mobile_today.py` | read-only projection over the preceding authorities |
| Date/time | `app.timeutil.app_today()` | server-owned Europe/Istanbul day |

### Training preference schema

The canonical request schema is:

| Field | Type | Allowed/default |
|---|---|---|
| `gun_sayisi` | integer | `3,4,5,6`; default `3` |
| `ekipman` | token | `spor_salonu,ev,minimal`; default `spor_salonu` |
| `odak` | token | `tum_vucut,ust_vucut,sirt,alt_vucut,core`; default `tum_vucut` |
| `sure` | integer | `30,45,60,90`; default `45` |
| `kardiyo_tipi` | token | `yok,kosu,bisiklet,yuzme,ip_atlama,yuruyus,karisik`; default `yok` |
| `kardiyo_gun` | integer | `0..6`; default `0` |
| `kardiyo_sure` | integer | `15,20,30,45`; default `20` |
| `kardiyo_yogunluk` | token | `dusuk,orta,yuksek,karisik`; default `orta` |
| `antrenman_tarzi` | token | general aliases, `fonksiyonel`, `bodybuilding`, `powerlifting`, `crossfit`, `calisthenics`; default `genel` |
| `odak_hedef` | token | `genel,guc,kondisyon,kas_kutlesi,yag_yakimi,esneklik`; default `genel` |
| `injuries` | string | posted value, otherwise stored profile injury text |

Known capability outcomes are: CrossFit is unsupported by the current seven-day
schema; powerlifting requires gym equipment; cardio days without a cardio type
conflict; and training plus cardio allocation cannot exceed seven days. These
decisions remain server-owned.

Flutter has no Training-preference or generation model, interface, repository,
controller, or production screen. It therefore does not currently match or
duplicate this schema.

### Generation execution

The actual call chain is:

```text
POST /training-plan (browser)
  -> parse_preferences / parse_canonical_preferences
  -> require_supported
  -> persist posted injuries
  -> build_features from User + latest UserSession
  -> classify_user
  -> build_program_context
  -> canonical exercise vocabulary
  -> provider completion (maximum two calls)
  -> JSON extraction
  -> structural and semantic validation
  -> one bounded repair only for parse/truncation failures
  -> canonicalize exercise names to catalog identities
  -> warn-only injury annotation after identity resolution
  -> candidate response + signed exercise-context token
```

Schema and semantic failures are not repaired. Exercise-authority failures are
also not repaired. Provider exceptions become a typed retryable generation
unavailable failure. Provider text and internal reasons are not client contracts.

The current browser flow then sends the candidate to `POST /training-plan/save`.
That boundary verifies the signed user-bound exercise context, revalidates
structure and semantics, re-resolves exercises and equipment compatibility, and
only then deletes/replaces the prior plan. A validation failure cannot alter the
current plan.

### Plan persistence and identity

`TrainingPlan.plan_data` is the current-plan authority. The active selector is
the most recently created row for the authenticated user. The legacy save route
deletes all user plan rows and inserts one replacement. Each inserted plan gets a
new opaque `lineage_id`; `mutation_version` starts at zero and is the
server-authoritative version for later mutations. A failed validation occurs
before deletion. A failure after deletion but before commit is not explicitly
wrapped as a domain transaction in the browser controller, which is one reason a
native route must call a lower-level persistence service rather than wrap that
controller.

The generated candidate is not a persisted plan. The mobile contract should not
reproduce the browser's generate-candidate/client-round-trip/save protocol.

### Plan and workout reads

Existing read surfaces are:

| Surface | Classification | Reason |
|---|---|---|
| `GET /training-plan/active` | WEB CONTRACT — DO NOT CONSUME DIRECTLY | browser auth; legacy shape; unbounded raw plan document |
| `GET /training/bootstrap` | WEB CONTRACT — DO NOT CONSUME DIRECTLY | browser auth; useful bounded serializers and coherent snapshot below it |
| `GET /workout/status` | WEB CONTRACT — DO NOT CONSUME DIRECTLY | browser auth; canonical service below it is reusable |
| `GET /api/training/weekly-program` | WEB CONTRACT — DO NOT CONSUME DIRECTLY | browser auth and adaptive recommendation, not current-plan authority |
| `GET /api/v1/today` | EXISTING `/api/v1` CONTRACT — REUSE | bearer auth, owner-only, bounded, canonical, no-store |
| `today_facts.get_active_plan` | SERVER INTERNAL — NO CLIENT CONTRACT | canonical selector to reuse |
| `workout_state.serialization` | SERVER INTERNAL — NO CLIENT CONTRACT | bounded projection helpers to extend |

There is no native-safe current-plan or arbitrary plan-day/workout-detail API.
The current public serializer strips `exercise_id`, so a mobile projection must
add a bounded canonical exercise identity rather than reuse that serializer
unchanged.

### Workout state and date authority

`resolve_workout_state` owns the state dimensions. Schedule states are
`scheduled`, `rest_day`, `no_plan`, and `schedule_unavailable`. Execution states
are `no_execution`, `execution_recorded`, and `completed`; with persisted
sessions enabled, an eligible active session produces `in_progress`. Actions are
`start`, `resume`, `none`, and `blocked`. Dominant states include
`scheduled_not_started`, `in_progress`, `completed`, `rest_day`, `no_plan`, and
`needs_attention` plus documented unscheduled/evidence variants.

Completion proof is today's `PumpCheck`; the synthetic `WorkoutLog` completion
marker corroborates it. Non-marker WorkoutLog rows are only execution evidence
and never prove resumability. Today and Training share this resolver. The server's
Istanbul date is authoritative; no client date selects today's workout.

### Session lifecycle

`WorkoutSession` is the persisted authority when
`FITX_WORKOUT_SESSIONS_ENABLED` is ON. The flag is default OFF. The database
enforces at most one active session per user. Session identity is an opaque
`public_id`; plan attachment is a soft plan ID, weekday slot, and versioned
exercise-list fingerprint.

Existing service behavior:

- start derives owner, date, plan, slot, and fingerprint server-side; a replay
  returns the existing active session and a different active workout conflicts;
- current is read-only;
- resume permits only an owned, same-day, compatible active session;
- checkpoint only coalesces a `last_activity_at` heartbeat for 30 seconds;
- abandon is terminal and replay-idempotent;
- completion delegates to the canonical completion service;
- plan replacement, previous-day sessions, changed schedule slots, and
  lifecycle inconsistencies produce explicit stale/blocked classifications.

Crash/restart can recover the existence and identity of an active session, but
cannot recover completed-set progress because the server does not persist it.
Native resumability is therefore incomplete until canonical progress persistence
exists.

### Completion and side effects

`workout_completion.complete_workout` owns one atomic transaction containing the
PumpCheck, WorkoutLog completion marker, quest claim, XP, activity row, friend
share messages, and linked session transition to completed. Challenge updates
are best-effort inside an isolated savepoint. The unique `(user_id,date_key)`
PumpCheck constraint is the concurrency-safe daily completion claim. Sequential
replay and the race loser return `already_completed`; a linked active session is
reconciled to completed without duplicate rewards.

The browser route performs Pump Check image validation/provider work and optional
S3 upload before calling the completion service. This is a material legacy
coupling: confirmed workout completion and a daily PumpCheck share the same
canonical proof. PR1 preserves and documents it. A native completion contract
must reuse the completion service and coordinate with the separately owned
mobile Pump Check API; it must not create a second completion record or silently
change Pump Check behavior.

## D. Existing Mobile Training Architecture

### Navigation and screens

| Surface | Classification | Current behavior |
|---|---|---|
| Today | KEEP | production live repository consumes `GET /api/v1/today` when native auth is enabled |
| Plan | KEEP WITH DATA CONVERGENCE | reusable loading/error/empty/content UI; production repository unavailable |
| Workout detail | KEEP WITH DATA CONVERGENCE | reusable exercise UI; production repository unavailable |
| Active workout | REQUIRES FUNCTIONAL CHANGE | local draft UI exists; no write methods; finish explicitly unavailable |
| Completion/result | PLACEHOLDER | no Training completion screen; only unavailable state |
| Preference/generation | PLACEHOLDER | no screen or domain architecture exists |

Canonical routes already exist: `/today`, `/plan`, `/workout/detail`, and
`/workout/session`. Workout routes belong to the Plan destination, require typed
in-memory route context, and are deliberately not deep-linkable/restorable until
public workout/session identity exists.

Today routes `start` to workout detail and `resume` to workout session. `no_plan`
maps to a Create Plan button that opens an explicit “not connected” dialog.
Completion maps Today to Progress, but Progress production data remains outside
this slice.

### Repository and controller inventory

| Component | Interface exists | Production implementation | Fixture/test implementation | Current status |
|---|---:|---|---|---|
| `TodayRepository` | yes | `LiveTodayRepository` | fixture + unavailable | canonical live |
| `WeeklyPlanRepository` | yes | unavailable | fixture | presentation-ready, no live contract |
| `WorkoutDetailRepository` | yes | unavailable | fixture | presentation-ready, no live contract |
| `WorkoutSessionRepository` | yes | unavailable | fixture | read-only interface; insufficient for lifecycle |
| Training generation repository | no | none | none | missing |
| `TodayController` | yes | production-used | tested | reusable |
| `PlanController` | yes | production-used with unavailable repo | tested | reusable with mapper/error changes |
| `WorkoutDetailController` | yes | production-used with unavailable repo | tested | reusable with identity/action changes |
| `WorkoutSessionController` | yes | production-used with unavailable repo | tested | local-only draft; must gain commands |

`AppComposition.configured` wires live Today, Nutrition, and Pump Check only when
native auth is enabled. It wires Plan, workout detail, workout session, and
Progress to explicit unavailable repositories. Fixture repositories are confined
to development composition and guarded tests.

### Mobile model comparison

`WeeklyPlan` contains client-created concepts not directly present in the backend
plan: start/end dates, a plan ID, title/goal, dated days, day display states, and
workout IDs. These are harmless presentation concepts only if a live mapper
derives them from server-issued lineage/version/date/slot data. Flutter must not
use device time to derive the selected day or completion.

`WorkoutDetail` is structurally compatible with a bounded backend day projection,
but it requires stable `planId`, `workout.id`, exercise IDs, and numeric
`restSeconds`; the backend persists Turkish duration text for `dinlenme` and must
either add a normalized seconds field to the projection or the Flutter model must
treat it as display text. Flutter must not parse canonical rest semantics from
free-form text.

`WorkoutSession` contains set-level progress and action booleans that the backend
session projection does not currently return or persist. These fixture fields are
not server truth and cannot be promoted directly into production DTOs.

There is no Flutter Training preference model. The native model must use the
server's closed tokens and metadata; it must not copy the capability matrix into
client decision logic.

### Existing reusable tests

Mobile suites cover Plan, workout detail, active workout, their controllers,
fixture DTO mapping, Today API strict parsing, live Today repository behavior,
Today routing/lifecycle, canonical navigation, unavailable repositories,
production fixture exclusion, and auth/session transition boundaries. These are
good seams for replacing unavailable repositories with live ones.

Backend suites cover preference contracts, generator output reliability,
exercise authority, Training routes/UI, plan views/mutations, workout state,
session lifecycle and PostgreSQL behavior, completion and PostgreSQL races,
mobile Today auth/API/architecture, Progress projections, Coach context, and Pump
Check characterization.

## E. Contract Matrix

| Capability | Existing backend contract | Mobile-safe? | Required contract action |
|---|---|---:|---|
| T1 Preference discovery/configuration | internal preference contract | no | NEW MOBILE PROJECTION REQUIRED |
| T2 Generate first plan | browser `POST /training-plan` + `/save` | no | NEW MOBILE PROJECTION REQUIRED over a new idempotent orchestration service |
| T3 Read current plan | browser `/training-plan/active`, bootstrap | no | NEW MOBILE PROJECTION REQUIRED using active-plan selector and bounded serializers |
| T4 Read workout detail | bootstrap serializer for a day | no | NEW MOBILE PROJECTION REQUIRED |
| T5 Read current workout state | `/api/v1/today` | yes | EXISTING `/api/v1` CONTRACT — REUSE |
| T6 Start workout | browser session start + canonical service | no | NEW MOBILE PROJECTION REQUIRED; service reused |
| T7 Resume workout | browser current/resume + canonical service | no | NEW MOBILE PROJECTION REQUIRED; service reused |
| T8 Save/checkpoint progress | heartbeat only | no | NEW DOMAIN PERSISTENCE + MOBILE CONTRACT REQUIRED |
| T9 Abandon workout | browser session abandon + canonical service | no | NEW MOBILE PROJECTION REQUIRED; service reused |
| T10 Complete workout | browser completion + canonical service | no | NEW MOBILE PROJECTION REQUIRED; preserve Pump Check coupling |
| T11 Re-read canonical state | `/api/v1/today` | yes | REUSE and invalidate/re-read after writes |

No Flutter production code may call `/training-plan*`, `/training/bootstrap`,
`/workout/*`, or `/api/training/weekly-program` directly.

## F. Proposed Native API Surface

All endpoints are under the existing mobile API blueprint, require
`Authorization: Bearer`, return `Cache-Control: private, no-store`, use the
established mobile error envelope, derive owner/date from authentication and
server time, and never call a browser controller.

### Read contracts — implemented PR2

| Method/path | Request | Response | Idempotency | Canonical service reused |
|---|---|---|---|---|
| `GET /api/v1/training/preferences` | none | exact metadata contract below | safe GET | preference constants and capability matrix vocabulary |
| `GET /api/v1/training/plans/current` | none | exact `{ "plan": null }` no-plan state or the plan schema below | safe GET | `get_active_plan`, plan validator, exercise catalog, workout-state resolver |
| `GET /api/v1/training/workouts/{workout_ref}` | opaque reference from the current-plan response | exact workout schema below | safe GET | active-plan selector and the shared bounded day projection |

All three routes live on the existing feature-gated mobile blueprint, require
`Authorization: Bearer`, and return `Cache-Control: no-store`. The preference
response has exactly `contract_version`, `fields`, and `capability_constraints`.
`contract_version` is `1`. `fields` contains the eleven canonical preference
keys from the Training preference schema; each closed field publishes `type`,
`default`, and deterministically sorted `choices`, while `injuries` publishes
`{"type":"string","default":""}`. Capability constraints publish only closed
`status`, `reason`, and typed `when_any` alternatives for CrossFit unsupported,
powerlifting equipment, cardio days without a type, and weekly allocation over
seven days. Each alternative is an AND of field-to-allowed-value lists; the
ordered constraint list uses first-match semantics, matching the canonical
evaluator's precedence. Values and bounded weekly-overflow combinations are
derived by the canonical capability module and exhaustively checked against its
evaluator. This is rendering metadata, not an alternate client validator;
submission remains authoritative.

The current-plan endpoint always returns HTTP 200 for readable product states.
No plan is exactly `{ "plan": null }`; a plan is exactly:

```json
{
  "plan": {
    "plan_lineage": "opaque-lineage",
    "mutation_version": 4,
    "created_at": "2026-07-01T08:30:00Z",
    "score": 8.5,
    "current_workout_ref": "24-char-opaque-token",
    "days": []
  }
}
```

`score` is null or a finite canonical 1–10 value. `current_workout_ref` is null
unless the canonical Istanbul day is a scheduled workout/cardio day according to
`resolve_workout_state`. `days` has exactly seven canonical weekday slots in
Monday-through-Sunday order. Each day has exactly `slot`, `weekday`, `kind`,
`focus`, `duration_minutes`, `estimated_calories`, `workout_ref`, and
`exercises`. Closed `kind` values are `training`, `cardio`, and `rest`; rest days
have a null reference and no exercises. A day is bounded to at most 32 exercises.
An existing row that cannot be safely projected returns HTTP 409 with code
`TRAINING_PLAN_UNPROJECTABLE` and `retryable:false`; it is never collapsed into
no-plan. Flutter DTOs reject missing or additional top-level keys and never map
the 409 to an empty plan.

The plan object uses `plan_lineage` plus `mutation_version` as plan identity.
Each exercise has exactly `exercise_id`, `display_name`, `sets`, `reps`, `rest`,
and `notes`. Identity and display name come from the active canonical catalog;
the persisted display spelling is not an identity authority. Missing, unknown,
inactive, or malformed IDs make the existing plan unprojectable. `rest` is
exactly `{ "display_text": string, "seconds": integer|null }`. Seconds are
derived only from exact `N sn`, `N dk`, or `0` forms and only up to 86,400;
anything else remains bounded display text with null seconds. No fuzzy or
locale-dependent client parsing is allowed.

Each non-rest day carries a deterministic 24-character base64url HMAC reference.
It is domain-separated and bound to authenticated owner, lineage, mutation
version, and canonical slot; it contains no decodable database ID and is not a
new persisted plan authority. Rest days have no workout reference.
The workout-detail endpoint verifies every bound component against the current
owned plan. A replacement or mutation returns HTTP 409 with code
`TRAINING_WORKOUT_STALE` and `retryable:false`; the client discards the reference,
re-reads the current plan, and does not retry the stale request.

Workout detail is exactly `{ "workout": { "plan_lineage",
"mutation_version", "workout_ref", "slot", "weekday", "kind", "focus",
"duration_minutes", "estimated_calories", "exercises" } }`, using the same day
and exercise projection as the current plan. Malformed or oversized path tokens
return private `TRAINING_WORKOUT_NOT_FOUND` HTTP 404. Any syntactically valid
token that does not match the authenticated owner's current revision—including
foreign, tampered, random, replaced, mutated, or now-rest references—returns the
same `TRAINING_WORKOUT_STALE` 409 and reveals no cross-owner existence fact.

The read service performs zero writes and zero provider calls. With bearer
authentication stubbed at its separately tested boundary, contract tests measure
zero SQL statements for preference metadata, at most five for the current-plan
composition, and exactly one owner-scoped active-plan query for workout detail;
exercise count does not change those budgets. Existing `/api/v1/today` response
bytes and browser Training behavior are unchanged.

### First-plan write — later backend PR

`POST /api/v1/training/plans`

- headers: bearer auth and required `Idempotency-Key`;
- request: `{ "preferences": { ...canonical fields... },
  "replace_existing": false }`;
- response: `201` with the same current-plan projection and a generation result
  category; an exact replay returns the original committed response;
- conflict: an existing plan with `replace_existing:false` returns
  `TRAINING_PLAN_REPLACEMENT_REFUSED`;
- authority: a new lower-level generate-and-persist command reuses
  `generate_training_plan_payload`, canonical validation/exercise context, and a
  transaction-owned persistence service;
- no candidate plan or signed exercise-context token is round-tripped through
  Flutter.

This first slice does not expose plan replacement UI. The parameter is fixed
false by mobile and exists to make the safety rule explicit.

### Session contracts — SHIPPED (Mobile Training PR5)

| Method/path | Request/condition | Response | Replay/concurrency |
|---|---|---|---|
| `POST /api/v1/training/workout-sessions` | no client-selected “today”; optional approved workout ref only as a consistency assertion | `201 created` or `200 existing_active`, session projection + revision | intrinsic idempotency from one-active-session unique claim; duplicate tap safe |
| `GET /api/v1/training/workout-sessions/current` | none | no-session or owned active/blocked session with canonical workout and progress | safe GET |
| `POST /api/v1/training/workout-sessions/{public_id}/resume` | `If-Match: <revision>` | resumed or typed stale/terminal outcome | replay-safe touch; ownership and revision enforced |
| `PUT /api/v1/training/workout-sessions/{public_id}/checkpoint` | required `Idempotency-Key`, `If-Match`, full bounded progress snapshot | committed progress + new revision | durable key/request fingerprint and optimistic concurrency |
| `POST /api/v1/training/workout-sessions/{public_id}/abandon` | `If-Match`, bounded reason code | abandoned or already-abandoned terminal replay | conditional terminal transition; no key required |
| `POST /api/v1/training/workout-sessions/{public_id}/complete` | required `Idempotency-Key`, `If-Match`, approved completion/Pump Check input | completion result + terminal session revision | daily completion unique claim + durable request replay |

Checkpoint progress must be added as a server-owned model or bounded JSON
document tied to the session and revision. It includes current exercise index and
per-set completion/entered values. The server validates exercise/set identity
against the session's captured canonical workout; it never accepts a replacement
workout definition from Flutter.

Completion contract design must be finalized against the existing mobile Pump
Check creation contract before implementation. Acceptable implementations either
perform approved Pump Check validation inside the Training transport adapter and
call `complete_workout`, or reference an owned, validated, single-use Pump Check
claim that the completion service can atomically adopt. An independent “mark
complete” write is forbidden.

**Resolution (PR5): the first option.** `POST .../{session_ref}/complete` performs
the approved Pump Check validation (`validate_uploaded_pump_check_image` →
`validate_pump_check` → `s3_helper.upload_image`) in the transport adapter, before
the transaction, and then calls `complete_workout` through
`workout_session.complete_session`. The second option was rejected: adopting an
existing mobile Pump Check row would require changing what
`workout_completion.queries.already_completed_today` means for every caller —
the browser route, the AI-coach gym-photo tool and `workout_state` — which is the
cross-domain rewrite this slice is not allowed to attempt.

*Known pre-existing consequence, unchanged by PR5:* `already_completed_today`
counts ANY Pump Check created in the Istanbul day window, and the mobile
`POST /api/v1/pump-checks` photo route writes rows with `date_key = NULL`. A user
who takes a mobile Pump Check photo before finishing their workout therefore has
a day that already looks completed, and a later session completion replays
(`already_completed`) instead of creating a completion. This predates PR5 and is
equally true of the browser route; fixing it means changing that query's meaning
for all of its callers.

#### Implemented shape

* Everything is behind `FITX_WORKOUT_SESSIONS_ENABLED` (default OFF). While it is
  off, all six routes answer `404` — the surface is absent, not forbidden.
* One response shape for all six: `{"session": {...}}` (complete also carries
  `{"completion": {...}}`). It has the same key set in every state.
* `revision` is the optimistic concurrency token. `If-Match` is REQUIRED on
  checkpoint and complete, OPTIONAL on resume and abandon (neither writes
  progress, so demanding a precondition would only make a safe retry fail).
* `Idempotency-Key` is REQUIRED on checkpoint and complete. On checkpoint it is
  the replay identity, paired with a domain-separated SHA-256 fingerprint of the
  canonicalized snapshot: same key + same snapshot replays, same key + different
  snapshot is `TRAINING_SESSION_IDEMPOTENCY_CONFLICT`. On complete it is a client
  discipline requirement only — exact-once there comes from `uq_pump_check_day`,
  not from the key.
* Every response carries `Idempotency-Replayed: true|false`. Every refusal carries
  `Session-Resolution: retry|reread|terminal`, so a client learns what to DO
  without parsing status codes.
* Completion carries the client's `If-Match` revision into the completion
  transaction, where it is checked under the session row's `FOR UPDATE` lock —
  the only race-free place — so completing can never silently discard a
  checkpoint that landed after the client's last read.
* The native completion is `visibility="private"`: PR5 excludes the feed/social
  surface, so no native completion fans a Pump Check out to friends.

#### Known edge: adopting a browser-started session

A session started through the browser contract has no `workout_ref` — that
contract never had one. `POST /workout-sessions` adopts it (`200`, replayed) when
it names the same day, slot and source, because it IS the same intended workout,
and the projection honestly reports `workout_ref: null`. It can be read, resumed
and abandoned, but a checkpoint against it returns
`TRAINING_SESSION_STALE` / `Session-Resolution: reread`: the server will not
guess which canonical workout to validate the snapshot against. The recovery is
abandon, then start natively. This is exercised by
`test_a_browser_started_session_is_adopted_but_cannot_be_checkpointed`.

## G. State Machine

### Persisted plan

```text
NO_PLAN
  -> GENERATION_COMMAND_ACCEPTED (transient operation, not current plan)
  -> ACTIVE(lineage, mutation_version=0)

ACTIVE
  -> ACTIVE(new lineage, version=0) only through an explicit future replacement
```

The server owns every state. A failed or invalid generation leaves `NO_PLAN` or
the prior `ACTIVE` plan unchanged.

Projection health is a separate read dimension, not a persisted-plan transition:
an active plan is `PROJECTABLE` or `UNPROJECTABLE`. The latter returns the typed
409 above and contributes `needs_attention` through Today/workout state without
changing or deleting the active `TrainingPlan` row.

### Workout state

```text
no_plan | rest_day | scheduled_not_started | needs_attention
scheduled_not_started -> in_progress       (server session start)
in_progress -> in_progress                  (resume/checkpoint)
in_progress -> completed                    (canonical completion transaction)
in_progress -> abandoned                    (explicit terminal transition)
```

Today remains the dominant current-state projection. Plan content does not select
today; the workout-state resolver does.

### Session and completion

Persisted session status is `active`, `completed`, or `abandoned`. Staleness is a
derived classification, not a fourth status. Completed and abandoned are
terminal. A stale active session requires explicit resolution and cannot be
silently attached to a regenerated plan. Confirmed completion atomically writes
the canonical completion proof and terminalizes the linked active session.

## H. Error Model

Mobile repositories map transport codes into semantic categories. Copy remains a
presentation concern.

Generation categories:

- invalid preferences — terminal until input changes;
- unsupported configuration — terminal until choices change;
- conflicting preferences — terminal until choices change;
- missing generation prerequisite — current backend requires a UserSession;
- provider unavailable or timeout — retryable with the same idempotency key;
- parse/truncation exhausted — retryable only when the server code says so;
- schema/semantic/exercise authority invalid — generation failed; never let the
  client repair provider output;
- replacement refused — terminal for first-plan flow;
- unauthorized, forbidden/premium limit, rate limited, and temporarily
  unavailable.

Plan read categories: no plan, unauthorized, stale workout reference/plan
revision, deleted/replaced plan, malformed/incompatible response, and temporary
server failure.

Workout categories: no workout today, rest day, no session, already completed,
already abandoned, stale session requiring resolution, concurrent active-session
conflict, revision conflict, invalid transition, unauthorized/not-owned,
rate-limited, incompatible response, and retryable network/server failure.

Unknown server enum values map to an explicit unsupported/incompatible state,
never to workout-ready or completed.

## I. Idempotency and Retry Model

| Operation | Required mechanism | Client rule |
|---|---|---|
| Generate/persist plan | durable user + idempotency-key record, request fingerprint, stored result; existing-plan precondition | freeze one key for the logical attempt across timeout, refresh replay, and retry; new key only after explicit new attempt |
| Start session | DB unique active-session claim and same-intended-workout replay | disable duplicate taps; retry is safe without rotating identity |
| Resume | owned public ID + revision; touch is replay-safe | retry same command; never create a local replacement session |
| Checkpoint | idempotency key + session revision + full bounded snapshot | serialize writes; freeze key/payload; on revision conflict re-read and reconcile |
| Abandon | conditional active-to-abandoned transition + revision; terminal replay result | disable duplicate taps; re-read after ambiguous network result |
| Complete | idempotency key + revision + `uq_pump_check_day` + completion result replay | freeze key/payload; never optimistically make completion permanent; re-read Today |

An authentication refresh replay must preserve method, body, idempotency key,
and concurrency token. A client may show a temporary pending state, but canonical
success comes only from the server response followed by state re-read.

## J. Today Integration

- Before generation, `GET /api/v1/today` returns canonical `no_plan` and the
  mobile maps it to Create Plan.
- After generation commits, Flutter invalidates and re-reads both current Plan
  and Today. It does not mutate the cached Today object into a fake plan state.
- Every successful or network-ambiguous start, resume, checkpoint, abandon, or
  complete marks the retained Today controller stale. Before the Today branch is
  displayed again, the router/session coordinator calls an explicit
  `TodayController.refresh()` (or equivalent canonical re-read) and awaits its
  settled state; returning to an already-mounted shell branch is not treated as
  an implicit reload. Today exposes `in_progress/resume` only when the server
  session contract is enabled and proves resumability.
- Completion remains pending until the server commits. Flutter then re-reads
  Today and Plan; Today must report `completed` from canonical PumpCheck proof.
- Logout/account epoch changes discard cached Plan, workout detail, session,
  idempotency attempts, and Today together.

No second Today endpoint or client calendar selection is introduced.

## K. Cross-Domain Boundaries

**Nutrition:** no dependency. Training must not touch Sprint 13 Nutrition models,
routes, state, or Today diary composition.

**Progress:** completion already persists WorkoutLog/PumpCheck evidence used by
Progress summaries/history, plus XP/activity. Future mobile Progress should
invalidate after completion and read its own canonical projection. Training does
not implement Progress or navigate into new Progress behavior.

**Pump Check:** completion is coupled to PumpCheck as canonical daily proof and
currently includes image validation, optional S3 media, visibility, and sharing.
The coupling is launch-impacting because a separate native completion endpoint
cannot bypass it. Pump Check UI/network/history/comparison remain untouched in
PR1 and must be coordinated, not redesigned, in the completion-contract PR.

**Coach:** the Coach/adaptive context reads persisted plan, WorkoutLog, PumpCheck,
and workout-history facts. Reusing the canonical completion transaction preserves
those inputs. Native Coach is not part of this slice.

**Authentication:** every new route uses `require_mobile_auth`, `g.mobile_user`,
and the existing authenticated transport/refresh coordinator. No cookies,
redirects, Flask session identity, or client user ID are accepted.
`AXISAI_NATIVE_AUTH_ENABLED` remains default OFF; this work does not alter rollout
flags.

## L. PR Decomposition

| PR | Purpose | Repo | Depends on | Can run parallel? |
|---|---|---|---|---|
| PR1 | discovery and this contract specification | backend docs; mobile read-only | none | yes; current PR |
| PR2 | bearer-auth preference metadata, current-plan, and workout-detail read projections | backend | PR1 | yes with Sprint 13/Pump Check if registration conflicts are avoided |
| PR3 | strict DTOs and live Plan/workout-detail repositories; Today-to-Plan/detail convergence | mobile | PR2 | backend generation PR may run in parallel; avoid active Pump Check router/composition edits |
| PR4A | idempotent first-plan generate-and-persist service and mobile endpoint | backend | PR1; read projection reused | can run with PR3 |
| PR4B | native preference/generation flow and post-create Today/Plan re-read | mobile | PR2, PR3, PR4A | wait for overlapping mobile composition/router work |
| PR5 | canonical session progress persistence plus bearer-auth start/current/resume/checkpoint/abandon/complete contracts | backend | PR2; Pump Check completion decision | generation UI may run in parallel; completion portion must coordinate with Pump Check closure |
| PR6 | live workout execution, restart/resume, checkpoint, abandon, completion, and Today convergence | mobile | PR3, PR5 | not with mobile Pump Check routing/network changes without explicit file ownership |
| PR7 | cross-feature integration tests, rollout guards, and closed-beta hardening | both | PR4B, PR6 | mostly sequential final convergence |

### Dependency graph

```text
PR2 read contracts -> PR3 Plan/detail mobile -> PR6 workout execution
                                           \-> PR7 hardening

PR2 read contracts -> PR3 live Plan/detail ----\
                                             -> PR4B preference UI -> first plan
PR4A generation command ---------------------/

PR2 state/read vocabulary -> PR5 session/progress/completion contracts
                          -> PR6 session UI -> completion -> Today re-read
```

PR2 and PR4A can be developed in parallel after their shared response vocabulary
is fixed. PR3 and PR4A can run in parallel across repositories, but PR4B begins
only after both have landed so a generated plan has a live destination to render
and re-read. PR5 completion cannot be finalized independently of the documented
Pump Check coupling.

## M. Test Strategy

Backend read-contract tests:

- bearer auth required; cookie-only and cross-user access rejected;
- native-auth flag OFF behavior unchanged;
- exact bounded schemas and no-store headers;
- no-plan, rest, malformed plan, and active-plan projections;
- lineage/version and workout-ref ownership/staleness;
- canonical exercise IDs retained without provider/catalog internals;
- zero provider calls for all reads.

Generation tests:

- every preference/default/unsupported/conflicting combination;
- provider unavailable, timeout, parse repair, truncation repair, schema,
  semantic, injury annotation, exercise resolution, and equipment compatibility;
- existing-plan refusal and failed-generation preservation;
- same key/same payload replay, same key/different payload conflict, concurrent
  same-key requests, token-refresh replay, and rate limits;
- commit response and current Plan/Today consistency.

Session/completion tests:

- owner-only start/current/resume/checkpoint/abandon/complete;
- no workout, rest day, already completed, stale plan/day/session, and invalid
  transitions;
- duplicate start, checkpoint and complete; concurrent starts and completions;
- checkpoint revision conflict and replayed key behavior;
- kill/restart recovery of exact server progress;
- atomic completion rollback and session reconciliation;
- Pump Check, marker, XP, quest, activity, history and Today side effects remain
  single-write.

Flutter tests:

- exact-key DTO parsing, enum unknowns, bounds, timestamps and identity linkage;
- auth epoch capture, safe read replay, write replay preserving keys/tokens, and
  cache isolation on logout;
- no-plan, loading, error, empty/partial, rest, ready, active, completed and stale
  UI states;
- Plan selection never chooses Today; Today CTA uses canonical action;
- duplicate-tap suppression, serialized checkpoints, revision conflict re-read,
  abandoned/completed terminals, and restart/resume;
- production composition contains no Training fixture repository;
- every successful or ambiguous session write marks retained Today state stale,
  and navigating back cannot display Today before an explicit repository re-read;
- completion also invalidates and re-reads Plan.

Integration golden path:

```text
Login -> Today(no plan) -> Preferences -> Generate -> Plan
-> Workout detail -> Start -> Checkpoint -> process restart -> Resume
-> Complete -> server commit -> Today(completed)
```

Progress, Pump Check, and Coach assertions are limited to persistence side-effect
compatibility until their own mobile workstreams integrate them.

## N. Architecture Guards

1. Production `AppComposition.configured` must never reference `Fixture*` Training
   repositories.
2. Live Training repositories may call only approved `/api/v1/training/*` and
   `/api/v1/today` paths.
3. Backend mobile Training blueprints may import canonical services, not the
   browser Training blueprint/controller.
4. Flutter may parse canonical IDs and render values but may not validate
   exercises, equipment, injuries, plan semantics, or supported combinations as
   authority.
5. Flutter may not select today's workout from weekday/device time.
6. Only a persisted eligible active session may produce resume.
7. Completion UI may not permanently mark completed before server commit and
   Today re-read.
8. Every Training write test must cover duplicate tap, ambiguous response, auth
   refresh replay, and ownership.
9. New Training routes must use bearer auth and must fail cookie-only tests.
10. Native-auth and workout-session rollout defaults remain OFF unless a separate
    rollout decision changes them.

## O. Risks

1. Completion's PumpCheck coupling can create duplicate or incomplete side
   effects if a new endpoint bypasses `workout_completion.complete_workout`.
2. Existing session checkpointing cannot restore set progress after process
   death; shipping “resume” before persistence would overclaim reliability.
3. The two-step browser generation/save protocol is unsafe for mobile retries and
   replacement unless replaced by a durable command.
4. Backend rest duration is display text while Flutter expects seconds; client
   parsing would create semantic drift.
5. Training response registration and mobile composition/router files overlap
   likely Sprint 13 and Pump Check merge surfaces.
6. Session functionality is default OFF; contracts and UI must remain honest
   when Today returns the version without session fields.
7. Critically low local disk space can obstruct worktree checkout and validation;
   implementation PRs need adequate workspace before dependency/test runs.

Advanced editing/history, Adaptive Coaching activation, social workout sharing,
Feed, gamification redesign, advanced Progress, wearables, notifications, and UI
polish are outside the first vertical slice.

## P. Parallel Workstream Safety

Sprint 13 was not touched. Pump Check was not touched. The mobile repository was
read-only. The only PR1 repository change is this document in the isolated
backend worktree.

Likely conflict surfaces are backend mobile-blueprint registration, models and
migrations, feature-flag declarations, and mobile `app_composition.dart`,
`app_router.dart`, route tests, and shared repository-transition tests. PR2 should
prefer new Training blueprint/service/test files and the smallest possible
registration change. PR3/PR4B/PR6 should not edit Pump Check routing or Progress
entry behavior.

Safe parallel work now: backend PR2 read contracts and backend PR4A service
extraction, provided their shared plan DTO is agreed first. Mobile PR3 should
wait for active Sprint 13/Pump Check edits to shared composition/router files to
settle or use explicit file ownership and a narrow integration commit.

## Q. Final Review

Adversarial review results for PR1:

| Severity | Count | Disposition |
|---|---:|---|
| P0 | 0 | none |
| P1 | 0 | none |
| P2 | 3 | generation persistence/idempotency prerequisite; checkpoint progress persistence; Pump Check completion coordination — all explicitly gated before writes |
| P3 | 2 | rest-duration normalization and low-disk development constraint |

Review answers:

- Proposed APIs reuse backend services and do not duplicate backend authority.
- No browser-session route is approved for mobile consumption.
- No canonical plan/workout/completion state is client-owned.
- Generation/checkpoint/completion retries have explicit durable strategies.
- Today remains the sole native current-state convergence point.
- Progress, Pump Check, Coach, Nutrition, and auth rollout remain bounded.
- Every proposed PR has one primary architectural purpose and explicit
  dependencies.
- Existing session and completion services are classified as reuse, not mistaken
  for new domain work.
- No Sprint 13 or Pump Check file is changed.

## R. Implemented PR2

PR2 implements backend mobile Training read contracts only:

- `GET /api/v1/training/preferences`;
- `GET /api/v1/training/plans/current`;
- `GET /api/v1/training/workouts/{workout_ref}`;
- one shared bounded DTO/projection service over the canonical preference contract,
  active-plan selector, plan validation, and exercise identity;
- bearer-auth, ownership, exact-schema, no-store, malformed-plan, no-plan,
  stale-reference, and zero-provider-call tests;
- an architecture test prohibiting imports/calls from the new mobile route into
  the browser Training blueprint.

Verified acceptance criteria:

1. cookie-only requests fail and bearer-authenticated owners can read only their
   own current plan;
2. no-plan is exactly HTTP 200 `{ "plan": null }`; an existing but unprojectable
   plan is a typed `TRAINING_PLAN_UNPROJECTABLE` 409, never a 500 or no-plan;
3. plan/workout responses are bounded and include lineage/version and canonical
   exercise identity without provider internals;
4. rest/workout classification is server-authored;
5. all endpoints make zero provider calls and perform zero writes;
6. existing `/api/v1/today`, browser Training, Sprint 13, Pump Check, and rollout
   flags are behaviorally unchanged;
7. targeted backend tests pass with P0=0 and P1=0.

Do not begin generation or session writes in PR2.
