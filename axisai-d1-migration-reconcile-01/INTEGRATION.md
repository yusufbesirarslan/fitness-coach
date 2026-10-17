# D1 current-main integration handoff

Accepted original D1 HEAD: `1fd6fa9fe87fce910de82c1ab2ec7af560a19fc6`.
Initial main: `551f711b7c0d1dea24ddad7de6f598ed9df60c42`.
Final refreshed main: `f803bb43a26ba76a2ecc17cb498e919b4efe992c` (#442).

The initial controlled merge `d878698` and focused test corrections reached candidate `c3cfa6e`, with successful 12/12 direct and PR-merge CI. Final remote verification found a material main advance: training-dispatch recovery and its new migration. A second bounded merge is therefore necessary; there are no rebases, history rewrites or force pushes. Original R2 commits remain ancestors.

## Final graph and conflict resolution

```text
b1a8c9d0e1f2 -> c2b9d0e1f2a3 -> a9c1d2e3f4b5
  -> b0d3e4f5a6b7 -> d1c0a15e0001
```

D1's parent/header alone change; its DDL, constraints, fail-closed validation and populated-evidence downgrade refusal are unchanged. All 51 main migration files, including Apple, allowance and dispatch uncertainty, remain byte-identical. The graph has 52 reachable revisions and one D1 head. No evidence of non-disposable D1 application was found in the inspected draft/qualification record; no external database state was accessed or invented.

Initial six conflicts retained consent plus subscription/allowance routes and their approved inventories. The late four conflicts are only LP18-B0/B1, migration graph and workout-session architecture head inventories; they retain D1 as final head and count 52. Main's model, workflow and dispatch-recovery runtime/test changes are retained without independent modifications to that MON-02 functionality.

Consent, subscription entitlement, allowances and dispatch uncertainty remain independent authorities. An internal dispatch marker is not processing consent. D1 still checks each physical SDK attempt. The disclosure registry stays empty; Memory and monetization defaults remain dark; no new API abstraction or activation is introduced.

## Qualification boundaries

`tests/test_ai_consent_pg.py` is in the mandatory PostgreSQL CI job. Added coverage runs on PostgreSQL 16 and verifies empty full-chain bootstrap, all 52 revisions/single head, zero model/schema drift and no implicit grants. Existing-main testing actually upgrades through b0d3e4f5a6b7, populates two owners with subscription/allowance/training rows and NULL/TRUE dispatch markers, then applies D1. Every pre-existing table's rows and reflected schema are compared. Exact reentry, validated checks, name-only/NOT VALID rejection, FK/account cascades and evidence-preserving downgrade remain covered.

Paid entitlement plus a matching reserved training-generation allowance still cannot authorize either recipient's SDK attempt for either owner without consent. No SDK calls or consent writes occur; Memory stays unavailable. Dispatch-recovery crash/concurrency tests run alongside the D1 and monetization suites.

The accepted R2 authorization fixtures, explicit per-test ALLOW markers and all original privacy assertions are preserved. `test_pa06_privacy.py` closes existing test responses and asserts capacity restoration: two unclosed SSE responses previously held permits before subscription capacity tests. This changes no runtime code or authorization seam. Main's independently merged build_features stub is retained in the existing training API layer-isolation test. The fixture-closure audit and real-gate negative controls remain intact. Prior independent R2 approval is reused rather than repeated.

Final identities, tests, exact-head CI and evidence: `/Users/yusuf/axisai-d1-migration-reconcile-01/FINAL_REPORT.md`; historical 551f711 qualification is clearly separated. Independent integration review must verify ancestry, both upgrade paths, merged migration/runtime preservation, conflict resolutions, D1/PA06 constraints and CI binding. Section E remains NOT_ASSESSED. No source merge, AWS/external database access, deployment, consent/payment activation or public paid launch is authorized.
