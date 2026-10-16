# D1-R2 test-seam remediation

Original PR #439 HEAD: `4845711dc608a27cd5b02b002f72ad89fa9d916b`.

The independent review found automatic synthetic ALLOW in five modules. This repair removes those calls, keeping the provider module's unrelated spend-counter isolation fixture. Individual downstream tests now explicitly request the existing function-scoped `synthetic_ai_layer` or `synthetic_ai_memory_layer`. No provider fixture, imported fixture, module/class marker or conftest autouse fixture supplies ALLOW indirectly. The helper now defaults Memory OFF and restores its app config change with monkeypatch.

| Module | Explicit AI test functions | Explicit Memory test functions |
|---|---:|---:|
| test_ai_provider_call.py | 31 | 0 |
| test_pa06_privacy.py | 9 | 3 |
| test_pa06_pending_provenance.py | 0 | 5 |
| test_mobile_coach_api.py | 19 | 2 |
| test_mobile_training_generation_api.py | 14 | 0 |

Counts precede parametrization. Existing test bodies and substantive assertions are unchanged. Validation/domain/provider-isolation tests are not real consent proof. Authentication, missing-owner/context, unexpected social AI analysis, first-party history and deterministic provenance tests retain the real gate wherever they need no downstream ALLOW. Optional history/summary reuse is explicitly Memory-specific.

Each affected module now probes both OpenAI and Bedrock physical dispatch with two owners and no ALLOW fixture. Only the admission generation snapshot is frozen at zero to reach the final production gate on SQLite; dispatch, policies and authority remain real and deny. SDK call count stays zero. Separate opt-in tests prove mocked success, no durable consent/event rows, Memory OFF, separate explicit Memory ALLOW, and default-deny restoration. A subprocess collection audit checks pytest's actual transitive fixture closures and requires every synthetic ALLOW to be requested on the individual function. The helper-call inventory permits only the two named non-autouse function fixtures.

A negative control replaces final dispatch with a null context only during the ten new affected-module probes: all ten fail with DID NOT RAISE. No runtime source is mutated. PostgreSQL authority tests retain real grants, missing-consent denial, withdrawal, cross-owner isolation, per-attempt checks and locking, with mocked final SDKs.

Local focused result and hosted exact-head CI evidence are recorded in `/Users/yusuf/axisai-d1-r2-test-seam/FINAL_REPORT.md`. The existing CI push trigger includes only this additional D1 branch to qualify its exact HEAD despite unavailable PR merge refs. This does not qualify integration with current main or resolve migration ancestry.

## Migration dependency: unresolved

At review time D1 `d1c0a15e0001` and #436 `c2b9d0e1f2a3` both descend from `b1a8c9d0e1f2`. On 2026-10-16 GitHub reports #436 merged at `1b060aaf7d007b5aecd3107459dbd43d9827eaa0`, from head `14c60d12c1afe77898c1673af8a5b5c392aa21ac`. PR #439 is conflicting. Neither migration nor ancestry is changed in this repair.

Required order: establish #436's merged source/base; separately reconcile D1's ancestry against that resulting base and resolve integration conflicts; independently verify the revision graph, fresh upgrade/model parity and upgrade of the merged #436 state; only then consider D1 source merge. Earlier DDL compatibility in both orders is not valid Alembic ancestry. Branch-head CI cannot close this integration hold.

## Incomplete original review input

The prior independent review's original user request was recovered from its local session record. It ends at Section E, “D1 migration `d1c”. No complete original Section E was found in the review/evidence material. The omitted remainder is **NOT_ASSESSED**. This repair does not invent it or claim full independent acceptance.

No AWS, deployment, production traffic, activation, other-PR edit or merge. Runtime security contract and production code unchanged. Paid launch HOLD; next action is focused independent R2 review, with migration integration separately held.
