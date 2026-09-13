# Sprint 15 post-PR6 reconciliation and closure preflight

**Status: READY FOR INDEPENDENT SPRINT 15 POST-PR6 READINESS REVIEW.**
Implementation has converged in merged Flutter PR6; authenticated Android and staging evidence remains. This is **not** Sprint 15 closure or a soak GO. The [Sprint 15 discovery](2026-09-09-sprint15-pr1-mobile-launch-hardening-discovery.md) §17–23 remains the acceptance authority. No production activation or store distribution is authorized here.

## Provenance and evidence boundary

| Item | Observed 2026-09-12 | Limit |
|---|---|---|
| Backend `origin/main` | `f15d27b56aa8ba8620114ac621e0f7b2241fe5a2` | Fresh fetch; six commits after discovery merge `48deb1e` |
| Mobile `origin/main` and PR6 merge | `68e0ec5bb7cfa7d7c617d05440322fda005021b9` | Fresh fetch; merged PR6 is current main |
| PR6 CI | [Flutter CI run 34708886023](https://github.com/axisaiapp/axisai-mobile/actions/runs/34708886023), success at that SHA | Ubuntu tests/architecture scans and auth-OFF/auth-ON Android debug builds; macOS tests and unsigned simulator build. A CI APK is not a local staging APK |
| PR7 | Locally named `mobile-training-pr7-automated-integration` at PR6 SHA; no open mobile PR at inspection | Another agent owns the work. No unmerged branch state counted as evidence |
| Worktrees | Backend source and mobile source clean at inspection; this document is on isolated backend branch `docs/sprint15-post-pr6-readiness` | Mobile repository read-only |

PR6 evidence below is from merged code paths, not its proposal: `lib/app/composition/app_composition.dart`, `lib/features/workout/data/live_workout_session_repository.dart`, `lib/features/workout/domain/workout_checkpoint_coordinator.dart`, `lib/features/workout/presentation/{active_workout_screen.dart,workout_detail_screen.dart}`, `lib/app/app.dart`, the PR6 tests, and the CI run. All mobile paths refer to the mobile SHA above.

Backend contract drift after discovery does not touch `app/blueprints/mobile_api.py`, `mobile_training.py`, `mobile_workout_sessions.py`, `app/mobile_auth_middleware.py`, `app/services/mobile_workout_sessions/`, or `app/services/workout_session/`. Current public auth (`/auth/login`, `/auth/refresh`, `/auth/logout`), Today, Plan, detail, and six WorkoutSession operations therefore retain the contract consumed by PR6. Later backend edits concern web Training/Plan/Coach, not these native routes. No backend contract regression was found in this static audit; live compatibility still needs the staging journey.

## PR6 shipped capability map

“Shipped + verified” means inspected merged implementation plus applicable automated tests/CI. It does **not** mean device or staging proof. “Shipped but not yet live-proven” marks the runtime-sensitive parts of otherwise shipped paths.

| Area | Status | Merged evidence and remaining proof |
|---|---|---|
| A. Live WorkoutSession repository | SHIPPED + VERIFIED | `LiveWorkoutSessionRepository` implements all interface methods and sends through `ProtectedApiTransport`; repository/DTO tests ran in CI |
| B. Production auth-ON composition | SHIPPED + VERIFIED | `AppComposition.configured` constructs Live Today, Plan, detail, nutrition, preferences, generation, Pump Check, and WorkoutSession with shared authenticated transport |
| C. start/current/resume/checkpoint/abandon/complete | SHIPPED + VERIFIED | GET/POST/PUT paths in repository match backend routes; complete uses multipart |
| D. ReplayPolicy | SHIPPED + VERIFIED | `safe` current; `idempotent` start and all session commands; authenticated transport refreshes and reissues the same request template after 401 |
| E. If-Match declaration | SHIPPED + VERIFIED | Checkpoint uses frozen base revision; completion uses frozen acknowledged revision; resume/abandon use optional canonical revision |
| F. Stable Idempotency-Key | SHIPPED + VERIFIED | Frozen checkpoint/completion commands retain key, revision, photo and form values on logical retry; coordinator mints a new key after acknowledgement/new command. Completion multipart is re-encoded with a fresh boundary on each repository call, so body bytes and Content-Type can differ across logical retries. A 401 replay within one send reuses the same multipart body and Content-Type. Server requires keys for checkpoint/complete; its completion exactly-once claim is server-side |
| G. Typed server errors | SHIPPED + VERIFIED | Error mapper checks envelope code, retryability, retry-after, auth/transport failures and maps them to `WorkoutSessionFailureKind` |
| H. Session-Resolution | SHIPPED + VERIFIED | `retry`, `reread`, `terminal` parsed explicitly; coordinator branches on them |
| I. Revision conflicts | SHIPPED + VERIFIED | Conflict triggers canonical reread and preserves server state; stale snapshot is not blindly resent; coordinator tests cover this |
| J. Single writer | SHIPPED + VERIFIED | One `_sessions.checkpoint` send site in coordinator, frozen in-flight command, acknowledgement fencing and terminal drain; architecture/coordinator tests cover contention |
| K. Active execution UI | SHIPPED BUT NOT YET LIVE-PROVEN | Detail Start, set edits, sync state, Finish, confirmed Abandon, conflict/retry states are wired; real Android interaction belongs to PR7 |
| L. Process-death/restart recovery | SHIPPED BUT NOT YET LIVE-PROVEN | App account publication invokes coordinator recovery; app-support file store serializes atomic, account-scoped records; PR7 must prove OS lifecycle on Android |
| M. Account isolation | SHIPPED + VERIFIED | Account epoch/read generation fence async results; file records require account and session identity; cross-account tests run in CI |
| N. Completion synchronization | SHIPPED BUT NOT YET LIVE-PROVEN | Finish drains checkpoint writer before complete; terminal result clears recovery and refreshes Today/Plan. Real refresh of both surfaces pending |
| O. Completion-photo boundary | SHIPPED BUT NOT YET LIVE-PROVEN | Multipart image goes to session `/complete`, never standalone Pump Check POST. Native Finish UI currently selects an image before sending complete; staging proof handling must be observed |
| P. Today/Plan convergence | SHIPPED BUT NOT YET LIVE-PROVEN | Coordinator refreshes both on terminal outcome, not each edit; real displayed convergence pending |
| Q. Auth ON/OFF composition | SHIPPED + VERIFIED | ON builds Live; OFF keeps Unavailable session and other unavailable native data. Both debug APK CI jobs succeeded |
| R. Fixture isolation | SHIPPED + VERIFIED | Fixture repositories are only development composition; production configured branch uses Live/Unavailable. Architecture tests guard source and write paths |
| S. Android build coverage | SHIPPED + VERIFIED for CI; local artifact OPEN | Both CI debug polarities succeeded. Operator still needs a loopback-origin auth-ON APK for staging |
| T. Tests/non-vacuity | SHIPPED + VERIFIED for code/CI | `training_read_boundaries_test` keeps the plan-generation write and pins all five session writes; coordinator tests assert retry identity, stale ack, conflict, terminal barriers and account fencing. Live non-vacuity remains PR7 evidence |

The first repository implementation establishes replay, revision and idempotency semantics before UI handling. `AuthenticatedTransport.sendProtected` refreshes at most once on a 401 and reuses the frozen request; the coordinator distinguishes reissue of the same logical command from a new key after acknowledgement. The server's dark-route nuance remains: the mobile auth decorator is outside the sessions flag gate, so an unauthenticated request can answer 401 before an authenticated request sees dark 404. This is documented behavior, not a Sprint 15 blocker.

## Original implementation slices

| Planned slice | Reconciliation |
|---|---|
| PR2 — Live repository/composition | **SATISFIED; absorbed by shipped PR6.** Six operations, DTO/mapper, transport, auth-ON Live and auth-OFF Unavailable exist in merged code |
| PR3 — execution UX | **SATISFIED at code/CI; absorbed by shipped PR6.** Detail Start, active set editing, checkpoint, Finish, Abandon and snapshot recovery are wired. Device proof remains PR7 |
| PR4 — 401/revision recovery and non-vacuity | **SATISFIED at code/CI; absorbed by shipped PR6.** Shared auth refresh/replay, typed Session-Resolution, conflict reread, frozen keys and guarded write set are tested. Authenticated staging behavior remains PR7/closure proof |

No replacement PR2/PR3/PR4 is needed. This classification does not certify a live build.

## S15 acceptance matrix

| ID | Current status | Required final evidence |
|---|---|---|
| S15-1 | PASS — CODE/CI | Code/composition tests; PR7 confirms auth-ON build uses Live against staging |
| S15-2 | PASS — CODE/CI | Repository/contract tests; PR7 records the six-operation journey as applicable |
| S15-3 | PASS — SHIPPED BUT LIVE PROOF PENDING | Code/widget tests plus Android start, checkpoint, stale-revision reread, Finish |
| S15-4 | PASS — SHIPPED BUT LIVE PROOF PENDING | Auth restoration tests plus Android cold-start secure-session restore and live Today read |
| S15-5 | PASS — SHIPPED BUT LIVE PROOF PENDING | Auth transport/tombstone tests plus natural refresh cycles, logout and unauthenticated relaunch in staging |
| S15-6 | OPEN | Real isolated staging and Android emulator/device golden path, zero inbound, synthetic account |
| S15-7 | OPEN | Operator 90-minute authenticated soak GO with logs/metrics and rollback |
| S15-8 | PARTIAL | CI builds succeeded for both Android debug polarities; local loopback-origin auth-ON soak APK and Android execution pending |
| S15-9 | PARTIAL | Static defaults and docs keep production sessions OFF and distribution out of scope; operator must revalidate actual production flag/deploy records and zero production mutations at closure |

## Remaining work and one owner per item

| Item | Owner | Boundary |
|---|---|---|
| Real Android golden path, process restore, refresh, typed conflict, photo completion behavior, device failures, locally built soak APK | PR7 MOBILE EVIDENCE | Mobile agent; this task makes no Flutter/Android/CI edit |
| Authenticated 90-minute staging execution and mobile telemetry handoff | PR7 MOBILE EVIDENCE | Coordinate its live window; no independent staging activity here |
| Validate staging isolation/config/access immediately before live window; judge S15-6/S15-7 GO/NO-GO; restore sessions flag to 0; record final production invariant | SPRINT 15 FINAL CLOSURE | Operator/closure authority after PR7 evidence |
| Unscheduled browser-started session without reconstructible workout identity | POST-SPRINT-15 DEBT | Native client fails closed; not the scheduled core loop |
| Progress unavailable-state Pump Check entry point; native Coach/Progress content; iOS staging TLS/access topology | POST-SPRINT-15 DEBT | Outside selected Android core loop |
| Backend contract regression | BACKEND REGRESSION | None observed. If live proof exposes one, open bounded backend remediation and mark Sprint 15 blocked until corrected |

Discovery review P2s: **P2-1 STILL OPEN — NON-BLOCKING**: `ProgressScreen` renders Pump Check actions only in `ProgressData`, while production uses `UnavailableProgressSummaryRepository`, so they remain unreachable from the unavailable state. **P2-2 STILL OPEN — NON-BLOCKING**: authenticated dark session routes 404; unauthenticated requests can receive 401 from the outer auth decorator. **P2-3 CLOSED BY PR6**: ReplayPolicy, If-Match and stable Idempotency-Key were installed in the Live repository and frozen command/coordinator path. No unrelated P2 was changed.

## Staging closure preflight — read-only classification

| Prerequisite | Classification now | Before live window |
|---|---|---|
| `MOBILE_AUTH_ENABLED=1`, `FITX_WORKOUT_SESSIONS_ENABLED=1` during window, then `0`, `RUNTIME_METRICS_ENABLED=1` | KNOWN FROM CURRENT EVIDENCE as `.env.staging.example` intent (`1`, `0`, `1` at rest); NEEDS REVALIDATION BEFORE LIVE WINDOW for actual host values | Operator checks target host; only coordinated live operator changes staging session flag |
| Isolated local PostgreSQL, separate Cognito synthetic account, zero inbound | KNOWN FROM CURRENT EVIDENCE in `docs/STAGING.md`; NEEDS REVALIDATION BEFORE LIVE WINDOW | Assert host, DB/pool identity, account, security group, zero inbound |
| Correct Android staging origin and loopback forward | KNOWN FROM CURRENT EVIDENCE as documented Session Manager port-forward design; actual emulator path UNKNOWN | PR7/closure prove loopback URL reaches staging with no public ingress |
| Session Manager tooling and emulator on the operator workstation | UNKNOWN | Verify client/plugin, credentials, device and forwarding before starting the timed soak |
| Production session flag and pending deploy state | NEEDS REVALIDATION BEFORE LIVE WINDOW | Read-only operator evidence; do not approve/reject a production deployment here |

`docs/STAGING.md` is the access authority. This preflight did not start/stop staging, open a tunnel, change Cognito or flags, issue a workout write, or inspect live values through mutation. Staging and production mutation counts from **this reconciliation task** are both **0**.

## PR7 handoff and future 90-minute soak record

PR7 must return a bounded, secret-free record with: exact merged/tested mobile SHA; APK build identity and auth define polarity; staging environment/loopback identity; device/emulator; login and authenticated cold-start result; Today/Plan/detail read results; session references redacted or bounded; start/current/resume/checkpoint/abandon/complete outcomes; revision progression and intentional stale-revision reread; restart/restore result; completion photo validation and canonical side effect or explicit NOT EXERCISED; Today/Plan convergence; refresh timestamps/count; logout/relogin and unauthenticated relaunch; total elapsed wall time; unexpected 401, 5xx, throttle/overload and family-reuse counts; `WorkoutSessionLifecycle` counts; `WORKOUT_STATE` anomalies; final staging/production flag evidence; cleanup/rollback; production mutation count. No token, password, session secret or user PII belongs in the record.

The future clock starts at first successful authenticated login and runs **90 minutes**. Human-paced traffic must cover Today/Plan/detail, start, at least three checkpoints, background/restore, completion, nutrition read, logout/relaunch/relogin, and multiple natural refresh opportunities (access TTL about 900 seconds). Record any skipped network disconnect or natural refresh as **NOT EXERCISED**; do not turn a unit-test forced-expiry into a soak claim. Intentionally stale checkpoint may yield typed 409/reread. Unexpected 401 after refresh, any 5xx, family reuse, duplicate mutation, WorkoutSession IntegrityError, incoherent lifecycle counts, `WORKOUT_STATE` anomaly, production-origin use or inbound-rule change aborts GO. Record throttle/overload as observed or NOT EXERCISED. At end, return staging sessions flag to 0, stop the instance, confirm zero production writes, and attach metrics/log counts. GO requires **both** S15-6 and S15-7 PASS; otherwise record NO-GO and the precise failed criterion.

The discovery assumed a possible completion-without-photo staging path because object storage is unset. Merged PR6's active-screen Finish selects an image and `/complete` sends multipart; backend validation occurs before canonical completion and object-store upload is best effort. PR7 must report whether this real path succeeds with staging's provider/object-store setup. If it cannot, classify the concrete mobile/staging failure before closure; do not claim a no-photo path was exercised.

### Closure evidence fields — pending PR7

| Field | Current value |
|---|---|
| PR7 exact SHA, APK identity, device, auth define, target staging identity | PENDING PR7 EVIDENCE |
| Golden path, revision/checkpoints, restore, completion/abandon, Today/Plan convergence | NOT YET EXERCISED |
| 90-minute start/end, refresh cycles, logout/relogin, errors and lifecycle metrics | NOT YET EXERCISED |
| Staging flags restored, instance stopped, zero inbound, cleanup | NEEDS REVALIDATION AFTER LIVE WINDOW |
| Production sessions flag OFF, no store submission, production mutations | NEEDS READ-ONLY OPERATOR EVIDENCE; this task made 0 mutations |
| S15-6 / S15-7 verdict and independent review | PENDING PR7 AND FINAL CLOSURE |

## Validation and review gate

This docs-only change requires `git diff --check`, backend deploy-governance, flag/mobile-auth and WorkoutSession architecture/contract tests, single Alembic-head check, and a secret-shaped scan of added lines before local commit. The independent reviewer must inspect this exact commit SHA against PR6 and S15-1…S15-9, remaining ownership, PR7 separation, live-proof honesty, staging prerequisites and the production boundary. Do not push, open a PR, run staging, or close Sprint 15 from this document.
