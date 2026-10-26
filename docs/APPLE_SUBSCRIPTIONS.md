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
  `environment` is the deployment's configured environment, also on a Production
  deployment with the Sandbox fallback; it never predicts a transaction's environment.
  The token is generated once under the User row lock and is private to that
  authenticated account. It survives reinstall, but not deletion/re-registration.
- `POST /api/v1/subscription/verify`: strict JSON object
  `{"transaction_id":"<1–64 ASCII digits>"}`, maximum 1024 bytes. No additional
  keys. 200 `{"contract_version":1,"entitlement":{...}}`. Five requests/minute,
  30/hour per authenticated owner using existing Flask-Limiter storage.
- `GET /api/v1/subscription/entitlement`: same 200 entitlement envelope, derived
  locally from the last authenticated Apple lookup. Does not call Apple.

Entitlement fields: `tier` (`FREE|PREMIUM`), `effective_premium` (boolean),
`status` (`FREE|ACTIVE|EXPIRED|REVOKED|BILLING_RETRY|GRACE_PERIOD|VERIFICATION_PENDING|ELIGIBILITY_REVIEW_REQUIRED`),
`expires_at` (UTC ISO timestamp or null), `verification_pending` (boolean).
No row yields FREE, with or without valid eligibility. `ELIGIBILITY_REVIEW_REQUIRED`
appears only when eligibility withholds a grant (below); every other view keeps
the v1 status set that shipped clients decode. An expiry can appear on a FREE result; it is not an access
promise. Errors follow existing `error` with `code`, safe `message`, `retryable`,
`request_id`, and Retry-After for temporary failures.

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 / 413 | SUBSCRIPTION_INVALID_REQUEST | Invalid shape/type/size |
| 403 | SUBSCRIPTION_ELIGIBILITY_REQUIRED | Canonical adult Türkiye eligibility missing or requires review; no purchase context issued |
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

## Türkiye server purchase eligibility (#467)

Production context issuance and every effective-entitlement decision validate the
authenticated account's canonical `_account_eligibility_v1` signature and Cognito
subject. A valid canonical record derives `screen_passed`; it must also have
`age_band=18_plus`, `country=TR`, and an aware `checked_at`. There is **no
age-of-record purchase expiry**: no existing-account re-declaration journey
exists, so a record-age limit would lapse a paying annual subscriber with no
self-service recovery. Any future expiry needs an approved policy and a qualified
renewal contract first. This is separate from the 600-second registration ticket
and Apple's 24-hour verification freshness; it does not activate purchases.
Missing, malformed, copied, tampered, contradictory, teen, unsupported-market
and unknown/review-state records fail closed. The age screen is
self-declaration, not independent age assurance. Editable age can restrict, but
cannot establish or renew eligibility. Client claims never authorize purchase.

This check runs even with `ACCOUNT_ELIGIBILITY_ENABLED=0` in Production. The
explicit legacy MON-03 boundary applies only when the deployment environment is
`Sandbox`, `APPLE_SANDBOX_FALLBACK_ENABLED` is exactly false and
`ACCOUNT_ELIGIBILITY_ENABLED` is exactly false; only Sandbox evidence can use it.
Never configure a Production host as Sandbox. A Sandbox transaction accepted by
Production's App Review/TestFlight fallback receives the full production check.
No TESTING switch or client environment/status creates an exception.

Context checks precede association creation under the refreshed User lock.
Verify/restore and reconciliation still perform real Apple verification and
enforce token ownership, original identity, monotonic evidence and revocation.
They recheck the canonical account under that same owner lock before returning
access. `GET entitlement` also refreshes and locks the owner; a previously
eligible purchase cannot retain access from a stale in-memory account snapshot.

**Paid transaction, eligibility now invalid:** do not refuse to record an
authentic, correctly owned payment. Verification commits the existing Apple
state/identity idempotently, then returns 200 with `tier=FREE`,
`effective_premium=false`, `status=ELIGIBILITY_REVIEW_REQUIRED`,
`purchase_recovery_required=true`, and
`resolution=CONTACT_SUPPORT_OR_APPLE_REFUND`. These additional fields also appear
on entitlement reads (and the allowance view's embedded entitlement) while
suspended. The stored Apple status and expiry remain unchanged; the response
grants no Premium. Only a view that would otherwise grant Premium is rewritten:
no row stays `FREE` and a lapsed or pending row keeps its v1 status, without the
additional fields, whatever the eligibility. Apple freshness and revocation
checks still apply.

**Mobile contract (StoreKit #144, `ca6b1ad`):** its `decodeEntitlement` maps
`status` through a closed v1 switch, so `ELIGIBILITY_REVIEW_REQUIRED` is decoded
as an incompatible response: no Premium is shown, but the transaction is not
finished and no recovery copy appears. A purchase-context `403
SUBSCRIPTION_ELIGIBILITY_REQUIRED` also maps to the generic incompatible error.
Both need the mobile follow-up below before a paid release.

Mobile must treat this 200 as durable receipt acknowledgement, allow StoreKit
finish, and show the eligibility restriction plus account-review/support and
Apple refund/subscription-management actions. A 2xx alone never means Premium;
only `effective_premium=true` grants access. Do not launch another purchase or
retry-loop to recover access. Explain how to request an Apple refund and cancel
future renewal in Apple's subscription management; cancellation and refund are
different actions. This backend does not automatically cancel, promise a refund,
or invent an entitlement. If the commit or Apple verification fails, return the
existing error and retain StoreKit retry/restore behavior instead of finishing.

Support must inspect the owned Apple record and canonical restriction, arrange
controlled eligibility review or direct the customer to Apple's refund and
cancellation flows, and preserve the transaction for reconciliation. Do not edit
profile age or clear the reserved signed record to promote an account. No
self-service review can change a signed record's cohort; that remains a
controlled review case. A valid reviewed record can restore access
only while authentic Apple evidence is still fresh, paid and unrevoked. Background
reconciliation continues to record renewals/refunds/revocations while suspended;
its `APPLIED` outcome acknowledges Apple state persistence, not an access grant.

Release remains held until the mobile suspended-payment UX and support handling
are qualified. No allowances, D1 consent, infrastructure or activation change is
part of this source delta.

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
claims take the fewest-attempts request first, so a backing-off retry never runs
ahead of a fresh request (a row leaving quarantine competes as fresh: at most one
claim per row per day). A failed receipt purge is logged and never blocks refreshes. Completion compares the request generation, so a hint arriving
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
retry later. While either flag is OFF the limit is not evaluated (always 404).
Every Apple 404 is retried the same way, including codes that mean app/account
misconfiguration; this is bounded and fails closed, so alert on a spike of
`APPLE_NOT_FOUND`. No Apple IP allow-list is assumed.

Reconciliation schedule (required before activation; nothing is installed):
run `flask apple-subscription-reconcile --limit L --sweep-limit 2L` hourly, with
L >= max(25, ceil(N / 10)), where N counts the environment's non-revoked
subscriptions whose expiry is within the 60-day recovery window (the rows the sweep
polls, lapsed ones included). Rows are enqueued at 12 h, so the whole population can come due at
once (worst case) and must be looked up within the remaining 12 h; N / 10 leaves
two passes of margin plus room for retries. A test simulates 72 hourly passes:
40 subscribers verified at the same instant and 5 permanently failing rows; at
L = 4 every healthy row stays under 24 h, and at L = 2 the bound is broken.
L <= 200 per process. Duration is an estimate, not a bound: each status call has
a 3 s connect and 8 s per-read timeout, plus OCSP, so 200 slow lookups can take
about 37 min or more; overlapping passes are safe under leases and SKIP LOCKED. For
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
credentials; Production config verifies and serves Production records only,
plus Sandbox records while `APPLE_SANDBOX_FALLBACK_ENABLED` is ON (see below).
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

## Dual-environment verification

MON-BE-APPLE-DUAL-ENV-REVIEW-01. App Review and TestFlight install the Production
build but buy in Apple's Sandbox, so a Production backend that trusts only
Production rejects the reviewer's purchase. `APPLE_SANDBOX_FALLBACK_ENABLED`
(rollout flag, default OFF, no migration) adds Sandbox to a Production deployment.
It is valid only with `APPLE_SUBSCRIPTION_ENVIRONMENT=Production`; with any other
environment every subscription API answers 503 `SUBSCRIPTION_NOT_CONFIGURED`.
Staging (Sandbox) stays single-environment and is unchanged.

Verification. The adapter keeps one fully qualified Production verifier and API
client and, when ON, a second Sandbox verifier and client (`AppleAdapter.fallback`)
built from the same roots, bundle, App Apple ID and API key, with online checks.
Client verify calls Get Transaction Info in Production first. Only Apple's exact
`TransactionIdNotFoundError` (HTTP 404, `errorCode` 4040010) moves the lookup to
Sandbox. Authentication (401), other 4xx, rate limit (429), server errors, timeouts,
every other 404 code (account/app/original-transaction not found, retryable or
not), signature or OCSP failure, wrong environment, wrong identity and ownership
conflicts are final and never reach Sandbox. The environment that found the ID
then does everything else: its verifier authenticates the transaction JWS and
demands its own signed environment, and its client runs Get All Subscription
Statuses, whose envelope, transactions and renewals must name the same
environment. Bundle, product, subscription group, auto-renewable type, purchased
ownership, transaction ID echo and the account token are checked exactly as before.
The App Apple ID is required in Production and, if present, must match in Sandbox.
The verify body stays `{"transaction_id"}`; no client value selects or labels an
environment.

Persistence and entitlement. Evidence carries the verified environment. Rows keep
the unique `(environment, original_transaction_id)` identity, so a Sandbox record
is a different identity from any Production record, even with the same numeric ID.
It can neither claim, update nor revoke the other, and the BE-01 owner lock,
monotonic timestamps, terminal revocation and controlled repurchase apply to each
row on its own. Entitlement reads rows of the deployment's accepted environments
only (Production, plus Sandbox while ON) and ranks Production first:
the first effective row, else the first pending row, else the first row. States
are never merged across rows. Turning the flag OFF removes Sandbox rows from
entitlement at once, and Sandbox transactions then verify as not found. Rows are
retained, and Production access is unchanged.

Launch policy (founder decision required before ON):
- Sandbox entitlement exists only because the operator enabled the flag on the
  Production host and Apple's Sandbox endpoint returned authentic Sandbox data for
  this account's token. There is no reviewer allowlist, no client environment claim
  and no global Sandbox switch.
- Every App Review submission, including updates, buys in Sandbox, so in practice
  the flag stays ON for the life of the app.
- While ON, any TestFlight tester of a Production-API build gets Premium from a free
  Sandbox purchase. Sandbox purchases can be repeated, so in practice a tester's
  Premium never ends. It belongs to the account, so it also applies when the
  same account signs in to the App Store build.
- A public TestFlight link extends this to anyone who joins. Production-API builds
  must not use public links unless the founder accepts that.
- Access stays bounded by expiry, revocation and the 24 h verification age of each
  Sandbox record.
- Where Premium meters AI cost, those testers consume it.

Residual P3s, accepted:
- An authentic notification signed for another bundle, or a garbage transaction
  ID, costs one extra verification (OCSP) or Apple lookup before it is rejected.
  This is bounded by the per-IP and owner rate limits and the shared concurrency
  permit.
- The library reads Apple's `errorCode` only when `content-type` is exactly
  `application/json`. Any other response fails closed: the review purchase gets
  422. Rollout step 3 must therefore pass before submission.

Each Sandbox lookup logs `apple_subscription event=sandbox_fallback
kind=verify|notification` (closed codes only).

Notifications and reconciliation. Intake verifies with the Production verifier
first. Only a notification whose signature is already valid and which fails on
App Apple ID or environment (an authentic Sandbox one: Apple omits the App Apple
ID there) is re-verified from scratch by the Sandbox verifier, which demands
Sandbox. Retryable OCSP failures and bad signatures never fall back. Inner
transaction and renewal JWS are checked by the verifier that accepted the outer
payload. The receipt and the coalesced request carry that verified environment.
`flask apple-subscription-reconcile` runs one bounded pass per accepted
environment (each with its own `--limit` / `--sweep-limit`), and each pass asks
that environment's client for status. A Sandbox request can therefore only write
its `(Sandbox, original)` row. A Production outage stops only the Production pass.

Production notification/reconciliation release gate (separate approval; nothing
here enables it): `APPLE_NOTIFICATIONS_ENABLED` stays OFF. Before ON:
- the BE-02 migration and an approved production deploy
- production ingress/TLS for the exact route
- the App Store Connect Production Server URL
- a decision on the Sandbox Server URL, which today belongs to staging MON-03
  (`asn-sandbox`). Pointing it at production moves all Sandbox notifications there.
- an hourly reconcile schedule sized per environment (L from each environment's
  N; a pass can run up to 2L lookups when Sandbox is accepted)
- the Production Request-a-Test-Notification qualification
- alerting on `RETRY_EXHAUSTED` and `APPLE_NOT_FOUND`

Without a schedule, every row (Production and Sandbox) lapses to
`VERIFICATION_PENDING` 24 h after its last client verify.

Production rollout (each step a separate human approval; none done here):
1. Founder merge (source only, no migration). The production deploy of current
   main (with every migration it carries) is its own gate.
2. Production host: complete Apple config with `APPLE_SUBSCRIPTION_ENVIRONMENT=Production`,
   the numeric App Apple ID, the API key and the mounted Apple roots. Set
   `APPLE_SUBSCRIPTIONS_ENABLED=1` (payment activation approval) and
   `APPLE_SANDBOX_FALLBACK_ENABLED=1`.
3. Off-box check: `purchase-context` reports `Production`. A TestFlight build on
   the production API buys Monthly in Sandbox, verify returns 200, and the row is
   `environment='Sandbox'`. Restore is idempotent, and a second account gets 409.
4. Submit for App Review with the review notes that the subscription is live.
   Purchases need a signed adult Türkiye record (§ Türkiye server purchase
   eligibility), so provide a prepared `18_plus`/`TR` reviewer account. A
   reviewer who registers with another country is refused with 403
   `SUBSCRIPTION_ELIGIBILITY_REQUIRED`. New accounts get a record only while
   `ACCOUNT_ELIGIBILITY_ENABLED=1`, and legacy accounts only through its one-time review.
5. After launch, the first real purchase verifies as a `Production` row with no
   Sandbox lookup.

Rollback: set the fallback flag to 0 (Sandbox access stops), or set
`APPLE_SUBSCRIPTIONS_ENABLED=0`.

StoreKit #144 handoff (mobile). The release build must target the production
API. #144 currently refuses any origin other than `asn-sandbox` and authorizes
only the staging account. For a Production-API build:
- expect `purchase-context.environment == "Production"`, even for App Review and
  TestFlight purchases.
- never compare it with StoreKit `Transaction.environment`.
- never send an environment to the backend.
- submit the transaction ID on purchase, on `Transaction.updates` (renewals) and
  on restore, and finish the transaction only after a 2xx.
- `GET entitlement` never refreshes from Apple. A 2xx is receipt acknowledgement;
  honor `effective_premium` and the suspended-payment handling above.

A 422 for a review purchase means the fallback flag is OFF.

Tests: `tests/test_apple_dual_environment.py` (fake per-environment API with
exact 4040010, official verifier over a synthetic chain for signed-environment
authority, SQLite lifecycle, entitlement and reconciliation) and
`tests/test_apple_dual_environment_pg.py` (CI PostgreSQL list: unique arbitration
of one Sandbox identity, no cross-environment contention, concurrent restore,
account deletion before and after persistence). No live Apple Production or
Sandbox call is claimed.

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
