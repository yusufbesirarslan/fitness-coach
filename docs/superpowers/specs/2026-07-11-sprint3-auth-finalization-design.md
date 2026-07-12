# Sprint 3 — Password Recovery, Security Hardening & Authentication Finalization

Date: 2026-07-11
Status: Approved design
Sprint source: `sprint3.txt`

## Objective

Finish the native Amazon Cognito migration by adding password recovery, removing
the remaining local-password authentication paths, binding every authenticated
request to a verified Cognito identity, hardening session and error handling,
auditing authorization coverage, and documenting the final authentication
architecture. The existing authentication UI and user journeys remain intact;
the only new surfaces are Forgot Password and Reset Password pages built from
the current auth-card components.

## Global Constraints

- Do not introduce Cognito Hosted UI, OAuth redirects, or a new design language.
- Preserve the existing auth layout, spacing, typography, colors, responsive
  behavior, language switch, theme control, CSRF integration, and accessibility
  conventions.
- Reuse `app/blueprints/auth.py`, `app/services/cognito_service.py`,
  `static/auth.css`, `static/auth.js`, `_head.html`, and the existing translation
  catalogs.
- Cognito is the only credential authority. No runtime path may authenticate
  against `password_hash` or generate application JWTs.
- `User.password_hash` remains nullable in the database for migration
  compatibility but is not read or written by runtime authentication code.
- `cognito_sub` is the canonical identity. Every protected request must resolve
  and bind the local `User` through the verified Cognito access-token `sub`.
- Never log passwords, verification codes, access/ID/refresh tokens, reset
  identifiers, or raw AWS error messages.
- Tests must remain hermetic: stub Cognito/JWKS calls and never use live AWS.
- No database migration is expected for Sprint 3.

## Chosen Approach

Password recovery uses a session-bound reset handoff. The Forgot Password route
stores a canonical Cognito username and initiation timestamp in the signed Flask
session, then redirects to Reset Password. The identifier is not placed in the
URL or trusted from a hidden client field. This is simpler than introducing a
signed handoff token and avoids leaking usernames or email addresses into URLs,
browser history, or access logs.

Alternatives rejected:

1. URL or hidden-field handoff: simpler but exposes and permits tampering with
   the reset identifier.
2. Signed expiring handoff token: secure and stateless, but duplicates guarantees
   already provided by the Flask session and adds unnecessary key/lifecycle code.

## Password Recovery Flow

### Forgot Password

`GET /forgot-password` renders `templates/forgot_password.html`. It uses the
same `auth-shell`, `auth-topbar`, `auth-card`, shared assets, language switch,
theme control, form controls, notices, and responsive behavior as Login and
Register. Login adds a small `Forgot Password?` link using `auth-link`.

`POST /forgot-password`:

1. Accepts one `identifier` field containing a username or email address.
2. Trims whitespace; email input is lowercased and looked up case-insensitively
   in the local `User` table to obtain the canonical Cognito username.
3. Calls `cognito_service.forgot_password(username)` using Cognito
   `ForgotPassword` and the existing app-client secret-hash behavior.
4. Stores only the canonical/reset identifier and an initiation timestamp in
   temporary Flask session keys.
5. Always returns the same generic success message and reset-page redirect for
   known and unknown accounts. `UserNotFoundException`, provider throttling, and
   other Cognito errors never reveal account existence or AWS internals.

Endpoint-specific IP and normalized-identifier limits apply in addition to the
global limiter. A rate-limited response may return 429, but its message remains
generic and contains no account-existence signal.

### Reset Password

`GET /reset-password` requires valid temporary reset state. Missing or stale
state redirects to Forgot Password with a safe message. The page renders
verification-code, new-password, and password-confirmation fields using existing
auth components. The new password uses the current strength/visibility behavior.

`POST /reset-password`:

1. Loads the canonical username only from temporary session state.
2. Validates presence and format of the verification code.
3. Requires matching password/confirmation values.
4. Applies the existing server-side password policy before contacting Cognito.
5. Calls `cognito_service.confirm_forgot_password(username, code, password)`
   using `ConfirmForgotPassword` and secret-hash behavior.
6. Maps code mismatch, expiry, password policy, authorization, and throttle
   errors to fixed friendly responses without exposing provider details.
7. On success, invalidates sessions and redirects to Login with a success flash.

Both POST routes use the existing global Origin/Referer plus synchronizer-token
CSRF protection. Cognito makes confirmation codes single-use; application reset
state is also single-use as described below.

## Single-Use Reset & Session Invalidation

Successful `ConfirmForgotPassword` is a security boundary:

1. Resolve the local user associated with the canonical reset username.
2. Delete every `CognitoSession` row for that user in one transaction. This
   invalidates all application-managed sessions across browsers/devices on their
   next protected request.
3. Call `logout_user()` for the current browser if it is authenticated.
4. Clear the entire Flask session, including the temporary reset username,
   initiation timestamp, CSRF state, Flask-Login identity, and `cognito_sid`.
5. Add only a success flash and redirect to `/login`.

Reset never calls `login_user()`, never restores a previous session, and never
creates a `CognitoSession`. The next successful login runs `_login_fresh()` and
creates a new random `cognito_sid`. Revisiting Reset Password after success fails
because temporary reset state no longer exists; reusing the code also fails at
Cognito.

## Cognito Service Boundary & Error Handling

`app/services/cognito_service.py` remains the sole native Cognito implementation
and adds:

- `forgot_password(username) -> None`
- `confirm_forgot_password(username, code, new_password) -> None`

Both methods reuse `_get_client`, `_maybe_secret`, `_secret_hash`, and `_wrap`.
Error mapping covers at least:

- `ExpiredCodeException`
- `CodeMismatchException`
- `LimitExceededException`
- `NotAuthorizedException`
- `TooManyRequestsException`
- `PasswordResetRequiredException`
- `UserNotFoundException`
- `UserNotConfirmedException`
- `InvalidPasswordException`
- `InternalErrorException`

Routes expose stable application responses only. Forgot Password collapses all
account/provider outcomes into its generic success response. Reset Password may
distinguish actionable invalid/expired-code, weak-password, and retry-later
states, but treats user-not-found/not-authorized as a generic invalid-or-expired
reset attempt. Logs contain only safe operation labels and exception types/codes.

## Legacy Authentication Removal

The following runtime paths are removed:

- local-password registration fallback when Cognito is disabled;
- local-password login branch and dummy hash timing comparison;
- `User.set_password` and `User.check_password`;
- password-hash helper imports and the dummy password hash;
- legacy JWT generation/session helpers if any remain active;
- unused native/Hosted-UI Cognito compatibility modules after import audits.

Authentication POST endpoints never fall back to local credentials. Production
fails fast when mandatory Cognito configuration is absent. Development/test
configurations receive a controlled unavailable response unless tests explicitly
stub and enable Cognito.

Profile, referral, onboarding, user metadata, and other application business
logic remain untouched. `password_hash` remains in the model and migrations as
an unused compatibility column.

## Authenticated Identity Binding

`@require_auth` continues to preserve Flask-Login's current-user/template UX,
but removes the legacy no-`cognito_sub` passthrough. For every protected request:

1. Require an authenticated Flask-Login session and a `cognito_sid`.
2. Load the corresponding `CognitoSession` and require its `user_id` to match the
   Flask-Login user.
3. Refresh the access token when needed.
4. Cryptographically validate signature, issuer, audience/client ID,
   expiration, algorithm, and `token_use=access`.
5. Require a non-empty verified `sub`.
6. Resolve the local user by `User.cognito_sub == claims["sub"]`.
7. Require that resolved user, Flask-Login user, and session row refer to the
   same local user.
8. Store validated claims on `g.cognito_claims` and continue.

Any mismatch, missing row, invalid token, dead refresh token, or timeout deletes
the application session and requires login. JWT/JWKS outage behavior remains
fail-closed for new authentication; no invalid token is accepted.

## Session Timeout & Replay Hardening

Two configurable application limits apply to `CognitoSession`:

- idle timeout: 24 hours by default;
- absolute lifetime: 7 days by default.

`last_used_at` enforces idle expiry and `created_at` enforces absolute expiry.
Expired rows are deleted and cannot refresh. Environment variables document both
defaults. Existing secure-cookie settings remain HttpOnly, Secure outside
development, and SameSite=Lax.

Replay resistance comes from random per-login session IDs, signed cookie state,
server-side encrypted tokens, CSRF protection on mutations, strict user/session/
`sub` binding, single-use Cognito reset codes, one-time local reset context, and
deletion of all local sessions after a password change.

## Authorization Audit

Tests introspect Flask's route map and require every non-public application
endpoint to carry `@require_auth`. `/logout` remains the documented teardown
exception: it uses `@login_required` so logout still works when a Cognito token
is dead and keeps its explicit same-site navigation guard.

The intentionally public allowlist is documented and includes only required
surfaces such as landing/login/register/verification/recovery, language choice,
health, static assets, public referral/welcome pages, and explicitly reviewed
public callbacks. Each public endpoint is reviewed for mutation, enumeration,
rate limiting, CSRF applicability, and sensitive output.

## UI Integration

New templates reuse `_head.html`, `/static/auth.css`, `/static/auth.js`, and
`/static/actions.js`. No inline CSS/JavaScript or new layout system is added.
Translation keys are added to both `locales/tr.json` and `locales/en.json` for
page labels, generic recovery success, password mismatch, code errors, reset
success, and retry states. Client rendering continues to use `textContent`.

The login success flash renders through the existing `auth-alert-success`
component. Existing Login, Register, Verify, and Setup markup/behavior remains
unchanged except for the Forgot Password link and reusable flash rendering.

## Testing Strategy

Implementation follows TDD and each stage must be green before the next stage.
Hermetic tests cover:

- Cognito request shapes and secret-hash handling for both new service methods;
- username and normalized-email recovery;
- indistinguishable known/unknown forgot-password responses;
- safe provider-failure and throttle behavior;
- valid, mismatched, malformed, expired, and reused reset codes;
- weak, mismatched, and valid new passwords;
- CSRF and endpoint rate limits;
- successful reset clearing all temporary reset state;
- deletion of every application session for the reset user;
- no automatic login after reset and a fresh random session on next login;
- existing-device reauthentication after reset;
- idle and absolute session expiry;
- concurrent sessions and reset invalidation;
- verified-`sub`/Flask user/session-row mismatch rejection;
- JWT signature, issuer, audience/client ID, expiration, and token-use rejection;
- shared auth assets/markup and absence of inline design forks;
- registration, verification, resend, login, refresh, logout, protected-route,
  middleware, unknown-account, wrong-password, and concurrent-session regression.

Static audit tests/searches verify that runtime code does not read
`password_hash`, local password helpers are absent, no duplicate Cognito
implementation remains, no auth code logs secrets, and no credentials are
committed.

## Documentation & Rollout

Implementation stages and commits:

1. Approved Sprint 3 design specification.
2. Cognito password-recovery service methods and error mapping.
3. Recovery routes and single-use reset lifecycle.
4. Forgot/Reset UI, login link, translations, and flash behavior.
5. Legacy authentication removal.
6. Identity binding, session timeouts, and token/security audit fixes.
7. Authorization audit and public-endpoint documentation.
8. Full regression/security audit, cleanup, and final documentation.

`docs/cognito.md` will describe the final architecture, lifecycle, password
recovery, security decisions, JWT validation, protected-route strategy,
migration summary, limitations, and future compatibility. `docs/handoff.md` will
record completed work, architecture/migration summaries, remaining non-auth
technical debt, and Production Readiness recommendations.

Each stage runs focused tests and commits only after passing. The final gate runs
the entire pytest suite plus static auth/secret/route audits.

## Future Compatibility

Keeping Cognito operations behind one service, using `cognito_sub` as the local
identity, and retaining one server-side row per device session permits future
Google/Apple federation, passkeys, optional MFA, custom email delivery, native
mobile clients, and multi-device sessions without restoring local-password
authentication or redesigning current pages. Those features remain out of scope
for Sprint 3.

## Success Criteria

- Forgot Password and Reset Password work through native Cognito APIs.
- Recovery pages match the existing authentication UI exactly.
- Forgot Password never reveals account existence.
- Reset is single-use locally and at Cognito; all application sessions are
  invalidated and login is required afterward.
- Registration, verification, login, refresh, logout, and protected routes remain
  functional.
- JWT validation and session identity binding fail closed.
- Every protected endpoint uses the approved authentication middleware strategy.
- Runtime authentication never uses `password_hash`.
- Cognito is the sole credential authority and `cognito_sub` the sole identity.
- Legacy and duplicate authentication code is removed without touching business,
  profile, referral, or onboarding logic.
- Security, authorization, regression, static, and documentation audits pass.
- The project is ready for the Production Readiness phase.
