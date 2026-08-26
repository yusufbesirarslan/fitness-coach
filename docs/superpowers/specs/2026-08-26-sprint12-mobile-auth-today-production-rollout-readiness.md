# Sprint 12 — Mobile Auth + Today Production Rollout Readiness

Assessment date: 2026-08-26.
Assessor: bounded production-readiness review of current `origin/main`.
This document does **not** activate flags, deploy, distribute a mobile build, or start PR5.

**Verdict (2026-08-26 later gate): WAIT — SOAK IN PROGRESS.**

Earlier same-day assessment: **READY WITH CONDITIONS.** That still describes
architecture. The remaining Stage 2 authenticated production smoke is now
**green**. The 24h backend soak through **2026-08-27 10:40 UTC** is **not**
complete. Native-auth ON distribution remains **NO-GO**.

Backend Stage 1 (`MOBILE_AUTH_ENABLED=1`) is already live on production SHA
`a6d6b2e60dc7718bd47590d64b5f74542294025c`. See §29 for the controlled
smoke + soak gate. This document does not activate flags, deploy, distribute
a mobile build, or start PR5.

---

## 1. Executive verdict

Architecture and code on the assessed SHAs are sound:

- Historical Cognito-thread-exhaustion blocker remains **closed** on current main
  (Hardening PR4 `34f8dc79` is an ancestor).
- `GET /api/v1/today` is inside the gated `/api/v1` blueprint.
- Cross-user isolation is proven by tests; identity is the authenticated principal.
- Native-auth ON + backend unavailable degrades truthfully; **no fixture fallback**.
- Native-auth compile-time default remains **OFF**.

What changed the verdict from “READY FOR STAGED ROLLOUT”:

1. Production already has `MOBILE_AUTH_ENABLED=1` while operator docs still say
   **blocked until PR4**.
2. `RUNTIME_METRICS_ENABLED` is **unset** in host `.env` (repo default OFF).
   CloudWatch `FitX/Runtime` currently lists only capacity gauges
   (`ThreadReserve`, `AiSlotsActive`, `AiModelSlotsActive`, `ScrapeSlotsActive`).
   Documented HTTP abort signals (`HttpOverload`, login/Today SLIs) are **not**
   visible.
3. Authenticated owner-scoped Today **was smoked** against production with the
   dedicated E2E identity on 2026-08-26 ~12:52 UTC — green (see §29). This
   item is closed for Stage 2.
4. Native-auth ON store/TestFlight artifact and previous native-auth-OFF rollback
   build are **not** proven as distributable artifacts.

P0 = 0. Product-code P1 = 0. Operational P1 (HTTP SLIs) remains open for native
expansion; log-only abort is **explicitly accepted for the remaining soak only**
(see §29).

---

## 2. Backend SHA

| Field | Value |
|---|---|
| Authority | `origin/main` |
| SHA | `a6d6b2e60dc7718bd47590d64b5f74542294025c` |
| Subject | `feat(api): add canonical mobile Today endpoint (#245)` |
| Sprint 12 PR3 | **present** (this commit) |
| Hardening PR4 | `34f8dc79be746f233974461bf0465e4bf32eb72d` — ancestor of origin/main |
| GitHub CI | run 360, **success**, same SHA — https://github.com/yusufbesirarslan/fitness-coach/actions/runs/32937212385 |
| Deploy to EC2 | run 227, **success**, same SHA — https://github.com/yusufbesirarslan/fitness-coach/actions/runs/32937704329 |
| Running host SHA | **same** `a6d6b2e60dc7718bd47590d64b5f74542294025c` (`sudo -u ubuntu git -C /home/ubuntu/fitness-coach rev-parse HEAD`) |
| Local checkout | `C:\Users\yusuf\develop\fitness-coach` was 34 commits behind origin/main; **not** used as authority |

---

## 3. Mobile SHA

| Field | Value |
|---|---|
| Authority | `origin/main` |
| SHA | `3386df37198ef0193c64fa4754a686357868f785` |
| Subject | `feat(mobile): converge Today on canonical backend state` |
| Sprint 12 PR4 | **present** (this commit) |
| Sprint 12 PR2A | `91874fa95941b29ffad3ce16757e1666892f26b1` — ancestor |
| Flutter CI | run 32964280423, **success**, same SHA — https://github.com/axisaiapp/axisai-mobile/actions/runs/32964280423 |
| Local checkout | `C:\Users\yusuf\develop\axisai_mobile` was on `mobile/foundation-pr3-core-screens`; **not** used as authority |

CI on this SHA builds:

- Android debug APK with `AXISAI_NATIVE_AUTH_ENABLED=false`
- Android debug APK with `AXISAI_NATIVE_AUTH_ENABLED=true` and `AXISAI_API_BASE_URL=https://api.example.invalid`
- iOS simulator (no codesign) with native-auth **ON** and the same invalid origin

CI does **not** build iOS native-auth OFF. Builds were not distributed.

---

## 4. Architecture summary

Backend canonical Today:

```
MOBILE_AUTH_ENABLED
  → create_app / register_blueprints
  → mobile_api blueprint prefix /api/v1
  → GET /api/v1/today  (@require_mobile_auth)
  → build_today(g.mobile_user.id)
```

Properties already established on this SHA:

- authenticated, owner-scoped
- deterministic, canonical server date (`app.timeutil` / Europe/Istanbul)
- 0 provider/LLM calls
- projection of existing Today/workout authorities
- no second Today authority
- unreadable/corrupt plan content → 200 `status: needs_attention` (not empty, not rest)
- infrastructure read failure → 503 `TODAY_TEMPORARILY_UNAVAILABLE`, not an empty/rest day

Mobile PR4:

| Native auth | Production composition |
|---|---|
| ON | existing authenticated client → `LiveTodayRepository` → `GET /api/v1/today` |
| OFF | `UnavailableTodayRepository` |

The app does not infer rest/completion, independently select Today workout, use
the device clock as canonical Today authority, or fall back to fixtures.

Plan, Progress, and Workout detail/session remain unavailable in production
composition. Nutrition and Pump Check are live when native auth is ON (already
shipped in earlier sprints); they are **not** part of this Today rollout claim.

---

## 5. Feature flag inventory

### Backend `MOBILE_AUTH_ENABLED`

| Field | Repository default | Production runtime (proven 2026-08-26) |
|---|---|---|
| Value | OFF (`False`) | **ON** (`MOBILE_AUTH_ENABLED=1` in `/home/ubuntu/fitness-coach/.env`; `/health?deep=1` `flags.MOBILE_AUTH_ENABLED=true`) |
| Lifecycle metadata | `blocked` | **stale** — PR4 has been merged since 2026-08-04 |
| Parser | `app.services.mobile_credentials.validate_mobile_auth_config` | empty value is a **boot failure** |
| Restart | boot-time only | `docker compose up -d` after `.env` edit |
| Rollback | set `0` + `docker compose up -d` | same; issued opaque credentials stop being accepted |
| Other product-flag prerequisites | none | other rollout flags are all **false** in live deep health |

Stale “blocked until Hardening PR4” language still exists in:

- `app/feature_flags.py` (`lifecycle=LIFECYCLE_BLOCKED`, prerequisite text)
- `docs/FEATURE_FLAGS.md` row 9
- `docs/AUTH_CONTRACT.md` §6 (“current production default” OFF / blocked until PR4)
- `docs/ROLLOUT.md` order #9 (pointer added; still not a full lifecycle rewrite)

This assessment does **not** change lifecycle metadata in application code.
Operators must follow **this document**, not the stale `blocked` label.

Live deep-health flags on 2026-08-26:

```
WEEKLY_PROGRAM_UI_ENABLED=false
UIUX_TODAY_V2_ENABLED=false
UIUX_PLAN_V2_ENABLED=false
UIUX_COACH_PAGE_V2_ENABLED=false
UIUX_NAV_V2_ENABLED=false
FITX_WORKOUT_SESSIONS_ENABLED=false
AI_ADAPTIVE_PLAN_CONTEXT=false
AI_COACH_PLAN_MUTATION_TOOLS_ENABLED=false
MOBILE_AUTH_ENABLED=true
```

Auth contract (same deep-health body): `request_clock_skew_seconds=0`,
`request_token_use=access`, paths `web` and `mobile`.

### Mobile `AXISAI_NATIVE_AUTH_ENABLED`

| Field | Value |
|---|---|
| Default | `false` (`bool.fromEnvironment`, `lib/core/config/native_auth_rollout.dart`) |
| Mechanism | compile-time Dart define; **no** runtime server control |
| API origin | `AXISAI_API_BASE_URL` parsed **only** when flag is ON; HTTPS required in release |
| Current distributed build | **not proven**; default-off is the merged/CI default |
| Rollback | ship a new binary compiled OFF; wait for adoption |

Do not reverse the order: backend API first (already ON), then a native-auth ON
build. Do not enable native-auth in this assessment.

---

## 6. Historical blocker status

Hardening PR4 `34f8dc79be746f233974461bf0465e4bf32eb72d`
(`Hardening PR4 — concurrency, overload & recovery closure (#200)`, 2026-08-04)
**is an ancestor of current origin/main.**

Closed on `a6d6b2e`:

| Control | Proof |
|---|---|
| Shared blocking gate | `blocking_concurrency_slot()` in `app/services/ai_gate.py`; login/refresh/wearables share `_ai_slots` |
| Login | `mobile_auth.login` wraps Cognito `authenticate` in `with blocking_concurrency_slot()` |
| Refresh | `_renew_provider_tokens` takes the slot **after** releasing the DB snapshot; AST test forbids lock+network in one function |
| Overload | `BlockingConcurrencyLimit` → 503 `AUTH_TEMPORARILY_UNAVAILABLE` + `Retry-After: 15` |
| Thread reserve | `enforce_gate_invariants`; live deep health `thread_reserve=8`, `thread_reserve_floor=2`, `threads=8`, `workers=1` |
| JWKS | single-flight `_forced_refresh_for` + `JWKS_FORCED_REFRESH_COOLDOWN_SECONDS` default 60 |
| DB pool | live `size=8` aligned with threads; overflow configured |
| Tests | `tests/test_capacity_invariants.py`, `tests/test_ai_gate.py`, `tests/test_gunicorn_config.py` |

**The previous blocker remains closed in code.** The registry text is stale, not
the implementation.

---

## 7. Current runtime / deployment-state evidence

Distinguished on purpose. Do not treat repository defaults as production.

### Proven runtime (read-only SSM + HTTPS probes, 2026-08-26)

| Fact | Evidence |
|---|---|
| Host | EC2 `i-0c6f5352fc214e68d` (`AxisAI-server`, t3.medium, `eu-central-1`), public IPv4 `18.153.156.28` |
| Process | Docker Compose `web` = gunicorn `starter:app`, 1 worker × 8 threads, loopback `127.0.0.1:5000` behind host nginx |
| Deploy dir | `/home/ubuntu/fitness-coach` |
| Running SHA | `a6d6b2e60dc7718bd47590d64b5f74542294025c` |
| `MOBILE_AUTH_ENABLED` | **1** (host `.env` and deep-health flags) |
| `/api/v1` registered | **yes** — `GET /api/v1/today` → 401 JSON `AUTH_SESSION_EXPIRED`; `POST /api/v1/auth/login` with `{}` → 400 `AUTH_INVALID_REQUEST`; GET login → 405 Allow OPTIONS, POST |
| Shallow health | `{"db":"ok","limiter_storage":"redis","status":"ok"}` via `https://18.153.156.28/health` and `https://fitx-chatbot.duckdns.org/health` |
| Deep health | login ok, redis ok, worker **alive**, fatsecret_proxy ok, bedrock enabled, capacity as above |
| Public hostname for Flask | nginx `server_name fitx-chatbot.duckdns.org` |
| `www.axisaiapp.com` | CloudFront → S3 **landing site**, not Flask. `GET /health` there is 301 to AmazonS3 |
| `api.axisaiapp.com` | **does not resolve** |
| Web container | created/started `2026-08-26T10:40:33Z`, healthy |
| Worker container | compose STATUS `unhealthy` because the image HEALTHCHECK hits `:5000/health` and the RQ worker does not listen there; deep health `worker: alive`. **Not** a soak abort. |
| Redis | healthy, up 3 days |
| Cognito pool | `.env` `COGNITO_USER_POOL_ID=eu-central-1_kaX0SORRK` |
| `RUNTIME_METRICS_ENABLED` | **not present** in host `.env` → repo default `0` |
| `APP_BASE_URL` | **not present** in host `.env` |
| Restart mechanism | GitHub Actions `Deploy to EC2` via SSM: `git reset --hard origin/main`; `docker compose build`; `docker compose up -d`; deep-health gate; code rollback on failure. Flag change: edit `.env` then `docker compose up -d` (no merge). |

Logs already show production mobile-auth traffic from `85.107.65.28` (login 401
`NotAuthorizedException`, refresh 400, account/me 401, today 401). This
assessment did not add credentialed logins.

### Cannot be proven

- Exact time `MOBILE_AUTH_ENABLED` was flipped to 1 (web recreate at 10:40Z is
  consistent with a compose recreate; GitHub deploy for this SHA was 06:21Z).
- Whether `RUNTIME_METRICS_ENABLED` was ever 1 (CW capacity metric names exist;
  HTTP SLIs do not).
- CloudWatch **alarms** / dashboards (none confirmed).
- RDS snapshot / `PUBLIC_HEALTH_URL` GitHub var values (not readable as secrets).
- A TestFlight or Play Store binary of mobile `3386df3`.
- Controlled-identity authenticated Today body in production.

---

## 8. Backend registration behavior

```
MOBILE_AUTH_ENABLED
  → configure_app / validate_mobile_auth_config
  → register_blueprints
  → if flag ON: append mobile_api
  → mobile_today registers GET /today on that blueprint
```

Proof:

- Flag OFF → no `/api/v1` rules; `GET /api/v1/today` is 404
  (`tests/test_mobile_auth_feature_gate.py`).
- Flag ON → approved allow-list includes `/api/v1/today`
  (`test_enabled_startup_exposes_only_approved_mobile_routes`).
- No duplicate unguarded Today route
  (`tests/test_mobile_today_architecture.py`).
- No auth bypass: `@require_mobile_auth`; cookie session is 401 JSON.

Production: flag ON, unauthenticated Today is 401, not 404. That matches the
gated-ON path.

---

## 9. Mobile compile-time behavior

| Path | Behavior |
|---|---|
| Default / omitted define | native auth OFF |
| OFF startup | no API parse, no auth graph, no HTTP, no secure storage; Today = `UnavailableTodayRepository`; UI “Training data isn't available yet”, no retry |
| ON + missing/bad origin | `AxisAiConfigurationFailureApp` static screen; no crash-loop, no fixtures |
| ON + valid origin | loading → restore (refresh-first) → login or shell |
| ON Today | `LiveTodayRepository` → `GET /api/v1/today` |
| Logout | epoch invalidate, session tombstone, Today controller reset |
| Backend `/api/v1` disabled | login/restore protocol or connectivity failure → recovery/login; Today never fabricated |
| Host unreachable | connectivity/timeout → recovery or retryable Today error |

`AppComposition.development()` still uses fixtures; `main()` never selects it in
release (`kReleaseMode` throws). `pubspec.yaml` does not bundle `fixtures/` for
production composition.

---

## 10. Auth smoke matrix

| # | Case | Proven where | Result |
|---|---|---|---|
| 1 | Valid login | local tests; **production** 2026-08-26 ~12:52Z controlled E2E | 200 opaque `session` (`type=opaque`, Bearer, access+refresh+expiries); 462.8 ms client; no 5xx |
| 2 | Invalid credentials | local tests; production `POST /login` with `{}` → 400; `{"username":"x","password":"y"}` → 401; live Cognito 401 from owner IP | generic `AUTH_INVALID_CREDENTIALS` / `AUTH_INVALID_REQUEST` |
| 3 | Expired/invalid token | local tests; production `GET /today` without token and with `Bearer not-a-valid-token` | 401 `AUTH_SESSION_EXPIRED`, `Cache-Control: no-store` |
| 4 | Refresh success | local tests; **production** controlled E2E | 200; credentials rotated; subsequent Today 200; 224.4 ms client / 27.3 ms server; no loop |
| 5 | Refresh failure | local tests; production `POST /refresh` `{}` → 400; existing traffic 400 | 400 `AUTH_INVALID_REQUEST`; no `refresh_reuse` in soak logs |
| 6 | Logout/revocation | local tests | family revoke; mobile logout best-effort. Not re-run in §29 (non-destructive smoke). |

Controlled production identity **was** used in §29. Do not use another real
user’s data. No production plan mutation.

---

## 11. Today smoke matrix

| # | Case | Proven where | Result |
|---|---|---|---|
| 7 | Authenticated GET `/api/v1/today` | local tests; **production** controlled E2E | 200 canonical projection; `status=no_plan`; 210–263 ms client |
| 8 | Unauthenticated | **production** + tests | 401 `AUTH_SESSION_EXPIRED` |
| 9 | Malformed token | tests; **production** invalid Bearer | 401 `AUTH_SESSION_EXPIRED` |
| 10 | Owner-only | tests; production `/account/me` username matches controlled identity | pass (one-user). Two-user production not run |
| 11 | No query/header spoofing | tests; **production** `?user_id=999999`, `X-User-Id: 999999`, `?date=2020-01-01` | inert; same fingerprint; did not adopt client date |
| 12 | No-plan | tests; **production** controlled E2E (actual canonical state) | 200 `status: no_plan`, `plan.exists=false`, `is_rest_day=false` |
| 13 | Rest-day | tests | 200 `status: rest_day`. Not forced in production |
| 14 | Active workout | tests | 200 `scheduled_not_started` / action start. Not forced in production |
| 15 | Completed | tests | 200 `completed` (PumpCheck). Not forced in production |

Production authenticated Today body **was** fetched for the controlled E2E
user: canonical `no_plan` on 2026-08-26 (Istanbul). Other product states remain
covered by automated characterization. Plans were not mutated.

---

## 12. Cross-user isolation

**Hard gate. Status: PASS on tests + production one-user spoof-resistance
smoke. Authenticated two-user production probe: not run (no second controlled
identity; not manufactured).**

Backend:

- Identity from `g.mobile_user` after Bearer verification.
- `build_today(user_id)` takes only that id.
- Route reads no caller-supplied user selector.

Mobile:

- Logout clears Today (`today_controller_test`, `logout_invalidation_test`).
- Auth epoch change drops in-flight payloads.
- Today state is in-memory; not a public cache.

Unresolved cross-user ambiguity: **none in code**. Residual: production
two-user confirmation remains unrun and is **not** required to close Stage 2
given one-user spoof-resistance plus existing two-user automated tests.

---

## 13. Concurrency readiness

| Item | Proven | Inferred |
|---|---|---|
| Max blocking Cognito concurrency | gated by shared `_ai_slots` (AI_MAX_CONCURRENCY default 4) plus thread reserve 2 of 8 | live `ai_active=0`, `thread_reserve=8` at probe time |
| Login/refresh saturation | tests map `BlockingConcurrencyLimit` → 503 + Retry-After 15 | not load-tested in production |
| Today under auth pressure | Today is a read after auth; gate is on login/refresh Cognito I/O | unrelated `/health` remained 200 during this probe |
| Unsafe production load test | **not run** | — |

Existing regression: `tests/test_capacity_invariants.py`, CI job
`mobile-pg-concurrency` green on this SHA.

---

## 14. Failure-mode matrix

| Condition | Backend | Mobile (native ON) | User-visible | Retry / rollback |
|---|---|---|---|---|
| Backend flag OFF | `/api/v1` unregistered, 404 | login protocol/connectivity; no Today | recovery or “sign in not configured”; **not** fixtures | Enable backend first (already ON) |
| Native auth OFF | unchanged | `UnavailableTodayRepository` | “Training data isn't available yet”, no retry | Compile a new ON build **after** backend soak |
| Native ON + backend OFF | 404/non-contract | no crash-loop, no hang, no fixtures | recovery/login | Keep backend ON before distributing ON builds |
| API host unreachable | N/A | connectivity | connection copy / retryable Today | retry; check DNS/TLS |
| DNS failure | N/A | connectivity | same | do not invent Today |
| Timeout | Cognito/login may 503 | refresh 3s / other 10s | recovery or retryable Today | retry |
| 401 | `AUTH_SESSION_EXPIRED` | session reject / “Sign in again” | login | re-auth |
| Refresh expired | 401/reuse revoke | definitive session loss | “Sign in again” | re-auth |
| 403 | contract 403 | `incompatibleResponse` → retryable Today | retryable error, not rest/no-plan | retry; do not infer |
| 404 `/api/v1/today` | no route or missing | `notFound` → retryable error **not** no-plan | “could not be loaded” | confirm flag/route |
| 429 / overload | limiter + gate 503 | rateLimited / temporarily unavailable | retry | backoff; rollback flag if sustained |
| 500 / Today 503 | fail-closed 503 | temporarily unavailable | retry | inspect logs; do not empty-day |
| Malformed Today JSON | N/A | incompatibleResponse | retryable error | backend fix |
| Database unavailable | shallow `/health` 503; deploy rollback | connectivity/5xx | retry | do not serve empty Today |
| Cognito unavailable | login 503/401 generic | recovery | retry | gate protects threads |
| Today canonical service failure | 503 `TODAY_TEMPORARILY_UNAVAILABLE` | temporarily unavailable | retry | never rest/no-plan |

**No listed condition may result in fixture fallback.** Tests on mobile
`3386df3` prove production composition cannot construct `Fixture*`.

---

## 15. Observability inventory

| Signal | Code | Live now |
|---|---|---|
| Login attempts/failures | Flask logfmt `path=/api/v1/auth/login status=…`; Cognito `[COGNITO-IDP]`; **no** dedicated CW counter | docker logs **yes** |
| Refresh attempts/failures | same for `/api/v1/auth/refresh` | docker logs **yes** |
| Auth concurrency saturation | `GateRejections`; 503 `AUTH_TEMPORARILY_UNAVAILABLE` | CW **not listed**; logs would show 503 |
| JWKS refresh | `[COGNITO-JWT] JWKS unavailable` log; no dedicated CW counter | not observed |
| Request latency | `HttpLatency` if `RUNTIME_METRICS_ENABLED=1` | **not** in current `FitX/Runtime` metric list |
| `/api/v1/today` 2xx/4xx/5xx | HTTP SLIs `Blueprint=mobile_api` **not path-granular**; `mobile_today event=today_read_failed` | 401s in docker logs; no CW path SLI |
| DB pool | deep health + `DbPool*` gauges if metrics on | deep health **yes**; CW **not listed** |
| Worker/thread | deep health `capacity`; `ThreadReserve` gauge | deep health **yes**; CW name **present** (may be stale) |
| Health | `/health`, `/health?deep=1`, Docker HEALTHCHECK, deploy gate | **yes** |
| App error logs | docker json-file 10m×3; gunicorn stdout | **yes** |

Mobile app: production logging of login/Today is essentially none (architecture
forbids `print`/`debugPrint` in auth sources). Operators cannot distinguish 401
vs DNS from a device without a debugger.

**Sufficiency:** enough for a **low-volume backend soak using docker logs +
deep health + ThreadReserve**. **Not** sufficient for native-auth cohort
expansion against the documented HTTP dashboard.

---

## 16. Rollout metrics (operational checklist)

Use these. Do not invent percentages without a baseline.

### Auth

- Login success/4xx/5xx: **docker logs** `path=/api/v1/auth/login status=` until HTTP SLIs exist
- Refresh success/failure: same for `/api/v1/auth/refresh`
- Auth latency: log `dur_ms`; p50/p95/p99 **not available** in CW
- Concurrency-gate saturation: 503 `AUTH_TEMPORARILY_UNAVAILABLE` in logs; `GateRejections` if metrics on

### Today

- Volume / success / 401 / 404 / 5xx: logs `path=/api/v1/today status=`; 5xx also `mobile_today event=today_read_failed`
- Latency p50/p95/p99: **not in CW**

### Platform

- Thread reserve: deep health `capacity.thread_reserve` (live 8 / floor 2)
- DB pool: deep health `db_pool`
- CPU/memory: **not pulled** in this assessment (EC2 CW standard metrics may exist; not verified)
- Deploy health: GitHub Deploy workflow + container HEALTHCHECK
- Restart/crash: `docker inspect` startedAt; gunicorn logs

Owner/operator must calibrate numeric abort thresholds. None are invented here.

---

## 17. Abort criteria

**Immediate abort (security/privacy):**

- Any Today or account response for the wrong user
- Refresh-token reuse detection
- Fixture or client-inferred Today on a native-auth ON build
- Auth 5xx spike that is not a documented 503 shed

**Capacity abort (observe, then disable `MOBILE_AUTH_ENABLED`):**

- `thread_reserve` at floor under load
- Worker/thread exhaustion (gunicorn timeouts, `/health` 503)
- DB pool `checked_out` stuck at size with overflow
- Sustained login/refresh 503 `AUTH_TEMPORARILY_UNAVAILABLE` starving web routes

**Latency/error thresholds:** no repository baseline exists for mobile API in
production. Do **not** invent percentages. Until a week of log-derived rates
exists, treat **any** mobile 5xx and **any** `/health` 503 as an operator
review, and abort on repetition.

**Native-auth abort:** crash/startup regression, widespread login failure,
Today fixture regression → halt distribution and ship native-auth OFF build.

---

## 18. Backend staged rollout

**Do not execute a second enablement.** Stage 1 is already done.

### Stage 0 — Preflight (status)

| Check | Status |
|---|---|
| Main SHA verified | **yes** `a6d6b2e` origin + host |
| CI green | **yes** |
| Deployment healthy | **yes** shallow health, web healthy |
| Mobile API tests | focused local run + CI on SHA |
| Concurrency tests | CI `mobile-pg-concurrency` green on SHA |
| Rollback path known | `.env` `MOBILE_AUTH_ENABLED=0` + `docker compose up -d` |
| Metrics/log access | logs **yes**; HTTP CW **no** |

### Stage 1 — Backend API enablement

**Already true:** `MOBILE_AUTH_ENABLED=1`. `/api/v1` is on the internet.
Existing production **mobile** users remain unaffected **only if** they still
run native-auth **OFF** binaries (the compile-time default).

### Stage 2 — Backend smoke

Unauthenticated probes (earlier assessment) plus controlled-identity smoke
(§29, 2026-08-26 ~12:52Z):

- health 200 before and after smoke
- unauthenticated Today 401
- login empty body 400; malformed login 400; invalid credentials 401
- controlled login 200; refresh 200 (rotated); authenticated Today 200 `no_plan`
- spoof `user_id` / `X-User-Id` / `date` inert
- `thread_reserve=8` after smoke (floor 2)
- `RUNTIME_METRICS_ENABLED` left **UNSET**; log-only abort **accepted** for soak

**Stage 2 remaining items: closed.** Stage 3 soak is not.

### Stage 3 — Soak

Recommend **24 hours** from web recreate `2026-08-26T10:40:33Z`
(through **2026-08-27T10:40Z**), not a multi-week invention.

Rationale: single-host, 8 threads, low current mobile volume (one operator IP
plus scanners). 24h covers a full diurnal web-traffic cycle on the same
gunicorn process without extending a public pre-auth surface unnecessarily.

Watch: docker logs for 5xx/503, deep-health `thread_reserve`, `/health`,
refresh_reuse security events, nginx 4xx/5xx.

### Stage 4 — Internal/canary native-auth build

Only after Stage 2 remaining items + Stage 3 soak.

### Stage 5 — Broader mobile

Only after internal cohort is green.

---

## 19. Backend rollback

```
# on EC2 /home/ubuntu/fitness-coach
# 1. set MOBILE_AUTH_ENABLED=0 in .env  (exactly 0, not empty)
# 2. docker compose up -d
# 3. confirm GET /api/v1/today is 404 (not 401)
# 4. confirm /health 200
```

- Restart **is required** (boot-time flag).
- Time-to-effect: compose recreate of `web` (minutes, not instantaneous).
- Issued mobile credentials stop working; clients must re-auth when turned back on.
- Native-auth ON binaries (if any exist) degrade to recovery/login, **not** fixtures.
- Code rollback via GitHub Deploy is a separate path (`PREV_COMMIT`); it does
  **not** revert `.env` and does **not** revert DB migrations.

Do not assume rollback is instantaneous.

---

## 20. Native mobile staged rollout

**Do not distribute a native-auth ON build in this assessment.**

| Stage | Cohort | Expected | Metrics | Smoke | Abort | Rollback |
|---|---|---|---|---|---|---|
| 0 | none (current default OFF) | Today unavailable copy | n/a | current OFF APK | n/a | n/a |
| 1 | internal / owner devices | login → Today from `/api/v1/today` | logs + ThreadReserve | login, refresh, Today, logout, user switch | any fixture, wrong user, crash | uninstall / install OFF APK |
| 2 | small canary | **only if** Play/TestFlight staged rollout exists | same | same | same | halt track; OFF build |
| 3 | broader beta | closed TestFlight intent exists in prep docs; **not configured** | same | same | same | new OFF build |
| 4 | production | HTTPS origin must be the real Flask host | HTTP SLIs should exist first | full matrix | any P0/P1 | OFF build + optional backend flag OFF |

There is **no** remote runtime control for the compile-time flag.

Store/canary: `docs/mobile-prep` records TestFlight **not completed**. This
assessment found **no** Fastlane, Play, or TestFlight automation on
`3386df3`. Do not claim an App Store rollback that does not exist.

Recommended `AXISAI_API_BASE_URL` for an internal ON build, if and when the
owner proceeds:

```
https://fitx-chatbot.duckdns.org
```

Do **not** point native auth at `https://www.axisaiapp.com` (landing site) or
`https://api.axisaiapp.com` (does not resolve).

---

## 21. Mobile rollback constraints

Because the flag is compile-time:

1. Halt rollout of the native-auth ON build (stop internal distribution).
2. Revert users to a previous native-auth-OFF build **only where sideload /
   internal track allows**. App Store / TestFlight rollback is **not** proven.
3. Ship a replacement APK/IPA compiled with `AXISAI_NATIVE_AUTH_ENABLED=false`.
4. Emergency containment: `MOBILE_AUTH_ENABLED=0` on the backend (Stage 19).
   ON binaries then cannot complete login/Today; they must not show fixtures
   (already proven).

ADR 0004: disabled process does not purge Keychain. Tombstone/logout before
rolling back a binary, or pair with server family revocation.

---

## 22. Backend-off containment

Native-auth ON + backend API disabled/unavailable:

- Today degrades truthfully (`RepositoryFailure` / recovery / unavailable)
- **No fixtures**
- **No client inference** of rest/completion/workout/date

This is the safety property that makes backend rollback a viable containment
for a compile-time client flag. Proven by composition tests on `3386df3`, not
by a production flag-flip (this assessment does not disable the live flag).

---

## 23. Security review

| Topic | Rollout blocker? |
|---|---|
| JWT on the wire | Mobile API uses **opaque** credentials; Cognito JWT is provider-side, RS256, iss/aud/`token_use` checked | no |
| Token expiration | leeway 0 both paths; retired skew env **rejected at boot** even if mobile flag OFF | no |
| Refresh | rotate, family version, grace replay; reuse revokes | no |
| JWKS | cache + single-flight + cooldown; unavailable → 503, session kept | no (P2: no CW counter) |
| Ownership | `sub` must match family and user | no |
| User spoofing | no caller user selector on Today | no |
| Logs | `user=-` on mobile requests; security events use opaque family id | no |
| Secrets | keyring in host `.env` mode 600 (deploy enforces); documented M4 plaintext-on-disk | P2, known |
| CORS | none; native clients do not need it | no |
| Rate limit | login 10/min 50/hour IP + 15/15min username; refresh 30/min 300/hour; nginx 30r/s | no |
| Brute-force | `LOGIN_FAIL_CLOSED` default ON | no |
| Error leakage | generic messages; unconfirmed → 401 invalid credentials | no |
| Pre-auth surface | **already public** because flag is ON | operational: soak + rate limits, do not widen |

No auth redesign. No P0 auth bypass found.

---

## 24. Privacy review

| Risk | Status |
|---|---|
| User A Today visible to user B | tests pass; production two-user not run |
| Full fitness plans logged | Today payload has no exercise list; request log is path/status |
| Auth tokens logged | architecture forbids; Bearer only inside transport send |
| Injury data logged | Today does not log injury; Plan is unavailable on mobile |
| Cached Today across logout | controller reset + epoch fence |
| Public cache of private API | `Cache-Control: no-store` on 401 Today |

Unresolved privacy issue that would be P0/P1: **none found in code**.

---

## 25. Exact go/no-go checklist

### Backend enablement (Stage 1)

Already true on 2026-08-26 except where noted.

- [x] Exact deployed backend SHA known (`a6d6b2e`)
- [x] CI green
- [x] Auth hardening present
- [x] Concurrency guard present
- [x] Rollback path verified (documented; **not** executed)
- [x] Smoke test identities available **to this assessment** — dedicated production E2E account (see §29)
- [x] `/api/v1/today` contract current on SHA
- [ ] Monitoring sufficient for documented HTTP abort — **NO** (logs only; accepted for soak, not closed)
- [x] Product P0 = 0
- [x] Product P1 = 0

**Do not flip the flag again.** It is already ON.

### Native mobile rollout

- [x] Backend API already enabled
- [x] Backend smoke green **including authenticated Today** — §29
- [ ] Soak period green — **in progress** (started ~10:40Z 2026-08-26; through 10:40Z 2026-08-27). Evidence so far is clean; window not elapsed
- [x] Login/refresh green on a controlled identity — §29
- [x] Today green on a controlled identity — §29 `no_plan`
- [ ] Exact native-auth ON build validated against **production origin** — **NO** (CI used `api.example.invalid`; no ON build distributed)
- [x] Android CI green (OFF + ON compile) on `3386df3`
- [x] iOS CI green (ON simulator) on `3386df3`
- [ ] Rollback OFF build/path identified as a **store** artifact — **NO**; sideload OFF APK procedure exists (`flutter build apk --debug --dart-define=AXISAI_NATIVE_AUTH_ENABLED=false`); no retained OFF artifact proven in this gate
- [x] Product P0 = 0
- [x] Product P1 = 0

**Native mobile: WAIT — SOAK IN PROGRESS. Do not compile/distribute an internal native-auth ON build until the soak window closes clean.**

---

## 26. Known operational gaps

1. Operator docs still say `MOBILE_AUTH_ENABLED` is **blocked** while production is ON.
2. `RUNTIME_METRICS_ENABLED` unset; HTTP SLIs / `HttpOverload` / `AuthOutcomes` not in CW.
3. No path-granular `/api/v1/today` metric.
4. Login/refresh do not emit `AuthOutcomes`.
5. Flask public name is `fitx-chatbot.duckdns.org`; product domain `www.axisaiapp.com` is the landing site; `api.axisaiapp.com` does not exist.
6. Worker compose STATUS `unhealthy` is the image `/health` HEALTHCHECK on a process that does not serve HTTP; RQ heartbeat is alive. Do not abort on Compose STATUS.
7. Web container recreated 2h after the GitHub deploy — flag flip time not in git.
8. Controlled production E2E identity **was** used in §29. No second controlled identity. No other real user.
9. No TestFlight/Play staged rollout.
10. Mobile production logs/metrics none.
11. macOS CI does not build iOS native-auth OFF.
12. ADR 0004 still mentions fixture shell for disabled auth; code is Unavailable\*.
13. Deep health is loopback/CIDR-only; operators need SSM/docker exec to read flags.
14. PR5 Adaptive Coaching remains deferred (correct).
15. `plan_mutation/document.py` warning-overlay residual remains deferred (correct).

---

## 27. PR5 decision

**Sprint 12 PR5 — Adaptive Coaching Today Integration — remains DEFERRED.**

No rollout requirement justifies pending-proposal, why-plan-changed, or
mutation-journal surface. Core Today rolls out independently.
`AI_ADAPTIVE_PLAN_CONTEXT` and `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` are
**false** in production.

Do not start PR5. Do not fix PR2B residual.

---

## 28. Final verdict

## WAIT — SOAK IN PROGRESS

Architecture/code on backend `a6d6b2e` and mobile `3386df3` remain sound.
Stage 2 authenticated production smoke is **green** (§29). Stage 3 soak is
**not complete** (window through **2026-08-27 10:40 UTC**). Evidence since
web recreate `2026-08-26T10:40:33Z` is clean: no mobile 5xx, no overload 503,
no `/health` 503, thread reserve 8, no restart loop.

This is **not** GO FOR INTERNAL NATIVE-AUTH BUILD.

**Remaining before any native-auth ON compile/distribution:**

1. Leave `MOBILE_AUTH_ENABLED=1` through **2026-08-27 10:40 UTC** unless an
   abort criterion fires. Continue log-only soak watch (§29 commands).
2. Do **not** set `RUNTIME_METRICS_ENABLED` unless the owner later authorizes
   that specific change. Log-only abort is accepted for this soak only. Do
   not claim HTTP p95 abort until SLIs exist. Operational P1 remains open
   for native expansion / GO.
3. Native-auth HTTPS origin remains `https://fitx-chatbot.duckdns.org`.
   Do not use the landing CloudFront domain or unresolved `api.axisaiapp.com`.
4. Treat this document as the operator authority until lifecycle docs are
   updated in a separate change.
5. Sideload OFF rebuild procedure exists; store/TestFlight OFF rollback is
   still unproven. Keep an OFF APK if/when an ON internal build is compiled
   **after** soak.

**This does not mean flags were activated by this task.** Production already
had `MOBILE_AUTH_ENABLED=1`. `AXISAI_NATIVE_AUTH_ENABLED` remains default OFF
and was not compiled for distribution here. `RUNTIME_METRICS_ENABLED` was
**not** changed.

---

## Independent review (rollout dimensions)

| # | Dimension | Finding | Severity |
|---|---|---|---|
| 1 | Auth bypass | None. Today and account require mobile auth; production unauthenticated Today is 401 | — |
| 2 | Cross-user leakage | Tests pass; no caller user selector | — |
| 3 | Blocking concurrency | PR4 controls present; live thread_reserve=8 | — |
| 4 | Refresh loops | Refresh 3s; coordinator serializes; reuse revokes | — |
| 5 | JWKS failure | 503, session kept; cooldown | P2 (no CW counter) |
| 6 | DB/thread exhaustion | Pool aligned; reserve 2; not load-tested in prod | P2 residual |
| 7 | Backend flag ordering | Backend ON before native-auth ON — correct; **already ON** | — |
| 8 | Mobile flag ordering | Default OFF; do not ship ON until conditions | — |
| 9 | Backend-off containment | Proven in mobile tests | — |
| 10 | Fixture regression | Production composition tests pass (125 focused tests) | — |
| 11 | Today contract drift | PR3 on running SHA; 0 LLM; 503 fail-closed | — |
| 12 | Mobile startup failure | Config screen / recovery / login; no blank crash | — |
| 13 | Observability insufficiency | HTTP SLIs missing while API is public | **P1 operational** |
| 14 | Rollback feasibility | Backend `.env`+compose yes; mobile compile-time yes with sideload; store rollback unproven | P2 for store |
| 15 | Wrong go/no-go | Stale `blocked` label would have wrongly blocked or confused operators | P2 docs (this runbook supersedes) |
| 16 | Secrets/logging | `.env` on disk; no token logs found | P2 known M4 |
| 17 | Deployment-state uncertainty | Runtime **was** verified via SSM; remaining gaps listed in §7 | — |

**P0 = 0. Product-code P1 = 0. Operational P1 = observability for HTTP abort.**

---

## Validation evidence (this assessment)

Backend focused pytest (worktree at `a6d6b2e`): **429 passed**, 0 failed, 642
datetime deprecation warnings, 647s — feature-gate, Today architecture/API,
auth contract, mobile auth API/service/credentials, Cognito JWT, capacity
invariants, AI gate, feature-flag registry, gunicorn config.
GitHub CI pytest + schema-drift + PG concurrency: **success** on this SHA.

Mobile focused `flutter test` on worktree at `3386df3`: **125 passed**, exit 0,
covering fixture boundaries, auth security, data boundaries, composition,
production truth, Today, native-auth rollout default, router Today lifecycle,
logout invalidation, startup.

Shipped CI: Flutter CI **success** on `3386df3` (Android OFF, Android ON,
iOS ON simulator). Not redistributed.

No application behavior changes were made. No production mutation.

---

## Exact next action required from the owner to continue (not Stage 1 enablement)

Stage 1 is already live. Stage 2 authenticated smoke is green. Next owner actions:

1. **Do not distribute** a native-auth ON build.
2. **Do not compile** an internal native-auth ON build until the soak window
   closes clean at **2026-08-27 10:40 UTC**.
3. Continue log-only soak monitoring with the §29 commands until that instant
   (or abort per §17 / §29 abort status).
4. Do **not** change `MOBILE_AUTH_ENABLED` or `RUNTIME_METRICS_ENABLED` unless
   a later instruction authorizes that specific change.
5. After a clean soak close, the next decision is whether to accept remaining
   operational P1 (HTTP SLIs missing) for **internal-only** compile, or to
   enable metrics first. That decision is **not** made here.
6. Keep a native-auth OFF sideload procedure:
   `flutter build apk --debug --dart-define=AXISAI_NATIVE_AUTH_ENABLED=false`.

HARD STOP after this document. No flag changes, no deploy, no native-auth ON
distribution, no PR5, no Sprint 13.

---

## 29. Controlled Production Smoke + Soak Gate

Gate date/time: **2026-08-26 12:51–12:55 UTC**.
Assessor: continuation of this runbook (not a rediscovery).
Production origin: `https://fitx-chatbot.duckdns.org`.
No flags changed. No deploy. No Docker restart. No Cognito mutation. No
native-auth ON compile/distribution. PR5 remains deferred.

### Identity

| Field | Value |
|---|---|
| Classification | dedicated production E2E / release-validation account |
| Username kind | test-only (`axisai.native.e2e`) |
| Purpose | AxisAI release validation only |
| Other real user | **not used** |
| Second controlled identity | **none** — one-user spoof-resistance + existing two-user tests |
| Password / token | **not recorded** |

### SHAs and flags (reconfirmed, not rediscovered)

| Field | Value |
|---|---|
| Backend SHA (host `sudo -u ubuntu git -C /home/ubuntu/fitness-coach rev-parse HEAD`) | `a6d6b2e60dc7718bd47590d64b5f74542294025c` |
| Mobile SHA (authority, unchanged) | `3386df37198ef0193c64fa4754a686357868f785` |
| `MOBILE_AUTH_ENABLED` | **TRUE** (`1` in host `.env`; deep-health `true`) — **not** set again |
| `RUNTIME_METRICS_ENABLED` | **UNSET** — **not** changed |
| `blocking_concurrency_slot()` on login/refresh | **present** (2 call sites in deployed `app/services/mobile_auth.py`) |

### Login

| Field | Result |
|---|---|
| `POST /api/v1/auth/login` | **200** |
| Contract | opaque `session`: `type=opaque`, `token_type=Bearer`, access+refresh credentials, both expiries |
| 5xx / overload 503 | **none** |
| Client latency | **462.8 ms** |
| Cache-Control | `no-store` |

### Refresh

| Field | Result |
|---|---|
| `POST /api/v1/auth/refresh` | **200** |
| Rotated credentials | **yes** (new access and refresh, distinct from login) |
| Subsequent `GET /api/v1/today` | **200**, fingerprint unchanged |
| Refresh loop | **none** (single refresh) |
| Token-family anomaly / `refresh_reuse` | **none** observed |
| Client latency | **224.4 ms** (server `dur_ms=27.3`) |

### Authenticated Today

| Field | Result |
|---|---|
| `GET /api/v1/today` | **200** |
| Canonical status | **`no_plan`** |
| Date | `2026-08-26` (server Istanbul day) |
| `canonical_local_date` | `2026-08-26` |
| `plan.exists` | `false` |
| `is_rest_day` | `false` (distinct from rest) |
| `completed` | `false` |
| `action` | `none` |
| Workout summary | absent (`null`/none) — consistent with no plan |
| Principal | `/api/v1/account/me` username matched the controlled E2E account; `profile_complete=false` |
| Fixture / fabricated markers | **none** (`DRAFT_FIXTURE`, “Upper Body Strength”, `workout_example_001`, etc.) |
| Client latency | **263.4 ms** first read; 210–216 ms spoof/post-refresh |

No plan was mutated to force rest/completed/active states.

### Spoof-resistance

| Input | Status | Fingerprint vs baseline | Client date adopted? |
|---|---|---|---|
| `?user_id=999999` | 200 | **identical** | n/a |
| `X-User-Id: 999999` | 200 | **identical** | n/a |
| `?date=2020-01-01` | 200 | **identical** | **no** (stayed `2026-08-26`) |

Baseline and spoof bodies shared the same 12-char SHA for the first second
(`fbae36f3a0e9`). After refresh, `server_time` advanced (body hash changed)
but the canonical fingerprint (date/status/plan/rest/completed) was unchanged.
No second-user data was requested.

### Negative auth (non-destructive)

| Case | Result |
|---|---|
| Invalid Bearer on Today | 401 `AUTH_SESSION_EXPIRED`, `retryable=false`, `no-store` |
| Unauthenticated Today | 401 `AUTH_SESSION_EXPIRED` |
| Malformed login (username only) | 400 `AUTH_INVALID_REQUEST` |
| Empty login `{}` | 400 `AUTH_INVALID_REQUEST` (public probe) |
| Invalid credentials `x`/`y` | 401 `AUTH_INVALID_CREDENTIALS` (public probe) |
| Empty refresh `{}` | 400 `AUTH_INVALID_REQUEST` (public probe) |

### Health / deep health / thread reserve

Public `/health` (before smoke 971 ms / after 212 ms): **200**
`{"db":"ok","limiter_storage":"redis","status":"ok"}`.

Public `/health?deep=1` remains shallow (CIDR/loopback-only), as previously
documented.

Loopback deep health via `docker exec` **after smoke** (12:54:21Z):

| Field | Value |
|---|---|
| status | ok |
| db / redis / login | ok |
| worker | **alive** |
| fatsecret_proxy | ok |
| bedrock | enabled |
| `MOBILE_AUTH_ENABLED` | true |
| other rollout flags | all false |
| thread_reserve | **8** |
| thread_reserve_floor | 2 |
| threads / workers | 8 / 1 |
| ai_active | 0 |
| db_pool | size 8, checked_out 1 |
| auth_contract | skew 0s, token_use access, paths web+mobile |

Web: running **healthy**, started `2026-08-26T10:40:33Z`, restarting=false,
restarts=0. Worker: Compose **unhealthy** (known image HEALTHCHECK vs RQ;
not an abort). Redis `fitx-redis`: healthy, started 2026-08-22, restarts=0.

### Observability mode

**24h soak proceeds with log-only abort monitoring.**

`RUNTIME_METRICS_ENABLED` was **not** changed (host `.env` UNSET; no owner
authorization for configuration mutation). Missing CloudWatch HTTP SLIs
(`HttpOverload`, `AuthOutcomes`, login/Today 2xx/4xx/5xx, Today percentiles)
are **not** pretended to exist.

Docker json-file logs the gunicorn/app request lines on **stderr**. Merge
streams (`2>&1`) or counts will read as empty.

Exact soak-watch commands (EC2 / SSM, cwd `/home/ubuntu/fitness-coach`):

```bash
# /health (public)
curl -sS --max-time 15 https://fitx-chatbot.duckdns.org/health

# deep-health + thread reserve (loopback only)
docker exec fitness-coach-web-1 python3 -c "import json,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health?deep=1', timeout=10).read().decode())"

# login failures (gunicorn+app each emit one line; unique ~ half)
docker logs --since 24h --timestamps fitness-coach-web-1 2>&1 | grep 'path=/api/v1/auth/login' | grep -E 'status=401|status=400|status=5'

# refresh failures
docker logs --since 24h --timestamps fitness-coach-web-1 2>&1 | grep 'path=/api/v1/auth/refresh' | grep -E 'status=4|status=5'

# Today 5xx
docker logs --since 24h --timestamps fitness-coach-web-1 2>&1 | grep 'path=/api/v1/today' | grep 'status=5'

# Today latency
docker logs --since 24h --timestamps fitness-coach-web-1 2>&1 | grep 'path=/api/v1/today' | grep 'dur_ms='

# overload 503s
docker logs --since 24h --timestamps fitness-coach-web-1 2>&1 | grep -E 'status=503|AUTH_TEMPORARILY_UNAVAILABLE|blocking_capacity_exhausted|today_read_failed|refresh_reuse|Traceback'

# compose (do not abort on worker unhealthy HEALTHCHECK mismatch)
docker compose ps
```

### Soak evidence (window open)

| Field | Value |
|---|---|
| Window | `2026-08-26T10:40:33Z` → **2026-08-27T10:40Z** |
| Complete? | **NO** (~21h 45m remaining at 12:55Z) |
| Web logs since recreate (merged stdout+stderr, ~3h) | 139 lines; gunicorn started **once** |
| Login (raw dual-log counts) | 24 lines → ~12 unique: 200×3, 401×5, 400×4. **5xx=0** |
| Refresh | 10 lines → ~5 unique: 200×1, 400×4. **5xx=0** |
| Today | 30 lines → ~15 unique: 200×5, 401×10. **5xx=0** |
| Overload 503 / `AUTH_TEMPORARILY_UNAVAILABLE` | **0** |
| `today_read_failed` | **0** |
| `refresh_reuse` | **0** |
| Traceback | **0** |
| Login server `dur_ms` (Cognito path) | ~250–948 ms for 401/200; 1.6–3.4 ms for 400 |
| Today server `dur_ms` | ~1.0–1.4 ms for 401; 11.1–51.6 ms for authenticated 200 |

No percentages invented. Observed counts only. 401/400 are expected
unauthenticated/malformed probes plus owner-IP traffic, not abort signals.

### Abort status

**No abort criterion fired.**

- no wrong-user Today
- no cross-user leak attempt beyond inert spoof inputs
- no fixture/fabricated Today
- no client-date Today
- no refresh-family anomaly
- no auth/Today 5xx
- thread reserve 8 (not at floor 2)
- `/health` 200, not 503
- no crash/restart loop
- DB/Redis ok; worker heartbeat alive

### Rollback / containment (unchanged; not executed)

Backend: `MOBILE_AUTH_ENABLED=0` then `docker compose up -d`; prove
`GET /api/v1/today` is **404 not 401**; prove `/health` 200. Restart/recreate
required. Not instant.

Mobile: halt ON-build rollout; sideload
`flutter build apk --debug --dart-define=AXISAI_NATIVE_AUTH_ENABLED=false`;
emergency backend flag OFF. App Store/TestFlight downgrade **not** proven.
The local phase2 debug APK under `tmp/axisai-mobile-phase2` is a **native-auth
ON** validation build (production host present) and is **not** an OFF rollback
artifact.

### P0 / P1 / P2

| Severity | Count | Notes |
|---|---|---|
| P0 | **0** | no ownership, fixture, or health abort |
| P1 | **1 operational** | HTTP abort SLIs still missing. **Accepted for remaining log-only soak only.** Still blocks GO FOR INTERNAL NATIVE-AUTH BUILD (GO requires P1=0 and soak complete) |
| P2 | **5** (unchanged from independent review) | unreadable-plan doc wording; stale `blocked` label; log-only lossy under burst; worker HEALTHCHECK mismatch; rollback `.env` sloppiness / store rollback unproven |

### Final native-build GO/NO-GO

**WAIT — SOAK IN PROGRESS.**

Not GO FOR INTERNAL NATIVE-AUTH BUILD.
Not NO-GO (smoke did not fail; identity was available; no abort fired).

PR5 remains **deferred**.
