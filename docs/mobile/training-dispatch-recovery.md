# Native initial Training dispatch recovery (MON-02-BE-03B-01A)

This changes recovery safety even with allowance enforcement OFF. It does not
connect the AI allowance ledger or activate commercial policy. Owner acceptance
of the changed retry contract is required before merge/rollout. No deployment is
authorized by this document.

The existing operation remains the sole authority. Its nullable
`provider_dispatch_possible` is dispatch evidence, **never billing evidence**:

| Status / marker | Meaning / same-key recovery |
| --- | --- |
| IN_PROGRESS / False | Explicit new-code claim; no dispatch authorized. A short row transaction may win False → True. |
| IN_PROGRESS / True | Dispatch authorized and committed; zero or more internal provider attempts possible. Never authorize generation again. |
| IN_PROGRESS / NULL | Historical uncertainty; never authorize generation again. |
| GENERATED | Validated candidate durable; save only, no inference. |
| SUCCEEDED | Atomic plan + owner/lineage receipt; replay, no inference. |
| FAILED | Terminal logical outcome; replay fixed code, retryable=false. Marker survives. FAILED need not mean provider billing is known. |

Only the caller receiving successful commit acknowledgement of False → True
may invoke generation. The marker precedes even the first possible physical
call, conservatively including local preparation after authorization. No external
idempotency guarantee is assumed. A crash between marker commit and network send
can mean **zero** provider calls; recovery cannot distinguish that from a call
whose response was lost. Existing bounded format repair, provider transient
retry and fallback remain within that single authorized generation invocation.

Owner advisory arbitration is limited to admission/recovery and save-only
recovery. The unique active-owner index also spans workers. Inputs are frozen
before commit, any read transaction ends, the advisory connection closes, and
provider controls/dispatch run outside that scope. A short operation-row lock
arbitrates the marker. Final persistence keeps operation → User NO KEY UPDATE →
plan ordering, shared with browser persistence. Rows are refreshed under locks
so expired/terminal state cannot be overwritten from an ORM identity map.

MON-02-BE-03B-A2 tightens retirement for **metered** operations only. When the
locked read finds an aged operation, retirement also takes that owner's
`user FOR UPDATE NOWAIT` before committing FAILED. D1 already owns that account
fence throughout each blocking physical SDK call, including the scheduling gap
after A1's attempt marker commits. Retirement cannot commit FAILED in that gap.
If retirement wins first, the later attempt marker refuses and no SDK call starts.
Neither the operation lock nor owner advisory lock is extended across SDK I/O.

NOWAIT is essential: waiting for D1 while owning operation would invert D1's
user → operation admission order. Contention immediately rolls back and releases
operation, returning the existing persistence-unavailable response. A later
command can retry retirement after the fence is free. The 15-minute eligibility
interval is unchanged, but active D1 ownership can defer terminal retirement
beyond it, even if the worker is paused. This trades immediate occupancy recovery
for strict SDK-entry ordering; it does not impose a hard process or remote
execution deadline. The guarantee requires all retirement-capable workers to run
A2 (drain older workers at a separately approved rollout). Unmetered/OFF behavior
is unchanged.

IN_PROGRESS becomes eligible for retirement 15 minutes from its last durable
transition, lazily reconciled by the next owner command under short arbitration
(metered D1 contention can defer it as described above). It becomes
FAILED / TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN (503, retryable=false) if marker
True or NULL. Evidence and the existing legacy quota reservation survive. A
False marker can instead retire as TRAINING_PLAN_GENERATION_NOT_DISPATCHED and
refund its legacy reservation. Neither path mutates allowances. There is no
background worker or new operation table. A new attempt requires an explicit new
key and all existing plan, quota, rate, capacity and provider admission checks;
the same key never becomes reusable. GENERATED remains save-recoverable.

Retirement fences late candidate/failure writes, **not remote execution**. A
paused worker or provider may outlive the interval; a new explicit attempt can
therefore overlap remote work whose termination cannot be proven. There is at
most one *active durable native operation*, not guaranteed at-most-one remote
request across distinct logical attempts. Existing rate/capacity/spend guards
remain authoritative. This limitation needs explicit allowance policy in 01B;
never treat expiration or a new key as a provider cost refund.

## Crash boundaries

| Boundary | Durable state / physical inference | Recovery and client outcome |
| --- | --- | --- |
| Before claim commit | No claim / excluded | New claim may dispatch; transient error. |
| After claim commit, before marker commit | IN_PROGRESS False / excluded | Same key may win marker and dispatch. |
| Marker transaction rolls back | IN_PROGRESS False / excluded | Same key may dispatch. |
| Marker commit, before send | IN_PROGRESS True / possible, possibly zero | 409 IN_PROGRESS until retirement; then terminal unknown 503. No redispatch. |
| In-flight provider | IN_PROGRESS True / possible | Same as above; no assumption of remote cancellation. |
| Response returned, before staging | IN_PROGRESS True / mock confirms execution; durable evidence only possible | Same as above; lost output cannot be recreated with this key. |
| Staging transaction rolls back | IN_PROGRESS True / possible | Initial persistence 503, then 409/terminal unknown; never infer again. |
| Staging committed, before plan commit | GENERATED / possible | Save-only recovery; initial transient persistence error. |
| Plan transaction rolls back | GENERATED / possible | Candidate survives; save-only recovery. |
| Plan commit, before HTTP receipt | SUCCEEDED / possible | Replay canonical owner/lineage plan; lost HTTP response is not action failure. |

The historical staging-failure retry contract intentionally changes: previously
same-key retry ran generation twice; now it waits for recovery disposition and
terminates unknown if output was not staged. Successful and definitive failure
replays remain valid. Existing mobile 409/persistence error envelopes remain in
use; terminal unknown/not-dispatched use fixed safe command messages and
retryable=false. The client must explicitly initiate a new key after terminal
failure. This is **not byte-identical OFF parity**.

## Schema and rollout prerequisites

`a9c1d2e3f4b5` → `b0d3e4f5a6b7`: one nullable Boolean on the existing operation,
no backfill/default. Historical rows and old-code inserts remain NULL. The schema
is additive and old SQL can continue to write. Dispatch safety requires draining
old workers before serving commands with the new version: old code does not
honor the marker. Downgrade refuses to erase evidence. A runtime rollback to old
dispatch code is unsafe for uncertain rows; keep commands held instead. No
production/staging migration or rollout is performed by this task. Coordinate
with privacy #439 if it changes the canonical migration graph.

## Billing boundary

The adapter's usage telemetry/spend admission are not invoice receipts. Current
native candidates discard provider/cache provenance; timeouts, invalid output,
transport errors and lost staging do not prove billed or unbilled. All potentially
dispatched paths are UNKNOWN for billing at this boundary. Proven zero dispatch
is CONFIRMED_NOT_BILLED for this operation; CONFIRMED_BILLED requires evidence not
currently supplied. Never synthesize a receipt or map UNKNOWN to a guessed
`provider_billed` Boolean. Future ledger wiring needs reviewed tri-state evidence,
durable settlement intent/association, and founder approval of UNKNOWN/fallback
unit policy. GET allowance v1 and existing ledger semantics remain unchanged.
