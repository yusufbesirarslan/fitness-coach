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
   used by the frontend.
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
