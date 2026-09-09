# Sprint 15 PR1 — Mobile launch-hardening discovery

Discovery and prioritization only. No production behavior, schema, migration,
Flutter, feature flag, workflow, host `.env`, or deployment was changed by this
PR. Production mutations from this work: **0**.

---

## 1. Executive verdict

```text
READY FOR INDEPENDENT SPRINT 15 DISCOVERY REVIEW
```

Sprint 14 is **closed input**. The server WorkoutSession contract is shipped
dark. This document does **not** start implementation.

**Sprint 15 objective:**

> An `AXISAI_NATIVE_AUTH_ENABLED` production-composition Android debug build
> can authenticate a synthetic staging user over the isolation-preserving
> loopback path in `docs/STAGING.md`, restore a session, read live Today / Plan
> / workout-detail, execute a revision-gated WorkoutSession lifecycle against
> `/api/v1`, and survive a bounded authenticated soak — without activating
> production flags or submitting a store build.

That is a capability, not a ticket list. It is the smallest end-state that
makes the advertised native loop real and staging-verifiable.

---

## 2. Baseline / provenance

| Field | Value |
|---|---|
| Backend repository | `yusufbesirarslan/fitness-coach` |
| Brief expected handoff SHA | `9a881bc7dbd88479b4b57eee5eea71dcad4c1bd7` — `docs(training): complete workout session activation readiness (#294)` |
| **Actual `origin/main` at discovery start** | **same SHA** `9a881bc7dbd88479b4b57eee5eea71dcad4c1bd7` |
| Drift vs expected | **none** |
| Discovery branch | `sprint15-pr1-mobile-launch-hardening-discovery` |
| Worktree | `.worktrees/sprint15-pr1-mobile-launch-hardening-discovery` |
| Merge-base with `origin/main` | `9a881bc7dbd88479b4b57eee5eea71dcad4c1bd7` |
| Alembic heads | **1** — `f5a6b7c8d9e0` (`add_workout_session_native_execution`), 40 revisions |
| Primary worktree at start | clean on `fix/deploy-release-waiting-superseded`; discovery branched from `origin/main`, not that branch |

Sibling native repository (discovered, not assumed):

| Field | Value |
|---|---|
| Identity | `yusufbesirarslan/axisai-mobile` (`https://github.com/yusufbesirarslan/axisai-mobile.git`) |
| Inspected checkout | detached `origin/main` at `C:\Users\yusuf\develop\_tmp\axisai-mobile-s15-discovery` |
| **`origin/main` SHA** | `7f354e32a944973ba7d8d46e51fa32ba6bdd839d` — `feat(training): add native first-plan generation flow (#19)` |
| Parent checkout branch | `mobile/foundation-pr3-core-screens` (not used; **not** the discovery baseline) |
| Mobile working tree (parent) | porcelain-clean |
| Mobile implementation branch | **not created** |
| Mobile files edited | **0** |

Sprint 14 discovery already recorded mobile `origin/main` as `7f354e3`. **The
Flutter tree has not moved during Sprint 14.** That is expected: Sprint 14
forbade Flutter changes.

### Production deploy baseline (read-only, not authorized here)

GitHub Actions `deploy.yml` run 274 completed **success** on 2026-09-09 for
`head_sha=9a881bc7dbd88479b4b57eee5eea71dcad4c1bd7`. No `waiting` deploy run
was listed. This discovery **does not** approve, reject, or repeat that deploy.
Host `.env` is still not readable from the repository: a successful deploy
does not flip flags (`docs/ROLLOUT.md` — deploy never writes host `.env`).

---

## 3. Sprint 14 handoff

Carried forward as closed input. Not reopened.

| Fact | Disposition |
|---|---|
| Canonical browser/native WorkoutSession execution contract | **SHIPPED** (`#280`, `#285`, `#289`) |
| Revision-gated checkpoint/completion authority | **SHIPPED** |
| Cross-transport authority | **SHIPPED** (server tests + staging exercise) |
| Lifecycle observability | **SHIPPED** (`WorkoutSessionLifecycle`, `[WORKOUT_SESSION]` / `[WORKOUT_STATE]`) |
| Staging activation exercise | **RUN** 2026-09-09 on SHA `c3f579a`; cases A–N recorded in `docs/WORKOUT_SESSION_ACTIVATION.md` |
| Rollback / inertness | **PROVEN** in that exercise; staging finished with `FITX_WORKOUT_SESSIONS_ENABLED=0` |
| Repository default `FITX_WORKOUT_SESSIONS_ENABLED` | **OFF** (`app/feature_flags.py:349`) |
| Production sessions flag | **OFF** (last documented assertion 2026-09-09; deploy does not enable it) |
| Isolated staging environment | **EXISTS** (`docs/STAGING.md`, `#290`) |
| Staging inbound | **zero** (security group in `docs/STAGING.md` §2) |
| Session Manager access | **EXISTS** (operator path in `docs/STAGING.md` §6) |
| Synthetic staging identity | **EXISTS** (`docs/STAGING.md` §7; handle `s14pr5tester`) |
| Flutter WorkoutSession client | **NOT STARTED** — still `UnavailableWorkoutSessionRepository` |
| Native-auth ON distribution | **NOT STARTED** — compile-time default `false` |

Sprint 14 P2s stay backlog unless they intersect the chosen objective (§24).

---

## 4. Historical Sprint 15 ownership

Do not copy historical scope blindly. Current code is authority where docs
and code disagree.

| Historical assignment | Source | Classification |
|---|---|---|
| Fresh SHA/drift check → authenticated login/refresh/Today smoke → health/capacity → **new contiguous 24-hour soak** → only then internal native-auth ON build | `docs/superpowers/specs/2026-08-26-sprint12-mobile-auth-today-production-rollout-readiness.md` §33.2 | **NEEDS REVALIDATION.** The *ownership* (native-auth launch-hardening) is still true. The *24-hour production soak then ON-build* sequence is **SUPERSEDED** by isolated staging + the prohibition on a production soak in this brief. Access TTL is 900s, so token lifecycle is observable in a bounded window. |
| Native-auth production rollout deferred; no ON build authorized | same §33.1 | **STILL TRUE** |
| `MOBILE_AUTH_ENABLED` / `AXISAI_NATIVE_AUTH_ENABLED` / soak owned by Sprint 15, not 13/14 | Sprint 13 discovery; Sprint 14 PR1 §11; PR5 §10 | **STILL TRUE** as ownership. Backend `/api/v1` enablement is **ALREADY SHIPPED** as a dated production runtime (2026-08-26). Remaining work is the **Flutter compile-time ON path + client execution + isolated soak**, not first registration of the blueprint. |
| Sprint 13: mobile nutrition is shipped-dark because `MOBILE_AUTH_ENABLED` is blocked | Sprint 13 §21 | **STALE** vs 2026-08-26 production deep-health (`MOBILE_AUTH_ENABLED=1`). Nutrition contracts are reachable to any client that presents opaque credentials. No native-auth ON binary has been distributed. |
| Registry lifecycle `blocked` until Hardening PR4 | `app/feature_flags.py:535`; `docs/FEATURE_FLAGS.md:271-279`; `docs/AUTH_CONTRACT.md:190-192` | **STALE.** PR4 is merged (`34f8dc79`). Login/refresh sit behind `blocking_concurrency_slot`. |
| `ROLLOUT.md` flag 9: already `1` on production SHA `a6d6b2e` — do not enable it again | `docs/ROLLOUT.md:111` | **STILL TRUE** as operator instruction if the host flag was never turned off. **NEEDS REVALIDATION** of current host `.env`. Deploy of later SHAs does not rewrite `.env`. |
| Flutter WorkoutSession remains Unavailable | Sprint 14 PR1 §2.3; PR5 §10 | **STILL TRUE** at mobile `7f354e3` (**VERIFIED IN CODE**) |
| F-4 docs-integrity deferred to Sprint 15 | Sprint 14 PR1 §12 | **STILL TRUE** — registry / `FEATURE_FLAGS.md` / `AUTH_CONTRACT.md` still disagree with `ROLLOUT.md`. Not the sprint objective. |
| `AXISAI_NATIVE_AUTH_ENABLED` default false; rollback is a new binary | `app/feature_flags.py:622-644`; `docs/FEATURE_FLAGS.md:283-301` | **STILL TRUE** |
| Sprint 14 must not absorb Flutter session wiring | Sprint 14 PR1 §11; PR5 §10 | **STILL TRUE**; it did not. |

---

## 5. Backend native capability map

**VERIFIED IN CODE** at `9a881bc`. One blueprint: `app.blueprints.mobile_api.bp`,
`url_prefix="/api/v1"`. Registered only when `app.config["MOBILE_AUTH_ENABLED"]`
is true (`app/__init__.py:348-352`). Product modules attach to that same `bp`
(`mobile_api.py:218-223`).

Shared policy: `Cache-Control: no-store`; CSRF skipped; envelope
`{error:{code,message,retryable,request_id}}`.

### 5.1 Route inventory

| Method | Path | File | Auth | Extra flag |
|---|---|---|---|---|
| POST | `/api/v1/auth/login` | `mobile_api.py:95` | none | `MOBILE_AUTH_ENABLED` |
| POST | `/api/v1/auth/refresh` | `mobile_api.py:121` | refresh body | same |
| POST | `/api/v1/auth/logout` | `mobile_api.py:138` | optional Bearer | same |
| GET | `/api/v1/account/me` | `mobile_api.py:199` | `require_mobile_auth` | same |
| GET | `/api/v1/today` | `mobile_today.py:22` | required | sessions flag only for `contract_version=2` / session projection |
| GET | `/api/v1/training/preferences` | `mobile_training.py:109` | required | — |
| POST | `/api/v1/training/plans` | `mobile_training.py:47` | required | first-plan only |
| GET | `/api/v1/training/plans/current` | `mobile_training.py:115` | required | — |
| GET | `/api/v1/training/workouts/<workout_reference>` | `mobile_training.py:141` | required | — |
| POST | `/api/v1/training/workout-sessions` | `mobile_workout_sessions.py:121` | required | `FITX_WORKOUT_SESSIONS_ENABLED` else JSON 404 |
| GET | `/api/v1/training/workout-sessions/current` | `:137` | required | same |
| POST | `/api/v1/training/workout-sessions/<ref>/resume` | `:150` | required | same |
| PUT | `/api/v1/training/workout-sessions/<ref>/checkpoint` | `:165` | required + `If-Match` + `Idempotency-Key` | same |
| POST | `/api/v1/training/workout-sessions/<ref>/abandon` | `:195` | required | same |
| POST | `/api/v1/training/workout-sessions/<ref>/complete` | `:260` | required + multipart | same |
| GET | `/api/v1/nutrition/diary/today` | `mobile_nutrition.py:31` | required | — |
| GET | `/api/v1/nutrition/foods/search` | `:72` | required | — |
| GET | `/api/v1/nutrition/foods/fatsecret/<food_id>/servings` | `:84` | required | — |
| GET | `/api/v1/nutrition/foods/barcode` | `:98` | required | — |
| POST | `/api/v1/nutrition/logs` | `:113` | required | — |
| PATCH | `/api/v1/nutrition/logs/<entry_token>` | `:174` | required | — |
| DELETE | `/api/v1/nutrition/logs/<entry_token>` | `:214` | required | — |
| POST | `/api/v1/pump-checks` | `mobile_pump_checks.py:23` | required | — |
| GET | `/api/v1/pump-checks` | `:79` | required | — |
| GET | `/api/v1/pump-checks/<token>` | `:107` | required | — |
| POST | `/api/v1/pump-check-comparisons` | `mobile_pump_check_comparisons.py:22` | required | — |
| GET | `/api/v1/pump-check-comparisons/<id>` | `:67` | required | — |

**Absent on `/api/v1` (allow-list is exact, `tests/test_mobile_auth_feature_gate.py:106-154`):**
native Coach, native Progress, supplements, social, challenges, notifications,
wearables, weekly-program, web `/ask`, web `/workout/*`.

Unmatched `/api/v1` paths return **HTML** `404.html`, not the mobile envelope.

Native 403 is not part of the current vocabulary. Missing Bearer → 401
`AUTH_SESSION_EXPIRED`. Unconfirmed login → 401 `AUTH_INVALID_CREDENTIALS`
(ADR 0001's `AUTH_VERIFICATION_REQUIRED` is **STALE** vs code).

### 5.2 Domain readiness (server)

| Domain | Route exists | Auth | Flag | Production-ready? | Staging exercisable? | Tests |
|---|---|---|---|---|---|---|
| Auth / account | yes | login/refresh/logout pre-auth; me required | `MOBILE_AUTH_ENABLED` default OFF, lifecycle **blocked** (stale) | Code supports it. Host ON as of 2026-08-26 — **NEEDS REVALIDATION** | yes (2026-09-09 API tooling) | `test_mobile_auth_*`, `test_auth_contract.py` |
| Today | yes | required | sessions flag only for v2 session slice | projection; gated by mobile-auth host flag | yes | `test_mobile_today_*` |
| Plan read / workout detail / preferences | yes | required | mobile-auth | yes as contracts | yes | `test_mobile_training_*` |
| Plan generation | yes | required | first-plan only; 409 `TRAINING_PLAN_REPLACEMENT_REFUSED` if an active plan exists | yes as first-plan | yes | `test_mobile_training_generation_*` |
| WorkoutSession | yes | required | **`FITX_WORKOUT_SESSIONS_ENABLED` default OFF** — routes registered, answers JSON 404 while dark | **dark**; not production-activated | **yes, already exercised** then rolled back | `test_mobile_workout_sessions_*` |
| Nutrition | yes | required | mobile-auth | yes as contracts | yes | `test_mobile_nutrition_*`, diary/log-food suites |
| Pump Check + comparison | yes | required | mobile-auth | yes as contracts; needs provider + object store | staging object store is unset (`docs/STAGING.md` — `S3_BUCKET_NAME` unset) | `test_mobile_pump_check_*` |
| Coach | **no** | — | — | n/a | n/a | allow-list proves absence |
| Progress | **no** | — | — | n/a | n/a | allow-list proves absence |

Do not infer availability from service existence. Coach and Progress **web**
services exist; they are not native routes.

---

## 6. Mobile production-composition map

**VERIFIED IN CODE** at axisai-mobile `7f354e3`. Entry is always
`AppComposition.configured` (`lib/main.dart:11-23`). Release omitted composition
throws (`lib/app/app.dart:39-45`). Fixtures are development-only.

| Domain | Production composition | Live backend contract | Auth dependency | Launch severity | Owner | Required Sprint |
|---|---|---|---|---|---|---|
| Auth | `NativeAuthRepository` only if compile-time flag ON + valid origin | `/api/v1/auth/*`, `/account/me` | `AXISAI_NATIVE_AUTH_ENABLED` | **P1** for any real-user binary | auth | **15** (enablement + soak, not “login class exists”) |
| Today | `LiveTodayRepository` when auth ON; else Unavailable | `GET /api/v1/today` | protected transport | launch-critical | today | already on main |
| Plan read | `LiveWeeklyPlanRepository` when auth ON | `GET /api/v1/training/plans/current` | same | launch-critical | plan | already on main |
| Training preferences | Live when auth ON | `GET /api/v1/training/preferences` | same | first-plan | training | already on main |
| Plan generation | Live + secure pending store when auth ON | `POST /api/v1/training/plans` | same | first-plan | training | already on main (`#19`) |
| Workout detail | `LiveWorkoutDetailRepository` when auth ON | `GET /api/v1/training/workouts/<ref>` | same | read live; **start not wired** | workout | **15** (start, not another GET) |
| Workout session | **`UnavailableWorkoutSessionRepository` always** | backend six-route contract exists; **client interface is `getCurrentWorkoutSession()` only** | n/a in production (throws `unsupported`) | **P1 — advertised loop is dead** | workout | **15** |
| Nutrition | Live when auth ON; `null` when OFF | diary + search + log mutations | same | not required to close the workout loop | nutrition | park vs core loop; already live |
| Pump Check | Live when auth ON | `/api/v1/pump-checks*` | same | not core loop | pump_check | park |
| Progress summary | **Unavailable** | **no native backend route** | none | reachable tab, honest empty | progress | **park** |
| Coach | **no repository**; `UnavailableState` copy | **no native backend route** | shell only | reachable tab, honest empty | coach | **park** |
| Profile / signup / reset | **no feature implementation** | `profile_complete` parsed, unused for routing | login/logout only | not required for internal staging | auth | out of scope |

Unavailable is not automatically a P1. Coach and Progress are honest empty
tabs. Workout **execution** is a P1 because Today/Plan advertise start/resume
into a dead session (`workout_detail_screen.dart` “Start workout unavailable”;
`active_workout_screen.dart` `showFinishUnavailable`).

Composition comment at `app_composition.dart:208-210` still says “until real
backends exist.” **That sentence is stale.** The backend session contract
exists. The client does not.

---

## 7. Auth contract

Native request auth is **opaque server-issued credentials**, not a device-held
Cognito JWT (`docs/adr/0001-native-mobile-authentication.md`). Cognito remains
the password/token authority; Fernet-encrypted Cognito tokens live server-side.

### 7.1 Server (**VERIFIED IN CODE**)

| Step | Behavior |
|---|---|
| Login | `USER_PASSWORD_AUTH` inside `blocking_concurrency_slot`; issues opaque access/refresh |
| Request auth | Bearer → hashed `MobileAccessCredential` → decrypt stored Cognito **access** JWT → JWKS RS256, `exp` leeway **0**, `token_use=access`, audience/client_id match, `sub` bound to `User.cognito_sub` |
| Refresh | Client calls `POST /api/v1/auth/refresh`. Server rotates opaque refresh (reuse after grace revokes the family) and may refresh Cognito **on that call only**. Ordinary GET/POST do **not** silently refresh. |
| Access TTL | default **900 seconds** (`.env.example`) |
| Logout | local family revoke is authority; Cognito revoke is best-effort |
| Rate limits | login 10/min + 50/hour IP; 15/15min per username on 401; refresh/logout configurable |
| PII | security events log `family_id` / `request_id`, not tokens. Request logs on `/api/v1` use `user=-` and the route template. Workout-session logs still include numeric `user_id` (known, not a P1). |

Hardening PR4 capacity hole is **closed in code**. The registry “blocked until
PR4” line is documentation debt.

### 7.2 Mobile (**VERIFIED IN CODE** at `7f354e3`)

| Step | Status |
|---|---|
| Compile-time flag | `AXISAI_NATIVE_AUTH_ENABLED` default **false** (`lib/core/config/native_auth_rollout.dart:6-10`) |
| Sign-in | username/password `LoginScreen` when flag ON. **No signup, verify, reset** |
| Secure storage | `flutter_secure_storage`; Android Keystore namespace `com.axisaiapp.axisai.native_auth`; iOS Keychain `unlocked_this_device`, not synchronizable |
| Cold-start restore | `AuthController.restore()` from `/auth/loading` |
| Refresh | proactive within 30s of access expiry; 401 path; `RefreshCoordinator` single-flight |
| 401 | refresh once; replay only `ReplayPolicy.safe` / `idempotent`; never replay `never` |
| Logout | epoch invalidate, tombstone, `POST /auth/logout` |
| Account switch | logout then login; username change resets feature sessions |
| Background/resume | navigation re-evaluates; **no auth refresh-on-resume** |
| Origin policy | release = HTTPS only. Android **debug** may use loopback HTTP (`localhost` / `127.0.0.1` / `10.0.2.2`). iOS debug = HTTPS like release (`lib/core/config/api_environment.dart:15-21`, `lib/main.dart:18-22`) |

**Code can support** (flag ON + valid origin): cold start → restore → live read
→ mutation with 401 refresh → logout → clean unauthenticated state.

**A default compile cannot.** Login tests passing ≠ launch-ready.

### 7.3 Token lifecycle vs soak

Because opaque access TTL is 900s, a 90-minute soak covers multiple natural
refresh cycles. A 24-hour production soak is **not** required to observe token
lifecycle. If a refresh must be forced inside a shorter window, the client
already refreshes when access is within 30s of expiry — a bounded wait is
enough. Do not invent a production-token injection method.

---

## 8. WorkoutSession mobile state

| Layer | State | Evidence |
|---|---|---|
| Backend authority | **READY** (dark) | six `/api/v1/training/workout-sessions*` routes; canonical `workout_session` + `workout_completion`; Sprint 14 tests + staging exercise |
| Flutter interface | **read-only stub** | `WorkoutSessionRepository.getCurrentWorkoutSession()` only (`workout_session_repository.dart:3-5`) |
| Live implementation | **absent** | no `LiveWorkoutSessionRepository` |
| Production wiring | **Unavailable** | `app_composition.dart:129,166` |
| Start | **BLOCKED** | detail CTA disabled |
| Current / resume | resume route opens session screen, which then hits Unavailable |
| Checkpoint / complete / abandon | **absent** on the client. Finish UI is `showFinishUnavailable` |
| Revision / conflict / stale-plan / idempotency | **absent** on the session client (detail reads have `mutationVersion`) |
| Product UX | foundation/fixture UX only | controller holds local drafts; no server writes |

**Backend READY ≠ Flutter WIRED ≠ Flutter PRODUCT UX COMPLETE.**

Sprint 14 server correctness is **not** reopened. No current-main regression
was in scope for this docs-only discovery.

A load-bearing mobile architecture test currently **forbids** Training POSTs
other than plan generation (`test/architecture/training_read_boundaries_test.dart`).
A live session client **must** update that allow-list in the same Flutter PR
that adds the calls. Leaving it unchanged would either block the work or make
the guard vacuous.

---

## 9. Launch-critical user journey

From current native navigation (Today · Plan · Coach · Progress) with
native-auth **ON**. Default compile is the Unavailable shell and is omitted.

| Step | Status |
|---|---|
| Launch app | LIVE bootstrap `/auth/loading` if flag ON; else DARK |
| Unauthenticated / authenticated bootstrap | LIVE restore |
| Sign in | LIVE username/password. No signup |
| Today | LIVE `GET /api/v1/today` |
| Plan | LIVE `GET /api/v1/training/plans/current` |
| Workout detail | LIVE read from Plan in-memory ref |
| Start workout | **BLOCKED** |
| Checkpoint | **BLOCKED** |
| Background / reload / restore session | **BLOCKED** |
| Complete | **BLOCKED** |
| Nutrition read/write | LIVE when auth ON — **not required to close the core loop** |
| Progress metrics | UNAVAILABLE — **NOT REQUIRED FOR V1** |
| Pump Check | LIVE under Progress tab — optional |
| Coach | UNAVAILABLE — **NOT REQUIRED FOR V1** |
| Logout | LIVE |
| Relaunch | LIVE restore if the same binary still has the flag ON |

The bottleneck is not “auth classes missing.” It is **compile-time flag default
OFF** plus **WorkoutSession unwired**.

---

## 10. Staging / mobile access topology

Staging facts from `docs/STAGING.md` (not re-probed live in this discovery):

- Stopped by default. Own Postgres/Redis containers. Own Cognito pool.
  Synthetic accounts. Zero inbound. No public DNS. **No TLS certificate.**
- Operator reachability is Session Manager port-forward to loopback, documented
  in `docs/STAGING.md` §6. Substituting public ingress is forbidden.
- `.env.staging.example`: `MOBILE_AUTH_ENABLED=1`,
  `FITX_WORKOUT_SESSIONS_ENABLED=0`, `RUNTIME_METRICS_ENABLED=1` with namespace
  `AxisAI/Staging/Runtime`.
- After the 2026-09-09 exercise the sessions flag was returned to `0` and the
  instance was stopped. Instance state **today: UNKNOWN**.
- Flutter was **NOT EXERCISED** in that exercise.

### How a Flutter client can reach staging without opening inbound

Investigated, not assumed. There is **no** Flutter/emulator procedure in this
repository today.

| Topology | Isolation-preserving? | Client origin policy | Status |
|---|---|---|---|
| Workstation + documented loopback forward | yes | n/a (API tooling already used) | VERIFIED IN DOCS of Sprint 14 |
| Android emulator debug | **yes, if** the forward listens on the workstation and the app uses loopback (`10.0.2.2` or adb reverse to `localhost`) | Android debug **allows** loopback HTTP | **NOT EXERCISED** — this is the Sprint 15 soak topology |
| iOS simulator | isolation yes | iOS debug **requires HTTPS** | **BLOCKED** by client policy + no staging TLS. **NOT** an architecture defect of WorkoutSession. Record: `NOT EXECUTED — TOOLING/PLATFORM CONSTRAINT` for iOS soak |
| Physical device | device cannot use operator loopback | release requires HTTPS | **not supported** without a new isolation-preserving tunnel. Do not open inbound to make it convenient |
| Production HTTPS origin | **not staging** | release builds can speak HTTPS | **out of scope** (no production soak) |

**Do not weaken staging isolation to make iOS or physical-device soak convenient.**

Staging WorkoutSession soak additionally requires a **temporary** staging-only
flip of `FITX_WORKOUT_SESSIONS_ENABLED=1` for the window, then back to `0`,
exactly as Sprint 14 did. That is operator work on staging, not a production
activation, and not a Flutter PR.

---

## 11. Observability

If `RUNTIME_METRICS_ENABLED=1` (staging example: yes; production last live dump
2026-08-26: **unset / OFF** — **NEEDS REVALIDATION**):

| Signal | Exists? | Enough for soak GO/NO-GO? |
|---|---|---|
| `AuthOutcomes` (`Path=mobile\|web` × bounded Outcome) | yes | yes for auth |
| `HttpRequests` / `HttpLatency` / `HttpServerErrors` / `HttpThrottled` / `HttpOverload` | yes; dimensions Blueprint + Status class + Client | yes; cannot split 401 vs 403 in metrics (logs can) |
| Token refresh named metric | **no** | refresh is `POST /api/v1/auth/refresh` in logs + `mobile_api` HTTP SLIs |
| `WorkoutSessionLifecycle` (`Event` only) | yes, while sessions flag ON | yes for session counts; staging 2026-09-09 already saw started/checkpointed/completed/abandoned/revision_conflict |
| Per-route high-cardinality path | **no, by design** | do not add |

An authenticated soak **can** produce GO/NO-GO without new high-cardinality
metrics **on staging**, where metrics are already namespaced to
`AxisAI/Staging/*`. Do not build an observability platform. Do not require
production `RUNTIME_METRICS_ENABLED=1` to close Sprint 15 — that is a
production-ops prerequisite for a later production rollout, not for an
isolated staging client proof.

Minimum extra if logs are insufficient during the soak: none planned. Abort if
`HttpOverload` / unexpected 5xx / `AuthOutcomes` `session_invalid` spike /
`WorkoutSessionLifecycle` `revision_conflict` spike without a matching test
command / `IntegrityError` on `WorkoutSession`.

---

## 12. Security / privacy

| Topic | Finding |
|---|---|
| Device token storage | Keystore / Keychain; complete pair or tombstone. Namespaces documented in the native-auth client design |
| Server token storage | opaque credentials hashed; Cognito tokens Fernet-encrypted |
| JWT in logs | not logged; do not add |
| Cookies vs Bearer | mobile is Bearer-only, CSRF-exempt |
| Refresh | device holds opaque refresh only; server holds Cognito refresh |
| Debug logging | `FLASK_DEBUG` default 0; host value UNKNOWN |
| Synthetic staging credentials | `.invalid` emails; no CustomEmailSender. Password is **not** in the repo (known gap in `docs/STAGING.md` §7). Do not paste it into chat, docs, or tests |
| Cross-user | principal from hashed credential; session writes owner-scoped |
| Retries | checkpoint requires `If-Match` + `Idempotency-Key`; 413 is non-retryable |
| Logout | local revoke is authority |
| Staging vs production Cognito | **different pools**. A production-compiled client id will not authenticate to staging |

No real user data. No production token. No secret in this document.

---

## 13. Android / iOS readiness

### Android — **VERIFIED IN CURRENT REPO CONFIG**

| Item | Value |
|---|---|
| Application id | `com.axisaiapp.axisai` |
| minSdk / targetSdk | `flutter.minSdkVersion` / `flutter.targetSdkVersion` (not pinned as integers in-repo). CI Flutter **3.44.8** |
| Release signing | **debug keys** (`android/app/build.gradle.kts` release block) |
| Network | main: no cleartext. Debug-only loopback exception |
| Env injection | `--dart-define` only |
| Native auth default | false |
| CI | Ubuntu: `flutter test` + debug APK with flag OFF and ON (`https://api.example.invalid`). **No Play upload** |

### iOS

| Item | Value |
|---|---|
| Bundle id | `com.axisaiapp.axisai` |
| Deployment target | 13.0 |
| Signing | `iPhone Developer`; no `DEVELOPMENT_TEAM` in inspected configs |
| Entitlements | empty `keychain-access-groups` |
| CI | macOS: tests + `flutter build ios --simulator --no-codesign` with auth ON + dummy origin |
| This workstation | Windows — device/archive **NOT EXECUTED — TOOLING/PLATFORM CONSTRAINT** |

### Four planes

| Plane | Status |
|---|---|
| Code readiness | Partial: live reads + first-plan + nutrition + pump + auth client. Session execution missing. Flag defaults off |
| CI readiness | Strong for tests, debug Android, unsigned iOS simulator. No store artifacts |
| Operator hardware | Windows cannot iOS-archive; macOS CI covers simulator compile |
| Store-signing | **Not ready.** Android release uses debug signing. No TestFlight/Play job |

Architecture is **not** blocked because this machine cannot iOS-build.

---

## 14. Findings

### F-1 — Native WorkoutSession client is missing after a ready server — **P1**

- **Surface:** axisai-mobile production composition + workout UX
- **Current state:** Unavailable repository; read-only interface; start/finish CTAs explicitly unavailable
- **Evidence:** `app_composition.dart:129,166`; `workout_session_repository.dart:3-5`; `workout_detail_screen.dart`; `active_workout_screen.dart` — **VERIFIED IN CODE**
- **User/release consequence:** an authenticated user can see Today/Plan/detail and cannot execute a workout
- **Why now:** Sprint 14 closed the server half; this is the remaining launch-critical hole
- **Live/dark/unshipped:** unshipped client; server dark
- **Owner:** axisai-mobile

### F-2 — `AXISAI_NATIVE_AUTH_ENABLED` defaults false; no ON binary has been soaked — **P1**

- **Surface:** compile-time flag + staging topology
- **Current state:** default false; CI builds ON only against `api.example.invalid`; Flutter never hit isolated staging
- **Evidence:** `native_auth_rollout.dart:6-10`; `flutter-ci.yml`; Sprint 14 “Flutter NOT EXERCISED”
- **User/release consequence:** the only shippable default binary has no auth graph
- **Why now:** historical Sprint 15 ownership; now there is an isolated environment to prove it without a production soak
- **Live/dark/unshipped:** dark compile-time
- **Owner:** axisai-mobile (binary) + operator soak (staging)

### F-3 — Isolation-preserving Flutter soak topology is Android-debug-only — **P2**

- **Surface:** staging access × client origin policy
- **Current state:** staging has no TLS; release and iOS debug require HTTPS; Android debug allows loopback HTTP
- **Evidence:** `api_environment.dart:15-21`; `docs/STAGING.md` §1/§6
- **User/release consequence:** Sprint 15 can prove an Android debug production-composition binary against staging. It cannot prove an iOS or release-HTTPS binary against staging without adding TLS (forbidden here)
- **Why now:** choosing the soak environment incorrectly would either open inbound or imply a production soak
- **Live/dark/unshipped:** operational
- **Owner:** operator procedure in a later PR; do not change isolation

### F-4 — `MOBILE_AUTH_ENABLED` lifecycle record is stale — **P2** (docs integrity)

- **Surface:** `app/feature_flags.py`, `docs/FEATURE_FLAGS.md`, `docs/AUTH_CONTRACT.md` vs `docs/ROLLOUT.md`
- **Current state:** registry `blocked` + “until PR4”; `ROLLOUT.md` says PR4 merged and production already `1` on 2026-08-26
- **Evidence:** `feature_flags.py:535`; `FEATURE_FLAGS.md:271-279`; `ROLLOUT.md:111`; Sprint 14 F-4
- **User/release consequence:** an operator who reads the registry will think `/api/v1` is off
- **Why now:** Sprint 15 was assigned this record. It does **not** block the Android staging soak if operators follow `ROLLOUT.md`
- **Live/dark/unshipped:** documentation
- **Owner:** fitness-coach docs/registry in a later bounded PR — **not this discovery**

### F-5 — Production `RUNTIME_METRICS_ENABLED` last seen OFF — **P2** for *production* rollout, **not** for staging soak

- **Surface:** production host
- **Current state:** 2026-08-26 deep health unset; staging example ON
- **Evidence:** Sprint 12 readiness §26; `.env.staging.example:99-100`
- **User/release consequence:** a later production native-auth distribution would lack HTTP SLIs
- **Why now:** do not fold production metrics activation into the Flutter objective
- **Live/dark/unshipped:** operational; **NEEDS REVALIDATION**
- **Owner:** production ops, later

### F-6 — No native Coach or Progress API, and no live client — **P2** parked

- **Surface:** primary IA destinations
- **Current state:** honest Unavailable / placeholder UI; no `/api/v1/coach*` or `/api/v1/progress*`
- **Evidence:** route allow-list; `coach_screen.dart`; `unavailable_progress_summary_repository.dart`
- **User/release consequence:** two of four tabs are empty in an ON build
- **Why now:** empty tabs are not the advertised workout-loop blocker. Pulling both would be a different sprint
- **Live/dark/unshipped:** unshipped native domains
- **Owner:** later product sprint

### F-7 — Store signing / TestFlight / Play are not ready — **P3** for this sprint (would be P1 for public distribution)

- **Surface:** Android release debug-signed; iOS no team; no store CI
- **Evidence:** `android/app/build.gradle.kts`; `flutter-ci.yml`
- **User/release consequence:** cannot submit stores
- **Why now:** public submission is not the Sprint 15 milestone
- **Owner:** later distribution work

### F-8 — `ROLLOUT.md` flag-9 relative link is one directory too high — **P3**

- **Evidence:** Sprint 14 PR5 §10.2
- **Owner:** same docs PR as F-4
- **Do not fix in this discovery**

No P0. No security/data-integrity catastrophe on current main.

---

## 15. Candidate objectives

Scoring: 1–5, higher better except scope and cross-repo (lower better).

| Dimension | A. Auth ON + remaining Live reads | B. Flutter session wiring only | C. Launch-critical slice + staging soak | D. Store / release engineering | E. Native Coach + Progress |
|---|:--:|:--:|:--:|:--:|:--:|
| User value | 3 | 4 | **5** | 2 | 4 |
| Launch criticality | 4 | 4 | **5** | 2 | 3 |
| Risk reduction | 3 | 4 | **5** | 2 | 2 |
| Architectural leverage | 3 | **5** | **5** | 2 | 4 |
| Dependency unlocking | 3 | 4 | **5** | 1 | 2 |
| Scope size (lower better) | **2** | 3 | 3 | 4 | 5 |
| Testability | 4 | **5** | **5** | 2 | 3 |
| Staging proof availability | 3 | 3 | **5** | 1 | 1 |
| Cross-repo complexity (lower better) | **2** | **2** | 3 | 3 | 5 |
| Time-to-proof (higher = faster) | 4 | 3 | **4** | 1 | 1 |

**A** turns the compile-time flag on without making the advertised workout
real. An ON build that still cannot start a workout is not launch-hardening.

**B** wires the missing client but skips the only isolated proof path. Green
unit tests would repeat Sprint 12's mistake: “login exists.”

**C** is the only candidate that (i) closes the advertised loop, (ii) uses the
isolated environment that now exists, (iii) observes token lifecycle without a
production soak, (iv) keeps store submission and production flag flips out.

**D** is release engineering before the product loop exists.

**E** is a full native Coach/Progress program. No native backend routes exist.
It is not required to satisfy C.

---

## 16. Rejected candidates

| Candidate | Reason |
|---|---|
| Generic UI polish | No evidence UI is the bottleneck. Start/finish are *functionally* unavailable, not ugly |
| Sprint 14 P2 cleanup as the objective | P2-1/3/5/6/8 do not block C. P2-4/P2-7 are **CONDITIONAL** and belong as soak *observations*, not the objective |
| Nutrition post-closure backlog | Sprint 13 closed; native nutrition is already live when auth ON |
| Native Coach as Sprint 15 | No `/api/v1` Coach. Honest empty tab. Not required for C |
| Native Progress summary as Sprint 15 | No `/api/v1` Progress. Pump Check on that tab is already live. Park summary |
| Production `FITX_WORKOUT_SESSIONS_ENABLED=1` | Staging-only flip for the soak window is enough. Production activation is a separate operator decision after C |
| Public App Store / Play submission | No authoritative roadmap assigns it here. Signing is not ready. Smallest proof is internal Android debug vs staging |
| New AI features / Coach streaming restoration | Sprint 14 Candidate B; web problem; not native launch-hardening |
| 24-hour **production** soak | Forbidden by this brief. Superseded as the *first* proof by isolated staging. Remains a later production-distribution prerequisite, not Sprint 15 |
| Production `RUNTIME_METRICS_ENABLED=1` as the objective | Needed for a later production rollout; staging already has namespaced metrics |

---

## 17. Selected Sprint 15 objective

```text
Sprint 15 objective:
"An AXISAI_NATIVE_AUTH_ENABLED production-composition Android debug build can
authenticate a synthetic staging user over the isolation-preserving loopback
path in docs/STAGING.md, restore a session, read live Today/Plan/workout-detail,
execute a revision-gated WorkoutSession lifecycle against /api/v1, and survive
a bounded authenticated soak, without activating production flags or submitting
a store build."
```

Cross-repo coherent: Flutter owns the client and composition; fitness-coach
owns staging operator procedure and must **not** change production flags.
Bounded: one platform (Android debug), one environment (isolated staging), one
core loop. Testable: architecture + repository + widget tests, then a recorded
staging soak. Not a list of tickets.

---

## 18. S15 acceptance criteria

| ID | Criterion | Owner | Evidence class |
|---|---|---|---|
| **S15-1** | A production-composition build with `AXISAI_NATIVE_AUTH_ENABLED=true` and a loopback origin wires `Live*` for Today, Plan, workout-detail, nutrition, preferences, plan generation, pump-check, **and** a live WorkoutSession repository. It still must not wire `Fixture*`. Auth-disabled composition remains all-Unavailable. | axisai-mobile | architecture + composition tests |
| **S15-2** | The live session repository implements start, current, resume, checkpoint (base revision + idempotency key), complete, and abandon against the existing `/api/v1/training/workout-sessions*` contract. `getCurrentWorkoutSession()` is not the only method. | axisai-mobile | unit/repository + contract tests |
| **S15-3** | From Plan → workout detail, Start is enabled when the server says the workout is startable. A checkpoint advances revision. A stale `If-Match` mutates nothing and surfaces a typed reread. Finish is no longer the unavailable stub. | axisai-mobile | widget/unit + emulator |
| **S15-4** | Cold start restores an authenticated session from secure storage and issues a live Today read without re-typing the password. | axisai-mobile | existing auth tests + staging |
| **S15-5** | Access-credential expiry inside the soak window results in a successful refresh (single-flight) and continued authenticated reads/writes. Logout leaves a credential-free tombstone; relaunch is unauthenticated. | axisai-mobile | auth tests + staging |
| **S15-6** | Against isolated staging, with `MOBILE_AUTH_ENABLED=1` and a temporary staging-only `FITX_WORKOUT_SESSIONS_ENABLED=1`, one synthetic user completes the journey in §9 (excluding parked Coach/Progress). Staging inbound remains zero. Production flags are untouched. | operator + both repos | real staging + Android emulator |
| **S15-7** | Soak GO: duration in §21; no unexpected 5xx; no family-reuse detections; `WorkoutSessionLifecycle` counts coherent; sessions flag returned to `0` on staging; instance stopped after. | operator | real staging + metrics/logs |
| **S15-8** | Android debug APK with native-auth ON builds in CI (dummy origin) **and** locally for the soak (loopback origin). iOS simulator compile remains the existing unsigned CI job; iOS staging soak is **not** required. | axisai-mobile | CI + Android emulator |
| **S15-9** | Production `FITX_WORKOUT_SESSIONS_ENABLED` remains OFF. Production `AXISAI_NATIVE_AUTH_ENABLED` is not distributed. No store submission. Production mutations from Sprint 15 implementation PRs = 0 unless a later *separate* operator task says otherwise. | both | static/docs + deploy records |

No criteria for Coach content, Progress summary, signup, or store signing.

---

## 19. PR sequence

Do not hide infrastructure inside the soak PR. Do not mix backend runtime
changes with Flutter wiring.

| PR | Repo | Responsibility | Depends on | Expected layers | Tests | Explicitly does NOT | Exit criterion |
|---|---|---|---|---|---|---|---|
| **PR1** | fitness-coach | this discovery | — | `docs/superpowers/specs/2026-09-09-sprint15-pr1-mobile-launch-hardening-discovery.md` | deploy-governance + flag registry + session architecture + secret scan | implementation, flags, Flutter, push | independent review |
| **PR2** | axisai-mobile | Live WorkoutSession repository + DTO/mapper + composition wiring when auth ON | PR1 merged | `lib/features/workout/data/live_workout_session_repository.dart` (new), domain interface expansion, `app_composition.dart`, architecture allow-list | repository + `training_read_boundaries_test` update + fixture-boundary tests | UX start/finish, staging, flags | composition test: auth ON wires Live session; auth OFF still Unavailable; no Fixture |
| **PR3** | axisai-mobile | Session UX: start from detail, checkpoint, revision conflict, complete, abandon, snapshot restore | PR2 | controller, `active_workout_screen`, `workout_detail_screen`, router start path | widget/unit | Coach/Progress, store, iOS soak | S15-3 tests green; finish unavailable stub gone |
| **PR4** | axisai-mobile | Failure/recovery hardening on the new writes: 401 refresh during checkpoint, replay policies, no refresh-on-resume documented if still absent | PR3 | authenticated transport usage on session writes; tests | auth+session integration tests | new metrics backend | mutation tests prove stale revision and 401-refresh paths are not vacuous |
| **PR5** | fitness-coach (docs/runbook) + operator | Authenticated staging soak / launch-readiness closure. Document Android emulator loopback procedure by **reference** to `docs/STAGING.md` (edit STAGING only if the Flutter loopback steps must live next to the existing operator path). Staging sessions flag ON for the window, then OFF. | PR3 (PR4 if recovery gaps are real) | `docs/STAGING.md` additive Flutter subsection **or** a new `docs/mobile/` soak runbook that references STAGING and does **not** name disallowed deploy verbs | `tests/test_deploy_workflow.py` must stay green | production flag flips, store submission, iOS soak, inbound rules | S15-6/S15-7 recorded GO or NO-GO |

Optional **docs-only follow-on** (not the objective): fitness-coach F-4/F-8
lifecycle honesty (`MOBILE_AUTH_ENABLED` registry vs `ROLLOUT.md`). Do it as
its own PR so discovery review stays uncontaminated.

Backend runtime PRs are **not** required for C unless soak evidence shows a
server regression. Do not reopen Sprint 14 session correctness speculatively.

---

## 20. Test strategy

| Layer | When | Notes |
|---|---|---|
| Unit / repository | every Flutter PR | live session mapper, revision errors, idempotency key |
| Architecture | every Flutter PR | composition Live vs Unavailable vs Fixture; Training route allow-list **updated**, not deleted |
| Contract | PR2 | request/response field names vs backend envelope |
| Widget | PR3 | start enabled; finish no longer unavailable stub; conflict reread |
| Flutter existing auth suite | PR4 + CI | do not rewrite; add session-write 401 cases |
| Android debug build | CI already; soak locally | dummy origin in CI; loopback origin on operator machine |
| iOS simulator build | existing CI only | not a soak |
| PostgreSQL | not in Flutter PRs | backend already covered; do not add backend tests “while here” |
| Staging E2E soak | PR5 | one synthetic user; recorded |
| Full mobile suite locally | not after docs-only edits | CI on Flutter PRs |
| `tests/test_deploy_workflow.py` | this PR and any later fitness-coach docs PR | **must run before commit** |

### Mutation / non-vacuity (load-bearing)

| Guard | Why it can go vacuous | Required mutation |
|---|---|---|
| `training_read_boundaries_test.dart` single Training POST | adding session writes without updating it fails CI; deleting it removes the guard | extend the allow-list to the six session paths **and** keep plan-generate |
| `production_fixture_boundaries_test.dart` | a Live session that internally loads fixtures would still type-check as Live | assert constructed session type name starts with `Live`, not `Fixture` |
| Checkpoint stale revision | a client that ignores `If-Match` can look green | test that a stale base performs zero local accept and asks for reread |
| Auth-disabled composition | wiring Live session even when flag OFF | existing Unavailable assertion must keep passing |
| Staging vs production origin | a test that uses a remote HTTP URL would pass parse in debug Android and must not in release | keep `api_environment_test.dart` release-HTTPS invariant |

---

## 21. Soak contract

**In Sprint 15. Not a production soak.**

| Field | Value |
|---|---|
| Environment | isolated staging (`docs/STAGING.md`). Zero inbound preserved |
| Build | Android **debug**, `AXISAI_NATIVE_AUTH_ENABLED=true`, loopback origin only |
| Device | Android emulator on the operator workstation that runs the documented loopback forward |
| Account | synthetic staging user (`docs/STAGING.md` §7). Do not put the password in this repo |
| Duration | **90 minutes** wall-clock after first successful login. Rationale: access TTL 900s ⇒ ≥5 natural refresh opportunities. Not a vanity 24h |
| Traffic | human-paced, one account: login, Today, Plan, start, ≥3 checkpoints, background the app ≥1, restore, complete, nutrition read, logout, relaunch, login again |
| Token events | at least one refresh that is not operator-forced if the window includes a 15-minute wait; if the wait is skipped, record “refresh NOT EXERCISED naturally” and run one forced near-expiry path in tests instead of claiming soak coverage |
| Network | one airplane-mode or disconnect/reconnect on the emulator if practical; else mark NOT EXERCISED |
| Acceptable errors | typed 409 reread after an intentional stale checkpoint; 401 that recovers via refresh |
| Unacceptable | 5xx, family reuse, IntegrityError, unexpected 401 after refresh, fixture copy on screen, production origin used |
| Metrics | staging `AuthOutcomes` Path=mobile, `Http*` Client=mobile, `WorkoutSessionLifecycle` |
| Abort | any unacceptable error; any production host change; any inbound-rule change |
| Rollback | staging sessions flag → `0`; stop staging instance; keep production untouched |
| iOS | **not in this soak** (F-3) |
| Physical device | **not in this soak** (F-3) |

GO is binary: S15-6 and S15-7 both PASS, or Sprint 15 is not closed.

---

## 22. Rollback / abort rules

| Surface | Rollback | Must not require |
|---|---|---|
| Mobile build / config | ship a binary compiled with `AXISAI_NATIVE_AUTH_ENABLED=false` (or stop distributing the ON debug APK) | database downgrade |
| Server runtime flag | unchanged in production. Staging sessions flag back to `0` | production change |
| Staging config | restore `.env` sessions=0, metrics retained as in the example | production `.env` |
| Production config | **do not change it in Sprint 15** | — |

If launch-critical auth fails (cannot login/restore/refresh/logout against
staging): distribution remains closed; S15-4/S15-5 fail.

If WorkoutSession client fails: do not paper over with fixtures; S15-1–S15-3 fail.

If Coach or Progress remain Unavailable: **product scope excludes them** for
v1 staging proof. That is an explicit GO for C, not an ambiguous GO.

A staging test failure must not mutate production. A mobile rollback must not
require a database downgrade.

---

## 23. Flag strategy

Discovery changes **no** flag.

| Flag | Repo default | Staging desired during soak | Staging after soak | Production desired in Sprint 15 | Who controls | When it may change |
|---|---|---|---|---|---|---|
| `MOBILE_AUTH_ENABLED` | OFF (registry) | **1** (already in staging example) | 1 | leave as currently deployed; **do not enable again** if already 1 | host `.env` | not in Sprint 15 implementation PRs |
| `AXISAI_NATIVE_AUTH_ENABLED` | false (compile-time) | true **in the soak APK only** | n/a (binary) | **false in any distributed store/release binary** | Flutter `--dart-define` | soak APK; rollback = new binary |
| `FITX_WORKOUT_SESSIONS_ENABLED` | OFF | **1 for the soak window only** | **0** | **OFF** | host `.env` | staging operator during PR5; never production in this sprint |
| `RUNTIME_METRICS_ENABLED` | OFF | 1 (staging example) | 1 | UNKNOWN / do not flip here | host `.env` | not in this sprint |

Mobile distribution and server production activation stay decoupled.
Staging session proof does **not** require production session activation.

---

## 24. Carry-forward debt

| Item | Blocks C? | Disposition |
|---|---|---|
| P2-1 unscheduled durable checkpoint refused | **NO** | stay backlog |
| P2-3 AST guard bypasses | **NO** | stay backlog |
| P2-4 flag-ON 429 unproven | **CONDITIONAL** | observe during soak; do not make it the objective. If the single-user soak produces no 429, record NOT EXERCISED (same as Sprint 14) |
| P2-5 unreachable snapshot byte-cap | **NO** | stay backlog |
| P2-6 PG CI file-selection gap | **NO** | stay backlog |
| P2-7 previous-day ACTIVE vs non-resumable read | **CONDITIONAL** | if the soak includes a previous-day ACTIVE, observe. If the environment has none, mark NOT EXERCISED — do not construct production data |
| P2-8 retired idempotency key reuse | **NO** unless the new client recycles keys. PR2 must mint fresh keys per command | Flutter must not regress this |
| docs-consistency / F-4 | **NO** for soak if operators follow `ROLLOUT.md` | later docs PR |
| Coach / Progress native | **NO** | parked |
| Nutrition P2/P3 backlog | **NO** | parked |
| No auth refresh-on-resume | **NO** for 90-minute emulator soak; **P2** if a later physical-device resume is in scope | document; PR4 if cheap |

---

## 25. Out of scope

- Sprint 15 implementation in this PR
- Flutter edits in this PR
- Feature-flag code changes in this PR
- Production flag flips
- Production soak
- Store submission / TestFlight / Play internal testing
- Opening staging inbound or adding a staging certificate
- Native Coach conversations
- Native Progress summary
- Signup / email verify / password reset / account deletion
- Production `RUNTIME_METRICS_ENABLED` activation
- Approving or rejecting any production deploy
- Reopening Sprint 14 session correctness

---

## 26. Risks / unknowns

| Item | Label | Impact |
|---|---|---|
| Current production `.env` (`MOBILE_AUTH_ENABLED`, `RUNTIME_METRICS_ENABLED`) | **NEEDS REVALIDATION** — last live dump 2026-08-26; later deploys do not write `.env` | does not block staging soak |
| Staging instance running **now** | **UNKNOWN** (last recorded: stopped) | PR5 starts it for the window |
| Operator Session Manager plugin on the soak workstation | **UNKNOWN today** | required for the documented loopback path; install is a workstation action, not a code PR |
| Android emulator loopback through the documented forward | **NOT EXERCISED** | F-3; PR5 must prove or fail closed |
| iOS soak | **BLOCKED** by origin policy + no TLS | accepted constraint |
| Staging Pump Check complete-with-photo | object store unset on staging | complete-without-photo path must be used, or record photo NOT EXERCISED |
| `already_completed_today` vs pump-check `date_key=NULL` | documented unfixed server oddity | avoid constructing that row in the soak account |
| Pending production deploy | **none waiting** at discovery time; run 274 already succeeded for `9a881bc` | do not treat that success as Sprint 15 authorization |

---

## 27. Recommended next action

1. Independent exact-SHA review of **this** document at the discovery commit.
2. Only after approval: push / PR / CI / merge of this docs-only change.
3. Then start **PR2 in axisai-mobile**, not a backend runtime PR.

Do not start an implementation branch from pre-review assumptions.

---

## Appendix A — Evidence labels used

VERIFIED IN CODE · VERIFIED BY TEST (suite exists; this discovery ran a focused
backend subset, not the full Flutter suite) · VERIFIED IN CURRENT REPO CONFIG ·
VERIFIED READ-ONLY IN ENVIRONMENT (GitHub Actions deploy run list only) ·
HISTORICAL — NEEDS REVALIDATION · NOT EXERCISED · UNKNOWN · BLOCKED
