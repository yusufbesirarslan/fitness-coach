# Native Progress vertical slice — PR1 discovery and contract specification

Status: discovery PR1. This document changes no production behaviour. The only
code in this PR is `tests/test_mobile_progress_pr1_convergence_guard.py`: one
strict `xfail` that pins defect D1, and one characterization test.

Companion to `docs/mobile/training-vertical-slice.md` (#255). Mobile-side facts
refer to `axisaiapp/axisai-mobile`.

---

## A. Executive summary

Native Progress is not a greenfield feature. The backend already owns a canonical
Pump Check domain with a native `/api/v1` contract: create, read one, history,
and comparison. The mobile app already ships a complete Pump Check vertical slice
against it (mobile PRs #13, #14, #15). The web product also has a canonical,
id-free Progress summary read model (`app/services/progress_summary`,
`contract_version: 1`). The web publishes it only through a cookie-session route.

Three findings drive the plan. Everything else is secondary.

1. **D1 — P0, backend, confirmed by execution.** A standalone
   `POST /api/v1/pump-checks` counts as that day's workout completion.
   `already_completed_today` (`app/services/workout_completion/queries.py:39`)
   and the workout-state resolver (`app/services/workout_state/queries.py:121-124`)
   match *any* `PumpCheck` created that Istanbul day. They do not check
   `date_key`. A native standalone check is written with `date_key=None`
   (`app/services/mobile_pump_checks/service.py:153`). After one:
   - WorkoutSession completion answers `already_completed`, marks the session
     COMPLETED, and writes **no completion marker, no XP, no quest, no activity
     and no proof**.
   - Today reports `completed_today=true` without a workout.
   - The browser `POST /workout/complete` (`training.py:434`) and the Coach tool
     (`ai_coach.py:694`) short-circuit in the same way.

   Standalone Pump Check and Training completion are therefore **already**
   competing authorities on the server. Native Progress must not expose
   standalone creation until this is fixed. The strict-xfail test in this PR
   reproduces D1.
2. **D2 — P1, mobile.** On mobile `main`, the "New Pump Check" and "Pump Check
   history" entries render only inside `_ProgressContent`, which appears only in
   the `ProgressData` state (`lib/features/progress/presentation/progress_screen.dart`).
   Configured composition wires `UnavailableProgressSummaryRepository`, which
   always throws `unsupported`. So **the shipped Pump Check slice is unreachable
   in the production composition**. The fix already exists as commit `d3512e2`
   on the unpushed branch `closure/pump-check-entry-and-networking-guards`. It
   was never merged.
3. **D3 — P1, contract gap.** The mobile `ProgressSummary` domain is a fixture
   shape that the canonical backend summary cannot supply:
   - consistency percentage and planned workouts;
   - streak days;
   - total volume in kg and active minutes;
   - a free-text insight.

   The backend deliberately publishes counts, not an adherence rate ("there is
   no canonical denominator", `progress_summary/payload.py`). The mobile domain
   must be **replaced**, not connected. No native summary route exists.

Native Progress V1 is therefore:
- the canonical Progress summary, served through a new thin `/api/v1` route;
- reachable Pump Check history, detail and comparison, already built;
- standalone Pump Check creation, only after D1 is fixed;
- honest completion visibility through server re-reads.

No client-side interpretation is added.

---

## B. Repository state

| | Backend `fitness-coach` | Mobile `axisai-mobile` |
|---|---|---|
| `origin/main` | `5636610ffa3b26da6cf0bcbb43c998bd6882b786` (#341) | `0da1f1ec793fe094aea9d955cab48706b3bd1afb` (#23) |
| Training Resume/phantom-checkpoint fix | n/a | present: `0da1f1e` is #23 |
| Open PRs touching Progress, Pump Check, media or history | none. Open: #342 (draft, AI model), #322/#321/#193 (deps), #208/#201 (triage docs) | none open |
| Relevant unmerged work | worktrees for merged sprint branches only | `closure/pump-check-entry-and-networking-guards` @ `840c64b`, 3 commits on old main `3fa6d11`, unpushed: `d3512e2` (D2 fix), `68d3ec5` (form a11y), `840c64b` (nutrition resolver test) |
| PR1 worktree | clean worktree at `origin/main`, branch `docs/mobile-progress-pr1-discovery` | read only; no change |

Merged mobile Pump Check history: #13 (canonical flow), #14 (history and
comparison), #15 (post-merge fixes). The Sprint 10 backend PRs are merged.

---

## C. Backend Progress authority

| Candidate | Authority (file) | Classification for native Progress |
|---|---|---|
| Progress summary: trajectory, body, performance, consistency | `app/services/progress_summary` → `training_progression` → `training_history`. Web route `GET /api/progress/summary` (`tracking.py:633`) | **CANONICAL FOR NATIVE PROGRESS**. Needs a native route (J1) |
| Pump Check history, detail, comparison | `app/services/mobile_pump_checks`, `mobile_pump_check_comparisons` | **CANONICAL FOR NATIVE PROGRESS**. Native routes exist |
| Workout completion evidence | `workout_completion.complete_workout`: day PumpCheck (`date_key`), marker WorkoutLog, XP, quest, activity | **CANONICAL** as a *server* fact. Consumed only through summary and Today; never re-derived |
| Today `completed_today` | `workout_state` resolver, via `/api/v1/today` | **CANONICAL** (already consumed by native Today). Affected by D1 |
| Axis Insights | `app/services/progress_insights` (`/api/progress/axis-insights`) | OPTIONAL / FUTURE |
| Physique (comparison-backed) | `app/services/progress_physique` (`/api/progress/physique`) | OPTIONAL / FUTURE. Overlaps the explicit comparison loop |
| Check-in history | `app/services/progress_history` (`/api/progress/history`) | OPTIONAL / FUTURE |
| Weekly workout minutes | `/api/progress/workout` (inline route query) | LEGACY. No service authority; DO NOT CONSUME |
| Heatmap | `/api/progress/heatmap` (inline route query) | LEGACY / DO NOT CONSUME |
| XP, level, achievements | `/api/progress/achievements`, `gamification` | OPTIONAL / FUTURE (gamification is its own vertical) |
| Web Pump Check gallery | `/pump-check-gallery/data`, `DELETE /pump-check-gallery/<int:id>` (`profile.py:207,243`) | WEB CONTRACT. DO NOT CONSUME: integer DB id, cookie auth, mixes completion proofs and body checks |
| Likes, comments, reposts, feed | `social.py`, `feed.py` | SOCIAL/FEED. OUT OF SCOPE |
| `/progress`, `/progress-page`, `/history`, `/checkin-history`, `/api/progress` | `tracking.py` | WEB/LEGACY. DO NOT CONSUME |
| Streaks | no canonical service (the mobile fixture invents one) | DO NOT CONSUME. None exists |

### Known limitation: performance trends ignore native set data

`training_progression` reads `WorkoutLog` rows (`training_history/queries.py`).
Completion markers count as sessions (`include_markers=True`). Native
WorkoutSession execution writes **no per-exercise `WorkoutLog` rows**: set
progress lives in the session checkpoint, and the completion writes only the
marker. For a native-only user, `consistency.sessions` reflects completions, but
`performance.volume_trend` and `strength_trend` stay in baseline/insufficient
states. The client must render those states literally.

Projecting session sets into `WorkoutLog` is a Training-domain decision. It is
out of scope here (see I).

---

## D. Pump Check authority

One table, `PumpCheck` (`app/models.py:813`). It has exactly two writers in
`app/`: `mobile_pump_checks/service.py:139` and `workout_completion/service.py:139`.
The browser `/workout/complete`, the native session `/complete` and the Coach
tool all call the second. The table therefore holds **two distinct kinds of
row**:

| Property | Standalone body check (`POST /api/v1/pump-checks`) | Completion proof (`complete_workout`) |
|---|---|---|
| Writer | `mobile_pump_checks.service.create_or_replay` | `workout_completion.service.complete_workout` |
| `date_key` | `None` | Istanbul `YYYY-MM-DD`, unique per user (`uq_pump_check_day`) |
| `public_id` | HMAC-bound random 24-character token (`identity.py`) | `None` |
| `captured_at`, `body_region` | set, validated | `None` |
| `analysis_status` / `analysis` | leased provider analysis, `pump-check-analysis/v1` | `None` (vision is only a pass/fail gate) |
| `idempotency_key` / fingerprint | `(user_id, key)` unique plus a semantic SHA-256 fingerprint | none. The day constraint is the claim |
| `visibility` | `private` | native `private`; browser user-chosen (`feed`/`friends`/`private`) |
| XP, quest, activity, marker | **none** | atomic in one transaction |
| Native history (`GET /api/v1/pump-checks`) | included | **excluded** (`captured_at IS NOT NULL` filter) |
| Native detail (`GET /api/v1/pump-checks/{id}`) | addressable | not addressable (no `public_id`) |
| Web gallery | included | included |

These are therefore **not the same canonical domain object**. The table is
shared, but they mean different things: a body-progress capture versus workout
completion evidence. The native path keeps them separate everywhere except in
the completion preflight and resolver queries. That exception is D1.

Other Pump Check authority facts:
- **Ownership.** Every native read and write is scoped by `g.mobile_user.id`
  from the bearer principal. An unknown, malformed or foreign id gets one answer,
  `404 PUMP_CHECK_NOT_FOUND` (`service.get_owned`).
- **Media.**
  - Storage: a private S3 key under `pump-checks/{user_id}/…`.
  - Signed URL: presigned for 3600 s, only when the key belongs to the caller
    (`generate_presigned_url(expected_user_id=…)`).
  - The key itself is never serialized. History returns **no media**.
- **Validation.**
  - Allowed formats: JPEG, PNG, WEBP, decoded by Pillow.
  - Limits: 6,000,000 bytes and 40 MP. The declared and detected MIME types
    must match.
  - `captured_at` must be UTC with a `Z` suffix, within [now−365 d, now+10 min].
  - Region and environment come from enum sets; description is ≤200 characters.
- **Analysis.**
  - Lease: 900 s.
  - Retry: a failed attempt is reclaimed on replay, except persistence failures.
  - Failure codes: 503 with `PUMP_CHECK_{STORAGE,PROVIDER,PERSISTENCE}_UNAVAILABLE`
    or `PUMP_CHECK_ANALYSIS_INVALID`, retryable.
- **Rate limits.** Create and compare each carry `BEDROCK_RATELIMIT` (default
  `10 per hour`, keyed per user) plus the AI concurrency gate. GET routes have no
  rate limit.
- **Delete.** Web only (`profile.py:243`). The row is deleted first and the
  object is released after commit. **No native delete.**
- **Fallback.** A completion proof may record `fallback=True` (the vision gate
  failed open). Standalone analysis never falls back: it fails with a 503.

---

## E. Workout completion convergence

Traced path (native):

```
POST /api/v1/training/workout-sessions/{ref}/complete     (multipart, If-Match REQUIRED)
  → Idempotency-Key parsed and deliberately unused (mobile_workout_sessions.py:298)
  → prepare_complete (owner, revision)
  → replaying = session COMPLETED or already_completed_today(...)   ← D1 enters here
  → if not replaying: _completion_proof(): image validate → vision gate → S3 upload (best-effort)
  → sessions.complete → workout_completion.complete_workout (ONE transaction):
       lock session → revision check → preflight → PumpCheck(date_key) → challenge (savepoint)
       → friend messages → marker WorkoutLog → quest → XP → activity → session COMPLETED → commit
       uq_pump_check_day race loser → ALREADY_COMPLETED + session reconciliation
  → response {session, completion{outcome, xp_awarded, level, title}}   (no Pump Check id)
```

**Invariant (normative).**

```
TRAINING COMPLETION  → WorkoutSession multipart /complete ONLY
STANDALONE PUMP CHECK → Progress capability ONLY; never completion evidence
```

- Mobile already follows the first half.
  - `WorkoutCompletionMultipartEncoder` posts to `/complete` and reuses only the
    image bytes.
  - `test/architecture/workout_session_execution_boundaries_test.dart:53` asserts
    this statically.
  - `native_training_golden_path_test.dart:995` asserts at runtime that
    `server.paths` never contains `/api/v1/pump-checks`.
- The server does **not** yet follow the second half. That gap is D1.
- The fix (PR2) makes *completion evidence* mean `date_key IS NOT NULL` in both
  queries. Only the canonical completion writer sets `date_key`, and the
  `uq_pump_check_day` constraint already keys on it. That makes the preflight,
  the resolver and the durable claim one definition. A PR1 mutation run showed
  that adding the filter to `already_completed_today` alone flips the guard to
  XPASS. The resolver needs the same change so that Today agrees.

**What Progress treats as completion evidence.** Only server read models:
- Today `completed_today`;
- summary `consistency.sessions`, which counts days that carry a marker or logs.

Progress never counts Pump Checks, never infers completion from a completion
response, and never adds a local row after completing.

---

## F. Existing mobile Progress architecture

| Component | Evidence | Verdict |
|---|---|---|
| `AppRouteId.progress`, `/progress`, destination `progress` | `app_route_registry.dart:479` | KEEP |
| Pump Check routes `/progress/pump-check/{new,result,history,detail,compare/baseline,compare/result}`: typed in-memory `extra`, no id in the URI, not restorable | `app_route_registry.dart:489-550`, `pump_check_route_context.dart` | KEEP |
| `ProgressScreen` layout (percentage hero, streak, volume, active minutes) | `progress_screen.dart` | REPLACE. It renders fields that have no canonical source |
| `ProgressScreen` entry gating | Pump Check entries only in `ProgressData` | REFACTOR. Land `d3512e2` (D2) |
| `ProgressSummary` domain, fixture DTO/mapper, fixture JSON | `lib/features/progress/{domain,data}` | REPLACE with canonical `contract_version: 1` types. Keep the fixture only in development composition, reshaped to the canonical contract |
| `UnavailableProgressSummaryRepository` | configured composition | KEEP as the auth-OFF implementation; replaced by a live repository when auth is ON |
| `ProgressController` | per-screen `DisposableLoadController`, **no auth-epoch fence** | REFACTOR. Needs a session owner with `resetForAuthTransition` (P9) |
| Pump Check feature: domain, `LivePumpCheckRepository`, DTOs, mappers, error mapper, multipart encoder, 4 controllers, 7 screens | `lib/features/pump_check/**` | KEEP. Verified against current backend routes, fields, codes and page size (20 ≤ 50). Byte limit (6,000,000) and MIME sniffing match `validators.py` |
| `PumpCheckRouterSession` (one owner, reset on every account change) | `app_router.dart:514`, `app.dart:164` | KEEP |
| `ImagePickerPumpCheckSelector` (gallery only, `requestFullMetadata:false`, magic-byte MIME, lost-data recovery) | `image_picker_pump_check_selector.dart` | KEEP |
| Completion → navigation | `onComplete` → `context.go(today)` (`app_router.dart:742,759`) | KEEP. Progress re-reads on entry; no push from completion |
| `FileWorkoutExecutionStore` holding the pending completion image on disk | `file_workout_execution_store.dart:275` | KEEP. Training-owned; already fenced by PR6/PR7/#23 |
| Native auth rollout default | `AXISAI_NATIVE_AUTH_ENABLED=false` | KEEP (P10, P11) |

No stale Pump Check mobile code was found beyond the D2 entry gating. The old
`sprint10-*` worktrees are content-merged and dead.

---

## G. Contract inventory

| Method + path | Auth | Request | Response | Pagination | Errors | Rate / idempotency | Media | Cache | Class |
|---|---|---|---|---|---|---|---|---|---|
| `POST /api/v1/pump-checks` | bearer | multipart: `image`, `body_region`, `environment`, `description`, `captured_at`, plus header `Idempotency-Key` | `201`/`200` `{pump_check}` | – | 400 `INVALID_IDEMPOTENCY_KEY`/`INVALID_PUMP_CHECK_IMAGE`/`INVALID_PUMP_CHECK`; 409 `IDEMPOTENCY_CONFLICT`; 503 (retryable) | 10/h per user, AI gate; `(user,key)` plus fingerprint | upload → private S3 | `no-store` | EXISTING NATIVE — REUSE (**write gated on D1**) |
| `GET /api/v1/pump-checks?limit&cursor` | bearer | – | `{pump_checks[{id,captured_at,body_region,analysis_status,analysis_quality}], next_cursor, has_more}` | keyset, encrypted owner-bound cursor; `limit` 1..50 (strict), default 20 | 400 `INVALID_PAGE_SIZE`/`INVALID_PAGE_CURSOR` | none / safe | none | `no-store` | EXISTING NATIVE — REUSE |
| `GET /api/v1/pump-checks/{id}` | bearer | – | `{pump_check{id,captured_at,created_at,body_region,environment,description,image_url,analysis_status,analysis_version,analysis}}` | – | 404 `PUMP_CHECK_NOT_FOUND` | none / safe | presigned 1 h | `no-store` | EXISTING NATIVE — REUSE |
| `POST /api/v1/pump-check-comparisons` | bearer | JSON pair plus `Idempotency-Key` | comparison | – | typed | 10/h, AI gate, idempotent | none | `no-store` | EXISTING NATIVE — REUSE |
| `GET /api/v1/pump-check-comparisons/{id}` | bearer | – | comparison | – | 404 | safe | none | `no-store` | EXISTING NATIVE — REUSE |
| `POST /api/v1/training/workout-sessions/{ref}/complete` | bearer, flag-gated | multipart plus `If-Match`, `Idempotency-Key` | `{session, completion}` | – | typed session errors | 10/h, AI gate, day claim | proof → S3 | `no-store` | EXISTING NATIVE — Training-owned; Progress never calls it |
| `GET /api/v1/today` | bearer | – | includes `completed` | – | – | safe | – | `no-store` | EXISTING NATIVE — REUSE (affected by D1) |
| `GET /api/progress/summary` | web cookie session | – | `contract_version:1` summary | – | 500 generic | – | – | web | WEB CONTRACT — DO NOT CONSUME. **SERVER INTERNAL — REUSE BELOW HTTP** (`build_progress_summary` + `progress_summary_payload`) |
| `GET /api/v1/progress/summary` | – | – | – | – | – | – | – | – | **MISSING NATIVE CONTRACT** (J1) |
| `/api/progress/{axis-insights,physique,history}` | web cookie | – | versioned read models | – | – | – | – | – | WEB — DO NOT CONSUME (future native wrappers possible) |
| `/api/progress/{workout,heatmap,achievements}`, `/api/progress` | web cookie | – | ad hoc | – | – | – | – | – | LEGACY — DO NOT CONSUME |
| `/pump-check-gallery/data`, `DELETE /pump-check-gallery/<int:id>` | web cookie | – | integer ids | offset | – | – | presigned | – | WEB — DO NOT CONSUME |
| Native Pump Check delete | – | – | – | – | – | – | – | – | MISSING. Deferred (I) |
| Native completed-workout list | – | – | – | – | – | – | – | – | MISSING. Deferred (I) |

---

## H. Native Progress V1 scope

| Capability | Why in V1 | Canonical source | Mobile surface | R/W | Dependencies | Risks |
|---|---|---|---|---|---|---|
| Progress summary | The Progress destination is empty or unavailable in prod today; the canonical read model exists | `build_progress_summary` via new `GET /api/v1/progress/summary` | `ProgressScreen` (replaced content) | R | J1, mobile domain replacement | Native-only users see baseline performance states (C). Rendered literally |
| Pump Check entry reachable in every summary state | The shipped slice is unreachable (D2) | – | `ProgressScreen` | – | `d3512e2` | none new |
| Pump Check history, detail, comparison | Already built and verified against current contracts | `mobile_pump_checks`, comparisons | existing screens | R (+ compare W) | reachability | none new |
| Standalone Pump Check creation | Existing, safe multipart contract | `POST /api/v1/pump-checks` | existing form | W | **D1 fixed and deployed** | D1 if shipped out of order |
| Completion visibility | Progress must reflect a completed workout | summary `consistency.sessions` and Today `completed` (server re-read) | `ProgressScreen` refresh on entry | R | J1 | none: no client-side counting |
| Loading, empty, error, unavailable, auth-OFF states | honesty | – | `ProgressScreen` | – | – | copy must not claim "local data" |

---

## I. Deferred scope

- A native completed-workout or history list. No native contract exists; it needs
  a new backend read model over WorkoutSession and the marker.
- Projecting native session sets into `WorkoutLog` for performance trends
  (Training domain).
- Completion proofs in native Pump Check history. They are evidence rather than
  body captures and carry no region, capture time or analysis. Showing them would
  be a product decision plus a contract change.
- Native Pump Check delete, including the comparison-reference and storage-release
  design.
- Axis Insights, physique, check-in history, heatmap, XP, achievements and
  streaks. Streaks have no authority.
- Charts, trends or any new aggregation. Weight logging and body-composition
  trends.
- Camera capture (gallery only today). New media transformations or
  compression.
- Feed and social, Coach integration, challenges and quests, notifications and
  reminders, AI interpretation.

---

## J. Required backend prerequisites

- **J0 (PR2) — Fix D1.** Standalone Pump Checks are not completion evidence.
  - Filter `PumpCheck.date_key.isnot(None)` in `already_completed_today` and in
    the workout-state completion query.
  - Remove the xfail. Add tests for Today, `/workout/status`, browser completion,
    native completion, and a PG race (standalone and completion concurrently).
  - Historical rows are unaffected: every completion writer since `uq_pump_check_day`
    sets `date_key`, and only "today" windows are queried.
- **J1 (PR4) — `GET /api/v1/progress/summary`.**
  - `require_mobile_auth`; owner from the principal only; no query parameters
    (the window is pinned).
  - Body: `jsonify(progress_summary_payload(build_progress_summary(g.mobile_user.id)))`.
  - `no-store` comes from the blueprint. Error: `mobile_error("PROGRESS_UNAVAILABLE", …, 503, True)`.
    `UnknownProgressionSignal` → 503, never a fabricated state.
  - Architecture test: the blueprint imports only the service and payload.
  - No new flag: a read-only, id-free projection of an already-shipped read
    model. It stays unreachable in practice while mobile auth is OFF.

No other backend prerequisite exists for V1.

---

## K. Security and privacy invariants

- **Owner scoping.** Every Progress and Pump Check read takes the owner from the
  bearer principal. Cursors are encrypted and owner-bound. Ids are 144-bit
  HMAC-bound random tokens, so enumeration is infeasible. Foreign, unknown and
  malformed ids get the same 404.
- **No internal ids or keys.** The summary payload is hand-written and id-free.
  Pump Check exposes `public_id` only; `image_key`, integer `id`, `user_id`,
  `idempotency_*` and `visibility` are never serialized. The web gallery's
  integer id stays web-only.
- **Media.** Short-lived presigned URLs only, checked against the owner's key
  prefix. They are never persisted on device (mobile `docs/PUMP_CHECK.md`
  "Privacy"). History carries no media.
- **Cache.** `/api/v1` responses are `no-store`. No Progress or Pump Check data
  goes to disk, preferences or secure storage.
- **Account switch.** `PumpCheckRouterSession.resetForAuthTransition` exists and
  is tested (`pump_check_account_isolation_test.dart`). **The Progress summary
  controller has no fence yet.** PR5 must add an owner session with
  (generation, auth-context) fencing and a reset on every transition (P9).
- **Pagination abuse.** `limit` is rejected, not clamped, outside 1..50. Keyset
  pagination makes deep pages O(limit).
- **Rate limiting.** Writes are limited per user. GETs are unlimited but cheap
  and owner-scoped. This is an accepted P3.
- **Deletion lifecycle.** Web delete removes the row, then the object. See L for
  the effect on completion.

---

## L. Concurrency and idempotency invariants

**Standalone create:**
- Same key and same fingerprint → the same row (200 replay).
- Same key with a different payload → 409.
- A concurrent insert race is resolved by `uq_pump_check_user_idempotency`.
- The analysis lease stops two simultaneous analyses. A lost response is
  recovered by resending the identical frozen command and key
  (`PumpCheckSubmissionController`).
- A new intent gets a new key. There is **no daily uniqueness**: several body
  checks per day are allowed.

**Completion:**
- `uq_pump_check_day` is the only claim. `If-Match` protects progress.
- A replay or race loser gets `ALREADY_COMPLETED` with no duplicate side
  effects.

**Completion vs standalone:**
- After J0 they are independent. A standalone row (`date_key=None`) can neither
  collide with nor pre-empt the day claim.
- Before J0 a standalone row pre-empts the claim (D1).

**Account change mid-request.** The epoch-bound `sendProtected` and controller
fences publish nothing.

**Partial storage failure:**
- Standalone: the S3 upload happens before the `image_key` update commit. A
  commit failure orphans the object, and a retry uploads again.
- Completion: the proof is uploaded *before* the transaction. A revision
  conflict, abandoned session or race loss leaves an unreferenced object.
- No bucket lifecycle rule exists in `infra/`. **P2**, pre-existing, out of
  V1 scope; tracked in P.

**Web delete of today's completion proof.** This re-opens the day, so a second
completion can award XP again. **P2**, pre-existing and web-only; not changed
by V1.

---

## M. Media and platform boundaries

| Concern | Shared Flutter | Android | iOS |
|---|---|---|---|
| Selection | `image_picker` 1.2.3, gallery only, `requestFullMetadata:false` | `retrieveLostData` recovery after activity death | `NSPhotoLibraryUsageDescription` present; **no camera key**, and `pump_check_security_test` forbids camera/broad permissions |
| MIME | magic-byte sniff (JPEG/PNG/WEBP); server re-validates | – | – |
| Size | 6,000,000-byte client guard, equal to the server limit | – | – |
| Compression | **none**. A large HEIC→JPEG export can exceed 6 MB and is rejected | – | HEIC conversion behaviour is **unverified**. Physical validation required after the Mac transition |
| Multipart | `pump_check_multipart_encoder.dart` (standalone) and `workout_completion_multipart_encoder.dart` (completion); separate encoders, secure boundary | – | – |
| Temp files | standalone: in memory only; completion: Training execution store (fenced) | – | – |
| Cancel / failure | frozen command retry; picker cancel → `null` | – | – |

Device-free tests prove none of the platform columns (P14).

---

## N. Proposed PR sequence

**PR2 — backend: completion evidence excludes standalone Pump Checks (J0).**
- Objective: remove D1.
- Files: `workout_completion/queries.py`, `workout_state/queries.py`, the PR1
  guard (drop the xfail), completion, today and resolver tests, and a new PG
  race test added to `ci.yml`'s explicit `pg_concurrency` list.
- Dependencies: none. Out of scope: every other Pump Check behaviour.
- Gates: full suite plus the PG job; the guard passes non-xfail.
- Rollback: a single revert. The data is untouched.

**PR3 — mobile: Pump Check reachability (D2).**
- Objective: Pump Check entries render in every summary state. Rebase and land
  `d3512e2` (plus `68d3ec5`, the independent a11y fix).
- Files: `progress_screen.dart` and its tests.
- Dependencies: **PR2 merged and deployed** (reachability exposes standalone
  create).
- Out of scope: summary data. Gates: auth OFF/ON suites, architecture, analyze,
  semantics-tap tests.
- Rollback: revert. The UI becomes unreachable again; nothing else changes.

**PR4 — backend: `GET /api/v1/progress/summary` (J1).**
- Files: a new `mobile_progress.py` blueprint module registered on `mobile_api`;
  API, architecture and ownership tests; a docs update.
- Dependencies: none. It can run in parallel with PR2 and PR3.
- Out of scope: new fields, insights, physique, history.
- Gates: contract literal test, auth-required test, stranger isolation,
  `no-store`, 503 on unknown signal.
- Rollback: revert. The route disappears and nothing else depends on it.

**PR5 — mobile: canonical Progress summary live read.**
- Objective: replace the `ProgressSummary` domain with a typed
  `contract_version:1` model. Fail closed on an unknown version or enum.
- Also: `LiveProgressSummaryRepository`, a `ProgressRouterSession` with auth
  fencing and reset, re-read on every entry to Progress, and literal rendering of
  baseline/insufficient states.
- Remove fixture-only fields. The development fixture is reshaped to the
  canonical contract.
- Dependencies: PR4 deployed, PR3.
- Out of scope: charts, insights, local derivation.
- Gates: DTO fail-closed tests, account-isolation matrix, architecture test (no
  `Fixture*` in configured composition; progress feature must not import
  training, pump_check or workout internals), auth OFF → unavailable.
- Rollback: revert to the unavailable repository.

**PR6 — mobile: device-free Progress integration closure.**
- Objective: a golden path under a fake server:
  - Today → workout → complete → Progress re-read shows server counts;
  - Pump Check create → history refresh → detail → compare;
  - account switch mid-read;
  - auth OFF.
- It asserts the standalone POST count is 0 during completion, and completion
  count is 0 during standalone create.
- Dependencies: PR5. Rollback: tests only.

**Separate, not scheduled here:** physical iOS validation (after the Mac/Xcode
transition), Android physical validation (deferred, no device), and the P2
storage hygiene of L.

---

## O. Test matrix

| Invariant | Exists today | Required | PR |
|---|---|---|---|
| Pump Check ownership and foreign 404 | `test_mobile_pump_check_api.py`, `_history_api.py`, `_identity.py` | – | – |
| History pagination and cursor | `test_pump_check_history_{cursor,service}.py`, `_history_pg.py` | – | – |
| Create idempotency and race | `test_mobile_pump_check_pg.py` | – | – |
| Completion exactly-once | `test_workout_completion{,_pg}.py`, `test_mobile_workout_sessions_{api,pg}.py` | – | – |
| **Standalone ≠ completion (D1)** | **PR1 strict xfail** | pass; PG race; Today/`status` agreement | PR2 |
| Completion proof excluded from native history | PR1 characterization | keep | – |
| S3 object release | `test_pump_check_object_lifecycle.py` (web delete) | orphan cleanup: deferred P2 | – |
| Media validation | `test_pump_check_gallery_media.py`, validators | – | – |
| Native summary contract, auth, isolation | – | new | PR4 |
| Training never calls standalone POST | static `workout_session_execution_boundaries_test.dart:53`, runtime golden path `:995` | keep | – |
| Pump Check account isolation | `pump_check_account_isolation_test.dart` | – | – |
| Progress summary account isolation | **none** | new | PR5 |
| Pump Check entry reachable when summary unavailable | on the unpushed branch only | land | PR3 |
| Progress navigation, auth OFF/ON | `progress_screen_test.dart`, auth OFF/ON suites | extend | PR3, PR5 |
| End-to-end Progress golden path | – | new | PR6 |

---

## P. Definition of done (Native Progress V1)

1. D1 fixed. The standalone POST can never mark a day completed. Guard green
   without xfail.
2. `GET /api/v1/progress/summary` is live, owner-scoped, `no-store` and id-free.
3. Mobile Progress renders the canonical summary with no locally derived metric.
4. The Pump Check entry is reachable in every Progress state. History, detail,
   compare and create work against production contracts.
5. Account switching clears Progress and Pump Check state; late responses
   publish nothing.
6. Auth OFF: Progress is unavailable, Pump Check is unavailable, and there is no
   fixture data.
7. Device-free golden path green on ubuntu and macOS CI.
8. No production flag changed. Physical validation is explicitly **not**
   claimed.
9. The storage-orphan P2 and the web-delete re-completion P2 are recorded as
   follow-ups.

---

## Q. Explicit non-goals, and the required invariants

- **P1.** Backend remains the Progress and Pump Check authority.
- **P2.** Flutter holds no Python or business semantics: no streak, percentage,
  trend, comparison eligibility beyond published fields, or completion counting.
- **P3.** Native Progress consumes `/api/v1` only. `/api/progress/*`,
  `/pump-check-gallery*` and `/progress*` are never called.
- **P4.** Training completion never creates a standalone Pump Check first.
  Enforced by the mobile static and runtime guards.
- **P5.** Training completion is one WorkoutSession completion transaction
  (`complete_workout`).
- **P6.** Progress reads completion only from server evidence, via the summary
  and Today, never from Pump Check history. On the server, standalone checks stop
  counting after PR2.
- **P7.** Foreign data is inaccessible: owner from the principal, uniform 404,
  owner-bound cursor and id.
- **P8.** No DB ids, `image_key` or `user_id` in any native payload.
- **P9.** Account switching fences late Progress and media responses. Pump Check
  does this today; the summary gets it in PR5.
- **P10.** Auth OFF keeps the unavailable repositories and no Pump Check
  session.
- **P11.** No rollout flag is flipped by this work.
- **P12.** Feed and community are out of scope.
- **P13.** Coach is out of scope.
- **P14.** Device-free tests do not stand in for physical iOS or Android
  validation.

### Adversarial review

| # | Question | Answer |
|---|---|---|
| 1 | Can Training completion hit the standalone POST? | No. The static and runtime mobile guards (E). |
| 2 | Can one completion produce two PumpChecks? | No. `uq_pump_check_day` plus race-loser reconciliation. |
| 3 | Can Progress show a completion that did not happen? | **Yes today, via D1** (Today `completed_today`). Fixed in PR2; guarded by the PR1 xfail. |
| 4 | Can a stale or foreign Pump Check be exposed? | No for foreign data (owner scope, uniform 404). Stale data is fenced by the Pump Check session reset. |
| 5 | Can Flutter become a second authority? | The current fixture domain *is* one (percentage, streak). PR5 replaces it; an architecture test forbids local derivation. |
| 6 | Can browser routes leak into native? | Not at present: no mobile source references `/api/progress` or `/pump-check-gallery`. PR5 adds an architecture test. |
| 7 | Can an account switch show another account's Progress? | Pump Check: no. Summary: no data today (unavailable); PR5 must fence it. |
| 8 | Can a failed upload orphan objects? | Yes, in both writers (L). P2, deferred and recorded. |
| 9 | Can a retry duplicate a standalone check? | No, as long as the frozen key is reused. A new key is a new intent, by design. |
| 10 | Is a backend prerequisite overlooked? | J0 and J1 are the only ones for V1. The performance-trend gap (C) is a documented limitation, not a V1 prerequisite. |
| 11 | Are we exposing something the backend cannot support? | A streak, adherence percentage, volume or active minutes would be. They are removed in PR5. |
| 12 | Are existing Pump Check mobile components stale? | No: verified against current routes, fields, codes and limits. Only the entry gating (D2) is wrong. |
