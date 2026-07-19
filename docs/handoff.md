# Phase 6 - Authentication, Onboarding & Security Handoff

Date: 2026-07-09
Scope: Landing, login, register, email verification, onboarding, frontend auth hardening.

## Completed Work

- Redesigned the public landing page with a mobile-first hero, clear Get Started/Login CTAs, and compact AI coaching value props.
- Redesigned login, registration, and email verification around one shared premium auth shell.
- Added password visibility toggles, loading/disabled button states, live-region error/success messaging, and browser autofill hints.
- Added registration password strength feedback and lightweight inline validation before submitting.
- Reworked onboarding into the shared Phase 6 surface and added optional target weight collection using the existing backend-supported field.
- Removed repeated inline style/script blocks from the legacy auth/onboarding templates and moved behavior into static assets.
- Added regression tests for the new auth UI/security contract.

## Modified Files

- `templates/landing.html`
- `templates/login.html`
- `templates/register.html`
- `templates/verify.html`
- `templates/setup.html`
- `static/auth.css`
- `static/auth.js`
- `tests/test_auth_phase6_ui.py`
- `docs/auth-review.md`
- `docs/handoff.md`

## Components Created

- Shared auth/landing/setup stylesheet: `static/auth.css`
- Shared auth/verification/onboarding behavior module: `static/auth.js`
- Phase 6 UI contract tests: `tests/test_auth_phase6_ui.py`

## Security Improvements

- Reduced CSP inline surface by removing page-local inline CSS/JS from auth/onboarding pages.
- Prevented duplicate auth submissions through button disabling during requests.
- Added safe live error rendering via `textContent`.
- Confirmed frontend JS does not store passwords in browser storage.
- Preserved existing backend auth, CSRF, rate-limit, session-fixation, and protected-route behavior.

## Remaining Technical Debt

- Forgot password and password reset are not implemented because the backend has no reset routes or Cognito helper calls for that flow yet.
- Setup option cards are focusable and labelled as radios, but full arrow-key radiogroup behavior is still a future accessibility improvement.
- Password strength feedback currently uses existing translation strings plus one English fallback label; move labels into `locales/*.json` in a later copy pass.

## Verification

- `python -m pytest tests/test_auth_phase6_ui.py -v` - 5 passed.
- `python -m pytest tests/test_auth.py tests/test_profile_routes.py tests/test_design_system.py -v` - 45 passed.

## Suggested Next Phase

Implement backend-compatible password recovery and add browser-based visual/accessibility regression coverage for the auth and onboarding flows.

## Sprint 1 - AWS Cognito Foundation

Date: 2026-07-09
Scope: Native Cognito registration, email verification, resend code, and local DB
compatibility while preserving legacy login.

Completed:

- Added `app/services/cognito_service.py` as the native Cognito boundary for
  SignUp, ConfirmSignUp, ResendConfirmationCode, auth, client creation, and
  friendly exception mapping.
- Updated `/register`, `/verify`, and `/verify/resend` to call the service
  boundary instead of the older route-level native helper import.
- Disabled Cognito Hosted UI/Authlib OAuth for this sprint; `/login/cognito`
  and `/auth/cognito/callback` return 404 and auth templates do not render
  Cognito redirect links.
- Changed Cognito-created local users to store `password_hash = NULL`; legacy
  users keep their existing hashes and old local authentication remains intact.
- Added Alembic migration `d6e7f8a9b0c1` to drop the PostgreSQL NOT NULL
  constraint on `user.password_hash`.
- Updated `.env.example` with the Sprint 1 Cognito User Pool and App Client IDs.
- Added `docs/cognito.md` with architecture, registration, and verification
  flow details.

Verification:

- `python -m pytest tests/test_cognito.py tests/test_cognito_idp.py tests/test_auth.py tests/test_auth_phase6_ui.py -v` - 63 passed.

## Sprint 2 - Cognito Login & Sessions

Date: 2026-07-10
Scope: Native Cognito password login, cryptographic JWT validation, server-side
encrypted session store, refresh-token lifecycle, GlobalSignOut logout, and a
`@require_auth` middleware across every protected endpoint.

Completed:

- `app/services/cognito_jwt.py` - JWKS-based JWT validator
  (`validate_token(token, expected_use)`): signature, `iss`, `aud`/`client_id`,
  `exp`, `token_use`; single JWKS refetch on unknown kid (key rotation).
- `app/services/cognito_service.py` - added `authenticate` (USER_PASSWORD_AUTH,
  returns `{"tokens","claims"}`), `refresh_tokens` (REFRESH_TOKEN_AUTH), and
  `global_sign_out`.
- `app/models.py` - `CognitoSession` model (opaque `session_id`, Fernet-encrypted
  access/refresh tokens, `access_token_exp`, unique `session_id` index,
  FK→user ON DELETE CASCADE).
- `app/services/session_store.py` - create/get/`current_access_token`/
  `get_valid_access_token` (refresh-on-expiry within
  `COGNITO_REFRESH_SKEW_SECONDS`)/touch/delete; `SessionInvalid` on dead refresh.
- `app/auth_middleware.py` - `require_auth`: anonymous→`/login`, legacy user
  (no `cognito_sub`) passthrough, Cognito user → validated access token or
  session invalidation; validated claims on `g.cognito_claims`.
- `app/blueprints/auth.py` - `/login` cognito branch now authenticates via
  Cognito, JWKS-validates the id token, enforces `sub` match, and opens a
  `CognitoSession`; `/logout` does best-effort GlobalSignOut + row delete.
- Swapped `@login_required` → `@require_auth` on every protected endpoint across
  14 blueprint files (`/logout` intentionally keeps `@login_required`).
- Alembic migration `aa11bb22cc33` creates `cognito_session` (chained onto head
  `d6e7f8a9b0c1`).
- `.env.example` - documented `COGNITO_TOKEN_ENC_KEY` and
  `COGNITO_REFRESH_SKEW_SECONDS`.
- `docs/cognito.md` - added the Sprint 2 login/JWT/session/refresh/logout section.
- Marked the legacy local-password path with `# TODO(Sprint 3)` in
  `app/models.py` and `app/blueprints/auth.py`.

Remaining technical debt:

- Legacy local-password auth (`password_hash`, `User.check_password`, the
  `/login` local branch) still present for users without a `cognito_sub`;
  remove once all users are Cognito-backed (`# TODO(Sprint 3)` markers).
- `app/services/cognito_idp.py` and `app/services/cognito.py` overlap with
  `cognito_service.py` and should be consolidated.
- Forgot-password / reset-password is still absent (Cognito
  ForgotPassword/ConfirmForgotPassword not yet wired).

Sprint 3 follow-ups:

- Remove the `# TODO(Sprint 3)` legacy code paths after confirming no active
  users depend on local-password login.

Coordination note (migrations):

- The `cognito_session` migration `aa11bb22cc33` chains onto the committed head
  `d6e7f8a9b0c1`. An in-flight barcode migration `e7f8a9b0c1d2` (currently
  untracked WIP) also chains off `d6e7f8a9b0c1`. If the barcode migration lands
  on the mainline first, rebase `aa11bb22cc33`'s `down_revision` onto it to keep
  a single linear Alembic chain (Alembic reports "Multiple heads" until then).

Verification:

- `python -m pytest tests/test_cognito_jwt.py tests/test_cognito_service_tokens.py tests/test_session_store.py tests/test_require_auth.py tests/test_cognito_auth.py tests/test_auth.py -v` - all green.
- Full suite: 1150 passed; the only non-green items are pre-existing and
  unrelated to Sprint 2 (a stale CSP-nonce template assertion, and db_init
  "Multiple heads" errors caused solely by the untracked barcode WIP migration).

### Addendum - Sprint 2 compliance re-audit (2026-07-11)

- Re-audited the full sprint spec against the merged implementation; every
  requirement (Cognito USER_PASSWORD_AUTH login, JWKS JWT validation,
  `@require_auth` middleware, encrypted sessions, refresh lifecycle,
  GlobalSignOut, error mapping, TODO(Sprint 3) markers, tests, docs) verified
  in place.
- Closed the one residual gap from the spec's security list: registration
  email is now normalized (trim + lowercase) and the duplicate-email check is
  case-insensitive (`app/blueprints/auth.py`); regression test added in
  `tests/test_auth.py`. Existing rows keep their stored casing - the collision
  check compares case-insensitively, and Cognito claim emails were already
  lowercased on read.
- Protected-route coverage re-verified: every authenticated endpoint across
  all 15 route files (incl. `wearables` and the split `nutrition/*` modules,
  superseding the "14 blueprint files" count above) uses `@require_auth`;
  only the intentional public routes (landing, invite, login, register,
  verify, set-language, health) remain open.
- At the time of this re-audit, the remaining technical debt and Sprint 3
  follow-ups above were unchanged.

## Sprint 3 - Authentication Finalization

Date: 2026-07-11
Scope: Native password recovery, Cognito-only credentials, verified identity
binding, session deadlines, executable authorization audits, and production
handoff.

This section supersedes the Sprint 1-2 authentication debt notes above.

### Completed work

- Added native Cognito `ForgotPassword` and `ConfirmForgotPassword` operations
  with fixed, user-safe provider error mapping.
- Added `/forgot-password` and `/reset-password` with enumeration-resistant
  responses, a 15-minute session-bound handoff, single-use success, bulk local
  session invalidation, and mandatory fresh login.
- Added matching Turkish/English recovery pages using the existing auth shell,
  accessibility conventions, CSP/CSRF integration, password controls, and
  shared JavaScript behavior.
- Removed runtime local-password registration/login, `User.set_password`,
  `User.check_password`, the timing dummy hash, and the duplicate
  `cognito.py`/`cognito_idp.py` services.
- Kept the nullable `User.password_hash` column unchanged for schema and
  migration compatibility; runtime authentication never reads or writes it.
- Login now authenticates Cognito first, cryptographically validates the ID
  token, and resolves the local profile only through verified `sub`.
- Added 24-hour idle and 7-day absolute local session deadlines, both
  configurable through environment variables.
- Bound every protected request across Flask-Login user id,
  `CognitoSession.user_id`, and the local user resolved from verified access
  token `sub`; removed legacy middleware passthrough.
- Added an executable route-map audit with an explicit public endpoint
  allowlist and static guards against reintroducing local or duplicate auth.
- Redacted usernames and raw unexpected provider exception text from auth logs.
- No database migration was required for Sprint 3.

### Changed areas

- Cognito services: `app/services/cognito_service.py`,
  `app/services/session_store.py`, removed duplicate service modules.
- Auth boundary: `app/blueprints/auth.py`, `app/auth_middleware.py`,
  `app/models.py`, `app/services/validators.py`, `app/config.py`, `.env.example`.
- Recovery UI: `templates/forgot_password.html`,
  `templates/reset_password.html`, `templates/login.html`, `static/auth.js`, and
  both locale catalogs.
- Coverage: auth/recovery/JWT/session/middleware/UI/i18n/CSRF/referral tests plus
  `tests/test_auth_audit.py`.
- Architecture and operations: `docs/cognito.md` and this handoff.

### Baseline timeout investigation

The original `python -m pytest -v` baseline was killed by a five-minute command
timeout with no streamed output. It was not hung and did not wait on AWS,
OpenAI, Redis, or another external service:

- collection completed reliably (1,228 tests at investigation time);
- an observable verbose run advanced continuously across test files;
- `tests/conftest.py` explicitly disables real Redis, Bedrock, S3, and Cognito
  access and uses in-memory SQLite;
- the function-scoped `app` fixture creates and drops the full schema for every
  app-backed test, creating cumulative cost across the large suite;
- representative timing showed a 22.26-second cold app setup, while the final
  suite's slowest items were subprocess/import checks (14.55s, 8.57s, and two
  ~6.9s MCP process gates).

Use an observable run or a timeout of at least 15 minutes for the full Windows
suite. Focused Sprint 3 feedback remains under two minutes.

### Verification evidence

- Focused auth suite:
  `python -m pytest tests/test_auth.py tests/test_auth_phase6_ui.py tests/test_password_recovery.py tests/test_cognito_service_tokens.py tests/test_cognito_jwt.py tests/test_cognito_auth.py tests/test_session_store.py tests/test_require_auth.py tests/test_auth_audit.py tests/test_hooks.py -q`
  - 134 passed in 80.17s.
- Log-redaction regression: 47 auth/service tests passed.
- Full suite before documentation commit: 1,258 passed, 5,376 warnings in
  469.57s (7m49s).
- Static audit: only the compatibility `User.password_hash` column remains;
  no runtime password helpers, duplicate Cognito implementations, committed AWS
  credentials, or unreviewed `login_required` business routes were found.

### Known limitations and remaining non-auth debt

- The suite emits many pre-existing `datetime.utcnow()` deprecation warnings on
  Python 3.14; migrate models/services to timezone-aware UTC incrementally.
- Native MFA and `NEW_PASSWORD_REQUIRED` challenge UI are not implemented;
  unsupported challenges fail closed. **Consequence:** enabling MFA on the pool
  from the console would break *every* login. `scripts/check_cognito_pool.py`
  now fails if `MfaConfiguration != OFF`.
- Logout remains a same-site-guarded GET until navigation links migrate to POST.
- Setup radio-card arrow-key behavior and browser-level visual/accessibility
  regression remain non-auth UI follow-ups.

### Production readiness

- Configure `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, optional client
  secret, and a dedicated `COGNITO_TOKEN_ENC_KEY`; production boot fails closed
  when the token key is missing.
- Review `COGNITO_SESSION_IDLE_HOURS=24` and
  `COGNITO_SESSION_ABSOLUTE_DAYS=7` against product policy before deploy.
- Confirm Cognito `ALLOW_USER_PASSWORD_AUTH`, e-mail verification, forgot
  password delivery, and password policy in the target User Pool.
- Run the focused auth suite, `tests/test_auth_audit.py`, and a full suite with a
  sufficiently long timeout in CI/deploy validation.
- Monitor Cognito throttling, reset failures, session invalidation reasons, and
  Redis login-throttle health without logging user identifiers or tokens.

## Sprint 6 PR1 - Adaptive Training Engine Foundation

Date: 2026-07-18
Scope: A canonical, deterministic, ORM-based training-history foundation and
limited convergence of the two highest-value duplicated runtime readers onto it.
This is the FIRST PR in the Sprint 6 chain — the next Sprint 6 PR must read this
section before implementing anything.

### What this PR changed

- Added `app/services/training_history/` — the single source of truth for reading
  workout history and computing progression baselines. Layered pure/impure:
  - `models.py` — frozen value objects `WorkoutEntry`, `WeeklyVolume`,
    `TrainingHistorySummary`.
  - `queries.py` — `fetch_workout_entries(user_id, start_day, end_day, *,
    include_markers=False)` (the one `WorkoutLog` read) + `is_completion_marker`.
  - `analysis.py` — pure deterministic calcs: `total_volume`, `total_sets`,
    `session_days`/`count_sessions`, `weekly_windows`, `bucket_by_week`,
    `volume_trend`, and a minimal Epley `estimated_1rm` building block.
  - `__init__.py` — public API + `build_training_history_summary(user_id,
    weeks=4, *, end_day=None)`.
- Converged two readers (behavior byte-identical, verified by regression):
  - `app/services/training_generation/time_series_model.py` —
    `build_performance_history` now sources each 7-day window's WorkoutLog rows via
    `fetch_workout_entries(..., include_markers=True)`; `sessions = len(entries)`
    (preserves prior COUNT(*)-incl-markers semantics) and `volume =
    total_volume(entries)` (excludes markers). PumpCheck / WeeklyCheckIn /
    adherence / stable-weeks / dropout logic untouched.
  - `app/services/ai_coach.py` — `_today_workout_totals` delegates to the
    foundation; same `{total_volume, entry_count}` shape and values (empty-day
    volume is now `0.0` float vs the prior int `0` — numerically equal).
- Docs: added `docs/TRAINING_HISTORY.md`; added the service-index line in
  `CLAUDE.md`.
- Tests: `tests/test_training_history.py` (13 tests — pure analysis, fixture-free,
  plus DB-backed reads via `make_user`).

### Code paths inspected

`app/models.py` (`WorkoutLog` 615-633, `WORKOUT_COMPLETION_MARKER` 633, related
`TrainingPlan`/`WeeklyLog`/`WeeklyCheckIn`/`DailyActivity`), `app/timeutil.py`,
`app/services/training_generation/*` (esp. `time_series_model.py`, `models.py`,
`scoring_engine.py`), `app/services/ai_coach.py`, `app/blueprints/training.py`,
`app/blueprints/tracking.py` (progress API), `app/services/analytics_engine.py`,
`app/services/context_builder.py`, `app/services/coach_context_queries.py`,
`fitx_mcp/server.py`, `app/cli.py` (`_user_child_models`), `tests/conftest.py`,
`tests/test_calculations.py`, `tests/test_analytics_engine.py`,
`tests/test_cascade_delete.py`.

### Architectural decisions

- **Additive foundation, limited convergence** (user-chosen scope): establish the
  single source of truth AND migrate only the top-value runtime readers now; keep
  the diff focused and low-risk.
- **No schema change / no migration.** `WorkoutLog` already carries the needed data
  and indexes (`user_id`, `created_at`). No new model → no `_user_child_models`
  change, cascade contract unaffected.
- **Pure/impure split** (mirrors `training_generation/`) so deterministic logic is
  fixture-free unit-testable and DB access is isolated.
- **Canonical definitions:** "session count" = distinct trained days (marker or
  real); "volume"/"sets" exclude markers; volume trend uses a ±5% dead-band.

### Assumptions discovered (next PR must respect)

- `WorkoutLog.created_at` is **naive UTC with no day-key column**; all day windows
  must go through `app.timeutil.utc_day_bounds`/`app_date_of`.
- `WORKOUT_COMPLETION_MARKER` rows are synthetic (`volume=0`) and are a genuine
  signal ("a session happened") — exclude from volume/exercise counts, but they DO
  count as trained days.
- The same history/window logic still lives inline in **three not-yet-converged
  readers** (intentional debt): `blueprints/tracking.py` (`/api/progress/workout`,
  heatmap, insights), `fitx_mcp/server.py` (`generate_weekly_report`, raw SQL /
  Postgres-only / standalone), and `analytics_engine.py` (`_check_missing_logs`).
  Also `ai_coach._tool_get_progress_metric` `volume_lifted` (range sum) and
  `context_builder`/`coach_context_queries.get_user_workout_history` (which reads
  `TrainingPlan` + quest completions, NOT raw `WorkoutLog`).
- `WorkoutLog` has no per-set granularity and no exercise catalog — sets/reps/load
  are flat scalar columns on one row per exercise.

### Known technical debt left intentionally

- The three inline readers above are not converged in this PR (limited scope).
- `estimated_1rm` exists but is not yet consumed by any intensity-trend feature.
- Pre-existing `datetime.utcnow()` deprecation warnings remain (Python 3.14).

### Exact next steps for the following PR

1. Read this section first.
2. Build on `build_training_history_summary` / `fetch_workout_entries` — do not add
   a new inline windowing/marker-exclusion implementation.
3. Highest-value next work: converge `blueprints/tracking.py` progress endpoints
   onto the foundation (ORM, SQLite+Postgres safe), then decide whether to add
   progression-analysis helpers (per-exercise best set, est-1RM trend, plateau
   signal) in `analysis.py` — still deterministic, still additive.
4. Leave `fitx_mcp/server.py` last (raw SQL, standalone process, Postgres-only) —
   converging it needs an ORM/session strategy for the MCP boundary.

### Verification evidence

- `python -m pytest tests/test_training_history.py -v` — 13 passed.
- `python -m pytest tests/test_training_generation.py tests/test_ai_coach.py tests/test_coach_tools.py tests/test_analytics_engine.py tests/test_progress_api.py tests/test_cascade_delete.py -q` — 128 passed (behavior preserved; incl. `test_workout_trend_marker_excluded_from_volume`).
- Module import chain (`training_history` → `time_series_model` / `ai_coach`) clean — no circular import.

### Independently safe to merge

Yes — purely additive service + hermetic tests + docs; the two converged readers
produce byte-identical results (regression-verified); no schema/migration, no route
or coach-prompt changes, no behavior change.

## Sprint 6 PR2 - Progressive Overload Engine (Progression-Analysis Layer)

Date: 2026-07-19
Scope: A canonical, deterministic progression-analysis layer built on top of the
Sprint 6 PR1 training-history foundation, turning raw workout history into normalized
progression signals (volume/strength trend, plateau, deload, load consistency, and a
single "next signal" for the coach). Purely additive — no runtime convergence, no
schema, no route/coach-prompt/UI change. **The next Sprint 6 PR must read this section
(and the PR1 section above) before implementing anything.**

### What this PR changed

- Added `app/services/training_progression/` — the single source of truth for
  interpreting training history into progression signals. Layered pure/impure and
  strictly one-way dependent on the foundation (`training_progression` →
  `training_history`; the foundation never imports this layer):
  - `models.py` — frozen value objects `WeeklyStrength` and `ProgressionReport`
    (the normalized output; every field has a safe neutral default).
  - `analysis.py` — pure deterministic signal functions: `series_trend` (reuses the
    foundation's ±5% `TREND_BAND`), `weekly_best_estimated_1rm` (per-week peak Epley
    estimate — finally *consumes* PR1's previously-unused `estimated_1rm`),
    `is_progressing`, `detect_plateau`, `detect_deload_due`, `assess_consistency`,
    `derive_next_signal`. All thresholds are explicit module constants.
  - `__init__.py` — public API + `build_progression_report(user_id, weeks=4, *,
    end_day=None)` orchestrator; reads history once via `fetch_workout_entries(...,
    include_markers=True)` then derives every signal purely.
- Docs: added `docs/TRAINING_PROGRESSION.md`; added the service-index line in `CLAUDE.md`.
- Tests: `tests/test_training_progression.py` (21 tests — fixture-free pure signal tests
  plus DB-backed roll-up via `make_user`).

### Code paths inspected

`docs/handoff.md` (PR1 section), `app/services/training_history/*`
(`__init__`/`models`/`queries`/`analysis`), `app/timeutil.py`, `app/models.py`
(`WorkoutLog` 615-633, `WORKOUT_COMPLETION_MARKER`),
`app/services/training_generation/time_series_model.py` (the PR1-converged reader and
its `PerformanceHistory` LLM-context shape — deliberately *not* duplicated),
`app/services/ai_coach.py` (`_today_workout_totals`, `_tool_get_progress_metric`
`volume_lifted`), `app/blueprints/tracking.py` (progress endpoints — a future
convergence target, left untouched), `tests/test_training_history.py`,
`tests/conftest.py` (`make_user`), `pytest.ini`.

### Progression decisions made (thresholds & definitions)

- **Trend band:** reused the foundation's `TREND_BAND = 0.05` (promoted from the former
  private `_TREND_BAND` in the PR2 follow-up); volume and strength are
  judged on one scale so the band lives in a single place (no drift, no duplicated
  magic number).
- **Strength trend:** per-week *peak* estimated 1RM (Epley), earliest→latest active-week
  direction via `series_trend`. Bodyweight / zero-load entries count as entries but
  contribute `0.0`.
- **Plateau** (`MIN_PLATEAU_WEEKS = 3`): last 3 active volume weeks form a flat run
  (whole run within the band); negated if estimated strength is still trending up
  (progress via intensity).
- **Deload** (`MIN_DELOAD_WEEKS = 4`): fires **only** on a sustained unbroken block
  (last 4 windows all active, no rest week) that has *also plateaued*. Because true
  deload readiness needs fatigue/recovery data the foundation does not carry, this is
  intentionally the most conservative volume-only inference — a healthy rising block is
  never flagged (neutral `False`).
- **Consistency** (`CONSISTENCY_MIN_ACTIVE_WEEKS = 3`, `MIN_DATA_WEEKS = 2`):
  `insufficient_data` (<2 trained windows) / `consistent` (≥3 of last 4) / `inconsistent`.
- **Next signal precedence** (exactly one wins): `insufficient_data` →
  `build_consistency` → `deload` → `plateau` → `progressing` → `keep_pushing`.
- **Neutral-value contract:** empty history / `weeks <= 0` / thin data all return
  explicit neutral values, never a speculative heuristic (per the PR spec).

### What the canonical progression service now provides

`build_progression_report(user_id, weeks=4, *, end_day=None) -> ProgressionReport`
answers, deterministically and user-scoped, the PR's deliverable questions: is the user
progressing? is volume/strength trending up/flat/down? plateauing? due for a deload?
consistent enough to support overload? what signal should the coach surface next? Plus
the per-week `weekly_volume` / `weekly_strength` series for transparency.

### Intentionally left for later PRs (deliberate debt)

- **No runtime convergence** in this PR (user-chosen scope: purely additive). The three
  inline history readers PR1 flagged (`blueprints/tracking.py`, `fitx_mcp/server.py`,
  `analytics_engine.py`) remain unconverged, and nothing consumes `ProgressionReport` in
  runtime yet.
- **No coach wiring:** `next_signal` is not surfaced in the coach prompt/context (would
  change AI behavior; the spec forbids user-facing signals this PR).
- **No adaptive program generator** (explicitly out of scope).
- **Deload has no fatigue input:** it is volume-only by design; a later PR can fold in
  `WeeklyCheckIn.fatigue` / recovery data (already used by
  `training_generation/time_series_model` + `recovery_model`) for a richer signal.
- Pre-existing `datetime.utcnow()` deprecation warnings remain (Python 3.14).

### Exact next steps for the following PR

1. Read this section first (and the PR1 section above).
2. Build on `build_progression_report` / `fetch_workout_entries` — do **not** add a new
   inline windowing / marker-exclusion / trend implementation.
3. Highest-value next work (unchanged from PR1's recommendation): converge
   `blueprints/tracking.py` progress endpoints onto the foundation (ORM, SQLite+Postgres
   safe), with characterization coverage proving byte-identical `/api/progress/workout`,
   heatmap, and insights output.
4. Then consider the first *consumer* of `ProgressionReport`: surface `next_signal` in
   the coach context block (additive, behind a flag, with prompt tests) and/or enrich
   `detect_deload_due` with `WeeklyCheckIn` fatigue.
5. Leave `fitx_mcp/server.py` last (raw SQL, standalone process, Postgres-only).

### Verification evidence

- `python -m pytest tests/test_training_progression.py -v` — 21 passed.
- Regression (behavior preserved): `python -m pytest tests/test_training_history.py
  tests/test_training_generation.py tests/test_ai_coach.py tests/test_progress_api.py -q`
  — 92 passed.
- Import direction confirmed one-way: `training_history` contains no reference to
  `training_progression` (no circular import).

### Independently safe to merge

Yes — a purely additive service package + hermetic tests + docs. No schema/migration, no
route, no coach-prompt, no UI, and no change to any existing runtime caller (all four
foundation consumers regression-green). The layer is dormant until a later PR consumes it.
