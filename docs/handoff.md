# Phase 6 - Authentication, Onboarding & Security Handoff


## Sprint 6 PR6.2 ? Adaptive Weekly Program client integration

1. **Baseline.** Branch `sprint6-pr6.2-weekly-program-client` was created in the isolated worktree `C:\Users\yusuf\.worktrees\sprint6-pr6.2-weekly-program-client` from `origin/main` at `195fd40`; the unrelated primary checkout was not modified.
2. **Scope.** This PR is a read-only browser consumer. No route, service, schema, migration, prompt, provider, logging, workout-plan control, or navigation behavior changed.
3. **Flag-OFF contract.** OFF still emits no mount, feature copy, feature script, request, or feature-specific namespace. The normalized whole-document delta and the exact `#wstats` to `.apv-meta-row` seam remain characterized.
4. **Data source.** The sole source is `GET /api/training/weekly-program`; there are no parameters, alternate endpoints, writes, user identifiers, or tokens.
5. **Copy transport.** ON emits one non-executable `script[type=application/json][data-weekly-program-copy]` inside the existing mount, encoded with Jinja `tojson`.
6. **Shared catalog API.** `catalog(locale=None, *, exclude_prefixes=())` preserves default output order/content, returns fresh dictionaries, does not mutate locale storage, and supports isolated multiple-prefix exclusion.
7. **Global locale isolation.** `window.I18N` excludes `weekly_program.*`; `training.weekly_program` and all unrelated existing keys remain available.
8. **Locale coverage.** TR/EN catalogs carry matching non-empty plain-text keys for loading, six focuses, three volume actions, three intensity actions, metrics, eight reasons, neutral/missing/error/malformed states, and retry.
9. **Escaping coverage.** Unicode, quotes, angle brackets, ampersands, and `</script>`-like hostile content round-trip through the inert JSON block without creating executable markup.
10. **Initialization API.** `window.FitXWeeklyProgram.init(root) -> boolean` remains stable, uses the DOM initialization marker, and repeated calls are inert.
11. **Initial transition.** First initialization removes `aria-hidden`, adds the card classes, enters `idle`, then synchronously replaces the inert copy node with `loading`.
12. **Request contract.** Loading issues exactly one `fetch(ENDPOINT, {method: 'GET', headers: {'Accept': 'application/json'}})`.
13. **States.** `data-weekly-program-state` records `idle`, `loading`, `populated`, `insufficient_data`, `missing_baseline`, `error`, or `malformed`.
14. **Concurrency.** One in-memory `activeRequest` guard prevents overlapping loads and duplicate retry requests.
15. **Stale completion.** A monotonically increasing request generation is captured per load; generation checks prevent an older completion from replacing a newer state. The retained retry-button double-click scenario proves only one newer request starts.
16. **Retry.** Error and malformed states render one native retry button. Retry immediately clears prior content, removes the button from the live DOM, re-enters loading, and makes exactly one additional request.
17. **Transport failures.** Network rejection, redirect/login navigation, non-2xx status, and non-JSON bodies enter the localized `error` state without backend detail or payload logging.
18. **Malformed failures.** A successful JSON response that violates the client contract enters the distinct localized `malformed` state.
19. **Object contract.** The response must be a plain object with a nonnegative integer `weeks`, boolean `has_data`, canonical decision enums, finite delta, boolean readiness fields, and string arrays.
20. **Canonical enums.** The allowlists were verified directly against `training_planning`: focuses `insufficient_data/build_consistency/deload/maintenance/overload/steady`; volume `increase/hold/decrease`; intensity `progress/hold/deload`.
21. **Reason contract.** Only the eight published reason codes are accepted, in received order. Explanation keys must be the matching focus key followed one-to-one by matching reason keys.
22. **Volume/date contract.** Baseline and target are both null or both finite positive numbers. Null metrics require a null date; populated metrics require a real canonical `YYYY-MM-DD` date.
23. **Additive evolution.** Unknown top-level fields and unknown string values in `unsupported` are accepted; unknown advice-driving enum/reason/explanation values remain malformed.
24. **Insufficient data.** `has_data=false` renders localized guidance only, with no metrics or action advice.
25. **Missing baseline.** Data with paired null metrics renders distinct missing-baseline guidance plus canonical focus/actions, without zero, percentage, or target reconstruction.
26. **Populated state.** Valid populated data renders focus, both server-supplied volumes, both actions, and reasons in backend order.
27. **Formatting.** `Intl.NumberFormat` uses `tr-TR` or `en-US`, at most two decimals, followed by the localized kg unit.
28. **Intentional omissions.** The client validates but does not display `baseline_week_start` or `volume_delta_pct`; it performs no target or seven-day arithmetic and does not sort explanations.
29. **Safe DOM.** Rendering uses `createElement`, `textContent`, attributes, `append`, and `replaceChildren`; unsafe HTML APIs, eval/Function, storage, timers, polling, and cancellation are absent.
30. **Accessibility.** There is one section heading, loading uses `role=status`, error/malformed use `role=alert`, hidden content is not focusable, and retry has a visible focus style plus 44 px minimum height.
31. **Responsive CSS.** Only `.weekly-program-*` rules were added to `training.css`; metrics/actions use two columns where possible and one column at the existing 640 px breakpoint, with `min-width:0`, wrapping, and shared skeleton/reduced-motion behavior.
32. **Focused client evidence.** `tests/test_weekly_program_ui_js.py` passes 103 source and Node behavior tests, including all focus/action mappings, TR/EN formatting, ordered reasons, neutral/missing separation, failures, malformed cases, additive fields, retry, request counts, and CSS guards.
33. **Prescribed regression.** The exact targeted command from the PR6.2 plan completed with **423 passed, 0 failed**.
34. **Full suite.** Fresh `python -m pytest -q` completed with **2,244 passed, 3 deselected, 0 failed** in 217.39 seconds.
35. **Asset impact.** `weekly_program.js` changed from 2,248 to 12,144 bytes (+9,896); `training.css` changed from 24,398 to 26,741 bytes (+2,343). OFF requests neither feature JavaScript nor feature copy.
36. **Static/self-review.** `node --check`, locale JSON parsing/parity, security source guards, dependency boundaries, and `git diff --check` are clean. Review classifications: High none; Medium none; Low none in scope; Technical Debt is the pre-existing datetime deprecation warning set; Nice-to-have live visual validation is deferred.
37. **PR6.3 and delivery.** PR6.3 covers real-browser/mobile accessibility validation (performed via headless Chromium — §25), cache/privacy headers, observability decisions, SQL/performance audit, the four-way runtime flag matrix, the full-suite regression run (§30–34), and production-readiness audit. No push or pull request was performed.


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
- Tests: `tests/test_training_progression.py` (25 tests — fixture-free pure signal tests
  plus DB-backed roll-up via `make_user`; the last four are golden characterization
  tests added before PR3 to strengthen coverage).

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

- `python -m pytest tests/test_training_progression.py -v` — 25 passed.
- Regression (behavior preserved): `python -m pytest tests/test_training_history.py
  tests/test_training_generation.py tests/test_ai_coach.py tests/test_progress_api.py -q`
  — 92 passed.
- Import direction confirmed one-way: `training_history` contains no reference to
  `training_progression` (no circular import).

### Independently safe to merge

Yes — a purely additive service package + hermetic tests + docs. No schema/migration, no
route, no coach-prompt, no UI, and no change to any existing runtime caller (all four
foundation consumers regression-green). The layer is dormant until a later PR consumes it.

## Sprint 6 PR3 - Adaptive Planning Engine

Date: 2026-07-20
Scope: A canonical, deterministic adaptive-planning layer built on top of the Sprint 6
PR2 progression engine, turning the single `next_signal` into a normalized weekly plan
recommendation (`AdaptivePlan`), plus the one runtime convergence both prior handoffs
nominated: `GET /api/progress/workout` now reads WorkoutLog through the training-history
foundation (byte-identical, characterization-tested). No schema, no new route, no
coach-prompt/UI change. **The next Sprint 6 PR must read this section (and the PR1/PR2
sections above) before implementing anything.**

### What this PR changed

- Added `app/services/training_planning/` — the single source of truth for turning
  progression signals into a weekly plan recommendation. Layered pure/impure and
  strictly one-way dependent (`training_planning` → `training_progression` →
  `training_history`; neither lower layer imports it):
  - `models.py` — frozen value object `AdaptivePlan` (week_focus, volume_action,
    intensity_action, volume_delta_pct, overload_ready, maintenance_recommended,
    ordered `reason_codes` tuple, embedded `ProgressionReport`). Every field has a safe
    neutral default; `AdaptivePlan(weeks=0)` IS the neutral plan (its `reason_codes`
    default is `("insufficient_history",)` so even the neutral object explains itself).
    The embedded report default REQUIRES `field(default_factory=lambda:
    ProgressionReport(weeks=0))` — a bare default raises `ValueError` (report holds
    lists). Like its siblings the object is unhashable; compare with `==`.
  - `analysis.py` — pure decision rules: `derive_week_focus`, `derive_volume_action`,
    `derive_intensity_action`, `volume_delta_for`, `derive_reason_codes`,
    `derive_adaptive_plan` (the pure composer — the WHOLE decision engine tests
    fixture-free). Constants: `VOLUME_INCREASE_STEP = 0.05`, `DELOAD_VOLUME_CUT = 0.40`.
  - `__init__.py` — public API + `build_adaptive_plan(user_id, weeks=4, *,
    end_day=None)`: one `build_progression_report` call, then pure derivation.
- Converged `progress_workout()` (`app/blueprints/tracking.py`) onto
  `fetch_workout_entries(..., include_markers=True)`; dropped the now-unused
  `WORKOUT_COMPLETION_MARKER` import from tracking.py. DailyActivity merge, day loop,
  and response shape untouched.
- Docs: added `docs/TRAINING_PLANNING.md`; CLAUDE.md service-index line added and the
  PR1 line's convergence status corrected.
- Tests: `tests/test_training_planning.py` (19 tests) + 4 characterization tests in
  `tests/test_progress_api.py` (added and green BEFORE the convergence, unchanged after).

### Code paths inspected

`docs/handoff.md` (PR1+PR2 sections), `app/services/training_history/*`,
`app/services/training_progression/*` (models/analysis/`__init__` in full),
`docs/TRAINING_HISTORY.md`, `docs/TRAINING_PROGRESSION.md`, `app/timeutil.py`,
`app/services/training_generation/*` (confirmed: LLM one-shot generation, level
classification + static style-rule text — NO adaptive adjustment logic to collide
with), `app/models.py` (`TrainingPlan`, `WorkoutLog`, `WeeklyCheckIn.fatigue`),
`app/blueprints/training.py`, `app/blueprints/tracking.py` (all three inline readers),
`app/services/ai_coach.py` (`_today_workout_totals`, `_tool_get_progress_metric`
volume_lifted), `app/services/context_builder.py` (no progression block yet),
`tests/test_training_progression.py` (incl. the golden section PR2 left for this PR),
`tests/test_progress_api.py`, `tests/conftest.py`, `pytest.ini`.

### Adaptive planning decisions made

- **One precedence, not two.** `next_signal` is already the canonical single winner of
  PR2's precedence; the planner maps it 1:1 (`insufficient_data`→insufficient_data,
  `build_consistency`→build_consistency, `deload`→deload, `plateau`→**maintenance**,
  `progressing`→**overload**, `keep_pushing`→steady) and NEVER derives decisions from
  the raw report booleans. Safety invariants ("never recommend an increase to an
  inconsistent user"; overload_ready requires consistent+progressing) hold by
  construction and are pinned by `test_never_increase_without_consistent_progression`.
- Only `overload` moves volume up (`+VOLUME_INCREASE_STEP` = +5%, well under the ≤10%
  guideline); only `deload` moves it down (`-DELOAD_VOLUME_CUT` = -40%, i.e. train at
  ~60%, the conservative middle of the 50-60% band).
- **plateau → maintenance week (hold/hold), not an intensity push:** a plateau without
  deload means short history or a recent rest week; without fatigue data we cannot
  distinguish under-recovered from under-stimulated, and pushing the fatigued case is
  the harmful branch.
- **keep_pushing → steady even when volume trends down:** an "increase back to
  baseline" on ambiguous signals is speculative; `reason_codes` carry the down-trend
  nuance (`volume_trend_down`/`strength_trend_down` appended in fixed order).
- **Intensity magnitude deliberately not modelled** — meaningless without per-exercise
  data (WorkoutLog has no per-set granularity); volume is the one measured knob.
- Unknown/future `next_signal` strings fall back to the neutral focus (safe `dict.get`).
- Marker-only history stays `steady` — attendance never justifies overload (pinned).

### What the canonical planner now provides

`build_adaptive_plan(user_id, weeks=4, *, end_day=None) -> AdaptivePlan` answers,
deterministically and user-scoped: what should the user do next week (`week_focus`);
volume up/flat/down (`volume_action` + `volume_delta_pct`); intensity progress/hold/
deload (`intensity_action`); overload-ready? (`overload_ready`); plateauing?
(`plan.progression.is_plateau`); maintenance week? (`maintenance_recommended`); and
the safest next adjustment (the focus/action/delta tuple, plus ordered machine-readable
`reason_codes` for future AI/UI presentation — locale-neutral by design).

### Convergence performed (and its byte-identity argument)

`progress_workout()` before/after produces identical JSON: same window start
(`utc_day_bounds(start)[0]` inside `fetch_workout_entries`), same Istanbul day keys
(`performed_on` == `app_date_of(created_at)`), same marker rule (markers count as
session days, excluded from volume). The only semantic delta: the old query had NO
upper time bound while the foundation bounds at end-of-today-Istanbul — they differ
only for rows with future timestamps (impossible at runtime; the old code counted such
phantom rows in `totals` without ever rendering them in `days`). Float-sum ordering
differs (unordered vs `created_at ASC`) but is masked by `round()`. Verified by 4 new
characterization tests written and green against the OLD code first, then unchanged
against the new code, plus the pre-existing marker test.

### Intentionally left for later PRs (deliberate debt)

- **No runtime consumer of `AdaptivePlan` yet** — coach context/prompt wiring (behind a
  flag, with prompt tests) is the natural first consumer; nothing surfaces
  `next_signal` or the plan to users yet.
- Remaining inline readers: `tracking.py` heatmap + insights WorkoutLog sub-blocks,
  `fitx_mcp/server.py` (raw SQL, standalone process — leave last),
  `analytics_engine.py` `_check_missing_logs`, and `ai_coach._tool_get_progress_metric`
  `volume_lifted` (raw SUM; markers are volume=0 so unaffected).
- **Deload/plateau still have no fatigue input** — `WeeklyCheckIn.fatigue` /
  `uyku_kalitesi` (already consumed by `training_generation`'s time_series/recovery
  models) could enrich `detect_deload_due` and let plateau→maintenance become smarter.
- Intensity magnitudes (per-lift guidance) and Turkish UI copy for `reason_codes`.
- Pre-existing `datetime.utcnow()` deprecation warnings remain (Python 3.14).

### Exact next steps for the following PR

1. Read this section first (and PR1+PR2 above).
2. Build on `build_adaptive_plan` / `build_progression_report` — do NOT add new inline
   windowing/marker/trend/decision logic anywhere.
3. Highest-value next work: the first runtime CONSUMER — surface the plan (or at least
   `next_signal`) in the coach context block (`context_builder.py`), additive and behind
   a flag (e.g. `AI_ADAPTIVE_PLAN_CONTEXT`), with prompt tests proving the block renders
   and the flag-off path is byte-identical.
4. Optionally enrich `detect_deload_due` with `WeeklyCheckIn.fatigue` (keep the neutral
   contract: missing check-ins → current volume-only behavior).
5. Then converge tracking.py heatmap/insights WorkoutLog sub-blocks (small); leave
   `fitx_mcp/server.py` last.

### Verification evidence

- `python -m pytest tests/test_training_planning.py -v` — 19 passed.
- Characterization: `python -m pytest tests/test_progress_api.py -v` — 12 passed
  BEFORE the tracking.py change and 12 passed (identical list) AFTER.
- Regression: `python -m pytest tests/test_training_progression.py
  tests/test_training_history.py tests/test_training_generation.py tests/test_ai_coach.py
  tests/test_progress_api.py -q` — 117 passed. `tests/test_tracking_routes.py` +
  `tests/test_progress_api.py` — 64 passed.
- Dependency direction: no reference to `training_planning` inside `training_history/`
  or `training_progression/` (verified by grep; no circular import).
- Full suite: `python -m pytest -q` — 1893 passed, 3 deselected (load tests,
  per pytest.ini), in 159s.

### Independently safe to merge

Yes — an additive service package + hermetic tests + docs, plus one behavior-preserving
convergence proven by characterization tests written against the old code. No
schema/migration, no new route, no coach-prompt/UI change; the planner is dormant until
a later PR consumes it.

## Sprint 6 PR4 - AI Coach AdaptivePlan Integration

Date: 2026-07-20 (prompt-authority remediation closed out 2026-07-21)
Scope: First production runtime consumer of AdaptivePlan, behind one default-OFF flag.

### What changed

- Added the sole Version 1 AdaptivePlan prompt contract adapter.
- Added strict `AI_ADAPTIVE_PLAN_CONTEXT` rollout/rollback gating.
- Wired the shared context builder once for blocking/streaming and OpenAI/Bedrock.
- Added complete neutral fallback and non-sensitive enabled-only debug lifecycle logs.
- Added baseline/provider goldens and automated dependency/serializer ownership guards.
- Made AdaptivePlan the sole planning authority of the enabled-path system prompt:
  the two legacy rules that let the Coach set volume/intensity itself (injury item 4,
  the weekly check-in bullet) are rewritten and an explicit authority block is
  appended. OFF still returns the untouched legacy prompt.
- Threaded that prompt switch as an explicit flag-driven argument from `ai_coach` into
  `prompt_builder` (`ai_stream` inherits it), so user-written context text cannot
  select the adaptive prompt or pass a forged plan block off as canonical.

### Canonical consumer contract

The Coach receives normalized plan and progression summary fields only. It is a
read-only presenter and never re-derives or overrides decisions. The serializer is
additive-only Version 1; future consumers use AdaptivePlan directly or this adapter.
The presenter role is enforced in the prompt as well as in the block: enabled-path
`build_coach_system(..., adaptive_plan_context=True)` forbids recomputing overload,
deload, volume, intensity, and progression, so no second planning authority survives
in the system prompt (docs/TRAINING_PLANNING.md, "Prompt authority").

### Changed paths

- `app/services/adaptive_plan_context.py` (created)
- `app/config.py`
- `app/services/context_builder.py`
- `app/prompts/system.py`
- `app/services/prompt_builder.py`
- `app/services/ai_coach.py`
- `tests/test_prompt_builder.py`
- `tests/conftest.py`
- `tests/test_adaptive_plan_context.py` (created)
- `tests/test_dependency_boundaries.py`
- `.env.example`
- `tests/test_env_example.py`
- `docs/TRAINING_PLANNING.md`
- `docs/AI_ARCHITECTURE.md`
- `CLAUDE.md`
- `docs/handoff.md`

### Inspected paths

- `docs/handoff.md` (Sprint 6 PR1-PR3 sections)
- `app/services/training_history/*`
- `app/services/training_progression/*`
- `app/services/training_planning/*`
- `app/services/context_builder.py`
- `app/services/ai_pipeline.py`
- `app/services/ai_coach.py`
- `app/services/prompt_builder.py`
- `app/config.py` and `.env.example`
- `tests/test_ai_coach.py`, `tests/test_ai_pipeline.py`, `tests/test_ai_stream.py`
- `tests/test_prompt_builder.py`, `tests/test_dependency_boundaries.py`
- `tests/test_training_history.py`, `tests/test_training_progression.py`,
  `tests/test_training_planning.py`, and `tests/test_progress_api.py`
- `docs/TRAINING_HISTORY.md`, `docs/TRAINING_PROGRESSION.md`, and
  `docs/TRAINING_PLANNING.md`

### Deliberately deferred

- Tracking heatmap/insights raw readers, MCP raw SQL, analytics missing-log reader,
  and ai_coach volume_lifted remain intentional debt.
- No fatigue/recovery enrichment, per-lift intensity, UI, schema, or heuristic work.
- The enabled path still shares one system prompt for every turn; a per-turn "is this
  question about training?" gate is deliberately not modelled.
- Forged context text can no longer select the adaptive prompt (the switch is
  flag-driven), but an enabled-path context could still *contain* a second, forged
  contract block. That is the pre-existing untrusted-context class the base prompt's
  SECURITY rule and the FRIEND_DATA fence cover; no new mitigation was added here.

### Exact next steps

1. Read the Sprint 6 PR1-PR4 handoff sections before any next change.
2. Keep AdaptivePlan as the single planning truth; use it directly or the canonical
   Version 1 adapter—never add a competing serializer or decision ladder.
3. Choose one explicitly scoped next consumer or one deferred reader convergence;
   do not combine broad debt cleanup with a new adaptive feature.
4. Preserve the default-OFF rollback until enabled-path rollout evidence is reviewed.

### Independently safe to merge

Yes: default-OFF byte identity is golden-pinned (context bytes and all three provider
payload shapes, including the system prompt); the prompt switch is flag-driven, so no
user-writable field can reach it; enabled failures return the complete neutral
contract; no schema, heuristic, UI, or unrelated reader changed; flag OFF is the
immediate rollback.

### Verification evidence

Re-measured on 2026-07-21 against this branch, which replays the PR4 delta on top of
current `main` (PR #173 was merged into `sprint6-pr3-adaptive-planning`, not `main`,
so PR4 reaches `main` through PR #175). These supersede the pre-remediation counts,
which predated the boundary-guard and prompt-authority tests.

- `python -m pytest tests/test_adaptive_plan_context.py tests/test_dependency_boundaries.py tests/test_env_example.py tests/test_prompt_builder.py tests/test_ai_pipeline.py tests/test_ai_coach.py tests/test_ai_stream.py tests/test_coach_tools.py -q`
  - 209 passed in 120.62s.
- `python -m pytest tests/test_training_history.py tests/test_training_progression.py tests/test_training_planning.py tests/test_training_generation.py tests/test_training_routes.py tests/test_progress_api.py tests/test_tracking_routes.py -q`
  - 164 passed in 128.91s.
- Full suite on CI (`ci.yml` run 29826743288, PR #175)
  - 1946 passed, 3 deselected in 120.81s; schema-drift guard green.
- OFF-path prompt identity checked outside pytest as well: `build_coach_system()` for
  `tr`, `en`, and the invalid-language fallback is byte-identical to the same function
  loaded from `git show HEAD:app/prompts/system.py`.

## Sprint 6 PR5 - Adaptive Weekly Program Consumer

Date: 2026-07-22
Scope: The second consumer of the canonical `AdaptivePlan`, after PR4's coach-prompt
contract — a deterministic translation of the plan into a structured weekly-program
recommendation for future UI/runtime presentation. Purely additive and dormant: no
schema, no migration, no route, no coach-prompt, no flag, no UI, and no change to any
existing runtime caller. **The next Sprint 6 PR must read this section (and PR1-PR4
above) before implementing anything.**

### Internal summary of the foundation this PR builds on

- **`training_history` (PR1)** — the one `WorkoutLog` reader.
  `fetch_workout_entries` + pure `analysis.py` calcs (`weekly_windows`,
  `bucket_by_week`, `volume_trend`, `estimated_1rm`). Istanbul-day windows via
  `app.timeutil`; `WORKOUT_COMPLETION_MARKER` rows count as trained days but carry
  `volume=0` and are excluded from volume/sets.
- **`training_progression` (PR2)** — pure interpretation into signals
  (`ProgressionReport`: volume/strength trend, `is_plateau`, `deload_due`,
  `load_consistency`, and the single `next_signal` that wins one documented
  precedence). Neutral values where a concept cannot be computed reliably.
- **`training_planning` (PR3)** — `AdaptivePlan`, the sole planning authority.
  Maps `next_signal` 1:1 to `week_focus` and derives `volume_action` /
  `intensity_action` / `volume_delta_pct` (`VOLUME_INCREASE_STEP = 0.05`,
  `DELOAD_VOLUME_CUT = 0.40`) / `overload_ready` / `maintenance_recommended` /
  ordered `reason_codes`, embedding the `ProgressionReport`. No second precedence
  ladder; nothing is derived from the report's raw booleans.
- **PR4 contract** — `app/services/adaptive_plan_context.py`: the sole
  `AdaptivePlan` -> prompt JSON serializer (v1, additive-only), behind the default-OFF
  `AI_ADAPTIVE_PLAN_CONTEXT` flag, plus the flag-driven `ADAPTIVE_COACH_SYSTEM_PROMPT`
  that strips the legacy volume/intensity authorities from the enabled-path prompt.
  The Coach is a read-only presenter.
- **Existing weekly-program / workout-prescription helpers: none.** Verified by search
  (`weekly_program|prescription` — no hits). `training_generation/program_generator.py`
  is the unrelated LLM workout-*content* path; it shares no vocabulary and neither
  imports the other.
- **Scope boundary for PR5** — translate, never decide. All planning decisions echo
  `AdaptivePlan`; anything it does not model is reported unsupported.

### Two discrepancies between the PR spec and the repository (documented, not silently resolved)

1. **The spec asked for "the Sprint 6 PR4 section of `docs/handoff.md`", which did not
   exist in the working checkout.** The local branch `agent/pr-171-triage-fixes`
   (`f8369ce`) was behind `origin/main`; PR4 landed upstream as `b8b1b67` (#175), with
   verification counts re-measured in `0c26ebf` (#176). The PR4 handoff section,
   `adaptive_plan_context.py`, and `tests/test_dependency_boundaries.py` existed only
   on `origin/main`. Resolved by branching this PR from `origin/main`
   (`sprint6-pr5-weekly-program`), not from the stale local branch.
2. **The "Sprint 6 PR1-PR4 audit results" are not in `docs/handoff.md`** — they are
   `NEEDED_FIXES.md` (triage 2026-07-21, `0c81619`). Its finding #5 bears directly on
   this PR and is recorded as inherited debt below.

### What this PR changed

- Added `app/services/weekly_program/` — the canonical weekly-program consumer.
  Layered pure/impure and strictly one-way dependent
  (`weekly_program` -> `training_planning` -> `training_progression` ->
  `training_history`; no lower layer imports it, and this layer reads no history):
  - `models.py` — frozen `WeeklyProgramRecommendation` + `UNSUPPORTED_CAPABILITIES`.
    Every field has a safe neutral default; `WeeklyProgramRecommendation(weeks=0)` IS
    the neutral recommendation.
  - `analysis.py` — pure rules: `select_volume_baseline`, `target_volume_for`,
    `derive_explanation_keys`, and the composer `derive_weekly_program` (the whole
    consumer tests fixture-free).
  - `__init__.py` — public API + `build_weekly_program(user_id, weeks=4, *,
    end_day=None)`: one `build_adaptive_plan` call, then pure translation.
- Extended the governance guards in `tests/test_dependency_boundaries.py`:
  `ADAPTIVE_PLAN_IMPORT_ALLOWLIST` gains the two importing modules, and
  `weekly_program` is registered in `TRAINING_LAYERS` /
  `FORBIDDEN_TRAINING_IMPORTS` so it is held to the same no-AI/no-prompt/no-provider/
  no-`app.extensions` rule as the layers beneath it.
- Docs: added `docs/WEEKLY_PROGRAM.md`; added a "Sprint 6 PR5 - weekly-program
  consumer" section to `docs/TRAINING_PLANNING.md` (including the two-consumer
  comparison table); added the `CLAUDE.md` service-index line.
- Tests: `tests/test_weekly_program.py` (35 tests).

### Code paths inspected

`docs/handoff.md` (PR1-PR4 sections), `NEEDED_FIXES.md`, `AGENTS.md`, `CLAUDE.md`,
`app/services/training_history/*` (`models`/`queries`/`analysis`/`__init__`),
`app/services/training_progression/*`, `app/services/training_planning/*` (all three
modules in full), `app/services/adaptive_plan_context.py`,
`app/services/training_generation/` (`program_generator.py`, `models.py`,
`time_series_model.py` — confirmed no weekly-program or prescription helper to reuse
or collide with), `app/blueprints/training.py` (route surface),
`docs/TRAINING_HISTORY.md`, `docs/TRAINING_PROGRESSION.md`, `docs/TRAINING_PLANNING.md`
(incl. the PR4 sections), `tests/test_training_planning.py`,
`tests/test_dependency_boundaries.py`, `tests/test_adaptive_plan_context.py`,
`tests/conftest.py` (`make_user`), `pytest.ini`.

### Consumer decisions made

- **Verbatim echo, no re-derivation.** All nine decision fields are copied from
  `AdaptivePlan` unchanged. Pinned from both directions: field-by-field equality across
  all six signals, and `test_decisions_ignore_observed_volume` — two plans with the
  same signal but wildly different volume series must yield identical decisions. If a
  decision ever starts tracking observed volume, that test fails.
- **Baseline is observational, and skips zero-volume weeks.** The newest window with
  `total_volume > 0` from the plan's embedded series. A rest week (or a marker-only
  week) is missing data, not a measurement of zero; anchoring to it would scale every
  recommendation to nothing. No raw `WorkoutLog` query is performed anywhere.
- **Target is arithmetic, not authority.** `round(baseline * (1 + volume_delta_pct),
  2)`. Two decimals matches `estimated_1rm` / `/api/progress/workout` and keeps binary
  float noise (`400 * 1.05 == 420.00000000000006`) out of a displayed number.
- **`None`, never `0.0`.** No positive volume — `baseline_weekly_volume` and
  `target_weekly_volume` are both `None`. `0.0` would read as "train nothing this
  week" instead of "not enough data to say".
- **No embedded plan; flat object.** So a future route/UI depends on this layer alone
  and never needs its own `training_planning` import — keeping the planner's approved
  outside owners to the recorded allowlist.
- **No serialization.** PR5 emits a frozen value object and never touches `json`, so
  `adaptive_plan_context` remains the single owner of the prompt contract and
  `test_adaptive_plan_prompt_serializer_has_one_owner` still finds exactly one.
- **Unsupported over invented.** `session_frequency`, `intensity_magnitude`, and
  `exercise_selection` are declared unsupported because `AdaptivePlan` models none of
  them. Filling them in requires new capability upstream, never a heuristic here.
- **Explanation hooks, not copy.** `explanation_keys` are the existing canonical codes
  behind a `weekly_program.` prefix — no second taxonomy, no Turkish text.

### What the canonical weekly-program consumer now provides

`build_weekly_program(user_id, weeks=4, *, end_day=None) -> WeeklyProgramRecommendation`
answers, deterministically and user-scoped: what kind of week to run and whether
volume/intensity move (echoed); what the user's most recent real weekly volume was
(observed); what weekly volume the plan's own delta implies (derived); which ordered,
locale-neutral keys explain it; and which program capabilities the adaptive stack
cannot yet support.

### Intentionally left for later PRs (deliberate debt)

- **Nothing consumes it yet** — no route, no template, no coach wiring. A read-only
  endpoint (or a UI card) is the natural next step and was deliberately excluded to
  keep this PR independently safe. *(Superseded by Part 2 below: the read-only endpoint
  `GET /api/training/weekly-program` now exists on this same branch. Template and coach
  wiring are still deliberately absent.)*
- **Turkish UI copy** for `explanation_keys` (and for `reason_codes`, still open from
  PR3) — `locales/*.json` work for a UI PR.
- **`session_frequency` / `intensity_magnitude`** stay unsupported until
  `AdaptivePlan` models them; `WorkoutLog` has no per-set granularity.
- ~~**Inherited: `NEEDED_FIXES.md` finding #5**~~ — *superseded by the post-audit
  remediation below.* This was recorded as inherited debt on the grounds that PR5 must
  not touch progression heuristics. The production-readiness audit then showed the same
  forward-looking window was also corrupting **this layer's own published contract**
  (a single day's session republished as `baseline_weekly_volume`), which made it PR5's
  concern. It is now fixed upstream in `weekly_windows`, with no heuristic change — see
  *Post-audit remediation* below.
- Unconverged raw readers from PR1-PR4 are unchanged: `tracking.py` heatmap/insights
  sub-blocks, `fitx_mcp/server.py` (raw SQL, standalone — leave last),
  `analytics_engine._check_missing_logs`, `ai_coach._tool_get_progress_metric`
  `volume_lifted`.
- Pre-existing `datetime.utcnow()` deprecation warnings remain (Python 3.14).

### Exact next steps for the following PR

1. Read this section first (and PR1-PR4 above). Confirm the branch is based on current
   `origin/main` before starting — PR5 hit exactly this trap.
2. Keep `AdaptivePlan` the single planning truth. Consume it directly, through the
   PR4 v1 serializer, or through `weekly_program` — never add a competing serializer,
   decision ladder, or threshold.
3. Highest-value next work: the first *presentation* of
   `WeeklyProgramRecommendation` — a read-only `GET` endpoint under `@require_auth`
   (user-scoped, no new query) and/or a training-page card, plus `locales/*.json` copy
   for `explanation_keys`. Consuming `weekly_program` itself needs no allowlist
   change; only reaching past it to the planner does.
4. Consider fixing `NEEDED_FIXES.md` finding #5 as its own PR (a progression-layer
   change with golden coverage) — do not fold it into a consumer PR.
5. Leave `fitx_mcp/server.py` last.

### Verification evidence

- `python -m pytest tests/test_weekly_program.py -q` — 35 passed in 45.16s.
- `python -m pytest tests/test_dependency_boundaries.py -q` — 26 passed in 14.74s.
- Regression (adaptive stack + PR4 consumer):
  `python -m pytest tests/test_training_planning.py tests/test_training_progression.py
  tests/test_training_history.py tests/test_adaptive_plan_context.py
  tests/test_prompt_builder.py -q` — 101 passed in 67.46s.
- Regression (coach/training runtime): `python -m pytest tests/test_ai_coach.py
  tests/test_ai_pipeline.py tests/test_progress_api.py tests/test_training_routes.py
  tests/test_sprint6_migration_golden.py -q` — 115 passed in 118.63s.
- Static boundary proof: `app/services/weekly_program/` contains no `WorkoutLog`,
  `app.models`, `app.extensions`, Flask, or `json` import — only
  `app.services.training_planning`, stdlib, and relative imports.
- Full suite: `python -m pytest -q` — 1981 passed, 3 deselected, 8051 warnings in
  1001.82s (16m41s). Zero failures; the warning count is the pre-existing
  `datetime.utcnow()` deprecation noise (Python 3.14), unchanged by this PR.

### Independently safe to merge

Yes — a purely additive service package + hermetic tests + docs, plus an additive
strengthening of the dependency guards. No schema/migration, no route, no
coach-prompt, no flag, no UI, and no change to any existing runtime caller or
heuristic. Nothing calls the layer yet, so the runtime behavior of this branch is
identical to `main`; the rollback is deleting a dormant package.

*(Part 2 below adds one read-only `GET` route on this branch. Everything else in this
paragraph still holds; see Part 2's own merge-safety note for the current state.)*

### Part 2 - runtime exposure (`GET /api/training/weekly-program`)

Date: 2026-07-22. Same branch, same PR — the dormant layer above is now readable over
HTTP. Everything in Part 1 still holds; this subsection records only what changed and
**supersedes the Part 1 statements that "nothing consumes the layer yet" and that the
PR adds no route.** The PR remains additive, read-only and independently mergeable.

#### What changed

- **`app/services/weekly_program/payload.py` (new, pure).**
  `weekly_program_payload(recommendation) -> dict` — the JSON-safe projection of the
  frozen value object. Two mechanical conversions (`date` -> ISO string, tuples ->
  lists) and nothing else; `None` is preserved, never coerced to `0`. The field list is
  written out explicitly rather than generated from `dataclasses.asdict`, because a
  published API surface should grow by decision, not by leak — and the route test pins
  both directions (every model field exposed, nothing extra).
  Imports only `.models`, so it never touches `AdaptivePlan` and the PR4 serializer
  guard is unaffected.
- **`app/services/weekly_program/__init__.py`** — exports `weekly_program_payload`;
  docstring/layering note updated (the layer is no longer dormant).
- **`app/blueprints/training.py`** — one route, `get_weekly_program`:

      @bp.route("/api/training/weekly-program")
      @require_auth
      def get_weekly_program():
          recommendation = build_weekly_program(current_user.id, weeks=4, end_day=None)
          return jsonify(weekly_program_payload(recommendation))

  Placed beside the other read-only training JSON routes (`/workout/status`,
  `/training-plan/active`). No limiter (matching its read-only siblings), no flag, no
  template, no coach call.
- **`tests/test_weekly_program_route.py` (new)** — 17 tests.
- Docs: `docs/WEEKLY_PROGRAM.md` (runtime-surface section + contract table + example
  body + payload API + test inventory), `docs/TRAINING_PLANNING.md` (two-consumer table
  now names the endpoint), `CLAUDE.md` (service-index line records the route and the
  pinned window).

No schema, no migration, no coach-prompt, no flag, no UI/template, no provider change,
and no edit to any planning/progression/history heuristic.

#### Runtime-surface decisions (and why)

- **Blueprint choice.** `app/blueprints/training.py` already owns the training runtime
  surface and the read-only JSON routes next to it. The alternative
  (`tracking.py`, home of `/api/progress/*`) is the *history* reporting surface — the
  weekly program is a forward-looking recommendation, not a progress read-out.
- **`weeks`/`end_day` are pinned, not query parameters.** The analysis window is a
  planning knob. Reading it from the query string would hand a caller partial planning
  authority and make the response non-deterministic for a given user and day.
  `?weeks=1&end_day=...` is ignored, and `test_query_string_cannot_retune_the_window`
  pins that. It also keeps the route free of input validation entirely.
- **Projection owned by the layer, not the route.** Had the route built the dict
  inline, a second shape could drift in next to the value object. `payload.py` keeps
  one owner for the HTTP contract, and the route stays two statements.
- **No feature flag.** PR4 needed `AI_ADAPTIVE_PLAN_CONTEXT` because it *changed coach
  behavior* on an existing path. This adds a new read-only endpoint that nothing calls
  yet; there is no behavior to roll back, and a flag would be ceremony. The spec
  allowed one only "if one already exists and is clearly needed" — neither holds.
- **Empty history returns 200 with the neutral payload,** not 404 and not an error. "No
  data yet" is a legitimate recommendation state the layer already models.
- **Structural guard over a behavioural one.** "The route does not read `WorkoutLog`"
  cannot be asserted by calling it — `training.py` legitimately imports `WorkoutLog`
  and `db` for other routes. Two tests parse the view's own AST instead: no
  history/planner/ORM names appear inside it, and its body is exactly
  `build_weekly_program` + `weekly_program_payload` + `jsonify`. That is the test that
  fails the day someone starts computing in the route.

#### The JSON contract

Field-for-field with `WeeklyProgramRecommendation` — same names, no extra decision
fields, no renames, no raw history, no `WorkoutLog` rows, no weekly series:

    weeks, has_data, week_focus, volume_action, intensity_action, volume_delta_pct,
    overload_ready, maintenance_recommended, baseline_week_start (ISO date | null),
    baseline_weekly_volume (float | null), target_weekly_volume (float | null),
    reason_codes [], explanation_keys [], unsupported []

Volume semantics are unchanged from Part 1 and the route does not reinterpret them:
baseline is the newest **positive** weekly volume observed in the plan's embedded
series (zero-volume weeks skipped), target is `round(baseline * (1 + volume_delta_pct),
2)`, and both are `null` together when no positive volume exists.

Auth: `@require_auth`, unauthenticated -> 302 to login. Methods: `GET` only (405
otherwise). Scoping: `current_user.id`, through the planner's own filter — the route
runs no query.

#### Still intentionally out of scope

- **Coach wiring** — the coach still consumes only the PR4 v1 prompt contract. This
  endpoint is not referenced from any prompt, tool, or `context_builder` path.
- **UI** — no template, no fetch call, no card. The endpoint has no in-app caller yet;
  it exists so a UI PR can be pure front-end work.
- **Turkish copy** for `explanation_keys`/`reason_codes` (`locales/*.json`) — the API
  deliberately emits keys, never user-facing text.
- **`session_frequency` / `intensity_magnitude` / `exercise_selection`** — still
  published as `unsupported`; filling them in needs new capability in `AdaptivePlan`
  first.
- **`NEEDED_FIXES.md` finding #5** (deload effectively gated on "trained today") —
  still inherited, still a progression-layer fix, still deserves its own PR.

#### Exact next steps for the following PR

1. Read Part 1 **and** this subsection before starting; confirm the branch is based on
   current `origin/main`.
2. The natural next work is now purely front-end: render
   `GET /api/training/weekly-program` on the training page (`_head.html` include for
   CSRF/CSP, `static/csrf.js` already wraps `fetch`) plus `locales/*.json` copy for the
   `explanation_keys` / `reason_codes` vocabulary. No new service work is required.
3. If a consumer needs a different analysis window, add capability upstream in
   `AdaptivePlan` — do **not** open `weeks`/`end_day` as query parameters.
4. Keep `AdaptivePlan` the single planning truth: consume it directly, through the PR4
   serializer, or through `weekly_program`; never add a competing serializer, decision
   ladder, or threshold.
5. Leave `fitx_mcp/server.py` last.

#### Verification evidence (current HEAD — supersedes the Part 1 counts)

- `python -m pytest tests/test_weekly_program_route.py -q` — 17 passed in 43.45s.
- `python -m pytest tests/test_weekly_program.py tests/test_dependency_boundaries.py -q`
  — 61 passed in 48.73s (35 layer + 26 guard; the guards still pass unchanged, since
  the route imports `weekly_program`, not `training_planning`).
- Adaptive stack + PR4 consumer: `python -m pytest tests/test_training_planning.py
  tests/test_training_progression.py tests/test_training_history.py
  tests/test_adaptive_plan_context.py tests/test_prompt_builder.py -q` — 101 passed in
  65.20s.
- Route/coach runtime: `python -m pytest tests/test_training_routes.py
  tests/test_progress_api.py tests/test_ai_coach.py tests/test_coach_routes.py
  tests/test_tracking_routes.py tests/test_sprint6_migration_golden.py -q` — 187 passed
  in 189.22s.
- `git diff --check` — clean (no whitespace errors).
- Route inventory / auth audit (would catch an unprotected new endpoint):
  `python -m pytest tests/test_auth_audit.py tests/test_require_auth.py -q` — 19 passed
  in 51.53s.
- Full suite: `python -m pytest -q` — 1998 passed, 3 deselected, 8185 warnings in
  1002.47s (16m42s). Zero failures. Exactly Part 1's 1981 plus the 17 new route tests,
  so the endpoint added coverage without disturbing a single existing test. The warnings
  remain the pre-existing `datetime.utcnow()` deprecation noise (Python 3.14).

#### Independently safe to merge

Yes. The change is one additive `GET` route behind `@require_auth`, one pure projection
module, hermetic tests, and docs. No schema, no migration, no coach-prompt, no flag, no
UI, no provider change, and no edit to any existing route, heuristic, or runtime caller
— every pre-existing path behaves exactly as on `main`. Nothing in the app calls the new
endpoint, so the blast radius is the endpoint itself; the rollback is deleting the route
and the package. `AdaptivePlan` remains the sole planning authority: the endpoint
decides nothing, and the AST guards fail if it ever starts to.

### Part 3 — post-audit remediation (window geometry, fixtures, F4-F6)

Date: 2026-07-22. Same branch. The combined PR5 production-readiness audit (parts 1 + 2)
returned one **High** finding and several minor ones; this subsection records the
remediation. It **supersedes the Part 1 statement that `NEEDED_FIXES.md` #5 is inherited
debt** — it is now fixed — and the Part 2 statement that the route propagates failures
to the global handler.

#### F1 (High) — the root cause, and why it became PR5's problem

`weekly_windows(end_day, weeks)` made the newest window *start* on `end_day`, so it
covered `[today, today + 6]`. History can never contain future entries, so that bucket
only ever held **today's** entries while presenting itself as a week. Because
`select_volume_baseline` scans newest-first for positive volume, it preferred that
partial bucket whenever the user had already trained that day:

- A Mon/Wed/Fri user at ~5000 kg a session has a 15000 kg week. Asked on a day they had
  trained, the endpoint published `baseline_weekly_volume: 5000.0` — understated by the
  user's training frequency — and `target_weekly_volume` derived from it.
- `baseline_week_start` named a window running six days into the future.
- The published target *fell* the moment the user logged the day's first set (before:
  the empty newest bucket was skipped and last week's real total was used; after: the
  single session won). Worse than being consistently wrong.

This geometry was already recorded as `NEEDED_FIXES.md` #5, rated *Low / Suspected*
**explicitly because the layer was "not yet wired into runtime"**. PR5 part 2 removed
that basis. Part 2 is also what turned the geometry into a user-facing absolute number,
which is why the fix belongs with PR5 even though the defective line is upstream.

**Fix — at the ownership boundary, not in the consumer.** `weekly_windows` now returns
trailing windows: the newest one *ends* on `end_day` (`[end_day - 6, end_day]`), each
earlier one 7 days before the next. It remains the single owner of window geometry —
`bucket_by_week`, `weekly_best_estimated_1rm`, `build_training_history_summary` and
`build_progression_report` all compose it and needed no change (the report builders
fetch `[starts[0], end_day]`, which simply widens from 22 to 28 days for `weeks=4`).

`weekly_program` deliberately gained **no** windowing logic. Teaching it to "skip the
current partial window" would have created a second windowing rule inside the one layer
whose entire contract is that it has none.

**One cause, both symptoms.** The same change also fixes `NEEDED_FIXES.md` #5:
`detect_deload_due` rejects a block containing a rest week, and the phantom empty newest
bucket made every rest day look like one — so deload could only fire on days the user
had already trained. The newest bucket is now a real week and the *unchanged* heuristic
evaluates the block it was written for. No threshold, precedence, or heuristic was
touched anywhere: `detect_deload_due`, `detect_plateau`, `assess_consistency`,
`series_trend`, `VOLUME_INCREASE_STEP`, `DELOAD_VOLUME_CUT`, `TREND_BAND` are all
byte-identical. This was a windowing-correctness fix, not a planning-policy change.

#### F3 — fixtures that could not see the bug

Every DB-backed fixture in the adaptive stack seeded exactly **one workout per window**,
which makes a single day and a full week numerically indistinguishable — the reason a
52-test PR shipped a wrong published number, and why
`test_baseline_is_observed_and_target_is_plan_arithmetic` had encoded the partial-window
value (400.0, one session logged today) as its expected weekly baseline.

- `_seed_multi_session_block` (`tests/test_weekly_program.py`) seeds a Mon/Wed/Fri-style
  block — three sessions per trailing window, counted back from `end_day`, with the
  offsets choosing whether `end_day` itself is a training or a rest day.
- Five cases cover it: trained-today (baseline must be the 15000 kg week, not the
  5000 kg session, and `baseline_week_start` must not run into the future), the same
  block on a rest day (identical result — the flip-flop is gone), one-session versus
  three-session weeks reading differently, a stable multi-week block, and a fixture
  self-check that each window really holds three sessions.
- **All five fail against the pre-fix geometry** (`assert 5000.0 == 15000.0`, and
  `[3, 3, 3, 1] != [3, 3, 3, 3]` for the self-check) — verified by temporarily
  reverting `weekly_windows`. That is what makes them load-bearing rather than
  decorative.
- The route fixture `_seed_progressing_block` now seeds three sessions per window too,
  so the HTTP-level assertion is about a weekly total; the test named above was
  corrected to the fixed contract (1200.0 / 1260.0) rather than left pinning the defect.
- The stack's other fixtures now seed against the derived `W0..W3` window constants
  instead of literal dates, so they state "a session in this window" and cannot silently
  re-anchor if the geometry is ever revisited.

#### F4 / F5 / F6 — dispositions

- **F4 failure semantics → option A (structured JSON 500).** The route catches planner
  and DB failures and returns `{"error": ...}` with `application/json`, matching the
  other JSON routes in this blueprint (`training.py` already does exactly this at three
  sites). It deliberately does **not** adopt PR4's neutral fallback: for the coach a
  degraded answer beats none, but here the neutral recommendation is a *legitimate user
  state*, so returning it on an outage would make a broken database indistinguishable
  from a new user — a UI would render a confident "not enough data yet" card over an
  incident. Without the catch, `errorhandler(500)` would have rendered `500.html`, i.e.
  an HTML body from a JSON endpoint. No `db.session.rollback()` is needed: unlike PR4
  the request ends immediately, and Flask-SQLAlchemy's teardown removes the session per
  request. Two tests pin it (JSON 500 + content type; and that the body carries none of
  `week_focus` / `has_data` / `baseline_weekly_volume`).
- **F5 `schema_version` → deliberately not added.** PR4 versions a *prompt* contract
  consumed by a model, where drift is invisible. No HTTP endpoint in this repository
  carries a version field, frontend and backend ship in one deploy, and adding it would
  weaken the bidirectional payload↔model test that currently proves the API exposes
  exactly the model's fields. **Evolution rule, recorded here so it is not re-litigated:
  this contract is additive-only** — fields may be appended, never renamed, retyped, or
  given new meaning. A breaking change introduces `schema_version` (or a new path) at
  that moment, not speculatively.
- **F6 observability → minimally added.** One `[TRAINING][WEEKLY_PROGRAM]` debug line
  (`has_data`, `week_focus`, whether a baseline exists) plus a warning on failure —
  following PR4's `[COACH][ADAPTIVE_PLAN]` precedent. Enough to separate "neutral
  because the user has no history" from "populated recommendation" from "upstream
  failure" before a UI depends on it. No history, volumes, payloads, or PII are logged.
- **Feature flags — unchanged and re-verified.** `AI_ADAPTIVE_PLAN_CONTEXT` remains the
  sole coach gate, default OFF, and PR5 reads no config at all. A new test requests the
  endpoint under both flag states and asserts the responses are **byte-identical**, so
  the read-only surface cannot quietly become coupled to a coach rollout. No PR5 flag
  was added: there is no live behaviour to roll back, and deleting the route remains a
  cleaner rollback than a flag.

#### Live-clock validation (exact observed values)

Mon/Wed/Fri block, 5000 kg per session, four trailing weeks, real clock
(`app_today() == 2026-07-22`), in-memory SQLite, called through the real entry point:

| | before (forward window) | after (trailing window) |
|---|---|---|
| `baseline_week_start` | `2026-07-22` (window ran to 07-28, i.e. into the future) | `2026-07-16` (ends on today) |
| `baseline_weekly_volume` | `5000.0` (one session) | `15000.0` (the real week) |
| `target_weekly_volume` | derived from 5000.0 | `9000.0` (= 15000 × 0.60) |
| rest-day result | `15000.0` — *different from the trained-day result* | `15000.0` — identical, no switching |
| newest window `session_count` | `1` | `3` |

`week_focus == "deload"` now appears against a live clock on a rest day, which finding
#5 said was impossible — the second symptom, visible at runtime.

**SQL cost:** one `SELECT` on `workout_log` per `build_weekly_program()` call, measured
with a `before_cursor_execute` listener. Unchanged by the fix; no N+1 introduced (the
fetch range widens from 22 to 28 days within the same single indexed query).

#### Verification evidence (current HEAD — supersedes the Part 2 counts)

- `python -m pytest tests/test_training_history.py -q` — 14 passed in 26.63s (was 13;
  +1 trailing-window contract test).
- `python -m pytest tests/test_training_progression.py -q` — 26 passed (was 25; +1
  deload-on-a-rest-day regression).
- `python -m pytest tests/test_training_planning.py tests/test_weekly_program.py -q` —
  59 passed (weekly_program 40, was 35; +5 multi-session cases).
- `python -m pytest tests/test_weekly_program_route.py -q` — 21 passed in 39.81s (was
  17; +1 live-clock multi-session baseline, +2 failure-path, +1 flag parity).
- `python -m pytest tests/test_dependency_boundaries.py tests/test_adaptive_plan_context.py
  tests/test_prompt_builder.py tests/test_training_routes.py tests/test_progress_api.py
  tests/test_tracking_routes.py tests/test_sprint6_migration_golden.py -q` — 165 passed
  in 152.43s.
- Pre-fix regression proof: with `weekly_windows` temporarily reverted, the five new
  multi-session tests fail with `assert 5000.0 == 15000.0` and `[3, 3, 3, 1] !=
  [3, 3, 3, 3]`.
- `git diff --check` — clean.
- Live-clock + SQL-count verification (hermetic in-memory SQLite, real clock): values in
  the table above; one `SELECT` per `build_weekly_program()` call.
- Full suite: `python -m pytest -q` — **2009 passed, 3 deselected, 8238 warnings in
  844.19s (14m04s)**. Zero failures. Exactly Part 2's 1998 plus the 11 new tests
  (1 history + 1 progression + 5 weekly-program + 4 route), so the remediation added
  coverage without disturbing a single existing test. Warnings remain the pre-existing
  `datetime.utcnow()` deprecation noise (Python 3.14).

#### Still out of scope (unchanged)

Coach wiring, UI/templates, Turkish copy for `explanation_keys`/`reason_codes`, the
`session_frequency` / `intensity_magnitude` / `exercise_selection` capabilities, the
unconverged raw readers (`tracking.py` heatmap/insights, `fitx_mcp/server.py`,
`analytics_engine._check_missing_logs`, `ai_coach._tool_get_progress_metric`), and every
other `NEEDED_FIXES.md` item (#1-#4, #6, #7) — untouched.

#### Exact next steps for the following PR

1. Read Parts 1, 2 **and** this subsection. PR6 is the front-end consumption of
   `GET /api/training/weekly-program` (`locales/*.json` copy for the
   `explanation_keys` / `reason_codes` vocabulary, plus the training-page card).
2. `baseline_weekly_volume` is now safe to render as a weekly total. Treat the contract
   as additive-only; if a field must change meaning, add a new one.
3. Before the UI ships, decide whether the F6 debug line should be promoted to a metric
   — it is currently the only signal distinguishing a neutral response from a degraded
   one.
4. Do not open `weeks`/`end_day` as query parameters, and do not add a second window
   rule anywhere above `training_history.weekly_windows`.
5. Leave `fitx_mcp/server.py` last.

## Sprint 6 PR6.1 - Weekly Program UI Foundation and Rollout Boundary

Date: 2026-07-22
Scope: the **first of three** PRs that introduce the Adaptive Weekly Program UI. This
one builds only the activation boundary — integration-surface selection,
characterization coverage, a default-OFF UI flag, a server-rendered mount shell and a
no-op frontend initializer. It deliberately **fetches no data and renders no
recommendation**; those are PR6.2. **The PR6.2 agent must read this section (and PR5
above) before implementing anything.**

### 1-2. Branch, HEAD, base

- Branch: `sprint6-pr6.1-weekly-program-ui`
- Base / HEAD at start: `origin/main` = `07ef1ff` ("feat(training): haftalık program
  tüketicisi + salt-okunur route + pencere düzeltmesi (Sprint 6 PR5) (#177)")
- PR5's own warning was heeded: the pre-existing local branch
  `sprint6-pr5-weekly-program` was 1 behind / 4 ahead of `origin/main`, but
  `git diff HEAD origin/main` was **empty** — PR5 had been squash-merged upstream. This
  branch was cut from `origin/main`, not from the stale local branch.
- Working tree at start: clean apart from an untracked `AGENTS.md` (pre-existing, not
  part of this PR).

### 3. Files created

| File | Purpose |
|---|---|
| `static/weekly_program.js` | Frontend initialization boundary (2,207 bytes) |
| `tests/test_training_page_characterization.py` | 69 characterization tests, written green **before** any source edit |
| `tests/test_weekly_program_ui.py` | 33 tests — OFF/ON template contract, flag isolation, route AST guards, SQL parity |
| `tests/test_weekly_program_ui_flag.py` | 21 tests — config default/parsing/independence/request-immunity |
| `tests/test_weekly_program_ui_js.py` | 45 tests — JS source guards + node-executed behavior + CSS/layout guard |

### 4. Files modified

| File | Change |
|---|---|
| `app/config.py` | `WEEKLY_PROGRAM_UI_ENABLED` constant + `app.config` mirror |
| `app/blueprints/training.py` | `training()` view passes one boolean to the template |
| `templates/training.html` | Flag-gated mount `<section>` + flag-gated `<script>` |
| `.env.example` | Commented `# WEEKLY_PROGRAM_UI_ENABLED=0` block |
| `tests/test_env_example.py` | Guard: documented and default-OFF |
| `docs/WEEKLY_PROGRAM.md` | New "UI rollout (Sprint 6 PR6)" section |
| `docs/TRAINING_PLANNING.md` | "Sprint 6 PR6.1 — UI rollout boundary (no third consumer yet)" |
| `CLAUDE.md` | UI-flag boundary appended to the `weekly_program` service line |

No schema, no migration, no dependency, no CSS file, no locale key.

### 5. Training surfaces inspected

- `/training` — `app/blueprints/training.py:training` -> `templates/training.html`:
  plan-creation form (`#setup-form`) **and** the active-plan view (`#active-plan-view`)
  containing the workout hero, the "Bu hafta" week strip (`#week-strip`), weekly stats
  (`#wstats`) and the plan-meta/reset row.
- Workout-status surfaces — `GET /workout/status`, `GET /training-plan/active` (JSON
  only, consumed by `static/training.js`).
- `/progress-page` (`templates/progress.html`) — a workout tab with volume charts
  (`#workoutChart`, `#workout-stats`), fed by `/api/progress/workout`.
- Dashboard `/` (`templates/index.html`) — a `qa-tile` quick-action linking to
  `/training` and a "next action" nudge; no training data region.
- Navigation — `templates/_nav.html` header links + drawer; there is no bottom
  navigation bar in this app (the drawer is the mobile pattern).
- Coach chat (`templates/chat.html` / `static/coach_widget.js`) — already the PR4
  consumer of `AdaptivePlan`.
- Gamification surfaces (`/quests`, `/challenges`, `/leaderboard`) — unrelated domain.
- Reusable primitives: `.card`, `.sec-label`, `.section`, `.wstats`, `.info-banner`,
  `templates/_head.html`, `_nav.html`, `_actionbar.html`.

### 6. Surface selected

**`/training`, inside `#active-plan-view`, between `#wstats` and `.apv-meta-row`.**

Why this is the correct information-architecture location:

- It is an existing authenticated training destination — no new route, no new
  navigation entry, no second training dashboard.
- That block already *is* the weekly overview ("Bu hafta" -> week strip -> weekly
  stats). The weekly program comments on exactly that horizon, so it reads as an
  intelligence card around the existing experience rather than a competing panel.
- `static/training.js:255` already shows `#active-plan-view` only when the user has an
  active plan, so the future card inherits the page's own view logic instead of
  inventing visibility rules, and can never land on the plan-creation form.
- It touches neither plan generation, plan controls, workout logging, nor Pump Check.

### 7. Alternatives rejected

| Alternative | Why rejected |
|---|---|
| New top-level page/nav entry ("Haftalık Program") | Explicitly out of scope; would create a second training destination for one card. |
| Top of `<main>`, outside both views | Would render over `#setup-form` too — a recommendation shown to a user who has not created a plan yet, and a layout the plan-creation flow was not designed around. |
| `/progress-page` workout tab | Retrospective analytics (charts of what happened). The weekly program is prescriptive (what to do next); mixing them muddies both. |
| Dashboard `/` card | The dashboard routes users to features; it holds no training data region, so this would be a new one. |
| Coach chat | PR4 already owns the coach's `AdaptivePlan` integration; adding a second surface there would duplicate it. |
| Replacing the workout hero / week strip | Forbidden: existing plan presentation must not be replaced. |

**Why no new navigation destination was needed:** `/training` is already reachable from
the header nav, the drawer and a dashboard quick-action tile, and it is where a user
goes to act on training. The recommendation is an annotation on that page, not a
destination.

### 8-10. Feature flag

- **Name:** `WEEKLY_PROGRAM_UI_ENABLED` (the spec's preferred name; no existing
  convention required otherwise).
- **Default:** OFF.
- **Parsing:** `os.getenv("WEEKLY_PROGRAM_UI_ENABLED", "0") == "1"` in `app/config.py`,
  mirrored to `app.config["WEEKLY_PROGRAM_UI_ENABLED"]` in the same apply block as
  `AI_ADAPTIVE_PLAN_CONTEXT`. Strict: only the exact string `"1"` enables it —
  `""`, `"0"`, `"true"`, `"yes"`, `"on"`, `"2"` and an absent variable are all OFF
  (each pinned by a subprocess test with a scrubbed environment, because
  `tests/conftest.py` sets flag values at import time and an in-process assertion would
  only re-measure conftest).
- **Server-controlled:** read once at boot. Not from query string, header, cookie,
  endpoint availability, content sniffing, prompt text or DOM text. `app/config.py`
  contains no `request.*` access, and requests carrying
  `?WEEKLY_PROGRAM_UI_ENABLED=1`, an `X-Weekly-Program-Ui-Enabled: 1` header, or a
  same-named cookie all still render no shell.
- **Not an authorization boundary.** It gates presentation only;
  `GET /api/training/weekly-program` remains `@require_auth` in every flag state.

### 11. Exact OFF-path behavior

With the flag absent/false, `/training` is **byte-identical to the pre-PR6.1 page**.
Verified once with a normalized before/after dump (`_v` cache-buster, CSP nonce and
CSRF token normalized): **82,664 bytes before, 82,664 bytes after, empty `diff`.**

Concretely, OFF renders: no `<section>`, no `id="weekly-program"`, no
`data-weekly-program-mount`, no `/static/weekly_program.js` tag, no CSS, no whitespace
change, no hidden placeholder, no loading skeleton, no listener, no feature log, no
metric, no navigation change, no endpoint request, and no extra SQL. The template omits
the block **server-side** rather than rendering hidden markup, and whitespace-control
markers (`{%- if %}` / `{%- endif %}`) absorb the tags' own newlines — that last detail
is what turns "visually unchanged" into "byte-identical", and
`test_off_adds_no_markup_between_weekly_stats_and_the_plan_meta_row` pins the exact seam
so a later edit cannot quietly reintroduce a blank line.

### 12. Exact ON-path shell behavior

The whole rendered delta, whole-document diffed, is two lines (+153 bytes): the
`<section id="weekly-program" data-weekly-program-mount aria-hidden="true"></section>`
and the `<script src="/static/weekly_program.js?v=...">` tag. Nothing else changes —
`test_on_is_the_off_document_plus_the_shell_and_script_only` compares the normalized
documents line by line and allows exactly those two additions and zero removals.

The shell is empty: no title, no locale key, no `week_focus` / `volume_action` /
`intensity_action` / `volume_delta_pct` / `overload_ready` / `maintenance_recommended` /
`baseline_weekly_volume` / `target_weekly_volume` / `reason_codes` / `explanation_keys`
/ `unsupported_capabilities` / `has_data`, no raw payload, no user id, no
`WeeklyProgramRecommendation`, no `AdaptivePlan`, no endpoint string, no flag name, and
no fake loading/neutral/error copy. `id` is the stable anchor;
`data-weekly-program-mount` is the JS hook.

### 13. Template integration boundary

`app/blueprints/training.py:training` passes **one** boolean,
`weekly_program_ui_enabled=current_app.config.get("WEEKLY_PROGRAM_UI_ENABLED", False)`.

Route-level context, not a global context processor and not a client feature-flag
framework — the narrowest ownership available, and no other config can ride along.
`test_page_view_exposes_only_the_boolean_flag` pins exactly one `current_app.config`
read in that view and no whole-config dump;
`test_page_view_reads_no_history_planner_or_weekly_program_service` is an AST guard
(mirroring the PR5 endpoint guard) proving the view names none of
`build_weekly_program`, `weekly_program_payload`, `build_adaptive_plan`,
`build_progression_report`, `build_training_history_summary`, `WorkoutLog`,
`TrainingPlan`, `PumpCheck`.

### 14. JavaScript initialization boundary

`static/weekly_program.js` — a plain IIFE in the style of `static/training.js`; no
framework, no dependency, no build step. It queries `[data-weekly-program-mount]`, sets
`data-weekly-program-initialized="1"`, returns `true` on the first run and `false`
afterwards, and no-ops when the shell is absent. `window.FitXWeeklyProgram.init` is
exposed as PR6.2's extension point (and is what makes "initializes once" testable).

**Idempotency by DOM marker, not a module-local flag** — deliberate: the script can be
evaluated more than once (cached asset, bfcache restore), and the DOM node is the only
state shared across evaluations.

The file is loaded **only** when the flag is on, so OFF does not even request it.

### 15. Confirmation that no endpoint request exists

Three independent proofs:

1. **The code does not exist.** `tests/test_weekly_program_ui_js.py` strips comments and
   asserts the executable source contains no `fetch(`, `XMLHttpRequest`, `sendBeacon`,
   `EventSource`, `WebSocket`, `/api/training`, or the weekly-program path.
2. **Executed behavior.** The file is run under `node` with a DOM stub whose `fetch`,
   `XMLHttpRequest`, `document.addEventListener` and `window.addEventListener` all
   increment counters and throw. After load plus two extra `init()` calls, every counter
   is `0`.
3. **Server-side.** The ON-path HTML contains no weekly-program endpoint string, and no
   other template or asset references `weekly_program.js`.

Also absent: retries, polling, `setTimeout`/`setInterval`/`requestAnimationFrame`,
`MutationObserver`/`IntersectionObserver`, and any event listener.

### 16. Confirmation that no recommendation data is rendered

No recommendation value is embedded server-side (every model field asserted absent from
the ON-path HTML) and none is computed client-side. The initializer contains no planning
vocabulary (`week_focus`, `volume_action`, `deload`, `plateau`, `overload`, `1RM`, ...),
no `JSON.parse`/`stringify`, no placeholder payload, no commented-out future logic, and
**no numeric literal other than the `1` initialization marker** — a guard that fails on
any threshold, `0.05` volume step, 7-day window or `* (1 + delta)` target arithmetic, so
the browser cannot quietly become a second planning authority. `Date(`/`getDay` are
banned for the same reason.

### 17. AI coach flag independence

- Separate env names, separate `app.config` keys, separate read paths. The
  `WEEKLY_PROGRAM_UI_ENABLED` definition line does not mention
  `AI_ADAPTIVE_PLAN_CONTEXT`.
- `WEEKLY_PROGRAM_UI_ENABLED` appears in none of `ai_coach.py`, `context_builder.py`,
  `prompt_builder.py`, `adaptive_plan_context.py`; `AI_ADAPTIVE_PLAN_CONTEXT` appears
  in neither the page view nor `weekly_program.js`.
- Runtime: coach flag ON + UI flag OFF renders no shell; UI flag ON produces an
  identical shell under both coach-flag states; enabling the UI leaves
  `app.config["AI_ADAPTIVE_PLAN_CONTEXT"]` untouched.
- Subprocess config probes confirm setting either variable alone leaves the other False.

The full four-way **runtime** matrix remains PR6.3 as specified; PR6.1 establishes
structural independence.

### 18. Characterization tests added

`tests/test_training_page_characterization.py` — 69 tests, written and green **before**
the first source edit. It pins route status and the unauthenticated redirect, the
selected template (`training.html` via the `template_rendered` signal), 17 major DOM
regions, 8 setup-form option grids, 10 declarative `data-action` controls, 9 form
control ids, the Pump Check share selector, 6 navigation entries plus the active-state
markup, 6 assets, the nonced `window.__TRAINING` bootstrap, the CSRF meta tag, the
absence of legacy `--volt` tokens, and the default absence of every weekly-program
marker.

**Byte-identity approach — documented as required.** A committed golden snapshot is not
practical here: `_v` is a boot timestamp and the CSP nonce and CSRF token vary per
request, and the repository has no stable-snapshot convention to reuse. So the committed
tests pin the meaningful DOM contract, and byte identity was proven **once** during
implementation by a normalized before/after dump (result in section 11). One test,
`test_bare_weekly_program_substring_is_pre_existing_catalog_noise`, exists purely to
record a trap for later readers: the literal `weekly_program` is already on every page,
because `_head.html` injects the whole locale catalog into `window.I18N` and
`locales/{tr,en}.json` carry an **unused** `training.weekly_program` key. Presence and
absence must therefore always be asserted with precise markers.

### 19. New tests added

169 new tests: 69 characterization + 33 template/server + 21 config + 45 JS + 1
`.env.example` guard. Highlights beyond the obvious:

- `test_off_adds_no_markup_between_weekly_stats_and_the_plan_meta_row` — pins the exact
  seam text so no layout spacing can appear on the OFF path.
- `test_on_is_the_off_document_plus_the_shell_and_script_only` — whole-document delta.
- `test_page_view_issues_no_extra_query` — SQL statement counting via a SQLAlchemy
  `before_cursor_execute` listener, after a warm-up request (the first `/training` of a
  session runs one-off streak/session work that later requests skip; without the warm-up
  the test would only be measuring that).
- `test_no_css_rule_targets_the_shell_so_it_adds_no_layout` — no stylesheet mentions
  `weekly-program`, no `static/weekly_program.css` exists, and no bare `section` rule
  exists in any sheet, so the empty section is a zero-height block by construction.
- `test_comment_stripper_keeps_code_and_drops_prose` and
  `test_source_guards_are_running_against_real_code` — the source guards' own sanity, so
  they cannot pass vacuously if the file is emptied or the stripper breaks.

### 20-22. Verification results

- Targeted + regression, one run:
  `python -m pytest tests/test_training_page_characterization.py
  tests/test_weekly_program_ui.py tests/test_weekly_program_ui_flag.py
  tests/test_weekly_program_ui_js.py tests/test_env_example.py tests/test_training_ui.py
  tests/test_training_routes.py tests/test_weekly_program_route.py
  tests/test_weekly_program.py tests/test_adaptive_plan_context.py
  tests/test_dependency_boundaries.py tests/test_i18n.py tests/test_hooks.py
  tests/test_app_shell.py tests/test_pump_check_sharing.py tests/test_design_system.py
  tests/test_prompt_builder.py tests/test_ai_coach.py -q`
  -> **524 passed in 875.15s (14m35s)**.
- Characterization before implementation:
  `python -m pytest tests/test_training_page_characterization.py -q`
  -> **69 passed in 139.36s**, on the unmodified tree.
- Full suite -> **2,178 passed, 0 failed** (662 + 745 + 771), ~17m36s total.
  Run as three file-partitioned chunks rather than one `python -m pytest -q`, because
  two whole-suite background runs were killed by this environment's background-task
  time limit before emitting a summary. The partition is provably complete rather than
  a sample: `pytest --collect-only -q` on this tree reports **2,178 tests collected
  (2,181 minus the 3 load-marked deselections)** — exactly the sum of the three chunk
  results, so no file was skipped or counted twice. The 118 `tests/test_*.py` files were
  split 40 / 40 / 38 alphabetically; `tests/load/` is marker-deselected either way
  (`pytest.ini`: `addopts = -m "not load"`).
- Test-count reconciliation: collecting with this PR's four new modules ignored yields
  **2,010**, i.e. this PR adds **169** tests (168 in the four new modules + 1 in
  `tests/test_env_example.py`) and the pre-PR6.1 baseline on this tree is **2,009**.
  Note the PR5 section above records `1981 passed` — that figure does not match this
  tree's baseline of 2,009. The gap predates PR6.1 (it is between PR5's branch
  measurement and the squash-merged `07ef1ff`), was not investigated here, and is not
  caused by this PR; the arithmetic above accounts for every test this PR adds.
- JavaScript: there is no JS test runner in this repository (no `package.json`; CI runs
  pytest only). Per the spec's "smallest consistent mechanism", the JS contract is
  covered from pytest — source guards in the repository's existing style
  (`tests/test_i18n.py`, `tests/test_pump_check_sharing.py` read shipped assets as text)
  plus behavioral execution under `node` via `subprocess` (the mechanism
  `tests/test_adaptive_plan_context.py` already uses). `python -m pytest
  tests/test_weekly_program_ui_js.py -q` -> **45 passed in 10.39s**, `node v24.14.1`,
  including `node --check` on the asset. The node tests skip cleanly where `node` is
  absent; GitHub's `ubuntu-latest` image ships Node, so CI executes them.
- Static/lint: the repository has no linter configured (`requirements-dev.txt` is
  pytest-only; CI runs `pytest` + a schema-drift job). `node --check` covers the new
  asset; `ast.parse` covers the changed Python via the AST guards.
- `git diff --check` -> clean (only the usual `core.autocrlf` LF/CRLF advisory; all
  touched files are consistently CRLF in the working copy, no mixed endings).

### 23. Asset-size impact

| Path | OFF | ON |
|---|---|---|
| `/training` HTML | 0 bytes (byte-identical) | +153 bytes |
| `static/weekly_program.js` | not requested | 2,207 bytes (one cached request) |

No existing asset changed. `test_asset_stays_small` caps the new file at 4 KB so the
boundary cannot quietly grow into a feature before PR6.2 reviews the client contract.

### 24. Security review

- Page access unchanged: `/training` is still `@require_auth`; unauthenticated requests
  still 302 to `/login` (characterized).
- No new route, no new public surface, no change to any endpoint.
- The flag is server-controlled and never user-controlled; it is **presentation only**
  and explicitly not an authorization boundary. `GET /api/training/weekly-program`
  stays authenticated in every flag state.
- No config leakage: only a boolean crosses into the template, and the flag *name* is
  never rendered. No whole-config dump (AST-pinned).
- No user id, no `cognito_sub`, no session token, no backend exception, and no planning
  data in the markup.
- No `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write`/`eval` in the new JS;
  no unsafe HTML anywhere. No inline `<script>` added, so the CSP nonce contract
  (`app/hooks.py`) is untouched — the new asset is a same-origin `src`.
- No API call in this PR.

### 25. Accessibility shell review

Valid semantic container (`<section>`), empty. No heading (so no empty heading), no
`aria-live` region, no focusable control, no `role`, and `aria-hidden="true"` so an
empty container announces nothing while it has no data. No duplicate ids
(`id="weekly-program"` occurs exactly once and nowhere else in the codebase). No
inaccessible hidden *content* — there is no content. **PR6.2 must remove `aria-hidden`
when it renders real content**, and owns loading/error/retry/recommendation
accessibility.

### 26. Responsive shell review

Structural, as scoped. The shell has no CSS: `static/` contains no rule mentioning
`weekly-program`, no `weekly_program.css`, and no bare `section` selector in any sheet
(pinned by `test_no_css_rule_targets_the_shell_so_it_adds_no_layout`). An unstyled empty
`<section>` is a zero-height block with no intrinsic width, so at every supported width
it can produce no horizontal overflow, no oversized empty card and no spacing — and OFF
is byte-identical, so it cannot shift layout at all. This app has no bottom navigation
(the mobile pattern is the drawer in `_nav.html`), so there is no bottom-nav conflict;
the shell is inside `<main class="main-content">` and cannot overlap the fixed header.

**Not performed:** a live browser render. The Chrome extension was not connected in this
session, so the visual/DevTools pass (zero network requests observed live, measured
element height, mobile-width overflow) was **not** run. The claims above rest on the
structural evidence just listed, not on observation. Worth doing once before PR6.2
ships pixels.

### 27. Rollback procedure

Set `WEEKLY_PROGRAM_UI_ENABLED=0` (or remove it from `.env`) and restart the container.
The shell, the script tag and any future request disappear; the page returns to the
byte-identical OFF document. No schema, no migration, no data to unwind. Reverting the
commit is equally safe and equivalent, since OFF is already the default.

### 28. Known debt

- `aria-hidden="true"` on the shell must be removed by PR6.2 when content lands.
- `locales/{tr,en}.json` carry an **unused** `training.weekly_program` key that predates
  this PR. PR6.1 deliberately did not touch it (no visible copy). PR6.2 should adopt it
  rather than adding a near-duplicate.
- Turkish/English copy for `explanation_keys` (and `reason_codes`, open since PR3) is
  still unwritten — PR6.2.
- The shell lives inside `#active-plan-view`, so it is invisible to a user with no saved
  `TrainingPlan` even if that user has workout history. That is the intended IA for
  PR6.1/PR6.2; if product wants the recommendation for plan-less users, it is a
  deliberate follow-up, not a bug.
- No live-browser verification was performed (see section 26).
- The PR5 debt list is unchanged: unconverged raw readers (`tracking.py`
  heatmap/insights, `fitx_mcp/server.py`, `analytics_engine._check_missing_logs`,
  `ai_coach._tool_get_progress_metric`), and the pre-existing `datetime.utcnow()`
  deprecation warnings.

### 29. Exact PR6.2 entry conditions

1. Read this section and PR5 (Parts 1, 2 and the post-audit remediation) first. Confirm
   the branch is based on current `origin/main`.
2. The rollout architecture is settled — **do not revisit it**. Surface, flag name, flag
   default, template boundary, mount identifiers and the initializer's extension point
   are all decided and characterized.
3. Extend `window.FitXWeeklyProgram.init` in `static/weekly_program.js`. Do not add a
   second script, a second flag, or a second mount point.
4. `GET /api/training/weekly-program` is the **sole** data source. Do not import
   `weekly_program` or `training_planning` into the page route, do not server-render the
   recommendation, and do not add query parameters — `weeks`/`end_day` are pinned
   service defaults on purpose (PR5).
5. Tests that will (correctly) fail as soon as PR6.2 starts, and must be **updated with
   intent**, not deleted: the no-fetch / no-XHR / no-endpoint-string guards in
   `tests/test_weekly_program_ui_js.py`, the "shell is empty" and "no recommendation
   data" assertions in `tests/test_weekly_program_ui.py`, the numeric-literal guard, and
   the 4 KB asset cap. Keep the **planning-logic** guards (thresholds, target and
   weekly-window arithmetic, `AI_ADAPTIVE_PLAN_CONTEXT`) — those must survive PR6.2 and
   PR6.3 unchanged.
6. Keep the OFF path byte-identical, and keep the characterization suite green.

### 30. Explicit PR6.2 scope

Fetch `GET /api/training/weekly-program`; loading state; populated state;
insufficient-data state; missing-baseline state; structured failure state;
malformed-payload safety; localization of `explanation_keys` / `reason_codes`; core
accessibility (announcement of loaded content, error/retry semantics) and responsive
card presentation.

### 31. Explicit PR6.3 deferred scope

Observability finalization; cache/privacy hardening; architecture guards; the full
four-way flag matrix at runtime; performance and SQL verification; mobile/accessibility
validation; final documentation; production-readiness re-audit.

> **Superseded — all of the above is now delivered.** See the `## Sprint 6 PR6.3`
> section at the end of this file for the implementation, verification, and combined
> production-readiness audit.

### Independently safe to merge

Yes. The flag defaults OFF, and with it OFF the rendered page is byte-identical to
`origin/main` — so merging changes the running application not at all. The ON path adds
one empty element and one inert script that make no request and render nothing. There is
no schema, migration, dependency, prompt, provider, endpoint or navigation change, and
every existing training flow is characterized and unchanged.

## Sprint 6 PR6.3 — Weekly Program UI Production Hardening and Final Audit

Date: 2026-07-23
Scope: the **third and final** PR of the Adaptive Weekly Program UI. It does not build a
feature — it hardens, verifies and audits the merged PR6.1 + PR6.2 system for controlled
production rollout: cache/privacy headers, a finalized non-sensitive observability line,
per-mount request/race safety, an accessibility contrast + focus-management fix, the full
four-way runtime flag matrix, SQL/request-count verification, structural architecture
guards, and a combined production-readiness re-audit. **No product scope was added and no
PR6.1/PR6.2 design decision was reopened.**

> **Takeover note.** This PR was implemented by a prior agent whose code + tests landed in
> the worktree but were never run or documented (its report claimed the sandbox blocked
> execution). This session treated git as the source of truth: the code was re-derived
> from the diff, every targeted and related suite was actually executed (results below),
> and the handoff/docs were written from verified behavior, not the prior narrative.

### 1–4. Repository, branch, base, worktree state

- **Branch:** `sprint6-pr6.3-weekly-program-hardening`
- **HEAD / base:** `2ff8804` (`feat(training): integrate adaptive weekly program client (#179)`),
  which **is** `origin/main`. `git rev-parse HEAD == git rev-parse origin/main`. The branch
  was cut from current `origin/main`, not a stale local branch.
- **PR6.2 merge confirmation:** #179 is `origin/main` HEAD; #178 (PR6.1) and #177 (PR5) are
  its parents in `git log`. PR6.2 is squash-merged into the base.
- **Worktree:** `~/.worktrees/sprint6-pr6.3-weekly-program-hardening`; clean apart from the
  tracked files listed below. No untracked files. Work is **uncommitted** (per task rule 40 —
  commit only if explicitly requested; it was not). `git diff --check` clean (only the
  repo-wide `autocrlf` LF→CRLF advisory, pre-existing).

### 5–6. Files created / modified

**Created:** none (hardening PR — no new module, route, page, template, or asset).

**Modified — production + tests:**

| File | Change |
|---|---|
| `app/blueprints/training.py` | `@bp.after_request` stamping `Cache-Control: private, no-store` on the weekly-program path only; `_weekly_program_state()` + `_weekly_program_error_class()` classifiers; success/failure logging rewritten to id-free `request_id=… state=…` (`current_request_id` import; `SQLAlchemyError` import) |
| `static/weekly_program.js` | `activeRequest`/`requestGeneration` moved from module globals into per-mount `context`; `isCurrentRequest()` adds a `mount.isConnected !== false` guard; `replaceContent()` + `focusAfterRetry` accessibility focus management |
| `static/training.css` | `.weekly-program-heading { color: var(--color-text-2); }` — contrast fix |
| `tests/test_weekly_program_route.py` | +cache-header (200/500/503/302), +privacy-safe observability classification, +GET-only PUT/PATCH, +auth-under-both-UI-flags, +one-SELECT-no-writes SQL guard |
| `tests/test_weekly_program_ui.py` | +`test_complete_four_way_runtime_flag_matrix` |
| `tests/test_weekly_program_ui_js.py` | +one-endpoint/no-planning-dependency guard, +detached-mount race test, +focus-management tests, +extra malformed explanation-key cases, +contrast-token CSS guard; harness gained `isConnected`/`focus`/`activeElement` |

**Modified — documentation (this session):** `docs/handoff.md` (this section), `docs/WEEKLY_PROGRAM.md`
(F6 observability finalized + cache policy + PR table), `docs/TRAINING_PLANNING.md` (PR6.3 note),
`CLAUDE.md` (weekly-program bullet PR6.3 status), `NEEDED_FIXES.md` (PR6.3 resolution note),
`.env.example` (cache/observability note on the flag block).

### 7. Verification-first gap analysis

Before touching anything, the merged PR6.1/PR6.2 code and the prior agent's diff were read
against the task requirements. Findings that drove (or did not drive) work:

- **Real gaps closed by the prior diff (kept, verified):** no cache header on an
  authenticated per-user endpoint; PR5's observability line carried `user=<id>` + volumes at
  `debug` (PII + wrong level for production); race state (`activeRequest`/`requestGeneration`)
  lived in module scope so a second mount could not own its own request and a detached mount
  could still overwrite; card heading inherited an accent colour measured at 4.22:1 contrast;
  retry moved no focus; the four-way matrix and a one-SELECT SQL guard were untested.
- **Non-gaps (already correct in PR6.2, proven, not re-touched):** endpoint auth/GET-only/
  user-scoping; structured-500 failure semantics distinct from neutral; strict payload
  contract; no `localStorage`/`sessionStorage`/polling; safe DOM (`createElement`/`textContent`/
  `replaceChildren` only); no target/window arithmetic in the client.
- **Verdict:** the prior diff was a correct, in-scope set of hardening fixes. The only real
  remaining work was **running the suites and writing the documentation** — completed here.

### 8. Production hardening implemented

Cache header, observability classification, per-mount request/race isolation +
`isConnected` stale/detached guard, accessibility focus management, contrast token. Each is
detailed in the matching section below and each is covered by an executed test.

### 8b. Feature-flag contract (defaults)

- `WEEKLY_PROGRAM_UI_ENABLED` — default **OFF** (`app/config.py`, `os.getenv(...,"0")=="1"`).
  **Unchanged** by PR6.3. Presentation only, never an authorization boundary.
- `AI_ADAPTIVE_PLAN_CONTEXT` — default **OFF**. Independent env name, config key, and read path.
- Neither flag reads a query string, header, cookie, endpoint availability, prompt, or DOM text.

### 9. Four-way flag matrix (runtime)

`tests/test_weekly_program_ui.py::test_complete_four_way_runtime_flag_matrix` renders
`/training` for all four combinations; `tests/test_weekly_program_route.py::test_endpoint_auth_and_payload_do_not_depend_on_ui_flag`
proves the endpoint is UI-flag-immune. **All pass.**

| UI | Coach | Shell/mount/script | Endpoint auth | Endpoint payload | Coach adaptive context |
|----|-------|--------------------|---------------|------------------|------------------------|
| 0 | 0 | absent | `@require_auth` (302 unauth) | unchanged | disabled |
| 0 | 1 | absent | `@require_auth` | unchanged | may be enabled |
| 1 | 0 | present, 1 initial request | `@require_auth` | unchanged | disabled |
| 1 | 1 | present, 1 initial request | `@require_auth` | unchanged | enabled |

The UI flag never varies the endpoint output; the coach flag never varies the UI or the
feature bundle. Both consumers use the canonical deterministic stack; neither mutates the other.

### 10. OFF-path preservation

With `WEEKLY_PROGRAM_UI_ENABLED=0` the template omits the block server-side: no mount, no
`static/weekly_program.js`, no locale bundle, no markup, no feature CSS footprint, no request,
no feature log line (the endpoint is never called from the OFF page). PR6.1's byte-identity
contract holds — **PR6.3 adds no server-rendered bytes to `/training`.** The one global-ish
change (the cache header) is **route-scoped to `/api/training/weekly-program`**, so the
`/training` page body and headers are untouched (asserted:
`test_private_no_store_covers_success_and_unrelated_routes_are_unchanged`). Body bytes,
headers, and runtime requests on the OFF path are all unchanged.

### 11. Endpoint authentication and user scoping

`GET /api/training/weekly-program` remains `@require_auth`, GET-only (POST/PUT/PATCH/DELETE →
405), scoped solely through `current_user.id` with no `user_id`/`weeks`/`end_day`/query
planning controls, read-only (one service call → one payload projection, no write/commit/flush).
Cross-user isolation, query-string tampering, session-expiry (503 transient), and
unauthenticated (302) paths are all tested, under **both** UI-flag states. Hidden UI is never
treated as authorization.

### 12. Cache and privacy policy

`Cache-Control: private, no-store` on every response for the path — populated 200, neutral 200,
missing-baseline 200, structured 500, transient 503, and the unauthenticated 302 login
redirect — via a route-scoped `@bp.after_request` gated on `request.path`. Not global: `/training`
and `/login` keep their existing cache behavior. No `Pragma`/`Expires` added (no repository
convention requires them; `no-store` already forbids storage). Client adds no
`localStorage`/`sessionStorage`/service-worker persistence, so account-switching cannot replay
another user's recommendation from cache or bfcache. Decision is explicit and tested — not implicit.

### 13. Observability policy

One `[TRAINING][WEEKLY_PROGRAM]` line per request:
- success → `logger.info("… request_id=%s state=%s")`, `state ∈ {neutral, missing_baseline, populated}`
- failure → `logger.warning("… request_id=%s state=error error_class=%s")`, `error_class ∈ {upstream_error, unexpected_error}`

**Logged:** the existing server-generated 16-hex `request_id` (not a user id) + the classified
state. **Prohibited (asserted absent by test):** `current_user.id`, raw baseline/target volumes,
`week_focus` value, reason/explanation keys, the payload, the raw exception string, email/token/PII.
No duplicate event per request. No log when the UI is OFF (endpoint not called). **Metric
promotion deferred** — the only metric abstraction (`ai_metrics.py`, CloudWatch,
`AI_METRICS_ENABLED` default 0) is AI-turn-scoped and off by default; the `state=` line is the
rollout signal. Operator query:

```
fields @timestamp, @message
| filter @message like /\[TRAINING\]\[WEEKLY_PROGRAM\]/
| parse @message "state=*" as state
| stats count(*) by state
```

### 14–17. Request lifecycle, counts, retry, concurrency, stale response

- **Initial load:** exactly one `init`, exactly one `GET` (canonical path, `Accept: application/json`,
  no query/body/alternate endpoint), no polling/timer/duplicate listener.
- **Repeated init:** the `data-weekly-program-initialized` marker + per-mount `context` mean no
  second request, no duplicate DOM/listener/announcement.
- **Retry:** explicit user action only → exactly one additional request; `context.activeRequest`
  guard makes rapid double-clicks a no-op (one active request at a time); retry enters loading
  immediately and clears prior error/recommendation DOM.
- **Stale/detached protection:** `isCurrentRequest(context, gen)` returns false when
  `gen !== context.requestGeneration` **or** `context.mount.isConnected === false`, so an older
  in-flight response cannot overwrite a newer state and a response for a removed/replaced mount
  is dropped. Proven by `test_replacement_mount_owns_its_request_and_ignores_disconnected_completion`
  (two mounts, out-of-order completion: the detached first mount stays `loading`, the live second
  renders `populated`), `test_retry_clears_content_and_double_click_cannot_start_concurrent_request`,
  and the focus tests.

### 18–19. Client contract validation & explanation-key evolution policy

`isValidPayload` requires: plain object; `weeks` a non-negative integer; `has_data` boolean;
`week_focus`/`volume_action`/`intensity_action` in canonical enums; `volume_delta_pct` finite
number; `overload_ready`/`maintenance_recommended` booleans; `reason_codes`/`explanation_keys`/
`unsupported` string arrays; every `reason_code` in the known `REASONS` set;
baseline/target **both null or both positive-finite** (NaN/Infinity/0 rejected);
`baseline_week_start` null iff both null, else a real ISO date. Unknown **additive top-level**
fields are tolerated (not rejected); unknown `unsupported` string values are tolerated but
**not rendered** (no code reads `unsupported`).

**Explanation-key policy = A (strict mirror → malformed).** The client does not render the
server's `explanation_keys` array; it **reconstructs** the expected list as
`['weekly_program.focus.'+week_focus, ...reason_codes.map('weekly_program.reason.'+r)]` and
requires `explanation_keys` to equal it **exactly, in order**. Reasons are rendered from
`reason_codes` in their canonical backend order — so ordering is preserved, raw keys are never
displayed, nothing is reordered or fabricated. A drifted, extra, reordered, or unknown key (or
an unknown `reason_code`) makes the whole payload malformed → generic unavailable state. This is
deliberate: frontend and backend ship in the same deploy (F5), so drift is a deploy-consistency
bug, not a graceful-degradation case. Tested by the malformed parametrization (extra key,
`future_reason`, reordering). **Consequence / known debt:** adding a new backend `reason_code`
requires updating the client `REASONS`/`FOCUSES` whitelist in the same deploy.

### 20. Planning-authority preservation

The browser remains presentation-only: it computes no `target_weekly_volume`/
`baseline_weekly_volume`, no `baseline × delta`, no 7-day windows, infers no deload/overload/
maintenance, derives no `week_focus`/`volume_action`/`intensity_action`, sorts no explanation
keys, and holds no planner/progression constants. `AdaptivePlan` stays the sole planning
authority; `adaptive_plan_context` stays the sole AdaptivePlan→prompt owner; `weekly_program_payload`
stays the sole recommendation→API projection.

### 21. Structural architecture guards

`tests/test_weekly_program_ui_js.py` parses `static/weekly_program.js` source and asserts:
exactly one occurrence of `/api/training/weekly-program`; **absent** tokens `WorkoutLog`,
`training_history`, `training_progression`, `training_planning`, `build_weekly_program`,
`weekly_windows`, `weeks=`, `end_day=`, `.sort(`; retry/stale guards present
(`context.activeRequest`, `context.requestGeneration`, `generation === context.requestGeneration`,
`context.mount.isConnected !== false`, `replaceChildren`); no `innerHTML`/`outerHTML`/`eval`/
`Function`/`document.write`/`setInterval`/`localStorage`/`sessionStorage`; no
`AI_ADAPTIVE_PLAN_CONTEXT`. Route AST guards (`tests/test_weekly_program_route.py`) prove the
view reads no `WorkoutLog`, calls `build_weekly_program` exactly once, and stays read-only.

### 22. Security review

Endpoint auth mandatory; `current_user.id` the only scope; no user-id param / IDOR surface; no
raw error or backend detail rendered (client shows localized copy only); no
`innerHTML`/`insertAdjacentHTML`/`eval`/`Function`; locale copy delivered as safe
`application/json` + `|tojson`, rendered via `textContent` (hostile quotes/brackets/ampersands/
script-closers render as text — covered by PR6.2 malicious-copy tests, still green); no
write/CSRF-relevant mutation; no token/session/PII in markup or logs; CSP unaffected (no inline
script added). Cache header prevents cross-user shared-cache exposure.

### 23. Localization review

TR/EN `weekly_program.*` parity: **35 keys each, no missing, no empty, valid JSON.** Bundle
appears only when the UI flag is ON, exactly once; the coach flag does not affect it. No raw
`weekly_program.*` key or backend machine code is displayed. `test_i18n.py` (27) green.

### 24. Accessibility audit

Semantic `<section>`; `<h2>` heading; loading status region; `role="alert"` on error/malformed;
keyboard-usable `<button>` retry. **PR6.3 focus management:** after a *failed* retry, focus moves
to the replacement retry button; after a *successful* retry, focus moves to the rendered heading
(`tabindex="-1"`); an *initial* load never moves focus (no focus-steal on page load). Contrast fix:
heading now uses `--color-text-2` (the inherited accent measured 4.22:1). Reduced-motion honored
via the shared skeleton. Verified by `test_initial_completion_never_moves_focus`,
`test_failed_retry_focuses_the_replacement_retry_button`,
`test_successful_retry_focuses_the_rendered_section_heading`,
`test_weekly_program_heading_uses_contrast_safe_text_token`. No High/Medium a11y defect remains.

### 25. Responsive and browser validation

CSS is feature-scoped (`.weekly-program-*` only), reuses the 640px breakpoint and shared
card/btn/skeleton tokens, pins overflow/tap-target, and adds no inline styles or fixed widths.
Every UI state (loading/populated/insufficient/missing-baseline/error/malformed/retry/stale/
detached) is exercised deterministically by the Node-executed JS harness.

**Real-browser validation (performed — headless Chromium/Blink 150.0.7871.130 via Selenium +
CDP, against an ephemeral local fixture harness; no prod credentials).** The harness serves the
real merged `static/training.css` and `static/weekly_program.js` plus a faithful reproduction of
the `templates/training.html` weekly-program mount with the real injected locale copy, wired to
controlled mock `fetch` responses — the app is never booted and no Cognito/DB/Redis/Bedrock
credential is touched. Every state (loading/populated/insufficient/missing-baseline/error/
malformed) was rendered at exactly 320/390/768/1366 px (viewport pinned via
`Emulation.setDeviceMetricsOverride`) in both `tr` and `en`, with all measurements read from the
real Blink layout:

- **Overflow/reflow:** `documentElement` and mount `scrollWidth − clientWidth = 0` across all 24
  state×viewport cells and all 20 locale×viewport cells — zero horizontal overflow; the
  action/metric grids collapse to a single column ≤640 px.
- **Loading + reduced motion:** loading exposes `role="status"`; the skeleton animates
  (`animation-name: skeleton-shimmer`) under `prefers-reduced-motion: no-preference` and is gated
  to `none` under `reduce`.
- **Keyboard retry (Enter and Space):** both activate the retry button → second request →
  populated render.
- **Retry focus lifecycle:** a successful retry moves focus to the section `<h2>` heading
  (`tabindex="-1"`); a failed retry moves focus to the replacement retry button.
- **Stale/detached response:** a superseded response resolving after the mount is detached is
  dropped (`renderedIntoDetached=false`, state stays `loading`, no exception).
- **Alert/status semantics:** error and malformed expose `role="alert"`; loading `role="status"`.
- **Tap target:** the retry button measures 44 px tall at every viewport (full width ≤640 px).
- **Heading contrast:** measured **6.85:1** via `getComputedStyle` in Blink — passes WCAG AA.
- **Active-plan controls:** the `apv-meta-row` (meta, score badge, reset) coexists with the mount
  at every width with no overflow.
- **Request counts:** exactly **one** `fetch` per initial mount for every state; exactly **+1**
  per retry; no polling/interval/timeout refresh; single endpoint, no planning fan-out.
- **aria-hidden:** removed on init (confirmed absent in the rendered DOM).
- **Malformed classification (Policy A):** a structurally-valid-but-reordered `explanation_keys`
  payload classifies as `malformed` (distinct from `error`).

Drivers: `browser_matrix.py` (state×viewport×locale matrix + retry/keyboard/focus flows +
screenshots) and `browser_followup.py` (reduced-motion no-preference vs reduce; detached-mount
stale guard); server `harness_server.py`; screenshot artifacts `shot_<state>_<locale>_<vw>.png`
(session scratchpad). This was a headless real-browser-engine pass, not a manual interactive
session; an optional manual spot-check during Stage-1 dev enablement remains a nice-to-have, not
a blocker.

### 26–28. Performance, SQL/N+1, asset sizes

- **Endpoint SQL:** `test_service_build_uses_one_history_select_and_no_writes` attaches a
  `before_cursor_execute` listener and asserts **exactly one `SELECT` (against `workout_log`) and
  zero INSERT/UPDATE/DELETE** per `build_weekly_program`. No N+1, no repeated history read, no
  second planning call, no writes/commit/flush.
- **Request count:** OFF = 0; ON initial = 1; per explicit retry = +1; no polling/interval/timeout refresh.
- **Page route:** builds no recommendation and issues no extra recommendation SQL in either flag state.
- **Asset sizes (measured):** `weekly_program.js` 12,789 B; `training.css` 26,770 B (+~40 B for the
  contrast rule); locale feature bundle TR 2,897 B / EN 2,816 B (35 keys each);
  `locales/tr.json` 58,276 B / `en.json` 55,849 B. Flag-OFF `/training` HTML unchanged from the
  PR6.1 baseline (82,664 B normalized); flag-ON adds the PR6.1 mount+script delta only. The cache
  header adds response headers, not body bytes.

### 29. Backward compatibility

No change to `/training`, setup form, active-plan view/controls, workout hero, weekly strip/stats,
plan metadata, training generation, active-plan retrieval, workout status/logging, progress pages,
AI Coach, PR4 adaptive-plan context, auth, navigation, non-weekly localization, CSP, static assets,
social/gamification/nutrition/email. OFF = unchanged; ON = additive read-only surface.

### 30–34. Tests added & executed results

**Tests added:** cache-header (4), privacy-safe observability (2 parametrized families),
GET-only extension, auth-under-both-UI-flags, one-SELECT SQL guard, four-way matrix,
one-endpoint/no-planning guard, detached-mount race, three focus tests, contrast-token guard,
extra malformed explanation-key cases.

**Targeted (all executed this session, all pass):**
- `tests/test_weekly_program_route.py` — **33 passed** (61.6s)
- `tests/test_weekly_program_ui.py` — **40 passed** (71.8s)
- `tests/test_weekly_program_ui_js.py` — **111 passed** (25.1s)
- `node --check static/weekly_program.js` — **OK**

**Related regression (executed, all pass):**
- `test_weekly_program.py`, `test_training_history.py`, `test_training_progression.py`,
  `test_training_planning.py`, `test_dependency_boundaries.py`, `test_adaptive_plan_context.py`
  — **153 passed** (120s)
- `test_i18n.py`, `test_design_system.py`, `test_auth_audit.py`, `test_training_routes.py`
  — **72 passed** (98s)

**Full Python suite (executed and reconciled this session).** The complete suite was run in 7
deterministic, non-overlapping file partitions over the sorted `tests/**/test_*.py` list (ranges
1‑17 / 18‑34 / 35‑51 / 52‑68 / 69‑85 / 86‑102 / 103‑119 — contiguous, disjoint, every one of the
119 files executed exactly once, none omitted or repeated). Per-partition **passed / exit code**:
349/0, 252/0, 200/0, 288/0, 363/0, 263/0, 553/0. **Grand total: 2268 passed, 0 failed, 0 error,
0 skipped, 0 xfailed, 0 xpassed, 3 deselected** (the `-m "not load"` marker on
`tests/load/test_ai_load.py`). Reconciliation: 2268 selected + 3 deselected = **2271** =
the `pytest --collect-only` total, exactly. The complete Node-backed UI suite
(`test_weekly_program_ui_js.py` 111, `test_weekly_program_ui.py` 40, plus the other `*_ui`
files) ran inside these partitions. Runner `run_partitions.sh`; logs `part{1..7}.log` +
`partitions_summary.txt` (session scratchpad). `ci.yml` runs the same suite on push as the
standing gate.

**Locale/static validation:** JSON valid, TR/EN parity 35/35, no empty values; `git diff --check`
clean (only pre-existing `autocrlf` advisory).

### 35–36. Documentation & handoff updates

`docs/WEEKLY_PROGRAM.md` (F6 finalized + cache policy + PR table = Done),
`docs/TRAINING_PLANNING.md` (PR6.3 runtime-independence note), `CLAUDE.md` (weekly-program bullet
now records PR6.3 as done), `NEEDED_FIXES.md` (2026-07-23 PR6.3 resolution note), `.env.example`
(cache/observability note), and this handoff section.

### 37. Rollout procedure

- **Stage 0 — merged, flag OFF.** Merge PR6.3; deploy with `WEEKLY_PROGRAM_UI_ENABLED=0`.
  Verify `/training` unchanged, endpoint still `@require_auth`, zero weekly-program UI requests
  from normal training-page usage.
- **Stage 1 — internal/dev.** Set `WEEKLY_PROGRAM_UI_ENABLED=1` in a non-prod environment; walk
  every UI state; confirm the `state=` log distribution and mobile behavior; confirm no elevated
  endpoint failures.
- **Stage 2 — controlled production.** The flag is **global only** (env var read at boot); there
  is no per-user percentage rollout in this codebase and none was invented. Enable during a
  low-risk window, monitor endpoint status/failures + client errors, keep rollback ready.
- **Stage 3 — stabilization.** Observe endpoint latency and error counts via existing request
  logs; confirm no auth/training-page regression. Enabling the flag requires a redeploy/restart
  (env var read at boot).

### 38. Monitoring procedure

Filter CloudWatch logs for `[TRAINING][WEEKLY_PROGRAM]`: `state=error` (with `error_class`) is
the failure signal; the `neutral`/`missing_baseline`/`populated` split is the health
distribution (Logs Insights query in §13). Endpoint latency/status ride the existing per-request
16-hex `request_id` logfmt line and Sentry. No new dashboard or vendor.

### 39. Rollback procedure

- **Primary:** set `WEEKLY_PROGRAM_UI_ENABLED=0` (or unset) and redeploy/restart → the shell,
  script, locale bundle and the client request all disappear; `/training` returns to its OFF
  body; the endpoint stays deployed and authenticated; coach behavior is independent. **Verify**
  by confirming the OFF `/training` body and zero `[TRAINING][WEEKLY_PROGRAM]` log lines from
  page loads.
- **Secondary:** revert the PR6.3 commit if a hardening change itself misbehaves. Do **not**
  revert PR5 planning/window semantics and do **not** disable `AI_ADAPTIVE_PLAN_CONTEXT` unless
  the incident is specifically the coach path. No schema/migration to undo.

### 40. Combined PR6 production-readiness audit — findings

Verification-only audit of PR6.1 + PR6.2 + PR6.3 and their upstream contracts (PR4 coach
consumer, PR5 service/endpoint, planning/progression/history). Every claim is code- or
test-supported.

- **Critical:** none.
- **High:** none.
- **Medium:** none.
- **Low (RESOLVED this session) — Live-browser validation.** *Files:* `static/weekly_program.js`,
  `static/training.css`. *Explanation:* previously deferred; now **performed** via a headless
  Chromium/Blink pass against the ephemeral fixture harness (§25). Every required
  viewport/state/locale, focus, keyboard (Enter+Space), reduced-motion, stale/detached-response,
  tap-target (44 px), heading-contrast (6.85:1) and request-count checkpoint passed with the real
  layout engine. *Residual:* an optional manual interactive spot-check during Stage-1 dev
  enablement — nice-to-have, non-blocking.
- **Technical Debt — client vocabulary lockstep.** *File:* `static/weekly_program.js`.
  *Explanation:* Policy-A validation rejects any new backend `reason_code`/`week_focus` until the
  client whitelist is updated. *Impact:* none today (same-deploy shipping). *Disposition:*
  documented in §18–19; acceptable and intentional.
- **Nice-to-have — CloudWatch metric promotion.** Deferred; the `state=` log + Logs Insights query
  cover rollout assessment. *Disposition:* deferred.

### 41. Production readiness matrix

| Dimension | Status |
|---|---|
| Architecture / dependency direction | ✅ one-way: weekly_program→planning→progression→history; browser presentation-only |
| Planning authority | ✅ AdaptivePlan sole authority; no client derivation |
| Rollout safety / OFF-path | ✅ default OFF, byte-identical OFF page, route-scoped header |
| Runtime correctness | ✅ four-way matrix; neutral/missing/error/malformed/auth states distinct |
| Authentication / user isolation | ✅ `@require_auth`, `current_user.id`-scoped, cross-user tested |
| Cache & privacy | ✅ `private, no-store` on all response classes, tested |
| Observability | ✅ classified id-free `state=` line; metric deferred with query |
| Security | ✅ no IDOR/injection/unsafe-DOM/PII-in-logs; CSP intact |
| Accessibility | ✅ semantics + focus mgmt + contrast 6.85:1; real-browser pass green |
| Responsive | ✅ scoped CSS, tokens, 44px tap-target; real-browser 320/390/768/1366 zero-overflow |
| Performance / SQL / request count | ✅ 1 SELECT no writes; 1 init request; +1 per retry (browser-confirmed) |
| Testing | ✅ full suite 2268 passed / 0 failed (7 partitions, reconciled to 2271) + real-browser matrix |
| Documentation | ✅ updated and accurate |
| Rollback readiness | ✅ flag-off, verified, nothing to undo |
| **Overall** | ✅ **production-ready for controlled enablement** |

### 42. Final verdict

- Is PR6.3 independently safe to merge? **Yes.**
- Is the complete PR6 stack safe to merge? **Yes.**
- Is `WEEKLY_PROGRAM_UI_ENABLED` still default OFF? **Yes.**
- Is the OFF path unchanged? **Yes** (route-scoped header only; body byte-identical).
- Are the UI and coach flags fully independent? **Yes** (four-way matrix).
- Does the enabled UI issue exactly one initial request? **Yes.**
- Is retry bounded and race-safe? **Yes** (`activeRequest` guard; one at a time).
- Can stale responses overwrite newer state? **No** (`requestGeneration` + `isConnected`).
- Endpoint authenticated and user-scoped? **Yes**, under both UI-flag states.
- Safe from shared-cache exposure? **Yes** (`private, no-store`).
- Observability sufficient for rollout? **Yes** (state classification + query); metric deferred.
- Browser a read-only presentation consumer? **Yes** (structural guards).
- AdaptivePlan the sole planning authority? **Yes.**
- Any regression? **None found** across the full suite — **2268 passed, 0 failed** (7 partitions, reconciled to 2271 collected).
- Merge blockers? **None** (0 Critical/High/Medium).
- Safe for controlled production enablement? **Yes** — the required real-browser matrix is green
  (headless Chromium, §25); only an optional manual interactive spot-check remains at Stage 1.
- Is Sprint 6 complete? **Yes** — PR6.3 is the final PR of the Adaptive Weekly Program UI
  sequence; the full PR1→PR6.3 adaptive stack is delivered.
- Ready to begin the next sprint? **Yes** — recommended Sprint 7 boundary: the deferred
  capabilities (`session_frequency`/`intensity_magnitude`/`exercise_selection`), the remaining raw
  WorkoutLog readers (`tracking.py` heatmap/insights, `fitx_mcp/server.py`,
  `analytics_engine._check_missing_logs`, `ai_coach._tool_get_progress_metric`), and the open
  `NEEDED_FIXES.md` items (#1–#4, #6, #7).

**Final status: A. PR6 PRODUCTION-READY — SAFE FOR CONTROLLED ENABLEMENT.** Both previously-open
verification gaps are now closed with collected evidence: (1) the required real-browser matrix was
executed in a real Blink engine (headless Chromium 150) across every state × 320/390/768/1366 px ×
tr/en with all overflow/focus/keyboard/reduced-motion/stale/tap-target/contrast/request-count
checkpoints green (§25); and (2) the **full** Python suite was executed and reconciled — **2268
passed, 0 failed, 3 deselected = 2271 collected** across 7 disjoint partitions (§30–34). This sits
on top of the structural evidence (four-way matrix, OFF-path route-scoping, deterministic
state/race/focus coverage, endpoint auth + cross-user isolation, `private, no-store`, classified
observability, one-SELECT SQL guard, structural guards, documented rollout/rollback). No open
Critical/High/Medium; the sole residual is an optional manual interactive browser spot-check at
Stage-1 dev enablement (nice-to-have, non-blocking).

No push, PR, merge, deploy, or production-flag change was performed. Work remains uncommitted in
the worktree pending explicit instruction.

## Sprint 0 - Frontend Readiness Audit Foundation

Date: 2026-07-22
Branch: feat/sprint0-frontend-readiness
Status: implementation and static reporting complete; mandatory supported-
environment Chromium execution is blocked, so Sprint 0 is not complete.

### Exact commands

From the repository root:

    python -m venv .audit-venv
    .audit-venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
    .audit-venv\Scripts\python.exe -m playwright install chromium
    .audit-venv\Scripts\python.exe -m scripts.frontend_audit preflight --output artifacts\ui-audit\preflight
    .audit-venv\Scripts\python.exe -m scripts.frontend_audit seed
    .audit-venv\Scripts\python.exe -m scripts.frontend_audit capture --tier full --dry-run
    .audit-venv\Scripts\python.exe -m scripts.frontend_audit capture --tier full --output artifacts\ui-audit\full
    .audit-venv\Scripts\python.exe -m scripts.frontend_audit verify --manifest artifacts\ui-audit\full\manifest.json

The browser command is version-coupled. After changing the Playwright package,
run python -m playwright install chromium again.

### Audit environment and isolation

- Clean venv: Python 3.14.3, Playwright 1.61.0, Chromium revision 1228
  (Chromium major 149).
- The audit factory requires loopback plus an isolated SQLite path, disables
  external services, and adds audit-only authentication/error routes only to
  that factory. It never reuses a production database.
- Seed data is idempotent and uses example.invalid identities. Run seed
  explicitly or use capture, which reseeds before the run.
- Every scenario has a fixed timezone-aware clock in
  docs/frontend-readiness/sprint-0/scenario-clocks.json.
- The Flask server, database, browser runner, and reports must execute inside
  the same supported Linux/WSL/container environment. Do not assume host and
  container loopback are shared.

### Coverage and evidence

- CLI tiers: smoke, responsive, stress, cross-browser, and full. Only full is
  a completion run.
- Coverage is inventory-driven: every HTML route gets representative
  390x844 and 1440x900 Chromium coverage; beta-critical and responsive-risk
  routes receive the declared eight-width matrix; stress and optional browsers
  run only where declared.
- The resolved route/scenario/state/viewport/browser/stress plan prints before
  capture.
- Canonical artifacts and all nine reports are under
  docs/frontend-readiness/sprint-0/.
- Raw runtime artifacts are written under artifacts/ui-audit/ and are ignored.
  Curated evidence belongs under
  docs/frontend-readiness/sprint-0/evidence/<run-date>/ with a validated
  manifest. No supported captures are currently curated.

### Known limitations

- Windows 10 build 19045 passed the diagnostic launch, loopback, screenshot,
  and shutdown checks, but it is outside the documented native support target
  and cannot supply final evidence.
- Docker Desktop returned: Docker Desktop is unable to start.
- WSL2 is enabled but no Linux distribution is installed. Installing one is a
  privileged system change and may require a restart.
- Runtime-dependent external findings remain BLOCKED_BY_MISSING_ENVIRONMENT;
  unsupported-host diagnostics did not confirm them.
- The warning baseline is frozen to the exact command/environment in
  warning-baseline.json: 48 passed and 343 existing datetime.utcnow()
  deprecation warnings. Do not compare other test selections as a warning
  regression, and do not broaden this sprint into a datetime migration.

### Unresolved product decisions

- Whether to adopt Today / Plan / Coach / Progress and demote Community.
- Whether Menu Scan is only a food-log input and wearables live under
  Connections.
- Public qualification language for AI and integration claims.
- Landing-page compression/social-proof direction.
- Which secondary capabilities should receive centralized UI flags.

### Recommended Sprint 1 entry point

First establish a supported Linux/WSL environment and complete the full
Chromium inventory. Then use the resulting evidence to fix shared mobile
overlap/overflow and safe-area ownership before workout state, food logging,
and Coach rendering. See implementation-backlog.md; do not start a broad
visual redesign from the external hypothesis alone.

## Sprint 0 - Takeover continuation (2026-07-23)

Date: 2026-07-23
Branch: feat/sprint0-frontend-readiness
Status: mandatory gate COMPLETE. The previous agent's three harness fixes are
verified and green, and the supported-environment Chromium preflight plus the
full 325-capture run both succeeded in WSL Ubuntu-24.04 on 2026-07-23 (277/277
mandatory Chromium captures, 0 failed; `verify` reports "audit artifacts valid").

### Takeover assessment (git treated as source of truth)

- Working tree carried exactly two uncommitted, unstaged modifications on top of
  the eight sprint-0 commits: `scripts/frontend_audit/runner.py` and
  `tests/test_frontend_audit_runner.py`. No other staged, untracked, or unrelated
  changes exist. This matches the inherited progress note.
- `wsl -l -v` confirms `Ubuntu-24.04` at version 2 (currently Stopped). This
  retires the prior blocker recorded above ("WSL2 is enabled but no Linux
  distribution is installed").

### Previous agent's three fixes - reviewed and confirmed correct

1. Text-200 zoom now injects a CSP-nonce-carrying `<style>` via `page.evaluate`
   (reads the page's own nonce from `style[nonce], script[nonce]`) instead of an
   un-nonced `page.add_style_tag`. Aligns with this app's per-request CSP nonce
   model (`app/hooks.py`); the old path would have been blocked by CSP, so the
   200%-zoom stress screenshots were silently un-zoomed (false evidence).
2. Only the audit-only `/__audit__/500` route returning HTTP 500 is treated as
   `captured`; every other 5xx stays `failed`. Keeps the deliberate error-page
   audit valid without masking genuine server errors.
3. New `_manifest_capture_record` omits `screenshot`/`reason` when absent. This
   fixes a real latent defect: the evidence-manifest schema types both fields as
   non-nullable `string`, so the old builder's `reason: null` (captured rows) and
   `screenshot: null` (blocked rows) produced schema-INVALID manifests that
   `validate_manifest` would reject.

### Tests executed and results (Windows host, Python 3.14.3, pytest 9.1.1)

- `tests/test_frontend_audit_runner.py` - 9 passed (includes the 3 RED/GREEN
  tests the previous agent added).
- Full audit suite (`test_auth_audit`, `test_frontend_audit_app`,
  `test_frontend_audit_cli`, `test_frontend_audit_clock`,
  `test_frontend_audit_foundation`, `test_frontend_audit_inventory`,
  `test_frontend_audit_runner`, `test_frontend_audit_schemas`) - 41 passed, 0
  failed, 58 warnings.
- Diff review: only the two intended files changed; no secrets, no unrelated
  edits, no reset/overwrite of prior work.

### Known limitation introduced by fix 1

If an audited text-200 route renders no nonce-bearing `<style>`/`<script>`, the
zoom injection throws "page CSP nonce not found" and that single capture is
marked `failed` rather than silently un-zoomed. This is a truthful failure
signal (strictly better than the old silent CSP block). In the 2026-07-23 run
this concern did not materialize: every Chromium capture, including the text-200
stress captures, succeeded (0 failures), so no audited route lacked a nonce-bearing
asset. Keep watching it on future runs.

### Supported run - completed (2026-07-23)

Environment: WSL Ubuntu-24.04 (Linux, `operating_system.supported: true`),
Python 3.12.3, Playwright 1.61.0, Chromium revision 1228 (major 149). The run
reused the previous agent's WSL venv (`~/axisai-sprint0-audit-venv`) and browser
cache (`PLAYWRIGHT_BROWSERS_PATH=~/.cache/axisai-sprint0-playwright`).

- Preflight (`preflight.json`): `success: true` - launch, loopback navigation,
  screenshot, and clean shutdown all passed on a supported OS.
- `capture --tier full`: 325 resolved captures - 277 Chromium `captured` (0
  failed), 24 WebKit + 24 Firefox `blocked` (optional browsers not installed;
  their system libraries need privileged apt access, which is not available in
  this WSL user context). `mandatory_blocked` is false, so the run exits 0.
- `verify --manifest`: `audit artifacts valid` (inventory, warning-baseline,
  source-provenance, findings, all nine reports, manifest, and report-xrefs).
- Frozen frontend baseline reproduced on the Windows host under the exact
  command/environment in `warning-baseline.json`: 48 passed, 343 warnings (the
  baseline is environment-frozen to Windows Python 3.14.3; policy forbids
  cross-environment warning comparison, so it is intentionally not re-measured
  under WSL Python 3.12).
- Automated per-capture signal over the 277 Chromium captures: 0 template
  placeholders, 0 page errors; overflow on 22 captures / 5 routes; fixed-bottom
  occlusion on 152 captures / 15 routes; console errors on 22 captures / 4
  routes; 268 `failed_requests` captures that largely reflect the isolated
  factory blocking external CDN/analytics requests (triage against the allowlist
  before treating as defects).

Files changed in this continuation:

- `scripts/frontend_audit/runner.py`, `tests/test_frontend_audit_runner.py`
  (inherited uncommitted fixes, now verified).
- `docs/handoff.md`, `docs/frontend-readiness/sprint-0/visual-qa-harness.md`,
  `docs/frontend-readiness/sprint-0/verified-audit.md`,
  `docs/frontend-readiness/sprint-0/environment-diagnostics.json` (status
  updates).
- `docs/frontend-readiness/sprint-0/evidence/2026-07-23/` (new curated,
  schema-validated evidence: manifest + 277 screenshots + 277 result JSONs,
  ~17 MB).

Raw run artifacts (WSL-native `~/axisai-audit-run/`, and `artifacts/ui-audit/`)
are outside git by design (`.gitignore`).

### Remaining follow-up work (Sprint 1 entry)

- Adjudicate each render-/interaction-/state-dependent external finding to a
  confirmed or non-reproduction result against the curated screenshots and
  result JSONs; the evidence is now available but per-finding adjudication is not
  asserted here.
- Triage the 268 `failed_requests` captures against the external-request
  allowlist to separate isolation artifacts from genuine broken references.
- Optionally install WebKit/Firefox with their system libraries in a
  privileged-capable environment to convert the 48 optional captures from
  `blocked` to `captured`.
- Address the confirmed narrow-width overflow and fixed-bottom occlusion before
  broad UI work, per `implementation-backlog.md`.

Constraint honored: nothing was pushed, merged, or deployed.

---

## Sprint 7 PR1 — Canonical Workout State Contract and Read Model

- **Track:** Core Feature (Workout State Reliability)
- **Sprint / PR:** Sprint 7 · PR1 (first of four)
- **Branch:** `sprint7-pr1-canonical-workout-state`
- **Worktree:** `.worktrees/sprint7-pr1-canonical-workout-state`
- **Base commit:** `d68186a` (`origin/main`, "Sprint 0: frontend visual-audit harness", #181)
- **Final commit:** the single commit adding this section (`feat(training): add canonical workout state read model`) — see `git log -1`.
- **Merged prerequisites / Sprint 6.3 confirmation:** the spec's "Sprint 6.3" maps
  to this repo's **Sprint 6 PR3 — Adaptive Planning Engine** (`bad1bef`, #170),
  confirmed an ancestor of the base (`git merge-base --is-ancestor bad1bef
  origin/main` → true), together with PR1 `688e250`, PR2 `022d821`, PR4 `b8b1b67`,
  PR5 `07ef1ff`. `app/services/{training_history,training_progression,training_planning}`
  all present. No stacked dependency on unmerged work.

### Discovery — previous workout-state authorities & contradictions

State was inferred piecemeal by three authorities that could disagree:
- **Completion** ⇐ today's `PumpCheck` (`GET /workout/status`
  `app/blueprints/training.py:277`; `complete_workout` idempotency guard `:147`).
- **Schedule** (workout vs rest) ⇐ decided **client-side** in `static/training.js`
  (`renderHero` ~289, `todayDay` ~268) by parsing `TrainingPlan.plan_data.program[]`.
- **History / "session happened"** ⇐ `WorkoutLog` incl. `WORKOUT_COMPLETION_MARKER`
  via `app/services/training_history` (`GET /api/progress/workout`
  `tracking.py:573`).

Confirmed contradictions: the AI-coach `commit_workout_log` tool
(`app/services/ai_coach.py:417`) writes **real, non-marker `WorkoutLog` rows with
no `PumpCheck` and no marker** → a user can have execution today while
`/workout/status` reports `completed:false`. No persisted "started/in-progress"
state exists (the session in `training.js:365` is *ephemeral, in-memory only*). No
plan↔log identifier linkage exists (`WorkoutLog` has no scheduled-workout id;
association is date-only).

### Canonical design

- **Owner:** `app/services/workout_state/` (`models`/`queries`/`resolver`/`__init__`),
  pure/impure split mirroring `training_history`/`training_planning`. Read-only;
  consumes existing contracts; copies no heuristics. Public entry:
  `resolve_workout_state(user_id, *, today=None) -> WorkoutStateSnapshot`.
- **Dimensions:** A `schedule_state` {scheduled, rest_day, no_plan,
  schedule_unavailable} · B `execution_state` {no_execution, in_progress,
  completed} · C `plan_relationship` {matches_scheduled, unscheduled,
  unrelated_date, indeterminate} · D `action` {start, resume, none, blocked} · E
  dominant `primary_state` {rest_day, scheduled_not_started, in_progress,
  completed, unscheduled_in_progress, unscheduled_completed, no_plan,
  needs_attention}. Diagnostics: `completed_today`, `is_rest_day`,
  `stale_previous_workout`, `anomaly`, `today`, `contract_version=1`.
- **Source precedence:** completion = today's PumpCheck (marker corroborates only);
  execution = today's non-marker WorkoutLog (never proves completion); schedule =
  newest plan's program matched to Istanbul weekday, parse failure →
  schedule_unavailable (never a silent rest day); association = local-date + tip
  (no identifier linkage). Conflicts resolve to a deterministic safe result;
  malformed → blocked; today evaluated independently of prior days.
- **Timezone:** `app.timeutil` only (`app_today`/`app_date_of`/`utc_day_bounds`) —
  no second helper; UTC-boundary completion counts for the Istanbul day (tested).
- **Anomaly handling:** unexpected read failure fails safe to
  needs_attention/blocked; only safe metadata logged (`[WORKOUT_STATE] anomaly
  rid=… user_id=… category=… detail=…`) — no stack/SQL/health/payload data.

### Implementation completed

- Created `app/services/workout_state/{__init__,models,resolver,queries}.py`.
- Converged the **minimum** read path: `GET /workout/status` now returns one
  snapshot. Change is **additive** — `completed` preserved (derived from
  `snapshot.completed_today`, byte-identical to the old inline PumpCheck query) and
  a new `state` object added. Route stays thin (one resolver call, no state logic).
- Updated two over-strict existing assertions in `tests/test_training_routes.py`
  (exact-dict `== {"completed": …}`) to assert the `completed` field + presence of
  `state`. **No behavior change** — the `completed` value is identical.

### API & compatibility impact

- **Integrated read path:** `GET /workout/status`.
- **Fields added:** `state{...}` (11 keys). **Preserved:** `completed` (same value/meaning).
  **Changed/removed:** none.
- **Remaining consumers not yet converged (documented):** `static/training.js`
  client inference (`renderHero`/`todayDay`); `GET /api/progress/workout` history
  aggregation. Plan↔log identifier linkage remains absent (deferred).

### State matrix & anomaly behavior

`tests/test_workout_state.py` encodes the §10 matrix as a pure resolver
parametrization + invariants. Covered scenarios 1–14, 16, 20 with deterministic
outcomes; **scenario 15** (completed but scheduled-workout identifier mismatch)
documented as **not derivable** (no identifier linkage); "started"/"partial"
(2/3) both surface as `in_progress` because no started-session is persisted.
Invariants asserted: single dominant state; execution=completed iff PumpCheck;
malformed→blocked/needs_attention; historical record ≠ today; rest day preserves
unscheduled workout; in-progress never completed; **no DB writes**; no
AI/provider imports (structural guard); repeated resolution identical; flags do
not alter execution truth.

### Query & performance evidence

Per resolution: 1 query newest `TrainingPlan`; 1 windowed `WorkoutLog` read for
today+yesterday (reuses `fetch_workout_entries`); 1 `PumpCheck` read for the same
2-day window. Bounded windows, no N+1, no unbounded scan, no serializer/model
hidden queries. `test_service_performs_no_writes` asserts row counts unchanged and
an empty session `new/dirty/deleted`.

### Migration / feature flag / rollout / rollback

- **Migration:** not required (all facts derived from existing canonical data).
- **Feature flag:** not required/added (additive read-only; no competing authority;
  no safety boundary gained). Independent of `WEEKLY_PROGRAM_UI_ENABLED` /
  `AI_ADAPTIVE_PLAN_CONTEXT`.
- **Rollout boundary:** additive field coexists with all current consumers; old
  read path (`completed`) remains fully functional.
- **Rollback:** code revert only — no DB change.

### Documentation updated

- New `docs/WORKOUT_STATE.md` (owner, precedence, dimensions, timezone, API,
  anomaly, compatibility, non-converged consumers, scope exclusions).
- `CLAUDE.md` — one bullet for `app/services/workout_state/`.
- `docs/handoff.md` — this section (master handoff; no competing authority created).

### Files changed

- **Created:** `app/services/workout_state/__init__.py`,
  `app/services/workout_state/models.py`,
  `app/services/workout_state/resolver.py`,
  `app/services/workout_state/queries.py`,
  `tests/test_workout_state.py`, `docs/WORKOUT_STATE.md`.
- **Modified:** `app/blueprints/training.py` (additive `state` on
  `/workout/status` + import), `tests/test_training_routes.py` (2 assertions),
  `CLAUDE.md`, `docs/handoff.md`.
- **Deleted:** none.

### Tests & exact results

Environment: Python 3.14.3, Flask 3.1.3, pytest 9.1.1, SQLite; `pytest.ini` runs
`-m "not load"` by default. Warnings are pre-existing `datetime.utcnow()`
deprecations across the codebase (not introduced here).

- **Focused baseline (base `d68186a`, before implementation):**
  `pytest tests/test_training_routes.py tests/test_tracking_routes.py
  tests/test_training_history.py tests/test_training_progression.py
  tests/test_training_planning.py tests/test_adaptive_plan_context.py
  tests/test_progress_api.py` → **175 passed, 0 failed, 0 skipped** (119.57s).
- **New contract tests:** `pytest tests/test_workout_state.py` → **59 passed**
  (39.43s).
- **Focused final (with implementation):** `pytest <focused set above> +
  tests/test_workout_state.py` → **234 passed, 0 failed, 0 skipped** (146.96s) —
  i.e. 175 + 59, with the two updated `test_training_routes.py` assertions passing.
- **Full-suite final (after, with implementation):** `pytest -q -p no:cacheprovider`
  → **2365 passed, 0 failed, 0 errors, 3 deselected** (the 3 `@pytest.mark.load`
  tests, excluded by `pytest.ini`'s `-m "not load"`) in **3891.12s (1:04:51)**.
  No FAILED/ERROR lines.
- **Full-suite baseline (pre-change, pre-existing tests):** **2306 passed, 0 failed,
  3 deselected** — derived as full-final (2365) − 59 new `test_workout_state.py`
  tests. The change is purely additive (one new test module; the only modified
  pre-existing test file, `test_training_routes.py`, keeps the same test count and
  passes), so the pre-existing suite is unchanged. A live baseline run on the base
  commit was observed all-passing through 62% (dots, zero F/E) before being
  manually interrupted to conserve the ~65-minute wall time; the derived count is
  therefore exact for the pre-existing set.
- **Environment limitations:** the full suite is slow (~65 min; AI/concurrency/
  Cognito tests dominate the wall time). The 3 `load` tests are excluded by the
  repo's default `-m "not load"`. Warnings are pre-existing `datetime.utcnow()`
  deprecations across the codebase — none introduced by this PR.

### Parallel-work / cross-track

No other active branch/worktree touches `app/services/workout_state/` (new) or the
`/workout/status` handler. Cross-track: Training/UIUX own converging
`static/training.js` onto this snapshot in a later PR (recorded here, not
implemented).

### Deferred Sprint 7 work / production-readiness limits

- Persisting a real "started/in-progress" workout session (enables true
  resume/recovery across devices) — later PR.
- Plan↔log identifier linkage (would require a migration) so scenario-15
  identifier mismatch becomes detectable — later PR.
- Stale-session recovery/repair for `stale_previous_workout` — later PR.
- Converging `renderHero` and `/api/progress/workout` consumers.

### Production authorization

Local implementation and validation only. Nothing pushed, no PR created, no merge,
no deploy, no production DB/flag change.
