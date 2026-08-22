# Sprint 11 PR2 — Canonical Training Preference Contract

Date: 2026-08-19

`POST /training-plan` now rejects invalid, unsupported, and conflicting
preferences **before** Bedrock/OpenAI. Unknown styles no longer become General.
`odak_hedef` is wired into the generation prompt via `goals.json`. CrossFit is
unsupported (schema cannot express WOD). Powerlifting requires gym equipment.
`gun_sayisi + kardiyo_gun > 7` is conflicting. Typed `{error, code, retryable}`
bodies: `TRAINING_PLAN_INVALID_PREFERENCE` /
`UNSUPPORTED_CONFIGURATION` / `CONFLICTING_PREFERENCES` (422, non-retryable).
Provider models, Adaptive Coaching undo, save/lineage, exercise catalog, and
mobile generate are unchanged. Canonical doc: `docs/TRAINING_GENERATOR.md`.
Tests: `tests/test_sprint11_training_preference_contract.py`.

# Sprint 9 PR3A - Backend Diary Mutation Reliability

Date: 2026-08-11

Backend PR3A adds a no-migration, mobile-only mutation slice over the canonical
`MealLog` ledger. Canonical diary and LogFood entries now carry an opaque
`revision`. `PATCH /api/v1/nutrition/logs/<DiaryItemId>` accepts only absolute
`set_slot`; `DELETE` on the same path hard-deletes. Both require one strong
quoted `If-Match`, use Bearer-only mobile auth, resolve within the authenticated
owner's current server day, lock the row, and revalidate the revision at the
write boundary.

Supported for all current-day entries: slot move and delete. Deferred because
authoritative provenance is absent: manual description/nutrition edits,
provider quantity/serving/food edits, and provider/manual conversion. No source
string, fingerprint, description, barcode, or macro ratio is treated as
provenance. No migration, tombstone, soft delete, mutation journal, provider
call, or Flutter change exists in PR3A.

Preconditions/errors: missing `If-Match` is
`428 DIARY_PRECONDITION_REQUIRED`; malformed is
`400 INVALID_DIARY_PRECONDITION`; stale is `412 STALE_DIARY_ENTRY`; unknown,
malformed, cross-user, historical, and already-deleted IDs share private
`404 DIARY_ENTRY_NOT_FOUND`; unsupported bodies are
`400 INVALID_DIARY_MUTATION`; storage failure is retryable
`503 NUTRITION_TEMPORARILY_UNAVAILABLE`.

Delete success is `204`. A lost response is reconciled through
`GET /api/v1/nutrition/diary/today`: absence means the desired state is reached,
and returned totals remain the sole authority. A second DELETE is private 404;
there is deliberately no replayable delete record. PR3B must refresh after
ambiguous mutation outcomes and must not begin until PR3A is reviewed,
CI-green, and merged. Full wire details are in `docs/MOBILE_NUTRITION.md`.

# Phase 6 - Authentication, Onboarding & Security Handoff


## Sprint 9 Backend Prerequisite — Mobile Nutrition Contract

Full contract, null semantics and source-of-truth matrix:
[MOBILE_NUTRITION.md](MOBILE_NUTRITION.md).

1. **Baseline.** Branch `sprint9-backend-mobile-nutrition-contract` was cut in the isolated worktree `.worktrees/sprint9-backend-mobile-nutrition-contract` from `origin/main` at `34f8dc7` (Hardening PR4, #200). The primary checkout was dirty with unrelated work (`app/__init__.py`, `app/config.py`, `AGENTS.md`, `app/services/db_pool.py`, `tests/test_db_pool.py` at `102a21b`) and was not used, reset, stashed or staged.
2. **Problem.** No nutrition data was reachable from the native client. Every nutrition, food, barcode, menu and progress route carries `@require_auth` (Flask-Login cookie plus `cognito_sid`); `@require_mobile_auth` existed on exactly one non-auth route, `GET /api/v1/account/me`.
3. **Decision.** A versioned adapter, `GET /api/v1/nutrition/diary/today`, over the existing canonical ledger — not `@require_mobile_auth` bolted onto the web routes, which would have published their ambiguities (`DD.MM` day, no entry identity, naive timestamps, fabricated zeroes, sequential ids) as the mobile contract.
4. **One authority.** The adapter reads `MealLog` only. `/api/diary/today` (the `CustomMeal`/`CustomMealItem` builder) is never queried: committing a builder meal writes it to the ledger as well, so the two totals must never be added. No third definition of "today's nutrition" was created, and a test proves a committed builder meal appears exactly once.
5. **Day.** Full ISO `YYYY-MM-DD` plus the IANA zone, both from `app/timeutil` (`app_today`, `APP_TZ`). `?day`, `?timezone` and `X-Timezone` are ignored — the day boundary is server-owned, and a test pins that.
6. **Timestamps.** `logged_at` is offset-aware ISO 8601 via `timeutil.to_app_tz`, the repository's single rule for the naive-UTC `created_at` column. A missing `created_at` stays `null`.
7. **Identity.** `meals[].id` is `base64url(HMAC-SHA256(SECRET_KEY subkey, "<user_id>\0<meal_log_id>")[:18])` — opaque, stable, owner-bound, 24 characters. Derived rather than persisted, so **no migration**: `MealLog.id` already is the internal identity. `matches_diary_entry_id` ships with it so the future mutation PR resolves a token by constant-time comparison against the caller's own rows.
8. **Unknown is not zero.** Null macros stay `null`; a stored `0` stays a measured zero; a missing or non-positive `UserSession.target_calories` publishes as `goal: null` instead of `/api/progress/nutrition`'s ambiguous `target_kcal: 0`. Regression tests cover each.
9. **Totals.** Server-authoritative and unchanged in arithmetic: NULL counts as zero, exactly as `/meal-log/today` already sums. The consequence — a client re-sum can legitimately disagree when an entry carries a null — is documented rather than hidden, and the client does not recompute.
10. **Vocabularies.** `ogun` maps to `kahvalti`/`ogle`/`aksam`/`ara_ogun`, the keys `POST /api/quick-add-meal` already accepts, with `unknown` for anything else (the AI coach writes `AI Koç`, a shared suggestion writes a sentence containing the sender's name). `source` is published verbatim or `unknown`; a NULL is never folded into `manual`.
11. **Registration.** The route lives on the existing `mobile_api` blueprint, so it inherits `/api/v1`, `Cache-Control: no-store`, the 429 handler and the `MOBILE_AUTH_ENABLED` gate, and stays inside the approved-route allow-list in `tests/test_mobile_auth_feature_gate.py`. `app/__init__.py` was deliberately not touched.
12. **Errors.** One new code, `NUTRITION_TEMPORARILY_UNAVAILABLE` (503, retryable). The blueprint catch-all answers `AUTH_TEMPORARILY_UNAVAILABLE`, and a client reading a storage fault as an authentication outcome would discard a valid session.
13. **Privacy.** One log line, failure only: `mobile_nutrition event=diary_read_failed error_type=… request_id=…`. No meal, macro, target, slot, account identifier, credential or provider text on any path; no new analytics. Tested.
14. **Performance.** Two bounded user-scoped SELECTs per request (`ix_meal_log_user_id_tarih`, newest `UserSession`), rows returned as frozen value objects so no lazy load can become an N+1. A test asserts the query count is identical for a one-entry and a five-entry day. No provider call, cache, lock, write or transaction of its own.
15. **Compatibility.** No route, payload, field name, model, column, index, constraint or migration changed. A characterisation test pins that `/meal-log/today` still answers `DD.MM` with no entry id.
16. **Tests.** Baseline on `34f8dc7`: **3,070 passed, 5 skipped, 0 failed**. Final: **3,120 passed, 5 skipped, 0 failed** — exactly the 50 new contract tests, nothing else moved. Both runs were executed in six alphabetical batches because the full single invocation exceeds this environment's process budget. `-m load` and `-m pg_concurrency` were not run (repo default deselects load; no local PostgreSQL) and no persistence behaviour was modified.
17. **Deferred, with owners.** `/meal-log/history` keeps its `DD.MM` labels — mobile PR1 already scoped history out, and no mobile history surface exists yet (owner: the mobile PR that adds trends). Mobile-reachable food search, barcode, serving detail and a canonical LogFood command are Mobile PR2's backend adapters; `app/services/meal_idempotency.py` already provides durable, user-scoped, race-safe duplicate-submit protection and should be reused verbatim rather than replaced.


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
  schedule_unavailable} · B `execution_state` {no_execution, **execution_recorded**,
  completed} · C `plan_relationship` {matches_scheduled, unscheduled,
  unrelated_date, indeterminate} · D `action` {start, none, blocked} · E
  dominant `primary_state` {rest_day, scheduled_not_started, **execution_recorded**,
  completed, **unscheduled_execution**, unscheduled_completed, no_plan,
  needs_attention}. Diagnostics: `completed_today`, `is_rest_day`,
  `stale_previous_workout`, `anomaly`, `today`, `contract_version=1`.
  (Review Finding 2: the former `in_progress` execution/primary states were renamed
  to `execution_recorded` — *recorded execution evidence*, not an active session —
  and the `resume` action was removed from the contract entirely.)
- **Source precedence:** completion = today's PumpCheck (marker corroborates only);
  execution = today's non-marker WorkoutLog = **recorded evidence only** (never
  proves completion, never an active/resumable session); schedule = newest plan's
  program matched to Istanbul weekday, parse failure → schedule_unavailable (never
  a silent rest day); association = local-date + tip (no identifier linkage).
  Conflicts resolve to a deterministic safe result; malformed → blocked; today
  evaluated independently of prior days.
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
- Rewrote the two `tests/test_training_routes.py` `/workout/status` assertions to
  the **strict** additive contract (exact top-level shape `{"completed","state"}`,
  closed enum vocabularies via `assert_valid_state_contract`, completed↔snapshot
  consistency) — restoring strict contract testing rather than loosening it
  (review Finding 4). Still **no behavior change** — the `completed` value is
  identical.

### Review remediation (Sprint 7 PR1 review — READY WITH CONDITIONS → closed)

The post-implementation review returned four conditions; all are now closed on
this same branch (no PR2 started):

1. **Real full-suite baseline (Finding 1).** Ran the same complete command
   (`pytest -q -p no:cacheprovider`) to **completion** in a clean detached worktree
   at the exact base commit `d68186a` — no interruption, no subtraction. Result:
   **2306 passed, 3 deselected, 0 failed, 0 errors, 9411 warnings in 2707.10s
   (45:07)**; junit records 2306 testcases, 0 failures, 0 errors. Compared
   test-by-test against the final run (see *Tests & exact results*).
2. **`in_progress`/`resume` semantics (Finding 2).** A non-marker `WorkoutLog`
   proves execution *evidence*, not an active/interrupted/resumable session — and
   the AI-coach `commit_workout_log` tool writes exactly such rows outside any
   interactive flow. Renamed the execution/primary state `in_progress` →
   `execution_recorded` (*recorded execution evidence*) and **removed the `resume`
   action from the contract**; the ambiguous "evidence, completion unconfirmed"
   case now resolves to `action = none` (safe) — the server never fabricates a
   resumable session it cannot prove. Adversarial tests added:
   `test_service_ai_coach_logged_exercise_is_evidence_not_resumable`,
   `test_no_scenario_ever_emits_resume` (exhaustive input sweep),
   `test_contract_has_no_resume_action`.
3. **PumpCheck completion authority (Finding 3).** Audited every completion path
   (normal `/workout/complete`, AI-coach pump-check tool, AI-coach exercise
   logging, manual/imported/wearable logs, legacy quest path) — table in
   `docs/WORKOUT_STATE.md#completion-authority`. The two paths the product treats
   as "completed" both write a `PumpCheck`; paths that don't are genuinely not
   completions. The contract now keeps three distinct safe states: confirmed
   completion (PumpCheck), recorded execution evidence (non-marker rows), and
   inconsistent/unconfirmed (marker without PumpCheck →
   `completion_marker_mismatch`, completion **not** granted). Test:
   `test_service_marker_without_pumpcheck_is_not_completion`.
4. **Strict API contract testing + report clarifications (Finding 4).** Restored
   strict `GET /workout/status` assertions (exact shape, preserved legacy fields,
   exact `completed`, required keys, allowed enum values, no field removal). The
   structural read-only guard was upgraded from a crude substring scan to an
   **AST-based** check (real imports + real `db.session` write-calls) so it proves
   the dependency/no-write property precisely. Report clarifications (canonical
   Istanbul timezone is repo-wide via `app.timeutil`; all 11 `state` keys are
   intentionally public/additive; the single broad `except` is scoped to the DB
   read, records a `resolution_error` anomaly and fails safe rather than swallowing;
   malformed/unavailable schedule is `blocked` before any `start` branch) are all
   documented in `docs/WORKOUT_STATE.md`.

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
(2/3) both surface as `execution_recorded` (recorded evidence, `action=none`)
because no started-session is persisted — the contract never claims a resumable
session. Invariants asserted (via `assert_valid_state_contract` on every row):
exact key set + closed enum vocabularies; **`resume` never emitted**;
execution=completed iff PumpCheck; recorded-evidence ⇒ not completed & `action=none`;
malformed→blocked/needs_attention (before any `start` branch); historical record
≠ today; rest day preserves unscheduled workout; **no DB writes** (AST guard); no
AI/provider imports (AST guard); repeated resolution identical; flags do not alter
execution truth.

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
  `/workout/status` + import), `tests/test_training_routes.py` (strict contract on
  the two `/workout/status` assertions + import of `assert_valid_state_contract`),
  `CLAUDE.md`, `docs/handoff.md`.
- **Deleted:** none.
- **Review remediation touched** (same 6 created / 4 modified files — no new files):
  `models.py`/`resolver.py` (rename `in_progress`→`execution_recorded`, drop
  `ACTION_RESUME`), `tests/test_workout_state.py` (evidence/no-resume matrix,
  adversarial + strict-contract tests, AST structural guard), and the two docs.

### Tests & exact results

Environment: Python 3.14.3, Flask 3.1.3, pytest 9.1.1, SQLite; `pytest.ini` runs
`-m "not load"` by default. Warnings are pre-existing `datetime.utcnow()`
deprecations across the codebase (not introduced here).

- **Full-suite baseline (base `d68186a`, before implementation) — COMPLETED, not
  inferred (Finding 1):** a clean detached worktree at exactly `d68186a`, same
  command as the final run `pytest -q -p no:cacheprovider` → **2306 passed,
  3 deselected, 0 failed, 0 errors, 9411 warnings in 2707.10s (45:07)**; the junit
  report records **2306 testcases, 0 failures, 0 errors**. This is a real,
  uninterrupted run — the earlier subtraction-derived baseline is superseded.
- **Full-suite final (after, with implementation):** `pytest -q -p no:cacheprovider`
  → **2370 passed, 3 deselected, 0 failed, 0 errors, 9518 warnings in 3485.75s
  (58:05)**; the junit report records **2370 testcases, 0 failures, 0 errors** (the
  3 `@pytest.mark.load` tests are deselected by `pytest.ini`'s `-m "not load"`).
- **Baseline-vs-final delta (test-by-test, from the two junit reports):**
  baseline **2306** → final **2370** testcases (**net +64**). Programmatic id-level
  diff of `baseline.xml` vs `final.xml`: **0 removed** (every pre-existing testcase
  still present), **0 regressions**, **0 status changes** across all 2306 shared
  cases — each still passes identically, including the reworked
  `test_training_routes.py` assertions (same test ids, still green). The **+64**
  added are exactly the net-new `tests.test_workout_state` cases. Both runs: **0
  failures, 0 errors**. Delta is confirmed real, not inferred.
- **New/updated contract tests:** `pytest tests/test_workout_state.py` →
  **64 passed** (was 59 pre-review; +5: no-resume/exhaustive-sweep, AI-coach
  adversarial evidence, marker-without-pumpcheck, strict completed-contract).
- **Focused final set:** `pytest tests/test_training_routes.py
  tests/test_tracking_routes.py tests/test_training_history.py
  tests/test_training_progression.py tests/test_training_planning.py
  tests/test_adaptive_plan_context.py tests/test_progress_api.py
  tests/test_workout_state.py` → **239 passed, 0 failed, 0 skipped** (112.52s) —
  up from 234 pre-review (+5 new contract tests; the strict
  `test_training_routes.py` assertions pass).
- **Environment limitations:** the full suite is slow (~45–65 min; AI/concurrency/
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

---

## UIUX Sprint 1 PR1 - Application Shell & Primary Navigation Foundation

Date: 2026-07-23
Sprint/PR: AxisAI UIUX Track — Sprint 1, PR1 (independent of Core/Training sprint numbering)
Branch: `uiux/sprint1-pr1-navigation-shell`
Worktree: `.worktrees/uiux-sprint1-pr1-navigation-shell`
Verified base commit: `origin/main` @ `d68186a` (Sprint 0 visual-audit harness, #181)
Final commit: committed locally on `uiux/sprint1-pr1-navigation-shell` after all validation gates passed (hash in `git log`; not pushed, no PR, not merged, not deployed, production flag unchanged — per authorization boundary)
Working tree: clean after the local commit (implementation + real-browser validation evidence committed in the isolated worktree only)

### Objective

Create a reversible, accessible, responsive, centrally-governed application-shell +
primary-navigation foundation that presents **Today · Plan · Coach · Progress** as the
beta-critical primary journey, while keeping Nutrition + Community + utility reachable as a
secondary tier. No page/content redesign; no backend business rule moved into the client.

### Current-state findings (before)

- Primary navigation was **hardcoded twice** — `templates/_nav.html` (desktop header
  `.header-nav` ≥1024px + mobile `.nav-drawer`) and `templates/_actionbar.html` (mobile bottom
  bar `.action-bar` <1024px) — both rendering the same **5 tabs**: Home `/`, Nutrition
  `/nutrition`, Training `/training`, Progress `/progress-page`, Profile `/edit-profile`.
- Active state set per-page via `{% set nav_active %}` (`home|nutrition|training|progress|
  profile`) — explicit id (not brittle prefix matching), reused.
- Coach was a floating widget (`static/coach_widget.js`) on the 4 core pages, with **no page
  route**. Community-type features already lived in the drawer/profile hub (secondary).
- Duplication risk: two manually-maintained nav lists, two inline drawer scripts.

### Files changed

| File | Purpose |
|---|---|
| `app/config.py` | New flag `UIUX_NAV_V2_ENABLED` (default OFF) + `configure_app` mapping into `app.config` |
| `app/nav.py` (new) | Canonical navigation contract — read-only presentational metadata + `resolve_active` |
| `app/hooks.py` | New `inject_nav` context processor (flag + contract to every template) |
| `app/__init__.py` | Register `inject_nav` context processor |
| `app/blueprints/coach.py` | New thin `GET /coach` page route (`@require_auth`); import `render_template` |
| `templates/_nav_icons.html` (new) | Shared decorative icon macro (reuses existing SVG paths) |
| `templates/coach.html` (new) | Thin Coach page hosting the existing widget (auto-opens) |
| `templates/_nav.html` | Flag branch: v2 header/drawer from contract; legacy branch unchanged |
| `templates/_actionbar.html` | Flag branch: v2 4-tab bottom bar; legacy branch unchanged |
| `locales/en.json`, `locales/tr.json` | `nav.today/plan/coach`, `coach.page_title/page_intro` |
| `static/nav.css` | v2-scoped block (desktop drawer access, `.nav-drawer-link-danger`, coach page) |
| `tests/test_app_shell.py` | Add `coach.html`/`/coach` to legacy characterization |
| `tests/test_nav_contract.py` (new) | Contract unit tests |
| `tests/test_nav_shell_v2.py` (new) | Flag + route-state + a11y render tests |
| `docs/frontend-readiness/sprint-0/inventory.json` | Register the new `/coach` route (Sprint 0 audit inventory; required by `test_frontend_audit_inventory`) |

### Navigation architecture (after)

Primary (beta-critical), order fixed by contract:

| id | label key | canonical route | active_when (nav_active ids) |
|---|---|---|---|
| today | nav.today | `/` | today, home |
| plan | nav.plan | `/training` | plan, training |
| coach | nav.coach | `/coach` | coach |
| progress | nav.progress | `/progress-page` | progress |

Secondary tier (drawer + avatar/account hub, all `active_when` mapped but never activating a
primary tab): nutrition (`/nutrition`, demoted from primary), notifications, friends, feed,
leaderboard, quests, challenges, gallery, supplements, premium, profile (`/edit-profile`),
logout. **No route removed; no deep link broken** — only surfacing location changed.

Child-route active-state rule: a page sets its existing `nav_active` id; `app/nav.resolve_active`
maps it to a primary destination (e.g. `training` → Plan, `home` → Today). Secondary/utility/
unknown ids → `None` (no false primary active state, incl. `/nutrition`).

### Feature flag

- Name `UIUX_NAV_V2_ENABLED` (documents spec identity `uiux_sprint1_navigation_v2`).
- Default **OFF**. OFF = byte-compatible legacy 5-tab shell (existing production behavior).
- ON = four-destination v2 shell. Presentation-only — never an authorization boundary; every
  route keeps its own `@require_auth`. Independent of every other flag (own env/config key).
- Server-side `{% if nav_v2 %}` branch renders exactly one shell → no hidden-but-focusable
  legacy nav, no duplicate listeners, no duplicate interactive navigation.

### Ownership confirmation

UIUX-owned: shell presentation, primary IA + ordering, desktop/mobile presentation, drawer,
active/focus states, navigation metadata (presentational), tokens/CSS, the thin `/coach` page
shell. Backend authority preserved: no route semantics/auth/subscription/plan/nutrition/coach/
progress logic changed; `app/nav.py` contains no business rules; the client renders only
server-provided `nav_active` and static contract metadata.

### Tests

- Targeted: `python -m pytest tests/test_nav_contract.py tests/test_nav_shell_v2.py
  tests/test_app_shell.py tests/test_design_system.py -q` → **36 passed**.
- Regression slice: `... tests/test_coach_routes.py tests/test_dependency_boundaries.py
  tests/test_hooks.py tests/test_i18n.py tests/test_auth_phase6_ui.py -q` → **151 passed**.
- Full reconciled suite: `python -m pytest -q` → **2332 passed, 3 deselected** (load tests,
  `-m "not load"`), 0 failed, exit 0. The only failure during development —
  `test_frontend_audit_inventory::test_inventory_covers_every_rendered_template` — was caused by
  this PR adding `render_template("coach.html")`; fixed by registering `/coach` in
  `docs/frontend-readiness/sprint-0/inventory.json`. No pre-existing failures.

### Real-browser acceptance validation (WSL Playwright / Chromium)

Date: 2026-07-24. Harness: the hermetic Sprint-0 audit app (`scripts/frontend_audit` —
loopback SQLite, Cognito-free `/__audit__/login`, no outbound network; FatSecret pinned to
`https://fatsecret.invalid`) booted on an ephemeral loopback port and driven by Playwright
**Chromium** under WSL. Scenario: `active-workout` (authenticated, `profile_complete=True`).
Nothing touched production. Environment: WSL Ubuntu-24.04, Playwright 1.61.0, Chromium.
(WebKit/Firefox are unavailable in this WSL — no non-interactive sudo for
`playwright install-deps`; Chromium is sufficient for exact-viewport layout evidence.) EN is
exercised by setting the seeded user's `language` column to `en`, because authenticated locale
resolution ignores `session['lang']`.

**Exact matrix — 5 viewports × 2 locales × 2 flag states = 20 cells on route `/`: 20/20 PASS.**
Each cell records exact viewport width (measured `clientWidth` == requested), document
horizontal overflow, application-shell overflow (`.global-header` / `.header-nav` /
`.action-bar`), label clipping/wrapping, active navigation state, visible shell variant,
content obstruction, duplicate-interactive-nav, and result.

| Viewport | EN·OFF | EN·ON | TR·OFF | TR·ON |
|---|---|---|---|---|
| 320  | pass | pass | pass | pass |
| 390  | pass | pass | pass | pass |
| 768  | pass | pass | pass | pass |
| 1024 | pass | pass | pass | pass |
| 1366 | pass | pass | pass | pass |

Across all 20 cells: no document horizontal overflow, no shell overflow, no label
clipping/wrapping, correct variant (OFF→legacy, ON→v2), and exactly one primary nav container
visible per breakpoint (`.action-bar` <1024px, `.header-nav` ≥1024px) — never both
(`duplicate_interactive_nav=false` in every cell). Localized labels rendered correctly at ON
(TR: Bugün/Plan/Koç/İlerleme; EN: Today/Plan/Coach/Progress) with no raw i18n key leakage.

**Single active state / a11y de-duplication (verified, not assumed).** The primary nav is
rendered in BOTH the desktop `.header-nav` and the mobile `.action-bar`; at every breakpoint
CSS sets exactly one of them to `display:none`, which removes it (and its `aria-current`) from
the accessibility tree. So on `/` the DOM carries two `aria-current="page"` instances of the
*same* id (`aria_current_dom_instances=2`) while exactly one is exposed
(`aria_current_exposed=1`, `distinct_active_primary=["today"]`). This matches the unit contract
`tests/test_nav_shell_v2.py::_active_ids` (which OR's the active state across both instances)
and the legacy shell's own structure. `nav_landmarks=3` in the DOM (header-nav, action-bar,
drawer-panel), but only one primary `<nav>` is exposed per breakpoint (the other is
`display:none`; the drawer carries the `hidden` attribute).

**Representative interaction checks — 7/7 PASS:**

- `/coach` direct load (200) auto-opens the Coach widget (`#cw-window.cw-open`), `coach`
  active; **refresh** re-loads (200), re-opens the widget, `coach` still active.
- Route→active: `/training`→Plan, `/progress-page`→Progress, `/nutrition`→no primary active
  (each with exactly one exposed instance, and none for nutrition).
- Secondary drawer: opens via keyboard (focus `#header-menu-btn` → Enter), focus moves into
  the drawer; **Escape** closes it and focus is **restored** to the trigger.
- Browser **back / forward**: back→Today, forward→Plan (active state tracks history).
- **Breakpoint transitions** across 1024 (1366→390→1024→1023px): exactly one primary nav
  visible at each width, never both.
- **200% zoom** (1366, `zoom:2`): no document horizontal overflow, no nav label clipping.
- **reduced-motion** preference: `prefers-reduced-motion` matches and the drawer still opens.

**Evidence (committed):**

- Machine-readable manifest: `docs/frontend-readiness/sprint-1-pr1/validation-manifest.json`
  (schema 1.0 — every cell + interaction with full measurements).
- 13 curated screenshots: `docs/frontend-readiness/sprint-1-pr1/screenshots/` (both flag states
  at 320 and 1366, EN+TR at ON, 768/1024 at ON, coach-open, drawer-open, 200% zoom,
  reduced-motion).
- The full raw screenshot set (all 20 cells + interactions) is written to
  `artifacts/ui-audit/sprint1-pr1/screenshots/` — **gitignored** (kept local; path documented
  here).

### Rollout / rollback (operational)

- Rollout: set `UIUX_NAV_V2_ENABLED=1` in the target environment and restart. Verify primary
  order Today/Plan/Coach/Progress, child-route active state, Community secondary placement.
- Rollback: set `UIUX_NAV_V2_ENABLED=0` and restart → server renders the legacy 5-tab shell.
  No cached-asset/session dependency (server-side branch; nav labels non-cacheable HTML). After
  rollback, smoke-test: `/`, `/nutrition`, `/training`, `/coach` (still routable), `/progress-page`,
  `/edit-profile`, Community deep links (`/feed`,`/friends`,`/leaderboard`,`/quests`,`/challenges`),
  `/logout`. `/coach` remains a valid route in both flag states (harmless when OFF).

### Known limitations

- `/coach` renders the existing widget expanded; the Coach page itself is intentionally a thin
  host (no Coach redesign — deferred to a later Coach UX PR).
- Nutrition demoted to secondary per the locked decision; on desktop it is reached via the ☰
  menu/drawer (v2 shows the drawer at all breakpoints) or the avatar/account hub.

### Follow-up for Sprint 1 PR2 (do not implement here)

Page-level Today/Plan/Coach/Progress UX can now build on a stable, centrally-governed shell and
the `app/nav.py` contract. PR2 can consume `nav_primary`/`nav_secondary`/`resolve_active`
without redefining navigation, and can enrich the Coach page beyond the thin host.

### Authorization boundary

Nothing was pushed, no pull request was opened, nothing was merged, nothing was deployed, and no
production feature flag was changed.

## Sprint 7 PR2 — Canonical Workout Mutation Integrity, Idempotency, and Completion Convergence

- **Track:** Core Feature. **Sprint:** 7. **PR:** 2. **Production authorization:** local implementation + validation only.
- **Verdict:** **READY FOR REVIEW.** The one prior condition — execute the committed postgres:16 concurrency test — is **discharged: executed 2026-07-24 against real postgres:16, PASSED 4/4** (single winner on `uq_pump_check_day`). A test-harness defect surfaced during that run (detached-thread invocation trip­ping the pre-existing `_flush_lb_dirty` after_commit hook) was root-caused, proven **not** to affect the production request path, and fixed in the test only (one remediation commit). See "Postgres concurrency test — execution".
- **Branch:** `sprint7-pr2-workout-mutation-integrity`. **Worktree:** `.worktrees/sprint7-pr2-workout-mutation-integrity`.
- **origin/main:** `3d9c582` (Sprint 7 PR1 "Canonical Workout State Contract" #183 — merged squash — plus UIUX nav #182). **Base commit:** `3d9c582`. **Merged PR1:** #183, contained in `3d9c582`. **PR2-only diff range:** `3d9c582..HEAD`.
- **Base note:** an earlier plan pinned the unmerged PR1 dev branch `4f5fd75`; a `git fetch` showed PR1 had since merged into `origin/main`, so PR2 was re-based onto merged main (byte-identical PR1 contract) — a normal, non-stacked branch, no future rebase needed.

### Discovery — writer inventory (verified on `3d9c582`)

Confirmed-completion writers (write today's `PumpCheck` + `WORKOUT_COMPLETION_MARKER`) — **converged**:
- `POST /workout/complete` → `complete_workout` (`app/blueprints/training.py`).
- AI-coach `_tool_analyze_gym_photo` (`app/services/ai_coach.py`) — the prompt's `_tool_analyze_workout_photo` name is stale; the real tool is `_tool_analyze_gym_photo`.

Evidence-only writers (must never create completion artifacts) — **preserved + guarded**:
- AI-coach `_tool_confirm_and_commit_workout_log` — non-marker `WorkoutLog` + XP 15 only.
- `fitx_mcp/server.py` — separate process, raw-SQL `INSERT INTO workout_log` (single exercise); marker refs there are read filters.
- `app/services/wearables/adapters.py` — writes `WearableWorkoutLog` (different table), not the completion path.

Transaction ownership before PR2: each writer owned its own inline transaction with near-duplicate logic (PumpCheck + marker + `record_event` + `_claim_quest` + `award_xp` + `log_activity` + commit). The real risk was **duplication/divergence**, not an open race — the `uq_pump_check_day` unique constraint + `IntegrityError→already_completed` rollback already existed in both.

### Canonical mutation design

- **Owner:** `app/services/workout_completion/` (`models.py`, `queries.py`, `service.py`, `__init__.py`) — layered like `workout_state`/`training_history`.
- **Command/result contract:** `complete_workout(CompleteWorkoutCommand) -> CompletionResult`; `CompletionOutcome ∈ {CREATED, ALREADY_COMPLETED}`.
- **Completion identity:** user + Istanbul day (`date_key = app_today()`), enforced by existing `uq_pump_check_day (user_id, date_key)`.
- **Transaction boundary:** the service owns the single commit/rollback; entry paths never commit completion state. No network (Bedrock/S3) inside the transaction.
- **Result contract:** `CREATED` (pump_check_id, new_total, level, title, quest_result, xp_awarded) / `ALREADY_COMPLETED` (idempotent replay + concurrency race-loser). Only a **verified** `uq_pump_check_day` violation maps to `ALREADY_COMPLETED`; any other `IntegrityError` rolls back and re-raises → internal error.
- **Side-effect ownership:** required-atomic = PumpCheck + marker + quest + XP + Activity + friend Messages; best-effort in-tx savepoint = challenge/badge/notification/feed via `record_event`; response enrichment = presigned URL + Redis LB after_commit (not the completion authority). No deferred post-commit DB side effect exists → no synthetic post-commit test.

### Writer convergence

| Writer | Before | After |
|---|---|---|
| `POST /workout/complete` | inline PumpCheck+marker+XP+quest+challenge+commit | delegates to `complete_workout`; keeps transport/validation/S3/Bedrock/response; **preserved** response shape (`points_awarded/pump_bonus/pump_check_id/new_total/level/title/visibility/shared_friend_ids/quest_awarded/pump_image_url`, `already_completed` 400, friendly 500) |
| AI-coach `_tool_analyze_gym_photo` | inline duplicate + guard **after** Bedrock | delegates to `complete_workout` (private/ai_tool); idempotency preflight moved **before** Bedrock (correction #2); tool JSON (`committed`/`already_done`/`xp_awarded`) preserved |
| `_tool_confirm_and_commit_workout_log`, `fitx_mcp`, wearables | evidence-only | **unchanged**; guarded to stay `execution_recorded` |

### Idempotency / concurrency evidence

- **Persistence mechanism:** existing `uq_pump_check_day` UNIQUE — the sole concurrency-safe atomic claim. Read-only preflight `already_completed_today` is a cost optimization only (skips Bedrock/S3 on replays), never the claim.
- **Sequential replay:** second attempt → `ALREADY_COMPLETED`, no duplicate rows/XP (`test_sequential_replay_is_idempotent`).
- **Constraint > preflight:** with an injected past date the preflight misses but the constraint still yields `ALREADY_COMPLETED` (`test_replay_caught_by_constraint_even_when_preflight_misses`).
- **Race-loser:** deterministic non-500 replay; only `uq_pump_check_day` maps that way (`test_unrelated_integrity_error_is_reraised_not_already_completed`, `test_is_pump_check_day_violation_identity`).
- **Concurrency proof:** deterministic SQLite (constraint + simulated race + rollback) is the default. A real two-thread, separate-session Postgres race lives in `tests/test_workout_completion_pg.py`, gated by `@pytest.mark.pg_concurrency` + `FITX_PG_CONCURRENCY_TEST=1` + `PG_TEST_DATABASE_URL` (targets `postgres:16`, matching CI). **EXECUTED 2026-07-24 against a real disposable postgres:16 (16.14) — PASSED 4/4** (see "Postgres concurrency test — execution" below). Outcome: exactly one `CREATED`, one `ALREADY_COMPLETED` (loser via the `uq_pump_check_day` constraint), one PumpCheck, one marker, XP credited once (35). Genuine multi-connection race, not SQLite-simulated.

### Atomicity / rollback evidence

- Required atomic writes: PumpCheck, marker, quest, XP, Activity, friend Messages. Injecting a failure at each required helper rolls back **everything** — no partial rows (`test_required_helper_failure_rolls_back_everything`, parametrized over `_claim_quest`/`award_xp`/`log_activity`).
- Session not poisoned after rollback; a subsequent legitimate completion succeeds (`test_session_not_poisoned_after_rollback`).
- Post-commit: none (no async completion job). Presigned URL is response enrichment.
- Helper transaction-neutrality **verified in-repo**: `award_xp`/`_claim_quest`/`log_activity` (gamification) and `record_event`/`notify` (challenges/notifications) do not commit; the service avoids `complete_quest_for_user`/`run_weekly_rollover`/`seed_challenges` (which do).

### PR1 state compatibility

- Confirmed completion → resolver `completed_today=True`, `execution_state=completed` (`test_pr1_resolver_reports_completed_after_canonical_completion`).
- Failed completion → state unchanged, not completed (`test_pr1_resolver_unchanged_after_failed_completion`).
- Evidence-only write → `execution_recorded`, not completed (`test_ai_coach_exercise_logging_stays_evidence_only`).
- No mutation produces `in_progress`/`resume`; PR1 public vocabulary unchanged.

### API / client compatibility

- `POST /workout/complete`: response shape and status codes preserved (200 success incl. `points_awarded=base+photo+quest`, 400 `already_completed`, 422 invalid Pump Check, friendly 500). Verified by unchanged `test_training_routes.py` + `test_route_replay_skips_pump_check_validation`.
- AI tool: `committed`/`already_done`/error JSON preserved (`test_coach_tools.py` unchanged; `test_tool_replay_skips_bedrock`). One intentional change: the *preflight* replay returns `already_done` without `form_rating`/`reason` (Bedrock skipped); the post-Bedrock race-loser still includes them.

### Migration / database

- **Migration: none.** `uq_pump_check_day` already provides the atomic claim across all Postgres processes that insert PumpCheck. Single migration head unchanged.

### Tests and exact results

**Command:** `python -m pytest -q -p no:cacheprovider` (canonical; `pytest.ini` applies `-m "not load"`).

**Method note:** the full suite (130 files) exceeds this environment's background-job wall-clock cap (a single serial run was killed at 69%), so full baseline and full final were each run as **three parallel file-partition chunks** (`NR%3`) and totaled. The two new test files shift the partition between baseline and final, so **per-chunk counts are not directly comparable — totals are.**

- **Focused baseline** (at `3d9c582`): **264 passed, 0 failed** (`test_training_routes test_ai_coach test_coach_tools test_mcp_tools test_pump_check_sharing test_workout_state test_analytics_engine test_barcode_workflow test_cascade_delete`).
- **Full baseline** (pristine `3d9c582`, via `git stash`, 3 chunks): 973 + 668 + 755 = **2396 passed, 0 failed, 0 skipped**.
- **Full final** (PR2, 3 chunks): 940 + 657 + 818 = **2415 passed, 1 failed, 1 skipped**.

**Test-by-test comparison:**
- **+19 passed** = the 19 new `tests/test_workout_completion.py` cases (2396 + 19 = 2415). Every baseline-passing test still passes.
- **+1 skipped** = `tests/test_workout_completion_pg.py` (opt-in `pg_concurrency`; skips in the default no-env run by design). It was **executed separately against real postgres:16 and PASSED 4/4** — see "Postgres concurrency test — execution" below.
- **The single final "failure"** = `tests/test_mcp_gate.py::test_http_transport_refused_without_optin[entrypoint1]` — `subprocess.TimeoutExpired` after 60 s spawning `python fitx_mcp/server.py --http`. A **non-deterministic CPU-contention artifact** of 3 parallel pytest processes (that subprocess needs ~54 s uncontended): **re-run in isolation it passes 3/3 (53.76 s)** and it **did not fail in the baseline run**. PR2 does not touch `fitx_mcp`. **Not a regression.**
- **Pre-existing failures: none. Newly-introduced real failures: none.**

**Supplementary isolated runs (PR2 code):** 253 passed (completion suites + new tests) and 134 passed (dependency-boundaries / remaining-work / migration-graph / ownership / training-page-characterization) — 0 failures. `test_mcp_gate.py` isolated: 3 passed.

**Static/type/lint:** repo has no flake8/ruff config and no CI lint gate; `py_compile` passes on all created/modified modules; `training.py` unused-import cleanup verified. **Migration head:** single head, unchanged (no new migration). Import/startup: app imports and all `app`-fixture tests initialize cleanly.

### Query, locking, performance

- No new historical scans/N+1. The mutation is a bounded insert set guarded by one unique index; the preflight is a single indexed `PumpCheck` existence read (removes redundant provider work on replays). No new locks/table locks; no network in-transaction.

### Files changed

- **Created:** `app/services/workout_completion/{__init__,models,queries,service}.py`; `tests/test_workout_completion.py`; `tests/test_workout_completion_pg.py`.
- **Modified:** `app/blueprints/training.py` (converge + import cleanup); `app/services/ai_coach.py` (converge + preflight-before-Bedrock); `pytest.ini` (`pg_concurrency` marker); `docs/WORKOUT_STATE.md`; `CLAUDE.md`; `docs/handoff.md`.
- **Deleted:** none.

### Rollout / rollback

- Additive, code-revert-compatible: no schema/flag change. Rollout = deploy code; monitor duplicate/conflict rate (expect ~0 duplicate PumpCheck/day) and completion 5xx. Rollback = revert the PR2 commit (no DB action). Old clients/workers unaffected (response contract preserved; no new columns).

### Deferred (record only — not implemented)

- Persisted/active sessions, resume/recovery, plan-to-log linkage, `/api/progress/workout` + `renderHero` convergence, workout UI/nav redesign, XP/challenge redesign — later Sprint 7 PRs.
- Opt-in Postgres concurrency test now runs in CI too (CI has `postgres:16`); wire the `pg_concurrency` marker + env into a CI job if a standing gate is wanted (currently opt-in/manual).
- **Latent fragility (record only — NOT a PR2 defect, do not fix in PR2):** `gamification._flush_lb_dirty` (an `after_commit` listener, pre-existing) calls `db.session.get(User, id)`. If the completing session no longer holds the `User` (e.g. a **detached** caller that awarded XP + committed without keeping a strong reference — no Flask-Login `current_user`), the object is weak-ref-collected from the identity map and that `get()` emits SQL on a committed session → `InvalidRequestError`. Every current production caller (`POST /workout/complete`, AI-coach tool) runs inside an authenticated request that holds `current_user`, so this is **unreachable today**. A future detached/background XP writer would hit it. Defensive hardening (make `_flush_lb_dirty` never emit SQL post-commit — snapshot the ids/values pre-commit, or guard for absent identity) is out of PR2 scope (shared gamification infra, affects all XP paths).

### Postgres concurrency test — execution (2026-07-24)

- **Environment:** Docker Desktop 29.6.2 (overlayfs) started locally; disposable, loopback-only container `postgres:16` (digest `sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20`, server **PostgreSQL 16.14**), bound to `127.0.0.1:55432`, ephemeral throwaway credential (redacted, destroyed at teardown). **No production or shared-dev DB touched.**
- **Command (creds redacted):** `FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://fitx_race:<REDACTED>@localhost:55432/fitx_test python -m pytest -m pg_concurrency tests/test_workout_completion_pg.py -q`.
- **Result:** **PASSED, run 4×** (≈40–49 s each; single-completion + two-thread race). Separate connections + separate SQLAlchemy sessions per contender (own app context). Asserted on persisted rows: winner `CREATED`, loser `ALREADY_COMPLETED` via `uq_pump_check_day`, PumpCheck=1, marker=1, XP=35 once; no poisoned transaction, no partial rows; disposable DB dropped afterward.
- **Harness defect found & fixed (PR2-scoped, test-only):** the committed test initially FAILED — the winner raised `InvalidRequestError` from the `_flush_lb_dirty` `after_commit` hook (see "Latent fragility" above). Root-caused to the test invoking `complete_workout` from a **detached** worker thread that discarded the loaded `User`, so it was weak-ref-collected — a calling convention production never uses. **Verified production is unaffected**: the real HTTP `/workout/complete` path (`test_complete_awards_xp_and_records_pump_check`, `test_pump_check_day_unique_constraint`, `test_workout_status_flips_after_completion`, 6 tests) **passes against this same postgres:16**. Fix: each contender now holds a **strong reference** to its `User` for the whole completion (mirroring Flask-Login `current_user`) and teardown disposes the pool before `drop_all`. Only `tests/test_workout_completion_pg.py` changed — no source, no shared test infra (`conftest.py` unchanged) — so no full-suite rerun was triggered; the focused SQLite suite (completion + both writer entry points + pump-check sharing) re-ran green (**112 passed, 1 skipped**).

### Authorization boundary

Nothing was pushed, no PR opened, nothing merged, deployed, or production-flag-changed (this is the PR2 boundary as of 2026-07-24). Docker Desktop + a disposable local Postgres container were started for the concurrency test only, and the container/credential were torn down afterward. **Sprint 7 PR3 is documented in its own section below, with its own authorization boundary.**

## UIUX Sprint 1 PR2 - Today Experience: Next-Action Hierarchy & State Semantics

### Dependency and base (reconciled against current origin/main)

- **PR1 is now merged into `origin/main`** as `a272b5b` (PR #182; its tree is **byte-identical** to the
  original stacked base `1f32c2f`). After PR1 landed on `d68186a`, **Sprint 7 PR1** (`3d9c582`,
  "Canonical Workout State Contract and Read Model", PR #183) landed on top, so `origin/main` advanced
  from `d68186a` to `3d9c582`.
- PR2 was therefore **rebased onto `origin/main` = `3d9c582`** (PR1 is present in main's ancestry via
  `a272b5b`, so the stacked-PR rule against rebasing past an absent/unmerged PR1 no longer applies).
  Branch `uiux/sprint1-pr2-today-next-action`, worktree `.worktrees/uiux-sprint1-pr2-today-next-action`.
  The PR1 worktree/branch was left untouched at `1f32c2f`.
- **Second reconciliation (after PR #185 opened):** `origin/main` advanced again from `3d9c582` to
  `307b7b5` when **Sprint 7 PR2** ("Canonical Workout Mutation Integrity", PR #184) merged. PR2 was
  **rebased onto `origin/main` = `307b7b5`**. Only two files overlapped: `app/blueprints/training.py`
  (auto-merged — #184 rewrote the *write* path `complete_workout()`/import block while PR2 touched the
  *read* paths `workout_status()`/`get_active_training_plan()`) and `docs/handoff.md` (both append a
  section; both preserved). Sprint 7 PR2 owns the write side via `app/services/workout_completion/`;
  PR2's `today_facts` still reads through `workout_state.resolve_workout_state`, which #184 left intact,
  so the read/write split holds and no PR2 behavior changed. Full reconciled suite re-run green after
  the rebase.

### Reconciliation with Sprint 7 PR1 (canonical workout-state)

The two commits main gained since PR1's base were **PR1 itself** (`a272b5b`) and **Sprint 7 PR1**
(`3d9c582`). Sprint 7 PR1 introduced `app/services/workout_state/resolve_workout_state()` as the
**single canonical owner** of current workout-state and refactored `/workout/status` to return the
`completed` flag **plus** an additive `state` object from that resolver. That directly overlaps PR2:

- **`app/blueprints/training.py`** conflicted on `workout_status()`. Resolution: **keep Sprint 7's
  canonical version** — PR2 no longer touches that route, so the additive `state` field is preserved.
  PR2 keeps only its `get_active_training_plan()` change (share the active-plan selector), which
  Sprint 7 did not touch.
- **`app/services/today_facts.workout_completed_today`** now **delegates to
  `resolve_workout_state(user_id).completed_today`** instead of running its own day-bounded `PumpCheck`
  query. After Sprint 7 that resolver *is* the completion helper behind `/workout/status`, so a second
  query would re-introduce exactly the duplication correction #2 forbids. `gather_today_facts` maps the
  resolver's fail-safe `resolution_error` anomaly to `read_ok=False` (honest error), while benign
  domain anomalies (e.g. an unparseable schedule) stay non-error → the plan-exists state is preserved.
- `docs/handoff.md` auto-merged cleanly (Sprint 7 PR1, UIUX PR1 and UIUX PR2 sections all intact).

### Objective

Turn the Today destination (`/` -> `tracking.py:home()`) into a production-safe, accessible,
responsive, reversible experience behind a new default-OFF flag: one authoritative next action,
honest missing/completed/error semantics, a labeled quick-log, PR1 nav consumed (not redefined),
no backend authority moved into the UI, and no client-side recommendation engine.

### Current-state findings (before)

- Legacy Today (`templates/index.html`) is an 8-card "AI command center". Its primary defect was a
  **client-side next-action engine** (`computeNextAction()`) guessing the next step from
  `new Date().getHours()`, consumed calories, water and a workout flag — forbidden client-owned
  inference. Loading/no-plan/completed/error were conflated.
- Legacy first-render HTTP: `GET /last-session`, `/meal-log/today`, `/water`, `/workout/status`,
  `/checkin-history`, `/leaderboard/reward-check` = **6 requests** (+ Chart.js CDN).

### Files changed

- `app/config.py` — `UIUX_TODAY_V2_ENABLED` (env `== "1"`, default OFF) + registered into `app.config`.
- `app/today_presenter.py` (new) — **pure** presenter: frozen `TodayFacts` -> `build_today_view` ->
  frozen `TodayView`. No DB/session/model/query/write/AI/HTTP/timezone/business logic.
- `app/services/today_facts.py` (new) — read-only facts layer: owns the canonical active-plan selector
  (shared with `/training-plan/active`) and **delegates today-completion to the Sprint 7 canonical
  `resolve_workout_state`**; `gather_today_facts` tolerates read failure (`read_ok=False`, including the
  resolver's fail-safe `resolution_error` anomaly).
- `app/blueprints/training.py` — `/training-plan/active` shares `today_facts.get_active_plan`;
  `/workout/status` is left as Sprint 7's canonical `{completed, state}` (PR2 reads completion from the
  same resolver rather than re-querying). Endpoint responses characterization-tested.
- `app/blueprints/tracking.py` — `home()` reads the flag; OFF renders unchanged `index.html`, ON
  builds facts+view and renders `today.html`.
- `templates/today.html` (new), `static/today.css` (new), `locales/{tr,en}.json` (+21 `today.*` keys,
  parity kept), `.env.example` (flag doc), `tests/test_today_v2.py` (new),
  `scripts/frontend_audit/today_pr2_matrix.py` (new, evidence tooling; now records per-cell
  `server_errors`/`failed_requests` and adds a `--behavior` interaction-evidence run).
- `scripts/frontend_audit/app.py` — **harness-only** request-serialization lock so the hermetic audit's
  per-request global clock/validator patching is coherent under the threaded server's parallel XHRs
  (Blocker-2 fix; no application code changed).
- `docs/frontend-readiness/sprint-0/inventory.json` — additive `today.html` variant entry (excluded /
  audit_only) so the "every rendered template is inventoried" invariant holds for the new template.
- `docs/frontend-readiness/sprint-1-pr2/` — `validation-manifest.json` (48 cells) +
  `interaction-results.json` (6 cells) + `behavior-results.json` (17 checks) + curated `screenshots/` +
  `test-partition.json` (chunked-suite proof) + `xhr-500-investigation.md` + `provenance.json`.

### Presentation contract (canonical sources + states)

Facts are gathered by the route/read layer and mapped by the pure presenter. Canonical sources:
`has_active_plan` reuses the exact `/training-plan/active` selector (most recent `TrainingPlan`);
`workout_completed_today` delegates to the Sprint 7 canonical
`resolve_workout_state(...).completed_today` — the same signal `/workout/status` now returns (today's
`PumpCheck`, Istanbul day window). States: `no_plan` (primary "Create your plan" -> `/training`); `plan_ready`
(neutral primary "View your plan"/"Planini goruntule" -> `/training` — **not** "today's workout /
recommended"); `workout_done` (explicit completion, **no dominant CTA**, secondary View progress /
Open Plan, no stale Start); `error` (`read_ok=False` -> honest error, **not** no-plan, neutral
secondary Open Plan only). State ids are stable ASCII, never derived from translated copy.

### Ownership confirmation

UIUX-owned: Today page hierarchy/visual/layout/state presentation, primary-action placement,
quick-log presentation, Today a11y/i18n/flag/init/browser validation. **No recommendation engine
was created.** No workout/recovery/nutrition/entitlement/Coach/Progress rule was reconstructed;
backend authority is unchanged. The presenter is a pure mapping over already-authoritative facts.

### Unsupported states (Core/Training dependencies, not fabricated)

The repository has no authoritative "active/in-progress workout session", "scheduled workout-for-
today", explicit "rest day", "check-in required", or user-facing "why the plan changed" reason.
Per spec they are **not** invented: the "Why this changed" section is omitted, and rest-day / active-
session are recorded here as Core/Training dependencies for a later PR.

### Feature flag (four combinations)

`UIUX_TODAY_V2_ENABLED` — default OFF, server-config only (not query/cookie/header/storage),
independent of `UIUX_NAV_V2_ENABLED`. OFF renders legacy `index.html` byte-identically; ON renders
`today.html`. All four Nav x Today combinations render exactly one Today tree and one accessibility-
exposed primary nav (verified by tests + the browser matrix). Either flag rolls back alone.

### Request counts and performance

Today V2 first-render HTTP: `/last-session`, `/meal-log/today`, `/water`, `/checkin-history`,
`/leaderboard/reward-check` = **5 requests** — the legacy client `/workout/status` fetch is
**dropped** because completion is now read server-side for the primary action (+2 cheap server-side
reads, not HTTP). Net: **V2 (5) < legacy (6)**, no duplicated authoritative read, no new dependency.
Tested by `test_v2_does_not_client_fetch_workout_status` / `test_legacy_off_path_still_fetches_workout_status`.

### Automated tests

Command: `python -m pytest -q` (worktree; load tests deselected by `pytest.ini -m "not load"`).
`tests/test_today_v2.py` (**27 tests**): presenter purity/contract, primary-action, four-combo flag
matrix, canonical endpoint sources (`/training-plan/active` selector reused; `/workout/status` owned by
the Sprint 7 resolver with its `completed` signal shared), the today-facts↔`/workout/status` agreement,
the resolver `resolution_error`→honest-error mapping (with a benign domain anomaly staying non-error),
request-count dedup, one-H1/no-raw-key-leak, AxisAI-only copy. Affected slices (`test_today_v2`,
`test_training_routes`, `test_workout_state`, `test_nav_shell_v2`, `test_app_shell`, `test_i18n`,
`test_nav_contract`): **all passed**.

**Full reconciled suite on the rebased stack: 2423 tests, `pytest.ini -m "not load"` applied.** Run in
four file-partitioned chunks (single long runs were being SIGTERM-killed in this environment):
**606 + 606 + 605 + 606 = 2423 passed, 0 failed.** Partition proof (`test-partition.json`): the suite
was partitioned **by file** (each node in exactly one chunk → chunks provably disjoint, union = the
whole suite; 129 files), the per-chunk collected counts re-summed to the full 2423, and every chunk
exited 0 — no node omitted or duplicated. (The earlier pre-rebase run had found and fixed one
PR2-caused inventory failure — `today.html` now carries a documented `excluded`/`audit_only` Sprint-0
inventory entry since it shares `/` in production; that fix is included here.)

### Browser validation (WSL Playwright / Chromium, hermetic) — re-run on the rebased stack

`scripts/frontend_audit/today_pr2_matrix.py` reuses the Sprint-0 hermetic audit app + `AuditServer` +
fixed browser clock + Chromium, toggling the Nav/Today flags per cell. Re-run in WSL Ubuntu-24.04 with
the Sprint-0 venv (python 3.12.3) + `PLAYWRIGHT_BROWSERS_PATH` browsers (Chromium 149.0.7827.55) after
the rebase. Matrices: A = 20-cell Today (5 viewports x 2 locales x Today{off,on}, Nav ON); B = 16-cell
cross-flag (2 viewports x 2 locales x 4 combos); C = 12-cell state (no_plan/plan_ready/workout_done x
390/1366 x EN/TR). Per cell: document + Today-mount scroll/client width, horizontal overflow, visible
Today tree, `data-today-state`, raw + exposed primary-nav counts, primary-action count/label, label
clipping, raw-key leakage, **plus (new) per-cell `server_errors` (same-origin ≥500) and
`failed_requests`** — any same-origin 5xx now **fails** the cell so a passing layout can no longer hide
a network failure. Result: **48/48 cells passed, 0 failed, 0 blocked**, and **zero same-origin 5xx
across all 48 cells**. Every cell: no document or Today-shell horizontal overflow across
320/390/768/1024/1366; exactly one accessibility-exposed primary nav in all four flag combinations;
correct `data-today-state`; neutral primary label ("View your plan" / plan_ready) and **no** dominant
CTA on `workout_done`; no raw-key leakage. External Google-Analytics/CDN requests fail by design in the
no-network hermetic env and are recorded but not counted as cell failures (they are environmental and
identical on the legacy page). Chromium is the required minimum engine; WebKit/Firefox not installed →
recorded as an environmental limitation.

#### Blocker-2: the transient summary-XHR 5xx — investigated, classified, fixed

The single pre-rebase cell that logged a `500` was fully investigated (`xhr-500-investigation.md`):
single-threaded, all five Today summary endpoints return 200; under concurrency the failure reproduces
**probabilistically (~1-2 % of authed reads), endpoint-agnostically**, as a 503 (or 500 in a different
race) from the **auth/session layer** (`auth_middleware._service_unavailable` ← `session_store`
`SessionTransient`). Root cause: the hermetic audit installs **per-request GLOBAL** clock+validator
patching that is not thread-safe under the threaded server when a page fires parallel XHRs; the legacy
page fires **more** authed XHRs, so the base is equally/more exposed → **a harness (audit-environment)
defect, not a PR2 defect**. Fix: a harness-only request-serialization lock in
`scripts/frontend_audit/app.py` (A/B proof: pre-fix 3 of 4 concurrency runs hit a 503; post-fix
0 across 480+ authed reads). The affected cell (`A-today-20__plan_ready__320__tr__nav-on_today-on`) now
**passes cleanly** on rerun (empty `server_errors`/`failed_requests`/`console_errors`); the hardened
manifest proves no same-origin 5xx hides behind a layout pass.

#### Interaction + behavioral evidence

Interaction checks (`interaction-results.json`), Today ON: **6/6 passed** — reduced-motion (plan_ready
390/EN, workout_done 390/TR), 200% page zoom (plan_ready 1366/EN, 768/TR), 150% text (plan_ready
390/TR, no_plan 390/EN); each held zero horizontal overflow, an intact non-clipped primary action, and
the correct state. Behavioral checks (`behavior-results.json`, `--behavior`): **16 passed, 1 n/a, 0
failed** — primary-CTA pointer + keyboard activation (→ `/training`); quick-log pointer + keyboard
activation (→ `/nutrition`; the quick-log is a labeled link, not an in-page overlay → the "open
overlay / keyboard-navigate-within / Escape-restore" sub-checks are recorded **n/a** with
justification); browser back/forward with route-return-to-Today (state + single primary preserved);
breakpoint transitions below and above 1024 px (320/768/1023/1024/1366/1440 — no overflow, one primary
each); repeated breakpoint transitions with **no duplicate init** (`window.__axTodayInit` single) and
**no duplicate summary XHRs**; no duplicate HTTP requests on load (each summary endpoint once); no
duplicate analytics loader; no duplicate same-origin action submission (primary + quick-log are
GET-navigation links; no in-page mutating POST); no hidden-but-tabbable controls inside the Today tree;
exactly one accessibility-exposed Today tree and one primary nav across all four flag combinations; and
Escape + focus behaviour for the one dismissible surface (the pre-existing reward dialog — seeded so it
opens: focus enters the dialog, Escape closes it, focus not trapped). Evidence:
`docs/frontend-readiness/sprint-1-pr2/{validation-manifest,interaction-results,behavior-results,test-partition,provenance}.json`
+ `xhr-500-investigation.md` + curated `screenshots/`; raw runs (gitignored) under `artifacts/ui-audit/`.

### Accessibility

One H1 ("Today"/"Bugun"), `main` landmark, `role="status"` live regions for loading/summary-error,
server-rendered primary action as a real link with visible token-based focus, icon-only controls
`aria-hidden` with text labels, completion/error communicated by icon+text (not color alone),
reduced-motion honored (CSS + Chart.js animation off), no duplicate IDs, PR1 `aria-current` nav
preserved. Validated by template tests + the real-browser matrix.

### Rollout / rollback (operational)

- Rollout: set `UIUX_TODAY_V2_ENABLED=1` (optionally with `UIUX_NAV_V2_ENABLED=1` for the beta shell)
  and restart. Smoke `/`: confirm one primary action, `data-today-state` correct, no new network
  requests vs legacy, TR/EN. Independent of nav v2.
- Rollback: set `UIUX_TODAY_V2_ENABLED=0` and restart -> legacy Today dashboard returns. Server-side
  branch, no session/cache/static-asset or data-migration dependency; nav v2 unaffected.

### Known limitations

- Quick Log is a single labeled meal-logging entry (-> `/nutrition`). Inline water/weight quick-entry
  is deferred: adding it safely needs the legacy inline logging extracted into a shared partial/JS
  module (avoids a duplicate write implementation) — a UIUX follow-up.
- `error` and loading states are validated at unit/route level; a real DB read failure is not
  reproducible in the hermetic browser run without fault injection.
- The hermetic audit harness now serializes request bodies (a harness-only lock) because its
  per-request global clock/validator patching is not thread-safe under a page's parallel XHRs. This is
  an audit-environment property only — production has no such global patching and is unaffected.
- WebKit/Firefox are not installed in the WSL audit environment, so cross-browser coverage beyond the
  required Chromium minimum is not claimed.

### Dependencies and follow-up (PR3)

Core/Training: authoritative active-session, scheduled-workout-for-today, explicit rest-day,
check-in-required, and a user-facing plan-change explanation would let Today surface richer honest
states. UIUX: shared logging partial for inline quick-log; optional Coach entry on Today.

### Authorization boundary

Local implementation and validation were completed under a local-only boundary; the branch was
subsequently pushed and a pull request opened against `main` under explicit authorization
(PR #185). Nothing was merged, nothing was deployed, and no production feature flag was changed.

## UIUX Sprint 1 PR3 - Plan Experience & Coach Destination Hardening

### Dependency and base

- **PR2 is already merged into `origin/main`** as `9400641` (PR #185). `origin/main` therefore already
  contains UIUX PR1 (`a272b5b`, #182), Sprint 7 PR1 (`3d9c582`, #183), Sprint 7 PR2 (`307b7b5`, #184)
  and UIUX PR2 (`9400641`, #185). PR3 branches **directly from `origin/main` = `9400641`**, so — unlike
  PR2 — there is **no separate PR2-reconciliation commit**: PR3 is a single clean commit on merged main,
  matching the one-local-PR3-commit boundary. Branch `uiux/sprint1-pr3-plan-coach-separation`, worktree
  `.worktrees/uiux-sprint1-pr3-plan-coach-separation` (fresh; the PR2 worktree was left untouched).

### Objective

Two production-safe, accessible, responsive, independently reversible surfaces behind two new
default-OFF server-owned flags: **Plan V2** (a server-authoritative redesign of the existing `/training`
page that removes legacy `training.js`'s forbidden client-side authority) and **Coach Page V2** (a
hardened `/coach` destination that **reuses the exact existing widget** and guarantees exactly one
interactive Coach instance via explicit lifecycle ownership). No second training-plan authority, no
second Coach implementation, no change to AI system/adaptive prompts, model/provider, streaming
protocol, conversation persistence, rate-limit/entitlement/auth/moderation policy, or API contracts.

### Two independent flags

`app/config.py` after `UIUX_TODAY_V2_ENABLED` (same `os.getenv(NAME,"0")=="1"` idiom), registered in
`configure_app()`, read only at request time, documented in `.env.example` as commented `=0`:

- `UIUX_PLAN_V2_ENABLED` — default OFF. ON → `plan.html`; OFF/invalid/missing → legacy `training.html`.
- `UIUX_COACH_PAGE_V2_ENABLED` — default OFF. ON → `coach_v2.html`; OFF/invalid/missing → legacy
  `coach.html`.

Server-config only (never query/cookie/header/localStorage/sessionStorage). Each rolls back alone and
is independent of `UIUX_NAV_V2_ENABLED` / `UIUX_TODAY_V2_ENABLED` / `WEEKLY_PROGRAM_UI_ENABLED`.

### Plan V2 (`/training`) — two independent state contracts

- **Read layer** `app/services/plan_facts.py` (read-only; no writes/AI/HTTP): reuses the exact
  `get_active_plan` selector (shared with `/training-plan/active`), then **safely** parses
  `TrainingPlan.plan_data` into a presentation-only, canonically-ordered day→exercise structure.
  Fallback semantics: DB/read failure → `read_ok=False` (honest error, never fabricated/`no plan`);
  unparseable `plan_data` → `parse_ok=False` (→ `partial_active_plan`, malformed ≠ no plan); canonical
  order preserved; unknown labels neutral; **empty exercise list is NOT a rest day** (only explicit
  `tip == "dinlenme"`).
- **Presenter** `app/plan_presenter.py` (PURE, frozen dataclasses, mirrors `today_presenter.py`) →
  `PlanView`. **Two separate state models:** a **page** state (`read_error` / `no_active_plan` /
  `active_plan` / `partial_active_plan`) and an **independent weekly-section** state (`loading` /
  `populated` / `missing_baseline` / `insufficient_data` / `partial` / `error` / `disabled`). A weekly
  failure never collapses the page. `active_plan` has **no dominant CTA** (`primary=None` — plan content
  is the destination, no "View plan" self-link, no invented "Start today's workout"); `no_active_plan`
  uses the existing canonical in-page generator (no `/training` self-link, not an error); `read_error`
  offers a **safe retry** (page reload), not an "Open Plan" action. Actions carry a label KEY, never
  copy; state ids are stable ASCII.
- **Route** `app/blueprints/training.py training()` mirrors the PR2 swap: `if config[UIUX_PLAN_V2_ENABLED]:
  render plan.html(build_plan_view(gather_plan_facts(user), weekly_enabled))` else legacy
  `training.html` unchanged. No new SQL beyond the shared selector.
- **Template** `templates/plan.html` (new, dedicated tree): one `<h1>`, `{% set nav_active='plan' %}`,
  status conveyed by icon **and** text; **never loads `static/training.js`**; plan days use native
  `<details>/<summary>` (keyboard/reduced-motion/CSP-safe, no new JS); inline scripts none; interactions
  via `data-action` delegation. Creation submitter `static/plan_create.js` (creation-only, no plan
  authority — POST `/training-plan` → POST `/training-plan/save` → reload; 400 → `/setup`) loaded ONLY
  in `no_active_plan`. `static/plan.css` new (`.weekly-program-*` copied from `training.css` so the
  reused mount is styled without loading `training.css`).
- **Weekly section (answer.txt §6/§7):** `weekly_program.js` verified reusable as a standalone consumer
  (queries `document` for the mount, one GET `/api/training/weekly-program`, reads `window.LOCALE`, no
  `training.html`/`training.js` dependency). `WEEKLY_PROGRAM_UI_ENABLED=0` → section `disabled`: no
  mount, no script, no request, active plan stays visible. ENABLED + active/partial → section mounts and
  fires at most one weekly GET; a weekly endpoint error leaves the plan page 200 and visible.

### Coach Page V2 (`/coach`) — lifecycle idempotency + classified shared changes

- **Shared-widget hardening (answer.txt §4/§5)** in `static/coach_widget.js`: a **module-level init
  guard** (`window.__cwWidgetInit`) placed before any injection / `var CW` / `/coach/history` fetch, plus
  **adopt-existing `#cw-root`** — so a second script evaluation is a clean no-op (no duplicate root /
  event/bootstrap wiring / history fetch / second accessibility-exposed instance). On the first (normal)
  evaluation every step runs exactly as before, so single-init behavior is unchanged (proven by the
  behavior suite). Route-mode behaviors are **NOT** added to the shared widget.
- **Route** `app/blueprints/coach.py coach_page()`: `if config[UIUX_COACH_PAGE_V2_ENABLED]: render
  coach_v2.html else render coach.html`; `@require_auth` on both. No business logic / data fetch added.
- **Template** `templates/coach_v2.html` (new): one `<h1>`, honest concise intro (no promotional claim,
  no "Coach controls the Plan"), stable shell, `{% set nav_active='coach' %}`, reuses the exact widget
  (`coach_widget.js` + `.css` + `actions.js`). Route-mode behaviors live HERE only: a nonce'd inline
  opener `window.axCoachOpen` that toggles **only when closed** (idempotent open — re-clicks/poll never
  close an open widget or make a second), an "Open Coach" fallback button via `data-action` delegation,
  and a one-time auto-open on arrival. The floating widget on every other page is untouched. `.coach-page-cta`
  / `.coach-page-hint` added to the existing `.coach-page` block in `nav.css`.

### i18n

`locales/{tr,en}.json`: **+73 `plan.*`** keys (page/section state labels, neutral secondary-action
labels, retry, creation-form labels/options, day/rest/exercise labels) and **+4 `coach.v2.*`** keys
(title/intro/open/hint), added by textual append (CRLF + mixed-escaping preserved). TR/EN parity kept
(**1188 keys each**, verified by `tests/test_i18n.py`). AxisAI-only copy, no "FitX". State identity
derives from presenter ids, never translated text.

### Automated tests

`python -m pytest -q` (worktree; `pytest.ini -m "not load"`).

- `tests/test_plan_v2.py` (**29**): pure-presenter matrix (no-dominant-CTA in every state, populated has
  no self-link/no start, no_active_plan≠error, read_error has no "Open Plan", partial keeps the plan,
  weekly independence, ASCII state ids); read layer (honest absence, canonical order, empty-exercise≠rest,
  malformed→partial, non-list/empty→partial, read failure→read_error); flag OFF legacy / ON V2 /
  missing→fail-safe / exactly-one-tree; template (inline generator only in no_active_plan, days render
  with no CTA, one h1, no key leak, no client-authority markers); weekly disabled/enabled/never-without-plan.
- `tests/test_coach_page_v2.py` (**26**): route flag OFF→coach.html / ON→coach_v2.html; `@require_auth`
  both; reuses widget once, no re-implemented composer; **source-encoded lifecycle invariant** (single
  module guard before side-effects, adopt-existing host, one bootstrap, one history fetch); route-mode
  lives in the template not the shared widget; one h1, nonce'd inline script, data-action (no onclick),
  AxisAI-only + no plan-authority claim; key presence.
- Extended `tests/test_env_example.py` (both new flags documented `=0`, never `=1`) and the audit
  inventory (`plan.html` + `coach_v2.html` added as `audit_only`/`excluded` variant entries so the
  "every rendered template is inventoried" invariant holds).
- Affected + regression slices (`test_plan_v2`, `test_coach_page_v2`, `test_i18n`, `test_env_example`,
  `test_frontend_audit_inventory`, `test_nav_contract`, `test_nav_shell_v2`, `test_today_v2`,
  `test_coach_routes`): **211 passed, 0 failed.** Full-suite partition proof in `test-partition.json`.

### Browser validation (WSL Playwright / Chromium, hermetic)

`scripts/frontend_audit/plan_coach_pr3_matrix.py` reuses the Sprint-0 hermetic audit app + `AuditServer`
+ fixed browser clock + Chromium (WSL Ubuntu-24.04, Sprint-0 venv python 3.12.3,
`PLAYWRIGHT_BROWSERS_PATH`, Playwright build chromium-1228), toggling the Plan/Coach (and, for the
weekly cells, `WEEKLY_PROGRAM_UI_ENABLED`) flags per cell. Each cell fails on unexpected same-origin
≥500, failed same-origin requests, duplicate bootstrap/history fetch, more than one
accessibility-exposed Coach instance/composer/submit, horizontal overflow, or raw-key leakage.

- **Matrix (`validation-manifest.json`): 86 cells, 86 passed, 0 failed, 0 blocked.** Matrices A(Plan 20),
  B(Coach 20), C(Plan×Coach cross 16), D(upstream Nav/Today 8), E(Plan states 16), W(weekly 6 —
  disabled/enabled/enabled-error). Every PR3-owned surface passes at every width 320–1366, EN/TR: Plan V2
  one tree / no `training.js` / status-as-text / no `/training` self-link / correct page state / create
  form only in `no_active_plan` / retry only in `read_error` / weekly disabled→no mount+no request,
  enabled→one mount+≤1 request, weekly error→plan still visible; Coach V2 exactly one root/window/
  composer/submit and auto-open, and exactly one Coach instance on every Plan page too.
- **Legacy 320px overflow — investigated and reconciled (answer.txt 2026-07-26).** The first run reported
  84/86: the two 320px **legacy `training.html`** cells (Plan V2 **OFF**) overflowed the document by 24px
  (EN) / 36px (TR). Proven **pre-existing** — reproduced identically on a clean checkout of base `9400641`
  and on HEAD flags-OFF (byte-identical screenshots), with the legacy render files `git diff`-identical to
  base. **Precise cause** (measured per-element, not assumed): `static/training.css`
  `.wstats { grid-template-columns: repeat(3, 1fr) }` — three `.stat-card` grid items can't shrink below
  their locale-dependent text min-content at 320px (TR>EN confirms text sizing, not a fixed width).
  **Fix** (smallest safe, PR3-scoped, presentation-only): `@media (max-width: 380px) { .wstats {
  grid-template-columns: repeat(2, minmax(0, 1fr)); } }` — legacy-only (`training.css` is loaded solely by
  `training.html`; `.wstats` exists in no other template), inert under Plan ON and on Nav/Today, 390px+
  layout unchanged. After the fix both cells measure zero overflow (`scrollWidth=clientWidth=320`). Full
  detail + evidence: `docs/frontend-readiness/sprint-1-pr3/legacy-overflow/` (`investigation.md`,
  `diag-overflow-{base,head,headfix}.json`, before/after screenshots); regression test
  `tests/test_training_ui.py::test_wstats_collapses_to_fewer_columns_on_narrow_screens`.
- **Interactions (`interaction-results.json`): 6/6 passed** — reduced-motion, 200% page zoom, 150% text
  across Plan V2 and Coach V2 at 390/768/1366, EN/TR (no overflow, one Coach instance, plan/composer
  intact).
- **Behavior (`behavior-results.json`): 10/10 passed** — the answer.txt §9 Coach lifecycle set: Coach OFF
  floating unchanged (one root, toggle flips, exactly one history fetch); Coach ON route single instance
  (one root/composer/submit, auto-open, one history fetch); **script evaluated twice** → no duplicate
  root/composer, still one history fetch; **init() twice** → no duplicate; **route-after-floating** and
  **floating-after-route** → one root each; close→reopen keeps one instance; Plan day expand/collapse via
  native `<details>`; **read_error safe-retry** (`fxReload` → `active_plan` after the injected read
  failure clears); Plan back/forward keeps the surface + one Coach instance.

Chromium is the required minimum engine; WebKit/Firefox are not installed → cross-browser coverage beyond
Chromium is not claimed (environmental limitation). External GA/CDN requests fail by design in the
no-network hermetic env and are recorded but not counted.

### Accessibility

Plan V2: one H1, `main` landmark, `role="status"` on the status line, status by icon **and** text (not
color alone), native `<details>` progressive disclosure (keyboard + reduced-motion safe), server-rendered
secondary links, `nav_active='plan'` `aria-current`. Coach V2: one H1, honest intro, reuses the existing
widget's a11y, one accessibility-exposed Coach instance. Validated by template tests + the browser matrix.

### Ownership confirmation

UIUX-owned: Plan page hierarchy/state presentation/creation-form presentation and Coach destination
shell/lifecycle. **No second training-plan authority and no second Coach implementation were created.**
No AI system/adaptive prompt, model/provider, streaming protocol, conversation persistence, or
rate-limit/entitlement/auth/moderation policy was changed. CSP per-request nonce, CSRF, and output
escaping/sanitization are preserved.

### Rollout / rollback (operational)

- **Rollout:** each flag independently. `UIUX_PLAN_V2_ENABLED=1` + restart → smoke `/training`: populated
  plan shows no dominant CTA, `no_active_plan` shows the in-page generator, `WEEKLY_PROGRAM_UI_ENABLED=0`
  keeps the plan valid with no weekly mount, ≤1 `/api/training/weekly-program` request when enabled, TR/EN.
  `UIUX_COACH_PAGE_V2_ENABLED=1` + restart → smoke `/coach`: exactly one interactive widget (one
  `#cw-root`/composer/`/coach/history`), auto-open, "Open Coach" fallback. Verify the legacy floating
  widget is unchanged on other pages. Flags are independent of Nav/Today/Weekly and of each other.
- **Rollback:** set the flag back to `0` and restart → the legacy `/training` (training.html) or `/coach`
  (thin host) returns. Server-side branch only; no session/cache/static-asset dependency, no
  migration/data repair. Disabling one flag never touches the other.

### Known limitations / pre-existing findings

- Legacy `training.html` (Plan V2 OFF) had a pre-existing 320px horizontal overflow (24–36px), proven on
  base `9400641`. **Resolved** in this PR by a narrow, presentation-only, legacy-scoped CSS fix
  (`.wstats` collapses to two columns below 380px) — see the "Legacy 320px overflow" note above and
  `legacy-overflow/investigation.md`. Plan V2 itself was and remains overflow-clean at 320.
- `read_error` and the weekly-section `error` state are exercised in-browser via fault injection (patched
  selector / patched planner) since a real DB/planner failure is not otherwise reproducible in the
  hermetic run; both are also unit-tested.
- WebKit/Firefox not installed in the WSL audit environment → only the required Chromium minimum is claimed.

### Authorization boundary

Local implementation and validation only. This work is **not** authorization to push, open a PR, merge,
deploy, change production configuration, or enable a production feature flag. Nothing was pushed, nothing
merged, nothing deployed, and no production feature flag was changed. Both flags remain default OFF.
## Sprint 7 PR3 — Persisted Workout Session Lifecycle, Safe Resume, Abandonment & Stale Recovery

- **Track:** Core Feature. **Sprint:** 7. **PR:** 3. **Production authorization:** implemented and validated locally, then — **under explicit user authorization** — the branch was pushed and **PR #186** was opened against `main` (rebased onto current `main`, past #185). **Still nothing merged / no deploy / no prod migration / no prod flag change / no PR4.**
- **Verdict:** **READY FOR REVIEW.** Full suite green vs. baseline (node-level reconciliation below, not arithmetic); the persisted-session lifecycle is default-OFF and, with the flag OFF, PR1/PR2 behavior is byte-identical. The opt-in Postgres proof was **executed 2026-07-25 against a real disposable `postgres:16` (16.14) — the migration validation and all three concurrency tests PASSED**; the tests still skip cleanly in the default run for reviewers without Docker (see "Opt-in Postgres concurrency proof — executed" below).
- **Branch:** `sprint7-pr3-workout-session-lifecycle`. **Worktree:** `.worktrees/sprint7-pr3-workout-session-lifecycle`.
- **origin/main:** originally branched from `307b7b524f8a6ea6dc2a820fa37b1731f5ffd22d` (Sprint 7 PR2 merge #184; PR1 `3d9c582` #183 is a merged ancestor). Since then `main` advanced with **#185** (`9400641`), so the 5 PR3 commits were **rebased onto `9400641`** (linear, no merge commit). **Current base commit:** `9400641`. **PR3-only diff range:** `9400641..HEAD`.

### The gap PR3 closes

Before PR3 the server had **no persisted workout-session concept**. The client `_session` (`static/training.js`) was in-memory only and lost on refresh; `localStorage` held only a paint-cache flag. PR1 deliberately never emitted `resume`/`in_progress` because nothing resumable was persisted. PR3 adds a **server-owned, durable session lifecycle** so the server can truthfully answer whether a session started, is active, is safely resumable, was completed/abandoned, or has gone stale — **without** redesigning the UI, plan storage, or set logging.

### Design (as built)

- **Model** `app/models.py::WorkoutSession` — int PK `id` (never exposed) + opaque `public_id` (`secrets.token_urlsafe`, unique) for all API exposure; `user_id` (FK CASCADE, indexed), `status` (`active|completed|abandoned` + `CheckConstraint`), `workout_date` (ISO Istanbul **start** day — context not identity), `weekday_slot`, `source` (`scheduled|unscheduled`), `planned_training_plan_id` (**plain Integer soft reference, NOT a hard FK**), `plan_fingerprint` (versioned `v1:<sha256>`), `started_at`, `last_activity_at`, `completed_at?`, `abandoned_at?`, `terminal_reason?`, `version` (terminal-transition version), `created_at`, `updated_at`. Added to `app/cli.py::_user_child_models` (cascade-delete introspection).
- **Active-owner invariant (single atomic claim):** partial unique index `uq_workout_session_active_owner` on `user_id WHERE status='active'` (SQLite ≥3.8 + PostgreSQL). `is_active_session_owner_violation(exc)` classifies that `IntegrityError`; any other integrity error re-raises (fail-closed).
- **Service** `app/services/workout_session/` — pure/impure split (`models.py`=frozen commands/results + `SessionOutcome` enum + pure classification, no ORM/Flask; `queries.py`=DB; `service.py`=transaction ownership; `__init__.py`=public API).
- **Lifecycle:** `ACTIVE / COMPLETED / ABANDONED`; stale is a **derived** condition of ACTIVE, never a persisted status. Terminal→terminal immutable. No PAUSED. Reads never mutate.

### Six mandatory corrections — how each is satisfied

1. **Contract version is flag-conditional.** Flag OFF ⇒ `resolve_workout_state` returns the exact PR1 `contract_version=1` snapshot (identical key set / enum vocabulary / legacy fields, no `session` keys, `resume`/`in_progress` never emitted). Flag ON ⇒ additive `contract_version=2` (new `session_state`, `session`; additive `action=resume`, `execution_state/primary_state=in_progress` — producible only from a persisted *eligible* ACTIVE session). Pure projection `resolver.enrich_with_session(base, facts)` never mutates the v1 base. Strict snapshot tests for **both** modes.
2. **Heartbeat replay-idempotent.** `checkpoint_session` is a lock-free conditional `UPDATE … WHERE status='active' AND last_activity_at < cutoff`, touching **only** `last_activity_at`, coalescing within `HEARTBEAT_COALESCE_SECONDS=30`. No client version, no progress blob; a retried heartbeat never returns a false conflict and never touches identity/start/ownership/status. Row-lock/`version` reserved for terminal transitions only.
3. **Explicit completion↔session reconciliation.** `CompleteWorkoutCommand.session_id` (additive, default `None` = unchanged legacy path). **Fixed lock order** (session row first via `lock_session_for_completion` `FOR UPDATE`, then artifacts) on both create and reconcile. Every path that ends the day completed terminalizes the owned matching ACTIVE session (`mark_session_completed`, conditional on `active`): fresh `CREATED`, preflight replay, and the `uq_pump_check_day` race-loser (`_reconcile_session_after_race`, fresh artifact-free txn). No duplicated PumpCheck/marker/XP/quest/challenge/activity/notification; a matching session is never left permanently ACTIVE; COMPLETED is never written without PumpCheck authority; an ABANDONED session ⇒ `SessionCompletionConflict` (rolled back, no artifacts).
4. **Versioned fingerprint.** `plan_fingerprint = v1:<sha256hex>` over ordered casefolded exercise names of the session's `weekday_slot`, computed server-side, never client-supplied. `fingerprints_match` returns `None` on a version/algorithm mismatch ⇒ relationship `indeterminate` (safe), never a silent match.
5. **Fail-closed migration.** `migrations/versions/a994f9bed783_add_workout_session_sprint_7_pr3.py` (down_revision `bb88cc99dd00`, single new head). Verify-or-create — does not blanket-skip when the table exists; inspects and creates each missing required object; raises `RuntimeError` on an incompatible existing table. Downgrade drops indexes + table.
6. **No inactivity-based staleness.** Stale derives only from concrete lifecycle/relationship evidence (previous local day, plan missing/regenerated/replaced, schedule-slot changed, lifecycle/completion inconsistency, indeterminate relationship). `last_activity_at` is heartbeat/observability only and never gates a same-day resume.

### Public operations & outcomes

`start_session`, `get_current_session`, `read_session_for_state`, `resume_session`, `checkpoint_session`, `abandon_session`, `resolve_for_completion`, `complete_session`. `SessionOutcome ∈ {CREATED, EXISTING_ACTIVE, RESUMED, CHECKPOINTED, ABANDONED, COMPLETED, ALREADY_COMPLETED, ALREADY_ABANDONED, STALE_SESSION_REQUIRES_RESOLUTION, CONFLICT, NOT_FOUND, INVALID_TRANSITION}`.

### API routes

`@require_auth`, `current_user.id` server-side, no client-supplied `user_id`/`status`/timestamps/`version`. `POST /workout/session/start`, `GET /workout/session/current`, `POST /workout/session/<public_id>/{resume,checkpoint,abandon}`, and the extended `POST /workout/complete` (optional session `public_id` → ownership-resolve → internal id → `session_id`; absent ⇒ legacy). Session routes gated by the flag — **OFF ⇒ 404 (inert)**. The flag is a rollout gate, not an auth gate. HTTP map: `CREATED`→201, active/resumed/checkpointed/abandoned/completed/already_*→200, stale/conflict/invalid_transition→409, not_found→404.

### Feature flag / rollout / rollback

`FITX_WORKOUT_SESSIONS_ENABLED` (default `False`, `app/config.py`, single owner). OFF ⇒ routes inert + resolver byte-identical to PR1/PR2. ON ⇒ full lifecycle + `contract_version=2`. Disabling after sessions exist is safe (persisted sessions ignored by the read contract, never deleted). **Not enabled in prod in this PR.** Removal criteria: once the UI consumer ships and soaks, the flag + OFF branch retire.

### Tests and exact results

**Command:** `python -m pytest -q -p no:cacheprovider` (canonical; `pytest.ini` applies `-m "not load"`).

**Method note:** this environment's background-job wall-clock cap kills a single serial full run (same constraint documented for PR2). The suite (134 test files) was therefore run as **four file-partition chunks** (`files[i::4]`), plus `tests/test_mcp_gate.py` executed **in isolation** (its subprocess-spawn test times out under parallel CPU contention — a pre-existing, documented artifact, not a regression).

- **Full baseline** (pristine `307b7b5`, captured pre-work): **2416 passed, 1 skipped, 3 deselected, 0 failed** (the 1 pre-existing skip is `test_workout_completion_pg.py`'s opt-in PG test; the 3 deselected are the `-m "not load"` load tests).
- **Full final** (PR3, current HEAD): **2524 passed, 4 skipped, 3 deselected, 0 failed, 0 errors.** The partitioned execution (four `files[i::4]` chunks + isolated `test_mcp_gate.py`) was captured with the PG test file at 2 opt-in tests and totalled 2524 passed / 3 skipped; a **third** opt-in PG test was then added to strengthen coverage (§ reconciliation race), so the current tree's default run is **2524 passed / 4 skipped** — the +1 is purely the added opt-in skip (`tests/test_workout_session_pg.py` now reports **3 skipped** in a default run, re-verified directly; passed count unchanged because opt-in PG tests never run without the env). The 3 deselected load tests are unchanged.
- **Test-by-test (node-level, not arithmetic):** a true collected-node manifest was produced at both the PR2 base (`307b7b5`) and PR3 HEAD and diffed set-wise. Baseline **2417** selected nodes → final **2528** selected nodes. Diff: **1 removed, 112 added.** The single removed node is a **rename** (`test_contract_has_no_resume_action` → `test_v1_contract_never_offers_resume_action`) whose target is in the added set and still passes — **not a deletion**. The 112 added = rename-target (1) + `test_training_routes.py` 14 + `test_workout_completion.py` 8 + `test_workout_session.py` 67 + `test_workout_session_pg.py` 3 + `test_workout_state_sessions.py` 19 = **111 genuinely new nodes** (of which 3 are the opt-in PG skips). No pre-existing node changed pass→fail or pass→skip; nothing was silently dropped.
- **Two pre-existing tests deliberately updated for PR3 reality** (both still pass): `tests/test_migration_graph.py` single-head assertion `bb88cc99dd00`→`a994f9bed783`; `tests/test_workout_state.py::test_contract_has_no_resume_action` → `test_v1_contract_never_offers_resume_action` (the `ACTION_RESUME` constant now exists but is a v2-only value the pure v1 resolver never emits and is not in the v1 action alphabet).
- **New skipped:** `tests/test_workout_session_pg.py` (3, opt-in `pg_concurrency` — skip cleanly without the env). Total default skips = 4 (3 PR3 + 1 pre-existing `test_workout_completion_pg.py`).

New/updated test files: `tests/test_workout_session.py` (67 — model, active-owner invariant, start/resume/checkpoint/abandon, ownership isolation, heartbeat replay-idempotency, stale/relationship classification, versioned-fingerprint mismatch, fault-injection rollback, timezone, 4 fail-closed migration tests), `tests/test_workout_session_pg.py` (3 opt-in PG — concurrent-start, complete-vs-abandon, concurrent-completion reconciliation), `tests/test_workout_state_sessions.py` (19 — flag-conditional v2 contract, both modes, pure enrich matrix + service flag on/off), `tests/test_workout_completion.py` (+8 reconciliation), `tests/test_training_routes.py` (+14 session routes flag on/off + envelope + ownership + complete-with-session), `tests/test_workout_state.py` + `tests/test_migration_graph.py` (updated).

#### Test-suite reconciliation (collected-node manifest)

Per the review's explicit requirement to not rely on arithmetic totals, the canonical full-suite manifest was reconciled against the executed partitions:

- **Canonical collect** (`pytest --collect-only -q` with default `-m "not load"`): **2528** selected node IDs, zero duplicates. Including the deselected load file it is **2531** (2528 + 3 `tests/load/test_ai_load.py` nodes; the load marker is the only deselection).
- **Partition completeness + disjointness (file-level, exact):** the 4 chunks carried **34 / 33 / 33 / 33** file-path args (`files[i::4]`), **pairwise disjoint** (0 cross-chunk overlap), and `tests/test_mcp_gate.py` was in **none** of them (run isolated). The union of chunk files covers **every** canonical node-producing file (0 missing); the only executed file contributing 0 selected nodes is `tests/load/test_ai_load.py` (fully deselected). Because a partition runs whole files and the only deselection (`-m "not load"`) is identical to the canonical collect, the executed node union equals the 2528-node canonical manifest **node-for-node**.
- **Exact skip reasons (4):** `test_workout_completion_pg.py::test_concurrent_completion_has_single_winner_on_postgres` (1, pre-existing, in chunk3) and `test_workout_session_pg.py::{concurrent_start, complete_vs_abandon, concurrent_completion}` (3, PR3, in chunk1) — all skipped by the `pg_concurrency` skipif because `FITX_PG_CONCURRENCY_TEST`/`PG_TEST_DATABASE_URL` are unset. **Exact deselection reason (3):** the `load` marker via `pytest.ini addopts = -m "not load"` (`tests/load/test_ai_load.py`).

### Migration / database

Single new head `a994f9bed783` off `bb88cc99dd00`; verify-or-create, fail-closed; downgrade drops the table. Fresh-DB create, verify-or-create idempotency, incompatible-table `RuntimeError`, and downgrade are unit-tested (`tests/test_workout_session.py`). Boot order (`create_all`→stamp→upgrade) is safe because the migration is verify-or-create rather than blanket-skip.

**CLI verification (local, disposable scratch SQLite — prod never touched; `.env` absent in the worktree and `load_dotenv()` does not override the exported scratch `DATABASE_URL`, confirmed by printing the resolved URI):** `flask db upgrade` ran the full chain to `a994f9bed783 (head)` and created `workout_session` with all four indexes (`ix_workout_session_user_id`, `ix_workout_session_user_status`, `uq_workout_session_active_owner`, `uq_workout_session_public_id`); `flask db downgrade bb88cc99dd00` dropped the table cleanly. `flask db check` at head reports drift, but it contains **zero `workout_session` references** (grep count 0) — it is entirely **pre-existing SQLite-vs-model divergence** (models define `pump_check_comment`/`pump_check_like`, which only `create_all` builds, plus Postgres-only `JSONB` columns and `CASCADE` FKs the SQLite reflection can't match). The CI `migration-drift` gate runs on **PostgreSQL 16**, where these artifacts don't appear; PR3's table is absent from even the noisier SQLite drift, so PR3 introduces **no** new drift on any dialect.

**PostgreSQL 16 migration validation (executed 2026-07-25, disposable `postgres:16` 16.14 — the CI drift dialect):**
- `flask db upgrade` ran the chain to `a994f9bed783 (head)` and created `workout_session` with the expected columns, the `status` `CheckConstraint` (`active|completed|abandoned`), `public_id` uniqueness, and **all four indexes**. The active-owner index was verified on PG as `UNIQUE btree (user_id) WHERE status::text = 'active'::text` — the **partial** predicate is present (not a plain unique index).
- **Drift check clean:** `flask db check` at head reported **no new upgrade operations** for `workout_session` (the two Alembic log mentions were SERIAL-sequence detection lines, not drift ops).
- **Terminal sessions don't block a new active:** inserting COMPLETED + ABANDONED rows for a user then a fresh ACTIVE succeeded (partial index ignores terminal rows); a **second** ACTIVE for the same user raised `IntegrityError`, correctly classified by `is_active_session_owner_violation`.
- **Downgrade → re-upgrade** per policy: `flask db downgrade bb88cc99dd00` dropped the indexes + table cleanly; re-`upgrade` recreated them.
- **Fail-closed existing-table / boot `create_all` path:** with an incompatible pre-existing `workout_session` table, the verify-or-create migration raised `RuntimeError` and left the revision at `bb88cc99dd00` (it did **not** silently report success). The `create_all`→stamp→upgrade boot order is therefore safe because the migration is verify-or-create, not blanket-skip.

### Opt-in Postgres concurrency proof — EXECUTED

`tests/test_workout_session_pg.py` (mirrors `test_workout_completion_pg.py`): two threads + `threading.Barrier` + per-thread app context (independent connection/session) + strong `User` ref, gated by `@pytest.mark.pg_concurrency` + `FITX_PG_CONCURRENCY_TEST=1` + `PG_TEST_DATABASE_URL`. **Executed 2026-07-25 against a real disposable PostgreSQL 16 — all three tests PASSED (3/3, re-run for flakiness).**

**Environment (disposable, isolated, prod never touched):**
- **Image** `postgres:16` @ `sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20`; **server version 16.14 (Debian 16.14-1.pgdg13+1)**.
- Container `fitx_pg_pr3`, **loopback-only** bind `127.0.0.1:55433→5432` (never exposed off-host), throwaway role/db (credentials redacted; not the prod `.env` — the PG URL was passed only via `PG_TEST_DATABASE_URL`/`DATABASE_URL` in the test env with `FITX_SKIP_DB_INIT=1`). Each test `_make_pg_app()` does its own `drop_all`/`create_all` and tears the schema down after; the container + credential were destroyed at the end of the session.
- Confirmed **no prod/shared DB** referenced: the resolved URI was the loopback disposable throughout; `.env` is absent in the worktree and `load_dotenv()` does not override the exported test URL.

**Results (all PASSED):**
- `test_concurrent_start_yields_single_active_on_postgres` — two simultaneous `start_session` ⇒ exactly one `CREATED` + one `EXISTING_ACTIVE`, exactly one ACTIVE row (the `uq_workout_session_active_owner` partial unique index is the atomic claim; the loser is a deterministic idempotent replay).
- `test_complete_versus_abandon_only_one_terminal_wins_on_postgres` — `complete` vs `abandon` on the same active session ⇒ exactly one terminal state, never both, never still ACTIVE. When the winner is COMPLETED: exactly **1** PumpCheck + **1** marker + **1** `workout_completed` Activity, `completed_at` set, `version==2`. When ABANDONED: **0** completion artifacts.
- `test_concurrent_session_completion_no_duplicate_artifacts_on_postgres` (added this session) — two contenders complete the **same** session at once ⇒ exactly one `COMPLETED` + one `ALREADY_COMPLETED`; the `uq_pump_check_day` race-loser reconciles the owned session ACTIVE→COMPLETED in a fresh artifact-free txn. Session COMPLETED with `version==2` (terminalized exactly once, no double-bump) and **exactly 1** PumpCheck / marker / Activity — the multi-connection reconciliation invariant SQLite cannot exercise. No poisoned transaction, no partial rows.

Run: `FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://…@127.0.0.1:55433/… python -m pytest -m pg_concurrency tests/test_workout_session_pg.py -q`. The SQLite default suite already enforces the `uq_workout_session_active_owner` partial unique index (`test_partial_index_forbids_two_active_sessions`) and the terminalization/reconciliation invariants deterministically; these PG tests add the genuine multi-connection proof and still skip cleanly for reviewers without Docker.

### Files changed

- **Created:** `app/services/workout_session/{__init__,models,queries,service}.py`; `migrations/versions/a994f9bed783_add_workout_session_sprint_7_pr3.py`; `tests/test_workout_session.py`; `tests/test_workout_session_pg.py`; `tests/test_workout_state_sessions.py`.
- **Modified:** `app/models.py` (WorkoutSession + active-owner index + violation helper); `app/cli.py` (`_user_child_models`); `app/config.py` (flag); `app/blueprints/training.py` (5 session routes + `/workout/complete` session linkage); `app/services/workout_completion/{models,queries,service,__init__}.py` (session_id + reconciliation + fixed lock order); `app/services/workout_state/{__init__,models,queries,resolver}.py` (flag-conditional v2 enrichment); `tests/test_workout_completion.py`; `tests/test_training_routes.py`; `tests/test_workout_state.py`; `tests/test_migration_graph.py`; `docs/WORKOUT_STATE.md`; `CLAUDE.md`; `docs/handoff.md`.
- **Deleted:** none.

### Authorization boundary

The branch was pushed and **PR #186** opened against `main` under explicit user authorization, then rebased onto current `main` (past #185, resolving two trivial both-appended conflicts in `app/config.py` and this file). **Nothing merged, deployed, or production-flag/DB-changed; Sprint 7 PR4 not started.** Work confined to the `sprint7-pr3-workout-session-lifecycle` worktree; no other worktree's files absorbed.

### Deferred (record only — not implemented)

Stable plan-day identifier linkage (session↔plan soft reference; no set-level restoration — checkpoint is a lifecycle heartbeat), workout UI/nav redesign, offline sync, `TrainingPlan` schema redesign, historical `WorkoutLog` backfill, `/api/progress/workout` + `renderHero` convergence, automated destructive stale cleanup, **Sprint 7 PR4**.

## Sprint 7 PR4 — Workout-State Consumer Convergence

- **Track:** Core Feature. **Sprint:** 7. **PR:** 4.
- **Status:** **integrated onto latest `main` and re-validated (2026-07-31)**; independent post-integration diff review clean; PR #190 pushed with `--force-with-lease` (no merge / no deploy / no migration / no prod flag change).
- **Branch:** `sprint7-pr4-workout-state-convergence`.
- **Worktree:** `.worktrees/sprint7-pr4-workout-state-convergence`.
- **Current base:** `989d1e7d713f0e99e5b3a837d6b9549872f174e0` (`origin/main`, "feat: add native mobile authentication foundation" #191). Sprint 7 PR1 `3d9c582` #183, PR2 `307b7b5` #184, PR3 `8c486de` #186 remain merged ancestors. **PR4-only diff range:** `989d1e7..HEAD`.
- **Original frozen base:** `1e76e1374d1a8a61b4a44ceb1a763e9c2758061c` (#188). At the 2026-07-26 closeout the base was deliberately **not** rebased so the workout-state PR would not silently absorb unrelated UIUX work; the integration below performs that rebase explicitly, as its own reviewed step.

### Latest-main integration (2026-07-31)

**Integrated upstream.** `origin/main` had advanced two commits past the frozen
base `1e76e13`: `cab7c27` ("flag-gated Plan v2 + Coach sayfası v2", UIUX Sprint 1
PR3, #189) and `989d1e7` ("native mobile authentication foundation", #191).
`989d1e7` is the latest verified `origin/main` and the **new PR4 base**. PR1/PR2/PR3
are still ancestors (`git merge-base --is-ancestor` holds for `3d9c582`,
`307b7b5`, `8c486de`).

**Strategy: rebase, not merge.** `main` carries only single-parent squash merges
and PR1–PR3 each rebased onto their base, so the governed strategy is
`git rebase origin/main` with `rebase.autostash`. New PR4 commits:
`472dd74` (implementation) → `3ab7291` (tests) → `6661005` (docs/closeout), plus
this integration commit. A safety tag `pr4-pre-integration-backup` pins the
pre-rebase head `f7f165b`.

**Overlap inspected before rewriting history.** The upstream delta touches
exactly four PR4 files and nothing else — no route, blueprint-prefix, template,
locale-key, config, or migration collision. `989d1e7` registers
`app/blueprints/mobile_api.py` under `/api/v1`, which cannot collide with PR4's
`/training/bootstrap`; its new request hooks are all `before_request` and
blueprint-scoped to `mobile_api`, so they never wrap PR4's mid-request
`db.session.remove()` boundary, and `log_request` keeps its pre-existing
try/except on the non-mobile path. `989d1e7` also adds migration
`c7d8e9f0a1b2_add_mobile_auth_sessions`; PR4 adds no migration, so the graph
keeps a single head.

| Overlap file | Conflict | Resolution |
|---|---|---|
| `app/blueprints/training.py` | none (auto-merged) | Both preserved: UIUX's `build_plan_view`/`gather_plan_facts` imports and the flag-gated `plan.html` swap inside `/training`, and PR4's `/training/bootstrap` + `workout_state_payload` on `/workout/status`. PR4's contribution is byte-identical to the pre-rebase diff apart from line offsets. |
| `CLAUDE.md` | none (auto-merged) | UIUX Sprint 1 PR3 bullet and the PR4 convergence bullet both retained; PR1 bullet keeps its "PR4'te bu kanonik yola CONVERGE edildi" update. |
| `docs/handoff.md` | none (auto-merged) | Both handoff sections retained (UIUX PR3 at its own heading, Sprint 7 PR4 here). |
| `tests/test_training_ui.py` | **one real conflict** | Both sides appended imports and tests. Resolved to the **exact union** of both test sets — nothing dropped, nothing invented — and the auto-merge's duplicated import block consolidated into one ordered block that reuses `STATIC` for `TRAINING_SCRIPT`/`WORKOUT_STATE_CLIENT` (provably identical paths). Final inventory is 5 nodes: `test_training_renders_hero_and_session`, `test_training_loads_external_assets`, `test_wstats_collapses_to_fewer_columns_on_narrow_screens` (UIUX), `test_training_abandon_label_follows_authenticated_locale` (PR4), `test_blocked_training_state_never_falls_back_to_the_setup_form` (PR4). |

No conflict was resolved by taking one side wholesale, and no test, route, locale
key, documentation section, or feature-flag behavior was removed.

**Files touched by the integration itself:** `tests/test_training_ui.py`
(conflict resolution), `docs/handoff.md`, `docs/WORKOUT_STATE.md`,
`docs/frontend-readiness/sprint-7-pr4/browser-validation.json` (regenerated),
`docs/frontend-readiness/sprint-7-pr4/test-partition.json` (supersession
pointer only), and the new
`docs/frontend-readiness/sprint-7-pr4/test-partition-latest-main.json`. Full PR4
diff versus the new base: 25 files, 20,691 insertions, 118 deletions (24 files /
17,244 insertions before this integration commit added the new evidence
artifact). `.claude/settings.local.json` is a local Claude Code permission file,
was never staged, and stays out of every commit.

**Behavioural change inherited from the integration (recorded, not a PR4
change).** UIUX PR3 added `@media (max-width: 380px) { .wstats { 2 columns } }`
to `static/training.css`. The re-run browser matrix therefore records
`horizontal_overflow: false` at 320/360/375 where the pre-integration artifact
recorded `true` (12 cells). 390 px still records `true`, exactly as before the
integration and by UIUX PR3's explicit design ("the 390px+ layout is
unchanged") — a pre-existing main-side observation that PR4 neither causes nor
regresses. `tests/test_training_ui.py::test_wstats_collapses_to_fewer_columns_on_narrow_screens`,
preserved through the conflict resolution, guards the collapse.

### Post-integration validation

All commands were run in the integrated worktree at
`6661005` + this docs commit, base `989d1e7`.

- **Focused backend (25 files):** `python -m pytest -q -p no:cacheprovider -rs` over workout convergence/state/session/completion (+ the two `*_pg` gate files), training routes/UI/characterization, training history, progress API/UI, barcode (+ workflow), coach routes/tools/AI/adaptive context, i18n, Plan v2, Coach page v2, weekly-program flag/route, and mobile-auth feature gate → **660 passed, 4 skipped, 0 failed** in 147.00 s. The four skips are the opt-in `pg_concurrency` nodes, each printing its gate reason.
- **JavaScript:** `node --test tests/js/workout_state_client.test.js` → **17/17 pass**. (`node --test tests/js/` cannot be used on Windows — Node treats the directory as a module and raises `MODULE_NOT_FOUND`.)
- **Static / AST / structural:** `python -m compileall` over `app tests scripts fitx_mcp migrations starter.py` → exit 0; `node --check` over every file in `static/` → clean; `tests/test_dependency_boundaries.py tests/test_migration_graph.py tests/test_cascade_delete.py tests/test_env_example.py` → **64 passed**.
- **Migration head:** single head `c7d8e9f0a1b2` (main's mobile-auth revision). PR4 adds no migration and no model change.
- **Import/startup + feature-flag smoke:** hermetic double boot of `create_app()` with a purged module cache — `FITX_WORKOUT_SESSIONS_ENABLED` unset → config `False`, `=1` → `True`; `/training/bootstrap` is registered and auth-gated (302) in both; identical 141-route map in both.
- **PostgreSQL 16 opt-in concurrency (PR2/PR3 harness):** a disposable `postgres:16` container (**PostgreSQL 16.14**) bound to `127.0.0.1:55434` only, throwaway role/database, ephemeral credential passed solely through `PG_TEST_DATABASE_URL`, container **and** credential destroyed afterwards. `FITX_SKIP_DB_INIT=1 FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=… python -m pytest -m pg_concurrency -q -p no:cacheprovider -rs` → **5 passed, 0 failed** in 19.99 s. Five, not four: with the gate variable set, `origin/main`'s `tests/test_mobile_auth_pg.py` stops skipping at module level and collects one additional race test.
- **Whitespace:** `git diff --check` and `git diff --cached --check` clean; no untracked files besides the intended new evidence artifact.

### Reconciled repository suite

The frozen-base run stays in
`docs/frontend-readiness/sprint-7-pr4/test-partition.json`. The regenerated
post-integration manifest, partitions, node lists, per-partition exit codes,
skip reasons, and delta proof are in
`docs/frontend-readiness/sprint-7-pr4/test-partition-latest-main.json`.

- Default collection: **2,786 selected**; unfiltered: **2,789**; the three-node difference is `tests/load/test_ai_load.py` deselected by `pytest.ini addopts = -m "not load"` (node IDs recorded).
- New canonical selected-node SHA-256: **`16b1be322d56edbef0e5a690c494e56a375ac6199bfc19b3b0aa49c8a1dec360`** (frozen-base hash was `40ab2557…` over 2,581 nodes).
- Partitioning: MCP gate nodes isolated (they bind sockets); remainder split into 8 contiguous balanced partitions (7×348 + 1×347) executed from `@args` files.
- Proof: `assigned 2786 / unique 2786`, `missing 0`, `extra 0`, `duplicate_assignment 0`, `union_equals_selected true`, `disjoint true`, `executed_selected_equals_manifest true`, `all_partition_exit_codes_zero true`.
- Execution: **2,782 passed, 4 skipped, 0 failed, 0 errors**, every partition exit code 0. The four skips are the opt-in `pg_concurrency` nodes, all of which were executed separately against the real PostgreSQL 16.14 above.
- **Nothing was lost.** Against the frozen-base manifest the delta is +206 / −1. All 206 additions are attributed: 205 to `origin/main` (mobile-auth `#191` test files, Plan v2 29, Coach page v2 24, `test_env_example` 12, and smaller cognito/auth/session additions) and 1 to `tests/test_training_ui.py::test_wstats_collapses_to_fewer_columns_on_narrow_screens`, whose file both sides touched. The single removal is `tests/test_cognito_jwt.py::test_refetch_jwks_unavailable_reason_preserved`, which **`origin/main` itself renamed** to `test_matching_kid_signature_failure_is_definitive` (and inverted, because `989d1e7` made a matching-kid signature failure definitive with no re-fetch). `git log 989d1e7..HEAD -- tests/test_cognito_jwt.py` is empty: PR4 never touches that file. No PR4 test was lost, renamed, or disabled by the rebase.

### Contract and ownership result

- `GET /training/bootstrap` is authenticated, current-user scoped, and `Cache-Control: private, no-store`. It resolves the Istanbul date, session flag, newest active plan, and workout/session snapshot coherently, passing the same plan/date into strict canonical resolution.
- Bootstrap returns one public snapshot only after every required read and serialization succeeds. A plan read, malformed JSON/schedule, strict session read, canonical resolution, or bounds failure returns the generic localized `bootstrap_unavailable` envelope with HTTP 500—never partial contradictory data, raw exceptions, database identifiers, or sensitive content.
- No-plan, no-session, active, completed, blocked, and unavailable states are represented honestly. A missing persisted session never fabricates resume.
- The full-week and `today_plan` projections are closed and deterministic. `today_plan` is limited to `gun`, `tip`, `odak`, `sure_dk` 0..1440, `tahmini_kalori` 0..900, and at most 50 exercises; exercise keys and integer/text bounds are enforced, unknown/internal fields dropped. Legacy list and wrapped `{"program": [...]}` storage shapes remain supported.
- The same pure workout envelope serializer feeds bootstrap, `/workout/status`, and Progress's additive `current`. Barcode and Coach resolve current state once through the same canonical service.
- No migration, model, new feature flag, cache, offline queue, plan-schema redesign, or durable set/rep storage was added.

### Consumer ownership matrix

| Consumer | Canonical fields | Canonical source | Refresh trigger | Mutation owner | Failure/fallback | Independent authority removed |
|---|---|---|---|---|---|---|
| Training | `completed`, all `workout.state` fields, `session`, `plan`, `today_plan` | ordered `/training/bootstrap` backed by PR1/PR3 | load, focus/visibility return, every mutation settlement | PR2 completion and PR3 session endpoints | blocked fail-closed UI; no cached truth | no localStorage/local-date/DOM/POST inference of completion, active session, workout date, current plan, or success |
| Progress | `current.completed`, `current.state`; historical `days`/`totals` unchanged | shared canonical envelope in `/api/progress/workout` | each request/navigation refresh | none | history may render; current state is never guessed | no reconstruction from historical rows |
| Barcode | `completed_today` | one canonical resolver call per barcode context | context build | none | safe canonical unavailable/false; nutrition context unchanged | no marker/log inference of completion or other current state |
| Coach | compact canonical current snapshot plus unchanged historical context | one canonical resolver call per Coach context | context build/request | none | history remains; unavailable is explicit | no history/prompt-local inference of completion, session, date, plan, or mutation success |

Historical heatmap, streak, analytics, and detailed set/rep/timer behavior keep
only historical or page-ephemeral ownership. Remaining legacy debt is explicit:
set/rep progress is not durable; heatmap/streak/analytics keep their historical
models; plan-to-log identifier linkage and set-level checkpoint restoration are
outside PR4.

### Refresh, mutation, and heartbeat lifecycle

- A monotonic generation plus `AbortController` prevents stale/superseded reads from applying or clearing newer state.
- Mutations are single-flight. A transport success never claims canonical success; every settled mutation orders an authoritative bootstrap refresh, including failures.
- Initialization and teardown are idempotent. `pagehide`/navigation removes listeners, aborts controllers, and clears timers.
- Exactly one visible-page 60-second heartbeat exists only for an active v2 session. V1, inactive, blocked, completed, hidden, logged-out, terminal-error, and torn-down states have none.
- Checkpoint writes are single-flight. Failures never alter canonical workout state; terminal/auth-expiry responses stop the timer and trigger at most one ordered refresh. No unbounded retry or high-frequency polling was introduced. PR3's 30-second server coalescing remains unchanged.

### Reconciled repository suite — frozen base (historical, 2026-07-26)

Superseded by the post-integration run above; kept as the record of the
`1e76e13` base. Commands and every node/result are machine-readable in
`docs/frontend-readiness/sprint-7-pr4/test-partition.json`.

- Final default collection: 2,581 selected; unfiltered collection: 2,584; three `load`-marked nodes deselected by repository configuration.
- Selected-node SHA-256: `40ab2557b2c0bf51aa0f95d3a8e2bebd648e196e22823f762ef2e481bb37ce45`.
- Deterministic disjoint union: all 2,581 selected nodes assigned once, no omissions, extras, or duplicates.
- Execution: 2,577 passed, four opt-in PostgreSQL tests skipped, zero failed/errors, all partition exit codes 0. The 13-test closeout addendum is included in the final manifest and hash.

### Hermetic browser validation

<!-- PR4_BROWSER_RESULT_START -->
Re-run on the integrated tree (base `989d1e7`), 2026-07-31, under WSL
Ubuntu-24.04:
`PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/axisai-sprint0-playwright $HOME/axisai-sprint0-audit-venv/bin/python -m scripts.frontend_audit.workout_pr4_matrix`.

Result: **52/52 passed, zero failed/blocked, exit 0**; runner 79.529 seconds
(`failed_ids` and `blocked_ids` both empty, every cell `verdict: pass`). The 48
matrix cells covered no-plan, active, and completed Training states across
English/Turkish and all eight required viewports. Four special cells covered
bootstrap fail-closed behavior, browser navigation plus cross-consumer
consistency, ordered stale/mutation/heartbeat controller behavior, and the real
persisted-session heartbeat lifecycle.

Observed totals: zero page errors, zero hard failed requests, zero PR4 console
errors, and exactly one same-origin 5xx — the deliberate `bootstrap_unavailable`
500 inside the `bootstrap-fail-closed` cell. The 115 recorded console errors are
all hermetic-environment SRI blocks for `cdn.jsdelivr.net` marked/DOMPurify/
Chart.js plus that intended 500; external network is disabled, so
`external_blocked` also lists Google Fonts and GTM. Matrix cells made exactly
two bootstrap calls each (96 of the 103 total: initial load plus the audit
identity probe) with no on-load mutation. The controller cell recorded
`stale.applied = ["new"]` (superseded read discarded), mutation order
`POST → GET /training/bootstrap` twice with `post_count = bootstrap_count = 2`,
and heartbeat timers `1 → 1 (repeat init) → 0 (inactive)` with one checkpoint and
no remaining listeners. The real-session cell recorded one 60,000 ms timer, one
fired callback, one checkpoint request, and zero timers after teardown. Stale
`localStorage` (`fitx_workout_state = {"completed":true}` and
`fitx_workout_completed_2026-07-20 = true`) was present yet `hero_done` stayed
`false`, so cached values never controlled rendering; `authority_scan` reports
`local_storage_workout_authority_absent` and
`client_local_date_authority_absent` both true.

Machine-readable evidence:
`docs/frontend-readiness/sprint-7-pr4/browser-validation.json`.
<!-- PR4_BROWSER_RESULT_END -->

The approved harness configures the hermetic environment before importing
`app.*`, uses isolated SQLite and non-production integration settings, disables
external network access, and runs Chromium on Linux. Required locales are
English/Turkish; required viewports are 320x720, 360x800, 375x812, 390x844,
430x932, 768x1024, 1024x900, and 1440x900. Evidence records requests, same-origin
4xx/5xx, failed requests, console/page errors, canonical identities, duplicate
guards, timer counts, and interactions.

### Rollout, rollback, and authorization

Roll out with the existing `FITX_WORKOUT_SESSIONS_ENABLED` flag OFF; enable it
only in controlled non-production validation after all closeout gates pass.
Rollback is flag OFF for PR3 lifecycle calls and a PR4 code revert. No database
rollback is required. `UIUX_PLAN_V2_ENABLED` and `UIUX_COACH_PAGE_V2_ENABLED`
(inherited from `cab7c27`) also stay default OFF; PR4 does not read or change
them.

Authorization after the latest-main integration extends to the branch and its
pull request only: the branch is pushed with `--force-with-lease` (never an
unconditional force push) and PR #190 is kept up to date. Merging still requires
separate human authorization after review. Do not deploy, run migrations, access
production data, or change production feature flags.

### Remaining limitations (unchanged by the integration)

Set/rep/timer progress is page-memory-only; historical heatmap, streak, and
analytics keep their existing models; plan-to-log identifier linkage and durable
per-set checkpoint restoration remain outside PR4. `/training` still records
`horizontal_overflow: true` at 390 px in the browser artifact — pre-existing on
`main` and explicitly out of UIUX PR3's scope. Sprint 8–12 scope is untouched.

---

## Production Hardening PR1 — Runtime Metrics & Baseline Instrumentation

- **Track:** Production Activation & Hardening Sprint. **PR:** 1 of 5.
- **Status:** implemented; default OFF; no runtime behaviour change.
- **Branch:** `chore/prod-metrics-baseline`. **Base:** `95d94cd` (`origin/main`,
  "docs: triage report 2026-08-02" #194).
- **Rollback:** `RUNTIME_METRICS_ENABLED=0` in the host `.env` + restart the web
  service. That is also the merge-time default, so merging this PR changes nothing
  operationally until someone opts in.

### Why this PR exists and why it is first

The sprint brief (`prod-hardening.txt`) asks for controlled activation of latent
capabilities, bounded AI concurrency and measurable service objectives. Every one
of those needs numbers that do not exist yet. Before PR1 the only production
signal was a logfmt line per request and `FitX/AI`, which is default-OFF and wired
only to coach turns. There was no HTTP success rate, no latency percentile, no
DB-pool utilisation, no provider failure breakdown, and **no way to ask a running
instance which feature flags are ON** — because flags live in the host `.env`,
which the deploy pipeline never ships. So this PR lands first: nothing downstream
can claim an SLO, an abort threshold, or a "measured" Gunicorn setting without it.

### What was verified before writing code

Claims in the brief were checked against `origin/main`, not against docstrings:

| Brief's claim | Verified state |
|---|---|
| "Five default-OFF feature flags" | **Eight** backend flags plus one Dart compile-time flag in the Flutter client |
| AI calls lack timeouts / bounded retries | Already present — Bedrock `max_retries=1`, OpenAI `timeout=30`, per-turn 90 s budget, `ai_recovery` 2 jittered attempts |
| Gunicorn config is ad hoc | Already version-controlled in `gunicorn.conf.py` |
| Worker count is the lever | `ai_gate.enforce_gate_invariants` **raises at boot** outside dev when `FITX_WEB_WORKERS != 1` |

The genuine observability gaps — no HTTP SLIs, no pool visibility, no provider
failure categories, no flag introspection — are what PR1 closes.

### Design decision: buffer, do not emit inline

`put_metric_data` is a network call and the app serves on one worker with eight
threads. Emitting per request in `after_request` would add a round-trip to every
request and make a CloudWatch slowdown an application slowdown — it would degrade
the very latency it measures. Recording therefore writes to a process-local
buffer (dict update under a lock) and one daemon thread flushes every
`RUNTIME_METRICS_FLUSH_SECONDS` (default 60).

Accepted trade-offs, stated rather than hidden:

- a worker restart loses at most one unflushed window — metrics must never be
  more durable than the requests they measure;
- on a CloudWatch outage the buffer is still drained, so memory stays bounded;
- counters are per-window sums, so alarms must use `Sum`, not `Average`.

Latency uses `Values`/`Counts` over a fixed bucket ladder rather than
`StatisticValues`, because a StatisticSet cannot produce percentiles and §7 of the
brief requires p50/p95/p99.

### Components created

| File | Role |
|---|---|
| `app/services/runtime_metrics.py` | Buffered CloudWatch emitter for `FitX/Runtime`; counters, bucketed timings, gauges; total no-op when disabled |
| `tests/test_runtime_metrics.py` | 13 tests: OFF = no-op *and no buffering*, recording never touches the network, bucketing, ≤20 chunking, outage drains the buffer, dimension/PII safety |

### Modified files

| File | Change |
|---|---|
| `app/config.py` | `RUNTIME_METRICS_*` settings; `FEATURE_FLAG_KEYS` inventory + `feature_flag_state()`; `[FLAGS] enabled=…` boot log |
| `app/observability.py` | `client_class()`, `_status_class()`, `_record_request_metrics()`; `log_request` now also records HTTP SLIs |
| `app/services/ai_gate.py` | `_provider_error_category()`, `_measured_model_slot()`, `provider=` label on `model_concurrency_slot`, gate-rejection counter |
| `app/services/ai_recovery.py` | `_record_retry()` on the retry ladder |
| `app/__init__.py` | `_capacity_snapshot()`, `_record_capacity_gauges()`, `_record_dependency_gauges()`; `flags` + `capacity` in `/health?deep=1` |
| `.env.example`, `docs/OBSERVABILITY.md` | New settings, metric table, cardinality rules, SLI/SLO/alert tables |
| `tests/test_ai_gate.py`, `tests/test_health.py` | Gate/provider SLI tests; deep-health flag and capacity assertions |

### Deliberate design choices worth reviewing

1. **`ai_metrics.py` was not refactored.** `runtime_metrics` duplicates ~30 lines
   of boto3 transport instead of extracting a shared module. Existing tests
   monkeypatch `ai_metrics._get_client` / `_BOTO3_AVAILABLE`, the two modules have
   genuinely different lifecycles (per-event vs. buffered), and the brief forbids
   broad refactors. The duplication is the cheaper risk.
2. **503 and 500 are separate counters.** `HttpOverload` (503) is the AI gate
   working correctly under load; `HttpServerErrors` (5xx) is a defect. Collapsing
   them would page someone for healthy load shedding.
3. **`GeneratorExit` is classified `cancelled`, not `error`.** A user closing a
   streaming coach response is normal behaviour; counting it as a provider error
   would inflate the failure rate and mistrigger the timeout alarm.
4. **Zero-overhead OFF path.** `model_concurrency_slot` keeps its original single
   `acquire()` when metrics are disabled; the measured path (non-blocking probe →
   contention counter → blocking acquire) only runs when the flag is ON. Semaphore
   accept/reject semantics are identical either way — only "we had to wait"
   becomes visible. Bounding that wait is PR4's job, not PR1's.
5. **Client class is a server-side fact.** `web` vs `mobile` comes from
   `request.blueprint == "mobile_api"`, never a client header (brief §6). A test
   asserts a spoofed `X-Client: mobile` header still classifies as `web`.

### Known limitations

- Per-worker gauges (`DbPool*`, `RedisUp`) are sampled on `/health?deep=1`, so
  their resolution is the probe interval, not the flush interval.
- The SLO column in `docs/OBSERVABILITY.md` is **proposed**, not validated. No
  production baseline exists yet; the flag must run for a full weekly traffic
  cycle before any number there is treated as agreed.
- Gunicorn worker restarts/boot failures are not yet instrumented — that needs a
  Gunicorn server hook and belongs with PR4's capacity work.

### Verification

- `python -m pytest -q` — full suite green.
- `python -m pytest tests/test_runtime_metrics.py tests/test_ai_gate.py tests/test_health.py tests/test_observability.py tests/test_ai_metrics.py tests/test_env_example.py -q` — targeted green.
- Deep health from loopback returns `flags` + `capacity`; the shallow public body
  returns neither (asserted).
# Sprint 10 PR1 canonical Pump Check foundation (local, 2026-08-13)

- Canonical authority remains pump_check. Migration e9f0a1b2c3d4 is additive
  and performs no fabricated legacy backfill.
- Mobile surface is multipart POST /api/v1/pump-checks with required
  Idempotency-Key and owner-only GET /api/v1/pump-checks/<PumpCheckId>.
- Analysis contract pump-check-analysis/v1 is strict, bounded, plain text, and
  blocks false precision, medical claims, and prompt injection.
- S3 remains private; one-hour signed URLs require owner validation. Storage
  logs no longer include keys, buckets, or owner IDs.
- Local PostgreSQL is unavailable. Three opt-in real-service Pump Check race tests and CI
  PostgreSQL flask db check are the authoritative review conditions.
- Flutter is untouched. PR2 waits for reviewed, CI-green, merged PR1. PR3
  comparison and PR4 history/retention remain deferred.

# Triage fixes — PR #201 (2026-08-07) + PR #208 (2026-08-14) findings

Both PRs are documentation-only triage reports. This change closes the code
findings they raised. **PR #208's carried items #4–#7 were re-verified as
already fixed at HEAD** — that report used `NEEDED_FIXES_2026-08-02.md` as its
baseline and never saw the (still-unmerged) 08-07 report, so it re-listed work
PR #199/#200 had already landed:

| Carried item | State at `dc6fda1` | Evidence |
|---|---|---|
| #4 `ai_gate` unbounded `_model_slots` | Fixed | `model_concurrency_slot` acquires via `_acquire_before_deadline` and raises `BlockingConcurrencyLimit` (`ai_gate.py`) |
| #5 `mobile_auth.refresh()` lock across network | Fixed | Two-phase snapshot → network (asserts `not in_transaction()`) → re-lock (`mobile_auth.py:507-517`) |
| #6 `ProxyFix` trusts `X-Forwarded-Host/-Port` | Fixed | `ProxyFix(..., x_host=0, x_port=0)` (`config.py`) |
| #7 OpenAI non-stream turn budget | Fixed | `_coach_turn_deadline` / `_remaining_coach_turn_seconds` (`ai_coach.py:932-1084`) |

## Fixed here

1. **`weekly_water` completable in one day** (201 #1 / 208 #1, the only Medium).
   The funnel gate read the user-resettable `WaterLog.count`, so a `5 → 0 → 5`
   toggle re-armed the `0 → positive` transition and drove the "5 different
   days" challenge to completion in one afternoon. The gate is now a durable
   per-day marker, `WaterLog.quest_fired` (migration `f0a1b2c3d4e5`, additive
   with an existence guard for the `create_all` boot path), claimed by a
   conditional `UPDATE ... WHERE quest_fired = false`. `uq_user_water_day`
   already makes the row unique per user+day, so that UPDATE is the single
   atomic claim — concurrent POSTs cannot both fire, and no extra lock is
   needed. A genuinely new day gets a new row, so the funnel still fires daily;
   `count=0` never consumes the marker.
2. **Ungated FatSecret surfaces** (201 #2). The slot now wraps the network
   round-trip in `barcode.get_barcode_product` (covers every caller), in
   `food_servings` / `food_servings_by_name`, and in the three
   `mobile_food_discovery` entry points — that mobile surface landed in #205,
   after the 08-07 audit, and carried the identical pattern. The permit wraps
   **only** the provider call: barcode cache hits stay pure DB and never
   consume capacity, and the cache write commits outside the permit. Saturation
   returns `503` + `Retry-After`, never a fabricated "not found" — on the
   mobile side it surfaces through the existing `FOOD_PROVIDER_UNAVAILABLE`.
3. **`/chat` unbounded input** (208 #2). `/chat` interpolated `message` straight
   into a Sonnet prompt with no cap while `/ask*` enforced `MAX_QUESTION_CHARS`;
   rate limits bound request *count*, not per-request token *cost*. `/chat` now
   applies the same cap (and rejects non-string values, which `len()` would
   otherwise mis-measure) before any model call. A global
   `MAX_CONTENT_LENGTH` (12 MiB, `MAX_CONTENT_LENGTH_BYTES`) sits above the
   largest legitimate upload (8 MB pump-check data URL); per-field validators
   remain the canonical limits. 413 returns JSON, and `mobile_api` maps it to
   `REQUEST_TOO_LARGE` with `retryable=False` — its generic handler would
   otherwise have reported a permanent client error as a retryable outage.
4. **`award_badge` deferred flush** (208 #3, latent). The insert now runs in its
   own savepoint and flushes immediately, so a duplicate `uq_user_badge` fails
   locally instead of at the caller's `begin_nested` exit — where it would have
   rolled back `_try_complete`'s guarded `completed_at` UPDATE and left the
   challenge in a permanent retry loop.

## Not fixed (deliberate)

- **God-modules** (201 #3 / 208 #8): `social.py` (1191), `ai_coach.py` (1247),
  `tracking.py` (728). Both reports classify this as tech debt with no
  behavioral defect and recommend *incremental* extraction; splitting these
  alongside behavioral fixes would move the monkeypatch/test surface for
  unrelated code in the same change. Left for a dedicated PR.
- **201 #4** (duplicate Istanbul-date helper in `coach_context_queries.py`) —
  the report itself marks it "no action required": functionally equivalent and
  deliberately Flask/SQLAlchemy-free for the psycopg2 read path.
- **208 informational notes** (`nbf`/`iat` not validated, menu-fetch exception
  *class* name, `/health?deep=1` outbound GET, `_pin_getaddrinfo` global
  monkeypatch) — all verified non-exploitable by that report; each would change
  a security-reviewed boundary for no measured gain.

---

# Adaptive Coaching Sprint 1 PR1 — Canonical Plan Mutation Foundation (local, 2026-08-14)

Branch `adaptive-coaching-s1-pr1-plan-mutation-foundation`, worktree
`.worktrees/adaptive-coaching-s1-pr1-plan-mutation-foundation`, based on
`origin/main` `c38a8d9`. Local commit only — **not pushed, no PR, not merged,
not deployed.** Sprint 1 PR2 stays deferred until this is independently
reviewed.

Full architecture: **docs/ADAPTIVE_COACHING.md**.

## What shipped

`app/services/plan_mutation/` — the one server-authoritative, typed, targeted
mutation boundary over the canonical `TrainingPlan`. Five commands (replace /
add / remove exercise, update `sets`+`reps` prescription, move a training day's
content between weekday slots), owner-scoped, atomic, validated against the
generator's own bounds.

Files: `commands.py` (typed contract) · `document.py` (PURE mutation engine, no
ORM/Flask) · `validation.py` (bounds reused from
`training_generation/response_validator.py`) · `service.py` (transaction +
ownership) · `errors.py` · `__init__.py`. Tests:
`tests/test_plan_mutation.py`, `tests/test_plan_mutation_architecture.py`.

**No runtime surface.** No route, no AI tool, no feature flag, no model change,
no migration (Alembic head stays `f0a1b2c3d4e5`). Nothing existing was migrated
onto the new boundary; `POST /training-plan/save` keeps its whole-plan replace
semantics and the Training Generator is untouched.

## Decisions worth not re-litigating

- **Ownership is structural, not a check.** No command carries a `user_id` or a
  plan id; the service resolves the caller's own active plan by scoping the
  query. Cross-user mutation is therefore unexpressible, and "no plan" and
  "someone else's plan" both surface as `PlanNotFound` so existence never leaks.
- **Targeted-ness lives in the document layer.** The plan is one JSON text
  column, so any write rewrites the column. `document.apply_command` deep-copies,
  reaches exactly one node and mutates it in place — untouched subtrees are the
  same parsed objects, so unknown/unmodelled fields survive. Rebuilding from a
  projection (the way `plan_facts` parses for display) would silently drop them.
- **Bounds reused, posture inverted.** Same limits as the generator; the
  generator *clamps* LLM output, the mutation boundary *rejects*. Storing 100
  when the caller asked for 999 would report success for a request that did not
  happen.
- **Ambiguous exercise names are refused, not resolved by position.** Exercise
  identity in `plan_data` is `isim` only — there are no exercise IDs. A stable
  exercise catalog is the correct long-term fix and is out of scope here.
- **Derived plan-level values are never recomputed.** `haftalik_ozet` is left
  exactly as the generator wrote it; recomputing it from one exercise swap would
  be this boundary inventing planning authority.
- **History safety is schema-level, not diligence-level.** `WorkoutLog` snapshots
  `exercise_name`/`sets`/`reps`/`weight_kg`/`volume` in its own columns and
  derives nothing from `plan_data`, so no code path exists from a plan mutation
  to a historical row.
- **Exactly-once is NOT claimed.** A retried call can apply the same change
  twice (converging for replace/update/move, additive for `add`). The mutation
  journal that would fix that is PR2 work.

## Known interaction (documented, unchanged on purpose)

Mutating a day's exercises changes the Sprint 7 PR3 workout-session fingerprint
(`v1:sha256(ordered names)`), so a linked ACTIVE session classifies as
`plan_regenerated_or_replaced` → stale and the existing UX asks the user to
resolve it. Refreshing that fingerprint here would make the mutation service a
second writer of workout-session state. Whether a mid-workout mutation should
instead be *refused* is a product question for the confirmation/impact work.

## Deferred to Sprint 1 PR2

Plan version identity · mutation history with actor/reason metadata ·
before/after representation · `undo_last_change` · safe rollback · idempotency
keys/replay protection. Also still unowned: AI Coach tool registration and
execution, intent parsing, impact classification, confirmation UX, proactive
coaching, plateau/fatigue/adherence detection, nutrition-plan mutations.

---

# Adaptive Coaching Sprint 1 PR2 — Plan Versioning, Mutation Journal, Idempotent Replay & Safe Undo (local, 2026-08-14)

Branch `adaptive-coaching-s1-pr2-plan-history-undo`, worktree
`.worktrees/adaptive-coaching-s1-pr2-plan-history-undo`, based on `origin/main`
`154a3f5` (which is PR1 merged as #210). Local commit only — **not pushed, no PR,
not merged, not deployed.**

Full architecture: **docs/ADAPTIVE_COACHING.md §§12-18**.

## What shipped

PR1 could change a plan safely. It could not answer *"did this retry already
happen?"* or *"put that back"*. PR2 answers both, and the whole design follows
from one sentence: **plan bytes, plan version and the journal row must move as
one unit, or none of them may move.**

New modules under `app/services/plan_mutation/`: `context.py` (the mutation
envelope — operation key, actor, bounded reason) · `fingerprint.py` (versioned
semantic digests, pure) · `journal.py` (history queries + the record shape).
`service.py` rewritten around one transaction; `errors.py` gains four domain
outcomes. Two additive columns on `TrainingPlan` (`lineage_id`,
`mutation_version`), one new table `plan_mutation_record`, one migration
`b3c4d5e6f7a8` (single head off `f0a1b2c3d4e5`).

**Still no runtime surface.** No route, no AI tool, no feature flag, no prompt
change, no mobile change. The journal is internal evidence — there is no history
endpoint and no serialization of it anywhere, and an architecture test fails if a
blueprint so much as imports the package.

## Decisions worth not re-litigating

- **The version only ever counts up — including on undo.** Restoring old
  *content* is what undo means. Restoring an old *version number* would make two
  different histories report the same position, and any optimistic check later
  built on it would pass on stale state. Undo is a forward version that happens
  to restore old bytes.
- **The undo precondition is bytes, not version.** After two undos the plan sits
  at a much higher version than the mutation being reversed — that is correct, so
  a version-equality precondition silently breaks multi-level undo. Comparing
  `snapshot_fingerprint(plan_data)` against the target's `after_fingerprint` is
  the honest check, and a mismatch means an out-of-band writer touched the plan →
  `UndoConflict`, fail closed.
- **Snapshots are the exact persisted `plan_data` text.** PR1 preserves plan
  fields this domain does not model; a snapshot rebuilt from `plan_facts`, a DTO
  or an exercise list would pass every other test and still lose them, so undo
  would "restore" a lossy plan.
- **The idempotency key is REQUIRED, not optional.** PR1 shipped with zero
  callers, so nothing was migrated by making it mandatory — and an optional key
  is a contract in which a future consumer can quietly opt out of replay
  protection and reintroduce the duplicate-`add` bug PR2 exists to close.
- **The database is the final race arbiter.** `uq_plan_mutation_user_key`, not
  process memory, not Redis, not a timestamp window. When both contenders get
  past their pre-flight check they both reach the INSERT and the loser rolls back
  its own half-applied mutation, re-reads the winner and replays it. Note that
  the row lock usually settles it earlier — the loser blocks, then converges at
  its second look without any exception — so a green PostgreSQL race run does not
  by itself demonstrate that the constraint fired. That path is pinned separately
  and deterministically by `TestDatabaseArbitration` in
  `tests/test_plan_mutation_history.py` (added during PR2 review).
- **An accepted no-op is recorded.** The unsafe version is subtle: sets are 3, a
  no-op "set them to 3" is accepted under key K, the user really changes them to
  4, then K is retransmitted. Without a durable row K looks fresh and drags the
  plan back to 3 — a mutation nobody asked for, produced by a *retry*.
- **A validation failure consumes no operation identity.** Rejected before any
  insert, so the corrected retry under the same key succeeds instead of being
  refused as a conflict.
- **Lineage is an opaque column default, not a rule.** `POST
  /training-plan/save` deletes every row and inserts a new one, so the PK cannot
  identify "the same plan over time". A regenerated plan gets a fresh
  `lineage_id` automatically, which makes cross-lineage undo *unexpressible*
  rather than merely checked.
- **`plan_lineage_id` and `reverts_mutation_id` are soft references.** A hard FK
  to `training_plan` would be cascade-deleted by the legacy replace path,
  destroying audit history that must outlive the row it describes. A hard *self*
  FK would make owner purge order-dependent under SQLite's immediate FK checks,
  and both escape hatches corrupt the trail (`CASCADE` destroys it, `SET NULL`
  rewrites it). Uniqueness — the invariant that matters — needs no FK.
- **`populate_existing()` on the locking query is load-bearing.** `get_active_plan`
  has already put the row in the identity map, so without it SQLAlchemy hands the
  `FOR UPDATE` query the same in-memory instance with pre-lock values: the lock is
  real, the data under it is stale, and two mutations compute from one base. This
  is the repository's known footgun (`tests/test_concurrency_staleness.py`) and it
  is what PG race C exists to catch.
- **The package has no logger at all**, enforced by an AST guard. Snapshots,
  command payloads, reasons, exercise lists, operation keys and fingerprints are
  exactly the material that must not reach a log line, and "no logger" is cheaper
  to keep true than "careful logging".
- **The legacy full-plan save is NOT migrated onto the journal.** Recording a
  wholesale replacement as a targeted mutation would put a snapshot in the journal
  that an undo could later restore over a plan the user deliberately regenerated.

## Known interaction (documented, unchanged on purpose)

PR1 decided this boundary is not a second writer of workout-session state, and
PR2 keeps that decision **including on the undo path**, where "put the fingerprint
back too" is more tempting and more wrong: it would let a reversal silently
re-bless a session whose planned workout no longer exists. Pinned by
`TestHistoricalSafety.test_an_active_session_fingerprint_is_not_refreshed`.

## Verification

Commands and exact results are in the completion report for this PR; the two that
cannot run on this machine are called out there rather than claimed:
`tests/test_plan_mutation_history_pg.py` (5 races — no local PostgreSQL or Docker;
collected and wired into CI's `PostgreSQL concurrency` job) and the PostgreSQL
schema-drift guard (`flask db check`, CI-only).

## Deferred to Sprint 1 PR3

AI Coach tool registration and execution · intent → typed command · confirmation
UX and impact classification · the transport that carries `outcome` /
`plan_version` / `mutation_id` / `replayed` and maps the four domain errors ·
history API or UI, if ever · redo and arbitrary rollback (deliberately absent) ·
proactive coaching, plateau/fatigue/adherence detection, nutrition-plan mutation.

One trap for whoever wires the transport: **the operation key must be stable
across the retries of one logical user request.** A key minted per HTTP attempt
provides no protection at all.
# Sprint 10 PR3 Pump Check Comparison Intelligence (local, 2026-08-14)

## Baseline validation finding (NOT a PR3 production change)

Commit `a69c958 test: isolate audit app database configuration` fixed a
deterministic pre-existing test-harness isolation defect discovered during
mandatory baseline validation before any Sprint 10 PR3 production changes.
`tests/test_frontend_audit_app.py` built an app whose SQLAlchemy configuration
leaked into the process, so a later test in the same session
(`tests/test_gamification_routes.py::test_leaderboard_orders_by_xp_then_streak`)
failed depending on file order. The defect existed on the branch point; it is
not caused by, and does not belong to, the comparison feature. It is listed
separately from the PR3 commits in the implementation report and must be
reviewed as a test-harness fix, not as feature work.

Regression guard (re-run at PR3 HEAD):
`python -m pytest -q tests/test_frontend_audit_app.py tests/test_gamification_routes.py::test_leaderboard_orders_by_xp_then_streak`
then the single-test form — both PASS.

## What shipped

- Comparison is a SEPARATE owner-private authority over an explicitly ordered
  PAIR of canonical PR1 Pump Checks. PR1 routes, payloads, prompt, schema, and
  `pump_check` columns are byte-identical; no comparison field was added there.
- Surface: `POST /api/v1/pump-check-comparisons` (create/replay/converge) and
  owner-only read `GET /api/v1/pump-check-comparisons/<comparison_id>` on the
  existing `mobile_api` blueprint (one `/api/v1` surface, one no-store, one 429,
  the same `MOBILE_AUTH_ENABLED` gate).
- The pair is DIRECTIONAL and never sorted; `baseline.captured_at` must precede
  `current.captured_at`. Seven deterministic eligibility rules all finish before
  any S3 read or Bedrock call, and a failure creates neither a comparison row
  nor a ledger row.
- Analysis contract `pump-check-comparison-analysis/v1`. `comparability` is
  promoted out of the JSON into its own column so there is exactly one public
  authority. `not_comparable` is a legitimate COMPLETED answer, never an error.
  A `limited` source CAPS the result at `limited` — provider output claiming
  `comparable` over a limited source is rejected as invalid output.
- One bounded two-image Bedrock call. Each image is normalized IN MEMORY to at
  most 1,500,000 bytes and a 1,600-pixel longest edge; the stored S3 object is
  read but never modified, replaced, or re-uploaded. Stored PR1 narratives are
  an ELIGIBILITY signal only and are never forwarded, so interpretations do not
  compound.
- The create route consumes the shared heavy-AI concurrency gate, exactly like
  the single pump-check route, and is declared in
  `tests/test_ai_gate.py::EXPECTED_GATED_ENDPOINTS` so the thread-reserve
  invariant counts it.
- Privacy: comparison IDs are owner-bound 144-bit URL-safe HMAC tokens
  (`axisai/mobile-pump-check-comparison/id/v1`). Responses carry no image URL,
  S3 key, internal ID, idempotency key, fingerprint, prompt, provider response,
  model metadata, lease field, or failure internals. Account erasure removes
  comparison rows and their ledger rows (`tests/test_cascade_delete.py`).

## Convergence, leases, and the one accepted artifact

Uniqueness on `(owner, baseline, current, analysis_version)` means two different
Idempotency-Keys for the same directional pair CONVERGE on one row and one model
call. Work is claimed by atomically moving `pending`, a reclaimable `failed`, or
an EXPIRED `analyzing` lease (900 s) to `analyzing` with an incremented attempt
generation; finalization is conditional on owner, id, status, and attempt, so a
stale generation can never overwrite a newer result. An unexpired lease held by
another worker returns that canonical `analyzing` representation with HTTP 200 —
not an error. No transaction or row lock spans S3 or Bedrock I/O.

Accepted, documented artifact: an idempotency conflict detected at
ledger-attach time can leave an orphan `pending` comparison row, because the
ledger's FK to `comparison_id` is NOT NULL and the row must exist before the
ledger insert can be attempted. The pair unique constraint makes a later
legitimate request CONVERGE onto that same row, so there is no duplicate model
spend and no duplicate canonical comparison. Cleanup of orphan `pending` rows is
PR4 retention work, not a correctness gap here.

## Migration fa1b2c3d4e5f (sole head)

Additive, no backfill, and VERIFY-OR-CREATE + re-runnable, because
`app/db_init.py` runs `db.create_all()` BEFORE Alembic on a fresh database — so
this table-creating migration also runs against a schema `create_all` already
built. When the tables exist it does not blanket-skip: it reflects columns,
types, indexes, and CHECK constraints and FAILS CLOSED on drift.

Dialect-aware check verification was the one real production defect found and
fixed on this branch (`425e657`). SQLAlchemy's inspector does NOT return
PostgreSQL's `pg_get_constraintdef` text: it strips redundant parentheses around
AND-groups and renders membership as `= ANY (ARRAY[...])` with a single opening
paren. The verifier now canonicalizes both spellings — literals are masked, casts
dropped, `ANY (ARRAY[...])` rewritten to an IN-list, and the predicate rebuilt
through an explicit AND/OR tree — and it FAILS CLOSED (returns the input
unchanged) if any token does not parse. Tolerating the spelling does not tolerate
a wrong value: `tests/test_pump_check_comparison_migration.py` asserts the exact
inspector reflection strings captured verbatim from real PostgreSQL 16 and real
SQLite, and asserts that adding one unexpected enum member to the inspector's
`ANY (ARRAY[...])` form still raises. The earlier tests only used
`pg_get_constraintdef` forms, which is exactly why CI stayed green while a
`create_all`-then-upgrade boot failed on PostgreSQL.

## Verification (all local; nothing pushed)

Executed at branch HEAD `3b3e131`:

- Disposable PostgreSQL 16: `create_all` → `db stamp e9f0a1b2c3d4` → `db upgrade`
  exits 0 (the production fresh-DB boot path, previously broken).
- Full `db upgrade` from an EMPTY PostgreSQL 16 database exits 0.
- `flask --app starter db heads` → `fa1b2c3d4e5f (head)` only.
- `flask --app starter db check` → "No new upgrade operations detected", exit 0
  (zero model/migration drift).
- Focused 13-module PR3 + adjacent suite: 338 passed.
- Migration module alone: 36 passed.
- All 8 authoritative modulo-8 file shards over 178 test files:
  3654 passed, 9 skipped, 3 deselected, ZERO failures.
  `pytest --collect-only -q` → 3658 collected / 3 deselected, exit 0.
- Real opt-in PostgreSQL race suite (the exact CI command, 5 modules,
  `FITX_PG_CONCURRENCY_TEST=1`): 17 passed — including comparison convergence on
  one key, convergence across two keys, one-winner-one-conflict on a reused key
  with different semantics, reversed-pair ineligibility BEFORE idempotency is
  consulted, cross-user independence, stale-generation rejection, and
  expired-lease reclamation by exactly one contender.

Rollout: no new feature flag. The routes live behind the existing
`MOBILE_AUTH_ENABLED` gate; rollback is the ordinary code rollback, and the
migration is additive so it is safe to leave applied.

Excluded from PR3 and still deferred: history, automatic previous-check
selection, image URLs, Flutter/mobile client work, progress scores, heatmaps,
body-fat estimates, numeric deltas, program rewrites, social behavior, a second
provider, and all PR4 retention work.

## Integration with an advanced `main` + shipping review (local, 2026-08-15)

The section above was executed at `3b3e131`, before `main` advanced. Everything
below supersedes it. Branch HEAD is now `62c26f1`, 23 ahead of `origin/main`
(`9bc2998`) and 0 behind.

**Alembic head divergence, resolved.** While this branch was open, `main` gained
two children of `e9f0a1b2c3d4` — `f0a1b2c3d4e5` (water funnel marker) and
`b3c4d5e6f7a8` (Adaptive Coaching S1 PR2). `fa1b2c3d4e5f` was a third sibling,
which would have left the graph with **two heads** and forced boot's automatic
`db upgrade` to pick one. It now chains off `b3c4d5e6f7a8`. `main` was merged in
rather than rebased onto, because the brief and the committed reports both cite
`a69c958` and `05cbb1f` by SHA and a rebase would rewrite them.

**Two bounded review fixes** (neither changes the comparison architecture):

- `c0d036a` — the shared safety validator passed two claim spellings in **all
  seven** text fields of **both** the single-image and comparison contracts: a
  bare growth/gain/hypertrophy **rate** (a quantified progress claim carrying no
  digit, so the numeric patterns never saw it) and `skeletal disorder` /
  `pathology` / `condition` (the diagnosis the medical pattern already blocked,
  under a different noun; only "skeletal abnormality" was listed). An
  adversarial probe pushing every §19/§20 string through the real parser in
  every field went from **14 leaks to 0**.
- `62c26f1` — `menu_ocr._extract_text_from_image` caught only
  `ImageTooLargeError` from the shared preparer, but the preparer raises the
  parent `ImagePreparationError` for an undecodable image. `app/blueprints/
  menu.py:110` calls that path with no `try/except`, so an oversized *and*
  undecodable upload produced a **500** where it used to produce the friendly
  "could not read the menu" answer. Reproduced concretely, then fixed by
  catching the parent class.

### Verification — all re-executed at HEAD `62c26f1`

Local interpreter Python 3.14.3; CI is authoritative on 3.11.

PostgreSQL 16 (disposable container, every database recreated for the run):

- full `db upgrade` from an **empty** database — exit 0; the chain ends
  `b3c4d5e6f7a8 -> fa1b2c3d4e5f`.
- `flask --app starter db heads` → **`fa1b2c3d4e5f (head)`**, sole head.
- `flask --app starter db check` → **"No new upgrade operations detected"**,
  exit 0 — zero model/migration drift.
- real fresh-DB boot path (`create_all` → automatic stamp → automatic upgrade) →
  `alembic_version = ['fa1b2c3d4e5f']`, with `pump_check`, `pump_check_comment`,
  `pump_check_comparison`, `pump_check_comparison_request`, `pump_check_like`
  and `plan_mutation_record` all present.
- incremental upgrade from `main`'s head (`b3c4d5e6f7a8`) to this branch's head —
  exit 0; comparison tables **0 before, 2 after**.
- schema-drift probe, **9/9 as designed**: `compatible_untouched` ACCEPTED,
  `missing_request_table` ACCEPTED (recreated), and REJECTED with an explicit
  message for `missing_column`, `widened_comparability_check`,
  `analysis_json_not_jsonb`, `timezone_aware_created_at`, `dropped_pair_unique`,
  `dropped_ledger_unique`, `nullable_analysis_version`.
- real opt-in race suite, the **exact CI command** (6 modules, `-m
  pg_concurrency`, `FITX_PG_CONCURRENCY_TEST=1`) — **22 passed**.

Full local suite, 8 deterministic modulo-8 shards over 181 test files, every
shard exit 0:

| Shard | Result |
|---|---|
| 0 | 721 passed, 1 skipped |
| 1 | 415 passed, 4 skipped |
| 2 | 443 passed |
| 3 | 467 passed |
| 4 | 506 passed, 1 skipped |
| 5 | 417 passed |
| 6 | 395 passed, 2 skipped |
| 7 | 477 passed, 2 skipped |
| **Total** | **3841 passed, 10 skipped, ZERO failures** |

Collection reconciles exactly: `pytest --collect-only -q` over `tests/` reports
**3845/3848 collected (3 deselected**, the `-m "not load"` load tests), and the
same 8 shard file groups collect 721/418/443/467/506/417/396/477 = **3845**. The
run reports 3841 passed + 10 skipped = 3851 outcome lines; the 6-line difference
is the six `pg_concurrency` modules, which call
`pytest.skip(..., allow_module_level=True)` at **collection** time — one skip
line each, zero collected items each. 3845 = 3841 passed + 4 in-run skips.

# Progress Redesign PR2 — Canonical Progress Summary & Trajectory Read Model (local, 2026-08-15)

Branch `feat/progress-redesign-pr2-summary`, worktree
`.worktrees/progress-redesign-pr2`, based on `origin/main` `9bc2998`. PR1 (#212,
`336a420`) is an ancestor. **Not merged, not deployed, no production config
touched.**

Full architecture: **docs/PROGRESS_SUMMARY.md**.

## What shipped

PR1 built the Progress information architecture and deliberately left the
trajectory neutral, because no authority existed to fill it. PR2 builds that
authority: one server-owned, deterministic read model behind
`GET /api/progress/summary`, and YOUR PROGRESS / BODY / PERFORMANCE /
CONSISTENCY all render it. The page can no longer show a card that disagrees
with its own headline, because there is only one answer to disagree with.

New package `app/services/progress_summary/`, layered exactly like
`training_progression`: `models.py` (frozen value objects + every bounded state
constant) · `analysis.py` (pure mappings, no DB/Flask/clock) · `queries.py` (the
only impure reads: `User.weight` / `User.target_weight` / qualifying
`WeeklyCheckIn` rows) · `payload.py` (explicit wire projection) · `__init__.py`
(`build_progress_summary` orchestrator).

Three trajectory states — `building_baseline`, `on_track`, `needs_attention`.
No `off_track`. No score of any kind.

**No schema change, no migration, no persistence, no cache, no LLM call.** The
summary is recomputed per request. Every pre-existing endpoint
(`/api/progress/workout`, `/api/progress/achievements`, `/checkin-history`,
`/api/progress/insights`, `/api/progress/heatmap`, Pump Check) is untouched and
keeps its other consumers.

## Decisions worth not re-litigating

- **The trajectory is training-led, and body does not vote.**
  `training_progression` is the only domain with a validated, documented
  longitudinal authority, and it already resolves its own overlapping booleans
  into one `next_signal` with a fixed precedence. This layer consumes that
  resolution and maps it — it does not build a second precedence chain out of
  `is_plateau` / `deload_due`, which would put two disagreeing authorities in
  one product. Deciding whether a weight movement is "good" would require
  inventing rate thresholds the repo has no authority to create, so BODY is
  context only. `test_body_does_not_override_the_training_trajectory` proves it
  in both directions — a 4 kg gain and a 4 kg loss leave the state
  byte-identical.

- **An unknown `next_signal` raises; it does not degrade to a state.**
  `UnknownProgressionSignal` → generic 500. Mapping unknown → `on_track` would
  invent success; mapping it → `building_baseline` would report a *contract
  drift* (a system fault) as "you haven't logged enough yet", which is a lie
  about the user. Infrastructure failure and insufficient evidence are different
  states and stay visibly different, on the client too: a failed fetch renders
  `progress.traj_unavailable`, never a trajectory.

- **`end_day` is resolved once, in the orchestrator.** Letting
  `build_progression_report` default its own `end_day` while the window builder
  read the clock again would let a request straddling Istanbul midnight report a
  window the signals were not computed over. `start` also comes from
  `weekly_windows(end_day, 4)[0]` — the same call the report makes — rather than
  parallel date arithmetic that could drift.

- **Sparse `/update-weight` rows are not check-ins, and the two concepts stay
  separate.** "Qualifying" means `yogunluk IS NOT NULL`, the exact filter
  `/checkin-history` and `/api/progress/insights` already use (BUG-5). A sparse
  row is a perfectly good answer to *what does this user weigh* — so it feeds
  the current-weight fallback — and not an answer to *is this a Progress
  observation* — so it never produces a delta.

- **Missing is not zero.** No target weight (or a stored non-positive one) →
  `null`, matching the mobile nutrition boundary's non-positive calorie goal.
  Fewer than two qualifying check-ins → `weight_delta_kg: null`, not `0.0`. But
  four analyzed weeks with nothing trained → `sessions: 0`, a real measured
  zero.

- **Stored weights are echoed unrounded; only derived values round** (delta and
  distance, 1 dp), matching `/api/progress/insights`.

- **The client translates, it never decides.** Every state arrives as a bounded
  enum and is looked up in an explicit table; an enum the build does not know
  renders the neutral state rather than a guess.
  `test_client_never_fabricates_a_trajectory` scans the file structurally and
  rejects any threshold on a summary field. It has already earned its keep: it
  caught an `analyzed_weeks > 0` display guard, which was removed rather than
  exempted.

- **The gamification streak is no longer a Progress consistency signal.**
  Logging in is not training. `/api/progress/achievements` is unchanged and
  still serves its other consumers.

- **Trajectory is never carried by colour alone.** `#ps-state` always spells the
  state out, and `data-state` (which tints only the card's left rule — no filled
  red/green verdict background) is written by the same function that writes the
  label.

## Known interaction (documented, unchanged on purpose)

`app/hooks.py::update_streak` is an app-wide `before_request` hook that writes
`streak_count` on the first *authenticated* request of the day — including a
`GET /api/progress/summary` that happens to be first. That is not this
endpoint's write, and `test_summary_read_performs_no_write` isolates it with a
warm-up request before capturing its baseline. Worth knowing before anyone reads
a streak change on a read endpoint as a bug in this package.

## Deferred to PR3 / PR4

**PR3 — AXIS INSIGHTS intelligence:** What's Working / Watch This / Next Move,
cross-domain narrative, recommendations. PR2 answers *how am I doing* and *why*
at a bounded deterministic level; it does not answer *what should I do next*.
**PR4 — Physique Progress:** Pump Check comparison, visual progression,
body-region change. A future body-trajectory authority is recorded in
docs/PROGRESS_SUMMARY.md §12, not implemented. Nutrition and recovery are
excluded from V1 classification on purpose — the weekly check-in carries
sleep/fatigue data but nothing validates it as a Progress authority, and using
it would create exactly the hidden scoring model the design forbids.

# Progress Redesign PR3 — Canonical Axis Insights & Next-Action Intelligence (local, 2026-08-16)

Branch `feat/progress-redesign-pr3-axis-insights`, worktree
`.worktrees/progress-redesign-pr3-axis-insights`, based on `origin/main`
`d3fa061`. PR1 (#212, `336a420`) and PR2 (#215, `d3fa061`) are ancestors.
**Not merged, not deployed, no production config touched, no rollout flag added
or changed.**

Full architecture: **docs/PROGRESS_INSIGHTS.md**.

## Baseline

`origin/main` = `d3fa0611989f3309de1d9e8ca5855ba66c60a128` at branch time, which
matches the SHA the brief expected. `app/services/progress_summary/` and
`GET /api/progress/summary` are present, so PR2 is really in the base. No open
PR touches the Progress Insights surface. `AdaptivePlan` exists and is stable
(`app/services/training_planning`), so the next-action authority PR3 requires
was already there to project.

## What shipped

PR2 made Progress *truthful* — one server-owned answer to "how am I doing".
PR3 makes it *useful without making it speculative*, in exactly three slots:

```
WHAT'S WORKING   the one positive signal worth saying out loud
WATCH THIS       the one concern that outranks the others
NEXT MOVE        the canonical next training action
```

New package `app/services/progress_insights/`, layered like `progress_summary`
minus one module: `models.py` (frozen value objects + every bounded vocabulary
constant) · `analysis.py` (pure slot selection, no DB/Flask/clock) · `payload.py`
(explicit wire projection) · `__init__.py` (`build_progress_insights`).

New read-only endpoint `GET /api/progress/axis-insights` (`@require_auth`, no
input at all, `Cache-Control: private, no-store`), a new client module
`static/progress_insights.js`, the AXIS INSIGHTS markup in `templates/progress.html`
rebuilt as three labelled slots, and 26 additive keys in each of
`locales/en.json` / `locales/tr.json`.

**No schema change, no migration, no persistence, no cache, no LLM/provider
call, no prompt, no feature flag.** The insights are recomputed per request.

## Ownership map (what this layer does and does not own)

| Concept | Owner |
|---|---|
| Which canonical signal earns which slot | `progress_insights` (**new**) |
| Trajectory / performance / consistency / body | `progress_summary` (unchanged) |
| `week_focus`, volume & intensity action, `volume_delta_pct` | `training_planning` (unchanged) |
| Trend / plateau / deload / `next_signal` | `training_progression` (unchanged) |
| History, week geometry, marker semantics | `training_history` (unchanged) |

`progress_insights → {progress_summary, training_planning} → training_progression
→ training_history`. One-way, no cycles, pinned by
`test_dependency_direction_is_one_way`.

## Decisions worth not re-litigating

- **This layer measures nothing.** Not one threshold, count, percentage or trend
  is computed in it. That is what lets a third Progress module exist without
  becoming a third progress authority. A structural test rejects numeric module
  constants in `analysis.py`.
- **NEXT MOVE comes from `AdaptivePlan.week_focus` and from nothing else.**
  Sessions, volume trend, strength trend, consistency counts, trajectory and
  body change are evidence the planner already weighed; re-deriving a move from
  them here would be a second planning engine able to contradict the AI Coach,
  the weekly program surface and the plan page — all of which read the same
  planner. The quantified adjustment is copied verbatim, and a test compares the
  published fraction against `VOLUME_INCREASE_STEP` / `DELOAD_VOLUME_CUT`.
- **WATCH THIS walks the planner's own `reason_codes` order.** Position 0 is the
  planner's primary cause; re-sorting here would be the duplicated authority the
  brief forbids. The attention-worthy codes and the explicitly-not-a-concern
  codes (`insufficient_history`, `progressing`, `steady_state`) *partition* the
  planner's whole vocabulary, so an unrecognised code is unambiguously new and
  raises instead of degrading into an all-clear.
- **A secondary reason still surfaces under a non-attention primary.** When the
  planner emits `["steady_state", "volume_trend_down"]` it has chosen not to
  *act* on the down-trend while still recording it. Surfacing that recorded code
  is not an invented warning — it is the planner's own.
- **Consistency can be praised while the trajectory is `needs_attention`.** A
  user with a plateau or a due deload has still trained consistently, and saying
  so is true. Its copy describes consistency and nothing else, so it cannot read
  as "everything is fine". This is the invariant WHAT'S WORKING exists for.
- **`plateau` / `deload` / `building_consistency` map to no positive code.**
  None of them is a positive claim; manufacturing one is exactly what that slot
  must not do.
- **NEXT MOVE is always `available`.** With no history the planner still emits a
  real canonical decision (`insufficient_data` → `build_baseline`) — "log some
  training so there is something to coach on" is a genuine next move, not a
  filled-in blank.
- **`empty` ≠ `insufficient_data` ≠ failure.** "Nothing needs your attention" is
  a finding, "we cannot tell yet" is an evidence gap, and an outage is a generic
  500 the client renders as `unavailable`. A failure is never shown as an empty
  insight. All three are pinned by tests and audited in the browser matrix.
- **Unknown canonical vocabulary fails closed** (`UnknownCanonicalVocabulary` →
  generic 500), following PR2's `UnknownProgressionSignal` precedent. Both
  plausible fallbacks would publish something nobody decided.
- **There is deliberately no `queries.py`.** A read of its own would make this
  layer the owner of a fact nobody else owns — the second authority PR3 exists
  to prevent. Everything is composed from existing *public pure* functions over
  a **single** `build_progression_report` call, and
  `test_history_is_read_exactly_once` pins that count at one. No refactor of
  `progress_summary` or `training_planning` was needed: both pure functions were
  already exported.
- **The window is imported, not re-declared.** `INSIGHTS_WEEKS = SUMMARY_WEEKS`,
  and a test asserts the two endpoints publish an identical window — the two
  sections cannot describe different periods. `end_day` is resolved once, so a
  request straddling Istanbul midnight cannot split the two branches.
- **The body is not judged, and nutrition/recovery/hydration are excluded.** No
  validated rate-of-change authority exists for any of them in this build.
  `DOMAINS = ("training",)` is a single-element tuple and the contract already
  carries `domain`, so a future domain arrives additively.
- **The client translates, it does not decide.** One endpoint, an explicit
  code→locale table, unknown code degrades to neutral copy, and structural tests
  reject thresholds, scores, percentage arithmetic and any numeric comparison.
  The only number rendered is `volume_delta_pct`, *formatted* by
  `Intl.NumberFormat`, never computed. It lives in its own module rather than in
  `progress.js` so PR2's "client never fabricates a trajectory" guard stayed
  exactly as strict as it was.

## Contract

```
GET /api/progress/axis-insights   →   { contract_version, window,
                                        working, watch, next_move }
```

Each slot always carries the same five keys (`status`, `code`, `domain`,
`evidence`, `action`), so a client never branches on key existence. No lists, no
prose, no ids, no ORM serialization, no free text. `status` ∈ {`available`,
`empty`, `insufficient_data`}. `null` means unavailable; `0` means a measured
zero.

Decision tables (all exhaustive over the upstream vocabulary, all asserted):

| `week_focus` → NEXT MOVE | | planner reason → WATCH | | performance/consistency → WORKING |
|---|---|---|---|---|
| `insufficient_data` → `build_baseline` | | `inconsistent_training` → `build_consistency` | | `progressing` → `training_progressing` |
| `build_consistency` → `prioritize_consistency` | | `deload_due` → `deload_due` | | `steady` → `training_steady` |
| `deload` → `deload` | | `plateau_detected` → `plateau_detected` | | consistency `consistent` → `training_consistent` |
| `maintenance` → `maintain_and_consolidate` | | `volume_trend_down` → `volume_trend_down` | | `building_baseline` → *insufficient_data* |
| `overload` → `progress_training` | | `strength_trend_down` → `strength_trend_down` | | otherwise → *empty* |
| `steady` → `maintain_current_training` | | (3 codes explicitly not a concern) | | |

## Verification

- `tests/test_progress_insights.py` (43) — decision tables vs the upstream
  tables, reachability of every published code, per-signal decision matrix,
  fail-closed params, verbatim action projection, bounded evidence, window
  equality, determinism, user isolation, no-write, exactly one history read, one
  clock read, dependency direction, no ORM/Flask import, no provider words.
- `tests/test_progress_insights_api.py` (18) — auth, `?user_id=` ignored, window
  pinned and not query-driven, versioned bounded contract, no lists, action
  fraction, no identifier/prose leak, `private, no-store`, empty-user baseline
  move, failure ≠ empty, contract drift → 500, failure isolation, legacy
  byte-shape preserved, routes independent.
- `tests/test_progress_insights_ui.py` (17) — three labelled slots in order,
  H2/H3 semantics, structural without JS, no carousel/tabs/chart, client reads
  only the new endpoint, **no decision threshold in the client**, three-way
  locale symmetry, all statuses handled, unknown code degrades, EN/TR symmetry,
  non-judgemental copy, failure ≠ empty, status not colour-only, `progress.js`
  hand-off, script order, check-in refresh.
- Browser evidence: `scripts/frontend_audit/progress_pr3_matrix.py` →
  `docs/frontend-readiness/progress-pr3/validation-manifest.json`. Hermetic
  Chromium via the Sprint-0 harness, fixed Istanbul clock (2026-07-20), **28/28
  cells passed, 0 failed, 0 blocked, `seed_drift: {}`** — 6 canonical states plus
  the endpoint-failure state × 4 viewports (390 / 768 / 1280 / 1440), 14 curated
  screenshots. Seeds are verified against the *service's* own output before any
  capture, so a drifting seed fails loudly instead of auditing the wrong state.
  Run it from WSL (the Windows host is hard-gated unsupported by
  `preflight.classify_environment`).

Two harness-only fixes came out of the first run and are worth knowing: the
matrix now waits for all three slots to carry a `data-status` instead of a fixed
timeout (the async read was being raced), and the console-error gate filters the
analytics tag's offline CSP noise plus the failure cells' own injected 500 —
both are recorded in the manifest either way (`console_errors` vs
`own_console_errors`), so nothing is hidden.

## Conflict risk

Low and confined. `app/blueprints/tracking.py` gains an import block, one path
constant, one route, and `_progress_summary_error_class` now buckets
`UnknownCanonicalVocabulary` alongside `UnknownProgressionSignal` (the helper
name is unchanged because `tests/test_progress_summary_api.py` references it).
`templates/progress.html` and `static/progress.css` change only inside the AXIS
INSIGHTS region — `id="insight-list"` and `aria-live="polite"` are kept because
PR1 asserts them. `static/progress.js` loses `loadInsights()` and calls
`loadAxisInsights()` instead. Locale files are **purely additive** (+26 / +26,
zero deletions, no reformatting).

## Rollback

Revert the commit. There is no flag, no migration, no persisted state, and no
production config to unwind. The legacy `GET /api/progress/insights` route was
never touched, so a revert restores the old section by itself.

## Deferred

PR4 (Pump Check / physique comparison) and PR5 (Progress History) are untouched.
Nutrition, recovery and hydration insight domains, body verdicts, gamification
signals, insight persistence/caching and any mobile/Flutter surface are excluded
from PR3 on purpose — see docs/PROGRESS_INSIGHTS.md §7 and §13.

# Progress Redesign PR4 — Canonical Physique Progress Integration

Branch `feat/progress-redesign-pr4-physique-progress`, worktree
`.worktrees/progress-redesign-pr4`, based on `origin/main`
`264d98f00120d8f6baaad34cc5bfcacfe5383b02`. PR1 (#212), PR2 (#215), PR3 (#216),
canonical Pump Checks (#207), comparisons (#213) and history (#218) are
ancestors. **Not merged, not deployed, no production config touched, no
rollout flag added or changed.**

Full architecture: **docs/PROGRESS_PHYSIQUE.md**.

PR4 replaces the thumbnail-only PHYSIQUE PROGRESS shell with a read-only
projection of canonical Pump Check history and any persisted
`PumpCheckComparison`. Page load creates nothing: no comparison, no Bedrock
call, no analysis retry. If a completed comparison exists it is shown; if not,
the section says so. Comparison creation stays on the existing mobile command
(`POST /api/v1/pump-check-comparisons`); this PR does not add a second web
creation workflow.

New package `app/services/progress_physique/` (`models` / `queries` / `payload`
/ `build_progress_physique`). New `GET /api/progress/physique`
(`@require_auth`, optional `?region=`, `Cache-Control: private, no-store`).
New client module `static/progress_physique.js`. `static/progress.js` only
calls `FitXPhysiqueProgress.load()`.

Chronology is `captured_at`. Legacy `captured_at IS NULL` rows stay in the
gallery. Comparability is the persisted canonical value
(`comparable` / `limited` / `not_comparable`). An unknown persisted token
raises `UnknownPhysiqueComparability` and `GET /api/progress/physique`
returns the generic 500. It is **not** remapped to a local `unknown` wire
value and is **not** rendered as `not_comparable` / `comparison_available`
content. Images are short-lived owner-scoped URLs minted only for the rows
the section renders.

Merge-readiness follow-up (same branch, not merged):

- Unknown canonical comparability is contract drift: dedicated exception,
  generic 500, isolated PHYSIQUE PROGRESS unavailable state, no raw value
  leak. `COMPARABILITY_UNKNOWN` removed from the Physique Progress wire
  vocabulary. The Pump Check comparison domain and its DB CHECK are
  unchanged.
- Hermetic Chromium matrix (WSL Ubuntu-24.04, Sprint-0 venv +
  `PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/axisai-sprint0-playwright`):
  9 states × 4 viewports = **36/36 pass**, seed drift empty.
  Evidence: `docs/frontend-readiness/progress-pr4/validation-manifest.json`
  plus 18 curated screenshots (390 and 1440 for every state).
  Command:
  `python -m scripts.frontend_audit.progress_pr4_matrix --output docs/frontend-readiness/progress-pr4`

  | State | 390 | 768 | 1280 | 1440 |
  |---|---|---|---|---|
  | empty | pass | pass | pass | pass |
  | legacy_only | pass | pass | pass | pass |
  | single_check | pass | pass | pass | pass |
  | history_only | pass | pass | pass | pass |
  | comparable | pass | pass | pass | pass |
  | limited | pass | pass | pass | pass |
  | not_comparable | pass | pass | pass | pass |
  | stale_comparison | pass | pass | pass | pass |
  | endpoint_failure | pass | pass | pass | pass |

  Every cell recorded: correct render, no horizontal page overflow,
  region-chip behaviour, baseline/current image layout, image load,
  long-text containment, console/page errors, and the other four
  Progress sections still rendering. Keyboard chip operation, visible
  focus, meaningful image alt, and non-colour-only comparability were
  asserted on the comparison/history cells. `comparable@390` first
  blocked on a 20s `/progress-page` navigation timeout; the same
  hermetic cell passed on retry.

Rollback: revert the commit. No schema, no migration, no flag, no cache, no
provider cleanup.

A later same-branch contract fix: `GET /api/progress/physique?region=` (and
whitespace-only `region`) is invalid HTTP 400. The route no longer remaps an
empty supplied region to omitted/default. Omitted `region` still selects the
latest canonical area. No frontend, schema, or Pump Check domain change.
The prior 36-cell hermetic matrix was not rerun: this is HTTP validation
only and does not change Progress rendering.

# Adaptive Coaching Sprint 1 PR3 — AI Coach Training-Plan Tool Integration (local, 2026-08-16)

Branch `adaptive-coaching-s1-pr3-ai-coach-plan-tools`, worktree
`.worktrees/adaptive-coaching-s1-pr3-ai-coach-plan-tools`, based on `origin/main`
`9bc2998` (which is PR2 merged as #211, on top of PR1 #210 `154a3f5`). Local
commits only — **not pushed, no PR, not merged, not deployed, flag not enabled
anywhere.**

Full architecture: **docs/ADAPTIVE_COACHING.md §§19-30**. Flag record:
**docs/FEATURE_FLAGS.md #8**. Rollout/observation: **docs/ROLLOUT.md**.

## What shipped

PR1 built the mutation boundary and PR2 gave it history, versioning and undo —
both with **no caller**. PR3 is the caller, and it is the first time in this
codebase that a language model can cause a durable write to user data.

New package `app/services/coach_plan_tools/` — the trusted execution context and
the single bridge between the Coach and `plan_mutation`:

| Module | Owns |
|---|---|
| `schemas.py` | the six LLM-facing tool definitions; bounds imported from the domain |
| `parser.py` | model arguments → a typed PR1 command, or a refusal |
| `identity.py` | the server-minted operation key |
| `results.py` | the bounded result + error vocabulary (4 statuses, 16 codes) |
| `executor.py` | flag, turn, budget, actor, the domain call, transaction settle |

Six narrow tools: `replace_training_plan_exercise`, `add_training_plan_exercise`,
`remove_training_plan_exercise`, `update_training_plan_prescription`,
`move_training_plan_day`, `undo_last_training_plan_change`.

Wiring: `app/services/ai_coach.py` (per-turn init, flag-gated tool lists for both
providers, plan-tool dispatch branch), `app/prompts/system.py`
(`PLAN_MUTATION_POLICY`, emitted only when the flag is on),
`app/services/prompt_builder.py` (`plan_mutation_tools=` threaded through both
provider paths), `app/feature_flags.py`
(`AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, default OFF, `staging_only`,
`depends_on=("AI_ADAPTIVE_PLAN_CONTEXT",)`).

**No new route, no schema change, no migration** (Alembic head unchanged at
`b3c4d5e6f7a8`), no mobile/Flutter change, no new dependency. The eight existing
Coach tools are byte-identical in behaviour with the flag off *and* on.

PR2's known Minor is closed here rather than carried: `service.py`'s
`IntegrityError` arbitration branch re-read the journal after its rollback and
left that read open. With no caller that was a documented residual; with a
blocking provider call immediately downstream it is a transaction held across
network I/O, so `_arbitrated_result` now closes it in a `finally`, at the layer
that owns the transaction.

## Decisions worth not re-litigating

- **Six narrow tools, never a generic one.** No `mutate_training_plan(operation,
  payload)`, no JSON Patch, no `set_plan_field`. A generic tool would hand the
  model an arbitrary mutation language *above* the typed boundary and make PR1's
  contract decorative. An architecture test rejects a property named `operation`,
  `payload`, `patch`, `path`, `field`, `value` or `sql` anywhere in a schema.
- **A property exists only if it carries user intent.** `user_id`, `plan_id`,
  `lineage_id`, `mutation_version`, `mutation_id`, `actor`, `reason`, the
  operation key, both snapshots and every fingerprint appear in no schema, and a
  test walks the published schemas to prove it.
- **The parser refuses, it never coerces.** Unknown property → named in the
  error, not silently dropped (dropping a field executes a *different* request
  than the model expressed and nobody learns about it). Missing required property
  → no defaulting; the plan *generator* fills gaps because a slightly-wrong plan
  beats no plan, but a mutation that invents "3 sets" is a fabricated
  instruction. `bool` is rejected before `int`, or `True` arrives as "1 set".
- **The turn identity is the existing `g.request_id`, not a new one.** It is
  server-generated, un-injectable, already the log/SSE correlation id, and it
  survives a provider retry, a Bedrock→OpenAI fallback and every tool-continuation
  round. A second turn-identity system would have been a competing source of
  truth with no evidence one was needed.
- **The operation key hashes the domain's own `semantic_fingerprint`, not raw
  model JSON.** Whitespace, key order or one echoed field would each mint a fresh
  key for one intent. Reusing PR2's comparator also means the key and the replay
  check can never disagree about what "the same mutation" is.
- **No occurrence counter in the key.** It would make the second delivery of one
  mutation a *new* operation — precisely the duplicate `add` and double `undo`
  this mechanism exists to prevent. Distinct commands separate themselves:
  different intent, different fingerprint, different key.
- **The replay guarantee is scoped to one server turn and said so out loud.** A
  new HTTP request carries a new `request_id` and is a new intent, which is
  correct — a user who says "add cable flies" in two messages has asked twice. No
  durable client-supplied chat-request identity exists in this architecture, and
  inventing fragile state to claim a broader guarantee would be a promise the
  system cannot keep.
- **`MAX_PLAN_OPERATIONS_PER_TURN = 2`, strictly below `_COACH_TOOL_LOOP_CAP`
  (5).** The loop cap bounds how many times the model may call *any* tool; it was
  never sized as a limit on durable writes, and a budget at or above it is
  decorative. Two is what an honest single message asks for; more is a plan
  redesign, which must not be decomposed into a mutation swarm.
- **A repeated operation key costs no budget.** Otherwise the second delivery of
  one mutation could be refused as "too many changes" and the model would tell
  the user their edit was rejected when it had in fact been applied — the worst
  available answer. The budget refusal also never rolls back earlier edits:
  punishing the user for the model's behaviour is a second bug on top of the
  first.
- **`actor="ai_coach"` is audit metadata, not authorization.** It is set by the
  server and appears in no schema; the mutation is scoped to the caller's own
  plan either way, because PR1 made cross-user mutation structurally
  inexpressible.
- **`reason` is `None`.** Every candidate was worse: the raw user message is
  untrusted input in a durable audit column, model-authored text is unverified
  narration of what the model *believes* it did, and a fixed "AI Coach" string
  says nothing the `actor` column does not.
- **The result payload is small, closed and free of identifiers.** A tool result
  is the next model call's input — billed, and paraphrasable to the user. Both
  snapshots, the plan document, `lineage_id`, any row id, the operation key, any
  fingerprint, SQL and raw exception text never cross. `mutation_id` is withheld
  too: the model cannot act on it, and an identifier in the context window is an
  identifier that can be echoed.
- **`replayed` is a distinct status from `applied`.** The model must be able to
  say "that is already done" instead of narrating a second change that never
  happened. `no_op` likewise says so explicitly and instructs the model not to
  claim a change.
- **Every applied result carries a staleness note.** The plan block in the prompt
  was built before the tools ran, so a committed mutation makes it stale.
  Re-rendering the block mid-turn was rejected: it would put a second description
  of the plan in the window and would be stale again after the next call.
- **Error recovery guidance travels with the failure.** Each of the sixteen codes
  carries a server-authored instruction in the payload rather than competing with
  the system prompt, and codes are mapped by exception **class**, so a domain
  wording change is not a contract change.
- **Undo is a dedicated, argument-less tool.** No `undo(version)`, no
  `rollback_to`, no redo. `undo_fingerprint()` is constant, so every undo in one
  turn *is* the same operation and a duplicated delivery replays the first
  instead of reaching back and reversing a second, earlier change. Arguments are
  refused rather than ignored, so a model that thinks it can choose *which*
  change to undo finds out that it cannot.
- **A committed mutation is never rolled back for a provider failure.** The
  B-rule now guards a durable write, so once a plan tool has run the turn
  degrades to a soft error rather than re-running against a plan that has already
  changed. A failed continuation is not a reason to reverse a change the user
  asked for.
- **`_settle_transaction()` rolls back only a provably read-only residual.**
  Anything pending is left to its owner; a blanket rollback here would be a
  second uninvited transaction authority. What it guarantees is the thing that
  matters: no transaction is held across the provider call that follows a tool.
- **The flag gates both halves.** OFF removes the tools from both provider
  schemas *and* refuses execution, so a model that remembers a tool name from an
  earlier turn still cannot reach the domain. OFF also emits no policy block — a
  prompt must not describe a capability the model cannot use.
- **One log line, no metric.** `[COACH][PLAN_TOOL] request_id=... tool=... outcome=...`,
  both values from closed server-owned vocabularies, pinned by an AST test that
  checks the logger call's *arguments* (the format string is a server constant;
  the arguments are where user or model data would enter). The unexpected-error
  path logs the exception **class**, not the exception, and the same test now
  rejects `exc_info=`/`extra=`: a traceback carries the exception's own message,
  and the realistic unexpected failure here is a SQLAlchemy `StatementError`
  whose message embeds the statement and its bound `plan_data`. The same
  reasoning added `from None` to `IdempotencyConflict`, which is raised from
  inside an `except IntegrityError` handler. No metric is emitted:
  the honest signal for this capability — did the AI change something the user
  did not ask for? — is not countable, and a counter implying it was would be
  worse than none. The authoritative record stays the `PlanMutationRecord`
  journal.
- **PR1's `test_no_coach_module_imports_the_mutation_domain_yet` was narrowed,
  not deleted.** It now asserts that every Coach module still reaches the domain
  only through `coach_plan_tools`, and a companion test keeps an explicit
  allow-list of who may import `plan_mutation` at all — the interesting failure
  is a *new* consumer appearing quietly.

## Known interaction (documented, unchanged on purpose)

Editing a day's exercises changes the Sprint 7 PR3 session fingerprint, so a
linked ACTIVE session becomes `plan_regenerated_or_replaced` → stale. PR1 decided
this boundary is not a second writer of workout-session state and PR2 kept that
on the undo path; PR3 keeps it with an AI in the loop, where "refresh the
fingerprint too" would let a model-initiated edit silently re-bless a session
whose planned workout no longer exists. `WorkoutSession` and `WorkoutLog` rows
are asserted byte-identical across a plan edit.

## Rollout / rollback

Enable order and the observation query are in **docs/ROLLOUT.md** (#8, after
`AI_ADAPTIVE_PLAN_CONTEXT`). The flag is `staging_only` — production activation
is a separate, unauthorized decision and is **not** part of this PR.

Rollback is `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED=0` (or removing the line) plus
a restart. It stops new AI writes; it does **not** revert mutations already
applied. Those are ordinary versioned plan history and the user can undo them —
which is why the observation window watches individual journal events, not rates:
one plan changed without a request is already the abort signal.

## Verification

Commands and exact results are in the completion report for this PR. As in PR2,
the two things that cannot run on this machine are named rather than claimed:
`tests/test_plan_mutation_history_pg.py` (no local PostgreSQL or Docker; wired
into CI's `PostgreSQL concurrency` job) and the PostgreSQL schema-drift guard
(`flask db check`, CI-only).

## Deferred (explicitly not in PR3)

Impact classification and confirmation/preview UX · mobile mutation UI and any
Flutter change · a public or mobile REST mutation API, and still no history
endpoint · redo, `rollback_to_version`, restore-by-id, plan diff view ·
full-plan regeneration, frequency/goal/split mutation and deload through these
tools · proactive or scheduled editing · plateau/fatigue/adherence detection ·
Pump Check or Weekly Check-in driven mutation · nutrition mutation · push
notification · production flag activation.

---

# Adaptive Coaching Sprint 1 PR4 — impact, durable proposals, structural confirmation

PR3's explicit intent remains prompt-policy. PR4 adds a different guarantee:
confirmation-required mutations get a server-owned impact decision, a durable
user-scoped plan-state-bound proposal, and a structural CONFIRM/CANCEL/NONE
gate derived from the raw current user turn. The model cannot choose impact,
cannot supply a proposal id, and cannot self-confirm.

Packages: `app/services/coach_plan_policy` (pure decision + parser),
`app/services/plan_confirmation` (pending row only — not a second plan writer),
`coach_plan_tools` still the only Coach bridge to `PlanMutationService`.

Migration `c2d3e4f5a6b7` (additive). Flag unchanged:
`AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, default OFF. No public route, no
Flutter, no nutrition mutation, no proactive adaptation.

Confirmed writes reuse PR2 idempotency keyed from the proposal public id so a
crash between mutation commit and proposal bookkeeping cannot double-apply.

Confirm and cancel serialize on the proposal row (`SELECT … FOR UPDATE`;
lock order: proposal, then plan). A still-pending cancellation cannot be
followed by that proposal's mutation. Cancel of an already-committed
proposal-derived journal identity reconciles only while its after-state still
exactly matches the current canonical plan; it does not claim the plan was
unchanged. Active-session impact is a snapshot at evaluation time. A cancel
that starts after confirmation has fully resolved selects the owner-scoped
latest durable proposal and replays APPLIED semantics while the current
lineage/version/snapshot still match. Historical rows with moved plan state or
APPLIED rows without journal evidence cannot be executed or rebound.


## Adaptive Coaching Sprint 1 PR3 — independent review fix (local, 2026-08-16)

**Finding (Important).** After a plan tool committed a mutation, every degraded
exit of the turn returned the generic soft error — `"İşlemi tamamlayamadım,
tekrar dener misin?"` on the blocking loop, the `coach.reply_failed` SSE key on
the stream. Three reachable exits each: a provider/parse failure after the tool
(the B-rule path this PR relies on), the tool-loop cap, and the turn deadline.

Two problems, one sentence. The change **is** committed, so the message is
false; and it tells the user to try again, which is a new HTTP request → a new
`request_id` → a new operation key → the same exercise added a **second** time.
The cross-request scope documented in ADAPTIVE_COACHING §22 is correct and
deliberate ("two messages = two intents"); what was missing was a message that
does not push the user across that boundary by mistake. A duplicated exercise
name in one day then makes every later targeted edit on it `AMBIGUOUS_TARGET`.
`is_coach_error_fallback` was already true for that text, so the turn was not
persisted either — the conversation carried no trace of a change that happened.

**Fix.** `coach_plan_tools` records, request-scoped on `g` beside the mutation
budget, whether this turn moved persisted plan state (`applied`/`replayed` only
— `no_op` and every refusal do not count, and the getter fails safe to False).
`ai_coach._coach_tool_fallback(language, kind)` and the stream's
`_bedrock_work_error` consult it and switch to a new text /
`coach.reply_failed_plan_saved` key. Both providers, both transports, one
decision. Deliberately NOT changed: the new text is still an error fallback, so
the AI-chat quota refund and the B16 do-not-persist rule behave exactly as
before. Only the sentence the user reads is different.

**Reviewer verification (this pass, all reviewer-rerun).**

```
independent falsification probe   77 assertions, all passing, then deleted
                                  (duplicate add/undo state, flag OFF + fail-closed,
                                  budget, 17 smuggled fields, type traps, identity,
                                  transaction residuals, privacy, session/history)
architecture-guard mutation test  3 planted defects → 6 distinct guards failed → reverted
focused PR3 suite                 208 passed
adjacent suites (+ test_i18n)     423 passed
full non-load suite               see below
PostgreSQL concurrency (CI job)   15 passed on real postgres:16
  incl. tests/test_plan_mutation_history_pg.py   5 passed
PostgreSQL schema drift           flask db check → "No new upgrade operations
                                  detected", single head b3c4d5e6f7a8
```

The two PostgreSQL gates were listed as "not runnable locally" in the
implementation report. They are now closed locally as well as in CI, which
mattered because PR3 changed `plan_mutation/service.py`.

**Still not fixed (reported, not touched).** Three Minor items: the registry
capability text says "the first flag of the eight" while the header now reads
nine; `test_undo_reverses_the_newest_change_and_only_on_request` never tests
"only on request" (nothing server-side enforces it — it is prompt policy);
`test_a_repeated_undo_in_one_turn_does_not_reach_further_back` seeds a single
prior mutation, so it cannot observe the reach-back it names (the guarantee
itself holds — the reviewer probe proved it with two). Also unchanged: with the
flag OFF a plan-tool name carrying malformed JSON answers `INVALID_ARGUMENTS`
rather than `CAPABILITY_DISABLED`; the executor is never reached either way, so
no mutation is possible.
# Progress Redesign PR5 — Canonical Progress History & Final Convergence

Branch `feat/progress-redesign-pr5-history-convergence`, worktree
`.worktrees/progress-redesign-pr5`, based on `origin/main`
`f2887e5e524383ac8f50239f73cbd001abf83eee`, later restacked onto
`49a8af0` (#221, coach plan tools; not a Progress History change).
PR1 (#212), PR2 (#215), PR3 (#216) and PR4 (#219) are ancestors.
**Not merged, not deployed, no production config touched.**

Full architecture: **docs/PROGRESS_HISTORY.md**.

PR5 replaces the Progress page's legacy `/checkin-history` consumer
(browser-side newest-first sort, weight delta and 12-row cap) with a
server-owned reconstructed read model. A row means "your Progress state
through this check-in day" — not a persisted AxisAI decision from that
timestamp. Reconstruction is Istanbul-day-granular: `end_day=D` evaluates
the full calendar day, so a workout later on the same Istanbul day may be
included. `build_progress_summary` is not reused wholesale: its
body/profile read is current-state. Historical training uses
`training_progression(..., end_day=D)` plus PR2's pure mappings. Historical
weight comes from the anchored qualifying WeeklyCheckIn
(`yogunluk IS NOT NULL`); delta is against the previous qualifying row.
Workouts, check-ins, and live profile/target changes on a later Istanbul
day cannot rewrite a past row.

New package `app/services/progress_history/` (`models` / `queries` /
`payload` / `build_progress_history`). New `GET /api/progress/history`
(`@require_auth`, no range input, `Cache-Control: private, no-store`,
visible bound 12 + one context row for oldest delta / `has_more`). New
client module `static/progress_history.js`. `static/progress.js` only calls
`FitXProgressHistory.load()`. Legacy `GET /checkin-history` is unchanged
(Today and Home still consume it). No schema, snapshot, cache, provider or
historical Axis Insights.

Hermetic Chromium matrix (WSL Ubuntu, Sprint-0 venv +
`PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/axisai-sprint0-playwright`):
9 states × 4 viewports = **36/36 pass**, seed drift empty.
Evidence: `docs/frontend-readiness/progress-pr5/validation-manifest.json`
plus 18 curated screenshots (390 and 1440 for every state).
Command:
`python -m scripts.frontend_audit.progress_pr5_matrix --output docs/frontend-readiness/progress-pr5`

| State | 390 | 768 | 1280 | 1440 |
|---|---|---|---|---|
| empty | pass | pass | pass | pass |
| sparse_only | pass | pass | pass | pass |
| one_baseline | pass | pass | pass | pass |
| mixed | pass | pass | pass | pass |
| on_track_expanded | pass | pass | pass | pass |
| needs_attention_expanded | pass | pass | pass | pass |
| missing_weight | pass | pass | pass | pass |
| bounded_has_more | pass | pass | pass | pass |
| endpoint_failure | pass | pass | pass | pass |

Rollback: revert the commit. No schema, no migration, no flag, no cache, no
provider cleanup.

A later same-branch semantic-precision fix: Progress History copy and
docs now say reconstruction is through the Istanbul check-in *day*.
`end_day=D` already evaluated the full calendar day; "up to this
check-in" over-claimed timestamp isolation. A same-day later workout is
in V1 scope. Isolation means data after the analysis day does not leak
backward. No `training_progression`, snapshot, or query change. The
prior 36-cell hermetic matrix was not rerun: localized drilldown copy
only.


---

# Sprint 11 PR4 — Canonical Exercise Authority (local, 2026-08-22)

Branch `sprint11-pr4-canonical-exercise-authority`, based on PR3 (`95fb056`).
**Not merged, not pushed, not deployed.** Full architecture:
**docs/TRAINING_GENERATOR.md** → "Exercise identity — the canonical catalog";
the Adaptive Coaching door is **docs/ADAPTIVE_COACHING.md** §§2, 7, 16.
This section is the handoff only — it does not restate the architecture.

## What shipped

Before PR4 an exercise was whatever string the provider wrote, and nothing in
the app could say what that string meant. PR4 makes a **server-owned catalog the
single authority on exercise identity** and enforces it at both plan-write
doors: `POST /training-plan/save` and the Adaptive Coaching mutation boundary.

- `app/services/exercise_catalog.py` + `training_assets/exercises.json` —
  **catalog version 1, 73 exercises**, 60 aliases, 133 normalized lookup keys.
  Stable IDs (`^ex_[a-z0-9_]+$`), closed equipment / movement / region
  vocabularies, exact resolution, no fuzzy matching anywhere.
- Provider vocabulary is constrained to canonical display names for the accepted
  context; `exercise_id` is not a generation schema key.
- `training_generation/exercise_resolution.py` canonicalizes a validated plan
  once, outside the repair boundary.
- `training_generation/exercise_context_token.py` — HMAC-signed, user-bound
  `exercise_context_token` minted at generate and required at save.
- `plan_mutation/` resolves canonical plans through the same catalog; legacy
  name-only plans keep their old behaviour and are never upgraded.
- Task 6 removed the last duplicate authority: `exercise_knowledge_base.py` is
  **deleted**. Its only live symbol moved verbatim to
  `training_generation/movement_coverage.py`; its unwired `EXERCISE_KB` table of
  risk/difficulty/progression opinions was dropped, not migrated.

## Deployment and rollback

**No migration, no table, no backfill, no feature flag.** The catalog ships as
code, so there is nothing in the database to undo. Rollback is reverting the
commits. `tests/test_migration_graph.py` asserts the Alembic head is still
`c1d2e3f4a5b6` across 36 revision files and that PR4 added none.

> Note for whoever merges: `origin/main` has moved ahead (it now asserts
> `c2d3e4f5a6b7`, from the Adaptive Coaching PR4 confirmation migration).
> Reconciling that literal is the merge's job. This branch is correct as it
> stands — `c2d3e4f5a6b7` does not exist in its history.

## Legacy compatibility — what deliberately did NOT change

Most stored rows are still pre-PR4. Nothing was migrated, so this had to be
proved rather than assumed:

- Bare-list and `{"program": …}` legacy plans still load through the presenter,
  workout state, workout-session fingerprinting, Adaptive Coaching context and
  training history.
- The session fingerprint hashes ordered `isim` values only — re-saving the
  same week canonically does **not** stale a running session. (A week whose
  exercises actually changed still does.)
- `exercise_id` never reaches a client: the bounded public day projection emits
  exactly `isim` / `set` / `tekrar` / `dinlenme` / `not`.
- An ambiguous legacy name is refused, never resolved by position and never
  given a fabricated ID.

**The legacy logging gap is real and was left open on purpose.**
`WorkoutLog.exercise_name` is a name, not an `exercise_id`. Historical logs
cannot be joined to catalog identity, and renaming a catalog entry does not
retroactively rename what is already logged. Filtering history through the
catalog would silently delete part of a user's past; migrating it would be an
irreversible reinterpretation of rows written before the catalog existed. If a
later PR wants the join, it needs its own design — not a backfill bolted onto
this one.

## Verification

Task 6 command (all green):

```
python -m pytest -q tests/test_sprint11_exercise_authority.py \
  tests/test_training_history.py tests/test_workout_session.py \
  tests/test_workout_state.py tests/test_adaptive_plan_context.py \
  tests/test_migration_graph.py tests/test_plan_mutation_architecture.py \
  tests/test_coach_plan_tools_architecture.py
```

Architecture guards live in `tests/test_sprint11_exercise_authority.py`: no
legacy KB and no fuzzy/`difflib`/`rapidfuzz` path (scanned over the *executable*
text of a file set derived from the package directories, so a new module cannot
escape it), zero SQL for a full week of resolution (engine event listener, not a
mock), provider schema never accepts `exercise_id` at the generation call site,
catalog never persists, save validates before `delete()`, provider budget stays
2 completions / 1 repair, generation never imports the mutation journal.

Every guard was neuter-tested — the guarded behaviour broken in production code,
the guard confirmed to fail, then restored. Details in the Task 6 report under
`.superpowers/sdd/2026-08-21-sprint11-pr4-canonical-exercise-authority/`.

## Deferred

Automatic substitution · a `WorkoutLog` → catalog join or history backfill ·
mobile generate contract · typed replace on save · generate idempotency ·
per-exercise progression or risk metadata (the deleted `EXERCISE_KB` fields were
never reviewed and are not product truth).
