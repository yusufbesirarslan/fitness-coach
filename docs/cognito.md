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


## Sprint 3 — Şifre Sıfırlama & Markalı Auth E-postaları (Resend)

Cognito'nun varsayılan e-postaları markalı AxisAI e-postalarıyla değiştirildi;
kod üretimi/doğrulaması Cognito'da kaldı. Ayrıntılı mimari: docs/auth-emails.md.

- Yeni route'lar (app/blueprints/auth.py): `GET/POST /forgot-password`
  (ForgotPassword — jenerik, enumeration-korumalı yanıt; 3/15dk) ve
  `GET/POST /reset-password` (ConfirmForgotPassword; 10/15dk; başarıda
  best-effort "şifren değiştirildi" e-postası).
- Yeni sarmalayıcılar (app/services/cognito_service.py): `forgot_password`,
  `confirm_forgot_password` — mevcut `_wrap`/Türkçe hata eşleme desenleriyle.
- Kod e-postaları (doğrulama + sıfırlama) artık Cognito CustomEmailSender
  trigger'ı üzerinden gider: infra/cognito-email-sender (Lambda + KMS, SAM).
  Havuza bağlama runbook'u: infra/cognito-email-sender/README.md.
- `POST /verify` başarısı hoş geldin e-postası gönderir (best-effort — e-posta
  hatası doğrulamayı ASLA düşürmez).
- Şablonlar: app/services/email_templates.py (Lambda kopyasıyla bayt-eş,
  tests/test_email_templates_sync.py zorlar).

### Testing Coverage (Sprint 3)

- tests/test_password_reset.py — forgot/reset route'ları: jenerik yanıt,
  throttle 429, bildirim maili, e-posta hatasının auth'u bloklamadığı.
- tests/test_cognito_idp.py — yeni sarmalayıcıların kwargs/hata eşlemesi.
- tests/test_cognito_email_sender.py — Lambda: trigger→şablon eşlemesi,
  asla-yükseltme sözleşmesi, düz kodun loglanmadığı.
- tests/test_email_templates.py + test_email_templates_sync.py — şablonlar.
