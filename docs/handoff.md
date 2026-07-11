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
- Remaining technical debt and Sprint 3 follow-ups above are unchanged.
