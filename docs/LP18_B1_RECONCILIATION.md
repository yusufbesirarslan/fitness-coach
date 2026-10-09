# LP18-B1 post-B0 merge reconciliation

B0 PR #421 is MERGED; its approved head was
`d265809bcc5d1f565eb701a92ca521c64b003f62`. Its squash merge and fetched main
are `a75d06b07017dd1a6ab89be4679c99b534c07871`.

Original qualified B1 head `c3ab69b2c079374c1bd88e13a6101f7c70a662ed` is
protected by `backup/lp18-b1-qualified-c3ab69b`. The original worktree keeps its
uncommitted audit, with a separate binary patch backup. This PR also preserves
that historical audit and explicitly supersedes its original admission block.

Ancestry inspection found exactly two B1 commits after frozen B0 base
`adb78bf5dd7cbc311441ac8257dd8e07e7e099f4`: `f5f36b9` and `c3ab69b`.
They were cherry-picked once into an isolated worktree on merged main, becoming
`5d2c7b1` and `276b25f`. No historical B0 commits were replayed. The only conflict
was the B0 migration graph test: retain merged B0's explicit revision, parent
and single-head assertions, while expecting new B1 head `b1a8c9d0e1f2`.

No implementation redesign occurred. B0 confirmation, refusal, fingerprint,
result and delivery functions are AST-identical to main. The only change to its
staging service is the previously qualified transaction-owned staging primitive
and commit-owning wrapper. Transition locks, workout snapshot ordering,
ANY-ACTIVE/Coach refusals, locked lineage/version/digest, atomic receipt/write,
durable replay and minimum 90-day horizon remain B0 authorities. Native confirm
delegates to B0 and performs zero provider calls.

LP16-B routes, parser, submission/history services, weekly service and
`reload_locked_owner` are unchanged from main. Browser replacement and first-plan
store/route behavior remain intact. No Flutter files or deployment files changed.
B0 migration `e3f4a5b6c7d8` is unchanged; B1's sole additive child is
`b1a8c9d0e1f2`.

## Qualification

- Focused B1/B0 API/service/contract, LP16, owner-lock, first-plan generation
  and replacement precondition group: **289 passed**.
- Auth/route inventory, B0/B1/LP16 architecture, CI sharding/selection,
  migration/expand-contract group: **273 passed**, including **109 migration
  checks**.
- Disposable PostgreSQL: **64 distinct checks passed**, no skips: B1 10,
  B0 21, LP16-B 8, LP16-A 7, browser/native replacement 18.
- Full Alembic upgrade from empty database and from B0 revision, upgrade reruns,
  B0/B1 verifier reruns, model column parity and unchanged B0 schema: **PASS**.
- One head: `b1a8c9d0e1f2`; expand/contract gate: **PASS (48 revisions)**.
- Existing schema-drift and migration guards passed. Original 20/20 meaningful
  mutation evidence remains applicable; no relevant implementation changed and
  mutation campaigns were not repeated. No full suite was run locally.

The first PG invocation used an unavailable driver and was corrected to the
installed psycopg2. A full-chain probe mistakenly overlapped the last race
module's shared disposable schema: the run had 62 passes plus three fixture
errors. Once the probe finished, all 18 affected module cases passed. Final
qualification counts distinct cases, not repeated executions.

Evidence and a rerunnable full-chain probe are in
[evidence/lp18-b1/reconciliation](evidence/lp18-b1/reconciliation/summary.json).
The pre-merge qualification document remains historical evidence; this document
supersedes its OPEN/HOLD state. Public PR CI is the natural exact-head CI; no
manual reruns, merge or deployment are authorized here.

P0=0; P1=0; P2=1; P3=0. P2 remains the deferred bounded retention cleanup worker
for proposal, generation replay/review and confirmation receipt authorities.
Existing records retain successful replay through at least 90 days and currently
remain until account erasure. Future cleanup must preserve that replay guarantee
and never turn an old key into new provider work.
