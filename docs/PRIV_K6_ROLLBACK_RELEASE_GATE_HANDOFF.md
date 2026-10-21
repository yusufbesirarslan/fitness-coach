# K6 rollback compatibility release gate — OPEN

K6 must remain production HOLD. This document is a remediation handoff, not an
implemented safeguard, and does not resolve independent review P2-02.

## Reproduced incompatibility

The exact pre-K6 BASE 01befa99aad2a2a20a465dfe2c03b636b062d9a1 completed-day
reader ignores a new no-photo claim. Against disposable PostgreSQL 16 the BASE
canonical service then commits a second reward bundle: one retained K6 claim,
one PumpCheck, two markers, two activities, 45 total XP. The current BASE HTTP
photo routes have an independent D1 deny gate; this is a service-level rollback
compatibility defect, not evidence of a reachable HTTP consent bypass.

Refusing schema downgrade protects retained evidence from deletion. It does
not stop old application code from reading/writing the retained schema.
`scripts/r6_deploy_transaction.py:classify_migration_delta` only classifies
schema overlap, while `Transaction._rollback` restores previous web and
worker images without a completion capability check. A new startup check or
feature flag in K6 cannot constrain an older image or its older deployment
script. A claim-count check without a write fence races new completions.

## Required enforceable gate before first K6 write

The release/deployment owner must implement and independently qualify a gate
outside replaceable application images, covering automatic rollback and manual
route/worker restoration. It must:

1. Drain and fence all historical-only web, worker, scheduled and manual
   completion writers before permitting K6 writes. Preserve READ COMMITTED
   and the shared owner/day advisory-lock protocol.
2. Establish a durable monotonic minimum capability, e.g.
   `workout_completion_claims_v1`, bound to qualified immutable web/worker image
   digests. Qualification must prove union reads, canonical claims in both
   modes, locked re-check, deletion semantics and exact-once rewards. Include
   every intended rollback image, not just the new candidate.
3. Enforce the durable floor before candidate traffic, worker start and every
   previous-route/previous-worker restore. Manual deploy commands and old
   deployment scripts must lack authority to bypass it. Missing, unknown or
   incompatible image identity must fail closed.
4. Once the floor is raised, retain it across feature disable, image rollback,
   process restart, schema upgrade and restore. If no compatible image is
   available, keep writes fenced in maintenance and fix forward; do not route
   back to a historical-only writer.
5. If an empty-claims exception is required, acquire an exclusive deployment
   write fence acknowledged by all writers, drain in-flight transactions, and
   only then check emptiness. Keep the fence through the entire transition.
   A configuration flag plus an unfenced SELECT is insufficient.

## Required acceptance evidence / R2 handoff

Use disposable PostgreSQL 16 and synthetic data. Record exact source/tree and
immutable image digests. Demonstrate: incompatible candidate denied before
traffic; incompatible automatic and manual web/worker rollback denied after
one no-photo and one photo claim; feature OFF and restart retain the floor;
missing/forged capability denied; concurrent completion cannot pass an
empty-claims exception; compatible rollback sees retained and historical days
and cannot duplicate rewards. Inject failure at each deployment step and
prove no path restores an incompatible image. Rehearse the maintenance/fix
forward path and rollback of the gate host itself. Deployment owner and
independent reviewer must accept the actual implementation and evidence.

No host/image enforcement is added by this K6 remediation. Retrofitting a
partial check into the current candidate deploy script would leave manual and
old-script restoration unprotected and would create false assurance. This
cross-image operational control needs a separately scoped deployment change.
Until it is implemented and qualified, P2-02 is OPEN and PRODUCTION=HOLD.
