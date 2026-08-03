# Cognito Registration Foundation

Date: 2026-07-09

> This document covers the **web** flow. What the web and mobile clients share,
> what they deliberately do differently, and which of those differences is
> enforced in CI is in [AUTH_CONTRACT.md](AUTH_CONTRACT.md).

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
- `COGNITO_USER_POOL_ID=eu-central-1_kaX0SORRK`
- `COGNITO_APP_CLIENT_ID=3rdtrk3vl1dp0m1d19gdc3pqib`
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
5. New Cognito users store `password_hash = NULL`. **Local password authentication
   no longer exists** — it was removed in Sprint 3. Cognito is the sole credential
   authority; `tests/test_auth_audit.py` asserts `password_hash` appears nowhere in
   the auth path.
6. If the local commit fails after `sign_up` succeeded (e.g. two concurrent
   signups race on the same email and the second `INSERT` violates the unique
   constraint), the Cognito user survives without a local row — a **Cognito
   orphan**. The user is not locked out: at login,
   `auth._reconcile_local_user` links or creates the local row from the
   **cryptographically verified** ID-token claims. See "Orphan Recovery" below.

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
verifies signature (RS256), `iss` (the user-pool URL), `aud`/`client_id`, `exp`,
and `token_use` (`id` vs `access`). Signature/issuer/audience/expiry/use
mismatches raise `TokenValidationError`. A key id absent from the cache triggers
a single JWKS refetch (key rotation) before failing.

**This is the only JWT validator in the application.** `cognito_service._decode_claims`
delegates to it. Until Sprint 3 there were *two* — an Authlib one here and a
joserfc one in `cognito_service`, each with its own JWKS cache — which meant a
security fix could land in one and be silently missed in the other. Both are now
`joserfc` behind this single entry point (which also removes the Authlib JOSE
deprecation warnings and the Authlib 2.0 migration deadline).

`TokenValidationError.reason` is load-bearing, not cosmetic: `jwks_unavailable`
means *"the signature could not be checked"*, which is emphatically **not**
*"the signature is invalid"*. Callers must not conflate them — see the transient
vs. definitive table below.

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
- **has no legacy passthrough.** A user without a `cognito_sub` (or without a
  server-side session row) is invalidated, not waved through — see
  `tests/test_require_auth.py::test_user_without_cognito_identity_invalidated`;
- for a Cognito user, resolves `cognito_sid` → `get_valid_access_token`
  (refresh-on-expiry) → `validate_token(access, "access")`. Validated claims are
  stashed on `g.cognito_claims`.

#### Transient vs. definitive failure (H1)

`require_auth` distinguishes *"this session is dead"* from *"we could not reach
Cognito right now"*. This matters because invalidation is **destructive** — it
deletes the server-side session row, and that cannot be undone.

| Failure | Classification | Result |
|---|---|---|
| `NotAuthorizedException` (refresh token revoked/expired), invalid signature, wrong issuer/audience/use, expired, idle/absolute timeout, user mismatch | **definitive** | session row deleted → `/login` |
| `TooManyRequestsException`, `LimitExceededException`, `InternalErrorException`, `ServiceUnavailableException`, botocore connect/read timeout (no error code) | **transient** | `SessionTransient` → **503 + `Retry-After`, session preserved** |
| `TokenValidationError("jwks_unavailable")` — JWKS could not be *fetched* (≠ signature invalid) | **transient** | 503 + `Retry-After`, session preserved |

Why it matters: access tokens refresh roughly hourly and expire at roughly the
same time across a user population, and the JWKS cache is **cold on every fresh
container** (i.e. right after each deploy). Treating a Cognito throttle or a
brief network fault as "not authenticated" would produce a correlated mass
logout from a transient blip. The classification lives in
`session_store._TRANSIENT_COGNITO_CODES`.

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
| `/health` | GET | Liveness/deploy gate. **`?deep=1` is honored only from loopback or explicitly trusted CIDRs** (see below) | None | Not applicable |
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

#### `/health?deep=1` is internal-only (M3)

The shallow `/health` stays fully public (liveness). The **deep** view is served
only to loopback or `DEEP_HEALTH_TRUSTED_CIDRS` source addresses, because it discloses internal posture
(`login: ok|offline`, `redis`, `bedrock`, `fatsecret_proxy`, `limiter_storage`)
and triggers an outbound request per call. An anonymous caller watching for
`login: offline` would learn exactly when Redis is down and login is fail-closed
— i.e. the best moment to start a campaign. A public caller passing `deep=1`
receives the **shallow body with 200**, not a 403; a 403 would itself be a signal.

The default `DEEP_HEALTH_TRUSTED_CIDRS=172.17.0.1/32` admits the Docker bridge
gateway, because compose publishes `127.0.0.1:5000:5000` and the deploy gate's
`curl 127.0.0.1:5000` arrives through docker-proxy. Configure additional
comma-separated CIDRs only for trusted networks; do not allow all private ranges.
Real internet traffic arrives via nginx, which appends the true client IP with
`$proxy_add_x_forwarded_for`, and `ProxyFix(x_for=1)` reads the rightmost entry —
so a spoofed `X-Forwarded-For: 127.0.0.1` cannot pass this gate.

### Orphan Recovery (`_reconcile_local_user`)

`/register` creates the Cognito user **first**, then commits the local `User` row.
If that commit fails, the Cognito account survives with no local row — a *Cognito
orphan*. This is reachable without a database outage: two concurrent signups with
the **same email but different usernames** both pass the pre-check, both succeed
at Cognito (which keys on username), and the second local `INSERT` violates the
unique email constraint.

Such a user was previously **permanently locked out**: re-registering returns
`UsernameExistsException`, logging in returned 401 forever (no local row), and the
`UNSIGNED` public app client cannot `admin-delete` the stranded Cognito user, so
the application could not clean up either side.

At login, when a verified `sub` resolves to no local user, `_reconcile_local_user`
runs against the claims returned by `validate_token` (**never** `authenticate()`'s
unverified decode):

1. `email_verified` must be true and an email must be present — Cognito only sets
   this after the user proves mailbox control. This is the security anchor;
   without it, someone registering with another person's address could bind
   themselves to that person's local account.
2. A local row found by username or verified email **with `cognito_sub IS NULL`**
   is linked to the `sub`.
3. A local row bound to a **different** `sub` is never rebound — the request 401s
   and the existing row is untouched. (That case is the same human signing up
   twice with one email; they already have a working account to log into.)
4. Otherwise a local row is created from the verified claims.

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
- Add browser-level recovery accessibility and visual regression coverage in
  addition to the current template, route, and JavaScript contract tests.

## Native mobile authentication boundary

The native mobile client authenticates only through `/api/v1/`. Cognito remains
the password authority, while AxisAI returns its own opaque access and rotating
refresh credentials. The API never accepts Flask-Login cookies as a fallback.
Mobile session families are isolated per device, have a non-sliding seven-day
absolute lifetime, and persist only indexed SHA-256 credential hashes. Cognito
token material is Fernet-encrypted at rest and is never returned to the device.

`scripts/check_cognito_pool.py` reports the app client's mobile-relevant posture:
whether a client secret is present, token revocation state, token lifetimes, and
Cognito refresh-token rotation. The report deliberately emits only the boolean
presence of the client secret, never its value. Unexpected provider failures are
reported by exception type and stable AWS error code only; raw provider messages
and payloads are excluded.

The app-managed refresh credential is the rotating credential for this API.
Cognito refresh-token rotation may remain disabled because Cognito token
material stays server-side and renewal is serialized against the mobile session
family. Validate pool configuration with read-only `DescribeUserPool` and
`DescribeUserPoolClient`; this checker never mutates Cognito.

`MOBILE_AUTH_ENABLED` defaults to `0`. Disabled startup does not register the
`/api/v1` mobile blueprint, parse or require the derivation keyring, or query the
mobile tables for readiness. Existing web authentication, cookies, and CSRF are
unchanged. Enabled startup requires a valid independent keyring and active
version and performs the read-only database preflight for derivation-key
rotation. Every key version referenced by a still-replayable consumed parent
must remain in `MOBILE_AUTH_DERIVATION_KEYRING` through its grace deadline plus
`MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS`; enabled startup fails
closed if a referenced version was removed too early. There is no default key.

Use this staged production rollout order:

1. Merge and deploy with `MOBILE_AUTH_ENABLED=0`.
2. Apply the additive mobile-auth migration.
3. Provision the independent derivation keyring and active version.
4. Verify database and derivation-key readiness.
5. Enable mobile authentication in a controlled deployment.

Deploy a new key alongside old keys, make it active in a later deployment, and
remove an old key only after the retention window has drained.
`COGNITO_TOKEN_ENC_KEY` remains a separate mandatory Fernet key outside
development/test and must never be reused as a derivation root. This task does
not change live production configuration.

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
