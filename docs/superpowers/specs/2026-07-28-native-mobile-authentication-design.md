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
consumption, retry-grace, committed-child references, derivation-key version,
and fixed replacement issuance/expiry metadata.

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

A focused service generates initial login credentials with
`secrets.token_urlsafe(32)` or an equivalent 256-bit CSPRNG operation. It derives
replacement credentials with the versioned HKDF/HMAC construction in the
Idempotent Grace Replay section, hashes all credentials with SHA-256, and
returns raw values only to the immediate route response. It also parses Bearer
syntax without accepting multiple, empty, or non-Bearer credentials.

The wire form is canonical unpadded base64url of exactly 32 bytes. The codec
rejects an input unless it decodes to 32 bytes and re-encodes byte-for-byte to
the presented ASCII form. Indexed credential hashes are SHA-256 of that
canonical ASCII form.

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
| `MOBILE_AUTH_ENABLED` | 0 | Dark-launch gate for mobile config, readiness, and routes |
| `MOBILE_AUTH_ACCESS_TTL_SECONDS` | 900 | Opaque access lifetime |
| `MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS` | 7 | Fixed family lifetime from login |
| `MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS` | 10 | Consumed refresh retry window |
| `MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS` | 60 | Provider renewal threshold |
| `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` | 0 | Mobile JWT validation and coverage allowance |
| `MOBILE_AUTH_REFRESH_RATELIMIT` | `30 per minute; 300 per hour` | Refresh abuse bound |
| `MOBILE_AUTH_LOGOUT_RATELIMIT` | `30 per minute` | Logout abuse bound |
| `MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS` | 300 | Clock-skew/deployment-drain key-retention buffer |
| `MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION` | none | Version used for new replacements |
| `MOBILE_AUTH_DERIVATION_KEYRING` | none | Secret version-to-key mapping |

TTL values must be positive. Access TTL must be shorter than refresh absolute
lifetime. Retry grace must be short and less than access TTL. Validation clock
skew and the retention buffer must be non-negative and bounded. The mobile JWT
validator and coverage calculation use the same skew value; zero preserves the
current strict-expiry behavior. Invalid production configuration fails at
startup instead of silently using unsafe values. The keyring has no default:
every decoded root key must contain at least 256 bits, the active version must
exist, versions must be unique and canonical, and secret values must never be
logged or persisted. When `MOBILE_AUTH_ENABLED=0`, startup does not parse or
require any other mobile setting, does not run derivation-key database readiness,
and does not register the `/api/v1` blueprint. When it is `1`, all mobile
configuration remains mandatory and fails closed. No derivation key has a
default value.

## Provider-Coverage Invariant

Login and first-use refresh fix one UTC `now` for the transaction and calculate:

```text
child_access_expires_at = min(
    now + MOBILE_AUTH_ACCESS_TTL_SECONDS,
    family_absolute_expires_at
)
coverage_deadline = (
    child_access_expires_at + MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS
)
renewal_trigger = max(
    now + MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS,
    coverage_deadline
)
```

Provider expiry comes only from the fully validated Cognito access JWT `exp`;
an untrusted `ExpiresIn` value cannot establish coverage. Renew Cognito when
`cognito_access_expires_at <= renewal_trigger`. Equality triggers renewal
because a JWT is not valid at its `exp` instant. After renewal, the newly
validated provider access token must satisfy
`cognito_access_expires_at > coverage_deadline`.

If the renewed token does not satisfy that strict postcondition, roll back the
whole login or refresh transaction. Do not consume a parent, persist a family or
credential row, or return an opaque credential. Return retryable
`AUTH_TEMPORARILY_UNAVAILABLE` and emit a structured event containing only
request ID, family identifier when one exists, and a safe failure category.

## Detailed Flows

### Login

1. Reject non-JSON bodies and missing username/password.
2. Apply current IP and normalized-username login limits and Redis fail-closed
   policy.
3. Call existing Cognito authentication.
4. Map verification-required, rate-limit, invalid-credential, and temporary
   failures to the mobile envelope.
5. Validate the Cognito ID and access tokens through the canonical validators.
6. Require their verified subjects to agree and resolve/reconcile the local user
   using only verified claims.
7. Calculate the new family's original absolute expiry as
   `login_time + configured refresh lifetime` without persisting it yet.
8. Calculate the capped access expiry and enforce the provider-coverage
   invariant, renewing Cognito first when required.
9. If renewed provider coverage is insufficient, roll back and return retryable
   `AUTH_TEMPORARILY_UNAVAILABLE` without creating a family or credentials.
10. Create the family and encrypt the covered Cognito token set.
11. Generate and hash one access and one refresh credential.
12. Commit before returning credentials with the calculated expiry timestamps.

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
4. Reject and locally revoke known expired or revoked families.
5. If the credential was already consumed:
   - inside grace, execute the read-only idempotent replay path before any
     Cognito-expiry evaluation;
   - outside grace, revoke the family as credential reuse, clear Cognito
     ciphertext, commit, and return `AUTH_REFRESH_FAILED`.
6. For first use, verify the optimistic family version in the update predicate.
7. Fix one transaction `now`; calculate the capped child access expiry,
   coverage deadline, and renewal trigger.
8. When verified Cognito expiry is at or before the renewal trigger, decrypt the
   provider refresh token and renew the provider token set under the same lock.
9. Fully validate a renewed provider access JWT and require its expiry to be
   strictly after the coverage deadline.
10. On insufficient renewed coverage or temporary provider failure, roll back
   without consuming the presented refresh credential, creating child rows, or
   returning opaque credentials; emit a safe structured event and return
   retryable `AUTH_TEMPORARILY_UNAVAILABLE`.
11. On definitive provider refresh rejection, revoke the family.
12. Select the active derivation-key version and fixed issuance/expiry values.
13. Derive one N+1 access/refresh pair from the locked N parent.
14. Mark N consumed, set its grace deadline, and record the key version, child
    generation, linked child row identifiers, and fixed issuance/expiry values.
15. Persist exactly one access hash and one refresh hash for N+1.
16. Preserve the original family absolute expiry as the child refresh expiry.
17. Increment the family version once, commit atomically, and return the pair.

The grace replay path derives the pair using the recorded key version and the
presented raw parent, verifies both hashes in constant time against the linked
child rows, and returns the stored issuance and expiry timestamps. It creates no
row, performs no provider call, and does not mutate the family version. Missing
key material, missing child metadata, or a hash mismatch emits a redacted
high-severity event and returns retryable `AUTH_TEMPORARILY_UNAVAILABLE`; it
never derives or persists a replacement alternative.

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

## Idempotent Grace Replay

Only hashes and non-secret derivation metadata are persisted. For key version V,
the credential codec derives a 32-byte replacement subkey with HKDF-SHA-256 from
the independently configured root key. The HKDF salt is the ASCII bytes
`axisai/mobile-auth/credential-derivation/salt/v1`. Its info is the ASCII bytes
`axisai/mobile-auth/replacement-subkey/v1`, one NUL byte, the unsigned 16-bit
big-endian byte length of the canonical ASCII family identifier, the family
identifier bytes, then unsigned 64-bit big-endian parent and child generations.

The codec computes the two credential bytes as HMAC-SHA-256 under that subkey.
Each message is its distinct ASCII label
(`axisai/mobile-auth/access/v1` or `axisai/mobile-auth/refresh/v1`), one NUL
byte, and the decoded 32 parent bytes. Outputs use canonical unpadded base64url.
The derivation root is independent of Fernet; the raw Fernet key is never used
as an HMAC key. The key version and all serialization rules are part of the
stable credential format and cannot vary by request.

First use of N creates exactly one N+1 generation, one access row, one refresh
row, one version increment, and at most one Cognito renewal. A concurrent caller
blocks on the same rows, then takes the consumed-parent replay path. Every replay
recomputes and verifies the committed pair before returning it with the original
stored timestamps. Replays do not mutate state. Consumed parent hashes remain as
tombstones until family absolute expiry, allowing post-grace reuse to revoke the
full affected family without touching other device families.

The keyring is versioned. Rotation first deploys a new version alongside old
versions, then changes the active version. A recorded version must remain
available through the last referencing grace deadline plus
`MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS`. Startup/readiness
preflight rejects a deployment when a replayable parent references a missing
version. New first-use rotations use the active version; replay always uses the
recorded version. Missing referenced key material fails safely with
`AUTH_TEMPORARILY_UNAVAILABLE` and cannot issue untracked credentials.

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
- Login access expiry is capped and covered by the verified Cognito access token
- Insufficient renewed provider coverage during login creates no family or credentials
- Refresh success rotates both credentials
- Refresh failure envelope
- Logout exact empty `204`
- `/account/me` allowed and excluded fields
- Every error contains code, safe message, retryability, and request ID

### Rotation and concurrency tests

- Old access invalid after ordinary refresh
- Old refresh accepted only inside configured grace
- Lost-response retry returns the identical pair and timestamps
- Two concurrent same-parent requests return identical pairs
- Exactly one child generation, access row, and refresh row exist
- No sibling credential rows exist
- Repeated grace replay does not mutate generation or family version
- Reuse after grace revokes the family
- Unknown refresh does not revoke another family
- Absolute expiry never moves
- Concurrent refresh increments the family version exactly once
- Optimistic conflict retry and exhaustion mapping
- Derivation-key/configuration failure issues no untracked credentials
- Startup rejects missing active, undersized, duplicate, or malformed keys
- Disabled startup succeeds without a keyring and exposes no mobile routes
- Enabled startup requires a valid keyring and exposes only the approved routes
- Web routes, cookies, Flask-Login, and CSRF match in both gate states
- Replay uses the recorded old key after the active version changes
- Readiness rejects removal of a still-referenced key version
- Grace replay hash mismatch fails temporarily without an alternative pair
- Only hashes and non-secret derivation metadata exist in database rows
- Credentials, hashes, derivation keys, and provider material are absent from logs

PostgreSQL row-lock behavior receives an opt-in concurrency test mirroring the
repository's existing `pg_concurrency` convention; SQLite unit tests exercise
the optimistic version path.

### Cognito renewal tests

- Cognito remaining lifetime shorter than the new opaque access TTL triggers renewal
- Cognito expiry exactly at the coverage boundary triggers renewal
- Sufficient Cognito lifetime outside leeway avoids renewal
- Renewed provider access still too short rolls back with no credential issuance
- Opaque access expiry is capped at family absolute expiry
- Refresh near the seven-day boundary returns the truthful shortened access expiry
- New provider token set encrypted and persisted atomically
- Optional provider refresh-token replacement
- Definitive provider failure revokes family
- Definitive renewed-token validation or subject mismatch revokes the family
- Renewed-token JWKS unavailability is retryable and preserves the family
- Every revocation commit failure returns normalized retryable 503
- Temporary provider failure rolls back token consumption
- Concurrent same-parent calls perform at most one renewal under the coverage rule

### Middleware and JWKS tests

- Missing/malformed Bearer
- Invalid, expired, and revoked access
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
it. `MOBILE_AUTH_ENABLED` defaults to `0`; disabled startup omits the mobile
blueprint, derivation-key parsing, and mobile readiness query. The production
rollout order is:

1. Merge and deploy with mobile authentication disabled.
2. Apply the additive migration.
3. Provision the independent derivation keyring and active version.
4. Verify database and derivation-key readiness.
5. Enable mobile authentication in a controlled deployment.

Rollback disables the mobile blueprint; existing web code continues unchanged
and the additive tables may remain. Clients are forced to log in again after
rollback. This design does not mutate production configuration.

## Explicit Non-Goals

- Flutter code, UI, secure storage, interceptors, or routing
- Registration, verification, password reset, or account deletion APIs
- Product endpoints beyond `/account/me`
- CORS expansion
- New user pool or app client deployment
- Cognito or AWS mutation
- Production deployment
- Database redesign outside the additive mobile-session tables
