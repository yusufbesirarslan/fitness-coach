# Mobile Training PR4A: Durable Idempotent Plan Generation Design

**Date:** 2026-09-01
**Status:** Approved for implementation planning
**Base:** `origin/main` at `2cfd008e1e8ab5f320a7bf810c485d0a4759a894`
**Branch:** `mobile-training-pr4a-idempotent-plan-generation`

## 1. Purpose

Add one bearer-authenticated native command:

```http
POST /api/v1/training/plans
Idempotency-Key: <opaque key>
Authorization: Bearer <access credential>
```

The command turns one authenticated first-plan intent into one canonical,
durably committed `TrainingPlan`. Generation, structured-output repair,
semantic validation, exercise resolution, injury annotation, persistence, and
the native response remain server-owned. Flutter never receives an uncommitted
candidate and never performs a correctness-sensitive generate-then-save pair.

The core invariant is:

```text
one authenticated user intent
  = one durable generation operation
  = at most one canonical persisted first plan
```

## 2. Scope

PR4A owns the native request contract, durable operation ledger, PostgreSQL
concurrency authority, canonical generator extraction, first-plan persistence,
typed mobile errors, migration, tests, architecture guards, and contract
documentation.

It does not modify Flutter, browser UI, Nutrition, Pump Check, Progress, Coach,
Feed/social, workout sessions, workout completion, Adaptive Coaching
activation, or native-auth rollout defaults. The existing browser generation
and save endpoints remain behaviorally compatible.

## 3. Existing Authorities Reused

- Authentication: `require_mobile_auth` and `g.mobile_user`.
- Native error envelope: `mobile_error`.
- Preference vocabulary: `training_generation.preference_contract` and the
  metadata published by `mobile_training.preference_contract()`.
- Capability decisions: `training_generation.capability.require_supported`.
- User fitness inputs: authenticated `User` plus the latest owned
  `UserSession`; neither identity nor experience is accepted from the body.
- Provider routing and recovery: `app.services.ai._heavy_complete`.
- Provider capacity: the existing mobile AI concurrency gate and model gate.
- Weekly entitlement: the existing `premium` training-plan quota authority,
  extracted so its mutation can participate in operation claiming.
- Generation: the existing `training_generation` prompt, classifier, program
  context, bounded completion budget, extraction, repair, schema validation,
  and semantic validation.
- Exercise and injury authority: `canonicalize_plan_exercises`, then
  `annotate_injuries`, in that order.
- Current-plan selection: `today_facts.get_active_plan`.
- Native response: the same projector used by
  `GET /api/v1/training/plans/current`.

No native route imports or calls `app.blueprints.training`.

## 4. Request Contract

The JSON body is an exact object containing the canonical generation fields:

```json
{
  "gun_sayisi": 3,
  "ekipman": "spor_salonu",
  "odak": "tum_vucut",
  "sure": 45,
  "kardiyo_tipi": "yok",
  "kardiyo_gun": 0,
  "kardiyo_sure": 20,
  "kardiyo_yogunluk": "orta",
  "antrenman_tarzi": "genel",
  "odak_hedef": "genel",
  "injuries": ""
}
```

All eleven fields are required. Unknown fields, non-object JSON, alternate
types, browser-only fields, and client-supplied user/session identifiers are
rejected. Integer fields accept JSON integers only; native input does not use
the legacy browser parser's numeric-string compatibility. Token fields must be
canonical identifiers published by the GET preference contract. `injuries` is
a string no longer than 2,000 characters after trimming and may be empty.

The strict DTO delegates token/value authority to the canonical preference
parser after enforcing presence, exact keys, native types, and size bounds. It
then invokes the canonical capability evaluator before any operation record,
quota reservation, lock, or provider call.

The authenticated user's latest `UserSession` remains a generation
prerequisite. Goal, fitness level, measurements, and other classification facts
come from authenticated server state rather than the request.

## 5. Idempotency Transport and Fingerprint

`Idempotency-Key` is required and follows the repository token convention:

```text
^[A-Za-z0-9._:-]{8,64}$
```

It is scoped by authenticated `user_id`; it is never logged or returned.

The request fingerprint is SHA-256 over UTF-8 canonical JSON with:

- a domain/version prefix, `axisai:training-plan-generation:v1`;
- sorted object keys and compact separators;
- the normalized values of every semantic request field;
- no token, timestamp, request ID, HTTP metadata, or user ID.

The user is a separate uniqueness dimension. Formatting and object key order do
not affect the digest; any material preference change does.

## 6. Durable Operation Model

Add `TrainingPlanGenerationOperation` with these bounded fields:

- internal integer primary key;
- `user_id` foreign key with cascade delete and owner index;
- `idempotency_key` up to 64 characters;
- `request_fingerprint` as a 64-character hex digest;
- `status`: `IN_PROGRESS`, `GENERATED`, `SUCCEEDED`, or `FAILED`;
- `attempt_count` positive integer;
- optional temporary `candidate_plan_data` bounded by the canonical plan size;
- optional candidate score;
- optional successful `training_plan_id` as a soft reference;
- optional successful `plan_lineage_id`;
- `quota_reserved` boolean and optional bounded `quota_week`, so crash recovery
  can distinguish an already-charged execution from a fresh claim;
- optional bounded public error code, HTTP status, and retryable flag;
- `created_at`, `updated_at`, and `completed_at` timestamps.

Constraints and indexes:

- unique `(user_id, idempotency_key)` is the final same-key arbiter;
- one partial unique index on `user_id` while status is `IN_PROGRESS` or
  `GENERATED` prevents two active first-plan operations for one owner;
- an owner/status lookup index supports active-operation checks;
- status and bounded-field check constraints fail closed on corrupt rows where
  portable repository conventions permit them.

The model is added to the canonical user-child deletion list. It stores no
bearer credential, prompt, raw provider response, request body, or native
response blob.

`candidate_plan_data` is temporary crash-recovery material, not a second plan
authority. It contains only the fully validated canonical persistence document.
It is cleared when the operation reaches `SUCCEEDED` or `FAILED`.

## 7. Generator Extraction

Extract the smallest reusable lower-level result from
`generate_training_plan_payload`:

```text
GeneratedTrainingPlanCandidate
  document
  score
  exercise_context
  existing bounded diagnostic metadata needed by the browser payload
```

The lower-level generator performs the existing pipeline exactly once:

```text
canonical preferences
  -> capability check
  -> authenticated features and classification
  -> bounded provider execution and one existing repair opportunity
  -> structured and semantic validation
  -> canonical exercise resolution
  -> injury annotation
  -> typed candidate
```

The browser wrapper continues to construct its existing payload and signed
exercise-context token from this candidate. Its route, response, quota behavior,
and separate save flow remain unchanged.

The native command calls the lower-level candidate function directly. It does
not sign, serialize, parse, or trust an exercise-context token. The native path
also does not persist posted injury metadata as an intermediate side effect;
the body is command input, while plan persistence is the command's only domain
write.

## 8. Command Flow

1. Authenticate with `require_mobile_auth`.
2. Parse the exact DTO and validate capability.
3. Resolve the authenticated user's required generation prerequisites.
4. Calculate the semantic fingerprint.
5. Read the owner/key operation:
   - different fingerprint: idempotency conflict;
   - `SUCCEEDED`: replay from the persisted plan;
   - `FAILED`: replay the stored typed failure;
   - `GENERATED`: continue at persistence without provider execution.
6. Attempt a nonblocking PostgreSQL advisory lock derived from authenticated
   `user_id`. Failure returns typed `TRAINING_PLAN_GENERATION_IN_PROGRESS` with
   `Retry-After`; the request never waits on another provider call.
7. Under the advisory lock, repeat all durable checks. This second look closes
   pre-check races.
8. Refuse a current plan and refuse another active operation before provider
   execution.
9. Atomically create or resume the operation and reserve weekly quota once.
10. Enter the existing AI capacity/provider controls and call the canonical
    generator.
11. Commit the validated persistence document to the operation as `GENERATED`.
12. In one final transaction, lock the operation, re-check that the user still
    has no current plan, insert one `TrainingPlan`, set the operation to
    `SUCCEEDED`, attach its soft result identity, clear the candidate, and
    commit.
13. Build the response through the shared native current-plan projector.
14. Release the advisory lock in `finally` without logging its key material.

The advisory lock uses a dedicated PostgreSQL connection in autocommit mode, so
no application transaction or row lock is held during provider I/O. A bounded
AI concurrency slot bounds the number of retained connections. SQLite remains
supported for ordinary local/unit tests; correctness-critical concurrency tests
run against PostgreSQL and prove the production path.

## 9. Quota and Rate-Limit Ordering

Malformed, unsupported, conflicting, replacement-refused, successful replay,
failed replay, and in-progress responses do not reserve another weekly provider
quota.

The quota reservation is associated with the durable operation and occurs once
inside its claim transaction. A resumed `IN_PROGRESS` or `GENERATED` operation
does not reserve again. A terminal generation failure refunds the reservation
in the same transaction that records `FAILED`. A successful operation retains
the charge.

The normal mobile request limit still protects the HTTP surface. Heavy AI
capacity and provider limits are entered only for a fresh or crash-recovered
`IN_PROGRESS` execution, never for `GENERATED`, `SUCCEEDED`, or `FAILED` replay.

## 10. Persistence and Projection

The native persistence document contains the generated canonical program,
weekly summary where supported, and the server-created exercise-context block.
It is inserted into the existing `training_plan` table with:

- authenticated ownership;
- a new opaque lineage generated by the model default;
- mutation version `0`;
- canonical score;
- normal creation metadata.

No existing plan is deleted, archived, updated, or replaced. A final
current-plan check inside the success transaction protects against another
writer that committed while generation was running.

Expose the PR2 row projector as a shared supported function. Both POST and GET
use it; neither defines a second DTO. A successful POST followed by GET current
must agree on plan lineage, mutation version, timestamps, score, workout
references, days, and canonical exercises. Existing `GET /api/v1/today`
naturally observes the inserted row through the current-plan authority; the
command performs no Today write.

New creation and successful replay both return `201 Created` with the same body
shape as GET current: `{ "plan": <canonical projection> }`. The response includes
`Idempotency-Replayed: true|false`; the body does not expose operation identity,
database IDs, keys, or fingerprints.

## 11. State and Replay Semantics

| Durable state | Same key and fingerprint | Provider call |
|---|---|---:|
| absent | claim and execute | yes |
| `IN_PROGRESS`, live lock held | typed 409 in progress | no |
| `IN_PROGRESS`, lock acquirable after crash | resume execution | yes |
| `GENERATED` | persist staged candidate | no |
| `SUCCEEDED` | return canonical 201 replay | no |
| `FAILED` | replay stored typed failure | no |

Same key with a different fingerprint always returns typed 409 before provider
execution and never changes the stored fingerprint.

A definitive post-claim failure consumes the key. Its replay returns the same
bounded error classification. If the error is retryable, the client may start a
new logical attempt with a new key after it has received that definitive result;
repeating the old key only resolves ambiguity and cannot spend again.

Pre-execution request, capability, prerequisite, existing-plan, entitlement,
and rate-limit failures do not create an operation record.

## 12. Failure Mapping

| Failure | HTTP | Code family | Retryable | Provider called | Plan changed |
|---|---:|---|---:|---:|---:|
| missing/invalid bearer | 401 | existing auth code | per auth contract | no | no |
| malformed body/unknown field | 400/422 | invalid request/preference | no | no | no |
| unsupported/conflicting preferences | 422 | canonical preference code | no | no | no |
| missing/invalid key | 400 | invalid idempotency key | no | no | no |
| same key/different fingerprint | 409 | idempotency conflict | no | no | no |
| live operation | 409 | generation in progress | yes | no additional | no |
| existing current plan | 409 | replacement refused | no | no | no |
| quota exhausted | 402 | existing premium limit | no | no | no |
| request/provider rate limited | 429 | training rate limited | yes | no | no |
| capacity unavailable | 503 | generation busy | yes | no | no |
| provider timeout/unavailable | 503 | generation unavailable | yes | yes | no |
| parse/repair exhausted | canonical generator status | canonical output code | as canonical | bounded | no |
| schema/semantic/exercise rejection | 422 | canonical output code | no | bounded | no |
| candidate staging/persistence failure | 503 | persistence unavailable | yes | maybe | no partial plan |
| projection fails after commit | 503 | Training read unavailable | yes | no additional | plan committed |

Raw provider, SQL, lock, and token exceptions never enter the response.

## 13. Crash and Concurrency Safety

- Before provider: the durable `IN_PROGRESS` row and quota reservation can be
  recovered by the same key after the dead worker releases its advisory lock.
- During provider: a live duplicate cannot acquire the lock and does not invoke
  the provider. If the worker dies, the lock releases and the same key may
  restart the bounded generator. Exactly-once remote inference across a crash
  inside an external provider call is impossible without provider-supported
  idempotency; the design chooses safe retry with no committed plan.
- After provider, before staging: the same crash rule applies.
- After `GENERATED`: retry persists the staged candidate without provider work.
- During final persistence: plan insert and `SUCCEEDED` transition are one
  transaction, so neither can commit alone.
- After commit, before response: retry observes `SUCCEEDED`, invokes no provider,
  creates no plan, and returns the canonical projection.
- Same user/key/fingerprint: one live provider execution; duplicates receive
  in-progress or replay.
- Same user/key/different fingerprint: conflict, zero second execution.
- Different users/same key: independent operation rows and advisory locks.
- Same user/different keys: the owner advisory lock and active-operation partial
  uniqueness prevent concurrent first-plan executions; after one success, the
  other intent is replacement-refused.

## 14. Migration

The migration is additive and idempotent for the repository's create-all-first
boot path. It creates the operation table, owner/key uniqueness, active-owner
partial uniqueness, lookup indexes, foreign key, and bounded columns only when
the table is absent. Downgrade drops only this new table and its indexes.

No existing column or table is renamed or removed, so code rollback remains
compatible after an automatic schema upgrade.

## 15. Security and Privacy

- Bearer auth is mandatory; browser cookies and Flask session fallback fail.
- `user_id` comes only from `g.mobile_user`.
- Every operation and plan lookup is owner-scoped.
- No request field can select another user or persisted plan.
- Request fields, key, fingerprint, prompt, provider output, bearer token, and
  candidate plan are not logged.
- Logs use bounded event names, public error codes, attempt/status, provider
  invocation count, and request ID only.
- Response bodies contain opaque lineage/workout references and canonical
  exercise IDs, never integer database IDs or operation metadata.
- JSON, strings, keys, candidate data, and list sizes are bounded before costly
  processing or persistence.

## 16. Tests

TDD coverage is split into focused suites:

1. DTO and fingerprint tests: exact keys, required fields, JSON types, canonical
   values, bounds, stable ordering, semantic differences, and no auth metadata.
2. Command unit tests: all state transitions, failure consumption, quota
   ordering, provider counters, staged-candidate recovery, and exact replay.
3. HTTP contract tests: bearer-only auth, cookie rejection, error envelopes,
   response status/header, ownership, existing-plan refusal, POST-to-GET equality,
   and Today convergence.
4. Generator reuse tests: browser payload remains compatible; native uses the
   same bounded repair, exercise authority, equipment checks, and injury order.
5. Side-effect guards: no WorkoutSession, WorkoutLog, Pump Check, XP, quest,
   Progress, Coach, Feed, or Nutrition writes.
6. PostgreSQL barrier tests: concurrent duplicate, fingerprint conflict,
   cross-user same-key independence, different-key owner serialization,
   successful replay, and response-loss retry. Tests use events/barriers and
   provider detonators, never sleeps for correctness.
7. Migration, schema-drift, architecture, mobile-auth, Training generator,
   native Training reads, and Today regressions.

The final validation runs targeted suites, PostgreSQL concurrency, migration
upgrade, schema drift, compile/import checks, formatter/lint configured by the
repository, `git diff --check`, and the full default pytest suite. Exact fresh
counts are reported.

## 17. Architecture Guards

Static and behavioral guards prove:

- the POST route carries `require_mobile_auth`;
- no mobile module imports the browser Training blueprint;
- the canonical generator, capability evaluator, exercise authority, injury
  annotator, quota authority, and PR2 projector are reused;
- durable operation uniqueness exists in model and migration;
- completed replay cannot reach provider code;
- current-plan protection occurs before provider and again in the final
  transaction;
- no workout-session, completion, Pump Check, Progress, Coach, Feed, or
  Nutrition dependency is introduced;
- native-auth and workout-session defaults are unchanged.

## 18. Parallel Workstream Safety

The intended production changes are limited to Training generation/mobile
service files, `app/models.py`, `app/cli.py`, one additive migration, and focused
tests/docs. Current Sprint 13 branches do not overlap those Training surfaces.
The older Pump Check history worktree touches `app/models.py` and migrations, so
the implementation will preserve its unrelated model content and re-check the
current Alembic head before generating the migration.

No Flutter/mobile repository or browser presentation file is modified.

## 19. Rejected Alternatives

**Hold a row transaction across provider I/O.** This naturally serializes
duplicates but retains a database transaction and connection through bounded but
long external calls, violating the repository's provider/transaction discipline
and increasing pool starvation risk.

**Use only a durable lease.** A lease allows crash recovery, but expiry while a
slow provider call is still alive can permit overlapping execution. A live
PostgreSQL advisory lock gives a precise worker-liveness signal without an open
application transaction.

**Introduce an asynchronous job system.** A queue and polling contract could
support long-running generation but adds another runtime, client state machine,
and deployment dependency. PR4A can meet its bounded synchronous contract with
the existing provider limits and a durable ledger.

## 20. Acceptance

PR4A is ready only when the command is bearer-only, strict, canonically
validated, durably idempotent, PostgreSQL-concurrency-safe, non-replacing,
atomically persisted, replayable after response loss, projection-convergent with
GET current and Today, isolated from workout/Pump Check writes, fully migrated,
and green in the required focused and full regression suites with no P0 or P1
review findings.
