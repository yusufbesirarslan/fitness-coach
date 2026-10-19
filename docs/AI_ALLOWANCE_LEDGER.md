# AI allowance ledger (MON-02-BE-03A)

Dark foundation for Free / Starter / Premium AI allowances. Flag
`AI_ALLOWANCE_LEDGER_ENABLED` (default OFF). Stacked on PR #436 (migration
`c2b9d0e1f2a3`); must not merge before it. Mobile contract:
[`mobile/ai-allowances-v1.md`](mobile/ai-allowances-v1.md).

## What BE-03A does and does not change

- Adds `app/services/ai_allowances/` (policy, service, errors), the read route
  `GET /api/v1/ai/allowances`, the operator command `flask ai-allowance-sweep`,
  migration `a9c1d2e3f4b5` (three empty tables) and the rollout flag.
- **No existing route consults the ledger** (AST gate
  `tests/test_ai_allowances_architecture.py`) — except, since BE-03B-01B, the dark
  native initial Training generation seam
  (`app/services/mobile_training_generation/allowance.py`, see
  [mobile/training-allowance-enforcement.md](mobile/training-allowance-enforcement.md)). `app/services/premium.py`, its
  weekly JSON counters, the `is_premium` bypasses, spend guards, model routing and
  inference config are unchanged in every flag state.
- **No allowance quantities exist in application code or env.**
  `app.config["AI_ALLOWANCE_POLICY"]` is `None`; only an explicitly constructed
  `AllowancePolicy` configures the ledger (today: tests). The Balanced Starter
  numbers live only in `tests/ai_allowance_support.py`, labelled test-only.

## Storage

| Table | Key | Purpose |
|---|---|---|
| `ai_starter_grant` | PK `user_id` | The at-most-once Starter claim + `policy_version`, `granted_at` |
| `ai_allowance_bucket` | UNIQUE (`user_id`, `scope`, `feature`, `period_key`) | `granted`, `used`, `reserved`, `billed_releases`. CHECKs: all ≥ 0 and `used + reserved <= granted` |
| `ai_usage_reservation` | UNIQUE `public_id`; UNIQUE (`user_id`, `key_digest`) | One logical action: `state` RESERVED/COMMITTED/RELEASED/EXPIRED, `outcome`, `provider_billed`, timestamps |

All three cascade on user delete and are in `cli._user_child_models`. Rows hold
counts, closed codes, timestamps and a SHA-256 of the caller's idempotency key —
no prompt, answer, image, health/medical context, IP, device or e-mail.

Scopes: `STARTER` (period key `NEVER`, never resets) and `PREMIUM` (`YYYY-Www` or
`YYYY-MM`, Europe/Istanbul calendar like the legacy week). Free recurring
allowances remain in the legacy weekly quota until BE-03B decides otherwise.

## Starter grant (exactly once)

- Eligibility = `User.created_at >= policy.starter_eligible_from`; NULL
  `created_at` is never eligible. **FOUNDER_DECISION_REQUIRED:** this cutoff is
  provisional test behavior, not an approved existing-user policy. It is isolated
  behind the default-OFF flag and unconfigured policy. Existing-user eligibility
  must be approved before any Starter activation.
- Grant runs under the owner row lock (`SELECT … FOR UPDATE`, populate_existing);
  `ai_starter_grant`'s PK arbitrates any lock-free race. The grant and its STARTER
  buckets (quantities snapshotted from the policy) commit together or not at all.
- Lazy: the first `reserve` for an eligible account grants inside the same
  transaction; `grant_starter()` is the explicit entry point. The read API never
  grants.
- Logout/login, reinstall and another device are new credentials for the same
  account: state is keyed by `user_id` only.
- **Existing-account policy hook (not active).** A future, separately approved
  backfill would be a one-off operator command that, in `--dry-run` by default,
  selects accounts with `created_at < starter_eligible_from` and no grant, prints
  counts by cohort, and on approval inserts grants in batches through the same
  `_starter_locked` path with a distinct `policy_version` (e.g.
  `existing-users-v1`). Lifetime usage before the ledger is not reconstructable
  from the weekly JSON, so any backfill grants full quantities (MON-GAP-01 D-1).
- **Deletion and re-registration.** Deletion removes every ledger row. A new
  registration is a new account; eligibility depends on future approved policy
  (the explicit test policy currently allows it); this ledger does not and
  cannot dedupe humans across deleted accounts. Any such mechanism (e.g. a keyed
  tombstone) needs a separately approved lawful basis (MON-GAP-01 D-5).

## Reservation lifecycle

`AUTHORIZE → RESERVE → EXECUTE → FINALIZE`

1. **Authorize** — tier from `subscriptions.entitlement(user_id)` (#436), called
   before any ledger lock. `SUBSCRIPTIONS_DISABLED` ⇒ Premium not effective.
   Any other subscription failure ⇒ `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` (no
   tier guess). Effective Premium uses **only** PREMIUM allowances: a missing
   Premium policy/feature is `AI_FEATURE_NOT_IN_PLAN`, never unlimited and never
   a silent Starter fallback (D-7 owns that choice). `User.is_premium` is never read.
2. **Reserve** — owner lock → key lookup (replay) → lazy expiry of this owner's
   abandoned holds → grant/bucket → one conditional
   `UPDATE … SET reserved = reserved + 1 WHERE used + reserved < granted` →
   reservation row → commit. Exhausted ⇒ `AI_ALLOWANCE_EXHAUSTED`.
3. **Execute** — the caller's provider work, with no ledger lock held. Only
   `Reservation.may_execute` (fresh RESERVED, not a replay) authorizes it.
4. **Finalize** — owner lock → reservation lookup by `public_id` **and** owner
   (another account's reference is `RESERVATION_NOT_FOUND`).
   `success` ⇒ COMMITTED (`reserved−1, used+1`). Failure outcomes ⇒ RELEASED
   (`reserved−1`). Repeating the same settlement replays; a different one is
   `RESERVATION_CONFLICT`.

| Outcome | Billed? | Effect |
|---|---|---|
| `success` | — | consume |
| `refused_before_provider` (capacity, moderation, validation) | never | release |
| `cancelled_before_delivery` | caller states | release |
| `provider_error`, `provider_timeout`, `invalid_output` | caller states | release; if billed, at most `max_billed_releases` per bucket, then consume |
| (TTL passed, never finalized) | unknown | EXPIRED = consume |

`provider_billed` is stored on the row as FinOps evidence, separate from user
consumption. Abandoned holds (worker crash, lost finalize) expire after
`RESERVATION_TTL` (15 min) **as consumed**, lazily on the owner's next reserve or
via `flask ai-allowance-sweep`; a late finalize of an expired hold replays EXPIRED.
Together with the billed-release cap this means neither a crash nor a timeout loop
can mint unlimited free retries. Spend guard / `[AI-USAGE]` keep counting physical
provider attempts; internal fan-out (tool rounds, repair, retries, menu batches)
never consumes extra visible units.

**Locking:** account writers first lock `user`, serializing all grant, reserve,
finalize and expiry work for that account. Replay/finalize/expiry then lock
reservation rows before updating buckets; fresh reserve updates a bucket before
inserting its reservation. Retention claims at most 500 candidates with a durable finite keyset cursor,
committing that short transaction before financial locks. It then locks owner →
operation NOWAIT → binding → reservation → original bucket for correction and
cleanup. Deferred candidates remain eligible for subsequent scan cycles; a
crashed claim is retried on a later cycle. Empty Premium bucket cleanup is also
capped at 500. NOWAIT avoids a blocking inversion with operation → owner paths.
Callers must not hold a ledger transaction across provider I/O.

**Idempotency** — keys are 8–128 printable ASCII chars, owner-scoped and stored
as SHA-256. Same key, same feature: replay (RESERVED ⇒ `IN_PROGRESS`; settled ⇒ the
stored state, never a second unit). Same key, other feature: `IDEMPOTENCY_CONFLICT`.
A RELEASED or EXPIRED replay tells the caller the earlier attempt is over; a retry
uses a new key and a new unit. Deduplication lasts only while the reservation is
retained (90 days after settlement). BE-03B must reject stale client replays and
never reuse a key for a different logical action. The transport must also bind
its request identity to the logical operation before reserve; this ledger stores
no request payload or payload fingerprint.

## Coach streaming success boundary (spec for BE-03B)

HTTP 200 is never success on its own: `/ask/stream` answers 200 before any content.

| Situation | Finalize |
|---|---|
| `done` frame sent, turn persisted, not an error fallback | `success` |
| Error fallback (`is_error_fallback`) or `error` frame, no plan tool committed | `provider_error` (billed if a provider call was made) |
| Error after a committed plan tool (`COACH_REPLY_INCOMPLETE` / plan-saved notice) | `success` — the user's requested change was applied |
| Client disconnect after ≥1 delta delivered (persisted `interrupted=True`) | `success` (partial delivery; prevents disconnect-for-free) |
| Client disconnect before any delta | `cancelled_before_delivery` (billed if a provider call was made) |
| Cooldown / rate limit / moderation input refusal / concurrency gate | `refused_before_provider` (or no reservation at all) |

Blocking `/ask` and native `POST /api/v1/coach/messages`: success = a non-fallback
answer recorded by `record_turn`.

## Feature taxonomy

Supported: `coach`, `training_generation`, `nutrition_generation`, `pump_check`,
`qr_menu`. Extensions (`policy.EXTENSIONS`):

| Name | Disposition | Notes |
|---|---|---|
| `web_coach`, `web_training_generation`, `web_nutrition_generation` | FAMILY | Same operation on the web transport; resolves to the family unit |
| `training_replacement` | UNDECIDED | Candidate family `training_generation` (D-3); refused until decided |
| `pump_comparison` | UNDECIDED | D-2 |
| `legacy_web_ai` | UNDECIDED | check-in feedback, plan comment, social, food AI (D-6) |
| `workout_photo_proof` | EXEMPT | Core completion action; must never consume a unit |
| `coach_summary` | EXEMPT | Background; spend guard only |

Unsupported/undecided names raise `AI_ALLOWANCE_FEATURE_UNSUPPORTED`; nothing is
mapped silently.

## Retention

`flask ai-allowance-sweep` (refuses while OFF): expire abandoned holds, then
delete settled reservations and PREMIUM buckets older than 90 days with no
remaining reservation. Starter grants/buckets are lifetime balances and are kept
until account deletion.

## Rollback

`AI_ALLOWANCE_LEDGER_ENABLED=0`: the GET answers 404 before auth and every entry
point raises `AI_ALLOWANCE_DISABLED`. Tables and rows stay (expand-only).

Migration downgrade refuses if any ledger table contains rows. Empty disposable
schemas may be downgraded for qualification. Runtime rollback retains all rows;
the feature flag does not gate schema migration or release preparation.

## BE-03B-01B extension (dark)

- `reserve_within(user_id, feature, key)` — the same authority as `reserve`
  (subscription before locks, owner lock, lazy expiry, grant, one conditional
  bucket UPDATE) but inside the **caller's** transaction: it neither commits nor
  rolls back, so a caller can commit the hold together with its own durable
  dispatch authorization. An existing key is refused (`IDEMPOTENCY_CONFLICT`): a
  dispatch-bound hold is never a replay. The hold starts with billing evidence
  `UNKNOWN`.
- `ai_usage_reservation.billing_evidence` (nullable; migration `b1e4f5a6c7d8`):
  `CONFIRMED_NOT_BILLED`, `CONFIRMED_BILLED` or `UNKNOWN`; NULL = not recorded
  (BE-03A callers, unchanged). `provider_billed` is NULL exactly for UNKNOWN —
  never guessed into False or True. The enum is enforced by this service (the
  expand-only migration gate forbids new CHECKs on existing tables).
- `finalize(..., billing_evidence=...)` instead of `provider_billed`. A failure
  with UNKNOWN evidence is not proven unbilled, so it follows the billed rule:
  released while `billed_releases < max_billed_releases`, otherwise consumed.
  A hold created with evidence must be settled with explicit evidence. Success,
  expiry and replay semantics are unchanged; an expired hold still settles as
  EXPIRED (consumed once).
- BE-03B-A1 (no ledger change): a dispatch-bound hold whose caller durably
  proves that no physical provider attempt happened is finalized as
  `refused_before_provider` with `CONFIRMED_NOT_BILLED` (RELEASED, not counted
  in `billed_releases`). The ledger accepts that refinement of the hold's
  initial UNKNOWN; it never reverses an EXPIRED hold. Native Training is the
  only caller (docs/mobile/training-allowance-enforcement.md, "Zero-attempt
  settlement").
- Migration `a9c1d2e3f4b5`'s create_all-first verifier accepts exactly this delta
  (one extra nullable column, `provider_billed` nullable) and nothing else; it
  checks the later column by name only. `b1e4f5a6c7d8` is the authority for its
  definition: exactly `VARCHAR(24)` NULL with no server default (CHAR, other
  lengths/types, NOT NULL or a default fail closed), and `provider_billed` a
  `BOOLEAN` with no server default. The same verifier accepts an existing
  `training_plan_generation_allowance` only with its exact DDL: column types,
  lengths, nullability and no defaults; the named state CHECK with the exact
  predicate and `convalidated` (every constraint on the table must be validated);
  PK, named reference UNIQUE, exactly the two `id` CASCADE foreign keys; and, when
  present, the owner index on `(user_id, state)` (non-unique, not partial).
- `billed_releases` counts UNKNOWN releases as well ("released while possibly
  billed"); it is not confirmed provider billing.

## Founder-approved zero-attempt expiry correction (MON-02-BE-03B-A2-A1)

The expiry transition above remains immutable. When both allowance flags are ON,
terminal FAILED metered Training with exact False attempt evidence and complete
same-owner dispatch/binding/reservation/bucket provenance can receive one separate
correction. `training_allowance_compensation` uniquely identifies the expired
reservation and operation. Its insertion and a conditional original-bucket
`used - 1` commit together. Reserved, granted, billed releases, expiry outcome,
UNKNOWN evidence and terminal operation remain unchanged. NULL/True, missing
or contradictory authorities, and non-FAILED operations never qualify.

The target is exclusively the reservation's stored bucket, with its stored
scope, period and policy version. The bucket/grant must predate the reservation,
and Premium period must match the original reservation time. Current entitlement and current policy are
never consulted. A historical Premium correction creates no current-period
allowance. Starter restoration changes only its existing lifetime balance.

`allowance.settle()` revisits EXPIRED bindings, including previously settled
ones. `settle_pending(owner)` selects at most three candidates, excludes applied
receipts, and rotates incomplete cases by binding update time. Missing retained
reservations mark bindings UNOBSERVABLE, stopping repeated scans. These run at
existing authenticated Training command reconciliation opportunities. No new
endpoint, scheduler or provider request is introduced. Dormant owners depend on
an explicitly operated sweep or a future separately approved periodic job;
production scheduling and activation remain separate gates.

Retention now selects at most 500 terminal reservations per invocation. Each
owner is locked before a NOWAIT operation lock, then binding, reservation and
bucket. Eligible corrections run before deletion, in the same transaction.
Transient lock/DB failures retain evidence for retry. An applied receipt and its
original reservation remain for the full existing 90-day window from application;
a later cleanup removes both via reservation cascade. Old unreferenced Premium
buckets can then be removed. Definitively incomplete evidence may age out without
refund. Evidence already purged is never reconstructed. User erasure cascades
all records; explicit application erasure also lists the receipt first.

The compensation lock order is owner -> operation NOWAIT -> binding -> reservation
-> bucket. Owner acquisition has a two-second PostgreSQL lock timeout. NOWAIT
prevents a blocking reverse edge against operation -> owner dispatch/retirement.
No compensation transaction spans provider I/O. Lost acknowledgments replay the
receipt without another movement; precommit failures roll back both facts.
Migration `f8b2d4e6a0c3` is additive, verifies create-all-first DDL, performs no
backfill, and refuses downgrade with retained correction evidence.


Retention callers cannot shorten the 90-day original-evidence horizon or the
independent 90-day audit horizon from compensation application. Negative or
non-timedelta retention is rejected before SQL; zero is valid with the same
floor. A longer caller horizon extends retention. Exact day 90 remains retained;
ordinary deletion is strictly after the applicable deadline. Owner erasure
remains a separate authorized path. The singleton ai_allowance_purge_progress
stores only finite cycle bounds, with no financial meaning or foreign key.
Repeated successful invocations traverse a finite cycle despite a locked prefix;
new IDs enter the next cycle. A crash after a claim cannot abandon evidence.
No wall-clock completion is promised without actually operated compatible
sweeps; dormant-owner scheduling remains a runtime activation gate. No scheduler
is installed. Revision f8b2d4e6a0c3 is unmerged and now creates this progress table
alongside receipts; its parent and 55-revision single-head graph are unchanged.
