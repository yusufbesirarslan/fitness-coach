# LP18-B1 native replacement HTTP contract — local draft

Stack base: LP18-B0 `adb78bf5dd7cbc311441ac8257dd8e07e7e099f4`, PR #421.
This is the first local B1 design artifact; endpoints are not implemented yet.
B1 stays local until B0 merges. The reconciled B0 authority takes precedence
over earlier LP18-A recommendations about locks, refusals and retention.

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
