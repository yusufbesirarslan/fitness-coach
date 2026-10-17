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
  `tests/test_ai_allowances_architecture.py`). `app/services/premium.py`, its
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
inserting its reservation. Retention deletes terminal reservations before empty
old Premium buckets, without an owner lock. The ledger never takes a plan or operation lock, so it
cannot form a cycle with the repo-wide order (operation row → user → plan rows).
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
