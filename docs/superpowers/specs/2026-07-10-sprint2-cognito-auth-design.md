# Sprint 2 — Cognito Login, JWT Validation & Session Management (Design)

Date: 2026-07-10
Status: Approved (design)
Sprint source: `sprint2.txt`

## Objective

Complete the migration of the **login** path to Amazon Cognito: authenticate
against Cognito, validate the returned JWTs with real cryptography (JWKS),
manage sessions securely, support refresh tokens, do a proper GlobalSignOut on
logout, and put every protected endpoint behind a single reusable auth
middleware — **without changing the frontend or UX** and while keeping legacy
username/password login working until Sprint 3.

Sprint 1 already delivered: the native Cognito service layer, registration,
email verification, resend, `cognito_sub` persistence, and env config.

## Guiding constraints (from the spec)

- Do NOT redesign UI, modify layouts, introduce Hosted UI, redirect to Cognito,
  or change existing frontend forms. Backend API calls only; continue using
  `boto3`.
- Preserve the existing frontend (Landing, Login, Register, Dashboard, Profile,
  Nutrition, Workout, Progress, Setup, Verify, Forgot-Password UI). Only connect
  the existing Login page to Cognito.
- Authentication identity is `cognito_sub`. `password_hash` is no longer used for
  authentication but remains for legacy compatibility.
- Do NOT remove legacy code; mark it `# TODO(Sprint 3)`.
- Migrations must be expand/contract (rollback does not undo boot-applied
  migrations — see CLAUDE.md A2).

## Core decisions (locked with the user)

1. **Auth model — unified `@require_auth` decorator, layered on Flask-Login.**
   Cognito is the *credential authority*; Flask-Login remains the *session
   mechanism*. A single `@require_auth` decorator replaces `@login_required` on
   every protected endpoint. `current_user`, templates, and the login-redirect UX
   are unchanged. This satisfies "every protected endpoint uses the auth
   middleware" without rewriting the session transport or `current_user` usage.

2. **Token storage — DB table, one row per session.** Cognito access + refresh
   tokens are stored server-side in a new `CognitoSession` table, encrypted at
   rest (Fernet), keyed by a random `session_id` carried in the signed
   Flask-Login cookie. Chosen over Redis (ephemeral; widens the one component the
   app tolerates losing) and over per-User columns (concurrent logins would
   overwrite each other). Works identically on local SQLite and prod Postgres,
   survives deploys, and supports the Concurrent Sessions / Session Expiration
   test cases naturally.

3. **One-time forced re-login accepted.** Cognito users with a live Flask session
   but no `CognitoSession` row (created before this deploy) are logged out once
   and re-login. Legacy users are unaffected. No grace fallback.

Rationale for not putting tokens in the Flask session cookie: the cookie is
*signed but not encrypted*, so its base64 payload is readable by the client — a
refresh token there would be exposed, violating "never expose refresh tokens".

## Architecture

```
LOGIN (existing username/password form, unchanged)
  → cognito_service.authenticate(username, password)   [InitiateAuth USER_PASSWORD_AUTH]
      returns { tokens: {access, id, refresh, expires_in}, claims: {sub,email,...} }
  → cognito_jwt.validate_token(id_token, expected_use="id")   [JWKS: sig/iss/aud/exp/token_use]
  → integrity: claims.sub must be non-empty AND == user.cognito_sub
  → _login_fresh(user)              [existing session-fixation-safe Flask-Login login]
  → session_store.create(user, tokens, cognito_username)
      → new CognitoSession row (tokens Fernet-encrypted); session_id → Flask session cookie

EVERY PROTECTED REQUEST (@require_auth)
  → Flask-Login authenticated?  no  → login_manager.unauthorized()  [redirect page / 401 JSON]
  → legacy user (cognito_sub is NULL)?  yes → pass through           [backward compat, Sprint 3]
  → Cognito user:
        load CognitoSession by session_id  (missing → invalidate + unauthorized)
        access_token = session_store.get_valid_access_token(session_id)
            valid           → proceed
            expired         → refresh_tokens(); update row; proceed
            refresh failed  → delete row; logout_user(); unauthorized()
        validate access_token via cognito_jwt (sig/iss/client_id/exp/token_use="access")
        attach claims to g.cognito_claims

LOGOUT (existing same-site guard kept)
  → global_sign_out(access_token)   [best-effort; revokes all user refresh tokens Cognito-side]
  → session_store.delete(session_id)
  → logout_user(); session.clear(); redirect(login)
```

## Components

### `app/services/cognito_jwt.py` — reusable JWT validator
- Uses `authlib.jose` + `cryptography` (both already in `requirements.txt`; **no
  new dependency**). No PyJWT.
- JWKS URL: `https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json`.
  Issuer: `https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}`.
- JWKS fetched once and cached process-wide; an unknown `kid` triggers a single
  refetch. Offline after first fetch.
- `validate_token(token, expected_use)` verifies **signature (RS256), `iss`,
  audience (`aud`==app_client_id for id tokens / `client_id`==app_client_id for
  access tokens), `exp`, and `token_use`==expected**. Returns validated claims or
  raises `TokenValidationError(reason)` where reason ∈ {malformed,
  invalid_signature, expired, wrong_use, wrong_audience, wrong_issuer,
  jwks_unavailable}.
- Transient JWKS fetch failure with no cache → `jwks_unavailable`; callers decide
  (login: 503; per-request refresh path: keep existing session + warn, to avoid
  mass-logout on a Cognito blip). A genuine validation failure is always
  fail-closed.

### `app/services/session_store.py` — session token store
- `create(user, tokens, cognito_username) -> session_id`
- `get(session_id) -> CognitoSession | None`
- `get_valid_access_token(session_id) -> str` (refreshes on/near expiry; raises
  `SessionInvalid` if refresh fails)
- `update_tokens(session_id, access_token, exp)`
- `delete(session_id)`
- Owns Fernet encrypt/decrypt. Key from `COGNITO_TOKEN_ENC_KEY` (a valid Fernet
  key) if set, else derived deterministically from `SECRET_KEY`
  (`base64.urlsafe_b64encode(sha256(SECRET_KEY.encode()).digest())`).

### `app/auth_middleware.py` — `@require_auth`
Replaces `@login_required` on protected endpoints. Behavior in §"Architecture".
Built on Flask-Login so anonymous handling (`login_manager.unauthorized()`)
preserves the exact current redirect (pages) / 401 (JSON/XHR) behavior.

### `app/services/cognito_service.py` — additions
- `authenticate(username, password)` — `InitiateAuth USER_PASSWORD_AUTH`; returns
  raw tokens **and** decoded id-token claims. (The existing `initiate_auth`,
  which returns claims only, is refactored to delegate here so Sprint 1 callers
  /tests keep working; caller in `auth.py` updated to the new shape.)
- `refresh_tokens(refresh_token, cognito_username)` — `InitiateAuth
  REFRESH_TOKEN_AUTH` (+ `SECRET_HASH` when the app client has a secret). Returns
  new access token (+ id token) and expiry. Refresh tokens are not rotated unless
  Cognito rotation is enabled.
- `global_sign_out(access_token)` — `GlobalSignOut`.
- Error map extended with `PasswordResetRequiredException` and
  `InternalErrorException`; existing `NotAuthorizedException`,
  `UserNotConfirmedException`, `UserNotFoundException`, `TooManyRequestsException`
  already mapped. Raw AWS messages are never returned.

## Data model & migration

New `CognitoSession` model (expand-only additive migration; safe under A2):

| column | notes |
|---|---|
| `id` | PK |
| `session_id` | random `secrets.token_urlsafe(32)`; **named unique index**, indexed; stored in Flask cookie |
| `user_id` | FK `user.id` `ON DELETE CASCADE`, indexed |
| `cognito_username` | needed to compute `SECRET_HASH` on refresh |
| `access_token` | Fernet-encrypted text |
| `refresh_token` | Fernet-encrypted text |
| `access_token_exp` | DateTime (UTC) |
| `created_at` | DateTime, default utcnow |
| `last_used_at` | DateTime; touched by `@require_auth` |

Migration authored via `FITX_SKIP_DB_INIT=1 flask --app starter db migrate`,
reviewed, committed. Named unique index (`uq_cognito_session_session_id`) to
match the project's index-not-inline-constraint convention (avoids
schema-drift-guard drift; see MEMORY schema-drift note).

## Session & refresh lifecycle

- Proactive refresh skew: default 60s before `access_token_exp`
  (`COGNITO_REFRESH_SKEW_SECONDS`).
- Expired access token → refresh, update row, proceed.
- Dead/revoked refresh token → delete row, `logout_user()`, redirect to login.
- Logout → GlobalSignOut (revokes all the user's refresh tokens) → delete row →
  clear session.

## Protected-route audit

Mechanical swap of `@login_required` → `@require_auth` across the protected
blueprints. `current_user` and templates untouched. This is the concrete way
"every protected endpoint uses the middleware" is satisfied.

**Explicit exception — `/logout` keeps `@login_required`.** Logout is auth
*teardown*: it must succeed even when the Cognito access token is already expired
or the refresh token is dead. `@require_auth` would try to refresh/validate and
could redirect a user who is simply trying to sign out. So logout requires only
an authenticated Flask-Login user, then does best-effort `GlobalSignOut` (ignore
errors), deletes the `CognitoSession` row, and clears the session. This exception
is intentional and documented, not an unprotected route.

## Legacy isolation (Sprint 3)

- `User.check_password` / the local-password branch in `login()` / the
  `password_hash` column: annotated `# TODO(Sprint 3): remove legacy local-password auth`.
- `@require_auth` passes legacy (no `cognito_sub`) users through on session auth
  only. Fully removable in Sprint 3 once all users are Cognito-backed.
- `cognito_idp.py` is a near-duplicate of `cognito_service.py`; flagged for Sprint
  3 consolidation (not touched now to limit blast radius).

## Error handling

Cognito exceptions → friendly, fixed user-facing messages (Turkish UI strings via
existing map). Handled: `NotAuthorizedException`, `UserNotConfirmedException`
(→ `/verify` redirect signal, existing behavior), `PasswordResetRequiredException`,
`UserNotFoundException`, `TooManyRequestsException`, `InternalErrorException`.
Never expose AWS exception text or codes to clients.

## Security

- Rate limiting on `/login` already present (10/min; 50/hr per-IP; 15/15min
  per-username on failures). Kept.
- Email normalization / input validation already present in `validators.py` and
  registration. Login stays **username-based** because the form cannot change;
  email normalization applies where emails are handled.
- Constant-time behavior: the existing dummy-hash timing equalization on the
  legacy path is retained.
- Cookies already `HttpOnly` + `Secure` (prod) + `SameSite=Lax` (config.py).
- No sensitive logging: tokens, passwords, and JWTs are **never** logged — only
  error codes / exception types. New code audited for this.

## New / changed config (`app/config.py`, `.env.example`)

- `COGNITO_TOKEN_ENC_KEY` — optional Fernet key for token-at-rest encryption;
  derived from `SECRET_KEY` if unset.
- `COGNITO_REFRESH_SKEW_SECONDS` — optional; default 60.
- Derived (no env): Cognito issuer + JWKS URL from `COGNITO_USER_POOL_ID` /
  `COGNITO_REGION`.

## Testing (`tests/test_cognito_auth.py`)

The 12 spec cases: Successful Login, Incorrect Password, Unknown Email/Username,
Unverified User, Expired Access Token, Expired Refresh Token, Invalid JWT,
Modified JWT, Logout (GlobalSignOut called), Protected Routes (anonymous redirect
+ authed allow), Concurrent Sessions (two independent rows), Session Expiration.

JWT tests use a **locally generated RSA keypair and a fake JWKS** so tokens can be
signed, tampered, and expired entirely offline — no network and no real Cognito.
`boto3` Cognito calls are stubbed. Existing Sprint 1 auth tests must stay green.

## Documentation

Update `docs/cognito.md` and `docs/handoff.md` with: Authentication Flow, JWT
Validation, Session Lifecycle, Refresh Token Lifecycle, Logout Flow, Protected
Route Strategy, and Sprint 3 remaining debt / follow-ups.

## Success criteria

- Login authenticates via Cognito; frontend unchanged.
- JWT validation is production-ready (JWKS; sig/iss/aud/exp/token_use).
- `@require_auth` protects every secured route.
- Refresh flow works; dead refresh forces re-login.
- Logout does GlobalSignOut and destroys the local session.
- `password_hash` is no longer used for authentication; `cognito_sub` is identity.
- Legacy auth is isolated behind `# TODO(Sprint 3)` and still functional.
- Docs updated; auth code reviewed; every protected endpoint uses the middleware.

## Out of scope (Sprint 3+)

Forgot/reset password, legacy code removal, `cognito_idp.py`/`cognito.py`
consolidation, MFA/challenge flows (already explicitly rejected as unsupported).
