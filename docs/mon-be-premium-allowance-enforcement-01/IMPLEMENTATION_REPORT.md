# Implementation and qualification record

Base: `e6af3067cec6ad623e16c57bae0141aa9d6ad91d` (also fetched main at recovery).
The exact reviewed/pushed revision is recorded in the PR and external task
`evidence/revision.json` after commit; obtain checkout identity with
`git rev-parse HEAD`. This document cannot embed its own commit's hash.

## Changed source

| Files | Result |
|---|---|
| `app/services/ai_allowances/execution.py` (new), `service.py` | Generic logical scope/dispatch marker over existing ledger, under-lock entitlement, sticky typed errors, bounded physical attempts, atomic evidence/settlement and canonical receipt |
| `app/models.py`, `migrations/versions/b5e7a9c1d3f0_allowance_dispatch_evidence.py` (new) | Four nullable reservation evidence columns, no new balances/tables/backfill; strict, repeatable, evidence-preserving migration |
| `migrations/versions/a9c1d2e3f4b5_ai_allowance_ledger.py` | Foundation verifier accepts later migration-owned columns; original DDL unchanged |
| `app/feature_flags.py`, `app/__init__.py`, `.env.example`, `docs/FEATURE_FLAGS.md`, `docs/ROLLOUT.md` | One atomic default-OFF unified gate, typed handler/publication installation and operator inventory |
| `app/services/ai_provider_call.py` | Independent metering admission and committed row-only evidence immediately before SDK; existing D1, spend/input limits and retry/fallback authority preserved |
| `app/services/mobile_training_generation/{allowance,service,store}.py` | Original adapter/dispatch binding retained; unified readiness and original operation scope; strict entitlement only in new mode |
| `app/services/premium.py` | New mode never treats legacy Boolean Premium quota bypass as authorization; OFF unchanged |
| `app/services/{ai_pipeline,ai_coach}.py`, `app/blueprints/coach.py` | Logical Coach turn wrappers, canonical tool receipts, synchronous refusal of unqualified web streaming |
| `app/services/training_generation/service.py`, `app/services/training_plan_replacement/{generation,service}.py`, `app/services/plan_mutation/service.py` | Web generation scope; proposed replacement binding in original dispatch commit; canonical mutation/proposal receipt |
| `app/services/nutrition_plan_generation.py`, `app/services/nutrition_native/plan.py` | Shared generation scope, native outer proposal validation/publication, no read/save unit, parse outcome distinct from billing evidence |
| `app/services/mobile_pump_checks/service.py`, `app/services/mobile_pump_check_comparisons/service.py` | Hold commits with original analysis lease; pair key convergence; canonical result receipt; lost lease cannot commit false success |
| `app/blueprints/mobile_menu.py`, `app/services/menu_analysis.py`, `app/services/ai_nutrition.py` | Native acquisition+analysis share one action, profile validation before possible paid OCR, joined worker scope propagation and bounded fan-out |
| `app/services/menu_extract.py`, `app/services/memory_manager.py` | Narrow existing proof/summary exemptions, no visible unit |
| `.github/workflows/ci.yml` | Exact branch-head CI and new PostgreSQL suites in existing concurrency gate; no deployment workflow changes |

New tests: `test_premium_allowance_execution.py`,
`test_premium_allowance_execution_pg.py`, `test_premium_allowance_training_pg.py`,
`test_premium_allowance_migration.py`. The existing Coach real transport harness
is parameterized legacy/metered; existing architecture import boundaries are
updated for the explicit new seams. Current-head migration assertions advance
to the single 59-revision head; historical migration-parent assertions remain.
Changed head assertions: `test_ai_consent_pg`, `test_apple_lifecycle_migration_pg`, `test_completion_fence_pg`,
`test_lp18_b0_migration`, `test_lp18_b1_migration`, `test_migration_graph`,
`test_mobile_training_allowance_pg`, `test_mobile_workout_sessions_architecture`,
`test_priv_k6_no_photo_completion_pg`. See `git diff --name-status <base> HEAD`
for the exact complete file inventory, including this task's documents/model.

## Verification evidence

All identities, disclosures, policies, photos and SDK responses used in tests
are synthetic. No live Bedrock/OpenAI, production data, payment or AWS call.
Disposable PostgreSQL is dedicated to this task on localhost:55971. Financial
model inputs remain read-only. Logs are retained in the external task directory
`~/axisai-mon-be-premium-allowance-enforcement-01/evidence/`.

| Evidence | Result / scope |
|---|---|
| `final-focused.log` | 408 PASS: logical/ledger/API/architecture, flag inventory, native Training allowance, migration shape/graph/expand checks |
| `final-feature-regression.log` | 284 PASS: existing Pump/comparison, LP15 native menu, native nutrition, Coach and D1 compatibility |
| `final-compatibility.log` | 99 PASS: final typed refusal compatibility, Pump source boundaries, logical failure/owner/lease tests |
| `postgres-regression.log` | 460 PASS: ledger races, native Training dispatch/zero-attempt/expiry compensation, D1 operation fences, Pump, Apple reconciliation and fresh/current-main migration chain/no drift |
| `new-postgres-final.log` | 24 PASS: all-family finite reservations/finalization, strict entitlement ordering and real enabled Pump lease/hold races |
| `final-postgres-transport.log` | 52 PASS: focused PG plus existing Gunicorn26 real TCP/withdrawal/deadline/account-switch harness in legacy and metered modes; no send/consent/socket mocks |
| `final-native-training.log` | 2 PASS: unified mode through original native command, real generator/router/D1/dispatch/binding, mocked SDK |
| `finops-verify-final.log`, `policy-comparison.log` | Original claims 58/58; original cost/proceeds reconciliation; extended three-policy model |
| Hosted exact-head CI | Required before source-merge recommendation; authoritative Linux/image/migration/PG/shard evidence collected separately after push |

Counts above overlap and must not be summed into a fictitious unique-test count.
The early full local run was invalidated by edits during source-inspection tests
and stopped with 6,420 passes / three failures. The actual OFF-path replacement
stub compatibility was corrected, and all three cases pass in the 99-test clean
rerun. Authoritative final full-suite evidence is hosted on a frozen revision.
The initial sandbox Chromium launch denial is environmental, not a waived test.
Local load tests are excluded by the existing default marker, not newly skipped;
required hosted Linux/transport/schema checks are not replaced with SQLite.

## Matrix interpretation and remaining qualification

Generic deterministic tests cover first/last/exhaustion, fresh/duplicate/changed
key, all-family isolation, success, zero attempt, UNKNOWN bounded releases,
retry counts, crash/expiry, account crossover, lost canonical acknowledgement,
failure outcomes, physical fan-out and flag rollback. Real PG proves concurrent
same/different keys, settlement vs D1 physical entry, stale entitlement snapshots
and reconciliation order. Original ledger suites cover Istanbul/year rollover,
in-flight old-period binding and Starter lifetime behavior. Original native
Training suites preserve its dispatch/recovery/retirement/compensation ordering.

The original and metered native Coach transport harness covers withdrawal before
publication, revoke/regrant, publication first, partial writes, slow reader,
disconnect/worker death, deadline and account switch. A committed canonical receipt
consumes even if the final response fails. After a possible physical attempt,
failure remains UNKNOWN unless independent proof establishes billing; an HTTP
status or local fallback never proves it free.

This is **not** a declaration that every five-family client/transport is ready
for paid launch. D1's registry is intentionally not broadened: QR internal
menu/OCR/nutrition purposes and Coach internal tools need their existing authority
owner's qualification. The web split paid-OCR path lacks a combined action and
web streaming remains closed. Final offer mappings, physical cost budgets,
eligibility and client handling need explicit approval/qualification. Safe
admission refuses these paths; it does not provide their missing availability.

Independent review initially found native entitlement ordering, settlement and
canonical receipt issues. They were fixed and re-reviewed: P0=0, P1=0 for dark
source safety, subject to exact committed SHA and hosted CI. This approval does
not waive launch availability, economics or release gates.
