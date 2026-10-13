# TD-01 PR2 — selected-exercise historical performance HTTP

Owner authorization: 2026-10-13, **Q3=A, PR2 only**. This delta freezes the reviewed
`td01-pr2-preparation-2026-10-13/PR2-CONTRACT-PROPOSAL.md` for the backend transport,
under [ADR 0002](adr/0002-performed-training-authority.md) and
[accepted TD-01](superpowers/specs/2026-10-10-td-01-historical-performance-design.md).
It supersedes their provisional PR2 sketches for this slice. Independent review,
exact-head CI and human squash merge remain required.

## Preflight and decisions

Fresh backend main: `71787cb9eadcd4bfbc81975f5ce67c28d7aa4fe2`.
Isolated branch: `feat/td01-pr2-exercise-history-http`.
First consumer: selected catalog exercise in native ActiveWorkoutScreen, opened
lazily, one exercise read, no fan-out/prefetch or modification of current inputs.
PR3 is not implemented or authorized.

**Retain separate capability GET.** Mobile main
`0d8e15c3492ae3fc28d53233ededc55bca94789e` independently inspected: execution
capability decoder uses exactKeys with contract_version, checkpoint_versions,
execution_context_enabled, insights_enabled. Adding history breaks that v1
consumer; history has different dependencies. Existing capability shape stays.

Open lanes #342/#383/#400/#430/#431 were inspected. None owns the two new modules,
history tests, guards, hooks or new records. #342 also edits `.env.example`, but
only AI provider/spend sections at lines 54–88; PR2 adds a separate default-OFF
history entry after the training flags. This shared inventory file has no
overlapping change. Registry-derived configuration avoids modifying #342's
`app/config.py`. Adding PG tests to the existing history suite avoids changing
the CI workflow edited by #430/#431.

**Verified contract delta:** shared before-request maintenance can commit during
a GET. Exempt exactly `mobile_api.exercise_history_capabilities` and
`mobile_api.read_exercise_history` from rollover/purge in `app/hooks.py`, proven
by tests. Other endpoints keep maintenance. Existing auth infrastructure retains
its behavior, including revocation on rejected credentials; history adds none.

## Frozen v1 transport

- GET `/api/v1/training/exercise-history-capabilities`
- GET `/api/v1/training/exercises/<exercise_id>/performance-history`

Both use Bearer auth. Readiness: history flag AND sessions flag; mobile-auth
startup gate additionally controls blueprint registration. OFF: app 404 before
view auth, possibly HTML. Shared principal/limiter hooks and OPTIONS/405 remain.
Ready without valid Bearer: existing 401. Default throttles and blueprint
Cache-Control: no-store apply. No ETag, cache or workout negotiation header.
Capability performs zero history SELECTs and reports readiness, not coverage:
`{"contract_version":1,"exercise_performance_history_enabled":true,"history_contract_version":1}`.

Typed serialization, exactly closed keys at every level; the four committed
[golden fixtures](../tests/fixtures/td01_pr2/available.json) pin v1 shape/values.
Future fields require a reviewed version strategy.

| Object | Exact keys |
|---|---|
| Envelope | contract_version, exercise_performance_history |
| History | exercise_id, state, window, latest_valid_occurrence_ref, occurrences, coverage |
| Window | start_date, end_date, timezone |
| Latest reference | session_ref, checkpoint_revision |
| Occurrence | session_ref, checkpoint_revision, workout_date, completed_at, sets |
| Set | index, reps, weight_kg |
| Coverage | complete, scanned_sessions, excluded_cross_date, excluded_missing_completed_at, unavailable_sessions, scan_limit_reached, occurrence_limit_reached |
| Unavailable marker | workout_date, completed_at, reason |

ISO day strings, naive stored UTC rendered RFC3339 Z, timezone Europe/Istanbul.
Latest reference is null iff no occurrences, otherwise exactly the first item's
reference. It means **latest valid recorded sets**, never guaranteed latest
performed session. Any gap needs persistent incomplete-coverage wording.

| State | Meaning |
|---|---|
| available | Occurrences, complete scanned window |
| available_with_gaps | Occurrences, unreadable candidate or scan bound |
| no_history | No occurrences, complete scanned window |
| undetermined | No occurrences, incomplete scanned window |

PR1 remains the sole selection authority: owned COMPLETED final checkpoints,
canonically accepted completed:true sets only; whole-row refusal on parser
rejection. Null stays unknown, zero stays zero. Inactive catalog identity stays
readable. Date exclusions are counted without becoming gaps. Marker reasons:
checkpoint_missing, checkpoint_invalid, session_invalid; only session_invalid
allows null workout_date or completed_at. No marker exposes a session reference.
No prescription, WorkoutLog, notes, context enrichment, legacy derived output,
AI, recommendations, persistence or second interpretation is introduced.
Unexpected exceptions including deeply nested JSON recursion refuse HTTP success.
No_history never means no training.

## Isolation, bounds and errors

Owner captured from g.mobile_user.id only; path exercise ID only. Request query,
body and other headers cannot override owner, anchor, window, limit or cursor.
Foreign rows change no facts, gaps, counts or latest pointer.

One owner-scoped history SELECT: five columns, COMPLETED, 57 server-derived day
keys, canonical byte tie order, LIMIT 64. Eight occurrences ×20 sets; all scanned
candidates classified after the eighth. Exactly 64 rows conservatively means
incomplete coverage; occurrence cap alone is not a gap. PG route instrumentation
records three auth SELECTs plus one history SELECT, no writes/flushes/commits/locks
on valid reads. No schema inspection, provider calls for history, N+1 or AI calls.

**65,536-byte compact UTF-8 success JSON** backstop, measured on the exact bytes
sent. Never truncate facts or markers. Reference strings 1–64 characters, strict
integer revision 0–999,999,999. Wire types, counts/collections and PR1 invariants
checked. Maximal 8×20 facts +56 markers and 64 markers/no facts qualified below
bound. PR1 parser/query remain unchanged; out-of-band checkpoint byte/recursion
risk remains accepted. LIMIT bounds returned rows, not lifetime rows visited.

| Condition | Response |
|---|---|
| Any valid four-state projection | 200 |
| Malformed ID | 400 TRAINING_HISTORY_INVALID_REQUEST, retryable false |
| Well-shaped unassigned ID | 404 TRAINING_HISTORY_EXERCISE_NOT_FOUND, retryable false |
| SQL/catalog/invariant/unexpected/oversized projection failure | 503 TRAINING_HISTORY_TEMPORARILY_UNAVAILABLE, retryable true, Retry-After: 15 |
| Gate absent / throttle / other method | Existing 404 / 429 / 405 |

Errors exactly mobile_error error.{code,message,retryable,request_id}, fixed
messages, no facts/IDs/exception text. Map failures inside the view before the
auth-flavored catch-all. Rollback only for failure cleanup. No Session-Resolution.

## Guards, telemetry, lifecycle

H-A8 admits exactly two modules: route with one direct build_exercise_history
call on captured owner/path, serializer with model and invariant dependencies.
Exact import sets; aliases, dynamic bypasses, direct query and third callers
remain forbidden. Non-vacuity includes Coach/Today/Progress imports and candidate
loader bypass. H-A1–A6/A9/A10, H-R1/R2, parity and catalog guards preserved;
four-file PR1 package has zero diff from base.

FITX_EXERCISE_PERFORMANCE_HISTORY_ENABLED: default false, shipped_dark,
owner @yusufbesirarslan, review 2026-11-01. Registry parsing/config only;
dependencies FITX_WORKOUT_SESSIONS_ENABLED and MOBILE_AUTH_ENABLED; independent
of P0/P1. Flag/dependency loss makes both GETs 404. Host flag changes need config
reload, then code revert if needed; no data/schema/cache cleanup.

ExercisePerformanceHistory has only Event dimension with four states,
checkpoint_missing/checkpoint_invalid/session_invalid/completed_at_missing,
scan_limit_reached/occurrence_limit_reached, invalid_request/exercise_not_found,
read_failed/invariant_failed/catalog_unavailable. Best-effort, once per event per
request. Failure logs: category/request ID only. Source-defect warning: fixed
event/request ID only. No names, IDs, facts, tokens, exception text or high-cardinality
dimensions. Existing HTTP SLIs retained. Telemetry failures cannot change the
answer. Quantitative abort policy remains a separate enablement decision.

## PostgreSQL G9 evidence

Raw exact query, binds, six EXPLAIN (ANALYZE, BUFFERS) runs, version and fixture:
[1k](evidence/td01-pr2/g9-1000.json), [4k](evidence/td01-pr2/g9-4000.json),
[summary](evidence/td01-pr2/g9-summary.json). Local PG16/Docker Desktop,
MacBook Air M2/8 cores/8 GiB. First run after server restart, OS caches retained;
five warm runs. Target 1,000→4,000 completed daily rows (57 in window), 20 other
owners ×1,000 interleaved rows, 21 ACTIVE/21 ABANDONED, 2,254–2,305-byte
checkpoints; ANALYZE each fixture. Other-owner counts stay fixed across fixtures.

| Signal | 1k | 4k | Ratio |
|---|---:|---:|---:|
| Median warm execution ms | 1.108 | 1.499 | 1.353× |
| Shared hit+read pages | 1,003 | 1,236 | 1.232× |

Both use existing ix_workout_session_user_id Bitmap Index/Heap Scan, sort, LIMIT.
No Seq Scan or escalation trigger. **G9 PASS on this recorded host**, no index or
migration. Host-specific qualification evidence, not an SLO or rollout approval.

Reproduce only in disposable local PG16: opt in FITX_PG_CONCURRENCY_TEST=1 and
PG_TEST_DATABASE_URL. Run tests/qualification/run_td01_pr2_g9.py seed 1000
--output-dir <evidence-dir>, restart PG, then measure 1000 --output-dir
<evidence-dir>; repeat for 4000. Seed drops/recreates the synthetic schema.

## Delivery boundary

Local qualification on the submitted tree: **515 passed** (PR2 API/wire,
preserved PR1 selection/architecture/parity/catalog, auth/discovery inventories,
flag registry and maintenance/default-limiter tests); **13 passed** on real
PostgreSQL 16, including the original eight PG proofs and five PR2 route tests.
No PG skips; locale-aware default en_US.utf8 makes the C-collation test non-vacuous.
Maximal compact payloads: 14,536 bytes for 160 facts +56 markers; 6,724 bytes for
64 markers/no occurrences. See [payload evidence](evidence/td01-pr2/payload-bounds.json).

Focused contract/SQLite/architecture tests and real PG PR1+PR2 execution precede
submission. Require exact-head CI, independent review and human squash merge.
G7 staging pending separate authorization. No mobile PR3, RC-01 edit, staging or
production change, endpoint enablement, deployment or automatic merge.
G9 PASS alone never establishes release readiness.
