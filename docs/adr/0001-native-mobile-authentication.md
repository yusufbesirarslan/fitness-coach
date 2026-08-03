# ADR 0001: Native Mobile Authentication

- Status: Accepted
- Date: 2026-07-28
- Decision owners: AxisAI backend and mobile architecture
- Scope: Backend PR4 only
- Cross-client contract: [../AUTH_CONTRACT.md](../AUTH_CONTRACT.md) — what this
  path shares with the web path, and the machine-readable error envelope below
  recorded there as an intentional difference

## Context

AxisAI web authentication uses Flask-Login and a signed browser cookie containing
an opaque `cognito_sid`. Cognito access and refresh tokens are encrypted in a
server-side `CognitoSession` row. Protected web routes resolve three identities:
the Flask-Login user, the session row owner, and the `sub` from a validated
Cognito access token.

The Flutter client cannot depend on browser cookies, browser redirects, or
Flask-Login. It needs a versioned JSON contract with deterministic login,
refresh, logout, current-user resolution, and machine-readable errors. Existing
web behavior must remain unchanged.

The existing Cognito app client is console-managed rather than defined in IaC.
Repository evidence describes it as a public/unsigned client and leaves
`COGNITO_CLIENT_SECRET` empty by default. The live app client's secret,
revocation, rotation, and token-lifetime settings have not been verified and
must not be changed or deployed in this PR.

## Decision

AxisAI will issue its own opaque mobile credentials. Cognito remains the
credential authority, while the AxisAI backend owns mobile session transport,
refresh rotation, revocation, and application-user resolution.

The mobile contract is isolated under `/api/v1/`:

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/account/me
```

The Flutter application is not modified in PR4.

### Dark launch and rollout

`MOBILE_AUTH_ENABLED` defaults to `0`. While disabled, AxisAI does not import or
register the mobile blueprint, parse or require the mobile derivation keyring,
or query the mobile tables for derivation-key readiness. Web routes,
Flask-Login, browser cookies, CSRF, and existing Cognito web sessions remain
unchanged. When the flag is `1`, the independent derivation keyring and active
version are mandatory, mobile readiness fails closed, and the complete approved
`/api/v1/` contract is registered without weaker defaults.

Production rollout is deliberately staged:

1. Merge and deploy the application with mobile authentication disabled.
2. Apply the additive mobile-auth migration.
3. Provision the independent derivation keyring and active version.
4. Verify database and derivation-key readiness.
5. Enable mobile authentication in a controlled deployment.

There is no insecure default derivation key, and PR4 does not change live
production configuration.

## Alternatives

### Direct Cognito access tokens

Flutter would authenticate directly with a dedicated public Cognito app client
and send Cognito access tokens to AxisAI. This is rejected for PR4 because it
requires a separately verified native app-client configuration and moves
provider errors, refresh ownership, revocation, and token lifecycle into the
mobile client. It also bypasses the backend's existing encrypted-token session
foundation.

### Backend-mediated Cognito tokens

AxisAI would authenticate credentials but return Cognito access and refresh
tokens to Flutter. This is rejected because it distributes Cognito refresh
tokens to devices and still requires backend revocation state if locally
validated JWTs must stop working immediately after logout.

### AxisAI opaque mobile sessions

Selected. Cognito tokens remain encrypted on the backend. Mobile clients receive
only high-entropy AxisAI credentials. The design preserves provider
encapsulation, immediate local revocation, multi-device isolation, stable error
mapping, and reuse of the existing Cognito/JWKS boundary.

## Session Model

One `MobileAuthSession` represents one device login and one credential family.
It stores:

- A non-secret public family identifier
- `user_id`, Cognito username, and expected Cognito `sub`
- Fernet-encrypted Cognito access and refresh tokens
- Cognito access-token expiry
- Original mobile refresh absolute expiry
- Revocation timestamp and reason
- An optimistic version counter
- Created, last-used, and updated timestamps

Separate access- and refresh-credential rows store only indexed, unique
SHA-256 hashes. Raw mobile credentials are never persisted, encrypted, logged,
or included in telemetry.

Initial login credentials are generated from at least 32 bytes from Python's
cryptographically secure `secrets` source. Replacement credentials are
deterministic 256-bit PRF outputs as defined below, seeded by both an independent
256-bit server key and the 256-bit parent refresh credential. All credentials
are the unpadded base64url encoding of exactly 32 bytes. Input is rejected unless
it decodes to 32 bytes and re-encodes byte-for-byte to the presented ASCII form.
The indexed database digest is SHA-256 of that canonical ASCII form. Credential
comparison is hash lookup followed by constant-time hash comparison where an
in-memory comparison is necessary.

Default configuration:

```text
MOBILE_AUTH_ACCESS_TTL_SECONDS=900
MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS=7
MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS=10
MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS=60
MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS=0
MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS=300
MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION=<configured-version>
MOBILE_AUTH_DERIVATION_KEYRING=<secret version-to-key mapping>
```

The lifetime, validator-skew, and key-retention values are application
configuration, validated as non-negative or positive as appropriate and bounded
at startup. The mobile JWT validator uses the same configured clock-skew value
as the coverage calculation; the default zero preserves the current strict
expiry behavior. The derivation keyring has no insecure default. Every decoded
key must contain at least 256 bits, and the configured active version must
exist. Invalid or missing key configuration fails startup. Refresh never changes
the original absolute expiry.

## Login

Login accepts JSON only and requires username and password. It applies the
existing IP and per-account throttles and the existing fail-closed login policy.
The backend calls Cognito `USER_PASSWORD_AUTH`, validates the returned ID token,
validates the returned access token, and resolves or reconciles the local user
from the verified `sub`.

Login fixes `now` once for the transaction, sets the family absolute expiry to
`now + MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS`, and calculates:

```text
access_expires_at = min(
    now + MOBILE_AUTH_ACCESS_TTL_SECONDS,
    family_absolute_expires_at
)
coverage_deadline = (
    access_expires_at + MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS
)
renewal_trigger = max(
    now + MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS,
    coverage_deadline
)
```

The Cognito expiry is taken from the verified access JWT `exp`. If it is at or
before `renewal_trigger`, login renews the provider token set with the returned
refresh token before creating the family. The resulting verified Cognito access
token must expire strictly after `coverage_deadline`. Otherwise login returns
retryable `AUTH_TEMPORARILY_UNAVAILABLE`, emits a safe structured event, and
does not persist a family or return opaque credentials.

A successful login creates a new mobile session family and returns:

```json
{
  "session": {
    "type": "opaque",
    "token_type": "Bearer",
    "access_credential": "opaque-value",
    "refresh_credential": "opaque-value",
    "access_expires_at": "ISO-8601 UTC timestamp",
    "refresh_expires_at": "ISO-8601 UTC timestamp"
  }
}
```

No Flask login is established, no browser session is read or written, and no
Cognito token or provider metadata is exposed.

## Refresh Rotation

The first successful use of refresh generation N returns a new access
credential and refresh credential for exactly one child generation N+1.
Refresh credentials are one-time members of a session family, subject only to
idempotent replay of that committed response during the grace window.

The refresh transaction locks the session family and presented credential row
with `SELECT ... FOR UPDATE` on PostgreSQL and also uses an optimistic version
predicate. A version conflict retries a bounded number of times or returns a
retryable temporary error; it never issues an untracked credential.

On first use:

1. Validate the hash, family state, original absolute expiry, and credential
   state while holding the lock.
2. Fix one transaction `now` and calculate the child access expiry as
   `min(now + MOBILE_AUTH_ACCESS_TTL_SECONDS, family_absolute_expires_at)`.
3. Calculate `coverage_deadline` and `renewal_trigger` with the same formulas
   used by login.
4. Renew the encrypted Cognito token set when its verified access-token expiry
   is at or before `renewal_trigger`. Verify that the resulting provider access
   token expires strictly after `coverage_deadline`.
5. Select the active derivation-key version and fixed child issuance and expiry
   timestamps.
6. Mark the presented refresh credential consumed, set its grace deadline, and
   record the child generation, key version, child row identifiers, and fixed
   issuance and expiry timestamps.
7. Revoke access credentials belonging to generations older than the new
   generation.
8. Derive exactly one access/refresh pair, create exactly one hashed access row
   and one hashed refresh row, and link them from the consumed parent.
9. Increment the family version once and commit all provider and opaque
   credential changes atomically.
10. Return the raw derived credentials only from process memory with the stored
   issuance and expiry timestamps.

If renewal fails temporarily or the renewed, fully validated provider access
token still expires at or before `coverage_deadline`, the transaction rolls
back. The parent remains unconsumed, no child or opaque credential row is
created, no credential is returned, and the response is retryable
`AUTH_TEMPORARILY_UNAVAILABLE`. A safe structured event records only request
ID, family identifier, key version where applicable, and a redacted failure
category.

A concurrent or repeated presentation of the same parent inside grace waits for
the family and parent locks. It then observes the committed child, recomputes the
same access/refresh pair, verifies both SHA-256 hashes in constant time against
the linked child rows, and returns that pair with the originally stored
timestamps. This replay path performs no write, version increment, child
creation, access creation, or Cognito renewal. A missing derivation key, missing
child, or hash mismatch returns retryable `AUTH_TEMPORARILY_UNAVAILABLE`, emits a
redacted high-severity event, and never issues a different or untracked pair.

Use of a consumed credential after grace or any known revoked refresh credential
is credential reuse. The entire affected local session family is irreversibly
revoked, its Cognito ciphertext is cleared, and the response is
`AUTH_REFRESH_FAILED`. An unknown random credential cannot be associated with a
family and therefore returns the same error without affecting other sessions.

Refresh rotation is an AxisAI rule independent of Cognito refresh-token
rotation.

## Idempotent Credential Derivation

`MOBILE_AUTH_DERIVATION_KEYRING` is an independently managed keyring. Its raw
keys are never stored in the database or logs and are not the Fernet key. For
key version V, the credential codec first derives a 32-byte replacement subkey
with HKDF-SHA-256 from the configured root key. The HKDF salt is the ASCII bytes
`axisai/mobile-auth/credential-derivation/salt/v1`. Its info is the ASCII bytes
`axisai/mobile-auth/replacement-subkey/v1`, one NUL byte, the unsigned 16-bit
big-endian byte length of the canonical ASCII family identifier, the family
identifier bytes, then unsigned 64-bit big-endian parent and child generations.

The codec then computes two HMAC-SHA-256 PRF outputs over the presented raw
parent refresh credential. Each message is its fixed ASCII label
(`axisai/mobile-auth/access/v1` or `axisai/mobile-auth/refresh/v1`), one NUL
byte, and the decoded 32 parent bytes. The 32-byte outputs are encoded with
unpadded base64url. This explicit HKDF and HMAC domain separation prevents
cross-purpose key reuse and does not use raw Fernet material as an HMAC key.

The consumed parent persists only non-secret derivation context: key version,
parent and child generations, linked child row identifiers, original child
issuance time, access expiry, refresh expiry, and grace deadline. Credential
rows still persist only indexed SHA-256 hashes. The parent raw credential is
supplied again by the retry and is never persisted.

Key rotation adds a new key version before making it active. A version used for
issuance must remain available until every parent issued with it is outside its
retry-grace interval, including configured clock-skew and deployment-drain
allowance represented by
`MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS`. Startup validates the
active key and every configured retained key. Startup/readiness preflight rejects
a deployment if a replayable parent references a missing version. A key version
cannot be removed until the last referencing grace deadline plus the retention
buffer has passed. Replays always use the recorded version, while new first-use
rotations use the active version.

## Cognito Token Renewal

The encrypted Cognito token set remains attached to the mobile session family.
During refresh, the backend checks the verified stored Cognito access expiry
while the family lock is held. It renews with the encrypted Cognito refresh token
when:

```text
cognito_access_expires_at <= max(
    now + MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS,
    child_access_expires_at + MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS
)
```

This retains the proactive 60-second provider leeway while also guaranteeing
that Cognito validation material outlives the opaque child access credential and
the validator's allowed clock skew.

Successful provider renewal atomically replaces the encrypted Cognito access
token, optional ID token, optional rotated Cognito refresh token, and provider
expiry before mobile credentials are committed. A definitive Cognito refresh
failure revokes the local family and returns `AUTH_REFRESH_FAILED`. A temporary
Cognito/network failure preserves the family and old refresh credential state,
rolls back the transaction, and returns retryable
`AUTH_TEMPORARILY_UNAVAILABLE`.

Renewed-token validation uses refresh-specific classification. Invalid
signature, malformed token, wrong issuer, wrong audience/client, wrong
`token_use`, expiry, missing required claims, and verified-subject mismatch are
definitive: only the affected family is irreversibly revoked, Cognito ciphertext
is cleared, all local credential rows are revoked, and `AUTH_REFRESH_FAILED` is
returned after the revocation commit succeeds. `jwks_unavailable` and temporary
Cognito/network failures remain retryable and preserve the parent and family.
`/api/v1/auth/refresh` never exposes the login-only
`AUTH_INVALID_CREDENTIALS` code.

The renewed access JWT is fully validated before its `exp` is trusted. It must
expire strictly after
`child_access_expires_at + MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS`.
Insufficient renewed lifetime is classified as temporary provider coverage
failure: the transaction rolls back without consuming the parent or issuing
opaque credentials and emits a safe structured event without provider text or
token material.

Because consumed-parent grace replay is recognized before provider-expiry
evaluation, only the first locked N-to-N+1 transition may renew Cognito. Replays
return the already committed opaque pair and provider state without a second
provider call.

The current Cognito app client uses `REFRESH_TOKEN_AUTH` according to repository
configuration. PR4 does not enable or assume Cognito refresh-token rotation.
If Cognito rotation is later enabled, the provider adapter must move to
`GetTokensFromRefreshToken` and persist the returned provider refresh token in
the same locked transaction.

## Protected Mobile Requests

Protected `/api/v1/` routes accept only:

```text
Authorization: Bearer <AxisAI opaque access credential>
```

They never authenticate from Flask-Login or browser cookies. The middleware:

1. Hashes and resolves the active access credential.
2. Rejects expired or revoked credentials with
   `AUTH_SESSION_EXPIRED`.
3. Decrypts and validates the Cognito access token signature, issuer,
   client/audience, expiry, `token_use=access`, and `sub`.
4. Requires the access row's family `user_id`, family `cognito_sub`, validated
   token `sub`, and resolved local `User.cognito_sub` to agree.
5. Places the resolved user and verified claims in dedicated request context.

Client-supplied user identifiers never select application data.

## JWKS Availability

JWKS entries are cached by `kid`. A token with a cached matching key is verified
with that key even if a refresh fetch temporarily fails. Signature failure with
a cached matching key is definitive cryptographic invalidity.

When no cached matching `kid` exists, the validator fetches JWKS once. A
temporary fetch failure then returns `AUTH_TEMPORARILY_UNAVAILABLE` and preserves
the session because verification could not proceed. It must not be mapped to an
invalid signature or forced logout. A successful refresh that still lacks the
token's `kid` is a definitive validation failure.

## Logout and Revocation

Logout accepts an available mobile access or refresh credential and is
idempotent. While holding the family lock, it:

1. Copies the provider refresh token into request-local memory if available.
2. Marks the local family revoked.
3. Revokes all access and refresh credential rows.
4. Clears the encrypted Cognito token columns.
5. Commits the local revocation.

After the commit, local logout is complete. The backend attempts Cognito
`RevokeToken` using only the request-local provider token. Provider failure is
logged with request ID, family identifier, safe provider category, and a metric
or structured event. It never restores local state, exposes raw provider text,
or changes the response.

Logout returns `204 No Content` after local revocation, including repeated calls
for an already revoked or unknown credential. The client clears local
credentials regardless of remote outcome. `AUTH_LOGOUT_FAILED` is not part of
the normal PR4 client contract.

Revocation is per mobile session family. Other device families remain locally
valid. Cognito `RevokeToken` is preferred over `GlobalSignOut`; live app-client
support for token revocation must be verified and documented but is not mutated
or deployed in PR4.

## Current User

`GET /api/v1/account/me` returns only fields supported by the existing model:

- `username` as the current public account identifier
- `display_name` from `full_name` with username fallback
- `profile_complete`
- `preferred_language`
- `goal`
- `goal_type`

Units, a separate onboarding flag, public UUID, and provider account state are
omitted because the backend does not currently store authoritative values for
them. Authentication provider identifiers and database primary keys are not
returned.

## Error Contract

Every non-204 error uses:

```json
{
  "error": {
    "code": "AUTH_INVALID_REQUEST",
    "message": "Safe user-facing message.",
    "retryable": false,
    "request_id": "server-generated-request-id"
  }
}
```

| Condition | HTTP | Code | Retryable | Client action |
|---|---:|---|---|---|
| Missing, malformed, or non-JSON input | 400 | `AUTH_INVALID_REQUEST` | false | Correct request |
| Invalid or unknown login credentials | 401 | `AUTH_INVALID_CREDENTIALS` | false | Correct credentials |
| Verification required | 403 | `AUTH_VERIFICATION_REQUIRED` | false | Start verification flow |
| Login or refresh throttled | 429 | `AUTH_RATE_LIMITED` | true | Honor `Retry-After` |
| Access credential missing, invalid, revoked, or expired | 401 | `AUTH_SESSION_EXPIRED` | false | Attempt one refresh if available |
| Refresh credential invalid, expired, revoked, or reused | 401 | `AUTH_REFRESH_FAILED` | false | Clear credentials and return to login |
| Cognito, JWKS, database-lock, or network failure classified as temporary | 503 | `AUTH_TEMPORARILY_UNAVAILABLE` | true | Preserve credentials and retry |
| Successful or repeated logout after local revocation | 204 | none | n/a | Clear local credentials |

Provider account-disabled responses that are not distinguishable from invalid
credentials by a stable Cognito error code remain
`AUTH_INVALID_CREDENTIALS` to avoid account enumeration. PR4 does not inspect
raw provider message text to manufacture `AUTH_ACCOUNT_DISABLED`.

## Web, CSRF, and CORS Compatibility

The existing web `/login`, `/logout`, password recovery, Flask-Login,
`CognitoSession`, cookie settings, redirects, and `@require_auth` behavior are
unchanged.

The `/api/v1/` blueprint is explicitly API-only and ignores browser cookies.
Its state-changing routes are excluded from browser synchronizer-token CSRF only
because they never authorize with ambient cookies. Existing CSRF checks remain
unchanged for every web route. No CORS origin is added and wildcard production
CORS is prohibited.

## Security and Observability

- Passwords, Authorization headers, raw credentials, token hashes, Cognito
  tokens, and provider payloads are never logged.
- Request IDs are always server-generated and returned in errors.
- Authentication headers are excluded or redacted from telemetry.
- Login keeps existing fail-closed distributed throttling.
- Refresh and logout receive per-IP and per-credential-family limits without
  logging the credential or hash.
- Revocation, refresh reuse, temporary JWKS failure, provider renewal failure,
  and remote logout-revocation failure emit safe structured events.
- Cognito ciphertext is cleared when a family is revoked or expires. Expiry
  cleanup is idempotent and tested.

## Infrastructure Boundary

No live Cognito or AWS configuration is changed. PR4 documents the following
undeployed checks or future IaC work:

- Verify the current app client has no secret or keep any secret server-only.
- Verify `EnableTokenRevocation` for per-session `RevokeToken`.
- Record access, ID, and refresh token lifetimes.
- Record Cognito refresh-token rotation state.
- Keep `PreventUserExistenceErrors=ENABLED`.
- Keep required password and refresh auth flows until a reviewed migration.
- Move the console-managed user pool/app client into reviewed IaC or strengthen
  the existing drift check.

## Deferred Flows

Registration, verification, password recovery, account deletion, Flutter secure
storage, Flutter refresh interception, authenticated mobile routing, and all
non-auth product APIs remain deferred.

## Rollback

The mobile blueprint and middleware are additive. Rollback disables or removes
the `/api/v1/` mobile auth registration while leaving web routes unchanged.
Database changes are expand-only: new mobile session tables can remain unused
after code rollback. No existing table or column is removed or reinterpreted.
Active mobile clients must be treated as signed out after rollback.
