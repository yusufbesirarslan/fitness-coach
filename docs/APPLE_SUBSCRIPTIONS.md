# MON-02-BE-01/BE-02 Apple subscription foundation and lifecycle

Dark backend foundation only. `APPLE_SUBSCRIPTIONS_ENABLED=0` is the default;
monthly and annual configured products yield one Premium tier. No legacy
`User.is_premium`, authentication, quotas, usage counters or paywalls change.

## HTTP contract v1

All routes use the existing mobile opaque Bearer credential, existing account
identity and `Cache-Control: no-store`. With mobile auth disabled the blueprint
is absent. With subscriptions disabled authenticated calls return HTTP 503
`SUBSCRIPTIONS_DISABLED`, `retryable: true`; clients must not retry-loop or offer
purchases while this configuration error persists. Authentication still applies.

- `GET /api/v1/subscription/purchase-context`: 200
  `{"contract_version":1,"app_account_token":"<UUID>","environment":"Sandbox|Production","evidence_type":"transaction_id"}`.
  The token is generated once under the User row lock and is private to that
  authenticated account. It survives reinstall, but not deletion/re-registration.
- `POST /api/v1/subscription/verify`: strict JSON object
  `{"transaction_id":"<1–64 ASCII digits>"}`, maximum 1024 bytes. No additional
  keys. 200 `{"contract_version":1,"entitlement":{...}}`. Five requests/minute,
  30/hour per authenticated owner using existing Flask-Limiter storage.
- `GET /api/v1/subscription/entitlement`: same 200 entitlement envelope, derived
  locally from the last authenticated Apple lookup. Does not call Apple.

Entitlement fields: `tier` (`FREE|PREMIUM`), `effective_premium` (boolean),
`status` (`FREE|ACTIVE|EXPIRED|REVOKED|BILLING_RETRY|GRACE_PERIOD|VERIFICATION_PENDING`),
`expires_at` (UTC ISO timestamp or null), `verification_pending` (boolean).
No row yields FREE. An expiry can appear on a FREE result; it is not an access
promise. Errors follow existing `error` with `code`, safe `message`, `retryable`,
`request_id`, and Retry-After for temporary failures.

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 / 413 | SUBSCRIPTION_INVALID_REQUEST | Invalid shape/type/size |
| 409 | SUBSCRIPTION_PURCHASE_CONTEXT_REQUIRED | Fetch context before buying |
| 409 | SUBSCRIPTION_OWNERSHIP_CONFLICT | Association mismatch or owned elsewhere; no account details |
| 409 | SUBSCRIPTION_ACCOUNT_UNAVAILABLE | Owner no longer exists |
| 422 | SUBSCRIPTION_INVALID_EVIDENCE | Failed signature/app/environment/product/identity validation |
| 429 | SUBSCRIPTION_RATE_LIMITED | Owner verification rate exceeded |
| 503 | SUBSCRIPTIONS_DISABLED | Dark feature |
| 503 | SUBSCRIPTION_NOT_CONFIGURED | Required mounted configuration missing |
| 503 | SUBSCRIPTION_VERIFICATION_PENDING | Stale/out-of-order signed state or attempted revocation reset |
| 503 | SUBSCRIPTION_TEMPORARILY_UNAVAILABLE | Apple/OCSP/storage temporary failure |
| 503 | SUBSCRIPTION_INVALID_STATE | Invalid persisted schema/state; fail closed |

## Apple trust and mobile dependency

Official `app-store-server-library==3.1.2` (OCSP fix GHSA-8f6j-263m-g72x),
`SignedDataVerifier` with online checks enabled, mounted Apple DER root
certificates, exact configured bundle and environment. Only Sandbox/Production
are admitted; Xcode/LocalTesting verification bypass modes are forbidden.
The server uses Get Transaction Info then Get All Subscription Statuses.
The original transaction, status envelope and matching subscription group are
linked; both returned transaction and renewal JWS are authenticated. App Apple
ID is checked on the official authenticated HTTPS status envelope: transaction
JWS does not contain this field. It is also passed to the official verifier,
which requires it for production. We do not claim it is signed in transaction JWS.

The untrusted ID selects a lookup. Signed transaction token must equal this
account's backend UUID; missing/legacy token and Family Sharing are rejected.
Unknown products, wrong group, inconsistent renewal identity and duplicate
matching status rows are rejected. Cancellation keeps paid access to expiry;
billing retry does not grant access. Grace requires Apple's status 4, signed
retry indication and signed grace expiry. Revocation is terminal for this
foundation; later controlled reconciliation must handle subsequent repurchase.

Flutter has no current purchase dependency at inspected mobile HEAD
`17a6d571b088e12c1ccc09823b3fe30f31f2ad9d`. Planned dependency:
`in_app_purchase` 3.3.1 / `in_app_purchase_storekit` 0.4.13+2, StoreKit 2 only.
Pin and qualify these in MON-02-IOS-01; no mobile edits here. Pass context UUID
as `PurchaseParam.applicationUserName`, which the official package forwards to
`SK2ProductPurchaseOptions.appAccountToken`. Submit `PurchaseDetails.purchaseID`
as a string. Restore uses the same transaction ID/server lookup and the signed
original account association; never overwrite the association on restore.
Do not send arbitrary `serverVerificationData`: SK1 is a base64 receipt; SK2
uses JWS in supported versions, but this API intentionally accepts an ID only.
No extra native bridge is evidenced necessary. MON-03 must verify purchase and
restore behavior on actual selected package versions and Apple sandbox.

Apple verification holds one existing shared blocking capacity permit across both
API lookups and all signed-data/online OCSP verification. The preliminary read
transaction ends before admission; the permit is released before ownership
locks and persistence. Saturation makes no Apple call or ownership write and
returns the existing retryable `SUBSCRIPTION_TEMPORARILY_UNAVAILABLE` HTTP 503
with `Retry-After: 30` (a retry delay, not a recovery-time guarantee).

## Apple signed data timestamps

Both API signed snapshots must be within five minutes of server time; no local
clock extension. This conservative fail-closed bound needs live MON-03 evidence
for old purchases, restore and cancelled renewal: Apple documents signing time
but does not explicitly guarantee both responses are re-signed per lookup.
No live interoperability is claimed.

BE-02 investigation: the bound is unchanged and still applies to every status
lookup, including reconciliation. It never applies to notifications, because a
notification's embedded snapshot is never authority: delayed Apple retries (days)
and resends stay valid hints and only trigger a fresh lookup. If Apple turns out
not to re-sign status snapshots per request for restored/older transactions, both
verify and reconciliation fail closed as `STALE_SNAPSHOT` (bounded retries, no
access granted or extended). Any relaxation needs MON-03 evidence and a separate,
versioned decision; it is not made here.

## Bounded entitlement and persistence

A verified row grants access only while
its paid/grace expiry is future AND last verification age is under 24 hours.
An outage leaves previously verified state intact within that bound, then
returns FREE/pending. Lookup never refreshes or extends the validity itself.

`apple_account_association`: User FK/PK, unique random UUID token.
`apple_subscription`: User FK, environment, original/latest transaction,
product/group, Apple status 1–5, UTC-aware expiry/revocation/grace, auto-renew,
transaction/renewal signed timestamps, authoritative verification and audit
timestamps. Unique `(environment, original_transaction_id)` prevents duplicate
ownership. User and subscription row locks, nested insert/unique arbitration,
immutable owner checks and monotonic signed timestamps make updates atomic.
No signed payloads are stored or logged. User deletion cascades both tables;
a newly registered account cannot claim an old signed account token. Support
recovery/transfers are deferred, not automatic.

## BE-02 notifications, lifecycle and reconciliation

`POST /api/v1/subscription/apple-notifications` (App Store Server Notifications
V2, `{"signedPayload": "<JWS>"}`, ≤ 64 KiB) exists only when
`APPLE_NOTIFICATIONS_ENABLED` and `APPLE_SUBSCRIPTIONS_ENABLED` are ON; otherwise
404 before any work. No Bearer: authenticity is the official
`verify_and_decode_notification` (online checks) plus verification of the inner
signed transaction and renewal, exact bundle/environment, App Apple ID (library
in Production; any value present in Sandbox must match), version 2.0, canonical
UUID, closed type/subtype names and no future signing beyond five minutes, all
inside the shared blocking capacity permit. Answers: 200 `{}` (recorded,
duplicate or authentic-but-irrelevant: TEST/summary, other product/group, Family
Sharing); 400/413 malformed; 422 `SUBSCRIPTION_INVALID_EVIDENCE` unauthenticated or
cross-identity; 503 temporary/unconfigured (Apple retries). Logs carry closed
codes only.

A notification never changes access. One commit records an
`apple_notification_receipt` (PK = verified notificationUUID: durable dedup; type,
subtype, original transaction ID, disposition, timestamps; no payload or token)
and coalesces an `apple_subscription_reconciliation` request per (environment,
original transaction) with the signed account token as a hint only.

`flask apple-subscription-reconcile [--limit 25] [--sweep-limit 50]` (not
scheduled anywhere) runs one bounded pass: it enqueues subscriptions last
verified over 12 h ago (not revoked, expiry within 60 days: Apple's billing retry
maximum), then claims due requests one at a time (`FOR UPDATE SKIP LOCKED`,
5-minute lease, attempt counted before any Apple I/O). The owner is our
ownership row, else the association matching the signed hint, else
`UNMATCHED_ACCOUNT`. A fresh Get All Subscription Statuses lookup goes through
the BE-01 validation and `persist_verified`. Outcomes: `APPLIED`, `SUPERSEDED`
(a newer lookup already stored), terminal codes for evidence that is invalid in
itself (signature, identity, group, grace), ownership, account or revocation, or
retries (`STALE_SNAPSHOT`, `OUT_OF_ORDER`, `APPLE_NOT_FOUND` = any Apple 404,
`SUBSCRIPTION_INVALID_STATE` storage race, Apple/OCSP outage) with backoff 1 min
doubling to 1 h and `RETRY_EXHAUSTED` after 8 attempts. Client verify still
answers a 404 as 422 `SUBSCRIPTION_INVALID_EVIDENCE`. An outage stops the pass;
claims take the fewest-attempts request first, so retries only use capacity that
fresh requests leave. Completion compares the request generation, so a hint arriving
mid-lookup triggers another lookup instead of being lost; a lease-fenced finish
lets an expired worker change nothing. Only success removes a request: terminal
or exhausted outcomes quarantine it for 24 h (the sweep skips subscriptions with
a request, so a failing row cannot crowd out healthy refreshes; it costs one
lookup per day if terminal, at most 8 per quarantine cycle if retryable; an
unmatched request drops its token hint); a new authentic notification re-arms a quarantined (never a backing
off) request; requests older than 60 days are dropped.

Known fail-closed limits (owner/MON-03): status 5 without a signed revocation
date can never be exited by repurchase; the status-lookup path still requires
App Apple ID to match in Sandbox (BE-01), unverified against live Sandbox; eight
consecutive failed lookups (about 8 h on an hourly schedule) or permanently
invalid evidence quarantine the row for 24 h, so an active row then lapses to
`VERIFICATION_PENDING` at its 24 h bound until a notification, client verify or
the next cycle succeeds. Failure never extends `last_verified_at`.

The intake route has its own per-IP limit, `APPLE_NOTIFICATION_RATELIMIT`
(default `120 per minute; 3600 per hour`), instead of the 600/h default. Flask-Limiter
counts per route, so other traffic from a shared IP cannot consume it; host nginx
`perip` (30 r/s, burst 60) is the only shared per-IP layer. A 429 makes Apple
retry later. No Apple IP allow-list is assumed.

Reconciliation schedule (required before activation; nothing is installed):
run `flask apple-subscription-reconcile --limit L --sweep-limit 2L` hourly, with
L >= max(25, ceil(N / 10)) for N verified non-revoked subscriptions in the
environment. Rows are enqueued at 12 h, so the whole population can come due at
once (worst case) and must be looked up within the remaining 12 h; N / 10 leaves
two passes of margin plus room for retries. A test simulates 72 hourly passes:
40 subscribers verified at the same instant and 5 permanently failing rows; at
L = 4 every healthy row stays under 24 h, and at L = 2 the bound is broken.
L <= 200 per process (at least 37 min worst case at the 11 s status transport bound,
plus OCSP; overlapping passes are safe under leases and SKIP LOCKED). For
N > 2000, run concurrent workers (SKIP LOCKED is safe) or raise the cadence.
Alert on `RETRY_EXHAUSTED`, and on a pass that ends with `processed = L` while
due requests remain.

Receipt retention (provisional V1 policy, owner decision): each pass purges at
most 1000 receipts older than 180 days in its environment. This is safe because
a receipt only deduplicates hints; a redelivery after purge costs one fresh
lookup. Purge uses a sequential scan (no `received_at` index); add an index in
a later migration if the table grows large.

Lifecycle comes from Apple's authenticated status: purchase, renewal,
cancellation (access to verified expiry), expiration, billing retry (no access),
grace (status 4 with signed grace expiry), refund/revocation, and product
changes within the configured group. Revocation stays terminal for its own
transaction (including a refund reversal: fail closed, owner decision). The only
exit is a controlled repurchase: a different Apple transaction, purchased after
the stored revocation, signed for this owner's token. A replacement purchase
signed for another AxisAI account conflicts and changes nothing. Deleted
accounts are unmatched. Account transfer and support recovery remain deferred.

## Configuration and deployment gates

Host-injected nonsecret values and mounted secret/root paths follow existing
env-file loading. Required keys are listed in `.env.example`. Issuer/key/private
key, app ID, bundle, product and group identifiers remain pending; no values
invented. Separate environments use separate deployment configurations and
credentials; Production config verifies and serves Production records only.
No client chooses an environment. Do not configure Sandbox on a production host.
Missing configuration does not block boot or CI, but closes all active APIs.

Migration `a4d6f8b0c2e1` (BE-02) adds the two empty tables above, expand-only and
reentrant with the same fail-closed adoption rule. Migration `c2b9d0e1f2a3` is additive, empty, reentrant for create_all-first boot,
and rejects incompatible pre-existing tables. No User data changes.
On PostgreSQL, both required domain CHECKs must also have authoritative
`pg_constraint.convalidated=true` on the resolved subscription table. Correct
predicates declared `NOT VALID` are rejected even with valid existing rows.
The migration never validates constraints or repairs historical records;
operational correction requires a separately authorized procedure. Downgrade
erases the new tables and is not an operational rollback instruction. A merge
can trigger a production candidate after main CI; boot may apply the migration.
Do not merge/deploy without separate human gates; flag remains OFF.

## Next tasks and qualification limits

- BE-02 (implemented, dark): see above. Open: owner confirmation of 180-day
  retention, installing the hourly schedule, App Store Connect URLs, refund-reversal and account-transfer policy,
  CONSUMPTION_REQUEST answers (not sent).
- BE-03: integrate effective entitlement into existing quota enforcement with
  approved allowances. Until then this API does not bypass any existing quota.
- IOS-01: selected StoreKit 2 dependencies, UUID binding, purchase/restore,
  backend-first completion, pending retry and account-switch isolation.
- MON-03: actual Apple PKI/OCSP, signed snapshot freshness, cancelled renewal,
  monthly/annual purchase, restore, expiry, retry, grace, refund and environments.

Offline adapter/business tests use fakes; cryptographic tests use the official
verifier with a synthetic chain and authentic synthetic signatures. Neither is
evidence of a real Apple purchase. Authoritative PostgreSQL tests observe real
blocking locks and unique contention. CI performs broader regressions and full
Alembic schema drift. Prices, Apple account/agreements, legal gates and exact AI
allowances remain separate launch requirements. Paid public launch is not ready.

Official references checked 2026-10-13:
[Apple library](https://github.com/apple/app-store-server-library-python),
[security fix](https://github.com/apple/app-store-server-library-python/security/advisories/GHSA-8f6j-263m-g72x),
[Flutter StoreKit contract](https://github.com/flutter/packages/blob/main/packages/in_app_purchase/in_app_purchase_storekit/lib/src/in_app_purchase_storekit_platform.dart),
[package changelog](https://pub.dev/packages/in_app_purchase_storekit/changelog),
[Apple account token](https://developer.apple.com/documentation/storekit/product/purchaseoption/appaccounttoken(_:)),
[Apple status](https://developer.apple.com/documentation/appstoreserverapi/status),
[signed date](https://developer.apple.com/documentation/storekit/transaction/signeddate).
