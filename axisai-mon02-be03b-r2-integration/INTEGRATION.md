# BE-03B post-D1 integration review handoff

Integrates main `5597645573674d13d1a81e11134cced0ba8a9e6c` into PR #443 at
`a6e4005f7570bd6c43049cd5dd09a1f0cf748db8` with a regular merge. No source merge,
deployment, AWS operation, environment database access or feature activation.

## Review delta

- Four textual conflicts: migration head assertions in `test_lp18_b0_migration`,
  `test_lp18_b1_migration`, `test_migration_graph`, and
  `test_mobile_workout_sessions_architecture`. One head: b1e4; revision count: 53.
- Reparent only the unmerged b1e4 migration to D1 (`d1c0a15e0001`). D1 still
  follows b0d3. No further edits to historical migration definitions; the existing
  candidate's narrow a9c1 create_all-first compatibility extension is retained.
- Verify automatic unions in models, account deletion CLI and CI PG selection.
  D1 consent tables and the allowance binding are both retained.
- D1 PG chain tests use reflected historical tables to seed b0d3 without columns
  introduced by future ORM models. Original D1-only schema/row preservation,
  downgrade refusal and cascade assertions remain; full-head parity follows.
- Existing allowance HTTP layer tests explicitly opt into D1's per-test
  `synthetic_ai_layer` fixture. No new autouse fixture or production bypass.
- New real-authority tests: native HTTP denied before any hold, quota or provider
  work with the empty registry (enforcement ON/OFF); Premium + hold + dispatch
  marker still cannot authorize OpenAI/Bedrock (empty registry/missing grant).
- New real full-chain PG tests: fresh base and seeded D1 to b1e4, zero model drift,
  preservation of consent events/state, subscription, ledger and dispatch history,
  no implicit grants or metering backfill, compatible table reentry.
- CI push branch selection adds this existing PR branch for exact-head execution;
  pull_request CI separately qualifies the synthetic merge commit.

## Preserved runtime contract

Generation/ledger source, flags and billing settlement are unchanged from a6e4005.
Operation lock -> ledger owner/reservations/bucket -> binding -> marker -> commit;
no generation/ledger transaction crosses provider I/O. D1's separate existing
consent fence intentionally holds its account lock through the bounded SDK call
for withdrawal ordering; this integration does not change that privacy contract.

The empty production disclosure registry keeps fresh native generation closed
before allowance reservation. At the final provider gate, entitlement, allowance
and dispatch permission independently fail to supply consent. A dispatch marker
records possibility, not proof of transmission. If that durable boundary was
crossed and later completion is uncertain, billing remains UNKNOWN and uses the
existing configured release cap. No new definitely-unbilled classification or
cap is inferred from a refusal, which could follow an earlier physical attempt.
No activation decision is made here.

## Existing evidence to reuse

`~/axisai-mon-02-be-03b-01b/evidence/mutations.log`: M1-M8 killed (settlement,
mode, quota, UNKNOWN, catch-up, replay, cap, atomic hold/marker).
`~/axisai-mon-02-be-03b-01b-r2/evidence/mutants.log`: G0-G2 and V1-V7 killed
(TTL and exact-schema validation). Their detecting runtime code is unchanged;
mutations are not expanded into a new audit. The strict verifier and accounting
regression suites are rerun against this integration.

Independent review remains PENDING. Review ancestry, strict verifier, conflict
unions, atomic lock/commit boundary, UNKNOWN/caps, TTL, recovery/idempotency,
D1 independence, OFF parity, and both CI revisions. Do not merge or activate.
