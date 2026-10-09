# LP18-B1 native replacement contract qualification

Local branch: `lp18/b1-native-replacement-contract`.
Frozen stack base: `adb78bf5dd7cbc311441ac8257dd8e07e7e099f4` (B0 #421).
The original uncommitted admission audit has been preserved. Its no-migration
block is superseded by the user's explicit authorization of one additive
replacement-generation authority. LP18-A, B0 and the B1 audit were read.

B0 #421 was inspected read-only during this work: OPEN, not merged, remote head
`744c26ecb2e793fca7a95aacc2aa4f937d0aac06`. B1 remains on the supplied frozen
B0 base. There was no reconciliation, push, B1 PR, CI, merge or deployment.
After B0 merges, reconcile once, run affected checks, create one PR and allow
one natural CI. No merge or deployment is authorized.

## Generation authority

The separate PostgreSQL table
`training_plan_replacement_generation_operation` is added by expand-only
revision `b1a8c9d0e1f2`, parent `e3f4a5b6c7d8`. There is one Alembic head.
Existing first-plan recovery storage is not reused: its GENERATED consumer
persists a TrainingPlan, which cannot represent an unconfirmed replacement.

Owner plus domain-separated key digest is unique; raw keys are never stored.
Canonical normalized preference fingerprints bind semantic intent. Each claim
freezes exact current lineage/version/UTF-8 snapshot digest, language, and the
canonical generator's bounded feature context. The ledger stores status,
attempt count, random lease fence, deadline, soft proposal locator, frozen
review DTO and expiry, typed failure, quota reservation/week and timestamps.
Schema constraints bound attempts/context/review and enforce complete state
shapes. A partial unique index allows one IN_PROGRESS operation per owner.
Account erasure explicitly purges this table and its owner FK is CASCADE.

Admission uses a short owner-scoped transaction advisory lock in the distinct
two-int namespace `0x41584919`. It persists identity and quota BEFORE provider
I/O. Claim commits release all locks; provider work receives frozen features,
not expired ORM objects that might silently open a transaction. The existing
canonical validator/generator, heavy provider routing, spend guard, rate and
concurrency controls, completion budgets and timeouts are reused. No provider,
catalog network or plan writer runs in confirmation.

Same key/intent success returns the original proposal token, frozen review and
original expiry with no provider or catalog projection. This continues after
proposal cleanup or subsequent plan replacement. Conflicting intent returns
409 before recovery. Failed keys replay their typed terminal failure.

An attempt owns a ten-minute lease. Same-key recovery after expiry rotates its
random fence, reuses the frozen context and quota reservation, and increments
attempt count to at most two. Publication AND failure bookkeeping check the
current fence, IN_PROGRESS state and unexpired lease under a refreshed row
lock. A late attempt cannot overwrite either a newer attempt or terminal
outcome. A second expired attempt records RECOVERY_EXHAUSTED. An explicitly
new key can retire an expired abandoned operation as SUPERSEDED; old keys
cannot revive it. Different keys during a live lease return IN_PROGRESS.

Recovery can duplicate provider work when a crash leaves an uncertain result.
The bound is two generation attempts, each using the canonical maximum two
completion calls; transport retries remain governed by the existing provider
policy. Exactly one provider invocation is not promised. There is exactly one
published effective proposal for an operation.

One weekly training quota reservation is committed with admission and reused
across recovery. Definitive provider/control/validation failure refunds once
using the original reservation week. Success consumes the reservation.
Uncertain abandonment/exhaustion and a candidate rendered stale during provider
work conservatively retain it. Refund takes operation -> User, before any plan
lock; publication takes operation -> User NO KEY UPDATE -> current plan UPDATE.
No User UPDATE upgrade is attempted while holding plan locks. Different
operations retain the existing shared training quota bucket and limit.

Candidate validation, bounded review projection and serialization precede
publication locks. Publication checks all three frozen plan dimensions again.
The extracted `stage_proposal_in_transaction` has no commit/rollback. Proposal
insert plus generation SUCCEEDED, token, review and timestamps commit together.
The existing public `stage_proposal` wrapper retains its commit behavior.
Generation never constructs or replaces TrainingPlan.

## Frozen HTTP contract

Both POST routes live under the existing Bearer-only mobile blueprint, derive
ownership exclusively from `g.mobile_user`, require the existing 8–64 character
Idempotency-Key grammar, reject unknown request fields and return no-store.
Generation and confirmation use independent key namespaces. No keys, candidate
content, injuries or exception details are logged by these adapters.

`/api/v1/training/plans/replacement-proposals` accepts exactly the existing
eleven native generation preferences, through the canonical native parser.
201 has exactly `contract_version: 1`, `proposal_token`, UTC `expires_at` and
`candidate`. Candidate has only `score` and seven ordered `days`. Each day has
weekday, kind, focus, duration_minutes, estimated_calories and exercises; each
exercise uses the existing bounded native display projection. No executable
workout references or synthetic plan identity are issued. Bounds: 32 exercises
per day, 256 KiB candidate bytes and 512 KiB serialized review. Success replay
has the same DTO and `Idempotency-Replayed: true`.

`/api/v1/training/plans/replacement/confirm` accepts exactly `proposal_token`
(a 43-character server locator) and literal boolean `confirmed: true`.
It delegates once to B0 `confirm_replacement`, and returns exactly
`{"contract_version":1,"outcome":"applied","reread_required":true}`.
Receipt identity and result lineage are not exposed. Success/refusal replay
carries `Idempotency-Replayed`. The canonical current-plan reread is mandatory.

B0 remains the sole replacement authority: transition lock, ANY ACTIVE refusal,
locked Coach-pending refusal, lineage/version/exact digest guard, fresh lineage
and version zero, atomic canonical write/receipt, durable refusal replay and
original successful confirmation replay all remain intact. Confirm does zero
provider work. Cancellation remains local to the client.

Errors use exactly the existing mobile `error` object with `code`, `message`,
`retryable`, `request_id`; there is no raw exception or receipt content.
Invalid keys are 400; invalid/unsupported preferences or confirmation bodies
are 422; quota refusal is 402; in-progress, conflict, stale, recovery exhaustion,
supersession and terminal confirmation refusals are 409; missing/wrong-owner
proposal is indistinguishable 404; provider busy/unavailable and infrastructure
unavailability are 503; rate refusal is 429. Terminal generation failures do not
claim that retrying their consumed key can make progress. Infrastructure rollback
remains retryable. B0 refusal mapping is closed and tested for every status.

## Evidence and remaining work

- B1 HTTP/service checks: 39 passed, including actual opaque Bearer session,
  real canonical generation, cross-owner concealment, loss/replay, lease expiry,
  context freeze, all binding dimensions, publication failure at proposal,
  finalization and commit, quota refund, strict DTOs and account erasure.
- B1 architecture and SQLite migration/drift checks: 10 passed.
- Affected regression group: 272 passed; no full suite run.
- Additional affected guard/migration group: 230 distinct checks qualified;
  two stale architecture allow-list/head expectations were updated precisely,
  then their affected 46-check group passed.
- Real disposable PostgreSQL: 10 B1 checks passed, including missing-row claims,
  duplicate/differing intent during provider work, quota single reservation,
  lease takeover with a delayed original provider return, cross-owner keys,
  rollback at all publication boundaries and confirmation replay, migration
  rerun/model parity and damaged same-name CHECK refusal. The 21 B0 PostgreSQL
  foundation checks also passed, including confirmation races. No qualification
  case was skipped. These are 31 distinct PostgreSQL checks.
- Expand/contract gate: PASS (48 revisions); one head; schema drift fails closed
  for columns, unique key, CHECK expression, partial predicate and owner cascade.
- 20/20 meaningful mutants require assertion detection; evidence is bounded in
  `evidence/lp18-b1/mutations.json`. Import/collection failures, timeouts and
  unclassified exceptions do not qualify. Source is restored after each mutant.

P0=0; P1=0; P2=1; P3=0. P2 is the already-deferred bounded retention worker and
cleanup authority, now also covering generation tombstones and frozen reviews.
Successful generation replay metadata/review is retained at least 90 days;
terminal generation context is cleared immediately. Confirmation receipts retain
B0's minimum 90-day guarantee. No automatic deletion worker is introduced.
Account erasure removes all of these authorities. A future cleanup must retain
successful replay metadata through its horizon and cannot turn an old key into
new provider work. The current code keeps evidence until account erasure.

Local qualified implementation is on HOLD for B0 merge. Mobile repository,
B0 PR, production and deployment state were not modified.
