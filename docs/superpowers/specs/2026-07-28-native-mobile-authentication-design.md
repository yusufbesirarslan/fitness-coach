# Native Mobile Authentication Backend Design

- Date: 2026-07-28
- Status: Approved for implementation planning
- Branch: `mobile/pr4-native-auth-foundation`
- Base: `origin/main` at `cab7c27e9d9270c7c2a112b642b2b66d43f85733`
- Durable decision: `docs/adr/0001-native-mobile-authentication.md`

## Objective

Add the minimum production-safe backend foundation for a future Flutter client:
opaque mobile login, rotating refresh, idempotent local logout, Bearer-protected
current-user lookup, stable errors, and complete web-auth compatibility.

PR4 changes only the Flask backend. It does not modify Flutter, deploy code or
infrastructure, call live Cognito with production users, or mutate AWS.

## Existing Foundation

The backend already provides:

- Cognito `USER_PASSWORD_AUTH`, `REFRESH_TOKEN_AUTH`, and `GlobalSignOut`
- A single JWT validator for signature, issuer, client/audience, expiry, and
  token use
- Safe distinction between definitive token failure and temporary JWKS or
  provider failure
- Fernet-encrypted server-side Cognito tokens
- Canonical user binding through `User.cognito_sub`
- Existing login throttling, request IDs, structured request logs, and web CSRF
- Web regression tests for login, logout, password recovery, sessions, and
  protected routes

Mobile support will be additive and will not reuse browser cookies as API
credentials.

## Components

### Versioned mobile blueprint

Create a dedicated API blueprint with `/api/v1` prefix. It owns mobile auth and
account projections. It never calls `login_user`, `logout_user`, or Flask
session APIs.

### Mobile session models

Add three expand-only models and an idempotent Alembic migration:

`MobileAuthSession` owns the device session family and encrypted Cognito token
set. `MobileAccessCredential` stores access hashes and expiry. A
`MobileRefreshCredential` stores refresh hashes, generation, parent,
consumption, retry-grace, and supersession state.

Required indexes and constraints:

- Unique indexed family public identifier
- Indexed `MobileAuthSession.user_id`
- Unique indexed access credential hash
- Unique indexed refresh credential hash
- Indexed refresh `(session_id, generation)`
- Indexed expiry/revocation fields used by cleanup
- Foreign keys with cascade deletion from user to session and session to
  credential rows
- Non-negative generation and version checks where supported

Mobile token columns contain SHA-256 hex or binary digests only. The family row
contains Fernet ciphertext only for Cognito token material.

### Credential codec

A focused service generates 32 random bytes with `secrets.token_urlsafe(32)` or
an equivalent 256-bit CSPRNG operation, hashes credentials with SHA-256, and
returns raw values only to the immediate route response. It also parses Bearer
syntax without accepting multiple, empty, or non-Bearer credentials.

### Mobile session service

This service owns login-session creation, access authentication, refresh
rotation, reuse detection, Cognito renewal, local revocation, expiry cleanup,
and safe logout metadata. Routes remain thin and do not implement token-state
rules.

### Mobile authentication middleware

A separate decorator resolves only AxisAI opaque Bearer credentials. It never
falls through to Flask-Login or cookie auth. It exposes a canonical mobile user
and verified Cognito claims through `flask.g`.

### Error factory

A single helper builds the approved error envelope using the existing
server-generated request ID. Routes and middleware map internal typed failures
to stable codes without exposing provider messages.

## Configuration

Add validated config values with these defaults:

| Setting | Default | Purpose |
|---|---:|---|
| `MOBILE_AUTH_ACCESS_TTL_SECONDS` | 900 | Opaque access lifetime |
| `MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS` | 7 | Fixed family lifetime from login |
| `MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS` | 10 | Consumed refresh retry window |
| `MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS` | 60 | Provider renewal threshold |
| `MOBILE_AUTH_REFRESH_RATELIMIT` | `30 per minute; 300 per hour` | Refresh abuse bound |
| `MOBILE_AUTH_LOGOUT_RATELIMIT` | `30 per minute` | Logout abuse bound |

TTL values must be positive. Access TTL must be shorter than refresh absolute
lifetime. Retry grace must be short and less than access TTL. Invalid production
configuration fails at startup instead of silently using unsafe values.

## Detailed Flows

### Login

1. Reject non-JSON bodies and missing username/password.
2. Apply current IP and normalized-username login limits and Redis fail-closed
   policy.
3. Call existing Cognito authentication.
4. Map verification-required, rate-limit, invalid-credential, and temporary
   failures to the mobile envelope.
5. Validate the Cognito ID token through the canonical validator.
6. Resolve/reconcile the local user using only verified claims.
7. Create a new family with original absolute expiry `login_time + configured
   refresh lifetime`.
8. Encrypt the Cognito token set.
9. Generate and hash one access and one refresh credential.
10. Commit before returning credentials.

If commit fails, no credential is returned. Raw password and token values are
not added to logs, exception strings, or telemetry.

### Protected request

1. Parse exactly one Bearer credential.
2. Hash it and resolve a non-expired, non-revoked access row.
3. Resolve its active family and local user.
4. Decrypt and validate the Cognito access JWT as `token_use=access`.
5. Compare family `user_id`, family `cognito_sub`, JWT `sub`, and
   `User.cognito_sub`.
6. Set `g.mobile_user`, `g.mobile_session`, and safe claims.

Any supplied user ID in query, path, or JSON remains ordinary untrusted input
and cannot replace the authenticated identity.

### Refresh transaction

1. Require a JSON `refresh_credential`.
2. Hash it and begin a transaction.
3. Resolve credential and family; lock both rows.
4. Verify the optimistic family version in the update predicate.
5. Reject and locally revoke known expired/revoked/superseded families.
6. If the credential was consumed:
   - inside grace, allow a bounded sibling child;
   - outside grace, revoke the family as credential reuse.
7. If Cognito access is expired or within leeway, decrypt the provider refresh
   token and renew the provider token set under the same lock.
8. On definitive provider refresh rejection, revoke the family.
9. On temporary provider failure, roll back without consuming the presented
   refresh credential.
10. Mark the presented credential consumed and set its grace deadline.
11. Supersede obsolete siblings when a branch advances.
12. Generate new access/refresh credentials and persist only their hashes.
13. Preserve the original family absolute expiry in the response.
14. Increment version, commit, and return the raw credentials.

The service performs a bounded retry on optimistic conflicts. Exhaustion maps
to `AUTH_TEMPORARILY_UNAVAILABLE`, not `AUTH_REFRESH_FAILED`.

### Logout

1. Accept an access credential, refresh credential, or neither; never require a
   browser cookie.
2. Resolve a known family without revealing whether it exists.
3. Lock and irreversibly revoke the local family and all child credentials.
4. Decrypt the provider refresh token into request-local memory, clear all
   provider ciphertext columns, and commit.
5. Attempt Cognito `RevokeToken` after the local commit.
6. Emit a safe structured event/metric on remote failure.
7. Return `204` for known, unknown, already revoked, and remote-failure cases.

The response body is empty. Flutter will clear local credentials regardless of
remote outcome.

### Current user

Authenticate through the mobile middleware and project only:

```json
{
  "user": {
    "username": "public-name",
    "display_name": "Display Name",
    "profile_complete": true,
    "preferred_language": "tr",
    "goal": "supported-current-value",
    "goal_type": "supported-current-value"
  }
}
```

Nullable model fields remain JSON `null`. The route does not expose local
database IDs, Cognito `sub`, email, provider claims, session identifiers, token
material, metadata blobs, or password fields.

## Refresh-Grace Branch Rules

Only credential hashes are persisted, so the backend cannot replay a lost
plaintext response. Retry grace therefore uses bounded branches:

- First use of refresh generation N creates child N+1 and consumes N.
- Reuse of N during grace may create one additional N+1 sibling.
- Both returned siblings are independently high entropy and initially valid.
- The first sibling that advances consumes itself and supersedes unused sibling
  leaves from the same parent.
- A consumed token outside grace, a superseded leaf, or a revoked known token
  revokes the full family.
- Consumed hashes remain as tombstones until family absolute expiry so reuse can
  be detected.

The allowed sibling count is a fixed security invariant, not client input. Unit
and transaction tests cover response loss, concurrent refresh, both sibling
orders, grace expiry, and reuse-family revocation.

## JWKS Cache Rules

Refactor key lookup around JWT `kid`:

- Decode only the untrusted header needed to select `kid`; do not trust claims.
- If cached JWKS contains that `kid`, verify with the cached key.
- Refresh JWKS on a cache miss, not on a known-key signature failure.
- If fetch fails and a matching cached key exists, use the cached key.
- If fetch fails and no matching cached key exists, raise
  `jwks_unavailable`.
- If fetch succeeds but the key remains absent, raise definitive invalid-key
  failure.
- Preserve algorithm pinning, issuer, client/audience, expiry, token-use, and
  subject checks.

Existing web callers inherit safer cache behavior without changing their
success or redirect contracts.

## CSRF and Cookie Isolation

The API blueprint is exempt from synchronizer-token CSRF only after the route
map establishes that every protected endpoint uses mobile Bearer authentication
and no endpoint authorizes from a Flask session. Add an audit test that fails if
an `/api/v1/` protected route lacks the mobile decorator or uses
`current_user`/`session`.

All existing web methods continue through the current CSRF hook. No CORS header,
origin, or dependency is added.

## Error Matrix

| Condition | HTTP | Code | Retryable | Client action |
|---|---:|---|---|---|
| Invalid JSON/request | 400 | `AUTH_INVALID_REQUEST` | false | Correct request |
| Invalid credentials | 401 | `AUTH_INVALID_CREDENTIALS` | false | Correct credentials |
| Verification required | 403 | `AUTH_VERIFICATION_REQUIRED` | false | Verify account |
| Rate limited | 429 | `AUTH_RATE_LIMITED` | true | Honor `Retry-After` |
| Access rejected | 401 | `AUTH_SESSION_EXPIRED` | false | Attempt one refresh |
| Refresh invalid/expired/revoked/reused | 401 | `AUTH_REFRESH_FAILED` | false | Clear and log in |
| Temporary Cognito/JWKS/lock/backend failure | 503 | `AUTH_TEMPORARILY_UNAVAILABLE` | true | Preserve and retry |
| Logout after local revocation | 204 | none | n/a | Clear local state |

The client-facing PR4 set intentionally excludes `AUTH_LOGOUT_FAILED`.

## Rate Limiting

Login retains current limits. Refresh is limited by remote address and a safe
prefix of an HMAC-derived rate-limit key; the raw credential and its SHA-256
database hash never enter logs or limiter keys visible outside the process.
Logout uses a separate inexpensive limit and always preserves idempotent local
semantics.

Rate-limit errors use the mobile envelope for `/api/v1/` and the existing web
response elsewhere.

## Test Strategy

Implementation is test-first. Provider calls are mocked; no production user,
credential, or AWS mutation is allowed.

### Contract tests

- Login success schema and exact allowed keys
- Login failure envelope
- Refresh success rotates both credentials
- Refresh failure envelope
- Logout exact empty `204`
- `/account/me` allowed and excluded fields
- Every error contains code, safe message, retryability, and request ID

### Rotation and concurrency tests

- Old access invalid after ordinary refresh
- Old refresh accepted only inside configured grace
- Grace retry returns a distinct access/refresh pair
- Maximum sibling bound
- First sibling advancement supersedes the other
- Reuse after grace revokes the family
- Unknown refresh does not revoke another family
- Absolute expiry never moves
- Concurrent refresh yields race-safe committed generations
- Optimistic conflict retry and exhaustion mapping
- Only hashes exist in database rows and logs

PostgreSQL row-lock behavior receives an opt-in concurrency test mirroring the
repository's existing `pg_concurrency` convention; SQLite unit tests exercise
the optimistic version path.

### Cognito renewal tests

- No provider refresh outside leeway
- Refresh at and inside leeway
- New provider token set encrypted and persisted atomically
- Optional provider refresh-token replacement
- Definitive provider failure revokes family
- Temporary provider failure rolls back token consumption
- Concurrent calls perform at most the expected renewal under lock

### Middleware and JWKS tests

- Missing/malformed Bearer
- Invalid, expired, revoked, and superseded access
- Valid request
- Wrong issuer, audience/client, token type, signature, expiry, or subject
- Unknown/disabled local user
- Client-supplied user ID cannot override identity
- Cached matching `kid` survives fetch failure
- Uncached `kid` plus fetch failure returns retryable 503 and preserves family
- Cached known-key invalid signature remains definitive

### Logout tests

- Successful local revoke and provider revoke
- Already revoked and unknown credential return 204
- Remote temporary/definitive failure still returns 204
- Provider failure produces safe event without raw token or error message
- Cognito ciphertext is cleared before remote call
- Other device families remain valid

### Web regressions

- Existing web login, protected routes, and logout
- Existing cookie and Flask-Login behavior
- Existing CSRF tests
- Existing password recovery and session refresh
- Existing web JWKS temporary-failure behavior

## Validation Baseline

From the clean worktree before changes:

```text
python -m pytest --collect-only -q
Result: 2613 selected, 3 deselected; exit 0; 9.71s

python -m pytest -q <16 auth/Cognito/session/security test files>
Result: 216 passed; exit 0; 31.36s
```

Two full-suite attempts were manually stopped after approximately 10 and 15
minutes because the shell adapter buffered all progress and neither attempt had
completed. This is not recorded as a pass or failure. The full suite remains a
mandatory final validation with a sufficiently large timeout.

## Final Validation

Run and record exact output for:

```text
python -m pytest -q <targeted mobile-auth tests>
python -m pytest -q <existing auth/web-regression tests>
python -m pytest
python scripts/check_schema_drift.py
python scripts/check_cognito_pool.py using mocked or non-production inputs only
repository formatter/linter/static checks discovered in CI
repository secret scan or equivalent tracked-file scan
```

No live login chain or Cognito configuration mutation is permitted. A local
manual flow may use fully mocked provider calls only.

## Documentation and Infrastructure Deliverables

- Update `docs/cognito.md` with the mobile boundary and rotation contract.
- Update `.env.example` with placeholder/default configuration only.
- Extend the Cognito drift checker tests and documentation to report, without
  mutating, secret presence, token revocation, token lifetimes, and rotation.
- Record any required live setting as undeployed follow-up.

## Rollout and Rollback

The API is additive and versioned. Existing web routes are never redirected to
it. The migration only creates new tables and indexes. Rollback unregisters or
disables the mobile blueprint; existing web code continues unchanged and the
new tables may remain. Clients are forced to log in again after rollback.

## Explicit Non-Goals

- Flutter code, UI, secure storage, interceptors, or routing
- Registration, verification, password reset, or account deletion APIs
- Product endpoints beyond `/account/me`
- CORS expansion
- New user pool or app client deployment
- Cognito or AWS mutation
- Production deployment
- Database redesign outside the additive mobile-session tables
