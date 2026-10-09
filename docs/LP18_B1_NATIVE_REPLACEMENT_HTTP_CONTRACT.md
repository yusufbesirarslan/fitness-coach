# LP18-B1 native replacement HTTP contract — local draft

Stack base: LP18-B0 `adb78bf5dd7cbc311441ac8257dd8e07e7e099f4`, PR #421.
This is the first local B1 design artifact; endpoints are not implemented yet.
B1 stays local until B0 merges. The reconciled B0 authority takes precedence
over earlier LP18-A recommendations about locks, refusals and retention.

## Implementation admission audit — BLOCKED

The LP18-B1 implementation request requires PostgreSQL-backed proposal-generation
idempotency while keeping `MIGRATION_ADDED=no`. At the frozen B0 SHA these
requirements cannot both be met within the approved storage authorities:

- `TrainingPlanReplacementProposal` stores only an immutable completed candidate,
  origin binding, locator and review timestamps. It has no generation command key,
  semantic intent fingerprint, admission state, frozen generation context,
  attempt counter, failure result or generation-command uniqueness constraint.
- `stage_proposal` explicitly delegates generation idempotency to B1. It creates
  and commits a new proposal on every invocation. Its non-null candidate fields
  cannot represent an operation admitted before provider work.
- `TrainingPlanReplacementReceipt` represents terminal confirmation only. Its
  closed status constraint and immutable result semantics cannot represent
  generation admission or recovery. Generation must not create a receipt.
- The first-plan `TrainingPlanGenerationOperation` has no command-kind isolation
  or frozen replacement binding. Its owner/key lookup and active-owner index
  cover all rows, GENERATED recovery calls `commit_plan`, and SUCCEEDED replay
  resolves a canonical TrainingPlan. LP18-A explicitly rejects silent reuse of
  this ledger for replacement. Namespacing a key alone does not isolate those
  consumers or supply replacement generation recovery semantics.
- A PostgreSQL advisory lock serializes live workers but does not persist a
  key-to-intent-to-proposal mapping after release or worker loss. Deriving the
  proposal locator from a key cannot record a different-intent conflict before
  candidate persistence or preserve failure/recovery metadata. Process memory,
  Redis and unrelated owner metadata cannot be the final authority.

Required scope decision: authorize a dedicated replacement-generation operation
table and an additive migration, or retain the no-migration boundary and hold B1.
No B0 transaction, transition lock, proposal immutability or receipt semantics
need redesign. The proposed addition owns only generation admission/recovery:
owner-scoped key digest uniqueness, LP18/native semantic intent fingerprint,
frozen origin/context, bounded attempts and terminal failure, and a soft reference
to the staged B0 proposal. Publication must atomically bind that proposal to the
generation operation; a crash between staging and command completion must not
publish a second effective proposal. Account erasure must include the new table.
Candidate validation/projectability and provider controls remain outside B0
confirmation locks. Retention for generation replay needs an explicit contract.

PR #421 was checked read-only on 2026-10-09: OPEN, head
`adb78bf5dd7cbc311441ac8257dd8e07e7e099f4`, no merge timestamp. No rebase,
push, B1 PR or CI was performed. Runtime/model/migration files remain unchanged.
This audit stops implementation admission; the complete subsystem review,
HTTP/PG qualification and required 16 mutations remain outstanding.

The transport tables below are historical design proposals, **not a frozen
LP18-C contract**. The current B1 request supersedes their receipt exposure:
confirmation success must omit receipt identity and use a small acknowledgment
with mandatory canonical reread. Final request names, errors and limits must be
updated and tested after the generation-storage decision. B0 supplies no
product-visible pending review slot requiring cancellation; cancel remains local.

## Transport boundary

Both routes require existing mobile Bearer authentication; the owner comes only
from `g.mobile_user`. Use the existing closed mobile error envelope and private
no-store responses. Require `Idempotency-Key` with `[A-Za-z0-9._:-]{8,64}`;
proposal generation and confirmation have separate key namespaces. Reject
unknown JSON fields and client-supplied owner, plan, lineage, version, digest,
timestamps or candidate JSON. No raw key, candidate or exception logging.

| Route | Closed request | Successful response |
|---|---|---|
| `POST /api/v1/training/plans/replacement-proposals` | The existing eleven native generation preference fields, parsed by the canonical native parser | 201 with opaque `proposal_token`, UTC `expires_at`, contract version 1 and bounded candidate review; no executable workout reference |
| `POST /api/v1/training/plans/replacement/confirm` | Exactly `proposal_token` and literal boolean `confirmed: true` | 200 with immutable receipt reference, contract version 1, `outcome: applied`, and `reread_required: true` |

Fresh/replay responses carry `Idempotency-Replayed`. A successful confirmation
receipt describes the original operation; it does not assert that its result is
still the current plan. The client rereads the existing current-plan endpoint.
Cancel remains client-local and never calls confirmation.

## Foundation result mapping

The transport calls B0's `confirm_replacement` once after auth/body/key checks.
It must not duplicate replacement logic, own an extra commit, move locks, or
perform provider/catalog/network work inside confirmation authority.

| B0 result | Proposed HTTP/code | Client action |
|---|---|---|
| APPLIED (fresh or replay) | 200 immutable success envelope | Reread current plan |
| STALE | 409 `TRAINING_PLAN_STALE_CURRENT_PLAN` | Reread and generate a new review |
| ACTIVE_REFUSED | 409 `TRAINING_PLAN_ACTIVE_SESSION_REFUSED` | Explicitly resolve the session, then confirm with a fresh key |
| COACH_PENDING_REFUSED | 409 `TRAINING_PLAN_COACH_PENDING_REFUSED` | Explicitly resolve Coach intent, then confirm with a fresh key |
| EXPIRED | 409 `TRAINING_PLAN_PROPOSAL_EXPIRED` | Generate a new review |
| CONSUMED | 409 `TRAINING_PLAN_PROPOSAL_CONSUMED` | Reread; never apply this proposal again |
| ConfirmationConflict | 409 `TRAINING_PLAN_IDEMPOTENCY_CONFLICT` | Preserve the original key/intent binding |
| ProposalUnavailable | 404 `TRAINING_PLAN_PROPOSAL_UNAVAILABLE` | Same response for missing and wrong-owner locators |

These codes are a local B1 draft pending the adapter's closed-vocabulary tests.
Durable refusals consume the confirmation key, not the proposal. Same-key
refusal replay stays refused after session/Coach resolution; a fresh key is a
new explicit acceptance attempt. Infrastructure faults roll back and consume no
key. Successful receipt lookup precedes expiry/current-plan/session checks and
continues to return the original result after later replacement or proposal
cleanup. Successful receipts guarantee at least 90 days of replay. Retained
receipts continue replay after day 90; exact day-90 deletion is not promised.
The cleanup authority and bounded worker remain deferred P2.

## Generation work remaining

Before exposing proposal generation, implement durable generation admission,
owner/key intent binding, bounded provider recovery, and candidate
validation/projectability. B0's trusted staging primitive alone does not provide
generation idempotency. Do not reuse first-plan auto-persistence semantics or
change the transition architecture to add these consumers.

Freeze the server-derived current base and normalized preferences on first
admission. Release database locks before provider work. Reuse the canonical
candidate generator, quota/spend/rate/concurrency controls and validation; never
persist posted injuries/preferences or alter TrainingPlan during review.
Replay staged candidates without another provider call; a changed intent under
the same generation key conflicts. Recovery before durable staging requires
explicit bounded attempts and cannot promise exactly one provider invocation.
Check the base again before publishing review and let B0 check it again under
confirmation authority. Keep candidate review projection separate from current
plan projection, without minting a synthetic persisted plan or lineage.

## Qualification to implement with the adapters

Exercise real Bearer auth and wrong-owner indistinguishability, strict JSON and
literal-true acceptance, closed error mapping, stable receipt envelopes, fresh
and replay headers, durable refusal replay/fresh-key resolution, and response
loss after commit. Generation tests must prove candidate-only persistence,
idempotent replay/conflict, bounded recovery, and zero provider calls in confirm.
Register only exact routes in existing gate/inventory tests. Preserve B0's
PostgreSQL races and additive migration. No B1 PR, push, merge or deployment is
authorized before B0 merges.

## Authorized additive implementation — qualification supersedes admission block

The user's LP18-B1 unblock decision explicitly supersedes the earlier
`MIGRATION_ADDED=no` restriction for one generation-operation authority.
The historical audit above is preserved unchanged. The implemented contract and
qualification are recorded in [LP18_B1_IMPLEMENTATION_QUALIFICATION.md](LP18_B1_IMPLEMENTATION_QUALIFICATION.md).
In particular, confirmation exposes only the applied acknowledgment with
mandatory reread, and generation has its own durable fenced operation table.
The historical transport table's receipt exposure is superseded.
The original stack was qualified before #421 merged. The post-merge reconciliation
and current review evidence are recorded in `LP18_B1_RECONCILIATION.md`.
