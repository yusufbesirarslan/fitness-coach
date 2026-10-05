# TI-00 — Training Intelligence canonical contract and implementation freeze

Date: 2026-10-05. Status: **READY WITH BLOCKERS** (implementation design ready;
release-gate mapping requires the current LP21/22/23 source; browser baseline
expectation failure is recorded separately). Owner: human repository
owner; TI-00 does not authorize merging or activating anything.

This is discovery and architecture only. No production code, models, migrations,
flags, Flutter UI, AWS operations, remote environment writes, plan changes, or
Coach behavior changes are part of this PR. Proposed identifiers and schemas below
are **future contracts**, not descriptions of already deployed capabilities.

## 1. Executive verdict and evidence rules

The smallest safe foundation is the existing `WorkoutSession`, its existing
checkpoint revision, and its existing completion authority. Add a negotiated
checkpoint V2 with context stored on that same session and committed atomically
with the V1 base snapshot. Keep V1 requests and responses exact. Persistent
exercise notes require a separate owner/exercise entity, not checkpoint prose.
Derive bounded facts from completed canonical sessions; recommendations are read
projections with no path to plan mutation.

**FACT** means verified on fetched main at the SHAs below. **INFERENCE** is an
engineering implication of those facts. **PRODUCT DECISION** freezes the future
TI contract. **OPEN QUESTION** means evidence or an owner decision is missing.
File paths in backend tables are relative to `fitness-coach`; mobile paths are
relative to `axisai-mobile`. Symbols and test names identify the evidence even
when line numbers move. Main advancement requires a targeted drift review before
implementation; historical documentation cannot override code.

### Mandatory preflight

Both original worktrees were clean before fetch. Nothing was reset, cleaned,
amended, or discarded. Commands run for **each** original repository, in order:

```sh
git status
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
git log --oneline -15
git fetch origin --prune
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count origin/main...HEAD
git log origin/main --oneline -15
```

| Repository | Initial branch / HEAD | Main before fetch | Main after fetch | Divergence before → after (main-only, HEAD-only) |
|---|---|---|---|---|
| fitness-coach | `training/unresolved-pr-a` / `f5687978f9b0a2bc4be49df11d48b19d0f2402c8` | `1a6ae1d871f734948623441c83de0afe6c4ee019` | same | `8,1` → `8,1` |
| axisai-mobile | `lp14-mobile-pr2-english-coherence` / `17a6d571b088e12c1ccc09823b3fe30f31f2ad9d` | `c1576419949b91e3368d6295e1453a3cdd1082c0` | `be7c65b5afffb99d9dae06e7c0d4ab9d8960829b` | `0,1` → `1,1` |

Inspection uses isolated worktrees, backend branch `docs/ti-00-contract-freeze`
from fetched main and detached mobile fetched main. Original branches stay intact.
The initial and main 15-commit logs are retained in
[ti-00-evidence/preflight.md](ti-00-evidence/preflight.md).

**Verified drift:** native generation now uses server-validated catalog identities
(#382); backend main contains LP-13 proof fail-closed (#386), same-day start refusal
(#385), and day-lock serialization (#387). Mobile main includes the English
presentation fix (#43) and typed-set preservation (#42). Older PR narratives about
native-only checkpoint authority or browser heartbeats are obsolete.

## 2. Current authority map

```text
TrainingPlan (lineage_id, mutation_version, catalog exercise_id)
  → server workout projection / owner-bound workout_ref
  → WorkoutSession (opaque public_id; owner; captured plan relationship)
  → canonical checkpoint validation + checkpoint_revision CAS
  → durable checkpoint_data + acknowledged server projection
  → Flutter canonical baseline → ordered pending draft → presentation

completion → workout_session.complete_session
           → workout_completion.complete_workout
           → PumpCheck day claim + marker + XP + terminal session transaction
           → workout_state → Today / Plan projections
```

**FACT:** `WorkoutLog` is execution evidence, not resumable session authority.
Flutter's recovery file is a cache of canonical acknowledgement and pending
commands, never permission to fabricate a session or a completed workout.

## 3. Backend execution map and exact V1 contract

| Boundary | Files / symbols / routes | Current fact / proof |
|---|---|---|
| Plan authority | `app/models.py:TrainingPlan`; `app/services/today_facts.py:get_active_plan`; `app/services/mobile_training.py:_project_plan`, `_project_exercise`, `workout_ref` | Owner + lineage + mutation version + weekday slot bound into HMAC workout reference. Catalog `resolve_exercise` supplies canonical exercise identity/display name. |
| Prescription | `app/services/training_generation/response_validator.py`; `mobile_training._project_exercise`, `_rest` | `set` integer, `tekrar` string, `dinlenme` string, `not` cue prose. Native fields: `exercise_id`, `display_name`, `sets`, `reps`, `rest:{display_text,seconds}`, `notes`. No typed prescribed load, RIR, or tempo. RPE/tempo can occur in `not`; that is not structured evidence. |
| Native start | POST `/api/v1/training/workout-sessions`; `app/blueprints/mobile_workout_sessions.py:start_workout_session`; `mobile_workout_sessions.service.start`, `resolve_startable_workout` | Bearer principal; today-only reference validation; canonical `start_session`; identity captured on the session; 201 new / 200 replay. |
| Browser start | POST `/workout/session/start`; `app/blueprints/training.py:workout_session_start`; `workout_session.service.start_session` | Cookie + CSRF; server date/plan; partial unique index `uq_workout_session_active_owner` claims at most one active session per owner. |
| Validation | `app/services/workout_session/checkpoint.py:parse_checkpoint`; native adapter re-exports | One shared closed validator; membership against session workout, not caller's supplied plan. |
| Membership | `execution.planned_exercise_identities`; `mobile_workout_sessions.service._session_exercise_ids`, `_session_workout` | Native stored reference re-resolved against lineage/version; browser-started fallback uses scheduled slot + session plan fingerprint. Missing/changed plan fails stale. Unscheduled has no checkpoint membership authority. |
| Checkpoint | Native PUT `/api/v1/training/workout-sessions/<session_reference>/checkpoint`; browser POST `/workout/session/<public_id>/checkpoint`; `execution.record_checkpoint` | Both call same orchestration: owned lookup → terminal refusal → membership → parse → replay → base revision → atomic persistence → refreshed projection. |
| Persistence | `app/services/workout_session/queries.py:advance_checkpoint`; `app/models.py:WorkoutSession` | One conditional UPDATE on owner, reference, ACTIVE, exact base revision. Snapshot, key, fingerprint, revision +1, checkpoint/activity time move together and commit. PostgreSQL arbitrates concurrent UPDATEs; no Python mutex. |
| Read / resume | GET native `/current`, `/<reference>`; POST `/<reference>/resume`; browser `/workout/session/current`, `/<public_id>/resume`; `service.get_current_session`, `resume_session` | Current means ACTIVE only, can report stale; native referenced read includes terminal. Resume preserves checkpoint/revision, updates heartbeat only if eligible. Native optional If-Match honored; browser resume exposes no revision guard. |
| Complete | Native POST `/<reference>/complete`; browser completion in `training.py`; `execution.prepare_completion`, `workout_session.service.complete_session`, `workout_completion.service.complete_workout` | Required checkpoint revision for durable execution completion; native If-Match, browser body `expected_checkpoint_revision`. Preflight before expensive proof, authoritative recheck under session row lock. |
| Abandon | native `service.abandon`; browser `workout_session_abandon`; `queries.terminalize_abandon` | ACTIVE→ABANDONED conditional transition; checkpoint retained; no completion side effects. Native revision optional; browser no declared guard. Abandon is not a progress write. |
| Convergence | `app/services/workout_state/`; `app/services/mobile_today.py`; `mobile_training.project_current_plan` | Server resolves completion/session state; no manual TI input may override Today/Plan truth. |

### Exact checkpoint schema and bounds (FACT)

Body is `{"checkpoint": snapshot}`. Snapshot keys exactly
`current_exercise_index`, `elapsed_seconds`, `exercises`; exercise keys exactly
`exercise_id`, `sets`; set keys exactly `index`, `completed`, `reps`, `weight_kg`.
All required; nullable reps/load; `completed` strictly boolean. No RIR, rest,
notes, timestamps, tempo, or arbitrary extension keys admitted.

* Max 32 exercise entries, max 20 set entries/exercise; duplicate identities reject.
* Exercise ID must be a member of canonical ordered workout IDs. A snapshot may
  contain a subset; it is still replacement of the complete submitted progress.
* Set index integer 0–19; reps null or integer 0–1000; bool never integer.
* Weight null or finite number 0–1000 kg; rounded by Python `round(value, 1)`.
  Negative, NaN, infinity and bool reject. Boundary rounding must match server,
  not assume Dart's `.round()` ties are identical.
* Elapsed integer 0–86400 seconds; current exercise index 0–(canonical count−1).
* Canonical UTF-8 JSON max 65,536 bytes; sorted object keys, compact separators,
  canonical workout exercise ordering, ascending set ordering, no NaN.
* Revision starts 0, required header integer syntax with optional surrounding
  quotes/whitespace, domain 0–999,999,999. Body completion revision is strictly int
  in same range. Idempotency key `[A-Za-z0-9._:-]{8,64}`.
* SHA-256 domain `axisai:training-workout-checkpoint:v1\0` + canonical JSON.
  Identity is the semantic **request snapshot**, not client byte formatting.
* Only last accepted key/fingerprint retained. Same last key/same semantics
  replays without any write, before checking stale base; same last key/different
  semantics conflicts. Old displaced keys have no replay journal: retries with
  their original base are stale. Keys must be unique per logical command; this
  does not promise permanent key uniqueness against malicious fresh-base reuse.
* Concurrent same-base writers: one advances, loser rereads; identical winner
  key/fingerprint replays, otherwise revision conflict. Terminal rows reject
  checkpoint, including late replay. Conflict does not touch activity time.

### Completion, concurrency and proof (FACT)

`workout_completion.queries.lock_session_for_completion` uses owner-scoped
`populate_existing().with_for_update()`. Lock order is session row → owner/day
advisory lock → completion artifacts. `lock_completion_day` serializes same-owner,
same-Istanbul-day starts with both linked and legacy session-less completions.
Start rechecks canonical completion under that lock; the unique active-owner
index remains its session claim. `uq_pump_check_day` supplies exact-once day
completion. SQLite tests do not establish PostgreSQL lock guarantees.

Pump Check is completion proof, not a TI store. Native image checks/provider
validation run outside transaction. Rejected proof is 422 and leaves session
ACTIVE; unavailable/unverified proof is retryable and writes no completion.
Object upload is best-effort after accepted proof. Completion replay skips proof;
Idempotency-Key is validated but day claim, not key, owns exact-once completion.
Completion produces a marker `WorkoutLog`, not one log row per checkpoint set.
Do not double-count marker and checkpoint or assume history already contains
rich set execution.

Evidence: `tests/test_sprint14_workout_execution_contract.py` (shared authority,
replay, stale, owner, terminal, cross-transport tests);
`tests/test_mobile_workout_sessions_api.py` (restart, browser-started/native,
bounds, exact schema);
`tests/test_mobile_workout_sessions_pg.py`;
`tests/test_sprint14_workout_execution_reliability_pg.py`;
`tests/test_lp13_no_start_after_completion.py`;
`tests/test_lp13_completion_proof_fail_closed.py`.

## 4. Flutter execution map and shared owners

**FACT:** current code uses the existing Workout feature boundary.

| Step | Exact mobile owner | Behavior / TI touchpoint |
|---|---|---|
| Routes / composition | `lib/app/router/app_router.dart:WorkoutExecutionRouterSession`; `lib/app/composition/app_composition.dart:AppComposition.configured` | One long-lived coordinator, live repository, app-support file store. Add dependencies once; no new global route required. |
| HTTP | `lib/features/workout/data/live_workout_session_repository.dart:LiveWorkoutSessionRepository` | Auth epoch protected transport; V1 checkpoint headers/body; multipart completion; typed errors via `WorkoutSessionErrorMapper`. |
| DTO | `workout_session_api_dto.dart:WorkoutSessionEnvelopeApiDto`, `WorkoutSessionApiDto`, `SetCheckpointApiDto`; mapper + codec | Exact key allow-lists at envelope/session/checkpoint/set. Unknown values fail closed. Rich fields cannot be appended to V1 responses. |
| Domain | `domain/workout_session.dart`, `workout_checkpoint.dart`, `workout_session_commands.dart` | Immutable canonical projection, full draft, frozen request. Put optional execution context in V2 domain/codec; no UI-owned authority. |
| Coordinator | `domain/workout_checkpoint_coordinator.dart:WorkoutCheckpointCoordinator` | Single `_sessions.checkpoint` send site, one writer, `_inFlight` frozen command, canonical baseline and `_localDraft` separate, max writer rounds 4. |
| Ack / retry | `_runWriter`, `_reread`, `_adoptCanonical` | Exact command retained across retry; account epoch/read generation fence ack application; newer reads supersede old responses and trigger reread. Do not mutate frozen payload after dispatch. |
| Restart | `recover`, `_recover`; `workout_execution_record.dart`; `data/file_workout_execution_store.dart` | Canonical-first `/current`; referenced read settles in-doubt completion; restore pending draft only at compatible revision/identity. Matching committed frozen snapshot settles lost acknowledgement; conflicting state drops pending edits and shows conflict. |
| Persistence | `FileWorkoutExecutionStore` | Schema 1, capacity 8 accounts, serial queue, JSON index via rename, app-private support directory, optional completion image file. It is recovery metadata, no token store. |
| UI | `presentation/state/workout_session_controller.dart:WorkoutSessionController`; `active_workout_screen.dart:ActiveWorkoutScreen` | Typed reps/load merge before toggle (#42); optional inputs must preserve this path. Existing display ticker reads coordinator elapsed, no per-set rest timer. |
| Finish | `coordinator.complete`, `_drainForTerminal`, `_settleTerminal` | Freeze edits → drain/ack checkpoint → complete at latest canonical revision. Failed final checkpoint prevents completion. Rejected proof restores editing without terminalizing. |
| Surface refresh | `WorkoutExecutionRouterSession` callback wiring | Terminal mutation refreshes existing Today/Plan controller owners. Insights do not add a competing refresh owner. |
| Account / lifecycle | `lib/app/app.dart` account transitions; router `noteLifecycle`; coordinator `noteAccount`, `noteBackgrounded`, `noteForegrounded` | Epoch reset on switch/logout; canonical recovery on new account; background persists/flushes dirty draft and pauses elapsed; foreground rereads then flushes. |
| Erasure | `AppComposition.configured` registration with `AccountScopedDataErasure` | Workout store cleared on confirmed deletion. Logout currently drops memory but retains account-scoped recovery records; do not describe it as disk erasure. TI note cache must clear on logout/switch and deletion. |

RIR and tempo adherence belong to optional **set context** in draft/frozen command/
acknowledgement. Rest event observation belongs to coordinator-owned transient
state, then set context. Persistent notes use their own repository/cache, linked
by catalog ID, with no write through workout checkpoint. All new asynchronous
results obey existing account/read fences. TI-02 will touch coordinator, DTO,
codec, store and controller together; serialize review with any LP work in those
owners. Router/composition changes are minimal dependency wiring only.

Mobile proofs already exist in `test/architecture/workout_session_execution_boundaries_test.dart`,
`test/features/workout/domain/workout_checkpoint_coordinator_test.dart`,
`workout_checkpoint_resume_contract_test.dart`,
`test/features/workout/data/workout_session_browser_started_test.dart`,
`file_workout_execution_store_test.dart`, `test/app/workout_execution_router_session_test.dart`,
and `test/features/training/integration/native_training_golden_path_test.dart`.

## 5. P0 information model — PRODUCT DECISION

One canonical session owns base progress, optional execution context and an
immutable start prescription snapshot. Targets and actual values never share
fields. No new mandatory input, prompt, photo, confirmation, or completion gate.
Simple interaction remains weight → reps → done. Advanced input is progressive
disclosure; capture omissions are normal, not errors.

Future server-owned `prescription_data` snapshot: schema 1, canonical exercise
order, exercise IDs, sets, `target_reps:{min,max}|null`,
`target_load_kg:{min,max}|null`, `target_rir|null`, `target_tempo|null`,
`planned_rest_seconds|null`, plus `source_plan_lineage`/`source_mutation_version`
where available and bounded provenance `structured|legacy_parsed|unavailable`.
At start: parse only exact numeric rep/range syntax and `_rest`'s numeric grammar;
load/RIR/tempo remain null because current plans have no structured authority.
Preserve existing display reps/rest/cues in existing plan projection. Never parse
RPE or tempo out of `not` with an LLM or infer target load from actual load.
Native/browser starts capture through the same authority; existing sessions have
null prescription, not a fabricated snapshot of today's replacement plan.
Exact prescription envelope is `{"schema_version":1,"source_plan_lineage":null,
"source_mutation_version":null,"exercises":[]}`. Each exercise has exactly
`exercise_id`, `sets`, `target_reps`, `target_load_kg`, `target_rir`, `target_tempo`,
`planned_rest_seconds`, `provenance`. Provenance is an exact object with keys
`target_reps`, `target_load_kg`, `target_rir`, `target_tempo`, `planned_rest_seconds`,
each one of the tokens above. Max 32 unique ordered exercises, sets 1–20;
if a prescription cannot meet those bounds, snapshot is unavailable rather than
changing existing start behavior. Entire snapshot max 65536 canonical UTF-8 bytes.
Null lineage/version reflect unavailable source; a malformed partial snapshot
projects null, not inferred targets. Exercise/set membership validation keeps its
existing authority even when the optional prescription snapshot is unavailable.
Set target defaults are exercise-level; per-set prescriptions are deferred.
Targets' bounds: reps 0–1000 with min ≤ max; loads 0–1000 kg at 0.1 kg precision
with min ≤ max; rest 0–86400 seconds; RIR/tempo as below. None changes generator.
TI-01 exposes null typed targets where current plans lack them. A future upstream
structured-prescription change requires a separate reviewed contract; it is not
silently added to TI-01.

## 6. RIR — PRODUCT DECISION

Use optional semantic **tokens** `"0"|"1"|"2"|"3"|"4_plus"` for both
`target_rir` and `actual_rir`, in separately named fields. JSON null means not
recorded; `"0"` means no repetitions in reserve. No prose, fractional values,
negative values or guesses. `4_plus` is an ordinal top bucket, never number 4
for averages. This matches launch selection 0/1/2/3/4+, avoids fake precision
at high reserves, and permits a versioned exact scale later without changing
these semantics. A 0–10 integer domain would falsely treat uncertain high-RIR
estimates as precise. Provider RPE prose does not become target RIR.

All context object fields are present/nullable in V2; absence of whole entry
means no context, normalized to all-null. V2 null explicitly clears a field;
no prompt is required to produce null. Acknowledged values survive restart;
actual RIR participates in V2 semantic fingerprint. Values can be entered only
for a completed set; reopening invalidates its execution context. RIR trend
uses ordinal comparisons, paired non-null observations, never imputes skipped
input or averages the top bucket.

## 7. Tempo — PRODUCT DECISION

Typed target union, optional:

```json
{"kind":"phases","eccentric_seconds":3,"bottom_pause_seconds":1,
 "concentric_seconds":1,"top_pause_seconds":0}
```

Each phase integer 0–10; positive sum required. `2-0-2` normalizes to four named
phases with top pause 0 **only when a future structured prescription source
supplies it**. Alternative `{"kind":"cue","cue":"controlled_eccentric"}`;
other allowed cues `pause_at_stretch`, `explosive_concentric`. One cue per target;
no arbitrary text, arrays, or undocumented ordering. Kind-specific exact keys.
Existing note prose remains display-only and does not supply this target.

Execution `tempo_adherence` is nullable token
`as_prescribed|faster|slower|lost_control`. `as_prescribed`, `faster`, `slower`
require a non-null canonical target; `lost_control` records self-report even
without target. No exact actual phase timer in launch. Semantic cue targets
permit `as_prescribed`/`lost_control`; faster/slower require phase target, because
speed relative to an unspecified cue is ambiguous. No set input is mandatory.
Post-launch exact measured tempo requires another tagged schema, not overloading
adherence. Nothing here is movement assessment, video analysis, or injury advice.

## 8. Rest — PRODUCT DECISION and measurement limitation

**FACT:** prescribed rest already projects as display text + nullable seconds
(`mobile_training._rest`). Browser has presentation countdown helpers
`static/workout_draft.js:deriveRestDurationMs`, `shouldStartRest`,
`remainingRestMs`, `extendRestDeadline`, `terminalRestEvent`; `static/plan_workout.js`
owns an in-memory deadline, Skip/extend and next-set editor. The deadline is not
persisted execution. Flutter currently only has workout elapsed time, paused in
background. Neither supplies historical actual rest.

**INFERENCE:** without a set-start signal, zero-extra-tap logging cannot observe
true end-of-set→start-of-next-set rest. Completion taps include the next set's
execution and logging delay. Calling that actual physiological rest would be
incorrect. Freeze explicit measured semantics rather than pretend precision.

Launch context `actual_rest` is null or:

```json
{"seconds":182,"method":"completion_gap","quality":"foreground_contiguous"}
```

`seconds` integer 0–3600. This is the time from the immediately previous set's
forward completion tap to the **current** set's forward completion tap, for
adjacent indices of the same exercise, observed by the same process/account/
session without discontinuity. Store on following/current completed set (index
≥1), never on the completed predecessor. UI/evidence must label it **between-set
logging interval**, not exact rest. It is client-observed evidence accepted by
the server, not server-observed physiology. Monotonic difference rounded down
seconds; zero means two observed taps in same second, null means unavailable.

Observer resets on reopen, non-forward edits, out-of-order completion, exercise
switch, conflict, terminal command, account transition, process restart, and
background/inactive/hidden. Foreground begins a new observation chain; no wall
clock reconstruction across kill/background. Leaving screen also resets chain
because intervening activities are unknown. No missing interval is zero-filled.
Automatic countdown expiry/skip/extend does **not** mean next set started; skip
neither records zero nor clears an otherwise valid foreground completion gap.
Pause/resume of any presentation timer does not pause the observation clock;
explicit workout pause or lifecycle interruption invalidates it. Above 3600 →
unavailable, not clamped. Set one and final unpaired predecessor have no rest
observation. Acknowledged closed intervals survive restart; unfinished anchors
do not. Browser V1 supplies no new rest data.

Future precise method `set_boundary` would need an already naturally observable
set-start event; it is not supported in launch schema. **P1 cannot generate
`short_rest_confound` from `completion_gap`**, even with large differences. It
can expose the logging-interval evidence descriptively. True rest confound is
reserved and unavailable until comparable `set_boundary` evidence exists. This
is a concrete launch cut, not an invitation to add a mandatory Start Set tap.

## 9. Persistent exercise notes — PRODUCT DECISION

**FACT:** no user×catalog-exercise note authority exists in `app/models.py`.
Plan `not` is prescription cue; WeeklyLog/WeeklyCheckIn/FeedReport notes serve
other scopes and cannot be reused. A new entity is required in TI-01.

Future `ExerciseNote`: internal PK; `user_id` FK cascade; catalog `exercise_id`
validated by `exercise_catalog.resolve_exercise`; unique `(user_id,exercise_id)`;
nullable text ≤500 Unicode code points and ≤2000 UTF-8 bytes; integer revision
0–999999999; updated_at. No catalog SQL FK exists: validate catalog membership
server-side. No raw PK/user ID in projection. Normalize CRLF to LF and trim
outer whitespace; empty→null. Reject other control characters except newline/
tab; reject oversized input, never silently truncate. Store plain text, render
escaped. Notes are private; excluded from facts, insights, Coach context, logs,
analytics and fingerprints of workout execution.

Future GET/PUT `/api/v1/training/exercises/<exercise_id>/note` with native bearer
owner. Envelope `{"note":{"exercise_id":"ex_...","text":null,"revision":0,
"updated_at":null}}` for no row. PUT exact body `{"text":"Seat position 4"}`,
required If-Match note revision. CAS upsert from 0 handles unique race; accepted
write +1. Identical normalized text at current or immediately previous revision
returns current without bump (safe retry); different text at stale revision→409
reread. Tombstone row retained after clear to prevent ABA. No workout revision
or plan mutation. At note revision maximum, refuse further changed writes with
bounded revision-exhausted error; identical retry/read remains usable. Both note routes require P0 readiness; dark→404 using existing
`TRAINING_SESSION_NOT_FOUND` envelope, before rate-limit work. Note errors use
existing mobile envelope: `TRAINING_NOTE_INVALID` 400/nonretryable for bounds,
`TRAINING_NOTE_REVISION_CONFLICT` 409/nonretryable/reread,
`TRAINING_NOTE_UNAVAILABLE` 503/retryable; no text echoed. Note responses carry
`Session-Resolution` for actionable errors following repository convention.
Owner read isolation matches absent foreign note; exercise ID
is catalog public identity. Do not use session idempotency keys for notes.

Mobile cache: per-account/exercise/revision memory cache, explicit optional save,
no persistent prose cache or offline outbox at launch. Failed/offline save keeps
editing view and says unsaved; never reports persistence before ack. Clear
memory on switch/logout; account epoch fences responses; register erasure if
later disk caching is introduced. Acknowledged note persists server-side across
plan regeneration, workouts, and app restart. No note revision guard on workout
completion; note failure cannot block logging or completing a session.

## 10. Server-derived training facts

**FACT:** `app/services/training_history/queries.py:fetch_workout_entries` reads
owner-scoped legacy `WorkoutLog`, converts UTC to Istanbul days, excludes
completion markers unless requested. `training_history.analysis` and
`training_progression.analysis` compute existing weekly volume/progression from
those rows. They do not have checkpoint RIR/rest/tempo, canonical per-set linkage,
or a guarantee that name-based historical logs are comparable. Reuse calendar
helpers and marker predicate; do not silently change these existing consumers.

**PRODUCT DECISION:** new TI facts read final acknowledged checkpoints of owned
COMPLETED sessions, including completed V1 sets where valid. Never ACTIVE drafts,
ABANDONED sessions, plan sets, or PumpCheck scores as performed sets. Final
snapshot is immutable at terminalization; capture prescription at start for
future comparable targets. Missing/corrupt checkpoint means unavailable execution
coverage; no reconstruction from current plan. Legacy name-only logs remain
separate provenance and are not merged into per-set comparisons or double-counted.

| Fact | Exact computation / missing semantics |
|---|---|
| Weekly set exposure | Count completed set identities per catalog exercise in Monday–Sunday Istanbul window, report coverage/missing sessions. No muscle-equivalent set weights. |
| Exercise frequency | Distinct completed-session workout dates with ≥1 completed set of exercise. |
| Training frequency | Distinct completed training dates; PumpCheck day claims can provide completion count with provenance separately, not imply logged set count. |
| Rep volume | Sum non-null reps of completed sets; null reps excluded, observed/eligible counts accompany total. |
| Load-volume | Sum reps×weight_kg only for paired non-null reps and load; unit kg·reps. Zero stays zero; null never zero; bodyweight/system load not inferred. No physiological workload claim. |
| Recent performance / previous sets | Last ≤8 completed sessions within 56 Istanbul days, ordered by completed_at then opaque ref internally for ties; grouped catalog exercise + comparable index/context. |
| Progression | Bounded paired observed load/reps comparisons defined in §14, not estimated 1RM or strength score. |
| Rest trend | Same-method measured intervals only; launch label logging-interval trend. No true-rest trend or confound without boundary measurement. |
| RIR trend | Paired optional ordinal buckets with explicit sample/coverage; unknown remains unknown. |

Use workout_date for exposure windows and completed_at for historical ordering;
exclude inconsistent cross-date completion data rather than re-date a session.
All windows derive server-side via `app.timeutil`, not client-selected day keys.
Query max 56 days/8 sessions per exercise/32 exercises/20 sets; no arbitrary
history dashboard. Unavailable reads are typed unavailable, not empty history.

## 11. Checkpoint evolution comparison

Scores: 5 safest/easiest; 1 poorest. Scores are **INFERENCE** from exact allow-lists,
CAS architecture and cache/replay design; they are not performance measurements.
B below includes D's storage/projection policy; bare V2 replacement is unsafe.

| Criterion | A modify V1 | B explicit V2 alone | C separate context write | D negotiated V2 + same-row context |
|---|---:|---:|---:|---:|
| Backward compatibility | 1 | 3 | 4 | 5 |
| Browser compatibility | 1 | 3 | 4 | 5 |
| Native compatibility | 1 | 4 | 4 | 5 |
| Replay semantics | 1 | 4 | 2 | 5 |
| Fingerprint stability | 1 | 4 | 3 | 5 |
| Revision model | 3 | 4 | 2 | 5 |
| Concurrency | 3 | 4 | 2 | 4 |
| Implementation simplicity | 4 | 3 | 2 | 3 |
| Migration simplicity | 4 | 4 | 2 | 3 |
| Rollback | 2 | 3 | 3 | 4 |
| Launch safety | 1 | 3 | 2 | 5 |

A breaks closed parsers and V1 fingerprint identity. Bare B does not say how V1
full replacement preserves invisible fields. C creates partial execution,
multiple acknowledgements, completion barriers, and either a competing revision
or needless conflicts. D retains a single command/revision and freezes the
V1-aware preservation policy. Selected: **explicit negotiated V2, implemented
with D's same-session atomic sidecar**. This is not a separate context API.

## 12. Selected checkpoint V2 contract — frozen

Use the existing native routes with request header
`AxisAI-Workout-Contract: 2` to negotiate **session response** and checkpoint
command, independently of existing browser `contract_version` identifiers.
Absent header means exact V1. Accepted values are exactly `1`, `2`; unknown/
malformed→typed invalid request. V2 requested while dark/unsupported→404 with
no write. Return `Vary: AxisAI-Workout-Contract` and private/no-store responses.
No additive JSON fields in any V1 response, including reads/start/resume/complete.
Header is sent on every V2 lifecycle/read request;
V2 validation reuses `InvalidSessionRequest`/existing 400 code, revision and
idempotency conflicts reuse existing 409 codes and `Session-Resolution`.
No TI-only error mapper becomes another lifecycle classifier; completion still carries
required If-Match and existing multipart shape. Browser continues V1; future
browser V2 must use the same domain implementation, not another validator.

Discovery: future authenticated GET
`/api/v1/training/workout-execution-capabilities`, exact response:

```json
{"contract_version":1,"checkpoint_versions":[1,2],
 "execution_context_enabled":true,"insights_enabled":false}
```

P0 OFF offers `[1]`, false/false; old backend 404→V1. Capability read failure→V1
for new clean commands, without pretending previous V2 edits were saved. Scope
capabilities to authenticated account/epoch, refresh on foreground/404. Do not
add capabilities to closed Today/Plan/session V1 DTOs. Do not automatically
reserialize an in-flight V2 command as V1. Resolve with canonical reread, surface
unsaved optional context and require a newly frozen command/key for downgrade.
This preserves the simple path without falsely acknowledging dropped context.

V2 checkpoint body exact shape:

```json
{"checkpoint":{"current_exercise_index":0,"elapsed_seconds":120,
 "exercises":[{"exercise_id":"ex_barbell_back_squat","sets":[
 {"index":0,"completed":true,"reps":8,"weight_kg":60.0}]}]},
 "execution_context":{"schema_version":1,"sets":[
 {"exercise_id":"ex_barbell_back_squat","index":0,"actual_rir":"2",
  "tempo_adherence":null,"actual_rest":null}]}}
```

Base checkpoint retains exact V1 schema/bounds. Context is a **bounded full
snapshot** of all non-null context the V2 client intends to retain, not patches.
Missing context entry clears that set's context on V2; omitted/null entire
`execution_context` is invalid V2. All entry keys above required. Empty `sets`
clears all context; all-null entries normalize away. Max 640 unique context
entries, sorted by canonical exercise order/index; each refers to a completed
base set. Unknown keys/versions/identities, duplicate entries or invalid values
reject entire command. Context canonical JSON max 131072 bytes; V2 body max
262144 bytes after transport decoding. Base retains 65536 limit. Do not change
shared global body limits without checking existing upload contracts.

V2 projection exact envelope `{"session":V1_SESSION,
"execution_context":{"schema_version":1,"sets":[]},"prescription":null}`;
start/read/current/resume/checkpoint/abandon use it. `/current` with no active
session has `session:null`, empty context, prescription:null. Completion adds
existing `completion` field; no other extra keys. Context sets use exact input
shape; prescription uses §5 structured snapshot (no note prose). Revision remains
`session.revision`. V1 projection excludes both extra envelope fields.

Future persistence on **WorkoutSession**:
* Keep `checkpoint_data` V1-only JSON and existing revision/key/request fingerprint.
* Nullable `execution_context_data` JSON text, schema 1, bounded entries plus
  internal base-set anchors (and preceding-set anchor for actual_rest) and
  `bound_revision`.
* Nullable immutable `prescription_data` JSON text; no writes by the client.
No second workout row or context revision. Internal anchors store the exact
normalized `(completed,reps,weight_kg)` of each associated base set. Projection
and facts use context only when `bound_revision == checkpoint_revision` and
anchor matches; invalid bindings become missing, never attached by index alone.

Atomic acceptance computes canonical request fingerprint **before** preservation
and before persistence. V1 domain/hash stays byte-for-byte unchanged. V2 SHA-256
domain `axisai:training-workout-checkpoint:v2\0` over canonical object containing
both base checkpoint and normalized context. Targets are server snapshot, not
client fingerprint input. Store request fingerprint/key regardless of merged
sidecar; do not hash the post-merge state as request identity. Normalize nullable
values/order/all-null entries identically before comparison. New context values
change V2 identity; absent entry and all-null entry are semantically identical.
V1 and V2 domains differ even when context is empty: same key across contract
versions conflicts, not false replay. Last-key-only replay model remains.

V1 write at declared latest base: preserve context for unchanged completed sets;
remove context when set absent, uncompleted, or reps/normalized load changed.
Elapsed/selection-only changes preserve all anchors. Rest also invalidates if
its preceding set's base changes/disappears/uncompletes. Stamp retained context
bound_revision to next revision. V2 fully replaces valid context for submitted
base; no opaque merge of V2 omissions. The CAS includes base and sidecar changes
in the **same UPDATE** and transaction; context computed from row at base r,
update WHERE r prevents applying a merge from another revision. Replay uses
request fingerprint only and returns current negotiated projection. No extra
revision increments, background sidecar writes or context-only endpoint.

Revision saturation must reject advancement at max; do not issue revision
1000000000 or unparseable authority. The exact max is 999999999; max−1→max allowed,
max→write refused with reread/terminal revision-exhausted reason. V1 must retain
working normal revisions; document saturation repair separately as §26 describes.

## 13. Browser / mobile coexistence — required falsifiable examples

1. Browser V1 saves base r1. Mobile reads negotiated V2 at r1, context empty.
2. Mobile V2 saves same base + RIR at r2. Browser read sees original V1 shape,
   r2, load/reps/completed; no context/notes/targets leaked into its parser.
3. Browser V1 updates elapsed only with base r2 → r3. Mobile V2 read retains RIR
   attached to unchanged set, new bound revision; unchanged V1 hash semantics.
4. Browser changes reps of set 0 → r4. Context of set 0 invalidated; actual_rest
   on set 1 invalidated because preceding base changed. Other sets retained.
   This is explicit invalidation of no-longer-applicable evidence, not accidental
   deletion of every invisible field. Removing an exercise removes its context.
5. Browser at stale r2 after mobile r3 loses without touching any columns.
6. Mobile V2 intentionally nulls RIR → r5; a later V1 elapsed update cannot
   resurrect it. Same key retry of V2 at stale r4 replays r5 if still last key.
7. Concurrent V1 and V2 same-base: exactly one full combined state wins. Finish
   on either transport requires latest acknowledged revision. No terminal write
   can gain context after completion, and no note write changes revision.
8. P0 turned OFF preserves stored context; V1 adapters continue anchor-aware
   preservation even when UI/capability is dark. Flag cannot bypass this plumbing.
   Binary rollback to pre-TI code can leave stale sidecar; bound_revision fails
   closed on re-enable. Core workout still works; such stale context is missing.

## 14. P1 deterministic facts and diagnostic eligibility

Future `app/services/training_intelligence/{queries,models,facts,diagnostics,policy,projection}.py`.
Impure owner/window query layer; pure functions for facts, comparison and policy;
transport-only native insight route. No provider call needed for correctness.
Results are not medical findings, fatigue estimates, readiness scores, or causes.

Frozen comparator v1:
* Compare current completed session to immediately previous eligible session
  within 56 days, same owner/catalog exercise/weekday slot/plan lineage/mutation
  version and same structured prescription snapshot. Require at least 2 paired
  completed indices with non-null reps/load. Only indices below the captured prescribed set count are eligible. No ordinal
  slot→exercise-name join. Current execution has no working/warm-up role: do not
  label these as verified working sets or compare added sets beyond prescription.
  Unobserved warm-up/technique changes remain explicit limitations.
* Current and previous eligible-set index sets must match. No comparison across
  exercise replacement, variation, differing targets, unknown load convention,
  missing prescription, cardio/duration targets, or bodyweight unknown load.
  Same catalog ID is necessary, not sufficient. Missing physical setup/ROM/set-role is
  acknowledged limitation; output describes logged performance only.
* Equal normalized loads across all paired sets: compare sum reps. Equal reps
  across all paired sets: compare loads per index with no contradictory direction.
  Strict all non-decreasing + ≥1 increase→`performance_improved`; converse→
  `performance_declined`; equal load/reps at every paired index→stable;
  mixed tradeoffs→`not_comparable`. No formula trading reps for kg, no estimated
  1RM, no unsupported confidence score. Stable means exact logged stability,
  not statistical equivalence.
* RIR requires ≥2 paired non-null RIR buckets across identical indices. All
  non-increasing + ≥1 decrease→effort_increased; converse→effort_decreased;
  all equal→effort_stable; mixed→effort_mixed. Contextual self-report, not proof
  of physiological effort. Compare separately from external performance.
* Tempo: ≥2 paired non-null compatible adherence/target observations; changed
  categorical distribution→`execution_quality_context_changed`; no causal
  correction of performance. Missing target limits usable categories (§7).
* Rest: launch completion gaps allow descriptive trend only. Reserved
  `short_rest_confound` requires future same-method precise boundaries on ≥2
  paired indices, previous median ≥30 s, decrease ≥30 s **and** ≥20%, with no
  missing/interruptions. Unsupported method→unavailable. No release workaround.
* Exposure: previous two complete 7-day Istanbul windows, exercise identity and
  checkpoint coverage. ≥2 extra sets **and** ≥20% increase relative to nonzero
  prior count→`volume_increase_context`; ≥1 extra trained date→frequency context.
  Zero baseline or incomplete logging coverage→insufficient data. This explains
  changed recorded exposure; it does not prove more training or fatigue.

Require explicit `missing_prescription|missing_execution|missing_rir|
missing_tempo|rest_method_unsupported|insufficient_pairs|plan_changed|
mixed_performance|incomplete_coverage` missing codes per fact. No imputation.
V1 sessions can contribute exposure/performance if server prescription snapshot
exists; old rows without snapshot cannot be paired as known prescription. No
backfill from replacement plans. Exposure and optional context do not gate base
logging. No insight generated from pending local draft.

## 15. Minimum-effective-intervention policy

Deterministic `policy.py` consumes facts, emits at most **one** primary lever per
session, with one bounded observation instruction. Priorities: unsupported or
insufficient evidence→no recommendation; verified rest confound (future
eligible method)→restore prescribed rest; declared tempo loss with stable target
and paired evidence→follow prescribed tempo; isolated effort increase with
comparable target RIR and no unresolved confounds→aim at prescribed effort;
exposure increase with decline but ambiguous effort/quality→hold current approach
and observe. No automatic volume reduction, deload, exercise change, load change,
frequency change, or multi-lever package. Tie/conflicting facts→observe, no causal
diagnosis. Launch reliable logging gaps cannot trigger a rest intervention.

Recommendations never call `plan_mutation.service`, `plan_replacement`, or
`mobile_training_generation`. Existing safe authority is lineage/version-gated
replacement (`app/services/plan_replacement.py`, shared `plan_owner_lock`) and
row-locked journaled `app/services/plan_mutation/service.py`. Post-launch proposals
must enter those reviewed authorities explicitly; no P1 implicit mutation.
Coach may later consume the bounded insight projection with source refs and
missing reasons. No note text, raw history dump or provider prose is required;
Coach handoff in TI-04 is explicit user navigation/context using existing Coach
boundary, not an architecture replacement or a write tool.

## 16. Training Insight API — future frozen projection

GET `/api/v1/training/workout-sessions/<session_reference>/training-insight`,
owner-authenticated, P1/P0/workout readiness gated, no mutation. Active session→
insufficient_data with `session_not_completed`; absent/foreign session→404.
Same mobile error envelope as existing training APIs on read failure; retryable
503 is not insufficient history. Successful response exact shape:

```json
{"training_insight":{"contract_version":1,"ruleset_version":"ti_rules_v1",
 "session_ref":"opaque_owned_reference","checkpoint_revision":3,
 "state":"available","kind":"execution_quality_context_changed",
 "title_key":"training_insight.execution_quality_context_changed",
 "evidence":[{"metric":"tempo_adherence","unit":"token",
   "previous":"as_prescribed","current":"lost_control",
   "paired_sets":2,"previous_session_ref":"opaque_previous_owned_reference"}],
 "missing_data":[],"recommended_action":{"lever":"tempo",
   "action":"follow_prescribed_tempo","observe":"next_comparable_session"}}}
```

For insufficient-data responses title_key is the fixed
`training_insight.insufficient_comparable_history`, kind is
`insufficient_comparable_history`, empty evidence is valid. For not-comparable,
kind is null and title_key is `training_insight.not_comparable`.
State `available|insufficient_data|not_comparable`; kind nullable closed fact
vocabulary from §14, plus `exposure_frequency_context` and
`insufficient_comparable_history`. Max 1 primary insight, max 8 evidence entries,
max 12 unique missing codes (the §14 vocabulary plus `session_not_completed`,
`history_unavailable`, `no_completed_sets`). Query failure returns HTTP error,
not `history_unavailable` success; that token denotes excluded corrupt historical
rows. No other missing tokens are accepted in v1. `title_key` selected from server-owned fixed mapping
of kind, not arbitrary provider string. Unit/metric tagged exact union: reps/int,
weight_kg/number, set_count/int, frequency_days/int, rir/token, tempo/token,
logging_interval_seconds/int; future precise rest requires next schema. Values
null only with corresponding missing code; numeric evidence uses identical
metric/unit typing on previous/current and only finite nonnegative bounded
values (reps ≤20000, weight ≤1000, sets ≤12800, frequency days ≤56,
logging interval ≤3600). RIR and tempo use their closed enums; `paired_sets`
means pairs supporting that evidence, not inferred population size; evidence includes paired_sets 0–20,
nullable previous opaque owned reference. No raw PK, user ID, notes, provider
payload, percentage confidence or free-form action. Unsupported vocabularies fail
closed in mobile. Insufficient/not comparable state has recommended_action:null;
no-data is explicit, not a fabricated stable state. Multiple fact axes can appear
as bounded evidence; policy chooses only one lever. Response is deterministic
for fixed completed snapshot, historical window and ruleset; no persisted
insight entity needed in P1. Source refs/revision make evidence inspectable;
UI does not expose database identifiers.

## 17. Feature flags and readiness

**FACT:** `app/feature_flags.py:ROLLOUT_FLAGS` is the lifecycle inventory;
`app/config.py` derives/reads keys; strict parser accepts `0|1` only. Records
carry owner/default/dependencies/observability/prerequisites/success/abort/
rollback/review date. Existing `FITX_WORKOUT_SESSIONS_ENABLED` gates transports.

**PRODUCT DECISION:** TI-01 registers default-OFF
`FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` (P0); TI-03 registers default-OFF
`FITX_TRAINING_INSIGHTS_ENABLED` (P1). Each uses full existing lifecycle record,
owner/review date determined at implementation, no second registry. P0 requires
workout sessions readiness and applied schema; P1 requires P0 ON plus ruleset
and qualification. Explicit P1 ON/P0 OFF makes readiness invalid and P1 absent;
do not silently advertise P1. Valid rollout states: OFF/OFF; ON/OFF; ON/ON.

Mobile feature visibility is server capability AND local release kill switch,
never local boolean alone. Use existing `lib/core/config` composition conventions;
future build booleans `AXISAI_TRAINING_EXECUTION_CONTEXT_ENABLED` and
`AXISAI_TRAINING_INSIGHTS_ENABLED`, default false, latter implies former. No global
routing redesign. P0 off hides advanced controls/notes and uses V1. P1 off means
no insight requests or UI. Turning P0 off suppresses P1 atomically; capability
refresh takes precedence over cached enabled UI. Preservation plumbing runs
regardless of flag; disabling feature must not destructively clear rich state.

## 18. Observability and privacy

Extend bounded session metrics convention (`workout_session.metrics`,
`runtime_metrics.increment`) with fixed events: `context_accepted`,
`context_stale`, `rir_prompt_shown`, `rir_provided`, `rir_skipped`,
`rest_observed`, `rest_unavailable`, `tempo_deviation`, `insight_generated`,
`insight_insufficient`, `insight_unavailable`. RIR skipped only if a shown optional
prompt was dismissed, not whenever null; no prompt forced for analytics. Accepted
counter increments only on new CAS commit, not replay. Client events show UI
interaction, never server acceptance. Rest event means logging interval with
method tag, not exact rest. Failure metrics are best-effort and cannot block
execution. Fixed contract/ruleset/method/reason dimensions only; request ID for
trace logs, never high-cardinality IDs in metric dimensions.

Never log notes, session/ref/account identifiers, full workout/request payload,
raw provider responses, photo metadata/prose, credentials, client secrets or
sensitive user input. Notes excluded even from Coach handoff and crash breadcrumbs.
No AWS access/mutation required to design or test this foundation. Observation
storage remains canonical session context, analytics are not workout evidence.

## 19. Migration and rollback analysis

TI-00: **no migration**. TI-01: additive nullable session text columns for
execution_context_data/prescription_data and ExerciseNote table with ownership
FK/unique/check revision. Follow Alembic graph, add at actual current head, do
not infer head from lexicographic filename order. Existing native execution
migration is `f5a6b7c8d9e0_add_workout_session_native_execution.py`.
No rewriting old checkpoint JSON/hashes, no backfilled actual RIR/rest/tempo,
no mandatory defaults, no WorkoutLog dual-write, no plan schema migration.
Existing rows: null context and prescription; V2 projection normalizes empty
context; old notes absent revision 0. Add owner/completed-date query index only
if measured query plan requires it. No speculative materialized facts table.

Expand→deploy server dark→prove V1→enable P0 only after cross-repo checks. Application
rollback to older binary keeps additive schema, V1 continues, stale sidecar
bindings become unavailable when upgraded again. Operational flag rollback
keeps preservation code and schema, preferred. Never drop context/note columns
as emergency rollback. A downgrade migration is reviewed only after backup,
feature-off/data-retention decision; tests on disposable DB only, never staging.
TI-03 has no required persistence migration; facts/insight derived read-only.
Mobile store schema 2 needs explicit v1→v2 local decoding preserving frozen V1
command version/key and acknowledged baseline; never upgrade pending command's
wire representation in place. Rollback older app may ignore schema 2 recovery,
so canonical server remains recovery source; pending optional unsent data is not
promised across binary downgrade. TI-02 must prove kill/restart at schema boundary.

## 20. Cross-repo ownership matrix

| Owner | Backend | Mobile | Serialization boundary |
|---|---|---|---|
| Session lifecycle / completion | `workout_session`, `workout_completion`, `models.WorkoutSession` | coordinator barrier / repository | Any LP lifecycle correction before TI-01 rebased tests; no TI completion rewrite. |
| Canonical contract | `checkpoint.py`, `execution.py`, `queries.py`, native/browser projections | DTO/mapper/codec/frozen command | Backend fixture freeze before TI-02 merge. |
| Prescription | start authority + `mobile_training._project_exercise` + catalog | V2 projection display | No provider/generator expansion in lane. |
| Notes | owner/exercise table + native transport | Workout note repository and memory cache | Auth epoch + deletion; no account-lifecycle implementation changes. |
| Timing | server context validation only | coordinator observer + lifecycle adapters | No UI-local timer authority. |
| Facts / policy | new pure deterministic service | Training Insight renderer | TI-03 golden fixtures before TI-04 merge. |
| Global shared owners | registry/config only when required | composition/router minimal wiring; Today/Plan existing callbacks | LP owner review and explicit serialization of overlapping files. |
| Release | human deployment/flag authority | physical iPhone release candidate | TI-05 joins before physical golden path. |

## 21. Parallel lane and LP integration

MAIN MOBILE LP LANE remains the launch authority and proceeds independently.
TI lane: TI-00 → TI-01 contract → TI-02 capture → TI-03 facts → TI-04 display →
TI-05 qualification. TI-03 can develop alongside TI-02 using frozen backend
fixtures; TI-04 can develop with fake fixtures alongside TI-03. Neither may merge
against guessed contracts. TI-06/07 reserved for evidenced qualification repairs,
not automatic additional features.

**OPEN QUESTION / source blocker:** no `LP21`, `LP22`, `LP23` or equivalent numbered
definitions exist in either fetched main's tracked docs (searched hyphenated/
spaced forms). Existing launch documents are Sprint15 hardening/readiness and
mobile training PR6/7, not the current numbered LP plan. Requested source from
owner; do not invent exact gate assignments from historical prompts. Active LP
PRs also require current owner coordination; main proves completed LP13/14,
not all work currently in flight.

Binding release requirements regardless of numbering: TI-05 must join before
**physical golden-path qualification**; feature-off core candidate must still
pass. **Release freeze** closes material feature expansion. **Final launch gate**
requires qualified exact backend/mobile SHAs and flag pair, or TI remains OFF.
The final verified mapping to LP21/22/23 is a release-documentation blocker,
not permission to delay core beta or change the LP roadmap.

## 22. Exact PR sequence and per-PR gates

| PR / repository / branch | Exact scope and non-scope | Dependencies / likely files | Migration / contract | Tests / merge gate / rollback |
|---|---|---|---|---|
| TI-01 backend `feat/ti-01-execution-context` | Implement §§5–13/17 P0, note authority, dark capability, V1 preservation; no diagnostics, UI, provider, generation or mutation | TI-00 accepted; `models.py`, Alembic, `workout_session/{checkpoint,execution,queries,service}`, native projection/routes, capabilities/note service, feature registry/config; browser adapter projection check | Additive migration; explicit V2 + notes/capability; V1 exact | All §23 backend P0 including real PG races and migration upgrade; frozen fixtures; V1 browser regression green. Merge dark. Rollback P0 OFF/P1 OFF; retain schema. |
| TI-02 mobile `feat/ti-02-execution-capture` | Optional RIR/tempo, auto logging-gap observation, persistent-note UI/save, negotiated V2/recovery; no insights or global routing redesign | TI-01 stable fixtures; `features/workout` DTO/domain/coordinator/controller/screens/store, minimal app composition/config wiring | Local recovery schema 2; accepts V1 fallback + V2 | All mobile P0 tests, zero-extra-tap path, account/capability failure; backend deployed contract verified before merge. Dark local gates; fallback V1. |
| TI-03 backend `feat/ti-03-diagnostic-facts` | Pure facts/comparison/policy/insight read projection; no LLM diagnosis, no writes/mutations, no exact-rest claim | TI-01 merged; new `services/training_intelligence`, native insight route, feature registry/config; reuse calendar/catalog helpers | No required migration; bounded insight v1 | Hand-calculated golden fixtures + eligibility/missing/owner/read-failure/determinism/privacy + P1/P0 readiness. Merge dark after P0 contract stable. P1 OFF independently. |
| TI-04 mobile `feat/ti-04-training-insights` | Existing Training/Workout surface insight renderer, evidence/missing state, explicit Coach handoff through existing boundary; no Progress dashboard, new global destination, plan write | TI-02 + TI-03 fixtures; workout/training presentation/data/domain; existing Coach context adapter only if compatible | No server migration; exact insight DTO v1 | P1 disabled=no calls/UI; unsupported token fail closed, stale/account fence, evidence golden renders; merged backend contract green. Dark gate / P1 OFF. |
| TI-05 both `test/ti-05-launch-qualification` | Cross-repo fixture contract matrix, reproducible evidence/device script and physical iPhone qualification; no feature expansion or silent fixes | TI-01–04 merged; `tests`, mobile `test`/integration evidence, docs/release checklist | No production contract/migration | §23 physical + PG + exact candidate SHA/flag matrix, LP source mapping verified; human device evidence required. Failure leaves affected flags OFF; core LP proceeds. |
| TI-06 backend `fix/ti-06-qualification-repair` (reserved) | Only evidenced launch-blocking backend P0/P1 repair; defect-specific non-scope recorded | TI-05 failing artifact; exact affected owner | None assumed; contract/migration change requires updated freeze and rerun | Regression reproduces failure first; rerun affected cross-transport/PG qualification; rollout remains dark. |
| TI-07 mobile `fix/ti-07-qualification-repair` (reserved) | Only evidenced launch-blocking device/mobile repair; no UI expansion | TI-05 failing artifact, TI-06 if contract-related | None assumed | Reproduction + affected coordinator/account/device checks; exact candidate rerun. Feature OFF until pass. |

### Parallelism matrix

| PR | Develop alongside LP? | Merge alongside LP? | Shared-owner collision | Required serialization |
|---|---|---|---|---|
| TI-01 | Yes, isolated branch | Yes dark after lifecycle owners settled | backend session/completion/flags/models | Rebase latest LP lifecycle fixes; one contract merge/review owner |
| TI-02 | Yes with fixed fixtures | Conditional, after TI-01 contract + LP workout owners | coordinator/DTO/store/controller/composition | LP set logging/lifecycle fixes first; V2 fixtures + shared-owner review |
| TI-03 | Yes with TI-02 | Yes dark after TI-01 | flags/history helpers/native registry | P0 contract; flag readiness gate |
| TI-04 | Yes with fixtures | Conditional after TI-02/03 | workout UI/Coach adapter | contracts before renderer; no concurrent same-owner presentation edits |
| TI-05 | Prepare scripts yes | Evidence changes yes; qualification serialized | release candidate/device/flags | joins LP before physical golden path; candidate freeze |
| TI-06/07 | Only evidenced repairs | Conditional under freeze repair policy | defect-dependent | targeted repaired candidate requalification, no roadmap expansion |

## 23. Test strategy and falsifiable acceptance

No future behavior tests added to production test suite in TI-00. Existing shared
architecture tests already pin one validator/one authority; TI-00 adds a small
current V1 semantic fixture guard (exact canonical bytes/hash, richer-key
rejection, null versus zero, revision bounds). It must fail if those actual
contracts change, not merely if this document changes. Do not weaken existing
route/schema allow-lists to accommodate TI-00.

### Backend implementation obligations

* V1 entire existing API/architecture/browser suite unchanged; exact key sets
  and canonical V1 fixture hash stable. Max-size, invalid NaN/bools/unknown keys.
* V2 round-trip RIR 0/top/null, adherence validation, rest zero/null/bounds,
  context unknown/duplicate/orphan IDs and unsupported method rejection.
* Semantic fingerprints: shuffled exercise/context/set ordering, float encoding,
  all-null versus absent entries equal; changed meaningful context differs;
  V1/V2 same key differs; preservation state not in request fingerprint.
* Lost-response replay, displaced key stale, same key conflicting payload,
  revision overflow refusal, no activity/column mutation on rejected request.
* Browser V1→mobile V2→browser V1 examples §13, unchanged preservation,
  per-set edits/removal/reopen/preceding-rest invalidation; response exact schemas.
* PostgreSQL barrier races V1/V2 same base, duplicate V2 once, context vs completion,
  note CAS creation/tombstone/retry, owner isolation, start vs linked/session-less
  completion day lock. Inspect persisted base/context/revision/terminal effects.
* Restart canonical recovery and completion latest acknowledged revision including
  context-only logical edit; no checkpoint after terminalization.
* Disposable DB migration fresh/upgrade from current head, null legacy rows,
  FK deletion/cascade, binary rollback stale binding, reviewed downgrade loss.
* Facts hand-calculated partial logging/markers/null-zero/ordinal top bucket,
  mixed load/reps, dates, missing prescription, target changes, bounded query,
  no double counts; insight deterministic output, read failure≠empty history.
* Flag matrix, privacy whitelist, no provider/plan writer dependency; non-vacuity
  by falsifying protected behavior in controlled temporary edits then restoring.

### Flutter implementation obligations

* Exact V1/V2 DTO fixtures, unknown enums/version/extra keys fail closed, finite
  bounds, Python-compatible normalized weights, old backend 404 fallback.
* One writer, frozen version/payload/key, newer draft separated from ack,
  retry preserves bytes; conflict reread drops inapplicable draft/context;
  finish drains latest acknowledgement. No false context ack on flag downgrade.
* Optional controls hidden until disclosure; no missing RIR/tempo/note save
  blocks done/finish; existing typed-weight/reps preservation remains green.
* Deterministic injected monotonic timing for gap 0, two adjacent completions,
  skipped countdown, expiry/extension independence, >3600 unavailable,
  edit/reopen/switch/out-of-order reset. No fake exact-rest label.
* Background/foreground/inactive gap invalidation; workout elapsed semantics
  unchanged. Kill/restart restores ack, not unfinished anchor; schema 1 pending
  V1 recovery preserved; unknown/corrupt store fails closed.
* Account A→B in-flight fences for checkpoint/note/insight; logout memory clear,
  note cache clear, old recovery scoped safely; deletion cleanup registered.
* P0 OFF simple V1 path, P1 OFF no reads/UI, invalid ON/OFF pair suppressed,
  failed capability fetch/unknown response never assumes V2 availability.
* Widget/integration tap count simple path equals baseline, no advanced modal on
  done; insufficient insight renders bounded explanation with no action/mutation.

### Physical iPhone qualification (TI-05, not claimed in TI-00)

Record exact OS/device/build/backend SHAs/flag pair and redacted evidence:
simple workout (same taps), advanced compound optional RIR, tempo-focused
isolation with structured-target fixture, disconnected checkpoint and note save,
app kill before/after acknowledgement, background interval invalidation,
foreground canonical reread, completion proof rejection/unavailability/retry,
browser V1/mobile V2 shared session where test account/server permits,
account switch/isolation/deletion, deterministic qualified insight and
insufficient history, P0/P1 off fallbacks. Structured tempo fixture is test data,
not a generator change. Device evidence must not expose note/photo prose.
Real device work starts only on authorized isolated staging, synthetic account,
with human AWS login if needed; no AWS operations in TI-00. Record unsupported
scenario as unqualified, never infer device behavior from widget tests.

## 24. Rollout, rollback and launch cut lines

1. Merge additive backend dark; verify V1 with P0 OFF. Backend unstable → mobile
   P0 cannot merge. Contract drift reopens freeze; no client guesses.
2. Merge mobile P0 dark after stable contract; qualify P0 ON/P1 OFF. P0 can ship
   independently if P1 facts or renderer miss gates. Null targets do not justify
   provider/generator expansion to rescue insight availability.
3. Merge P1 dark and qualify ON/ON with genuine evidence. Many insufficient-data
   responses at launch are expected; never fabricate diagnosis for demo coverage.
4. Device/replay/owner/compatibility qualification failure → affected feature OFF,
   preserve core candidate and continue LP beta. P0 failure disables both; P1
   failure disables P1 only. No emergency schema drop.
5. Before physical golden path, decide whether qualified TI joins candidate or
   remains dark. After release freeze: no material feature expansion, only
   launch-blocking repairs in bounded TI-06/07 with regression/requalification.
6. Final gate records SHAs/flags/migrations/capabilities and verified LP numbering.
   Human controls merge/deployment/activation; this PR activates nothing.

## 25. Known risks and cut-line severity

| Priority | Risk | Concrete mitigation / ship condition |
|---|---|---|
| P0 | Strict V1 parsing broken by additive rich fields | Negotiation; unchanged V1 fixture/response allow-lists; entire V1 regression green |
| P0 | Context detached from edited/reopened base sets | Same-row atomic CAS + anchors/preceding-rest dependency + §13 PG matrix |
| P0 | Account crossover or local command downgrade falsely acked | Existing epoch fences, versioned frozen command, no silent V2→V1 reserialization |
| P0 | Completion/new-session races regress | Preserve LP13 day/session lock order and run real PG tests before TI-01 gate |
| P0 | Missing current LP21/22/23 source | Verify mapping before release integration; core beta remains independent |
| P1 | Rest confound claimed from completion gap | Unsupported at launch; explicit descriptive label/method; no rest recommendation |
| P1 | Few comparable sessions / null typed targets | Insufficient data first-class; no backfill from plan prose; P0 independent |
| P1 | Note prose leaks / competing note edits | Separate owner table/cache/CAS; privacy whitelist; no Coach note context |
| P1 | Local schema downgrade loses unsent optional data | Server-first recovery; explicit unsaved state and schema-upgrade tests |
| P2 | Need richer target prescription or actual tempo later | Versioned post-launch source contracts; no expansion during freeze |

## 26. Separate correctness findings and genuinely open decisions

**Existing defect candidate (not fixed here):** parser accepts revision max
999999999 while `queries.advance_checkpoint` unconditionally writes base+1.
At saturation it can issue a revision that neither completion nor checkpoint
parser accepts. Reproducible by synthetic row/command; operationally remote
because one user would need ~1 billion accepted writes. Track separately as a
bounded revision-exhaustion repair with its own regression/PR; it is not an
unannounced feature fix in this discovery PR. TI-01 must settle the saturation guard before
contract acceptance. Do not treat this as authority to silently change V1 in TI-00.

**Existing baseline test defect (not fixed here):** direct command
`node --test tests/js/workout_checkpoint_client.test.js` has 22 passes/1 failure
at `an acknowledged checkpoint hydrates a separate page instance after reload`.
`static/workout_draft.js:hydrateExercises` now sets `repsExplicit:true` for saved
non-null reps, but the deep-equality expectation at test line 550 omits it.
The base load/reps/completed/revision values match. Both files are unchanged from
fetched backend main. Track expectation review separately; do not silently alter
browser hydration or weaken an allow-list in TI-00. This prevents claiming an
all-green browser baseline and must be reconciled before TI-01's browser merge gate.

**Measurement limitation, not existing defect:** Flutter currently cannot measure
true actual rest without a set-start signal. The launch decision is explicit
completion-gap evidence with no rest-confound diagnostic; exact physiological
rest remains outside launch unless a naturally observed start event is proven
without extra mandatory interaction.

**OPEN QUESTION:** authoritative current LP21/22/23 document and active shared-owner
work schedule. Exact gate numbering cannot be asserted from current main.
**OPEN QUESTION (release ownership):** dates/cohorts for flag activation, physical
iPhone evidence owner and final qualified build. This does not block frozen P0
API implementation; it blocks enabling/joining release.
**OPEN QUESTION (post-launch only):** authoritative structured target RIR/tempo/
load source. Launch nulls are frozen; solving this is not prerequisite to P0.
No unresolved launch numeric bounds, RIR scale, context placement, note ownership,
V1 merge semantics or P1 mutation permission remain in this contract.

Explicit non-scope TI-00–05: video/CV, injury diagnosis/prediction, wearables,
HealthKit/recovery/readiness scoring, autonomous deload/weekly replacement,
training generator/Coach redesign, Progress dashboard, nutrition, account lifecycle
redesign, localization overhaul, premium/social/gamification/notifications or
arbitrary history analytics. Future richer autoregulation remains post-launch.

## 27. Validation and final readiness

See [ti-00-evidence/validation.md](ti-00-evidence/validation.md) for exact commands,
counts, environment limitations and tested SHAs. Characterization is current
behavior only; future schemas above are frozen normative design reviewed in PR,
not claimed implementation. No physical device or AWS validation is claimed.

**READY WITH BLOCKERS:** another engineer can begin TI-01 from this frozen design
without rediscovering canonical ownership, optional context placement, V1/V2
projection/preservation/replay, note scope or deterministic P1 rules. Exact
LP21/22/23 mapping remains unverified; the existing browser test expectation
failure remains an external baseline repair; TI release integration stays blocked until
its source is inspected. TI-00 changes only docs and current-contract tests.
