# Cognito Registration Foundation

Date: 2026-07-09

## Architecture

Sprint 1 uses Amazon Cognito User Pools for registration and email verification
without changing the existing frontend. Flask remains the only integration point:
browser forms submit to the existing `/register`, `/verify`, and `/verify/resend`
routes, and those routes call `app/services/cognito_service.py`.

Routes must not call `boto3` directly. `cognito_service.py` owns the Cognito
client, `SignUp`, `ConfirmSignUp`, `ResendConfirmationCode`,
`USER_PASSWORD_AUTH`, secret hash handling, and Cognito exception mapping.

Hosted UI and Authlib OAuth are intentionally disabled for this sprint:
`/login/cognito` and `/auth/cognito/callback` return 404, and login/register
templates do not render Cognito redirect links. All Cognito interaction happens
through backend API calls.

Configuration is read from `.env` through `app/config.py`:

- `AWS_REGION=eu-central-1`
- `COGNITO_USER_POOL_ID=eu-central-1_t8wbHpN3z`
- `COGNITO_APP_CLIENT_ID=122df28apoafq08mb02bo23juf`
- `COGNITO_CLIENT_SECRET=` only when the app client has a secret

## Registration Flow

1. The existing register page posts username, email, password, and optional
   language/referral data to `/register`.
2. Flask validates the same local username, email, and password rules already
   used by the frontend. The email is normalized (trimmed + lowercased) before
   any further use; the normalized form is what reaches Cognito and the local
   `User` row, and the duplicate-email check is case-insensitive.
3. When Cognito is enabled, Flask calls `cognito_service.sign_up(...)`.
4. On Cognito success, Flask creates the local `User` row with `email`,
   `username`, `cognito_sub`, `created_at`, language/profile defaults, referral
   code support, and existing metadata behavior.
5. New Cognito users store `password_hash = NULL`. Legacy local users keep their
   existing password hashes and old authentication remains available.

## Verification Flow

The existing Verify Email page continues to use `/verify`.

- `POST /verify` calls `ConfirmSignUp`.
- `POST /verify/resend` calls `ResendConfirmationCode`.
- Cognito errors are mapped to friendly fixed messages and raw AWS messages are
  not returned to clients.

## Testing Coverage

The Sprint 1 regression tests cover native registration, duplicate/invalid
input, Cognito exception mapping, email verification, wrong/expired code mapping,
resend code, and legacy login behavior in:

- `tests/test_auth.py`
- `tests/test_cognito.py`
- `tests/test_cognito_idp.py`

## Sprint 2 — Login, JWT Validation & Sessions

Date: 2026-07-10

Sprint 2 builds native backend login on top of the Sprint 1 registration
foundation: password authentication against Cognito, cryptographic JWT
validation, and a server-side encrypted session store. `cognito_sub` is the
canonical auth identity; the local `password_hash` remains only as a
backward-compatibility fallback (see the `# TODO(Sprint 3)` markers).

### Authentication Flow

`POST /login` for a user whose `cognito_sub` is set (and `COGNITO_ENABLED`):

1. `cognito_service.authenticate(username, password)` runs
   `USER_PASSWORD_AUTH` and returns `{"tokens": {...}, "claims": {...}}`.
2. The returned **id token** is verified cryptographically with
   `cognito_jwt.validate_token(id_token, "id")` (JWKS) — claims are never
   trusted from the raw payload.
3. Identity integrity: the verified `sub` must be non-empty **and** equal the
   local `user.cognito_sub`; otherwise the login is rejected (401).
4. `_login_fresh(user)` establishes the Flask-Login session (which
   `session.clear()`s), then `session["cognito_sid"]` is written with a new
   `CognitoSession` row id.

### JWT Validation (`cognito_jwt.py`)

`validate_token(token, expected_use)` fetches and caches the pool JWKS and
verifies signature, `iss` (the user-pool URL), `aud`/`client_id`, `exp`, and
`token_use` (`id` vs `access`). Signature/issuer/audience/expiry/use mismatches
raise `TokenValidationError`. A key id absent from the cache triggers a single
JWKS refetch (key rotation) before failing.

### Session Lifecycle (server-side, encrypted)

The browser cookie carries only an opaque `cognito_sid`. Access and refresh
tokens live in the `cognito_session` DB row, Fernet-encrypted
(`app/services/session_store.py`). The Fernet key is `COGNITO_TOKEN_ENC_KEY`
when set, else derived from `SECRET_KEY`. Refresh tokens are therefore never
exposed to the client. Cookie flags remain HttpOnly / Secure / SameSite=Lax
from the existing config.

### Refresh Token Lifecycle

`session_store.get_valid_access_token(session_id)` returns the stored access
token, but if it is within `COGNITO_REFRESH_SKEW_SECONDS` (default 60) of `exp`
it first calls `cognito_service.refresh_tokens(refresh_token, cognito_username)`
(`REFRESH_TOKEN_AUTH`), re-encrypts the new access token, and returns it. A
failed refresh (`CognitoServiceError`, e.g. a revoked/expired refresh token)
deletes the row and raises `SessionInvalid` → the user is forced to re-login.

### Logout Flow

`GET /logout` (CSRF-guarded via Sec-Fetch-Site / Referer) calls
`cognito_service.global_sign_out(access_token)` best-effort (errors swallowed —
an expired token can fail), deletes the `CognitoSession` row, pops
`cognito_sid`, and clears the Flask-Login session. GlobalSignOut revokes every
refresh token for the user across devices.

### Protected Route Strategy

Every protected endpoint uses `@require_auth` (`app/auth_middleware.py`) instead
of `@login_required`. `require_auth`:

- rejects anonymous requests via `login_manager.unauthorized()` (→ `/login`);
- for a **legacy** user (no `cognito_sub`) passes straight through — local-login
  users keep working during migration;
- for a **Cognito** user, resolves `cognito_sid` → `get_valid_access_token`
  (refresh-on-expiry) → `validate_token(access, "access")`; any
  `SessionInvalid`/`TokenValidationError` invalidates the session and redirects
  to `/login`. Validated claims are stashed on `g.cognito_claims`.

`/logout` intentionally keeps `@login_required` (it must run for a user whose
Cognito session is already invalid, and it does its own CSRF check).

### Testing Coverage (Sprint 2)

- `tests/test_cognito_jwt.py` — JWKS validation, invalid/modified/expired token,
  wrong `token_use`, key rotation refetch.
- `tests/test_cognito_service_tokens.py` — `authenticate` / `refresh_tokens` /
  `global_sign_out` and error mapping.
- `tests/test_session_store.py` — encryption round-trip, refresh-on-expiry,
  dead-refresh invalidation, delete.
- `tests/test_require_auth.py` — anonymous reject, legacy passthrough, valid
  Cognito, missing/expired/dead-refresh invalidation.
- `tests/test_cognito_auth.py` — end-to-end: logout GlobalSignOut + row delete,
  concurrent independent sessions, session expiration → re-login, protected
  route after login.

## Sprint 3 — Native Password Recovery

`POST /forgot-password` accepts a username or e-mail address. Known e-mail
addresses are resolved to their canonical Cognito username, while unknown
identifiers are still passed to Cognito. Provider errors are deliberately
masked: known and unknown accounts receive the same generic 200 response, so
the endpoint does not become an account-enumeration oracle.

The canonical username is handed to `/reset-password` through server-signed
Flask session state, never through a query string or client-controlled hidden
field. This local reset context expires after 15 minutes. Missing, malformed,
or expired context is cleared and rejected before a confirmation call.

`POST /reset-password` validates the code and password fields locally, then
calls Cognito `ConfirmForgotPassword`. A successful confirmation is single-use:
all `CognitoSession` rows belonging to the local user are deleted, Flask-Login
state and browser session state are cleared, and the user must authenticate
again with the new password. Cognito remains the credential authority; the
application never writes a local password hash during recovery.

Provider code mismatch/expiry responses are fixed client-safe messages.
Cognito throttling maps to HTTP 429. Raw provider text, reset codes, passwords,
identifiers, and tokens are not logged.

### Cognito-only migration state

Registration and login now fail with a controlled 503 when Cognito is not
configured; there is no local-password fallback. Login calls Cognito first,
cryptographically validates the returned ID token, and resolves the local
profile only through its verified `sub`. No username lookup occurs before
Cognito authentication.

The `User.password_hash` column remains nullable and unchanged so existing
schema history and older databases stay compatible, but runtime authentication
never reads or writes it. The model password helpers, timing dummy hash, and the
obsolete `app/services/cognito.py` and `app/services/cognito_idp.py` modules have
been removed. `cognito_service.py` is the single native Cognito API boundary;
Hosted UI routes remain intentionally disabled.

### Session deadlines and identity binding

Application-managed Cognito sessions have two independent limits:

- `COGNITO_SESSION_IDLE_HOURS` defaults to 24 hours since `last_used_at`.
- `COGNITO_SESSION_ABSOLUTE_DAYS` defaults to 7 days since `created_at`.

User mismatch, absolute expiry, and idle expiry are checked—in that order—before
access-token refresh. The invalid row is deleted and the browser is redirected
to login. The absolute deadline cannot be extended by activity or refresh.

Every `@require_auth` request binds three identities: the Flask-Login user id,
the `CognitoSession.user_id`, and the local user resolved from the cryptographically
verified access-token `sub`. All three must identify the same row. Missing local
Cognito identity, missing session state, row/user mismatch, invalid token, or
verified-sub mismatch invalidates the session; there is no legacy passthrough.

### Intentionally public endpoints

The route-map audit in `tests/test_auth_audit.py` treats every endpoint as
protected unless it appears in this reviewed allowlist.

| Endpoint | Methods | Why public | Rate limit | CSRF |
| --- | --- | --- | --- | --- |
| `/static/<path>` | GET | Browser assets | Flask static handling | Not applicable |
| `/health` | GET | Liveness/deploy gate | None | Not applicable |
| `/welcome` | GET | Marketing/entry page | None | Not applicable |
| `/davet/<code>` | GET | Referral entry and first-party cookie handoff | None | Read/navigation only |
| `/set-language` | POST | Pre-login language choice | 30/hour | Origin + synchronizer token |
| `/register` | GET, POST | Account creation | 5/hour on POST | POST protected |
| `/login` | GET, POST | Session creation | 10/minute, 50/hour; plus 15/15 minutes per account on failed POST | POST protected |
| `/verify` | GET, POST | Cognito email confirmation | 10/15 minutes on POST | POST protected |
| `/verify/resend` | POST | Resend confirmation code | 3/15 minutes | Protected |
| `/forgot-password` | GET, POST | Start password recovery | 5/15 minutes on POST | POST protected |
| `/reset-password` | GET, POST | Complete password recovery | 10/15 minutes on POST | POST protected |
| `/login/cognito` | GET | Disabled Hosted UI compatibility route; always 404 | None | No state change |
| `/auth/cognito/callback` | GET | Disabled Hosted UI compatibility route; always 404 | None | No state change |

`GET /logout` is not a public business endpoint. It is authenticated teardown
using Flask-Login so an already-invalid Cognito session can still be cleared.
Because existing clients use navigation links, it retains GET with an explicit
`Sec-Fetch-Site`/`Referer` same-site guard, performs best-effort Cognito global
sign-out, deletes the local session row, and clears Flask-Login state.

### Known limitations and future enhancements

- Cognito challenge responses such as MFA and `NEW_PASSWORD_REQUIRED` are
  rejected safely but do not yet have native UI flows.
- Password-reset handoff state is held in the signed Flask session, so the code
  must be completed in the same browser context that initiated recovery.
- `/logout` remains a guarded GET for compatibility with existing navigation
  links. A future UI migration should make logout a CSRF-protected POST.
- Cognito IDP and JWKS calls are synchronous. Existing network timeouts bound
  failures, but higher scale should move identity-provider work behind dedicated
  capacity and monitoring.
- `app/services/cognito_jwt.py` still emits Authlib JOSE deprecation warnings;
  migrate that validator fully to `joserfc` before Authlib 2.0 compatibility is
  removed.
- Add browser-level recovery accessibility and visual regression coverage in
  addition to the current template, route, and JavaScript contract tests.

## Sprint 3 — Markalı Auth E-postaları (Resend)

Cognito'nun varsayılan düz e-postaları markalı AxisAI e-postalarıyla
değiştirildi; kod üretimi/doğrulaması Cognito'da kaldı. Ayrıntılı mimari:
docs/auth-emails.md.

- Kod e-postaları (doğrulama + sıfırlama) Cognito CustomEmailSender trigger'ı
  üzerinden gider: infra/cognito-email-sender (Lambda + KMS, SAM). Havuza
  bağlama runbook'u: infra/cognito-email-sender/README.md.
- `POST /verify` başarısı hoş geldin e-postası, `POST /reset-password` başarısı
  "şifren değiştirildi" e-postası gönderir (ikisi de best-effort — e-posta
  hatası auth yanıtını ASLA etkilemez; app/blueprints/auth.py `[AUTH-EMAIL]`).
- Şablonlar: app/services/email_templates.py (Lambda kopyasıyla bayt-eş,
  tests/test_email_templates_sync.py zorlar). Alıcı adresleri loglara maskeli
  yazılır (email_service.mask_email).

### Testing Coverage (Sprint 3 — e-posta katmanı)

- tests/test_password_reset.py — bildirim maili başarıda gider, e-posta hatası
  reset akışını düşürmez.
- tests/test_cognito_email_sender.py — Lambda: trigger→şablon eşlemesi,
  asla-yükseltme sözleşmesi, düz kodun loglanmadığı.
- tests/test_email_templates.py + test_email_templates_sync.py — şablonlar.
